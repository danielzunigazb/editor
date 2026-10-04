"""Deterministic text -> transparent PNG rendering for titles and subtitles, with a small style system.

Why not MLT's qtext? Text drawn by Qt depends on the platform's font setup; here fixed TTFs (bundled in ./fonts, all
SIL OFL) are drawn with Pillow, so the same input always gives the same pixels, accents/ñ/¿ are guaranteed (or
rejected up front), and text can never overflow the frame: it is wrapped, shrunk to fit, and refused with a clear
message if it cannot fit.

Styles: STYLES (the engine's 'classic' fallback + every text style defined by the loaded theme packs).
"""
import functools, hashlib, os, re

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..config import S

FONT_DIRS_EXTRA = []                      # font folders of the loaded theme packs (packs.load_all appends them); searched after S.fonts_dirs
POSITIONS = ("bottom", "center", "top")
ORNAMENTS = ("none", "line", "diamond")
MAX_CHARS = 200
MAX_LINES = 4
MIN_SHRINK = 0.55          # never shrink below 55% of the requested size
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


# font=None -> system fallback (classic). tracking in em. shadow: dx, dy, blur in em, alpha 0-1.
# halo: (width em, alpha) hairline dark outline under the fill so light text stays readable on bright footage.
# Optional keys: box_width (outline width in em, default 0.03) | axes {"wght","opsz","wdth"} | box_fill / box_outline (RGBA) / box_radius / box_default | orn_color | glow (colour, blur em, alpha) |
#   outline (colour, width em) | sticker (dx em, dy em, colour) hard offset shadow | pixel (glyphs without anti-aliasing).
# Only the engine's fallback style lives here; every other style is defined by a theme pack (packs/themes/*/theme.json -> text_styles).


class _Styles(dict):
    """The style table. It fills itself from the theme packs on first use (registry.load), so importing this module stays cheap and a
    removed pack takes its styles with it."""
    def _ensure(self):
        from .. import registry
        registry.load()

    def raw(self):
        return self

    def __getitem__(self, k):
        self._ensure(); return dict.__getitem__(self, k)

    def __contains__(self, k):
        self._ensure(); return dict.__contains__(self, k)

    def __iter__(self):
        self._ensure(); return dict.__iter__(self)

    def __len__(self):
        self._ensure(); return dict.__len__(self)

    def get(self, k, default=None):
        self._ensure(); return dict.get(self, k, default)

    def keys(self):
        self._ensure(); return dict.keys(self)

    def items(self):
        self._ensure(); return dict.items(self)

    def values(self):
        self._ensure(); return dict.values(self)


STYLES = _Styles({
    "classic": dict(label="Sans bold, white with a black outline (the original look)",
                    font=None, wght=None, tracking=0.0, upper=False, fill="#ffffff", stroke=True, shadow=None, halo=None,
                    ornament="none"),
})
_MISSING = {}              # font cache key -> notdef mask bytes (to detect missing glyphs)


def _pd(img, st):
    """ImageDraw for a mask; `pixel` styles draw glyphs without anti-aliasing (hard pixel edges)."""
    d = ImageDraw.Draw(img)
    if st.get("pixel"):
        d.fontmode = "1"
    return d


def font_path(style="classic"):
    f = STYLES[style]["font"]
    if f:
        dirs = list(S.fonts_dirs) + FONT_DIRS_EXTRA
        for d in dirs:
            p = os.path.join(d, f)
            if os.path.isfile(p):
                return p
        raise RuntimeError(f"font file {f} (style {style}) not found in {dirs} (setting fonts_dirs, or the theme pack's fonts/ folder)")
    for p in S.system_fonts:
        if os.path.isfile(p):
            return p
    raise RuntimeError("no usable system font found; install fonts-dejavu-core (apt-get install fonts-dejavu-core)")


def make_font(style, px):
    return _font(style, max(6, int(round(px))))


