"""SVG icons and hand-drawn doodles as overlay images.

* `icon="star"` -> a Lucide icon (assets/svg, ISC), tinted with the template's colour, optionally on a round plate that keeps it
  legible on busy footage. Rasterized with cairosvg at the exact export size (no resampling blur) and cached.
* `icon="doodle-arrow"` -> a hand-drawn arrow/star/circle/underline/burst/check/cross/heart (sketch.py strokes, deterministic).
* a user .svg path is accepted too, after a safety check (size, no scripts, no external references).
SVGs are never fetched from the network and never allowed to reference other files (cairosvg runs with unsafe=False)."""
import difflib, hashlib, io, math, os, re

from PIL import Image, ImageDraw

import sketch
import themes

HERE = os.path.dirname(os.path.abspath(__file__))
SVG_DIR = os.path.join(HERE, "assets", "svg")
DOODLES = ("doodle-arrow", "doodle-star", "doodle-circle", "doodle-underline", "doodle-burst", "doodle-check", "doodle-cross", "doodle-heart")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
MAX_SVG_BYTES = 1_000_000
UNSAFE = re.compile(r"<script|onload\s*=|onclick\s*=|<foreignObject|<!ENTITY|<image\b|<use\b[^>]*href\s*=\s*[\"'](?!#)|xlink:href\s*=\s*[\"'](?!#)|<style|@import|url\(\s*[\"']?(?!#)", re.I)


def list_icons():
    names = sorted(f[:-4] for f in os.listdir(SVG_DIR) if f.endswith(".svg")) if os.path.isdir(SVG_DIR) else []
    return names + list(DOODLES)


def icon_path(name):
    """Path of a bundled icon, or None for a doodle. Raises ValueError (suggesting close names) for an unknown one."""
    if name in DOODLES:
        return None
    if not isinstance(name, str) or not NAME_RE.match(name) or not os.path.isfile(os.path.join(SVG_DIR, name + ".svg")):
        close = difflib.get_close_matches(str(name), list_icons(), n=4, cutoff=0.5)
        raise ValueError(f"unknown icon '{name}'" + (f"; did you mean {', '.join(close)}?" if close else "") + " (list_assets(kind='icon') lists them)")
    return os.path.join(SVG_DIR, name + ".svg")


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
    """(plate fill RGBA, outline RGBA or None, outline width as a fraction of the plate, default icon colour) for a template."""
    c = themes.rgb
    table = {
        "luxury": ((8, 8, 11, 215), c(th.accent) + (200,), 0.035, th.accent),
        "corporate": ((255, 255, 255, 245), None, 0, th.accent),
        "academic": (c(th.paper) + (245,), c(th.accent) + (255,), 0.03, th.accent),
        "sketch": (c(th.paper) + (248,), c(th.ink) + (255,), 0.05, th.accent),
        "tech": ((10, 15, 28, 232), c(th.accent) + (255,), 0.03, th.accent),
        "minimal": ((0, 0, 0, 125), None, 0, "#ffffff"),
        "playful": (c(th.accent2) + (255,), c(th.ink) + (255,), 0.06, "#ffffff"),
        "neobrutalism": (c(th.accent) + (255,), c(th.ink) + (255,), 0.07, th.ink),
        "terracotta": (c(th.paper) + (245,), c(th.accent) + (255,), 0.03, th.accent),
        "cinema": ((0, 0, 0, 150), (255, 255, 255, 200), 0.03, "#ffffff"),
        "terminal": (c(th.paper) + (238,), c(th.accent) + (255,), 0.04, th.accent),
        "arcade": (c(th.paper) + (255,), c(th.accent) + (255,), 0.08, th.accent),
        "riso": (c(th.paper) + (248,), c(th.accent2) + (255,), 0.04, th.accent),
        "saas": (c(th.paper) + (240,), c(th.accent2) + (255,), 0.03, th.accent),
        "glass": (c(th.paper) + (150,), (255, 255, 255, 150), 0.03, "#ffffff"),
    }
    return table[th.name]


def _tint(svg_text, color):
    return svg_text.replace("currentColor", color)


def _plate(size, th):
    fill, outline, ow, _ = plate_style(th)
    ss = 3
    img = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = max(2, int(size * ss * 0.02))
    box = [pad, pad, size * ss - pad, size * ss - pad]
    if th.name == "playful":                                       # hard sticker shadow
        d.ellipse([box[0] + size * ss * 0.03, box[1] + size * ss * 0.04, box[2] + size * ss * 0.03, box[3] + size * ss * 0.04], fill=themes.rgb(th.ink) + (255,))
    if th.shape in ("brutal", "pixel", "term"):                    # square plates; brutal/pixel add a hard offset block
        if th.shape != "term":
            sh = max(2, int(size * ss * 0.05))
            d.rectangle([box[0] + sh, box[1] + sh, box[2], box[3]], fill=themes.rgb(th.ink if th.shape == "brutal" else th.accent2) + (255,))
            box = [box[0], box[1], box[2] - sh, box[3] - sh]
        d.rectangle(box, fill=fill, outline=outline, width=max(1, int(size * ss * ow)) if outline else 0)
        return img.resize((size, size), Image.LANCZOS)
    if th.name == "riso":                                          # pink ink block misregistered under the paper disc
        sh = max(2, int(size * ss * 0.035))
        d.ellipse([box[0] + sh, box[1] + sh, box[2], box[3]], fill=themes.rgb(th.accent) + (235,))
        box = [box[0], box[1], box[2] - sh, box[3] - sh]
    if th.name == "sketch":
        r = sketch.rng("plate", size)
        d.ellipse(box, fill=fill)
        sketch.circle(d, size * ss / 2, size * ss / 2, size * ss * 0.46, outline, max(3, size * ss * ow), r, turns=1.06)
    else:
        d.ellipse(box, fill=fill, outline=outline, width=max(1, int(size * ss * ow)) if outline else 0)
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
