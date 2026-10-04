"""Full-frame graphic overlays (frame, letterbox, vignette) and the entry points for lower thirds and callouts. What they look like is
decided by the theme's shape plugin (plugins/shapes); this module only dispatches, validates and caches. Each function returns a transparent
W x H RGBA image the same size as the video frame (callouts: a small image + the ring position)."""
import hashlib, os

from PIL import Image, ImageDraw

from . import registry, shapes, themes


class GraphicKind:
    """A graphic kind plugin (plugins/graphic_kinds). amount = (what it means, min, max, default) or None; user: offered by add_graphic."""
    name = ""
    amount = None
    user = True

    def key(self, params, amount):
        """Cache key part identifying what is drawn (the theme, size and version are added by render)."""
        return f"{self.name}|{amount}"

    def draw(self, W, H, params, amount, th):
        """The W x H RGBA image."""
        raise NotImplementedError


def kind(cls):
    """Class decorator registering a graphic kind under cls.name."""
    registry.register("graphic_kind", cls.name, cls())
    return cls


def __getattr__(name):
    if name == "KINDS":                                          # what add_graphic accepts
        return tuple(n for n, k in registry.items("graphic_kind") if k.user)
    if name == "AMOUNT":                                         # kind -> (name, min, max, default)
        return {n: k.amount for n, k in registry.items("graphic_kind") if k.user and k.amount}
    raise AttributeError(name)


_fit = shapes.fit
CALLOUT_SIDES, CALLOUT_SUB_MAX, CALLOUT_TITLE_MAX = shapes.CALLOUT_SIDES, shapes.CALLOUT_SUB_MAX, shapes.CALLOUT_TITLE_MAX   # re-exported for callers


def frame(W, H, amount, theme=None):
    """The theme's frame decoration (default: the default theme's)."""
    th = _theme(theme)
    return shapes.of(th).frame(W, H, amount, th)


def letterbox(W, H, amount, color=None):
    """Cinema bars with a hairline in the theme's colour (default: the default theme's letterbox colour) on the inner edge."""
    if color is None:
        th = _theme(None)
        color = shapes.of(th).letterbox_color(th)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bh = int(round(amount * H))
    d.rectangle([0, 0, W, bh], fill=(0, 0, 0, 255))
    d.rectangle([0, H - bh, W, H], fill=(0, 0, 0, 255))
    t = max(1, int(round(H * 0.0012)))
    d.rectangle([0, bh, W, bh + t], fill=tuple(color) + (170,))
    d.rectangle([0, H - bh - t, W, H - bh], fill=tuple(color) + (170,))
    return img


def vignette(W, H, amount):
    """Soft dark falloff toward the edges (the vignette graphic kind; kept as a function for callers that draw it directly)."""
    return registry.get("graphic_kind", "vignette").draw(W, H, {}, amount, _theme(None))


def _theme(theme):
    """A themes.Theme from a project theme spec ({"name", "accent"} | name | Theme | None)."""
    return theme if isinstance(theme, themes.Theme) else themes.get(theme)


def lower_third(W, H, title, subtitle="", align="left", strict=True, theme=None):
    """Name/role panel in the project's template, drawn by its shape plugin."""
    th = _theme(theme)
    return shapes.of(th).lower_third(W, H, title, subtitle, align, strict, th)


def callout(W, H, title, subtitle="", side="ne", strict=True, theme=None, size=1.0):
    """(image, ax, ay): a label pinned to a point; the image is small and (ax, ay) is the ring centre inside it. size scales the whole callout
    (type, ring, staff, flag): it is drawn as if the frame were `size` times bigger, so 1.0 is exactly the original."""
    if size != 1.0:
        W, H = int(round(W * size)), int(round(H * size))
    th = _theme(theme)
    return shapes.of(th).callout(W, H, title, subtitle, side, strict, th)


def render_merged(parts, W, H, cache_dir):
    """Alpha-composite several graphics (list of (kind, params)) into ONE cached PNG, so a single qtblend draws them."""
    key = "merged|" + "|".join(f"{k}:{sorted(p.items())}" for k, p in parts)
    out = os.path.join(cache_dir, f"gfx_{hashlib.sha1(f'v1|{key}|{W}|{H}'.encode()).hexdigest()[:16]}.png")
    if os.path.exists(out):
        return out
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for kind, params in parts:
        with Image.open(render(kind, W, H, cache_dir, **params)) as layer:
            img = Image.alpha_composite(img, layer.convert("RGBA"))
    os.makedirs(cache_dir, exist_ok=True)
    tmp = out + f".{os.getpid()}.tmp"
    img.save(tmp, format="PNG")
    os.replace(tmp, out)
    return out


def validate(kind, params):
    """Raise ValueError for bad graphic params (pure checks, no rendering)."""
    plug = registry.get("graphic_kind", kind, None)
    if plug is None or not plug.user:
        raise ValueError(f"unknown graphic '{kind}'; choose one of {__getattr__('KINDS')}")
    name, lo, hi, _ = plug.amount
    a = params.get("amount")
    if a is not None and not (isinstance(a, (int, float)) and lo <= a <= hi):
        raise ValueError(f"{kind}: amount ({name}) must be between {lo} and {hi}")


def render(kind, W, H, cache_dir, **params):
    """Render a graphic (or lower third) to a cached PNG and return its path."""
    th = _theme(params.get("theme"))
    plug = registry.get("graphic_kind", kind, None)
    if plug is None:
        raise ValueError(f"unknown graphic '{kind}'; choose one of {__getattr__('KINDS')}")
    amount = None
    if plug.user:
        validate(kind, params)
        amount = params.get("amount") if params.get("amount") is not None else plug.amount[3]
    key = plug.key(params, amount) + f"|{th.name}|{th.accent}"
    out = os.path.join(cache_dir, f"gfx_{hashlib.sha1(f'v2|{key}|{W}|{H}'.encode()).hexdigest()[:16]}.png")
    if os.path.exists(out):
        return out
    img = plug.draw(W, H, params, amount, th)
    os.makedirs(cache_dir, exist_ok=True)
    tmp = out + f".{os.getpid()}.tmp"
    img.save(tmp, format="PNG")
    os.replace(tmp, out)
    return out


def render_callout(W, H, title, subtitle, side, cache_dir, theme=None, size=1.0):
    """Cached PNG of a callout + its anchor. Returns (path, w, h, ax, ay)."""
    import json
    th = _theme(theme)
    key = f"v2|{title}|{subtitle}|{side}|{W}|{H}|{th.name}|{th.accent}" + (f"|size{size:g}" if size != 1.0 else "")
    base = os.path.join(cache_dir, f"callout_{hashlib.sha1(key.encode()).hexdigest()[:16]}")
    try:
        with open(base + ".json") as f:
            meta = json.load(f)
        if os.path.exists(base + ".png"):
            return (base + ".png", *meta)
    except (OSError, ValueError):
        pass
    img, ax, ay = callout(W, H, title, subtitle, side, strict=False, theme=th, size=size)
    os.makedirs(cache_dir, exist_ok=True)
    tmp = base + f".{os.getpid()}.tmp"
    img.save(tmp, format="PNG")
    os.replace(tmp, base + ".png")
    meta = [img.width, img.height, ax, ay]
    tmp = base + f".{os.getpid()}.json.tmp"
    with open(tmp, "w") as f:
        json.dump(meta, f)
    os.replace(tmp, base + ".json")
    return (base + ".png", *meta)
