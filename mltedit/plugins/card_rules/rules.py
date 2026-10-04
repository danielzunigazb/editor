"""Card rules: the divider between a card's title and subtitle. fn(d, th, x, x0, y, w, H, acc, t): d draws on a transparent layer,
x0/w = the rule's left edge and width, y = its centre line, acc = the card's accent (RGBA), t = a base thickness."""
from ...cardkit import c_, card_rule
from ...render import sketch


@card_rule("bar")
def bar(d, th, x, x0, y, w, H, acc, t):                                          # a short bar
    d.rectangle([x0, y - t, x0 + min(w, 0.14 * H * 2), y + t], fill=acc)


@card_rule("diamond")
def diamond(d, th, x, x0, y, w, H, acc, t):                                      # a hairline with a diamond in the middle
    d.line([(x0, y), (x0 + w, y)], fill=acc, width=max(2, int(0.0025 * H)))
    r = 0.011 * H
    cx = x0 + w / 2
    d.polygon([(cx, y - r), (cx + r, y), (cx, y + r), (cx - r, y)], fill=acc)


@card_rule("double")
def double(d, th, x, x0, y, w, H, acc, t):                                       # two thin rules
    d.line([(x0, y - t), (x0 + w, y - t)], fill=acc, width=max(1, t // 2))
    d.line([(x0, y + t), (x0 + w, y + t)], fill=acc, width=max(1, t // 2))


@card_rule("scribble")
def scribble(d, th, x, x0, y, w, H, acc, t):                                     # a hand-drawn underline
    sketch.scribble_underline(d, x0, x0 + w, y, acc, max(3, 0.006 * H), sketch.rng("rule", x, y))


@card_rule("dots")
def dots(d, th, x, x0, y, w, H, acc, t):                                         # one outlined dot per palette colour
    cols = (th.accent, *th.extra, th.accent2)
    for i, col in enumerate(cols):
        cx = x0 + w * (i + 0.5) / len(cols)
        d.ellipse([cx - 0.012 * H, y - 0.012 * H, cx + 0.012 * H, y + 0.012 * H], fill=c_(col), outline=c_(th.ink), width=max(2, int(0.003 * H)))


@card_rule("dot-end")
def dot_end(d, th, x, x0, y, w, H, acc, t):                                      # a line ending in an accent2 dot
    d.line([(x0, y), (x0 + w, y)], fill=acc, width=t)
    d.ellipse([x0 + w - 0.01 * H, y - 0.01 * H, x0 + w + 0.01 * H, y + 0.01 * H], fill=c_(th.accent2))


@card_rule("block")
def block(d, th, x, x0, y, w, H, acc, t):                                        # an accent block with an ink border and a hard shadow
    L = min(w, 0.3 * H * 2)
    tt = int(t * 1.8)
    d.rectangle([x0 + 0.008 * H, y - tt + 0.008 * H, x0 + L + 0.008 * H, y + tt + 0.008 * H], fill=c_(th.ink))
    d.rectangle([x0, y - tt, x0 + L, y + tt], fill=c_(th.accent), outline=c_(th.ink), width=max(2, t // 2))


@card_rule("dot-mid")
def dot_mid(d, th, x, x0, y, w, H, acc, t):                                      # a hairline with a dot in the middle
    d.line([(x0, y), (x0 + w, y)], fill=acc, width=max(2, t // 2))
    d.ellipse([x0 + w / 2 - 0.008 * H, y - 0.008 * H, x0 + w / 2 + 0.008 * H, y + 0.008 * H], fill=acc)


@card_rule("bar-mid")
def bar_mid(d, th, x, x0, y, w, H, acc, t):                                      # a hairline with a short thick bar in the middle
    d.line([(x0, y), (x0 + w, y)], fill=acc, width=max(2, t // 2))
    d.rectangle([x0 + w / 2 - 0.05 * H, y - t, x0 + w / 2 + 0.05 * H, y + t], fill=acc)


@card_rule("cursor")
def cursor(d, th, x, x0, y, w, H, acc, t):                                       # a line and a block cursor after it
    d.line([(x0, y), (x0 + w, y)], fill=acc, width=max(2, t // 2))
    d.rectangle([x0 + w + 0.008 * H, y - 0.014 * H, x0 + w + 0.026 * H, y + 0.014 * H], fill=c_(th.accent2))


@card_rule("blocks")
def blocks(d, th, x, x0, y, w, H, acc, t):                                       # a dotted line of square blocks
    b = max(4, int(0.011 * H))
    for k in range(int(w // (b * 2))):
        d.rectangle([x0 + k * b * 2, y - b // 2, x0 + k * b * 2 + b, y + b // 2], fill=acc)


@card_rule("misregister")
def misregister(d, th, x, x0, y, w, H, acc, t):                                  # two inks slightly out of register
    d.rectangle([x0 + 0.005 * H, y - t + 0.005 * H, x0 + w + 0.005 * H, y + t + 0.005 * H], fill=c_(th.accent, 235))
    d.rectangle([x0, y - t // 2, x0 + w, y + t // 2], fill=c_(th.accent2))


@card_rule("pill")
def pill(d, th, x, x0, y, w, H, acc, t):                                         # a short white pill
    d.rounded_rectangle([x0 + w / 2 - 0.1 * H, y - t, x0 + w / 2 + 0.1 * H, y + t], radius=t, fill=(255, 255, 255, 200))
