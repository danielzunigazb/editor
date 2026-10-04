"""Compatibility names for code written before the shapes became plugins. Everything here delegates to mltedit/shapes.py and the theme's
shape plugin; there is no drawing logic in this module."""
from . import shapes

S = shapes.SS
_c, _blur, _text_layer = shapes._c, shapes._blur, shapes.text_layer


def _panel(img, rect, th, u, key):
    return shapes.of(th).panel(img, rect, th, u, key)


def lower_third(W, H, title, subtitle, align, strict, th):
    return shapes.of(th).lower_third(W, H, title, subtitle, align, strict, th)


def callout(W, H, title, subtitle, side, strict, th):
    return shapes.of(th).callout(W, H, title, subtitle, side, strict, th)


def frame(W, H, amount, th):
    return shapes.of(th).frame(W, H, amount, th)
