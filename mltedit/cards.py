"""Full-frame cards in the project's template, drawn by card_layout plugins (plugins/card_layouts).

A card is a full-frame picture (procedural background + the theme's type) turned by ffmpeg into a short mp4 with a silent audio
track, so the editor treats it as any other source: add it with add_clip, dissolve into it with crossfade. No engine changes.
Everything is drawn with PIL (no numpy, no external images) and is deterministic: same card -> same pixels."""
import hashlib, os, random, subprocess

from PIL import Image, ImageDraw

from . import registry, shapes
from .render import text as T

MAX_ITEMS, MAX_ITEM_CHARS = 5, 60
c_ = shapes._c


def _card(th):
    """The theme's card options with their defaults (theme.json -> card)."""
    c = th.card
    return {"title": c.get("title", th.ink), "sub": c.get("sub", th.muted), "accent": c.get("accent", th.accent), "effects": c.get("effects", False),
            "align": c.get("align", "center"), "vertical_bar": c.get("vertical_bar", False), "rule": c.get("rule", "bar"),
            "list_role": c.get("list_role", "sub"), "stat_role": c.get("stat_role", "accent"),
            "bento": {"title_y": 0.07, "top": 0.28, "bottom": 0.90, **c.get("bento", {})}}


# ------------------------------------------------------------------------------------------------ backgrounds
def background(W, H, th):
    """The theme's card background: the card_bg plugin it names (plugins/card_bgs), seeded per theme and size so it is deterministic."""
    return registry.get("card_bg", th.card_bg)(W, H, th, random.Random(f"bg|{th.name}|{W}x{H}"))


# ------------------------------------------------------------------------------------------------ text helpers
def _block(text, style, px, max_w, max_lines, strict=True):
    """Largest font <= px (down to 55%) in which `text` wraps into <= max_lines lines of max_w. -> (font, track, lines)."""
    st = T.STYLES[style]
    if st["upper"]:
        text = text.upper()
    cur = px
    while True:
        f = T.make_font(style, cur)
        track = st["tracking"] * f.size
        lines = T._wrap(text, f, max_w, track, 0)
        if len(lines) <= max_lines:
            return f, track, lines
        if cur * 0.93 < px * T.MIN_SHRINK:
            if strict:
                raise ValueError(f"'{text[:40]}…' does not fit in {max_lines} line(s) at this size; shorten it")
            return f, track, lines[:max_lines]
        cur *= 0.93


def _gradient_text(size, xy, text, font, track, stops):
    mask = Image.new("L", size, 0)
    T.draw_tracked(ImageDraw.Draw(mask), xy[0], xy[1], text, font, track, 255)
    top, hgt = int(xy[1]), max(1, int(sum(font.getmetrics())))
    g = Image.new("RGBA", size, (0, 0, 0, 0)); g.paste(T.gradient(size[0], hgt, stops), (0, top)); g.putalpha(mask)
    return g


def _line(canvas, th, xy, text, style, font, track, role, align, W):
    """Draw one line (left edge at xy[0] for left-aligned, centred on xy[0] otherwise). role: 'title' | 'sub' | 'accent'.
    A card colour may be a #rrggbb, null (the style's own colour and effects) or a list of gradient stops."""
    cp = _card(th)
    w = T.text_width(text, font, track)
    x = xy[0] - (w / 2 if align == "center" else 0)
    col = cp["accent"] if role == "accent" else cp["title" if role == "title" else "sub"]
    if isinstance(col, tuple):                                                    # gradient stops
        layer = _gradient_text(canvas.size, (x, xy[1]), text, font, track, col)
    else:
        layer = shapes.text_layer(canvas.size, (x, xy[1]), text, style, font, track, c_(col) if col else None, cp["effects"])
    canvas.alpha_composite(layer)
    return w


def _heights(font, n):
    return (sum(font.getmetrics())) * 1.1 * n


def _decor_rule(canvas, th, x, y, w, align, H):
    """The template's signature divider between title and subtitle (x = centre for centred cards, left edge otherwise): its card_rule plugin."""
    ov = Image.new("RGBA", canvas.size, (0, 0, 0, 0))                          # translucent strokes on their own layer (ImageDraw replaces alpha)
    x0 = x - w / 2 if align == "center" else x
    registry.get("card_rule", _card(th)["rule"])(ImageDraw.Draw(ov), th, x, x0, y, w, H, c_(_card(th)["accent"]), max(2, int(0.004 * H)))
    canvas.alpha_composite(ov)


