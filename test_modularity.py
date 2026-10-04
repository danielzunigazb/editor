#!/usr/bin/env python3
"""Proof that the editor's pieces are removable and addable: themes (packs), plugins from an external folder (a transition, a shape, an
animation preset and easing, a card layout), removing a theme / transition / op, a broken pack that must not take the editor down, and
greps that no template name, absolute path or shape branch is hardcoded in the engine. Every scenario runs in a fresh process with its own
MLT_* environment, which is also how a user would configure it. Run: python3 test_modularity.py"""
import json, os, re, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "mltedit")
PACKS = os.path.join(PKG, "packs", "themes")
ok = bad = 0


def check(name, cond, detail=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if not cond and detail else ""))


def run(code, **env):
    """Run python code in a fresh process (sys.path = repo root) with extra MLT_* variables; returns the JSON it prints on its last line."""
    e = {**os.environ, "MLT_EDITOR_HOME": tempfile.mkdtemp(prefix="mod_home_"), **env}
    r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r)\n" % HERE + code], capture_output=True, text=True, env=e, timeout=300)
    last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    try:
        return json.loads(last)
    except ValueError:
        return {"_error": (r.stderr or r.stdout)[-600:]}


def packs_copy(without=(), extra=None):
    """A temporary copy of the built-in packs (minus some), plus extra {folder: theme dict}."""
    d = tempfile.mkdtemp(prefix="mod_packs_")
    for n in os.listdir(PACKS):
        if n not in without:
            shutil.copytree(os.path.join(PACKS, n), os.path.join(d, n))
    for folder, theme in (extra or {}).items():
        os.makedirs(os.path.join(d, folder), exist_ok=True)
        json.dump(theme, open(os.path.join(d, folder, "theme.json"), "w"))
    return d


def base_theme(**changes):
    """A copy of the minimal pack for a new theme: its text styles are renamed (style names are global, a pack may not reuse another's)."""
    t = json.load(open(os.path.join(PACKS, "minimal", "theme.json")))
    t["default"] = False
    name = changes.get("name", "copy")
    t["text_styles"] = {f"{name}-{k}": v for k, v in t["text_styles"].items()}
    t["styles"] = {k: f"{name}-{v}" for k, v in t["styles"].items()}
    for k, v in changes.items():
        t[k] = v
    return t


BUILTIN = sorted(os.listdir(PACKS))

# ------------------------------------------------------------------------------------------------ 1. add a theme by dropping in a pack
mine = base_theme(name="aurora", label="Test theme", palette={"ink": "#101820", "paper": "#f4f1ea", "accent": "#00aa88", "accent2": "#101820", "muted": "#6b7b74"})
d = packs_copy(extra={"aurora": mine})
r = run("""
import json, server, themes, graphics
from mltedit import registry
server.new_project(640, 360, 25)
t = server.set_template("aurora")
png = graphics.lower_third(640, 360, "Hola", "Mundo", "left", True, theme={"name": "aurora", "accent": None})
st = server.list_styles()
print(json.dumps({"names": list(themes.NAMES), "accent": themes.get("aurora").accent, "tpl": t["template"], "png": png is not None, "listed": "aurora" in st["templates"], "problems": registry.problems()}))
""", MLT_PACKS_DIRS=d)
check("a pack dropped in the packs folder is a new template (no code change)", r.get("names") and "aurora" in r["names"] and len(r["names"]) == len(BUILTIN) + 1, r)
check("the new template is selectable over MCP, listed, and draws with its own colours", r.get("tpl") == "aurora" and r.get("listed") and r.get("png") and r.get("accent") == "#00aa88", r)
defaults = [n for n in BUILTIN if json.load(open(os.path.join(PACKS, n, "theme.json"))).get("default")]
check("exactly one pack declares itself the default template (it is not a literal in the code)", len(defaults) == 1, defaults)

# ------------------------------------------------------------------------------------------------ 2. remove a theme
d = packs_copy(without=("cinema",))
r = run("""
import json, server, themes
server.new_project(640, 360, 25)
try:
    server.set_template("cinema"); err = None
except Exception as e:
    err = str(e)
ok = server.set_template("minimal")["template"]
print(json.dumps({"names": list(themes.NAMES), "err": err, "ok": ok}))
""", MLT_PACKS_DIRS=d)
check("removing a pack folder removes that template and nothing else", r.get("names") and "cinema" not in r["names"] and len(r["names"]) == len(BUILTIN) - 1, r)
check("asking for the removed template fails by name and lists what exists", r.get("err") and "cinema" in r["err"] and "minimal" in r["err"], r)
check("the other templates keep working", r.get("ok") == "minimal", r)

