"""SVG icons and hand-drawn doodles as overlay images.

* `icon="star"` -> a Lucide icon (assets/svg, ISC), tinted with the template's colour, optionally on a round plate that keeps it
  legible on busy footage. Rasterized with cairosvg at the exact export size (no resampling blur) and cached.
* `icon="doodle-arrow"` -> a hand-drawn arrow/star/circle/underline/burst/check/cross/heart (sketch.py strokes, deterministic).
* a user .svg path is accepted too, after a safety check (size, no scripts, no external references).
SVGs are never fetched from the network and never allowed to reference other files (cairosvg runs with unsafe=False)."""
import difflib, hashlib, io, math, os, re

from PIL import Image, ImageDraw

from .render import sketch
from . import themes
from .config import S

DOODLES = ("doodle-arrow", "doodle-star", "doodle-circle", "doodle-underline", "doodle-burst", "doodle-check", "doodle-cross", "doodle-heart")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
MAX_SVG_BYTES = 1_000_000
UNSAFE = re.compile(r"<script|onload\s*=|onclick\s*=|<foreignObject|<!ENTITY|<image\b|<use\b[^>]*href\s*=\s*[\"'](?!#)|xlink:href\s*=\s*[\"'](?!#)|<style|@import|url\(\s*[\"']?(?!#)", re.I)


def list_icons():
    names = sorted({f[:-4] for d in S.icons_dirs if os.path.isdir(d) for f in os.listdir(d) if f.endswith(".svg")})
    return names + list(DOODLES)


def icon_path(name):
    """Path of a bundled icon, or None for a doodle. Raises ValueError (suggesting close names) for an unknown one."""
    if name in DOODLES:
        return None
    found = next((os.path.join(d, name + ".svg") for d in S.icons_dirs if isinstance(name, str) and NAME_RE.match(name) and os.path.isfile(os.path.join(d, name + ".svg"))), None)
    if found is None:
        close = difflib.get_close_matches(str(name), list_icons(), n=4, cutoff=0.5)
        raise ValueError(f"unknown icon '{name}'" + (f"; did you mean {', '.join(close)}?" if close else "") + " (list_assets(kind='icon') lists them)")
    return found


def check_svg(path):
    """Raise ValueError unless the file is a small, self-contained SVG (no scripts, styles, images or external references)."""
    if os.path.getsize(path) > MAX_SVG_BYTES:
        raise ValueError(f"{os.path.basename(path)} is larger than {MAX_SVG_BYTES // 1000} KB")
    with open(path, "rb") as f:
        text = f.read().decode("utf-8", "replace")
    if "<svg" not in text[:2000].lower():
        raise ValueError(f"{os.path.basename(path)} is not an SVG file")
    m = UNSAFE.search(text)
    if m:
        raise ValueError(f"{os.path.basename(path)} contains something not allowed in an overlay SVG ({m.group(0)[:24].strip()!r}); use a plain SVG without scripts, styles, images or external references")


def svg_aspect(path):
    """width / height from the viewBox (or width/height attributes) of an SVG."""
    head = open(path, "rb").read(4000).decode("utf-8", "replace")
    vb = re.search(r'viewBox\s*=\s*["\']\s*[-\d.]+[ ,]+[-\d.]+[ ,]+([\d.]+)[ ,]+([\d.]+)', head)
    if vb and float(vb.group(2)) > 0:
        return float(vb.group(1)) / float(vb.group(2))
    w, h = re.search(r'\swidth\s*=\s*["\']([\d.]+)', head), re.search(r'\sheight\s*=\s*["\']([\d.]+)', head)
    if w and h and float(h.group(1)) > 0:
        return float(w.group(1)) / float(h.group(1))
    raise ValueError(f"{os.path.basename(path)}: cannot read its size (needs a viewBox or width and height)")


def plate_style(th):
    """(plate fill RGBA, outline RGBA or None, outline width as a fraction of the plate, default icon colour) from the theme's `plate`."""
    p = th.plate
    col = p.get("color", "accent")
    return th.color(p.get("fill", "paper")), th.color(p.get("outline")), float(p.get("width", 0)), (getattr(th, col) if col in ("ink", "paper", "accent", "accent2", "muted") else col)


def _tint(svg_text, color):
    return svg_text.replace("currentColor", color)


