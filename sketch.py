"""Hand-drawn strokes for the 'sketch' template: wobbly lines, rectangles, circles that overshoot, arrows, marker underlines
and highlighter bands. Everything is DETERMINISTIC: the wobble comes from a random.Random seeded with a hash of the content,
so the same label always gets the same pencil strokes (render caches stay valid, exports are reproducible).

All functions draw on a PIL ImageDraw in the caller's pixel space (the caller supersamples and shrinks)."""
import hashlib, math, random

from PIL import ImageDraw


def rng(*parts):
    """Random.Random seeded from the text/geometry it will decorate."""
    return random.Random(int(hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16))


def _wavy(p0, p1, r, amp, step):
    """Points from p0 to p1 displaced sideways by two out-of-phase sines (a hand's slow drift) plus a little tremor."""
    (x0, y0), (x1, y1) = p0, p1
    L = math.hypot(x1 - x0, y1 - y0) or 1.0
    nx, ny = -(y1 - y0) / L, (x1 - x0) / L
    n = max(3, int(L / step))
    f1, f2, ph1, ph2 = r.uniform(0.6, 1.4), r.uniform(1.8, 3.2), r.uniform(0, 6.28), r.uniform(0, 6.28)
    pts = []
    for i in range(n + 1):
        t = i / n
        off = amp * (0.65 * math.sin(6.283 * f1 * t + ph1) + 0.35 * math.sin(6.283 * f2 * t + ph2)) + r.uniform(-0.12, 0.12) * amp
        pts.append((x0 + (x1 - x0) * t + nx * off, y0 + (y1 - y0) * t + ny * off))
    return pts


def _poly(draw, pts, color, width):
    draw.line(pts, fill=color, width=max(1, int(round(width))), joint="curve")
    rr = max(1, width) / 2                                       # round caps
    for x, y in (pts[0], pts[-1]):
        draw.ellipse([x - rr, y - rr, x + rr, y + rr], fill=color)


def line(draw, p0, p1, color, width, r, amp=None, overshoot=0.02, double=True):
    """A pencil line: slight overshoot at both ends, wobble, and a thinner second pass beside the first."""
    (x0, y0), (x1, y1) = p0, p1
    L = math.hypot(x1 - x0, y1 - y0) or 1.0
    amp = width * 0.9 if amp is None else amp
    for k in range(2 if double else 1):
        ox0, ox1 = r.uniform(0, overshoot) * L, r.uniform(0, overshoot) * L
        ux, uy = (x1 - x0) / L, (y1 - y0) / L
        a = (x0 - ux * ox0, y0 - uy * ox0)
        b = (x1 + ux * ox1, y1 + uy * ox1)
        col = color if k == 0 else color[:3] + (int(color[3] * 0.65),)
        _poly(draw, _wavy(a, b, r, amp * (1.0 if k == 0 else 1.4), max(4, width * 5)), col, width * (1.0 if k == 0 else 0.6))


def rect(draw, x0, y0, x1, y1, color, width, r, fill=None, double=True):
    """Hand-drawn rectangle: optional fill with a slightly irregular edge, four overshooting strokes."""
    if fill is not None:
        j = width * 0.9
        draw.polygon([(x0 + r.uniform(-j, j), y0 + r.uniform(-j, j)), (x1 + r.uniform(-j, j), y0 + r.uniform(-j, j)),
                      (x1 + r.uniform(-j, j), y1 + r.uniform(-j, j)), (x0 + r.uniform(-j, j), y1 + r.uniform(-j, j))], fill=fill)
    for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        line(draw, a, b, color, width, r, overshoot=0.03, double=double)


def circle(draw, cx, cy, rad, color, width, r, turns=1.12):
    """A circle the way a hand closes it: more than one turn, the radius creeping outward, a wobble on top."""
    a0 = r.uniform(-3.14, 3.14)
    for k in range(2):
        n = int(max(24, rad * 0.9))
        pts = []
        for i in range(n + 1):
            t = i / n
            th = a0 + 6.283 * turns * t + k * 0.35
            rr = rad * (1 + 0.07 * t * turns + 0.035 * math.sin(3 * th + k) + r.uniform(-0.012, 0.012))
            pts.append((cx + rr * math.cos(th), cy + rr * math.sin(th)))
        _poly(draw, pts, color if k == 0 else color[:3] + (int(color[3] * 0.6),), width * (1.0 if k == 0 else 0.6))


def arrow(draw, p0, p1, color, width, r, head=None):
    """Line plus a two-stroke arrow head at p1."""
    line(draw, p0, p1, color, width, r, double=False)
    (x0, y0), (x1, y1) = p0, p1
    L = math.hypot(x1 - x0, y1 - y0) or 1.0
    head = head or max(width * 6, L * 0.18)
    ang = math.atan2(y1 - y0, x1 - x0)
    for s in (-1, 1):
        a = ang + math.pi + s * r.uniform(0.42, 0.58)
        line(draw, (x1, y1), (x1 + head * math.cos(a), y1 + head * math.sin(a)), color, width, r, amp=width * 0.3, overshoot=0.0, double=False)


def highlighter(layer, x0, y0, x1, y1, color, r):
    """A marker band (semi-transparent) with ragged ends, drawn on its own RGBA layer so it never darkens what is under it."""
    d = ImageDraw.Draw(layer)
    h = y1 - y0
    j = h * 0.12
    d.polygon([(x0 + r.uniform(-j, j), y0 + r.uniform(-j, j) * 0.5), (x1 + r.uniform(-j, j), y0 + r.uniform(-j, j) * 0.5),
               (x1 + r.uniform(-j, j) * 1.5, y1 + r.uniform(-j, j) * 0.5), (x0 + r.uniform(-j, j) * 1.5, y1 + r.uniform(-j, j) * 0.5)], fill=color)


def scribble_underline(draw, x0, x1, y, color, width, r):
    """Two quick wavy strokes under a word."""
    line(draw, (x0, y), (x1, y + r.uniform(-width, width)), color, width, r, amp=width * 1.4, double=True)


def star(draw, cx, cy, rad, color, width, r):
    """A five-point star drawn in one hand-made loop."""
    pts = [(cx + rad * math.cos(-1.5708 + k * 4 * math.pi / 5), cy + rad * math.sin(-1.5708 + k * 4 * math.pi / 5)) for k in range(6)]
    for a, b in zip(pts, pts[1:]):
        line(draw, a, b, color, width, r, amp=width * 0.4, overshoot=0.04, double=False)