# ------------------------------------------------------------------------------------------------ 3. a broken pack does not take the editor down
broken = base_theme(name="broken", shape={"name": "no-such-shape", "radius": 0})
d = packs_copy(extra={"broken": broken})
os.makedirs(os.path.join(d, "garbage"), exist_ok=True)
open(os.path.join(d, "garbage", "theme.json"), "w").write("{ not json")
r = run("""
import json, themes, server
from mltedit import registry
st = server.list_styles()
print(json.dumps({"names": list(themes.NAMES), "problems": registry.problems(), "listed": st.get("problems", [])}))
""", MLT_PACKS_DIRS=d)
check("broken packs are skipped, the good ones load", r.get("names") and "broken" not in r["names"] and "garbage" not in r["names"] and len(r["names"]) == len(BUILTIN), r)
check("each broken pack is reported by name (and shown by list_styles)", r.get("problems") and any("broken" in p for p in r["problems"]) and any("garbage" in p for p in r["problems"]) and r.get("listed"), r)

# ------------------------------------------------------------------------------------------------ 4. an external plugin folder: transition + shape + anim + easing + card layout
plug = tempfile.mkdtemp(prefix="mod_plugins_")
open(os.path.join(plug, "extras.py"), "w").write('''
import math
from mltedit import registry, cards
from mltedit.transitions import Mask
from mltedit.anim import Preset, Easing
from mltedit.shapes import Shape, register
from mltedit.plugins.shapes.flat import Flat

registry.register("transition", "spiral", Mask(lambda u, v, x, y, cx, cy, far: ((math.atan2(y - cy, x - cx) / 6.283 + math.hypot(x - cx, y - cy) / far) % 1.0), 0.08))
registry.register("easing", "snap", Easing(lambda p: 0.0 if p < 0.5 else 1.0))
registry.register("anim_preset", "nudge", Preset(lambda p, x, y, w, h, W, H: ((1 - p) * 0.05 * W, 0.0, 1.0, 0.0, min(max(p, 0.0), 1.0))))

@register
class Ring(Flat):
    name = "ring"

@cards.layout("tagline")
def tagline(c):
    f, tr, lines = c.block(c.title, c.ts, 0.1 * c.H, c.maxw, 2)
    c.put(lines, f, tr, c.H * 0.45, "title", c.ts)
''')
ring = base_theme(name="ringed", shape={"name": "ring", "radius": 0.2, "stroke": 0.002}, label="Uses a shape from an external plugin")
d = packs_copy(extra={"ringed": ring})
r = run("""
import json, server, transitions, anim, themes, cards, graphics
from mltedit import registry
server.new_project(640, 360, 25)
server.set_template("ringed")
png = graphics.lower_third(640, 360, "Hola", "Mundo", "left", True, theme={"name": "ringed", "accent": None})
card = cards.render_card("tagline", 640, 360, themes.get("ringed"), title="Plugin card")
anim.validate({"in": "nudge", "out": "fade", "ease_in": "snap"}, "t", 4.0)
mask = transitions.mask_path("spiral", 320, 180, __import__("tempfile").mkdtemp())
doc = server.crossfade.__doc__
print(json.dumps({"tr": "spiral" in transitions.STYLES, "mask": bool(mask), "doc": "spiral" in doc, "anim": "nudge" in anim.PRESETS and "snap" in anim.EASES,
                  "shape": registry.has("shape", "ring"), "png": png is not None, "card": card.size == (640, 360), "layouts": "tagline" in cards.LAYOUTS,
                  "problems": registry.problems()}))
""", MLT_PLUGIN_DIRS=plug, MLT_PACKS_DIRS=d)
check("an external folder adds a transition that the validator accepts and a mask can be drawn for", r.get("tr") and r.get("mask"), r)
check("tool docstrings list the new transition without editing any file", r.get("doc"), r)
check("an external folder adds an animation preset and an easing", r.get("anim"), r)
check("an external folder adds a shape, used by a theme pack, and a lower third is drawn with it", r.get("shape") and r.get("png"), r)
check("an external folder adds a card layout", r.get("card") and r.get("layouts"), r)
check("loading external plugins reports no problems", r.get("problems") == [], r)

# a plugin that raises is reported, not fatal
bad_plug = tempfile.mkdtemp(prefix="mod_badplug_")
open(os.path.join(bad_plug, "oops.py"), "w").write("raise RuntimeError('boom')\n")
r = run("import json, themes; from mltedit import registry; themes.NAMES; print(json.dumps({'n': len(themes.NAMES), 'problems': registry.problems()}))", MLT_PLUGIN_DIRS=bad_plug)
check("a plugin that raises is reported by file and the editor still starts", r.get("n") == len(BUILTIN) and r.get("problems") and "oops.py" in r["problems"][0] and "boom" in r["problems"][0], r)

