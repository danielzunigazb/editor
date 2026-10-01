#!/usr/bin/env python3
"""4K benchmark through the real MCP server: timings, peak RSS and CPU per tool, output verification.
Run: .venv/bin/python bench_4k.py [--res 1920x1080 --media media_1080 --prefix cam_{codec}_1080p --out 1080p]"""
import asyncio, base64, json, os, tempfile, threading, time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
import argparse
ap = argparse.ArgumentParser(); ap.add_argument("--res", default="3840x2160"); ap.add_argument("--media", default="media_4k")
ap.add_argument("--prefix", default="cam_{codec}_4k"); ap.add_argument("--out", default="4k"); ARGS = ap.parse_args()
RW, RH = map(int, ARGS.res.split("x"))
M = lambda n: os.path.join(HERE, ARGS.media, n)
OUT = os.path.join(HERE, "out", ARGS.out); os.makedirs(OUT, exist_ok=True)
CLK = os.sysconf("SC_CLK_TCK")


class Sampler:
    """Samples RSS (MB) and cumulative CPU (s) of the server process tree every 100 ms."""
    def __init__(self): self.pid = None; self.rss = 0.0; self.cpu = {}; self.stop = False
    def tree(self, root):
        out, todo = [], [root]
        while todo:
            p = todo.pop(); out.append(p)
            try: todo += [int(k) for k in open(f"/proc/{p}/task/{p}/children").read().split()]
            except OSError: pass
        return out
    def run(self):
        while not self.stop:
            if self.pid:
                rss = 0.0
                for p in self.tree(self.pid):
                    try:
                        s = open(f"/proc/{p}/stat").read().rsplit(")", 1)[1].split()
                        self.cpu[p] = (int(s[11]) + int(s[12])) / CLK
                        rss += next(int(l.split()[1]) for l in open(f"/proc/{p}/status") if l.startswith("VmRSS")) / 1024
                    except (OSError, StopIteration): pass
                self.rss = max(self.rss, rss)
            time.sleep(0.1)
    def begin(self): self.rss = 0.0; self.t0 = time.perf_counter(); self.c0 = sum(self.cpu.values())
    def end(self):
        wall = time.perf_counter() - self.t0
        return {"peak_rss_mb": round(self.rss), "cpu_pct_of_1core": round(100 * (sum(self.cpu.values()) - self.c0) / max(wall, 1e-6))}


async def main():
    home = tempfile.mkdtemp(prefix="bench4k_")
    env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}; env["MLT_EDITOR_HOME"] = home
    sp = StdioServerParameters(command=os.path.join(HERE, ".venv", "bin", "python"), args=[os.path.join(HERE, "server.py")], env=env)
    smp = Sampler(); threading.Thread(target=smp.run, daemon=True).start()
    log = []
    async with stdio_client(sp) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            # the server is OUR child (other server.py processes may exist, e.g. one kept by a Claude Code session)
            kids = [int(k) for k in open(f"/proc/{os.getpid()}/task/{os.getpid()}/children").read().split()]
            smp.pid = next(k for k in kids if "server.py" in open(f"/proc/{k}/cmdline", "rb").read().decode(errors="ignore"))

            async def call(name, args, label=None):
                smp.begin(); t0 = time.perf_counter()
                res = await s.call_tool(name, args)
                dt = time.perf_counter() - t0; m = smp.end()
                err = res.isError
                txt = next((c.text for c in res.content if c.type == "text"), "")
                img = next((c.data for c in res.content if c.type == "image"), None)
                row = {"tool": label or name, "s": round(dt, 2), "error": err, **m}
                if err: row["message"] = txt[:200]
                log.append(row); print(f"{row['tool']:34s} {dt:7.2f} s  rss {m['peak_rss_mb']:5d} MB  cpu {m['cpu_pct_of_1core']:4d}%" + (f"  ERROR {txt[:100]}" if err else ""), flush=True)
                return txt, img, err

            await call("new_project", dict(width=RW, height=RH, fps=30))
            for n, i in ((ARGS.prefix.format(codec="h264") + "30.mp4", "A"), (ARGS.prefix.format(codec="hevc10") + "30.mp4", "B"), (ARGS.prefix.format(codec="h264") + "60.mp4", "C")):
                await call("import_clip", dict(path=M(n), id=i), f"import_clip {i}")
            for name, a in [("add_clip", dict(source="A", start_s=0, end_s=4.5)), ("add_clip", dict(source="B", start_s=0, end_s=4.5)),
                            ("crossfade", dict(first_index=0, dur_s=0.8)), ("add_clip", dict(source="C", start_s=0, end_s=3.0)),
                            ("crossfade", dict(first_index=1, dur_s=0.8)), ("set_fades", dict(fade_in_s=0.5, fade_out_s=1.0)),
                            ("add_graphic", dict(kind="vignette", start_s=0, dur_s=10.4)), ("add_graphic", dict(kind="frame", start_s=0, dur_s=10.4)),
                            ("add_text", dict(text="Gran Inauguración", start_s=0.7, dur_s=2.6, position="top", size=0.07, style="luxury")),
                            ("add_lower_third", dict(title="Señor Muñoz", subtitle="Director de Proyecto", start_s=4.0, dur_s=2.5)),
                            ("add_subtitles", dict(cues=[{"start": 1.0, "end": 3.2, "text": "Bienvenidos a esta noche especial."}, {"start": 6.8, "end": 9.5, "text": "¿Listos para la inauguración?"}], style="champagne"))]:
                await call(name, a)
            tl = json.loads((await call("get_timeline", {}))[0]); print("timeline:", tl["duration_s"], "s", flush=True)
            for rep in ("cold", "warm"):          # second call hits the text/graphic PNG cache and warm file cache
                _, img, _ = await call("get_still", dict(time_s=2.0), f"get_still half-res ({rep})"); open(f"{OUT}/still_half.png", "wb").write(base64.b64decode(img))
            for rep in ("cold", "warm"):
                _, img, _ = await call("get_still", dict(time_s=2.0, full_res=True), f"get_still full-res ({rep})"); open(f"{OUT}/still_4k.png", "wb").write(base64.b64decode(img))
            for rep in ("cold", "warm"):
                _, img, _ = await call("get_contact_sheet", dict(count=6), f"get_contact_sheet 6 ({rep})"); open(f"{OUT}/sheet.png", "wb").write(base64.b64decode(img))
            for q in ("draft", "high"):
                txt, _, err = await call("export", dict(output_path=f"{OUT}/export_{q}.mp4", quality=q, overwrite=True), f"export {q}")
            await call("render_preview", {})
    smp.stop = True
    json.dump({"timeline_s": tl["duration_s"], "calls": log}, open(f"{OUT}/bench.json", "w"), indent=1)
    print("DONE", flush=True)

asyncio.run(main())
