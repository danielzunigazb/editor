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
print(f"\n{len(ok)} passed, {len(bad)} failed"); sys.exit(1 if bad else 0)
