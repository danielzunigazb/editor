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
check("the 6 original styles are still registered", {"classic", "luxury", "luxury-italic", "champagne", "noir", "modern"} <= set(T.STYLE_NAMES), T.STYLE_NAMES)
check("12 template styles registered (corp/acad/sketch/tech/min/kids x title+body)", len([s_ for s_ in T.STYLE_NAMES if s_.split("-")[0] in ("corp", "acad", "sketch", "tech", "min", "kids")]) == 12)
import themes
check("every template points at registered styles", all(s_ in T.STYLES for th_ in themes.THEMES.values() for s_ in (th_.title_style, th_.subtitle_style, th_.caption_style)))
check("7 templates: luxury + the 6 new ones", set(themes.NAMES) == {"luxury", "corporate", "academic", "sketch", "tech", "minimal", "playful"}, themes.NAMES)
for st_ in T.STYLE_NAMES:
    try:
        T.clean("áéíóúüñÁÉÍÓÚÜÑ ¿Qué? ¡Sí! 5€ «hola» “eco” — …", st_); ok_ = True
    except ValueError as e_:
        ok_ = e_
    check(f"style {st_}: every Spanish character (accents, ñ, ¿¡, €, quotes) has a glyph", ok_ is True, ok_)
w400 = T.text_width("Gran Inauguración", T._font("corp-body", 80), 0); w650 = T.text_width("Gran Inauguración", T._font("corp-title", 80), 0)
check("variable-font axes are applied by name (Inter wght+opsz: title and body widths differ by >2%)", abs(w650 - w400) > 0.02 * w400, (w400, w650))
wa = T.text_width("Gran Inauguración", T._font("acad-title", 80), 0); wb = T.text_width("Gran Inauguración", T._font("acad-body", 80), 0)
check("Source Serif (wght+opsz axes) and Source Sans load as different faces", abs(wa - wb) > 5, (wa, wb))
check("template styles carry their panel/outline/glow settings", T.STYLES["tech-title"]["glow"][0] == "#00e5ff" and T.STYLES["kids-title"]["sticker"] and T.STYLES["acad-title"]["box_default"])
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
import golden, json as _json
_ref, _cur = _json.load(open(golden.GOLD)), golden.compute()
check("luxury renders are pixel-identical to the reference recorded before the template refactor (golden.py)", all(_cur.get(k) == v for k, v in _ref.items()), [k for k, v in _ref.items() if _cur.get(k) != v])
# ---- themed overlays (lower third / callout / frame per template): pure PIL, no MLT needed
import graphics, subprocess, tempfile
NEW = [n for n in themes.NAMES if n != "luxury"]
TW, TH = 960, 540
for n_ in NEW:
    th_ = themes.get(n_)
    im_ = graphics.lower_third(TW, TH, "Señor Muñoz", "Director de Proyecto", "left", theme=th_)
    bb_ = im_.getchannel("A").getbbox()
    check(f"{n_}: lower third draws inside the frame, in the lower half", im_.size == (TW, TH) and bb_ and bb_[0] >= 0 and bb_[2] <= TW and bb_[1] > TH * 0.5 and bb_[3] <= TH, bb_)
    check(f"{n_}: lower third is deterministic (same pixels twice)", graphics.lower_third(TW, TH, "Señor Muñoz", "Director de Proyecto", "left", theme=th_).tobytes() == im_.tobytes())
    try:
        graphics.lower_third(TW, TH, "W" * 60, "M" * 80, "left", strict=True, theme=th_); e_ = None
    except ValueError as ex_:
        e_ = str(ex_)
    ov_ = graphics.lower_third(TW, TH, "W" * 60, "M" * 80, "left", strict=False, theme=th_).getchannel("A").getbbox()
    check(f"{n_}: an over-long lower third never overflows the frame (rejected in strict mode, or shrunk to fit)",
          (e_ is None or "fit" in e_) and ov_ and ov_[0] >= 0 and ov_[2] <= TW, (e_, ov_))
    right_ = graphics.lower_third(TW, TH, "Ana García", "", "right", theme=th_).getchannel("A").getbbox()
    check(f"{n_}: right-aligned lower third sits on the right half" + (" (minimal: its soft scrim spans the width, so only the lower band is checked)" if n_ == "minimal" else ""),
          right_ and right_[2] <= TW and right_[1] > TH * 0.5 and (n_ == "minimal" or right_[0] > TW * 0.3), right_)
    for side_ in ("ne", "nw", "se", "sw"):
        c_, ax_, ay_ = graphics.callout(TW, TH, "Arco monumental", "Entrada", side_, theme=th_)
        a_ = c_.getchannel("A")
        near_ = a_.crop((max(0, int(ax_) - 14), max(0, int(ay_) - 14), min(c_.width, int(ax_) + 14), min(c_.height, int(ay_) + 14))).getbbox()
        check(f"{n_}/{side_}: callout is small, its anchor is inside it and the marker is drawn on the anchor",
              c_.width < TW * 0.45 and c_.height < TH * 0.4 and 0 <= ax_ < c_.width and 0 <= ay_ < c_.height and near_ is not None, (c_.size, ax_, ay_, near_))
    c1_ = graphics.callout(TW, TH, "Edificio 1", "En construcción", "ne", theme=th_)[0].tobytes(); c2_ = graphics.callout(TW, TH, "Edificio 1", "En construcción", "ne", theme=th_)[0].tobytes()
    check(f"{n_}: callout is deterministic (hand-drawn strokes included)", c1_ == c2_)
    fr_ = graphics.render("frame", TW, TH, tmp, theme={"name": n_, "accent": th_.accent})
    fim_ = Image.open(fr_).convert("RGBA"); fa_ = fim_.getchannel("A")
    check(f"{n_}: frame leaves the picture alone (centre transparent, <8% of pixels covered) and is not empty", fa_.getpixel((TW // 2, TH // 2)) == 0 and sum(1 for v in fa_.getdata() if v > 8) < 0.08 * TW * TH and fa_.getbbox(), fa_.getbbox())
check("accent override changes the template's signature colour in its overlays",
      graphics.lower_third(TW, TH, "Hola", "", "left", theme={"name": "corporate", "accent": "#FF0000"}).tobytes() != graphics.lower_third(TW, TH, "Hola", "", "left", theme="corporate").tobytes())
check("sketch strokes are seeded by content: a different text gets different pencil strokes",
      graphics.callout(TW, TH, "Uno", "", "ne", theme="sketch")[0].tobytes() != graphics.callout(TW, TH, "Dos", "", "ne", theme="sketch")[0].tobytes())
check("letterbox takes the theme's accent for its hairline (and gold for luxury, unchanged)",
      graphics.letterbox(TW, TH, 0.1, (0, 229, 255)).tobytes() != graphics.letterbox(TW, TH, 0.1).tobytes())
# ---- cards (title / section / quote / list / stat / outro) in every template
import cards
CW, CH = 640, 360
KW = {"title": dict(title="Gran Inauguración", subtitle="Nuevo complejo residencial · 2026"), "section": dict(title="Avance de las obras", number="02", subtitle="Octubre"),
      "quote": dict(title="La arquitectura es música congelada y también un buen lugar donde vivir", author="Goethe"),
      "list": dict(title="Lo que viene", items=["Fase 1: estructura", "Fase 2: acabados", "Fase 3: paisajismo", "Entrega: diciembre"]),
      "stat": dict(title="Edificios", number="14", subtitle="en construcción"), "outro": dict(title="Gracias", subtitle="www.ejemplo.com")}
errs_ = []
for n_ in themes.NAMES:
    for lay_ in cards.LAYOUTS:
        try:
            im_ = cards.render_card(lay_, CW, CH, themes.get(n_), **KW[lay_])
            if im_.size != (CW, CH) or im_.getchannel("A").getextrema() != (255, 255):
                errs_.append((n_, lay_, "size/alpha"))
        except Exception as e_:
            errs_.append((n_, lay_, str(e_)))
check("all 7 templates x 6 card layouts render full-frame and opaque", not errs_, errs_[:3])
check("a card is deterministic (same pixels twice), incl. the random-looking backgrounds",
      all(cards.render_card("title", CW, CH, themes.get(n_), **KW["title"]).tobytes() == cards.render_card("title", CW, CH, themes.get(n_), **KW["title"]).tobytes() for n_ in themes.NAMES))
check("the same title looks different in each template", len({cards.render_card("title", CW, CH, themes.get(n_), **KW["title"]).tobytes() for n_ in themes.NAMES}) == 7)
for label_, lay_, kw_, needle_ in [("an over-long title", "title", dict(title="MMMMMMMMM " * 19), "fit"), ("a title-less card", "title", dict(title=""), "needs a title"),
                                   ("6 list items", "list", dict(title="L", items=["a"] * 6), "items"), ("a list item over 60 characters", "list", dict(title="L", items=["x" * 61]), "items"),
                                   ("an over-long stat figure", "stat", dict(title="x", number="1234567890123"), "figure"), ("an unknown layout", "poster", dict(title="x"), "layout")]:
    try:
        cards.render_card(lay_, CW, CH, themes.get("corporate"), **kw_); e_ = None
    except ValueError as ex_:
        e_ = str(ex_)
    check(f"a card rejects {label_}", e_ is not None and needle_ in e_, e_)
png_a = cards.card_png(tmp, "title", CW, CH, themes.get("sketch"), **KW["title"]); png_b = cards.card_png(tmp, "title", CW, CH, themes.get("sketch"), **KW["title"])
check("card PNGs are cached by content", png_a == png_b and os.path.exists(png_a))
mp4_ = cards.card_video(png_a, os.path.join(tmp, "card.mp4"), CW, CH, 25, 1.0)
probe_ = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,nb_frames", "-of", "csv=p=0", mp4_], capture_output=True, text=True).stdout
check("a card video is H.264 at the frame size with an audio track and 25 frames per second", "video,640,360,25" in probe_ and "audio" in probe_, probe_)
# ---- icons: bundled Lucide set + doodles, user SVG safety, exact-size rasterizing
import icons
all_icons = icons.list_icons()
check("about 100 icons are bundled (Lucide + 8 doodles)", len(all_icons) >= 95 and all(d_ in all_icons for d_ in icons.DOODLES), len(all_icons))
errs_ = []
for nm_ in all_icons:
    L_ = {"icon": nm_, "svg": icons.icon_path(nm_), "scale": 0.1, "color": "#1F6FEB", "plate": True, "theme": {"name": "corporate", "accent": "#1f6feb"}}
    try:
        im_ = Image.open(icons.render_layer(L_, 1280, tmp))
        if im_.size != (128, 128) or not im_.convert("RGBA").getchannel("A").getbbox():
            errs_.append((nm_, im_.size))
    except Exception as e_:
        errs_.append((nm_, str(e_)[:50]))
check("every bundled icon rasterizes at the exact requested size (scale x frame width) and is not empty", not errs_, errs_[:3])
Lb = {"icon": "star", "svg": icons.icon_path("star"), "scale": 0.2, "color": "#ff0000", "plate": False, "theme": {"name": "luxury", "accent": "#d9b25a"}}
im_b = Image.open(icons.render_layer(Lb, 1000, tmp)).convert("RGBA")
check("a bare icon is tinted with the requested colour (no plate)", im_b.size == (200, 200) and any(p[3] > 200 and p[0] > 200 and p[1] < 60 for p in im_b.getdata()))
La = dict(Lb, plate=True, color="#ffffff", theme={"name": "playful", "accent": "#ff6b6b"})
check("a plated icon is square, with the plate filling the frame and the glyph smaller inside it", Image.open(icons.render_layer(La, 1000, tmp)).size == (200, 200))
check("icon rendering is deterministic and cached", icons.render_layer(La, 1000, tmp) == icons.render_layer(La, 1000, tmp))
try:
    icons.icon_path("starr"); e_ = None
except ValueError as ex_:
    e_ = str(ex_)
check("an unknown icon name is rejected with a 'did you mean' suggestion", e_ and "star" in e_, e_)
try:
    icons.icon_path("../../etc/passwd"); e_ = None
except ValueError as ex_:
    e_ = str(ex_)
check("an icon name can never be a path", e_ is not None and "unknown icon" in e_, e_)
svgdir = tempfile.mkdtemp(prefix="svg_test_")
def svgfile(name_, body_):
    p_ = os.path.join(svgdir, name_); open(p_, "w").write(body_); return p_
good_ = svgfile("ok.svg", '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 20"><rect width="40" height="20" fill="currentColor"/></svg>')
icons.check_svg(good_)
check("a plain SVG is accepted and its aspect comes from the viewBox", abs(icons.svg_aspect(good_) - 2.0) < 1e-6)
for label_, body_ in [("a <script>", '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><script>alert(1)</script></svg>'),
                      ("an onload handler", '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" onload="x()"><rect width="5" height="5"/></svg>'),
                      ("an embedded <image>", '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><image href="http://evil.test/a.png"/></svg>'),
                      ("an external xlink:href", '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10"><use xlink:href="http://evil.test/a.svg#x"/></svg>'),
                      ("a <style> with @import", '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><style>@import url(http://evil.test/x.css);</style></svg>'),
                      ("an entity declaration (XXE)", '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><text>&x;</text></svg>'),
                      ("a url() fill pointing outside", '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="5" height="5" fill="url(http://evil.test/p)"/></svg>')]:
    try:
        icons.check_svg(svgfile("bad.svg", body_)); e_ = None
    except ValueError as ex_:
        e_ = str(ex_)
    check(f"an SVG with {label_} is rejected", e_ is not None and "not allowed" in e_, e_)
try:
    icons.check_svg(svgfile("fake.svg", "just text")); e_ = None
except ValueError as ex_:
    e_ = str(ex_)
check("a text file named .svg is rejected", e_ is not None and "not an SVG" in e_, e_)
Lu = {"icon": None, "svg": good_, "path": good_, "scale": 0.25, "color": "#00aa00", "plate": False, "theme": {"name": "luxury", "accent": "#d9b25a"}}
imu = Image.open(icons.render_layer(Lu, 800, tmp))
check("a user SVG is rasterized at width = scale x frame width with its own aspect (2:1)", imu.size == (200, 100), imu.size)
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

# ---------------- review regressions
import time as _t
check("clean() is memoised (same object back, no re-validation cost)", T.clean("Hola", "luxury") is T.clean("Hola", "luxury"))
t0 = _t.perf_counter()
for i in range(300): T.clean(f"Línea número {i}: ¿qué tal, señor Muñoz?", "champagne")
first = _t.perf_counter() - t0; t0 = _t.perf_counter()
for i in range(300): T.clean(f"Línea número {i}: ¿qué tal, señor Muñoz?", "champagne")
check("300 clean() calls are cheap once warm", _t.perf_counter() - t0 < 0.1 and first < 1.5, (round(first, 3), round(_t.perf_counter() - t0, 3)))
check("font objects are cached", T.make_font("luxury", 40) is T.make_font("luxury", 40))
a1 = T.render_text_png("Strict", 640, 360, cache_dir=tmp, strict=True); a2 = T.render_text_png("Strict", 640, 360, cache_dir=tmp, strict=False)
check("strict and non-strict renders are cached separately", a1 != a2)

# ---------------- on-screen collision warnings (pure layout, no MLT)
import live as L
L.CLIPS = {"A": "x"}; L.CLIP_LEN = {"A": 8.0}
def warns(w, h, *extra):
    L.W, L.H, L.FPS = w, h, 24
    return L.layout([{"op": "add", "src": "A"}, *extra])["warnings"]
sub = lambda pos="bottom", a=3.4, b=5.2: {"op": "subtitles", "pos": pos, "cues": [{"start": a, "end": b, "text": "¿Quién trae el balón?"}]}
lt = {"op": "lower_third", "title": "Señor Muñoz", "subtitle": "Director de Proyecto", "start": 3.2, "dur": 2.8}
ttl = {"op": "text", "text": "Gran Inauguración", "start": 0.5, "dur": 2.5, "pos": "top", "size": 0.06}
w1 = warns(1080, 1920, sub(), lt)
check("collision: subtitles bottom + lower third (the real run-A case) warn", len(w1) == 1 and "overlap" in w1[0] and "3.4s to 5.2s" in w1[0], w1)
check("collision: subtitles in the centre + lower third do not warn", warns(1080, 1920, sub("center"), lt) == [])
check("collision: subtitles bottom but at a different time do not warn", warns(1080, 1920, sub(a=0.5, b=2.0), lt) == [])
check("collision: title at the top + lower third do not warn", warns(1080, 1920, ttl, lt) == [])
check("collision: full-frame decoration never warns", warns(1080, 1920, {"op": "graphic", "kind": "frame", "start": 0, "dur": 8}, sub(), ttl) == [])
t2 = lambda s, pos: {"op": "text", "text": "Hola mundo", "start": s, "dur": 2.0, "pos": pos}
check("collision: two texts at the same place and time warn", len(warns(1280, 720, t2(1.0, "bottom"), t2(1.5, "bottom"))) == 1)
check("collision: two texts at different places do not", warns(1280, 720, t2(1.0, "top"), t2(1.5, "bottom")) == [])
check("collision: overlap shorter than 0.1 s is ignored", warns(1280, 720, t2(1.0, "bottom"), t2(2.95, "bottom")) == [])
pipA = {"op": "pip", "src": "A", "start": 1.0, "dur": 3.0, "pos": "top-right", "scale": 0.3}
check("collision: PiP top-right + a wide title at the top warn", len(warns(1080, 1920, pipA, ttl)) == 1)
check("collision: same-edit layers (overlapping cues of ONE subtitle op) never self-warn",
      warns(1280, 720, {"op": "subtitles", "cues": [{"start": 1, "end": 3, "text": "Uno"}, {"start": 2, "end": 4, "text": "Dos"}]}) == [])
many = [{"op": "subtitles", "cues": [{"start": 1, "end": 3, "text": f"Línea {i}"}]} for i in range(6)]
wm = warns(1280, 720, *many)
check("collision: many overlapping pairs are capped with a summary line", len(wm) == 5 and "more overlapping pairs" in wm[-1], wm)
print(f"\n{len(ok)} passed, {len(bad)} failed"); sys.exit(1 if bad else 0)
