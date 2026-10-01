#!/usr/bin/env python3
"""Unit tests for textrender.py (no MLT needed): .venv/bin/python test_text.py"""
import os, sys, tempfile
from PIL import Image
import textrender as T

tmp = tempfile.mkdtemp(prefix="txt_test_")
ok, bad = [], []
def check(name, cond, detail=""):
    (ok if cond else bad).append(name); print(("PASS " if cond else "FAIL ") + name + (f" [{detail}]" if not cond and detail else ""))
def raises(fn, needle):
    try: fn()
    except ValueError as e: return needle in str(e), str(e)
    return False, "no error"
def bbox(path):
    return Image.open(path).convert("RGBA").getchannel("A").getbbox()

W, H = 1280, 720
p = T.render_text_png("Canción: ¿Mañana vendrás? ÁÉÍÓÚ ñ ü", W, H, cache_dir=tmp)
bb = bbox(p); check("accents/ñ/¿ render, inside the frame", bb and bb[0] >= 0.04*W and bb[2] <= 0.96*W, bb)
long = "Este es un subtítulo bastante largo que no cabe en una sola línea del cuadro de video y debe ajustarse solo"
bb = bbox(T.render_text_png(long, W, H, size=0.07, cache_dir=tmp)); check("long text is wrapped inside margins", bb and bb[0] >= 0.04*W and bb[2] <= 0.96*W and bb[3] <= H, bb)
bb = bbox(T.render_text_png("SupercalifragilisticoespialidosoextraordinarioanticonstitucionalmenteXXXXXXXXXXXX", 640, 360, size=0.1, cache_dir=tmp))
check("one giant word is hard-broken, never clipped", bb and bb[0] >= 0 and bb[2] <= 640, bb)
for pos in T.POSITIONS:
    bb = bbox(T.render_text_png("Hola", W, H, pos=pos, cache_dir=tmp)); cy = (bb[1]+bb[3])/2
    check(f"position {pos}", {"top": cy < H*.25, "center": abs(cy-H/2) < H*.1, "bottom": cy > H*.75}[pos], cy)
check("same input -> same cached file", T.render_text_png("Hola", W, H, cache_dir=tmp) == T.render_text_png("Hola", W, H, cache_dir=tmp))
a = T.render_text_png("Hola", 320, 180, cache_dir=tmp); b = T.render_text_png("Hola", 640, 360, cache_dir=tmp)
check("scales with frame size", Image.open(a).size == (320, 180) and Image.open(b).size == (640, 360))
r, m = raises(lambda: T.render_text_png("Hola 🫠", W, H, cache_dir=tmp), "unsupported character"); check("glyph missing from the font is rejected clearly", r, m)
check("a basic emoji the font does have renders", bbox(T.render_text_png("Hola 😀", W, H, cache_dir=tmp)) is not None)
r, m = raises(lambda: T.render_text_png("你好", W, H, cache_dir=tmp), "unsupported character"); check("CJK rejected clearly", r, m)
r, m = raises(lambda: T.render_text_png("   ", W, H, cache_dir=tmp), "empty"); check("blank rejected", r, m)
r, m = raises(lambda: T.render_text_png("x"*201, W, H, cache_dir=tmp), "limit"); check("over 200 chars rejected", r, m)
r, m = raises(lambda: T.render_text_png("palabra "*24, 320, 180, size=0.2, cache_dir=tmp), "too long to fit"); check("text that can't fit is refused (strict)", r, m)
try: T.render_text_png("palabra "*24, 320, 180, size=0.2, cache_dir=tmp, strict=False); check("same text never fails at render time (strict=False)", True)
except Exception as e: check("same text never fails at render time (strict=False)", False, e)
for label, kw, needle in [("bad color", dict(color="red"), "#RRGGBB"), ("bad size", dict(size=0.9), "between"), ("bad pos", dict(pos="left"), "pos must")]:
    r, m = raises(lambda: T.render_text_png("Hola", W, H, cache_dir=tmp, **kw), needle); check(f"rejects {label}", r, m)
