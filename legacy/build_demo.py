#!/usr/bin/env python3
"""Builds out/demo/demo.html (self-contained: video, images and data embedded) from the recorded session.
Run after demo_session.py:  .venv/bin/python build_demo.py"""
import base64, json, os, re

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # repo root (this script lives in legacy/)
D = os.path.join(HERE, "out", "demo")
b64 = lambda p: base64.b64encode(open(p, "rb").read()).decode()
sess = json.load(open(os.path.join(D, "session.json")))
luma = json.load(open(os.path.join(D, "luma.json")))
tests = open(os.path.join(D, "tests.txt")).read()
n_text = int(re.search(r"(\d+) passed", tests.split("\n")[0]).group(1))
n_eng = int(re.search(r"(\d+) passed", tests.split("\n")[1]).group(1))
n_mcp = int(re.search(r"(\d+) passed", [l for l in tests.split("\n") if "passed" in l][-1]).group(1))

# ---- stepper data
steps = []
for s in sess["steps"]:
    r = s["result"]
    if "overlays" in r:
        ov = ", ".join(f"{o['kind']}" + (f":{o.get('graphic')}" if o["kind"] == "graphic" else "") for o in r["overlays"]) or "sin overlays"
        summary = f"{r['duration_s']:g} s · {len(r['entries'])} clip(s) · {ov}"
    else:
        if "ok" in r:
            summary = f"proyecto creado: {r.get('format')}"
        else:
            summary = f"fuente '{r.get('id')}' registrada: {r.get('duration_s'):g} s, {r.get('width')}×{r.get('height')}, {r.get('codec')}"
    steps.append({"n": s["n"], "tool": s["tool"], "label": s["label"], "args": json.dumps(s["args"], ensure_ascii=False, indent=2).replace(os.path.join(D, "src") + "/", "…/"),
                  "summary": summary, "ms": s["ms"], "sheet_ms": s.get("sheet_ms"), "image": ("data:image/jpeg;base64," + b64(os.path.join(D, "steps", s["image"]))) if s.get("image") else None,
                  "response": json.dumps(r, ensure_ascii=False, indent=1)[:1400]})
rej = [{"tool": r["tool"], "label": r["label"], "args": json.dumps(r["args"], ensure_ascii=False), "msg": re.sub(r"^Error executing tool \w+: ", "", r["message"])} for r in sess["rejections"]]

fixed, bug, ref = luma["fixed"], luma["bug"], luma["ref"]
dip = luma["bug_dips"][0]
dip_pct = round(100 * bug[dip] / ref[dip])
chart = {"fps": luma["fps"], "fixed": fixed, "bug": bug, "ref": ref, "dip": dip, "dip_pct": dip_pct}
total_ms = sum(s["ms"] for s in sess["steps"] if not s["tool"].startswith(("new", "import")))
export = sess["export"]
video = b64(os.path.join(D, "final.mp4"))
styles_img = b64(os.path.join(D, "styles.jpg"))
poster = b64(os.path.join(D, "poster.jpg"))
STYLES = [("classic", "Sans en negrita, blanco con contorno negro"), ("luxury", "Playfair Display, dorado metálico, filete con rombo"),
          ("luxury-italic", "Playfair Display cursiva, dorado"), ("champagne", "Cormorant Garamond, marfil, para subtítulos"),
          ("noir", "Cinzel, capitales con mucho espaciado"), ("modern", "Montserrat en mayúsculas, muy espaciado")]


def js(o):
    return json.dumps(o, ensure_ascii=False).replace("</", "<\\/")


page = open(os.path.join(HERE, "demo_template.html")).read()
for k, v in {"@@VIDEO@@": video, "@@STYLES_IMG@@": styles_img, "@@POSTER@@": poster, "@@STEPS@@": js(steps), "@@REJ@@": js(rej), "@@CHART@@": js(chart),
             "@@STYLES@@": js(STYLES), "@@N_TEXT@@": str(n_text), "@@N_ENG@@": str(n_eng), "@@N_MCP@@": str(n_mcp),
             "@@NTOOLS@@": str(len(sess["tools"])), "@@DUR@@": f"{export['duration_s']:g}", "@@RENDER@@": f"{export['render_s']:.1f}",
             "@@SHEET_MS@@": str(round(sum(s['sheet_ms'] for s in sess['steps'] if s.get('sheet_ms')) / sum(1 for s in sess['steps'] if s.get('sheet_ms')))),
             "@@DIP@@": str(dip), "@@DIPT@@": f"{dip / luma['fps']:.2f}", "@@DIPPCT@@": str(dip_pct)}.items():
    page = page.replace(k, v)
open(os.path.join(D, "demo.html"), "w").write(page)
print("demo.html", round(len(page) / 1e6, 2), "MB | steps", len(steps), "| rejections", len(rej), "| tests", n_text, n_eng, n_mcp, "| dip", dip, dip_pct, "%")