@functools.lru_cache(maxsize=256)
def _font(style, px):                   # loading a TTF + setting its variation axis is slow; layout() asks for it constantly
    st = STYLES[style]
    font = ImageFont.truetype(font_path(style), px)
    if st.get("axes"):                                   # several axes (weight, optical size, width): set by NAME, in the font's own order
        want = {"wght": "weight", "opsz": "optical", "wdth": "width"}
        vals = []
        for ax in font.get_variation_axes():
            nm = ax["name"] if isinstance(ax["name"], str) else ax["name"].decode()
            key = next((k for k, frag in want.items() if frag in nm.lower()), None)
            v = st["axes"].get(key, ax["default"]) if key else ax["default"]
            vals.append(min(max(v, ax["minimum"]), ax["maximum"]))
        font.set_variation_by_axes(vals)
    elif st.get("wght"):
        font.set_variation_by_axes([st["wght"]])
    return font


def validate_style(style):
    if style not in STYLES:
        raise ValueError(f"unknown style '{style}'; choose one of {tuple(STYLES)}")


def _notdef(font, key):
    if key not in _MISSING:
        _MISSING[key] = bytes(font.getmask("\U0010FFFF"))   # an unassigned code point renders the .notdef glyph
    return _MISSING[key]


@functools.lru_cache(maxsize=8192)
def clean(text, style="classic"):
    """Normalise text: CRLF -> \\n, strip control chars, trim. Raises ValueError on empty/too long/unsupported."""
    validate_style(style)
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    text = "".join(ch for ch in text if ch == "\n" or (ord(ch) >= 32 and ord(ch) != 127)).strip()
    if not text:
        raise ValueError("text is empty")
    if len(text) > MAX_CHARS:
        raise ValueError(f"text is {len(text)} characters; the limit is {MAX_CHARS} (split it into several texts)")
    f = make_font(style, 40)
    nd = _notdef(f, style)
    probe = text.upper() if STYLES[style]["upper"] else text
    bad = sorted({ch for ch in probe if ch not in "\n " and bytes(f.getmask(ch)) == nd})
    if bad:
        raise ValueError(f"unsupported character(s) {''.join(bad)!r} (the '{style}' font has no glyph for it; Latin text "
                         f"with accents, ñ, ¿ ¡ is supported, but CJK/Arabic/etc. and newer emoji are not)")
    return text


def text_width(line, font, track_px):
    """Width of a line including letter-spacing (kerning inside the font is preserved by measuring prefixes)."""
    return font.getlength(line) + track_px * max(0, len(line) - 1)


def draw_tracked(draw, x, y, line, font, track_px, fill, **kw):
    """Draw a line char by char with letter-spacing; positions come from prefix lengths so kerning is kept."""
    if not track_px:
        draw.text((x, y), line, font=font, fill=fill, **kw)
        return
    for i, ch in enumerate(line):
        if ch != " ":
            draw.text((x + font.getlength(line[:i]) + track_px * i, y), ch, font=font, fill=fill, **kw)


def gradient(w, h, stops):
    """Vertical gradient image (RGBA) through [(pos 0-1, '#rrggbb'), ...]."""
    col = [(p, tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))) for p, c in stops]
    strip = Image.new("RGB", (1, max(1, h)))
    px = strip.load()
    for y in range(max(1, h)):
        t = y / max(1, h - 1)
        for (p0, c0), (p1, c1) in zip(col, col[1:]):
            if p0 <= t <= p1:
                k = 0 if p1 == p0 else (t - p0) / (p1 - p0)
                px[0, y] = tuple(int(round(a + (b - a) * k)) for a, b in zip(c0, c1))
                break
    return strip.resize((max(1, w), max(1, h))).convert("RGBA")


def _wrap(text, font, max_w, track_px, stroke):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            trial = f"{cur} {word}".strip() if cur else word
            if text_width(trial, font, track_px) + 2 * stroke <= max_w:
                cur = trial
                continue
            if cur:
                lines.append(cur)
            cur = word
            while text_width(cur, font, track_px) + 2 * stroke > max_w and len(cur) > 1:   # a word wider than the frame
                k = len(cur) - 1
                while k > 1 and text_width(cur[:k], font, track_px) + 2 * stroke > max_w:
                    k -= 1
                lines.append(cur[:k])
                cur = cur[k:]
        lines.append(cur)
    return lines