p = T.render_text_png("Con caja", W, H, box=True, color="#ffdd00", cache_dir=tmp); check("box + colour render", bbox(p) is not None)

def srt(content, enc="utf-8", bom=False, crlf=False):
    f = os.path.join(tmp, f"s{len(os.listdir(tmp))}.srt"); c = content.replace("\n", "\r\n") if crlf else content
    open(f, "wb").write((b"\xef\xbb\xbf" if bom else b"") + c.encode(enc)); return f
S = "1\n00:00:01,000 --> 00:00:02,500\nHola, ¿cómo estás?\n\n2\n00:00:03,000 --> 00:00:04,250\n<i>Línea uno</i>\nLínea dos\n"
for label, kw in [("plain", {}), ("BOM + CRLF", dict(bom=True, crlf=True)), ("latin-1", dict(enc="latin-1"))]:
    c = T.parse_srt(srt(S, **kw)); check(f"srt {label}: 2 cues, accents kept, tags stripped",
        len(c) == 2 and c[0]["text"] == "Hola, ¿cómo estás?" and c[1]["text"] == "Línea uno\nLínea dos" and abs(c[1]["end"]-4.25) < 1e-9, c)
r, m = raises(lambda: T.parse_srt(srt("1\n00:00:01 --> garbage\nhi\n")), "bad timestamp"); check("bad srt timestamp names the block", r and "block 1" in m, m)
r, m = raises(lambda: T.parse_srt("/no/such.srt"), "not found"); check("missing srt", r, m)
r, m = raises(lambda: T.parse_srt(srt("\n\n")), "no subtitle cues"); check("empty srt", r, m)

# ---------------- styles
import graphics as G
check("6 styles registered", set(T.STYLE_NAMES) == {"classic", "luxury", "luxury-italic", "champagne", "noir", "modern"}, T.STYLE_NAMES)
for st in T.STYLE_NAMES:
    try:
        p = T.render_text_png("Señor Muñoz: ¿Cómo estás? ¡Excelente!", 540, 960, style=st, cache_dir=tmp); bb = bbox(p)
        check(f"style {st}: accents render inside the frame", bb and bb[0] >= 20 and bb[2] <= 520, bb)
    except Exception as e:
        check(f"style {st}: accents render inside the frame", False, e)
    try:
        bb = bbox(T.render_text_png("Una frase de subtítulo que se ajusta sola sin cortarse", 540, 960, style=st, size=0.05, box=True, cache_dir=tmp))
        check(f"style {st}: long text wraps inside margins (with box)", bb and bb[0] >= 5 and bb[2] <= 535, bb)
    except Exception as e:
        check(f"style {st}: long text wraps inside margins (with box)", False, e)
def rgb_mean(path, thr=200):
    im = Image.open(path).convert("RGBA"); px = [p for p in im.getdata() if p[3] > thr]; return tuple(sum(c[i] for c in px) / len(px) for i in range(3)) if px else None
