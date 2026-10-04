"""The built-in transitions: dissolve, wipes, iris, blinds, diagonal, clock, slides."""
import math

from ... import registry
from ...transitions import Dissolve, Mask, Slide

registry.register("transition", "dissolve", Dissolve())
for name, fn, soft in (
        ("wipe-right", lambda u, v, x, y, cx, cy, far: u, 0.12), ("wipe-left", lambda u, v, x, y, cx, cy, far: 1 - u, 0.12),
        ("wipe-down", lambda u, v, x, y, cx, cy, far: v, 0.12), ("wipe-up", lambda u, v, x, y, cx, cy, far: 1 - v, 0.12),
        ("iris-out", lambda u, v, x, y, cx, cy, far: math.hypot(x - cx, y - cy) / far, 0.10),
        ("iris-in", lambda u, v, x, y, cx, cy, far: 1 - math.hypot(x - cx, y - cy) / far, 0.10),
        ("blinds-v", lambda u, v, x, y, cx, cy, far: (u * 10) % 1.0, 0.06), ("blinds-h", lambda u, v, x, y, cx, cy, far: (v * 8) % 1.0, 0.06),
        ("diagonal", lambda u, v, x, y, cx, cy, far: (u + v) / 2, 0.14),
        ("clock", lambda u, v, x, y, cx, cy, far: (math.atan2(x - cx, -(y - cy)) % (2 * math.pi)) / (2 * math.pi), 0.04)):
    registry.register("transition", name, Mask(fn, soft))
for name, dx, dy in (("slide-left", 1, 0), ("slide-right", -1, 0), ("slide-up", 0, 1), ("slide-down", 0, -1)):   # where the NEW clip starts
    registry.register("transition", name, Slide(dx, dy))
