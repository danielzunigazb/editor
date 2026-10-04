#!/usr/bin/env python3
"""Pixel-hash snapshot of EVERY drawn component in every template, plus transition masks and animation keys, so the modular refactor can
prove it changed nothing. Hashes are over raw RGBA pixels (+ size), not PNG bytes.
  python3 snapshot.py --write   (record; done ONCE, before the refactor)
  python3 snapshot.py           (compare the current code with tests_data/snapshot.json; exit 1 on any difference)"""
import hashlib, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from PIL import Image  # noqa: E402

import anim  # noqa: E402
import cards  # noqa: E402
import graphics  # noqa: E402
import icons  # noqa: E402
import textrender  # noqa: E402
import themes  # noqa: E402
import transitions  # noqa: E402

REF = os.path.join(HERE, "tests_data", "snapshot.json")
W, H = 640, 360
h = lambda im: hashlib.sha256(im.convert("RGBA").tobytes() + f"{im.size}".encode()).hexdigest()[:20]
hj = lambda obj: hashlib.sha256(json.dumps(obj, sort_keys=True, default=repr).encode()).hexdigest()[:20]
CARDS = {"title": dict(title="Gran Inauguración", subtitle="Nuevo complejo residencial · 2026"), "section": dict(title="Avance de las obras", number="02", subtitle="Octubre"),
         "quote": dict(title="La arquitectura es música congelada y también un buen lugar donde vivir", author="Goethe"),
         "list": dict(title="Lo que viene", items=("Fase 1: estructura", "Fase 2: acabados", "Fase 3: paisajismo")),
         "stat": dict(title="Edificios", number="14", subtitle="en construcción"), "outro": dict(title="Gracias", subtitle="www.ejemplo.com"),
         "bento": dict(title="Resultados", items=("18 %|crecimiento anual", "4,2 M|usuarios activos", "98 %|satisfacción"))}


def compute():
    out = {}
    tmp = tempfile.mkdtemp(prefix="snap_")
    for st in textrender.STYLE_NAMES:
        out[f"style:{st}"] = h(textrender.render_text_image("Señor Muñoz ¿qué tal?", W, H, "bottom", 0.07, None, False, True, st))
        out[f"style:{st}:box"] = h(textrender.render_text_image("Hola", W, H, "top", 0.06, None, True, True, st))
    for n in themes.NAMES:
        th = themes.get(n)
        out[f"{n}:motion"] = hj([th.motion, th.transition, list(th.moods), list(th.sfx), th.label])
        out[f"{n}:lower_third:left"] = h(graphics.lower_third(W, H, "Señor Muñoz", "Director de Proyecto", "left", strict=False, theme=th))
        out[f"{n}:lower_third:right"] = h(graphics.lower_third(W, H, "Ana García", "", "right", strict=False, theme=th))
        for side in ("ne", "nw", "se", "sw"):
            im, ax, ay = graphics.callout(W, H, "Arco monumental", "Entrada", side, strict=False, theme=th)
            out[f"{n}:callout:{side}"] = h(im) + f":{ax:.2f},{ay:.2f}"
        im, ax, ay = graphics.callout(W, H, "Arco", "", "ne", strict=False, theme=th, size=1.5)
        out[f"{n}:callout:size1.5"] = h(im) + f":{ax:.2f},{ay:.2f}"
        for kind in graphics.KINDS:
            out[f"{n}:gfx:{kind}"] = h(Image.open(graphics.render(kind, W, H, tmp, theme={"name": n, "accent": th.accent})))
        for lay, kw in CARDS.items():
            out[f"{n}:card:{lay}"] = h(cards.render_card(lay, W, H, th, **kw))
            bg, layers = cards.render_card(lay, W, H, th, split=True, **kw)
            out[f"{n}:card:{lay}:split"] = hj([h(bg)] + [h(L) for L in layers])
        for icon in ("star", "doodle-arrow"):
            for plate in (True, False):
                L = {"icon": icon, "svg": icons.icon_path(icon), "scale": 0.1, "color": None, "plate": plate, "theme": n}
                out[f"{n}:icon:{icon}:{plate}"] = h(Image.open(icons.render_layer(L, W, tmp)))
        out[f"{n}:timing"] = hj(cards.animated_timing(th, 4, 3.0))
    for s in transitions.MASKED:
        out[f"mask:{s}"] = h(Image.open(transitions.mask_path(s, W, H, tmp)))
    for s in transitions.STYLES:
        if s.startswith("slide-"):
            out[f"slide:{s}"] = transitions.slide_geometry(s, 25)
    for p in anim.PRESETS:
        for out_p in ("fade", "none", p):
            spec = anim.validate({"in": p, "out": out_p, "in_s": 0.6, "out_s": 0.5}, "x", 4.0)
            out[f"anim:{p}:{out_p}:sample"] = hj(anim.sample(spec, (100.0, 80.0, 200.0, 60.0), W, H, 100, 25, 0.24))
            out[f"anim:{p}:{out_p}:wipe"] = hj(anim.wipe_keys(spec, 100, 25, 0.24))
            out[f"anim:{p}:{out_p}:draw"] = hj(anim.draw_keys(spec, 100, 25, 0.24))
            out[f"anim:{p}:{out_p}:callout"] = hj(anim.callout_keys(spec, 100, 25, 0.24))
    for e in anim.EASES:
        out[f"ease:{e}"] = hj([round(anim.ease(e, i / 20), 9) for i in range(21)])
    return out


if __name__ == "__main__":
    cur = compute()
    if "--write" in sys.argv:
        os.makedirs(os.path.dirname(REF), exist_ok=True)
        json.dump(cur, open(REF, "w"), indent=0, sort_keys=True)
        print(f"wrote {len(cur)} hashes -> {REF}")
    else:
        ref = json.load(open(REF))
        bad = sorted(k for k in ref if cur.get(k) != ref[k])
        missing = sorted(k for k in ref if k not in cur)
        print(f"{len(ref) - len(bad)}/{len(ref)} components identical to the snapshot" + (f"; DIFFERENT ({len(bad)}): {bad[:20]}" if bad else "") +
              (f"; MISSING: {missing[:10]}" if missing else ""))
        sys.exit(1 if bad else 0)