# ------------------------------------------------------------------------------------------------ 5. remove a transition and an op
r = run("""
import json, transitions
from mltedit import registry, engine
registry.load()
registry.unregister("transition", "clock")
try:
    transitions.validate("clock", "t"); e1 = None
except ValueError as e:
    e1 = str(e)
registry.unregister("op", "pip")
engine.CLIP_LEN = {"A": 5.0}
engine.CLIPS = {"A": "color:#000000"}
try:
    engine.layout([{"op": "add", "src": "A"}, {"op": "pip", "src": "A", "start": 0, "dur": 1}]); e2 = None
except ValueError as e:
    e2 = str(e)
m = engine.layout([{"op": "add", "src": "A"}, {"op": "fade", "in": 0.5, "out": 0.5}])
print(json.dumps({"e1": e1, "e2": e2, "total": m["total"], "tr": list(transitions.STYLES), "anim": list(engine.ANIMATABLE)}))
""")
check("removing a transition makes it an error that names it and lists the rest", r.get("e1") and "clock" in r["e1"] and "dissolve" in r["e1"] and "clock" not in r["tr"], r)
check("removing an op makes its edits an error that names the op and lists the known ones", r.get("e2") and "unknown op" in r["e2"] and "add" in r["e2"], r)
check("the other ops still lay out", r.get("total") == 5.0, r)
check("animatable ops follow the registry (pip no longer animatable)", r.get("anim") and "pip" not in r["anim"] and "text" in r["anim"], r)

# ------------------------------------------------------------------------------------------------ 6. hardcoding greps
def py_files(root):
    for dp, dn, fn in os.walk(root):
        dn[:] = [x for x in dn if x not in ("__pycache__",)]
        for f in fn:
            if f.endswith(".py"):
                yield os.path.join(dp, f)


names = "|".join(BUILTIN)
tpl_lit = re.compile(r"""["'](%s)["']""" % names)
hits = []
for p in py_files(PKG):
    rel = os.path.relpath(p, PKG)
    if rel.startswith(os.path.join("plugins", "shapes")):          # a shape's own name (cinema, glass...) is not a template reference
        continue
    for i, ln in enumerate(open(p, encoding="utf-8"), 1):
        code = ln.split("#")[0]
        if tpl_lit.search(code) and not code.strip().startswith(('"""', "'''")):
            hits.append(f"{rel}:{i}: {ln.strip()[:90]}")
check("no template name appears as a literal in mltedit/ (only in packs)", not hits, hits[:5])

abs_paths = []
for p in py_files(PKG):
    rel = os.path.relpath(p, PKG)
    if rel == "config.py":
        continue
    for i, ln in enumerate(open(p, encoding="utf-8"), 1):
        code = ln.split("#")[0]
        if re.search(r"""["'](/(home|tmp|usr|var|root|opt|etc|mnt)\b[^"']*)["']""", code) or re.search(r"os\.path\.join\(\s*(HERE|PKG|ROOT)\b", code):
            abs_paths.append(f"{rel}:{i}: {ln.strip()[:90]}")
check("no absolute path or HERE-relative join outside config.py", not abs_paths, abs_paths[:5])

shape_branches = []
for p in py_files(PKG):
    rel = os.path.relpath(p, PKG)
    if rel.startswith(os.path.join("plugins", "shapes")):
        continue
    for i, ln in enumerate(open(p, encoding="utf-8"), 1):
        code = ln.split("#")[0]
        if re.search(r"\bshape\s*(==|!=|in)\b|\.shape\s*(==|!=)|th\.name\s*(==|!=|in)|theme\.name\s*(==|!=|in)", code):
            shape_branches.append(f"{rel}:{i}: {ln.strip()[:90]}")
check("no branch on a shape name or a template name outside plugins/shapes", not shape_branches, shape_branches[:5])

kind_branches = []
for p in py_files(PKG):
    rel = os.path.relpath(p, PKG)
    if rel.startswith(os.path.join("plugins")) or rel.startswith("ops"):            # the plugins ARE the per-kind code
        continue
    for i, ln in enumerate(open(p, encoding="utf-8"), 1):
        code = ln.split("#")[0]
        if re.search(r"""\b(k|kind|op\.get\("op"\)|o\.get\("op"\))\s*==\s*["'](add|cut|crossfade|fade|pip|text|subtitles|graphic|lower_third|image|audio|callout)["']|L\["kind"\]\s*==\s*["']""", code):
            kind_branches.append(f"{rel}:{i}: {ln.strip()[:90]}")
check("no if/elif per op kind or layer kind in the engine/server (they go through plugins)", not kind_branches, kind_branches[:5])

anim_lit = []
for p in (os.path.join(PKG, "anim.py"), os.path.join(PKG, "engine.py"), os.path.join(PKG, "transitions.py")):
    for i, ln in enumerate(open(p, encoding="utf-8"), 1):
        code = ln.split("#")[0]
        if re.search(r"""["'](wipe|draw|pop|zoom|slide-left|slide-right|bounce|back|dissolve)["']""", code) and not code.strip().startswith(('"""', "'''", "*")) and "_reveal(" not in code:
            anim_lit.append(f"{os.path.basename(p)}:{i}: {ln.strip()[:90]}")
check("no animation preset, easing or transition name hardcoded in anim/engine/transitions", not anim_lit, anim_lit[:5])

print(f"\n{ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