def _tile(canvas, rect, th, H, key):
    """One bento tile in the theme's shape -> (title colour, sub colour, effects). Painted on its own transparent layer and composited:
    ImageDraw on the opaque card would REPLACE the alpha of translucent fills."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    res = shapes.of(th).tile(layer, rect, th, H, key)
    canvas.alpha_composite(layer)
    return res


# ------------------------------------------------------------------------------------------------ the layouts
class Layout:
    """A card layout plugin: draw(c) paints the card through a Ctx. needs_title: whether a card of this layout needs a title."""

    def __init__(self, draw, needs_title=True):
        self.draw, self.needs_title = draw, needs_title


def layout(name, needs_title=True):
    """Decorator registering a card layout: @layout("title") def draw(c): ..."""
    def deco(fn):
        registry.register("card_layout", name, Layout(fn, needs_title))
        return fn
    return deco


def __getattr__(name):
    if name == "LAYOUTS":
        return registry.names("card_layout")
    raise AttributeError(name)


class Ctx:
    """What a layout draws with: the card's text, the theme and its card options, the anchor/margins, and the current canvas.
    In split mode boundary() closes the current element group and starts a fresh transparent layer (c.canvas changes)."""

    def __init__(self, W, H, th, split, title, subtitle, items, number, author, strict):
        self.W, self.H, self.th, self.split, self.strict = W, H, th, split, strict
        self.title, self.subtitle, self.items, self.number, self.author = title, subtitle, items, number, author
        self.layers = []
        self.canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0)) if split else background(W, H, th)
        self.cp = _card(th)
        self.align = self.cp["align"]
        self.ml = 0.10 * W                                                         # left margin for left-aligned cards
        self.cx = self.ml if self.align == "left" else W / 2                       # anchor x
        self.maxw = (0.80 * W) if self.align == "center" else (0.78 * W)
        self.ts, self.ss = th.title_style, th.caption_style

    def boundary(self):
        if self.split:
            self.layers.append(self.canvas)
            self.canvas = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))

    def block(self, text, style, px, max_w, max_lines):
        return _block(text, style, px, max_w, max_lines, self.strict)

    def line(self, xy, text, style, font, track, role, align=None):
        return _line(self.canvas, self.th, xy, text, style, font, track, role, align or self.align, self.W)

    def put(self, lines, font, track, y, role, style):
        for ln in lines:
            _line(self.canvas, self.th, (self.cx, y), ln, style, font, track, role, self.align, self.W)
            y += sum(font.getmetrics()) * 1.1
        return y

    def rule(self, x, y, w):
        _decor_rule(self.canvas, self.th, x, y, w, self.align, self.H)

    def tile(self, rect, key):
        return _tile(self.canvas, rect, self.th, self.H, key)


def render_card(layout, W, H, th, title="", subtitle="", items=(), number="", author="", strict=True, split=False):
    """Full-frame card as an RGBA PIL image, drawn by the card_layout plugin `layout`. Raises ValueError (with a message to show verbatim) if
    text does not fit. split=True returns (background, [foreground layers]) instead: one transparent layer per element group (title, rule,
    subtitle, each list row or bento tile) so the card can be animated; compositing them over the background gives the same card."""
    lay = registry.get("card_layout", layout, None)
    if lay is None:
        raise ValueError(f"layout must be one of {registry.names('card_layout')}")
    title, subtitle, author = (T.clean(title, th.title_style) if title else ""), (T.clean(subtitle, th.caption_style) if subtitle else ""), (T.clean(author, th.caption_style) if author else "")
    if not title and lay.needs_title:
        raise ValueError("a card needs a title")
    c = Ctx(W, H, th, split, title, subtitle, items, number, author, strict)
    lay.draw(c)
    if split:
        c.layers.append(c.canvas)
        return background(W, H, th), c.layers
    return c.canvas


# ------------------------------------------------------------------------------------------------ png + mp4
def card_png(cache_dir, layout, W, H, th, **kw):
    key = repr((layout, W, H, th.name, th.accent, sorted(kw.items())))
    out = os.path.join(cache_dir, f"card_{hashlib.sha1(('v4|' + key).encode()).hexdigest()[:16]}.png")
    if not os.path.exists(out):
        img = render_card(layout, W, H, th, **kw)
        os.makedirs(cache_dir, exist_ok=True)
        tmp = out + f".{os.getpid()}.tmp"
        img.convert("RGB").save(tmp, format="PNG")
        os.replace(tmp, out)
    return out


def card_video(png, out, W, H, fps, dur_s, push=False):
    """PNG -> mp4 (H.264 + silent AAC) of dur_s seconds. push=True adds a slow 6% zoom-in on a 2x supersampled copy."""
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    tmp = out + f".{os.getpid()}.tmp.mp4"
    n = max(1, int(round(dur_s * fps)))
    vf = (f"scale={W * 2}:{H * 2}:flags=lanczos,zoompan=z='min(zoom+{0.06 / n:.6f},1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={fps}"
          if push else f"scale={W}:{H}:flags=lanczos")
    cmd = ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", str(fps), "-i", png, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
           "-vf", vf + ",format=yuv420p", "-frames:v", str(n), "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-c:a", "aac", "-b:a", "96k",
           "-shortest", "-movflags", "+faststart", tmp]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode or not os.path.exists(tmp):
        raise RuntimeError("ffmpeg could not make the card video: " + r.stderr.strip()[-300:])
    os.replace(tmp, out)
    return out


# ------------------------------------------------------------------------------------------------ animated cards
def card_layers(cache_dir, layout, W, H, th, **kw):
    """(background PNG, [foreground layer PNGs]) of a card, cached; compositing them gives the card (diff <= 1 level, measured)."""
    key = hashlib.sha1(('v4|' + repr((layout, W, H, th.name, th.accent, sorted(kw.items())))).encode()).hexdigest()[:16]
    bgp = os.path.join(cache_dir, f"cardbg_{key}.png")
    probe = os.path.join(cache_dir, f"cardfg_{key}_0.png")
    if os.path.exists(bgp) and os.path.exists(probe):
        n = 0
        while os.path.exists(os.path.join(cache_dir, f"cardfg_{key}_{n}.png")):
            n += 1
        return bgp, [os.path.join(cache_dir, f"cardfg_{key}_{i}.png") for i in range(n)]
    bg, layers = render_card(layout, W, H, th, split=True, **kw)
    os.makedirs(cache_dir, exist_ok=True)
    paths = []
    for i, L in enumerate(layers):
        p = os.path.join(cache_dir, f"cardfg_{key}_{i}.png")
        tmp = p + f".{os.getpid()}.tmp"
        L.save(tmp, format="PNG"); os.replace(tmp, p); paths.append(p)
    tmp = bgp + f".{os.getpid()}.tmp"
    bg.convert("RGB").save(tmp, format="PNG"); os.replace(tmp, bgp)
    return bgp, paths


def animated_timing(th, n_layers, dur_s):
    """(enter, stagger, start) in seconds for a card of dur_s: the template's own times, squeezed so the last layer has settled 0.4 s before the end."""
    m = th.motion.get("card", {"enter_s": 0.5, "stagger_s": 0.12, "rise": 0.03, "dx": 0.0})
    start = 0.2
    end = start + (n_layers - 1) * m["stagger_s"] + m["enter_s"]
    k = min(1.0, max(0.25, (dur_s - 0.4) / end)) if end > 0 else 1.0
    return m["enter_s"] * k, m["stagger_s"] * k, start * k, m["rise"], m["dx"]


