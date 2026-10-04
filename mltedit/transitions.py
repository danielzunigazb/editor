"""Transitions between two adjacent entries of the main track. Each style is a plugin (plugins/transitions) registered under kind "transition":
  Dissolve()                         MLT's `luma` with no map
  Mask(fn, softness)                 `luma` with a grey map generated with PIL from fn(u, v, x, y, cx, cy, far) -> 0..1 (where the map is DARK the
                                     new clip appears first); maps are deterministic and cached as PNG at the project's resolution
  Slide(dx, dy)                      MLT's `composite` with an animated geometry: the new clip travels over the old one from (dx, dy) frames away
`apply(mlt7, profile, style, W, H, cache_dir, n)` returns the configured Transition for `Playlist.mix`. Nothing here decodes video."""
import hashlib, math, os

from PIL import Image

from . import registry

MIN_S = 0.2                                                   # shorter than this is a glitch, not a transition (styles other than a plain dissolve)
MASK_W, MASK_H = 480, 270                                     # maps are built small and scaled up: they are smooth ramps


class Dissolve:
    plain = True

    def make(self, mlt7, profile, style, W, H, cache_dir, n):
        return mlt7.Transition(profile, "luma")


class Mask:
    plain = False

    def __init__(self, fn, softness):
        self.fn, self.softness = fn, softness

    def grey(self):
        """Grey ramp (PIL 'L', MASK_W x MASK_H): 0 = the new clip shows there first, 245 = last (255 would leave a fringe at the final frame)."""
        img = Image.new("L", (MASK_W, MASK_H))
        px = []
        cx, cy = (MASK_W - 1) / 2, (MASK_H - 1) / 2
        far = math.hypot(cx, cy)
        for y in range(MASK_H):
            for x in range(MASK_W):
                t = self.fn(x / (MASK_W - 1), y / (MASK_H - 1), x, y, cx, cy, far)
                px.append(int(round(min(max(t, 0.0), 1.0) * 245)))       # never 255: the last pixels must finish before the transition ends
        img.putdata(px)
        return img

    def make(self, mlt7, profile, style, W, H, cache_dir, n):
        t = mlt7.Transition(profile, "luma")
        t.set("resource", mask_path(style, W, H, cache_dir))
        t.set("softness", self.softness)
        return t


class Slide:
    plain = False

    def __init__(self, dx, dy):
        self.dx, self.dy = dx, dy

    def geometry(self, n):
        return f"0={self.dx * 100}%/{self.dy * 100}%:100%x100%:100;{n - 1}=0%/0%:100%x100%:100"

    def make(self, mlt7, profile, style, W, H, cache_dir, n):
        t = mlt7.Transition(profile, "composite")
        t.set("geometry", self.geometry(n))
        t.set("distort", 1)
        t.set("aligned", 1)
        return t


def __getattr__(name):                        # STYLES / MASKED follow the registered plugins
    if name == "STYLES":
        return registry.names("transition")
    if name == "MASKED":
        return tuple(n for n, t in registry.items("transition") if isinstance(t, Mask))
    raise AttributeError(name)


def validate(style, where):
    styles = registry.names("transition")
    if style not in styles + ("auto",):
        raise ValueError(f"{where}: unknown transition style '{style}'; choose one of {styles + ('auto',)}")
    return style


def is_plain(style):
    return getattr(registry.get("transition", style), "plain", False)


def mask_path(style, W, H, cache_dir):
    """PNG luma map of a masked style at the project's size (cached)."""
    key = hashlib.sha1(f"v3|{style}|{W}x{H}".encode()).hexdigest()[:12]
    out = os.path.join(cache_dir, f"luma_{style}_{key}.png")
    if not os.path.exists(out):
        os.makedirs(cache_dir, exist_ok=True)
        tmp = out + f".{os.getpid()}.tmp"
        registry.get("transition", style).grey().resize((W, H), Image.BICUBIC).point(lambda v: min(v, 245)).save(tmp, format="PNG")   # bicubic rings past 245 at hard edges
        os.replace(tmp, out)
    return out


def slide_geometry(style, n):
    """composite geometry keyframes: the new clip moves from off-screen to rest over the n frames of the transition."""
    return registry.get("transition", style).geometry(n)


def apply(mlt7, profile, style, W, H, cache_dir, n):
    """The MLT Transition for `style` over n frames."""
    return registry.get("transition", style).make(mlt7, profile, style, W, H, cache_dir, n)