def layout_text(text, W, H, size, strict=True, style="classic", uppercase=None):
    """Return (font, lines, stroke, track_px) that fit inside 90% x 40% of the frame.
    strict=True raises if it cannot fit in MAX_LINES lines at >= MIN_SHRINK of the requested size;
    strict=False never raises (used at render time so a render cannot fail after validation passed)."""
    st = STYLES[style]
    if st["upper"] if uppercase is None else uppercase:
        text = text.upper()
    px = size * H
    max_w = (0.84 if st.get("glow") else 0.9) * W      # a neon glow bleeds past the glyphs: keep it inside the frame too
    while True:
        font = make_font(style, px)
        stroke = max(1, int(round(px * 0.07))) if st["stroke"] else 0
        track = st["tracking"] * font.size
        lines = _wrap(text, font, max_w, track, stroke)
        asc, desc = font.getmetrics()
        height = len(lines) * (asc + desc) * 1.12
        if len(lines) <= MAX_LINES and height <= 0.4 * H:
            return font, lines, stroke, track
        if px * 0.92 < size * H * MIN_SHRINK or px <= 8:
            if strict:
                raise ValueError("text is too long to fit on screen at this size; shorten it, use a smaller size, or "
                                 "split it into several texts")
            return font, lines[:MAX_LINES], stroke, track
        px *= 0.92


def _ornament(draw, cx, y, width, font_px, kind, color=None):
    """Thin rule (optionally with a centred diamond) at vertical position y, in `color` (default: setting ornament_color)."""
    color = color or S.ornament_color
    t = max(1, int(round(font_px * 0.03)))
    half = width / 2
    if kind == "diamond":
        d = font_px * 0.15
        gap = d * 1.9
        draw.line([(cx - half, y), (cx - gap, y)], fill=color, width=t)
        draw.line([(cx + gap, y), (cx + half, y)], fill=color, width=t)
        draw.polygon([(cx, y - d), (cx + d, y), (cx, y + d), (cx - d, y)], fill=color)
    else:
        draw.line([(cx - half, y), (cx + half, y)], fill=color, width=t)


