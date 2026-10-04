#!/usr/bin/env python3
"""Pixel-hash reference for the LUXURY look, so the theme refactor can prove it changed nothing for existing projects.
  python3 golden.py --write   (record the reference; done ONCE, before the refactor)
  python3 golden.py --check   (compare the current renderers against tests_data/golden_luxury.json)
Hashes are over raw RGBA pixels (not PNG bytes), so they do not depend on the PNG encoder."""
import hashlib, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import graphics, textrender

GOLD = os.path.join(HERE, "tests_data", "golden_luxury.json")
W, H = 1280, 720
h = lambda im: hashlib.sha256(im.convert("RGBA").tobytes() + f"{im.size}".encode()).hexdigest()


def compute():
    out = {}
    cases = [("Gran Inauguración", "top", 0.08, "luxury", None, False, None, None), ("Señor Muñoz", "center", 0.06, "luxury-italic", None, True, None, "line"),
             ("Bienvenidos, señoras y señores.", "bottom", 0.05, "champagne", None, True, None, None), ("CAPÍTULO UNO", "center", 0.07, "noir", None, False, None, None),
             ("Hola mundo ¿qué tal?", "bottom", 0.06, "modern", "#ff8800", False, True, None), ("Classic text\nsecond line", "top", 0.06, "classic", None, False, None, None)]
    for i, (t, pos, size, style, color, box, up, orn) in enumerate(cases):
        im = textrender.render_text_image(t, W, H, pos, size, color, box, True, style, up, orn)
        out[f"text{i}:{style}:{pos}"] = h(im)
    for kind in graphics.KINDS:
        out[f"gfx:{kind}"] = h(getattr(graphics, kind)(W, H, graphics.AMOUNT[kind][3]))
    out["lower_third:left"] = h(graphics.lower_third(W, H, "Señor Muñoz", "Director de Proyecto", "left", strict=False))
    out["lower_third:right"] = h(graphics.lower_third(W, H, "Ana García", "", "right", strict=False))
    for side in ("ne", "nw", "se", "sw"):
        im, ax, ay = graphics.callout(W, H, "Arco monumental", "Entrada", side, strict=False)
        out[f"callout:{side}"] = h(im) + f":{ax:.2f},{ay:.2f}"
    return out


if __name__ == "__main__":
    cur = compute()
    if "--write" in sys.argv:
        json.dump(cur, open(GOLD, "w"), indent=1, sort_keys=True); print(f"wrote {len(cur)} hashes -> {GOLD}")
    else:
        ref = json.load(open(GOLD))
        bad = [k for k in ref if cur.get(k) != ref[k]]
        print(f"{len(ref) - len(bad)}/{len(ref)} luxury renders identical to the reference" + (f"; DIFFERENT: {bad}" if bad else ""))
        sys.exit(1 if bad else 0)