def _plate(size, th):
    from . import shapes
    fill, outline, ow, _ = plate_style(th)
    ss = 3
    img = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = max(2, int(size * ss * 0.02))
    box = [pad, pad, size * ss - pad, size * ss - pad]
    shapes.of(th).plate(img, d, box, size, ss, fill, outline, ow, th)
    return img.resize((size, size), Image.LANCZOS)


def _doodle(name, size, color):
    ss = 3
    S = size * ss
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = sketch.rng("doodle", name, size)
    col = themes.rgb(color) + (255,)
    lw = S * 0.06
    if name == "doodle-arrow":
        sketch.arrow(d, (S * 0.1, S * 0.85), (S * 0.88, S * 0.18), col, lw, r)
    elif name == "doodle-star":
        sketch.star(d, S * 0.5, S * 0.53, S * 0.4, col, lw, r)
    elif name == "doodle-circle":
        sketch.circle(d, S * 0.5, S * 0.5, S * 0.38, col, lw, r)
    elif name == "doodle-underline":
        sketch.scribble_underline(d, S * 0.06, S * 0.94, S * 0.5, col, lw * 1.2, r)
    elif name == "doodle-burst":
        for k in range(12):
            a = k * math.pi / 6 + r.uniform(-0.08, 0.08)
            sketch.line(d, (S * 0.5 + S * 0.2 * math.cos(a), S * 0.5 + S * 0.2 * math.sin(a)), (S * 0.5 + S * r.uniform(0.38, 0.47) * math.cos(a), S * 0.5 + S * r.uniform(0.38, 0.47) * math.sin(a)), col, lw * 0.8, r, double=False)
    elif name == "doodle-check":
        sketch.line(d, (S * 0.14, S * 0.55), (S * 0.4, S * 0.8), col, lw * 1.3, r, double=False)
        sketch.line(d, (S * 0.4, S * 0.8), (S * 0.88, S * 0.2), col, lw * 1.3, r, double=False)
    elif name == "doodle-cross":
        sketch.line(d, (S * 0.18, S * 0.18), (S * 0.82, S * 0.82), col, lw * 1.2, r)
        sketch.line(d, (S * 0.82, S * 0.18), (S * 0.18, S * 0.82), col, lw * 1.2, r)
    elif name == "doodle-heart":
        pts = [(S * (0.5 + 0.030 * 16 * math.sin(t) ** 3), S * (0.46 - 0.030 * (13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)) * 0.9))
               for t in [i * 2 * math.pi / 60 + 0.1 for i in range(64)]]
        sketch._poly(d, pts, col, lw)
    return img.resize((size, size), Image.LANCZOS)


def render_layer(L, W, cache_dir):
    """PNG path for an icon/SVG image layer (from live.layout): rasterized at the export size, tinted, plated, cached."""
    import cairosvg
    th = themes.get(L.get("theme"))
    size = max(8, int(round(W * L["scale"])))
    key = f"v1|{L.get('icon') or L.get('svg')}|{size}|{L.get('color')}|{L.get('plate')}|{th.name}|{th.accent}"
    if L.get("svg"):
        key += f"|{os.path.getmtime(L['svg'])}|{os.path.getsize(L['svg'])}"
    out = os.path.join(cache_dir, f"icon_{hashlib.sha1(key.encode()).hexdigest()[:16]}.png")
    if os.path.exists(out):
        return out
    color = L.get("color") or "#ffffff"
    inner = size if not L.get("plate") else int(size * 0.56)
    if L.get("icon") in DOODLES:
        glyph = _doodle(L["icon"], inner, color)
    else:
        path = L["svg"]
        check_svg(path)
        asp = svg_aspect(path)
        w, h = (inner, inner) if L.get("icon") else (inner, max(1, int(round(inner / asp))))
        svg = _tint(open(path, encoding="utf-8", errors="replace").read(), color)
        png = cairosvg.svg2png(bytestring=svg.encode("utf-8"), output_width=w, output_height=h, unsafe=False)
        glyph = Image.open(io.BytesIO(png)).convert("RGBA")
    if L.get("plate"):
        base = _plate(size, th)
        base.alpha_composite(glyph, ((size - glyph.width) // 2, (size - glyph.height) // 2))
        glyph = base
    os.makedirs(cache_dir, exist_ok=True)
    tmp = out + f".{os.getpid()}.tmp"
    glyph.save(tmp, format="PNG")
    os.replace(tmp, out)
    return out
