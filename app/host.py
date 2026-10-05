"""One editor-engine process per project, spoken to over MCP stdio.

A process per project keeps MLT/Qt's process-wide state of one project away from another, lets a project be stopped when idle (it is all on disk and restarts on the next
call) and bounds what a hostile project can reach: the process gets the project folder as its home and its only allowed folder, and none of the application's secrets.
Each process is owned by ONE asyncio task, because the MCP client's contexts (anyio task groups) must be entered and left from the same task."""
import asyncio, os, sys, time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class HostError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def normalize(res):
    """A CallToolResult as {"ok", "text", "images": [{"media_type", "data"}]}: what the chat and the API need, nothing MCP-specific."""
    texts, images = [], []
    for c in res.content:
        if c.type == "text":
            texts.append(c.text)
        elif c.type == "image":
            images.append({"media_type": c.mimeType, "data": c.data})
    return {"ok": not res.isError, "text": "\n".join(texts), "images": images}


class Conn:
    def __init__(self, pid, params, errlog_path, tool_timeout):
        self.pid, self.params, self.errlog_path, self.tool_timeout = pid, params, errlog_path, tool_timeout
        self.tools, self.instructions = [], ""
        self.last_used, self.busy, self.closed = time.monotonic(), 0, False
        self.q: asyncio.Queue = asyncio.Queue()
        self._ready: asyncio.Future = asyncio.get_running_loop().create_future()
        self.task = asyncio.create_task(self._run())

    async def _run(self):
        errlog = None
        try:
            if os.path.exists(self.errlog_path) and os.path.getsize(self.errlog_path) > 5_000_000:
                os.remove(self.errlog_path)                                   # one file per project, kept short
            errlog = open(self.errlog_path, "a")
            async with stdio_client(self.params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    self.instructions = init.instructions or ""
                    self.tools = [{"name": t.name, "description": t.description or "", "input_schema": t.inputSchema} for t in (await session.list_tools()).tools]
                    self._ready.set_result(True)
                    while True:
                        item = await self.q.get()
                        if item is None:
                            break
                        name, args, fut = item
                        try:
                            res = await asyncio.wait_for(session.call_tool(name, args), self.tool_timeout)
                        except asyncio.TimeoutError:
                            fut.set_exception(HostError("TOOL_TIMEOUT", f"{name} took more than {self.tool_timeout:g} s; the engine process was stopped"))
                            break                                            # a call stuck inside the engine: the process is restarted by the next call
                        except Exception as e:                               # noqa: BLE001
                            fut.set_exception(HostError("ENGINE_ERROR", f"{type(e).__name__}: {e}"))
                            break
                        else:
                            fut.set_result(normalize(res))
        except Exception as e:                                               # noqa: BLE001
            if not self._ready.done():
                self._ready.set_exception(HostError("ENGINE_START", f"the editor engine did not start: {type(e).__name__}: {e}"))
        finally:
            self.closed = True
            if not self._ready.done():
                self._ready.set_exception(HostError("ENGINE_START", "the editor engine did not start"))
            while not self.q.empty():                                        # whoever is waiting must not wait forever
                item = self.q.get_nowait()
                if item is not None and not item[2].done():
                    item[2].set_exception(HostError("ENGINE_STOPPED", "the editor engine process stopped"))
            if errlog:
                errlog.close()

    async def ready(self):
        await self._ready

    async def call(self, name, args):
        if self.closed:
            raise HostError("ENGINE_STOPPED", "the editor engine process is not running")
        fut = asyncio.get_running_loop().create_future()
        self.busy += 1
        try:
            await self.q.put((name, args, fut))
            return await fut
        finally:
            self.busy -= 1
            self.last_used = time.monotonic()

    async def stop(self):
        if not self.closed:
            await self.q.put(None)
        try:
            await asyncio.wait_for(asyncio.shield(self.task), 10)
        except (asyncio.TimeoutError, Exception):                            # noqa: BLE001
            self.task.cancel()


class ProjectHost:
    def __init__(self, settings, project_dir):
        self.s, self.project_dir = settings, project_dir               # project_dir(pid) -> the project's folder
        self._conns: dict[str, Conn] = {}
        self._lock = asyncio.Lock()
        self._reaper = None
        self.started = 0                                               # engine processes started so far (metrics)

    def alive(self):
        return {pid: c for pid, c in self._conns.items() if not c.closed}

    async def conn(self, pid) -> Conn:
        async with self._lock:
            c = self._conns.get(pid)
            if c is not None and not c.closed:
                c.last_used = time.monotonic()
                return c
            self._conns.pop(pid, None)
            live = self.alive()
            while len(live) >= self.s.max_procs:
                idle = sorted((c for c in live.values() if not c.busy), key=lambda c: c.last_used)
                if not idle:
                    raise HostError("TOO_MANY_PROJECTS_OPEN", f"{len(live)} projects are being worked on at this moment (the limit is {self.s.max_procs}); try again shortly")
                await idle[0].stop()
                self._conns.pop(idle[0].pid, None)
                live = self.alive()
            pdir = self.project_dir(pid)
            os.makedirs(pdir, exist_ok=True)
            params = StdioServerParameters(command=sys.executable, args=[os.path.join(self.s.code_root, "server.py")], env=self.s.engine_env(pdir), cwd=self.s.code_root)
            c = Conn(pid, params, os.path.join(pdir, "engine.log"), self.s.tool_timeout_s)
            self._conns[pid] = c
            self.started += 1
        await c.ready()                                                    # outside the lock: starting one project must not hold up the others
        return c

    async def call(self, pid, name, args=None):
        for attempt in (0, 1):
            c = await self.conn(pid)
            try:
                return await c.call(name, args or {})
            except HostError as e:
                if e.code == "ENGINE_STOPPED" and attempt == 0:           # it was stopped between getting it and calling it (idle reaper): start a new one
                    continue
                raise

    async def close(self, pid):
        c = self._conns.pop(pid, None)
        if c is not None:
            await c.stop()

    async def close_all(self):
        if self._reaper:
            self._reaper.cancel()
        for pid in list(self._conns):
            await self.close(pid)

    def start_reaper(self):
        async def loop():
            while True:
                await asyncio.sleep(max(1.0, self.s.idle_s / 4))
                now = time.monotonic()
                for pid, c in list(self._conns.items()):
                    if c.closed or (not c.busy and now - c.last_used > self.s.idle_s):
                        await self.close(pid)
        self._reaper = asyncio.create_task(loop())
