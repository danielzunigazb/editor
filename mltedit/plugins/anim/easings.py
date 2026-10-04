"""Easings: progress 0..1 -> 0..1."""
from ... import registry
from ...anim import Easing


def _back(p):
    c1 = 1.70158; c3 = c1 + 1
    return 1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2


def _bounce(p):
    n1, d1 = 7.5625, 2.75
    if p < 1 / d1:
        return n1 * p * p
    if p < 2 / d1:
        p -= 1.5 / d1; return n1 * p * p + 0.75
    if p < 2.5 / d1:
        p -= 2.25 / d1; return n1 * p * p + 0.9375
    p -= 2.625 / d1; return n1 * p * p + 0.984375


for _name, _fn, _over in (("linear", lambda p: p, False), ("in", lambda p: p * p, False), ("out", lambda p: 1 - (1 - p) ** 2, False),
                          ("inout", lambda p: 3 * p * p - 2 * p ** 3, False), ("back", _back, True), ("bounce", _bounce, True)):
    registry.register("easing", _name, Easing(_fn, _over))