def render_text_image(text, W, H, pos="bottom", size=0.06, color=None, box=False, strict=True, style="classic",
                      uppercase=None, ornament=None):
    """Render `text` onto a transparent W x H RGBA image (PIL)."""
    validate_style(style)
    if pos not in POSITIONS:
        raise ValueError(f"pos must be one of {POSITIONS}")
    if not 0.02 <= size <= 0.2:
        raise ValueError("size is a fraction of the frame height and must be between 0.02 and 0.2")
    if color is not None and not COLOR_RE.match(color):
        raise ValueError("color must look like #RRGGBB")
    if ornament not in (None,) + ORNAMENTS:
        raise ValueError(f"ornament must be one of {ORNAMENTS}")
    st = STYLES[style]
    text = clean(text, style)
    up = st["upper"] if uppercase is None else uppercase
    orn = st["ornament"] if ornament is None else ornament
    font, lines, stroke, track = layout_text(text, W, H, size, strict, style, uppercase)
    asc, desc = font.getmetrics()
    lh = (asc + desc) * 1.12
    widths = [text_width(l, font, track) for l in lines]
    block_w, block_h = max(widths), lh * len(lines)
    orn_gap = font.size * 0.55 if orn != "none" else 0       # space the rule takes under/over the text
    total_h = block_h + orn_gap
    margin = 0.08 * H
    y0 = {"bottom": H - margin - total_h, "center": (H - total_h) / 2, "top": margin}[pos]
    ty = y0 + (orn_gap if (orn != "none" and pos == "bottom") else 0)     # bottom: rule above; else: rule below
    solid = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5)) if color else None
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    if box:                                                  # box behind everything else
        d = ImageDraw.Draw(img)
        pad = font.size * 0.4
        bw = max(block_w, 0.35 * W if orn != "none" else 0) + 2 * pad
        x0 = (W - bw) / 2
        rect = [x0, y0 - pad * 0.55, x0 + bw, y0 + total_h + pad * 0.25]
        bfill = st.get("box_fill", (0, 0, 0, 150))
        bline = st.get("box_outline")
        d.rounded_rectangle(rect, radius=pad * st.get("box_radius", 0.5), fill=bfill, outline=bline,
                            width=max(1, int(font.size * st.get("box_width", 0.03))) if bline else 0)

    mask = Image.new("L", (W, H), 0)                         # the glyphs, as an alpha mask
    md = _pd(mask, st)
    for i, (line, w) in enumerate(zip(lines, widths)):
        draw_tracked(md, (W - w) / 2, ty + i * lh, line.upper() if up and not st["upper"] else line, font, track, 255)

    if st.get("halo"):                                           # hairline dark halo (legibility on bright footage)
        hw, ha = st["halo"]
        hm = Image.new("L", (W, H), 0)
        hd = _pd(hm, st)
        for i, (line, w) in enumerate(zip(lines, widths)):
            draw_tracked(hd, (W - w) / 2, ty + i * lh, line.upper() if up and not st["upper"] else line, font, track, 255,
                         stroke_width=max(1, int(round(hw * font.size))), stroke_fill=255)
        halo = Image.new("RGBA", (W, H), (0, 0, 0, 255)); halo.putalpha(hm.point(lambda v: int(v * ha)))
        img = Image.alpha_composite(img, halo)
    if st["shadow"]:                                         # soft drop shadow from the same mask
        dx, dy, blur, alpha = st["shadow"]
        sh = Image.new("L", (W, H), 0)
        sh.paste(mask, (int(round(dx * font.size)), int(round(dy * font.size))))
        sh = sh.filter(ImageFilter.GaussianBlur(max(0.5, blur * font.size))).point(lambda v: int(v * alpha))
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 255)); shadow.putalpha(sh)
        img = Image.alpha_composite(img, shadow)
    if st.get("glow"):                                       # neon: the glyphs blurred and tinted, under the fill
        gc, gb, ga = st["glow"]
        gm = mask.filter(ImageFilter.GaussianBlur(max(1.0, gb * font.size))).point(lambda v: min(255, int(v * 2.2 * ga)))
        glow = Image.new("RGBA", (W, H), tuple(int(gc[i:i + 2], 16) for i in (1, 3, 5)) + (255,)); glow.putalpha(gm)
        img = Image.alpha_composite(img, glow)
    if st.get("sticker"):                                    # hard offset shadow (sticker look), same silhouette incl. the outline
        sx, sy, sc = st["sticker"]
        ow = st.get("outline", (None, 0))[1]
        sm = Image.new("L", (W, H), 0); sd_ = _pd(sm, st)
        for i, (line, w) in enumerate(zip(lines, widths)):
            draw_tracked(sd_, (W - w) / 2 + sx * font.size, ty + i * lh + sy * font.size, line.upper() if up and not st["upper"] else line, font, track, 255,
                         stroke_width=max(1, int(round(ow * font.size))) if ow else 0, stroke_fill=255)
        sticker = Image.new("RGBA", (W, H), tuple(int(sc[i:i + 2], 16) for i in (1, 3, 5)) + (255,)); sticker.putalpha(sm)
        img = Image.alpha_composite(img, sticker)
    if st.get("outline"):                                    # opaque coloured outline under the fill
        oc, ow = st["outline"]
        om = Image.new("L", (W, H), 0); od_ = _pd(om, st)
        for i, (line, w) in enumerate(zip(lines, widths)):
            draw_tracked(od_, (W - w) / 2, ty + i * lh, line.upper() if up and not st["upper"] else line, font, track, 255,
                         stroke_width=max(1, int(round(ow * font.size))), stroke_fill=255)
        outl = Image.new("RGBA", (W, H), tuple(int(oc[i:i + 2], 16) for i in (1, 3, 5)) + (255,)); outl.putalpha(om)
        img = Image.alpha_composite(img, outl)
    if stroke:                                               # classic outline
        od = ImageDraw.Draw(img)
        for i, (line, w) in enumerate(zip(lines, widths)):
            od.text(((W - w) / 2, ty + i * lh), line, font=font, fill=(0, 0, 0, 255), stroke_width=stroke,
                    stroke_fill=(0, 0, 0, 255))
    if solid is None and isinstance(st["fill"], tuple):      # gradient (stops) across the text block only
        gtop, gh = int(ty), max(1, int(block_h))
        grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        grad.paste(gradient(W, gh, st["fill"]), (0, gtop))
        grad.putalpha(mask)
        img = Image.alpha_composite(img, grad)
    else:                                                    # explicit colour overrides the style's own fill
        rgb = solid or tuple(int(st["fill"][i:i + 2], 16) for i in (1, 3, 5))
        flat = Image.new("RGBA", (W, H), rgb + (255,)); flat.putalpha(mask)
        img = Image.alpha_composite(img, flat)
    if orn != "none":
        oy = (ty - orn_gap * 0.5) if pos == "bottom" else (ty + block_h + orn_gap * 0.05)
        _ornament(ImageDraw.Draw(img), W / 2, oy + (orn_gap * 0.45 if pos != "bottom" else 0), min(0.5 * W, block_w * 0.8 + font.size * 2), font.size, orn,
                  st.get("orn_color", S.ornament_color))
    return img


