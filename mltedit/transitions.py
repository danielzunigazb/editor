"""Transition styles between two adjacent entries of the main track.

dissolve (the original) is MLT's `luma` with no map. The wipes, iris, blinds, diagonal and clock are `luma` with a grey map generated here with
PIL (deterministic, cached as PNG next to the other render caches, at the project's resolution): where the map is DARK the new clip appears
first. The slides are MLT's `composite` with an animated geometry: the new clip travels over the old one.

`apply(mlt7, profile, style, W, H, cache_dir)` returns the configured Transition for `Playlist.mix`. Nothing here decodes video."""
import hashlib, math, os

from PIL import Image

STYLES = ("dissolve", "wipe-right", "wipe-left", "wipe-down", "wipe-up", "iris-out", "iris-in", "blinds-v", "blinds-h", "diagonal", "clock",
          "slide-left", "slide-right", "slide-up", "slide-down")
MASKED = tuple(s for s in STYLES if s not in ("dissolve",) and not s.startswith("slide-"))
SOFTNESS = {"wipe-right": 0.12, "wipe-left": 0.12, "wipe-down": 0.12, "wipe-up": 0.12, "iris-out": 0.10, "iris-in": 0.10, "blinds-v": 0.06, "blinds-h": 0.06,
            "diagonal": 0.14, "clock": 0.04}
MIN_S = 0.2                                                   # shorter than this is a glitch, not a transition
MASK_W, MASK_H = 480, 270                                     # maps are built small and scaled up: they are smooth ramps
SLIDE_FROM = {"slide-left": (1, 0), "slide-right": (-1, 0), "slide-up": (0, 1), "slide-down": (0, -1)}   # where the NEW clip starts (x, y in frame widths/heights)


def validate(style, where):
    if style not in STYLES + ("auto",):
        raise ValueError(f"{where}: unknown transition style '{style}'; choose one of {STYLES + ('auto',)}")
    return style


def _map(style):
    """Grey ramp (PIL 'L', MASK_W x MASK_H): 0 = the new clip shows there first, 245 = last (255 would leave a fringe at the final frame)."""
    img = Image.new("L", (MASK_W, MASK_H))
    px = []
    cx, cy = (MASK_W - 1) / 2, (MASK_H - 1) / 2
    far = math.hypot(cx, cy)
    for y in range(MASK_H):
        for x in range(MASK_W):
            u, v = x / (MASK_W - 1), y / (MASK_H - 1)
            if style == "wipe-right":
                t = u
            elif style == "wipe-left":
                t = 1 - u
            elif style == "wipe-down":
                t = v
            elif style == "wipe-up":
                t = 1 - v
            elif style == "diagonal":
                t = (u + v) / 2
            elif style == "iris-out":
                t = math.hypot(x - cx, y - cy) / far
            elif style == "iris-in":
                t = 1 - math.hypot(x - cx, y - cy) / far
            elif style == "blinds-v":
                t = (u * 10) % 1.0
            elif style == "blinds-h":
                t = (v * 8) % 1.0
            elif style == "clock":
                t = (math.atan2(x - cx, -(y - cy)) % (2 * math.pi)) / (2 * math.pi)
            else:
                raise ValueError(style)
            px.append(int(round(min(max(t, 0.0), 1.0) * 245)))                 # never 255: the last pixels must finish before the transition ends
    img.putdata(px)
    return img


def mask_path(style, W, H, cache_dir):
    """PNG luma map of a masked style at the project's size (cached)."""
    key = hashlib.sha1(f"v3|{style}|{W}x{H}".encode()).hexdigest()[:12]
    out = os.path.join(cache_dir, f"luma_{style}_{key}.png")
    if not os.path.exists(out):
        os.makedirs(cache_dir, exist_ok=True)
        tmp = out + f".{os.getpid()}.tmp"
        _map(style).resize((W, H), Image.BICUBIC).point(lambda v: min(v, 245)).save(tmp, format="PNG")   # bicubic rings past 245 at hard edges
        os.replace(tmp, out)
    return out


def slide_geometry(style, n):
    """composite geometry keyframes: the new clip moves from off-screen to rest over the n frames of the transition."""
    sx, sy = SLIDE_FROM[style]
    return f"0={sx * 100}%/{sy * 100}%:100%x100%:100;{n - 1}=0%/0%:100%x100%:100"


def apply(mlt7, profile, style, W, H, cache_dir, n):
    """The MLT Transition for `style` over n frames."""
    if style == "dissolve":
        return mlt7.Transition(profile, "luma")
    if style.startswith("slide-"):
        t = mlt7.Transition(profile, "composite")
        t.set("geometry", slide_geometry(style, n))
        t.set("distort", 1)
        t.set("aligned", 1)
        return t
    t = mlt7.Transition(profile, "luma")
    t.set("resource", mask_path(style, W, H, cache_dir))
    t.set("softness", SOFTNESS[style])
    return t
