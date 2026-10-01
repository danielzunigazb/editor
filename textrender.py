"""Deterministic text -> transparent PNG rendering for titles and subtitles.

Why not MLT's qtext? Text drawn by Qt depends on the platform's font setup; here a fixed TTF is drawn with Pillow,
so the same input always gives the same pixels, accents/ñ/¿ are guaranteed (or rejected up front), and
text can never overflow the frame: it is wrapped, shrunk to fit, and refused with a clear message if it cannot fit.
"""
import hashlib, os, re

from PIL import Image, ImageDraw, ImageFont

FONT_FILES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
]
POSITIONS = ("bottom", "center", "top")
MAX_CHARS = 200
MAX_LINES = 4
MIN_SHRINK = 0.55          # never shrink below 55% of the requested size
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_MISSING = {}              # font path -> notdef mask bytes (to detect missing glyphs)


def font_path():
    for f in FONT_FILES:
        if os.path.isfile(f):
            return f
    raise RuntimeError("no usable font found; install fonts-dejavu-core (apt-get install fonts-dejavu-core)")


def _font(px):
    return ImageFont.truetype(font_path(), max(6, int(round(px))))


def _notdef(font):
    key = font.path
    if key not in _MISSING:
        _MISSING[key] = bytes(font.getmask("\U0010FFFF"))   # an unassigned code point renders the .notdef glyph
    return _MISSING[key]


def clean(text):
    """Normalise text: CRLF -> \\n, strip control chars, trim. Raises ValueError on empty/too long/unsupported."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    text = "".join(ch for ch in text if ch == "\n" or (ord(ch) >= 32 and ord(ch) != 127)).strip()
    if not text:
        raise ValueError("text is empty")
    if len(text) > MAX_CHARS:
        raise ValueError(f"text is {len(text)} characters; the limit is {MAX_CHARS} (split it into several texts)")
    f = _font(40)
    nd = _notdef(f)
    bad = sorted({ch for ch in text if ch not in "\n " and bytes(f.getmask(ch)) == nd})
    if bad:
        raise ValueError(f"unsupported character(s) {''.join(bad)!r} (the font has no glyph for it; Latin text with "
                         f"accents, ñ, ¿ ¡ is supported, but CJK/Arabic/etc. and newer emoji are not)")
    return text


def _wrap(text, font, max_w, stroke):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            trial = f"{cur} {word}".strip() if cur else word
            if font.getlength(trial, features=None) + 2 * stroke <= max_w:
                cur = trial
                continue
            if cur:
                lines.append(cur)
            cur = word
            while font.getlength(cur) + 2 * stroke > max_w and len(cur) > 1:   # a single word wider than the frame
                k = len(cur) - 1
                while k > 1 and font.getlength(cur[:k]) + 2 * stroke > max_w:
                    k -= 1
                lines.append(cur[:k])
                cur = cur[k:]
        lines.append(cur)
    return lines


def layout_text(text, W, H, size, strict=True):
    """Return (font, lines, stroke) that fit inside 90% x 40% of the frame.
    strict=True raises if it cannot fit in MAX_LINES lines at >= MIN_SHRINK of the requested size;
    strict=False never raises (used at render time so a render cannot fail after validation passed)."""
    px = size * H
    max_w = 0.9 * W
    while True:
        font = _font(px)
        stroke = max(1, int(round(px * 0.07)))
        lines = _wrap(text, font, max_w, stroke)
        asc, desc = font.getmetrics()
        height = len(lines) * (asc + desc) * 1.12
        if len(lines) <= MAX_LINES and height <= 0.4 * H:
            return font, lines, stroke
        if px * 0.92 < size * H * MIN_SHRINK or px <= 8:
            if strict:
                raise ValueError("text is too long to fit on screen at this size; shorten it, use a smaller size, or "
                                 "split it into several texts")
            return font, lines[:MAX_LINES], stroke
        px *= 0.92


def render_text_png(text, W, H, pos="bottom", size=0.06, color="#ffffff", box=False, cache_dir=None, strict=True):
    """Render `text` onto a transparent W x H PNG and return its path (cached by content hash)."""
    if pos not in POSITIONS:
        raise ValueError(f"pos must be one of {POSITIONS}")
    if not 0.02 <= size <= 0.2:
        raise ValueError("size is a fraction of the frame height and must be between 0.02 and 0.2")
    if not COLOR_RE.match(color or ""):
        raise ValueError("color must look like #RRGGBB")
    text = clean(text)
    key = hashlib.sha1(f"{text}|{W}|{H}|{pos}|{size}|{color}|{box}".encode()).hexdigest()[:16]
    out = os.path.join(cache_dir or "/tmp", f"txt_{key}.png")
    font, lines, stroke = layout_text(text, W, H, size, strict)
    if os.path.exists(out):
        return out
    asc, desc = font.getmetrics()
    lh = (asc + desc) * 1.12
    block_h = lh * len(lines)
    margin = 0.08 * H
    y0 = {"bottom": H - margin - block_h, "center": (H - block_h) / 2, "top": margin}[pos]
    rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
    lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    outline = (0, 0, 0, 255) if lum > 110 else (255, 255, 255, 255)   # keep it readable on any background
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    widths = [font.getlength(l) for l in lines]
    if box:
        pad = font.size * 0.35
        bw = max(widths) + 2 * pad
        x0 = (W - bw) / 2
        d.rounded_rectangle([x0, y0 - pad * 0.6, x0 + bw, y0 + block_h + pad * 0.2], radius=pad * 0.5, fill=(0, 0, 0, 150))
    for i, (line, w) in enumerate(zip(lines, widths)):
        d.text(((W - w) / 2, y0 + i * lh), line, font=font, fill=rgb + (255,), stroke_width=0 if box else stroke,
               stroke_fill=outline)
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
