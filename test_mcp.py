#!/usr/bin/env python3
"""End-to-end test of server.py through a real MCP stdio client (spawns the server as a subprocess).
Run: .venv/bin/python test_mcp.py      (needs media/clip_{a,b,c}.mp4 -> `/usr/bin/python3.12 poc.py gen`)"""
import asyncio, glob, json, os, shutil, subprocess, sys, tempfile, textwrap, time

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

            def mkcall(sess):
                async def call(_tool, **kw):                  # `_tool` (not `name`): some tools take a `name` argument
                    res = await sess.call_tool(_tool, kw)
                    if res.isError:
                        return None, res.content[0].text
                    if res.structuredContent is not None:
                        return res.structuredContent.get("result", res.structuredContent), None
                    txt = next((c.text for c in res.content if c.type == "text"), None)
                    try:
                        return json.loads(txt), None
                    except (TypeError, ValueError):
                        return txt, None

                return call

            call = mkcall(s)

            tools = {t.name for t in (await s.list_tools()).tools}
            want = {"new_project", "import_clip", "list_sources", "add_clip", "cut_clip", "crossfade", "set_fades",
                    "add_pip", "get_timeline", "undo", "remove_op", "get_still", "get_contact_sheet",
                    "render_preview", "export", "apply_ops", "list_styles", "add_text", "add_subtitles", "add_graphic", "add_lower_third", "add_image", "add_callout"}
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
            check("timeline has 2 entries, 1 crossfade, fade, 1 overlay (pip)",
                  tl and len(tl["entries"]) == 2 and len(tl["crossfades"]) == 1 and tl["fade"] and len(tl["overlays"]) == 1)

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
            check("undo removes the pip", err is None and res and res["overlays"] == [], err)
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

            # ---- regression: a fade-OUT only must not black out frame 0 (bug found by a real model run)
            await call("set_fades", fade_in_s=0.0, fade_out_s=1.0)
            res = await s.call_tool("get_still", {"time_s": 0.0})
            img0 = next((c for c in res.content if c.type == "image"), None)
            if img0:
                import base64
                raw = subprocess.run(["ffmpeg", "-v", "error", "-i", "-", "-frames:v", "1", "-vf", "scale=32:18,format=gray",
                                      "-f", "rawvideo", "-"], input=base64.b64decode(img0.data), capture_output=True).stdout
                luma = sum(raw) / max(len(raw), 1)
                check("fade_in=0 keeps frame 0 visible", luma > 40, f"luma {luma:.1f}")
            else:
                check("fade_in=0 keeps frame 0 visible", False, "no image")
            await call("set_fades", fade_in_s=0.5, fade_out_s=1.0)

            # =================== text, subtitles, images, layers (fresh project) ===================
            import base64
            from PIL import Image as PILImage

            async def still_gray(tm, w=160, h=90):
                res = await s.call_tool("get_still", {"time_s": tm})
                im = next((c for c in res.content if c.type == "image"), None)
                if res.isError or im is None:
                    return None
                return subprocess.run(["ffmpeg", "-v", "error", "-i", "-", "-frames:v", "1", "-vf", f"scale={w}:{h},format=gray",
                                       "-f", "rawvideo", "-"], input=base64.b64decode(im.data), capture_output=True).stdout

            def changed(a, b, y0=0.0, y1=1.0, w=160, h=90, thr=40):
                rows = range(int(h * y0), int(h * y1))
                return sum(1 for y in rows for x in range(w) if abs(a[y * w + x] - b[y * w + x]) > thr)

            async def fresh():
                await call("new_project", width=1280, height=720, fps=25)
                for n in "ab":
                    await call("import_clip", path=M(n), id=n.upper())
                return await call("add_clip", source="A")      # 6 s of test pattern

            await fresh()
            base = await still_gray(1.0)
            res, err = await call("add_text", text="Canción: ¿Mañana vendrás? Ñandú", start_s=0.5, dur_s=2.0)
            check("add_text with accents/ñ/¿", err is None and res and res["overlays"][0]["kind"] == "text", err)
            with_text = await still_gray(1.0)
            check("text really appears in the bottom of the frame (thin default style: fewer px at 160x90)", base and with_text and changed(base, with_text, 0.7, 1.0) > 70,
                  base and with_text and changed(base, with_text, 0.7, 1.0))
            check("...and not in the top of the frame", changed(base, with_text, 0.0, 0.5) < 30, changed(base, with_text, 0.0, 0.5))
            before_t = await still_gray(3.0)
            check("text is gone after its duration", changed(base, before_t, 0.7, 1.0) >= 0 and changed(with_text, before_t, 0.7, 1.0) > 150)
            for label, kw, needle in [
                ("glyph missing from font", dict(text="Hola \U0001FAE0", start_s=1, dur_s=1), "unsupported character"),
                ("empty text", dict(text="  ", start_s=1, dur_s=1), "empty"),
                ("over 200 chars", dict(text="x" * 201, start_s=1, dur_s=1), "limit"),
                ("text that cannot fit", dict(text="palabra " * 24, start_s=1, dur_s=1, size=0.2), "too long to fit"),
                ("bad color", dict(text="Hola", start_s=1, dur_s=1, color="red"), "#RRGGBB"),
                ("starts after the timeline", dict(text="Hola", start_s=50, dur_s=1), "only"),
                ("zero duration", dict(text="Hola", start_s=1, dur_s=0), "dur")]:
                _, err = await call("add_text", **kw)
                check(f"add_text rejects: {label}", err is not None and needle in err, err)
            res, err = await call("add_text", text="Esta es una frase bastante larga que necesita ajustarse a varias líneas dentro del cuadro de video",
                                  start_s=3.0, dur_s=1.5, position="top", size=0.07)
            check("long text is accepted (wrapped, not clipped)", err is None, err)

            # subtitles: from a cue list and from an .srt (BOM + CRLF), with offset
            cues = [{"start": 0.2, "end": 1.0, "text": "Hola, ¿cómo estás?"}, {"start": 1.2, "end": 2.0, "text": "Línea uno\nLínea dos"},
                    {"start": 2.2, "end": 3.0, "text": "Adiós, señor Muñoz"}]
            res, err = await call("add_subtitles", cues=cues)
            sub = res and next((o for o in res["overlays"] if o["kind"] == "subtitles"), None)
            check("add_subtitles from a cue list", err is None and sub and sub["cues"] == 3, err)
            srt = os.path.join(TMP, "t.srt")
            open(srt, "wb").write(b"\xef\xbb\xbf" + "1\r\n00:00:00,500 --> 00:00:01,500\r\nPrimera línea\r\n\r\n2\r\n00:00:04,000 --> 00:00:05,000\r\n<i>Segunda</i> línea\r\n".encode())
            res, err = await call("add_subtitles", srt_path=srt, offset_s=0.5, position="top")
            check("add_subtitles from .srt with BOM/CRLF + offset", err is None, err)
            for label, kw, needle in [
                ("both srt and cues", dict(srt_path=srt, cues=cues), "exactly one"),
                ("neither", dict(), "exactly one"),
                ("missing srt", dict(srt_path="/no/such.srt"), "not found"),
                ("cue without text", dict(cues=[{"start": 1, "end": 2}]), "start, end and text"),
                ("cue with end before start", dict(cues=[{"start": 2, "end": 1, "text": "x"}]), "invalid time range"),
                ("all cues after the end", dict(cues=[{"start": 60, "end": 61, "text": "x"}]), "after the end"),
                ("bad glyph in a cue", dict(cues=[{"start": 1, "end": 2, "text": "ok"}, {"start": 3, "end": 4, "text": "\U0001FAE0"}]), "cue 1")]:
                _, err = await call("add_subtitles", **kw)
                check(f"add_subtitles rejects: {label}", err is not None and needle in err, err)

            # overlapping overlays get their own tracks; > 6 simultaneous is refused
            tl, _ = await call("get_timeline")
            tracks = sorted({o["track"] for o in tl["overlays"]})
            check("overlapping overlays are spread over tracks", len(tracks) >= 2, tracks)
            await fresh()
            for i in range(6):
                _, err = await call("add_text", text=f"Capa {i}", start_s=1.0, dur_s=2.0, position=["top", "center", "bottom"][i % 3], size=0.04)
                if err: break
            check("6 simultaneous overlays accepted", err is None, err)
            _, err = await call("add_text", text="Capa 7", start_s=1.0, dur_s=2.0)
            check("7th simultaneous overlay is refused clearly", err is not None and "more than 6 overlays" in err, err)
            res, err = await call("get_still") if False else (None, None)

            # images
            await fresh()
            png = os.path.join(TMP, "mark.png")
            im = PILImage.new("RGBA", (200, 100), (0, 0, 0, 0)); [im.putpixel((x, y), (255, 0, 0, 255)) for x in range(40, 160) for y in range(20, 80)]
            im.save(png)
            res, err = await call("add_image", path=png, start_s=1.0, dur_s=2.0, position="center", scale=0.4)
            check("add_image (PNG with transparency)", err is None and res and res["overlays"][0]["image"] == "mark.png", err)
            red_on, red_off = await s.call_tool("get_still", {"time_s": 1.5}), await s.call_tool("get_still", {"time_s": 4.0})
            def mean_rgb(r):
                im_ = next(c for c in r.content if c.type == "image")
                raw = subprocess.run(["ffmpeg", "-v", "error", "-i", "-", "-frames:v", "1", "-vf", "crop=iw*0.2:ih*0.2:iw*0.4:ih*0.4,scale=1:1,format=rgb24", "-f", "rawvideo", "-"],
                                     input=base64.b64decode(im_.data), capture_output=True).stdout
                return tuple(raw)
            on, off = mean_rgb(red_on), mean_rgb(red_off)
            check("image is drawn in the centre while visible, gone afterwards", on[0] > 150 and on[1] < 90 and off != on, (on, off))
            txt = os.path.join(TMP, "not_an_image.txt"); open(txt, "w").write("hola")
            for label, kw, needle in [("missing image", dict(path="/no/such.png", start_s=1, dur_s=1), "not found"),
                                      ("non-image file", dict(path=txt, start_s=1, dur_s=1), "not a readable image"),
                                      ("bad position", dict(path=png, start_s=1, dur_s=1, position="left"), "pos must")]:
                _, err = await call("add_image", **kw)
                check(f"add_image rejects: {label}", err is not None and needle in err, err)

            # cutting the base after overlays exist must NOT be rejected: late overlays are dropped/trimmed with a warning
            await fresh()
            await call("add_subtitles", cues=[{"start": 0.5, "end": 1.5, "text": "Visible"}, {"start": 4.5, "end": 5.5, "text": "Se pierde"}])
            res, err = await call("cut_clip", index=0, at_s=3.0)
            check("a cut that leaves subtitles past the end is accepted", err is None, err)
            tl, _ = await call("get_timeline")
            check("...with a warning naming what was hidden", tl and any("not shown" in w for w in tl["warnings"]), tl and tl["warnings"])
            check("...and the visible subtitle is still there", tl and tl["overlays"] and tl["overlays"][0]["cues"] == 1, tl and tl["overlays"])

            # load: 100 subtitle cues, then render + export with them burned in
            await fresh()
            many = [{"start": round(i * 0.05, 2), "end": round(i * 0.05 + 0.045, 2), "text": f"Línea número {i}: ¿qué tal, señor Muñoz?"} for i in range(100)]
            res, err = await call("add_subtitles", cues=many)
            check("100 subtitle cues accepted", err is None, err)
            t0 = time.perf_counter(); res = await s.call_tool("get_contact_sheet", {"count": 6}); dt_many = time.perf_counter() - t0
            check("contact sheet renders with 100 cues", not res.isError and any(c.type == "image" for c in res.content), res.content[0].text if res.isError else "")
            await fresh()
            await call("add_subtitles", cues=[{"start": 1.0, "end": 3.0, "text": "Subtítulo exportado: ñandú ¿sí?"}], position="bottom")
            ex_out = os.path.join(TMP, "with_subs.mp4")
            ex2, err = await call("export", output_path=ex_out, quality="draft")
            check("export with subtitles", err is None and ex2 and abs(ex2["duration_s"] - 6.0) < 0.05, err or ex2)
            if ex2:
                def frame_gray(ts):
                    return subprocess.run(["ffmpeg", "-v", "error", "-ss", str(ts), "-i", ex_out, "-frames:v", "1", "-vf", "scale=160:90,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
                fa, fb = frame_gray(2.0), frame_gray(4.5)
                check("subtitle is burned into the exported file (and gone later)", changed(fa, fb, 0.7, 1.0) > 150, changed(fa, fb, 0.7, 1.0))
            print(f"   (100-cue contact sheet: {dt_many:.2f}s)")

            # =================== luxury styles + graphics (fresh project) ===================
            await fresh()
            res, err = await call("list_styles")
            check("list_styles describes styles and graphics", err is None and res and "luxury" in res["text_styles"] and "frame" in res["graphics"], err)
            base_ = await still_gray(1.0)
            for stl in ("luxury", "luxury-italic", "champagne", "noir", "modern", "classic"):
                res, err = await call("add_text", text="Señor Muñoz: ¿listos?", start_s=0.5, dur_s=2.0, style=stl, position="center")
                check(f"add_text style={stl}", err is None, err)
                await call("undo")
            res, err = await call("add_text", text="Inauguración", start_s=0.5, dur_s=2.0, position="center", color="#ff0000")
            check("add_text colour override with the default (gradient) style", err is None, err)
            await call("undo")
            for label, kw, needle in [("unknown style", dict(text="Hola", start_s=1, dur_s=1, style="fancy"), "unknown style"),
                                      ("bad ornament", dict(text="Hola", start_s=1, dur_s=1, ornament="swirl"), "ornament must"),
                                      ("bad colour", dict(text="Hola", start_s=1, dur_s=1, color="gold"), "#RRGGBB")]:
                _, err = await call("add_text", **kw)
                check(f"add_text rejects: {label}", err is not None and needle in err, err)
            _, err = await call("add_subtitles", cues=[{"start": 0.5, "end": 1.5, "text": "Hola"}], style="fancy")
            check("add_subtitles rejects an unknown style", err is not None and "unknown style" in err, err)

            def corner_mean(raw, w=160, h=90):      # mean luma of the 4 corner blocks
                blk = lambda x0, y0: [raw[y * w + x] for y in range(y0, y0 + 8) for x in range(x0, x0 + 8)]
                px = blk(0, 0) + blk(w - 8, 0) + blk(0, h - 8) + blk(w - 8, h - 8)
                return sum(px) / len(px)
            def top_rows(raw, w=160, h=90): return sum(raw[:w * 6]) / (w * 6)
            await call("add_graphic", kind="vignette", start_s=0.5, dur_s=3.0, amount=0.9, fade_s=0.0)
            vg = await still_gray(1.5)
            check("vignette darkens the corners", base_ and vg and corner_mean(vg) < 0.8 * corner_mean(base_), (corner_mean(base_), corner_mean(vg) if vg else None))
            await call("undo")
            await call("add_graphic", kind="letterbox", start_s=0.5, dur_s=3.0, amount=0.12, fade_s=0.0)
            lb = await still_gray(1.5)
            check("letterbox paints black bars at the top", lb and top_rows(lb) < 30 and top_rows(base_) > 60, (top_rows(lb) if lb else None, top_rows(base_)))
            await call("undo")
            await call("add_graphic", kind="frame", start_s=0.5, dur_s=3.0, fade_s=0.0)
            fr_ = await still_gray(1.5)
            check("frame draws a keyline near the edges and leaves the centre alone",
                  base_ and fr_ and changed(base_, fr_, 0.0, 1.0) > 80 and sum(abs(base_[(45 * 160) + x] - fr_[(45 * 160) + x]) for x in range(60, 100)) < 40, changed(base_, fr_) if fr_ else None)
            await call("undo")
            res, err = await call("add_lower_third", title="Señor Muñoz", subtitle="Director de Proyecto", start_s=1.0, dur_s=3.0)
            check("add_lower_third", err is None and res and res["overlays"][0]["graphic"] == "lower_third", err)
            lt = await still_gray(2.0)
            check("lower third appears in the bottom area only", base_ and lt and changed(base_, lt, 0.6, 1.0) > 200 and changed(base_, lt, 0.0, 0.45) < 30, changed(base_, lt, 0.6, 1.0) if lt else None)
            for label, kw, needle in [("title over 60 chars", dict(title="x" * 61), "single-line"), ("line break", dict(title="Hola\nMundo"), "single-line"),
                                      ("glyph missing", dict(title="Hola \U0001FAE0"), "unsupported character"), ("bad align", dict(title="Hola", align="center"), "align")]:
                _, err = await call("add_lower_third", start_s=1.0, dur_s=2.0, **kw)
                check(f"add_lower_third rejects: {label}", err is not None and needle in err, err)
            for label, kw, needle in [("unknown kind", dict(kind="sparkles"), "unknown graphic"), ("amount out of range", dict(kind="letterbox", amount=0.9), "amount"),
                                      ("starts after the end", dict(kind="frame", start_s=60), "only")]:
                kw.setdefault("start_s", 1.0)
                _, err = await call("add_graphic", dur_s=2.0, **kw)
                check(f"add_graphic rejects: {label}", err is not None and needle in err, err)
            # a full luxury stack renders and exports
            await fresh()
            for name, kw in [("add_graphic", dict(kind="vignette", start_s=0.0, dur_s=6.0, amount=0.5)), ("add_graphic", dict(kind="frame", start_s=0.0, dur_s=6.0)),
                             ("add_text", dict(text="Gran Inauguración", start_s=0.5, dur_s=2.5, position="top", size=0.08, style="luxury")),
                             ("add_lower_third", dict(title="Señor Muñoz", subtitle="Director", start_s=3.0, dur_s=2.5)),
                             ("add_subtitles", dict(cues=[{"start": 1.0, "end": 2.5, "text": "Bienvenidos, señoras y señores."}], style="champagne"))]:
                res, err = await call(name, **kw)
                if err: break
            check("luxury stack (vignette + frame + title + lower third + subtitles) accepted", err is None, err)
            ex3, err = await call("export", output_path=os.path.join(TMP, "luxury.mp4"), quality="draft")
            check("luxury stack exports", err is None and ex3 and abs(ex3["duration_s"] - 6.0) < 0.05, err or ex3)

            # =================== regressions found by the full code review ===================
            await fresh()
            out_ok = os.path.join(TMP, "once.mp4")
            ex_a, err = await call("export", output_path=out_ok, quality="draft")
            check("export writes a new file", err is None and ex_a, err)
            _, err = await call("export", output_path=out_ok, quality="draft")
            check("export refuses to overwrite an existing file by default", err is not None and "already exists" in err, err)
            ex_b, err = await call("export", output_path=out_ok, quality="draft", overwrite=True)
            check("...but overwrites when asked", err is None and ex_b, err)
            _, err = await call("export", output_path=os.path.join(TMP, "movie.txt"))
            check("export rejects a non-video extension", err is not None and ".mp4" in err, err)
            _, err = await call("export", output_path=TMP + "/")
            check("export rejects a directory", err is not None, err)
            img_path = os.path.join(TMP, "pic.jpg")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=1:duration=1", "-frames:v", "1", img_path], check=True)
            _, err = await call("import_clip", path=img_path, id="IMG")
            check("import_clip rejects a picture and points to add_image", err is not None and "add_image" in err, err)
            _, err = await call("add_subtitles", cues=[{"start": "1", "end": "2", "text": "x"}])
            check("add_subtitles rejects non-numeric times with a clear message", err is not None and "numbers" in err, err)
            _, err = await call("crossfade", first_index=0, dur_s=0.01)
            check("crossfade shorter than a frame is rejected through the server", err is not None, err)

            # =================== apply_ops (batch) and on-screen collision warnings ===================
            LUX = [{"tool": "add_clip", "source": "A", "end_s": 4.0}, {"tool": "add_clip", "source": "B", "end_s": 4.0},
                   {"tool": "crossfade", "first_index": 0, "dur_s": 0.8}, {"tool": "set_fades", "fade_in_s": 0.5, "fade_out_s": 1.0},
                   {"tool": "add_graphic", "kind": "vignette", "start_s": 0, "dur_s": 7.2}, {"tool": "add_graphic", "kind": "frame", "start_s": 0, "dur_s": 7.2},
                   {"tool": "add_text", "text": "Gran Inauguración", "start_s": 0.7, "dur_s": 2.4, "position": "top", "size": 0.075},
                   {"tool": "add_lower_third", "title": "Señor Muñoz", "subtitle": "Director", "start_s": 3.5, "dur_s": 2.2},
                   {"tool": "add_subtitles", "cues": [{"start": 1.0, "end": 3.0, "text": "Bienvenidos."}, {"start": 5.9, "end": 7.0, "text": "¿Listos?"}]}]
            async def clean_project():
                await call("new_project", width=1280, height=720, fps=25)
                for n in "ab":
                    await call("import_clip", path=M(n), id=n.upper())
            strip = lambda tl: {k: v for k, v in tl.items() if k not in ("applied", "ops")}   # edit responses are compact: no op list
            await clean_project()
            res, err = await call("apply_ops", ops=LUX)
            check("apply_ops builds a 9-edit luxury project in ONE call", err is None and res and res["applied"] == 9 and abs(res["duration_s"] - 7.2) < 1e-6, err)
            batch_tl = strip(res) if res else None
            await clean_project()
            for spec in LUX:
                await call(spec["tool"], **{k: v for k, v in spec.items() if k != "tool"})
            seq_tl, _ = await call("get_timeline")
            seq_tl = strip(seq_tl)
            check("...and the result is IDENTICAL to making the same 9 calls one by one", batch_tl == seq_tl, None if batch_tl == seq_tl else "timelines differ")

            before_tl, _ = await call("get_timeline")
            bad = LUX[:2] + [{"tool": "cut_clip", "index": 0, "at_s": 99}]
            _, err = await call("apply_ops", ops=bad)
            check("apply_ops rejects the whole batch when one item is invalid, naming the item", err is not None and "item 2 (cut_clip)" in err and "nothing was applied" in err, err)
            after_tl, _ = await call("get_timeline")
            check("...and applied NOTHING (atomic)", after_tl == before_tl)
            for label, ops_, needle in [
                ("unknown tool", [{"tool": "explode"}], "unknown tool 'explode'"),
                ("a non-edit tool", [{"tool": "export", "output_path": "x.mp4"}], "unknown tool"),
                ("missing 'tool' key", [{"source": "A"}], "needs a 'tool' key"),
                ("unknown argument", [{"tool": "add_clip", "source": "A", "volume": 3}], "bad arguments"),
                ("missing required argument", [{"tool": "add_text", "text": "Hola"}], "bad arguments"),
                ("empty batch", [], "1-50"),
                ("more than 50 items", [{"tool": "set_fades"}] * 51, "1-50"),
                ("a later item that depends on a bad earlier one", [{"tool": "cut_clip", "index": 5, "at_s": 1}, {"tool": "set_fades"}], "item 0 (cut_clip)")]:
                _, err = await call("apply_ops", ops=ops_)
                check(f"apply_ops rejects: {label}", err is not None and needle in err, err)
            res, err = await call("apply_ops", ops=[{"tool": "add_text", "text": "Después", "start_s": 0.5, "dur_s": 1.0}, {"tool": "add_image", "path": "/no/such.png", "start_s": 0, "dur_s": 1}])
            check("a later invalid item in a batch is reported with its own index", err is not None and "item 1 (add_image)" in err and "not found" in err, err)
            await clean_project()
            res, err = await call("apply_ops", ops=[{"tool": "add_clip", "source": "A"}, {"tool": "cut_clip", "index": 0, "at_s": 3.0}, {"tool": "add_clip", "source": "B"}])
            check("items see the state left by earlier items (add -> cut -> add)", err is None and res and abs(res["duration_s"] - 8.0) < 1e-6, err)
            res, err = await call("apply_ops", ops=[{"tool": "add_subtitles", "srt_path": srt, "position": "top"}])
            check("apply_ops accepts an .srt inside a batch", err is None, err)

            # collision warnings: the editor hears about overlaps right after the edit that causes them
            await call("new_project", width=1080, height=1920, fps=24)
            await call("import_clip", path=M("a"), id="A")
            await call("add_clip", source="A")
            res, err = await call("add_lower_third", title="Señor Muñoz", subtitle="Director de Proyecto", start_s=2.0, dur_s=3.0)
            check("no warning with a lower third alone", err is None and res and res["warnings"] == [], res and res["warnings"])
            res, err = await call("add_subtitles", cues=[{"start": 2.4, "end": 4.2, "text": "¿Quién trae el balón?"}])
            check("subtitles at the bottom during a lower third produce a collision warning in the edit response",
                  err is None and res and any("overlap" in w and "lower_third" in w for w in res["warnings"]), res and res["warnings"])
            tl, _ = await call("get_timeline")
            check("...and get_timeline still reports it", tl and any("overlap" in w for w in tl["warnings"]), tl and tl["warnings"])
            await call("undo")
            res, err = await call("add_subtitles", cues=[{"start": 2.4, "end": 4.2, "text": "¿Quién trae el balón?"}], position="center")
            check("moving the subtitles to the centre clears the warning", err is None and res and res["warnings"] == [], res and res["warnings"])
            res, err = await call("add_text", text="Otro texto", start_s=2.5, dur_s=1.0, position="center")
            check("two texts in the same place at the same time warn", err is None and res and any("overlap" in w for w in res["warnings"]), res and res["warnings"])


            # =================== efficiency + robustness hardening ===================
            async def still_b64(sess, t, full=False):
                res = await sess.call_tool("get_still", {"time_s": t, "full_res": full})
                return None if res.isError else next(c.data for c in res.content if c.type == "image")
            ALLOWED = os.path.join(TMP, "allowed"); os.makedirs(ALLOWED)
            INV = os.path.join(ALLOWED, "inv.mp4"); INV2 = os.path.join(ALLOWED, "inv2.mp4")
            shutil.copy(M("a"), INV); shutil.copy(M("a"), INV2)

            await clean_project()
            res, err = await call("add_clip", source="A", end_s=4.0)
            check("edit responses are compact: no op-list echo, but an op_count", err is None and res and "ops" not in res and res["op_count"] == 1, res)
            tl, _ = await call("get_timeline")
            check("get_timeline still lists the numbered ops", tl and len(tl["ops"]) == 1 and tl["ops"][0]["index"] == 0, tl)
            t0 = time.perf_counter(); s1 = await still_b64(s, 1.0); d_cold = time.perf_counter() - t0
            t0 = time.perf_counter(); s2 = await still_b64(s, 1.0); d_warm = time.perf_counter() - t0
            check("tractor cache: a repeated still is byte-identical", s1 is not None and s1 == s2)
            print(f"      still cold {d_cold*1000:.0f} ms, warm {d_warm*1000:.0f} ms")
            await call("add_text", text="Hola", start_s=0.5, dur_s=2.0, position="center")
            s3 = await still_b64(s, 1.0)
            check("an edit invalidates the cached timeline (the new text shows)", s3 is not None and s3 != s1)

            # a second server, with the cache OFF and a path fence ON, is the reference for 'cached == fresh'
            env2 = {k: v for k, v in env.items()}
            env2.update(MLT_EDITOR_HOME=os.path.join(TMP, "home2"), MLT_TRACTOR_CACHE="0", MLT_EDITOR_ROOTS=ALLOWED)
            params2 = StdioServerParameters(command=params.command, args=params.args, env=env2)
            async with stdio_client(params2) as (r2, w2):
                async with ClientSession(r2, w2) as s2:
                    await s2.initialize()
                    call2 = mkcall(s2)
                    await call2("new_project", width=1280, height=720, fps=25)
                    ra, err = await call2("import_clip", path=INV2, id="A")
                    check("fence: a file inside MLT_EDITOR_ROOTS is accepted", err is None, err)
                    await call2("add_clip", source="A", end_s=4.0)
                    f1 = await still_b64(s2, 1.0)
                    check("cached still == fresh build (no cache), plain timeline", f1 is not None and f1 == s1)
                    await call2("add_text", text="Hola", start_s=0.5, dur_s=2.0, position="center")
                    f3 = await still_b64(s2, 1.0)
                    check("cached still == fresh build (no cache), with text", f3 is not None and f3 == s3)
                    # fence
                    _, err = await call2("import_clip", path=M("b"), id="B")
                    check("fence: a file outside the roots is rejected", err is not None and "outside the allowed folders" in err, err)
                    link = os.path.join(ALLOWED, "link.mp4"); os.symlink(M("b"), link)
                    _, err = await call2("import_clip", path=link, id="L")
                    check("fence: a symlink inside the roots that points outside is rejected", err is not None and "outside the allowed folders" in err, err)
                    _, err = await call2("import_clip", path=os.path.join(ALLOWED, "..", os.path.basename(M("b"))), id="D")
                    check("fence: '..' traversal is rejected", err is not None, err)
                    _, err = await call2("export", output_path=os.path.join(TMP, "escape.mp4"), quality="draft")
                    check("fence: export outside the roots is rejected", err is not None and "outside the allowed folders" in err and not os.path.exists(os.path.join(TMP, "escape.mp4")), err)
                    _, err = await call2("add_image", path=img_path, start_s=0.5, dur_s=1.0)
                    check("fence: add_image outside the roots is rejected", err is not None and "outside the allowed folders" in err, err)
                    srt2 = os.path.join(TMP, "outside.srt"); open(srt2, "w").write("1\n00:00:01,000 --> 00:00:02,000\nHola\n")
                    _, err = await call2("add_subtitles", srt_path=srt2)
                    check("fence: an .srt outside the roots is rejected", err is not None and "outside the allowed folders" in err, err)
                    ex_in, err = await call2("export", output_path=os.path.join(ALLOWED, "ok.mp4"), quality="draft")
                    check("fence: export inside the roots works", err is None and ex_in, err)

                    # source files that change on disk must invalidate the cached timeline
                    await call("new_project", width=1280, height=720, fps=25)
                    await call("import_clip", path=INV, id="X")
                    await call("add_clip", source="X", end_s=3.0)
                    x1 = await still_b64(s, 1.0)
                    shutil.copy(M("c"), INV)
                    x2 = await still_b64(s, 1.0)
                    check("replacing a source file on disk invalidates the cached timeline", x1 is not None and x2 is not None and x1 != x2)
                    shutil.copy(M("c"), INV2)
                    await call2("new_project", width=1280, height=720, fps=25)
                    await call2("import_clip", path=INV2, id="X")
                    await call2("add_clip", source="X", end_s=3.0)
                    xf = await still_b64(s2, 1.0)
                    check("...and the refreshed still equals a fresh build of the new file", xf is not None and xf == x2)

            # robustness: a second PROCESS editing the same project must not lose edits (flock around read-modify-write)
            await clean_project()
            await call("add_clip", source="A", end_s=4.0)
            helper = textwrap.dedent("""
                import sys; sys.path.insert(0, %r)
                import server
                for i in range(20):
                    server.commit(server._b_add_text("p%%d" %% i, 0.2 * i, 0.05, "center"))
                """ % HERE)
            envh = {k: v for k, v in env.items()}
            hp = subprocess.Popen([params.command, "-c", helper], env=envh, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            for i in range(20):
                _, err = await call("add_text", text=f"m{i}", start_s=0.2 * i + 0.1, dur_s=0.05, position="center")
                if err: break
            hrc = hp.wait(timeout=120)
            tl, _ = await call("get_timeline")
            check("two processes editing one project: all 41 edits survive (no lost update)",
                  hrc == 0 and tl and tl["op_count"] == 41, (hrc, tl and tl["op_count"], hp.stderr.read().decode()[-300:]))
            check("...and the project file is valid JSON", isinstance(json.load(open(os.path.join(TMP, "project.json"))), dict))

            # bad numbers
            n0 = tl["op_count"]
            for label, spec, needle in [
                    ("NaN time", {"tool": "add_text", "text": "x", "start_s": float("nan"), "dur_s": 1.0}, ""),
                    ("infinite duration", {"tool": "add_text", "text": "x", "start_s": 0.0, "dur_s": float("inf")}, ""),
                    ("a string where a number belongs", {"tool": "add_graphic", "kind": "frame", "start_s": "abc", "dur_s": 1.0}, "item 0")]:
                _, err = await call("apply_ops", ops=[spec])
                check(f"apply_ops rejects {label} without crashing", err is not None and needle in err, err)
            tl, _ = await call("get_timeline")
            check("...and none of them changed the project", tl["op_count"] == n0, tl["op_count"])
            _, err = await call("new_project", width=100000, height=720, fps=25)
            check("new_project rejects absurd sizes", err is not None, err)
            await clean_project()

            # a damaged project file is reported clearly (and new_project repairs it), never a bare traceback
            open(os.path.join(TMP, "project.json"), "w").write("{not json")
            _, err = await call("get_timeline")
            check("corrupt project.json gives a clear error", err is not None and "unreadable" in err, err)
            check("new_project recovers from a corrupt project file", (await call("new_project", width=1280, height=720, fps=25))[1] is None)
            await clean_project()

            # cache pruning + ffprobe timeout (called in-process in a helper: they are internals, not tools)
            h2 = textwrap.dedent("""
                import os, sys, time; sys.path.insert(0, %r)
                import server
                c = os.path.join(server.HOME, "cache"); os.makedirs(c, exist_ok=True)
                for n, age in (("old.png", 30), ("new.png", 0), ("new2.png", 0)):
                    p = os.path.join(c, n); open(p, "wb").write(b"x" * 1000)
                    os.utime(p, (time.time() - age * 86400,) * 2)
                assert server.prune_cache() == 1 and sorted(os.listdir(c)) == ["new.png", "new2.png"], os.listdir(c)
                assert server.prune_cache(max_mb=0.0015) == 1 and len(os.listdir(c)) == 1       # over budget: drop the oldest
                server.SUBPROCESS_TIMEOUT = 0.0001
                try: server._probe(%r); raise SystemExit("probe did not time out")
                except ValueError as e: assert "timed out" in str(e), e
                print("ok")
                """ % (HERE, M("a")))
            envp = dict(env); envp["MLT_EDITOR_HOME"] = os.path.join(TMP, "home3")
            r = subprocess.run([params.command, "-c", h2], env=envp, capture_output=True, text=True, timeout=120)
            check("cache pruning (age, size) and ffprobe timeout", r.returncode == 0 and "ok" in r.stdout, r.stderr[-400:] + r.stdout[-200:])
            check("renders leave no private FIFO directory behind", not glob.glob(os.path.join(tempfile.gettempdir(), "mltfifo_*")))


            # =================== callout: a label pinned to an x,y point ===================
            await clean_project()
            await call("add_clip", source="A", end_s=4.0)
            plain = await still_gray(1.5)
            res, err = await call("add_callout", title="Arco monumental", subtitle="Entrada", track=[[1.0, 0.25, 0.6]], dur_s=2.0, side="ne")
            check("add_callout accepts a fixed point and reports it as an overlay",
                  err is None and res and res["overlays"][-1].get("callout") == "Arco monumental" and "ops" not in res, err or res)
            pinned = await still_gray(1.5)
            xs = [x for y in range(90) for x in range(160) if abs(plain[y * 160 + x] - pinned[y * 160 + x]) > 40]
            ys = [y for y in range(90) for x in range(160) if abs(plain[y * 160 + x] - pinned[y * 160 + x]) > 40]
            check("the flag hangs from the requested point (staff at x=0.25 -> flag left edge at 40 px; flag bottom ~4 px above y=0.6; at 160x90, +-4 px; the thin ring itself vanishes at that scale)",
                  xs and 36 <= min(xs) <= 44 and 45 <= max(ys) <= 53, (xs and (min(xs), max(xs)), ys and (min(ys), max(ys))))
            for label, kw, needle in [("x outside the frame", dict(track=[[1.0, 1.4, 0.5]]), "between 0 and 1"),
                                      ("a path that goes back in time", dict(track=[[2.0, 0.2, 0.5], [1.0, 0.3, 0.5]]), "increase"),
                                      ("a title that is too long", dict(track=[[1.0, 0.5, 0.5]], title="x" * 50), "title"),
                                      ("an empty track", dict(track=[]), "track")]:
                kw = {"title": "Edificio", "dur_s": 1.0, **kw}
                _, err = await call("add_callout", **kw)
                check(f"add_callout rejects {label}", err is not None and needle in err, err)
            res, err = await call("apply_ops", ops=[{"tool": "add_callout", "title": "Edificio", "subtitle": "en construcción", "track": [[0.5, 0.3, 0.4], [2.5, 0.6, 0.45]]},
                                                    {"tool": "add_callout", "title": "Fuente", "track": [[1.0, 0.8, 0.8]], "dur_s": 1.5}])
            check("apply_ops takes moving and fixed callouts in one batch", err is None and res and res["applied"] == 2, err)
            ex_c, err = await call("export", output_path=os.path.join(TMP, "callouts.mp4"), quality="draft")
            check("a project with callouts exports", err is None and ex_c and abs(ex_c["duration_s"] - 4.0) < 0.05, err or ex_c)

            # =================== design templates ===================
            await clean_project()
            await call("add_clip", source="A", end_s=4.0)
            tl0, _ = await call("get_timeline")
            check("a new project starts in the luxury template", tl0 and tl0["template"] == "luxury", tl0 and tl0.get("template"))
            for label, kw, needle in [("an unknown template", dict(name="neon-pink"), "unknown template"), ("a bad accent colour", dict(name="corporate", accent="red"), "accent")]:
                _, err = await call("set_template", **kw)
                check(f"set_template rejects {label}", err is not None and needle in err, err)
            await call("add_text", text="Gran Inauguración", start_s=0.5, dur_s=2.0, position="center")
            lux = await still_gray(1.0)
            res, err = await call("set_template", name="corporate")
            check("set_template switches the project's template and keeps its edits", err is None and res and res["template"] == "corporate" and len(res["overlays"]) == 1, err or res)
            corp = await still_gray(1.0)
            check("the SAME edit now renders in the new template (different pixels)", lux and corp and changed(lux, corp) > 150, lux and corp and changed(lux, corp))
            tl1, _ = await call("get_timeline")
            check("...while the timeline itself did not change", tl1["duration_s"] == tl0["duration_s"] and [o["start_s"] for o in tl1["overlays"]] == [0.5], tl1)
            await call("set_template", name="luxury")
            back = await still_gray(1.0)
            check("switching back to luxury restores the exact original pixels", back == lux)
            for name in ("academic", "sketch", "tech", "minimal", "playful"):
                res, err = await call("set_template", name=name, accent="#12AB34")
                st_ = await still_gray(1.0)
                check(f"template {name}: renders the same edit and is distinct from luxury", err is None and st_ and changed(lux, st_) > 100, err)
            res, err = await call("set_template", name="playful")
            long_txt = ("palabra " * 20).strip()
            res, err = await call("add_text", text=long_txt, start_s=0.5, dur_s=2.0, size=0.12, position="top")
            check("a long text that fits the playful (condensed) type is accepted there", err is None, err)
            _, err = await call("set_template", name="luxury")
            check("switching to a template where an existing text would not fit is rejected, naming the op", err is not None and "would not fit" in err and "nothing was changed" in err, err)
            tl2, _ = await call("get_timeline")
            check("...and the project stays in the previous template", tl2["template"] == "playful", tl2["template"])
            ls_, err = await call("list_styles")
            check("list_styles lists the 7 templates and the template styles", err is None and ls_ and len(ls_["templates"]) == 7 and "corp-title" in ls_["text_styles"] and ls_["current_template"] == "playful", err)
            await call("set_template", name="luxury")
            await call("add_clip", source="A")

            # ---- stability: many renders in one process (repeated Factory.init / profile creation)
            t0 = time.perf_counter()
            ok = True
            for i in range(30):
                res = await s.call_tool("get_still", {"time_s": (i % 11) * 0.5})
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
