#!/usr/bin/env python3
"""Records a real session against server.py (through an MCP stdio client) for the demo page:
every tool call, its arguments, the server's answer, elapsed time, and a contact sheet after each edit.
Writes out/demo/session.json, out/demo/steps/NN.jpg and out/demo/final.mp4.
Run: .venv/bin/python demo_session.py"""
import asyncio, base64, io, json, os, shutil, tempfile, time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO = os.path.join(HERE, "out", "demo")
SRC = lambda n: os.path.join(DEMO, "src", n)
STEPS = os.path.join(DEMO, "steps")
shutil.rmtree(STEPS, ignore_errors=True); os.makedirs(STEPS)
HOME = tempfile.mkdtemp(prefix="demo_home_")

# (tool, args, short human label). Edits get a contact sheet afterwards.
SESSION = [
    ("new_project", dict(width=1280, height=720, fps=25), "Proyecto 1280x720 a 25 fps"),
    ("import_clip", dict(path=SRC("silk_teal.mp4"), id="TEAL"), "Importar clip teal"),
    ("import_clip", dict(path=SRC("silk_gold.mp4"), id="ORO"), "Importar clip dorado"),
    ("add_clip", dict(source="TEAL"), "Agregar el clip teal (6 s)"),
    ("cut_clip", dict(index=0, at_s=4.0), "Cortar en 4.0 s"),
    ("add_clip", dict(source="ORO", start_s=0.0, end_s=4.0), "Pegar el clip dorado (4 s)"),
    ("crossfade", dict(first_index=0, dur_s=0.8), "Fundido cruzado de 0.8 s"),
    ("set_fades", dict(fade_in_s=0.6, fade_out_s=1.0), "Fade in 0.6 s y fade out 1 s"),
    ("add_graphic", dict(kind="vignette", start_s=0.0, dur_s=7.2, amount=0.55), "Viñeta suave"),
    ("add_graphic", dict(kind="frame", start_s=0.0, dur_s=7.2), "Marco dorado"),
    ("add_text", dict(text="Gran Inauguración", start_s=0.7, dur_s=2.4, position="top", size=0.075, style="luxury"), "Título en estilo luxury"),
    ("add_lower_third", dict(title="Señor Muñoz", subtitle="Director de Proyecto", start_s=3.5, dur_s=2.2), "Tercio inferior"),
    ("add_subtitles", dict(cues=[{"start": 1.0, "end": 3.0, "text": "Bienvenidos a esta noche especial."}, {"start": 5.9, "end": 7.0, "text": "¿Listos para la inauguración?"}], style="champagne"), "Subtítulos champagne con acentos y ¿?"),
]
REJECT = [
    ("cut_clip", dict(index=0, at_s=99), "Cortar más allá del final del clip"),
    ("add_text", dict(text="Hola \U0001FAE0", start_s=1, dur_s=1), "Texto con un carácter que la fuente no tiene"),
    ("add_text", dict(text="palabra " * 24, start_s=1, dur_s=1, size=0.2), "Texto demasiado largo para caber"),
    ("add_graphic", dict(kind="letterbox", start_s=1, dur_s=1, amount=0.9), "Valor fuera de rango"),
]


def jpg(b64, width=1100, q=78):
    im = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=q, optimize=True)
    return buf.getvalue()


async def main():
    env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
    env["MLT_EDITOR_HOME"] = HOME
    params = StdioServerParameters(command=os.path.join(HERE, ".venv", "bin", "python"), args=[os.path.join(HERE, "server.py")], env=env)
    log = {"steps": [], "rejections": [], "tools": []}
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            log["tools"] = [t.name for t in (await s.list_tools()).tools]

            async def call(name, args):
                t0 = time.perf_counter()
                res = await s.call_tool(name, args)
                ms = (time.perf_counter() - t0) * 1000
                txt = next((c.text for c in res.content if c.type == "text"), "")
                img = next((c.data for c in res.content if c.type == "image"), None)
                return res.isError, txt, img, ms

            for i, (name, args, label) in enumerate(SESSION, 1):
                err, txt, _, ms = await call(name, args)
                if err:
                    raise SystemExit(f"step {i} {name} failed: {txt}")
                state = json.loads(txt) if txt.startswith("{") else {}
                entry = {"n": i, "tool": name, "args": args, "label": label, "ms": round(ms), "result": state}
                if name not in ("new_project", "import_clip"):
                    e2, _, img, ms2 = await call("get_contact_sheet", {"count": 6})
                    if e2 or not img:
                        raise SystemExit("contact sheet failed")
                    open(os.path.join(STEPS, f"{i:02d}.jpg"), "wb").write(jpg(img))
                    entry["image"] = f"{i:02d}.jpg"; entry["sheet_ms"] = round(ms2)
                log["steps"].append(entry)
                print(f"{i:2d}. {name:16s} {ms:6.0f} ms" + (f"  sheet {entry['sheet_ms']} ms" if "sheet_ms" in entry else ""))
            for name, args, label in REJECT:
                err, txt, _, ms = await call(name, args)
                log["rejections"].append({"tool": name, "args": args, "label": label, "error": err, "message": txt, "ms": round(ms)})
                print(f"   rejected? {err}  {txt[:90]}")
            tl_err, tl, _, _ = await call("get_timeline", {})
            log["timeline"] = json.loads(tl)
            out = os.path.join(DEMO, "final.mp4")
            err, txt, _, ms = await call("export", dict(output_path=out, quality="high", overwrite=True))
            if err:
                raise SystemExit("export failed: " + txt)
            log["export"] = {**json.loads(txt), "ms": round(ms)}
            print("export:", log["export"])
    json.dump(log, open(os.path.join(DEMO, "session.json"), "w"), indent=1, ensure_ascii=False)


asyncio.run(main())