def render_text_png(text, W, H, pos="bottom", size=0.06, color=None, box=False, cache_dir=None, strict=True,
                    style="classic", uppercase=None, ornament=None):
    """Render `text` to a transparent W x H PNG and return its path (cached by content hash)."""
    validate_style(style)
    text = clean(text, style)
    key = hashlib.sha1(f"v3|{text}|{W}|{H}|{pos}|{size}|{color}|{box}|{style}|{uppercase}|{ornament}|{strict}".encode()).hexdigest()[:16]
    out = os.path.join(cache_dir or S.tmp_dir, f"txt_{key}.png")
    if os.path.exists(out):                # check the cache BEFORE rendering (it used to render first, then look)
        return out
    img = render_text_image(text, W, H, pos, size, color, box, strict, style, uppercase, ornament)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + f".{os.getpid()}.tmp"
    img.save(tmp, format="PNG")
    os.replace(tmp, out)
    return out


_TS = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")


def parse_srt(path, max_cues=300):
    """Parse an .srt file into [{"start","end","text"}] (seconds). Lenient on whitespace/BOM/CRLF/tags,
    strict on timestamps (reports the block number)."""
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise ValueError(f"subtitle file not found: {path}")
    if os.path.getsize(path) > 1_000_000:
        raise ValueError("subtitle file is larger than 1 MB")
    raw = open(path, "rb").read()
    try:
        txt = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        txt = raw.decode("latin-1")            # common for old .srt files; accents survive
    txt = txt.replace("\r\n", "\n").replace("\r", "\n")
    cues = []
    for n, block in enumerate(re.split(r"\n\s*\n", txt.strip()), 1):
        lines = [l for l in block.split("\n") if l.strip()]
        if not lines:
            continue
        ti = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if ti is None:
            raise ValueError(f"subtitle block {n}: no 'start --> end' line")
        m = _TS.search(lines[ti])
        if not m:
            raise ValueError(f"subtitle block {n}: bad timestamp line {lines[ti]!r} (expected 00:00:01,000 --> 00:00:02,500)")
        g = m.groups()
        ms = lambda s: int(s.ljust(3, "0")[:3])
        start = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + ms(g[3]) / 1000
        end = int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + ms(g[7]) / 1000
        body = re.sub(r"<[^>]+>|\{\\[^}]*\}", "", "\n".join(lines[ti + 1:])).strip()
        if not body:
            continue
        cues.append({"start": start, "end": end, "text": body})
        if len(cues) > max_cues:
            raise ValueError(f"more than {max_cues} subtitle cues; split the file")
    if not cues:
        raise ValueError("no subtitle cues found in the file")
    return cues


def __getattr__(name):                    # STYLE_NAMES follows the loaded packs
    if name == "STYLE_NAMES":
        return tuple(STYLES)
    raise AttributeError(name)