def card_video_animated(bg_png, layer_pngs, out, W, H, fps, dur_s, th, push=False):
    """Card mp4 whose element groups fade/slide in one after another over the background (ease-out cubic), then hold. One ffmpeg graph."""
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    enter, stagger, start, rise, dx = animated_timing(th, len(layer_pngs), dur_s)
    tmp = out + f".{os.getpid()}.tmp.mp4"
    n = max(1, int(round(dur_s * fps)))
    bgf = (f"scale={W * 2}:{H * 2}:flags=lanczos,zoompan=z='min(zoom+{0.06 / n:.6f},1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={fps}"
           if push else f"scale={W}:{H}:flags=lanczos")
    cmd = ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", str(fps), "-i", bg_png]
    for p in layer_pngs:
        cmd += ["-loop", "1", "-framerate", str(fps), "-i", p]
    cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    graph = [f"[0:v]{bgf}[b0]"]
    for i in range(len(layer_pngs)):
        t0 = start + i * stagger
        prog = f"(1-min(1,max(0,(t-{t0:.4f})/{enter:.4f})))"
        graph.append(f"[{i + 1}:v]format=rgba,fade=t=in:st={t0:.4f}:d={enter:.4f}:alpha=1[l{i}]")
        graph.append(f"[b{i}][l{i}]overlay=x='{dx * W:.3f}*pow({prog},3)':y='{rise * H:.3f}*pow({prog},3)':eval=frame:format=auto[b{i + 1}]")
    graph.append(f"[b{len(layer_pngs)}]format=yuv420p[v]")
    cmd += ["-filter_complex", ";".join(graph), "-map", "[v]", "-map", f"{len(layer_pngs) + 1}:a", "-frames:v", str(n), "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
            "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart", tmp]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if r.returncode or not os.path.exists(tmp):
        raise RuntimeError("ffmpeg could not make the animated card video: " + r.stderr.strip()[-300:])
    os.replace(tmp, out)
    return out