gold = rgb_mean(T.render_text_png("Oro", 540, 960, style="luxury", cache_dir=tmp)); white = rgb_mean(T.render_text_png("Oro", 540, 960, style="classic", cache_dir=tmp))
near_white = lambda p_: sum(1 for px in Image.open(p_).convert("RGBA").getdata() if px[3] > 200 and min(px[:3]) > 230)
check("luxury text is gold (R>G>B)", gold and gold[0] > gold[1] > gold[2] + 40, gold)
check("classic text is white (many near-white pixels)", near_white(T.render_text_png("Oro", 540, 960, style="classic", cache_dir=tmp)) > 200)
check("luxury has (almost) no near-white pixels: it is gold, not white", near_white(T.render_text_png("Oro", 540, 960, style="luxury", cache_dir=tmp)) < 100)
red = rgb_mean(T.render_text_png("Oro", 540, 960, style="luxury", color="#ff0000", cache_dir=tmp))
check("explicit colour overrides the gold gradient (regression: tuple confusion)", red and red[0] > 180 and red[1] < 80 and red[2] < 80, red)
a = bbox(T.render_text_png("Hola", 540, 960, style="champagne", cache_dir=tmp)); b = bbox(T.render_text_png("Hola", 540, 960, style="noir", cache_dir=tmp))
check("noir is letter-spaced (wider than champagne for the same word)", (b[2] - b[0]) > 1.4 * (a[2] - a[0]), (a, b))
up = bbox(T.render_text_png("hola", 540, 960, style="champagne", uppercase=True, cache_dir=tmp)); lo = bbox(T.render_text_png("hola", 540, 960, style="champagne", cache_dir=tmp))
check("uppercase option changes the rendering", up != lo)
check("ornament override works (none vs diamond differ)", bbox(T.render_text_png("Hola", 540, 960, style="luxury", ornament="none", pos="top", cache_dir=tmp)) != bbox(T.render_text_png("Hola", 540, 960, style="luxury", ornament="diamond", pos="top", cache_dir=tmp)))
for label, kw, needle in [("unknown style", dict(style="fancy"), "unknown style"), ("bad ornament", dict(ornament="swirl"), "ornament must")]:
    r, m = raises(lambda: T.render_text_png("Hola", 540, 960, cache_dir=tmp, **kw), needle); check(f"rejects {label}", r, m)
check("style fonts all ship in the repo", all(os.path.isfile(T.font_path(s_)) for s_ in T.STYLE_NAMES))

# ---------------- graphics
for kind in G.KINDS:
    for amt in (None, G.AMOUNT[kind][1], G.AMOUNT[kind][2]):
        p = G.render(kind, 540, 960, tmp, amount=amt); im = Image.open(p)
        check(f"graphic {kind} amount={amt}: full-frame RGBA with content", im.size == (540, 960) and im.mode == "RGBA" and im.getchannel("A").getbbox() is not None)
    r, m = raises(lambda: G.validate(kind, {"amount": 5}), "amount"); check(f"graphic {kind}: rejects out-of-range amount", r, m)
r, m = raises(lambda: G.validate("sparkles", {}), "unknown graphic"); check("graphic: rejects unknown kind", r, m)
im = Image.open(G.render("letterbox", 540, 960, tmp, amount=0.1)); check("letterbox: bars at top and bottom only", im.getpixel((270, 20))[3] == 255 and im.getpixel((270, 480))[3] == 0)
im = Image.open(G.render("vignette", 540, 960, tmp, amount=0.8)); check("vignette: transparent centre, dark corners", im.getpixel((270, 480))[3] < 15 and im.getpixel((2, 2))[3] > 100)
for al in ("left", "right"):
    p = G.render("lower_third", 540, 960, tmp, title="Ñandú Vázquez", subtitle="Dirección creativa", align=al); bb = bbox(p)
    check(f"lower third ({al}) renders in the bottom area, inside the frame", bb and bb[1] > 960 * 0.6 and bb[0] >= 0 and bb[2] <= 540, bb)
r, m = raises(lambda: G.lower_third(540, 960, "x" * 70, ""), "title max 60"); check("lower third rejects a 70-char title", r, m)
r, m = raises(lambda: G.lower_third(540, 960, "Hola\nMundo", ""), "single lines"); check("lower third rejects line breaks", r, m)
r, m = raises(lambda: G.lower_third(540, 960, "Hola 🫠", ""), "unsupported character"); check("lower third rejects missing glyphs", r, m)
r, m = raises(lambda: G.lower_third(320, 180, "W" * 58, "M" * 78), "too long to fit"); check("lower third that cannot fit is refused (strict)", r, m)
try: G.lower_third(320, 180, "W" * 58, "M" * 78, strict=False); check("...but never fails at render time (strict=False)", True)
except Exception as e: check("...but never fails at render time (strict=False)", False, e)
print(f"\n{len(ok)} passed, {len(bad)} failed"); sys.exit(1 if bad else 0)
