#!/usr/bin/env python3
"""End-to-end test of server.py through a real MCP stdio client (spawns the server as a subprocess).
Run: .venv/bin/python test_mcp.py      (needs media/clip_{a,b,c}.mp4 -> `python poc.py gen` + live clip_c)"""
import asyncio, json, os, subprocess, sys, tempfile, time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = os.path.dirname(os.path.abspath(__file__))
M = lambda n: os.path.join(HERE, "media", f"clip_{n}.mp4")
TMP = tempfile.mkdtemp(prefix="mcp_test_")
passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


async def main():
    env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}   # prove the server brings its own X display
    env["MLT_EDITOR_HOME"] = TMP
    params = StdioServerParameters(command=os.path.join(HERE, ".venv", "bin", "python"),
                                   args=[os.path.join(HERE, "server.py")], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(name, **kw):
                res = await s.call_tool(name, kw)
                if res.isError:
                    return None, res.content[0].text
                if res.structuredContent is not None:
                    return res.structuredContent.get("result", res.structuredContent), None
                txt = next((c.text for c in res.content if c.type == "text"), None)
                try:
                    return json.loads(txt), None
                except (TypeError, ValueError):
                    return txt, None

            tools = {t.name for t in (await s.list_tools()).tools}
            want = {"new_project", "import_clip", "list_sources", "add_clip", "cut_clip", "crossfade", "set_fades",
                    "add_pip", "get_timeline", "undo", "remove_op", "get_still", "get_contact_sheet",
                    "render_preview", "export"}
            check("tools listed", want <= tools, f"missing {want - tools}")

            _, err = await call("add_clip", source="A")
            check("add_clip on empty project/unknown source is rejected", err is not None and "unknown source" in err, err)
            check("new_project", (await call("new_project", width=1280, height=720, fps=25))[1] is None)
            ids = []
            for n in "abc":
                res, err = await call("import_clip", path=M(n), id=n.upper())
                ids.append(res["id"] if res else err)
            check("import_clip x3", ids == ["A", "B", "C"], ids)
            _, err = await call("import_clip", path="/nonexistent.mp4")
            check("import missing file rejected", err is not None and "not found" in err, err)

            for name, kw in [("add_clip", dict(source="A")), ("cut_clip", dict(index=0, at_s=3.0)),
                             ("add_clip", dict(source="B")), ("crossfade", dict(first_index=0, dur_s=1.0)),
                             ("set_fades", dict(fade_in_s=0.5, fade_out_s=1.0)),
                             ("add_pip", dict(source="C", start_s=2.0, dur_s=3.0, scale=0.3, opacity=0.9))]:
                res, err = await call(name, **kw)
                check(f"{name} {kw}", err is None, err)
            tl, _ = await call("get_timeline")
            check("timeline duration 7.0 s", tl and abs(tl["duration_s"] - 7.0) < 1e-6, tl and tl["duration_s"])
            check("timeline has 2 entries, 1 crossfade, fade, pip",
                  tl and len(tl["entries"]) == 2 and len(tl["crossfades"]) == 1 and tl["fade"] and tl["pip"])

            # ---- validation (each must be rejected with a useful message and leave the state untouched)
            for label, name, kw, needle in [
                ("cut beyond entry", "cut_clip", dict(index=0, at_s=99), "outside entry"),
                ("cut missing entry", "cut_clip", dict(index=7, at_s=1), "no timeline entry"),
                ("crossfade longer than clip", "crossfade", dict(first_index=0, dur_s=50), "crossfades need"),
                ("fade longer than timeline", "set_fades", dict(fade_in_s=5, fade_out_s=5), "longer than the timeline"),
                ("pip bad position", "add_pip", dict(source="C", start_s=1, dur_s=1, position="middle"), "pos must be"),
                ("pip beyond source", "add_pip", dict(source="C", start_s=1, dur_s=9), "source length"),
                ("add range outside source", "add_clip", dict(source="A", start_s=2, end_s=60), "outside source")]:
                _, err = await call(name, **kw)
                check(f"rejects: {label}", err is not None and needle in err, err)
            tl2, _ = await call("get_timeline")
            check("rejected edits left state unchanged", tl2 == tl)
            _, err = await call("remove_op", index=0)
            check("remove_op that later ops depend on is rejected", err is not None, err)
            res, err = await call("undo")
            check("undo removes the pip", err is None and res and res["pip"] is None, err)
            _, err = await call("add_pip", source="C", start_s=2.0, dur_s=3.0, scale=0.3, opacity=0.9)
            check("re-add pip", err is None, err)

            # ---- rendering tools
            t0 = time.perf_counter()
            res = await s.call_tool("get_contact_sheet", {"count": 6})
            dt_sheet = time.perf_counter() - t0
            img = next((c for c in res.content if c.type == "image"), None)
            check("get_contact_sheet returns a PNG", img is not None and not res.isError)
            if img:
                import base64
                open(os.path.join(TMP, "sheet.png"), "wb").write(base64.b64decode(img.data))
            t0 = time.perf_counter()
            res = await s.call_tool("get_still", {"time_s": 3.0})
            dt_still = time.perf_counter() - t0
            check("get_still returns an image", any(c.type == "image" for c in res.content) and not res.isError)
            res = await s.call_tool("get_still", {"time_s": 99})
            check("get_still out of range is an error", res.isError)
            prev, err = await call("render_preview")
            check("render_preview", err is None and prev and os.path.getsize(prev["path"]) > 10_000, err)
            out = os.path.join(TMP, "final.mp4")
            t0 = time.perf_counter()
            ex, err = await call("export", output_path=out, quality="draft")
            dt_export = time.perf_counter() - t0
            check("export draft", err is None and ex and ex["resolution"] == "1280x720" and abs(ex["duration_s"] - 7.0) < 0.05,
                  err or ex)

            # ---- stability: many renders in one process (repeated Factory.init / profile creation)
            t0 = time.perf_counter()
            ok = True
            for i in range(30):
                res = await s.call_tool("get_still", {"time_s": (i % 14) * 0.5})
                ok &= not res.isError
            check("30 consecutive get_still calls", ok)
            dt30 = time.perf_counter() - t0
            print(f"\ntimings: contact_sheet(6) {dt_sheet:.2f}s | get_still {dt_still:.2f}s | 30 stills {dt30:.1f}s "
                  f"({dt30/30*1000:.0f} ms each) | export draft 7s {dt_export:.2f}s")

    # the exported file really is what the timeline says
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_name,width,height",
                        "-of", "compact", out], capture_output=True, text=True)
    print(r.stdout.strip())
    sys.stdout.flush()
    print(f"\n{len(passed)} passed, {len(failed)} failed  (artifacts in {TMP})")
    sys.exit(1 if failed else 0)


asyncio.run(main())
