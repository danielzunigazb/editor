"""Callouts: a ring pinned to a (moving) point of the frame with a flag beside it. The image is placed per keyframe so the ring follows
the path; entrance/exit presets scale about the ring, and the 'draw' reveal unfolds it from the ring (a qtcrop)."""
from ... import anim as animmod
from ... import graphics
from ...ops import Layer, layer


def target(L, t, W, H):
    """Pinned point (pixels) at timeline time t: linear between the path points, held before the first and after the last."""
    p = L["path"]
    if t <= p[0][0] or len(p) == 1:
        return p[0][1] * W, p[0][2] * H
    for (t0, x0, y0), (t1, x1, y1) in zip(p, p[1:]):
        if t <= t1:
            u = (t - t0) / (t1 - t0)
            return (x0 + (x1 - x0) * u) * W, (y0 + (y1 - y0) * u) * H
    return p[-1][1] * W, p[-1][2] * H


def pos(L, t, ax, ay, w, h, W, H):
    """Top-left of the callout image so that its ring sits exactly on the pinned point; kept inside the frame."""
    px, py = target(L, t, W, H)
    return min(max(px - ax, 0), W - w), min(max(py - ay, 0), H - h)


def source(L, W, H, cache):
    """Render (cached) the callout image. side='auto' picks the first of ne/nw/se/sw whose flag stays fully inside the
    frame along the whole path (so the ring never has to move off the point to keep the flag on screen); if none does,
    the one that overflows least. Returns (png, w, h, ax, ay)."""
    sides = ("ne", "nw", "se", "sw") if L["side"] == "auto" else (L["side"],)
    best = None
    for s in sides:
        png, w, h, ax, ay = graphics.render_callout(W, H, L["title"], L["sub"], s, cache, L.get("theme"), L.get("size", 1.0))
        over = 0.0
        for t_, x_, y_ in L["path"]:
            px, py = x_ * W, y_ * H
            x0, y0 = px - ax, py - ay
            over = max(over, -x0, x0 + w - W, -y0, y0 + h - H, 0)
        if best is None or over < best[0]:
            best = (over, (png, w, h, ax, ay))
        if over == 0:
            break
    return best[1]


@layer
class Callout(Layer):
    name = "callout"

    def source(self, L, ctx):
        png, w, h, ax, ay = source(L, ctx.W, ctx.H, ctx.CACHE)
        return png, (w, h, ax, ay)

    def crop_rect(self, L, ctx, n, extra):
        """'draw' reveal: the qtcrop rect grows from the ring to the whole image (ring at ax, ay)."""
        if not L.get("anim"):
            return ""
        cw, ch, cax, cay = extra
        dk = animmod.draw_keys(L["anim"], n, ctx.FPS, min(L["fade"], n / ctx.FPS / 2))
        return ";".join(f"{f_}={(cax - cax * p_) / cw * 100:.3f}%/{(cay - cay * p_) / ch * 100:.3f}%:{(cax * p_ + (cw - cax) * p_) / cw * 100:.3f}%x{(cay * p_ + (ch - cay) * p_) / ch * 100:.3f}%" for f_, p_ in dk)

    def keys(self, L, ctx, s0, n, extra):
        W, H, FPS = ctx.W, ctx.H, ctx.FPS
        w, h, cax, cay = extra
        ramp = min(ctx.fr(L["fade"]), (n - 1) // 2)
        lo, hi = s0 + ramp, s0 + n - 1 - ramp
        if L.get("anim"):                              # pop / zoom / fade scale about the RING (it must stay on the pinned point); draw is the crop
            ck = {f_: (s_, o_) for f_, s_, o_ in animmod.callout_keys(L["anim"], n, FPS, min(L["fade"], n / FPS / 2))}
            knots = {f for f in (int(round(t_ * FPS)) for t_, _, _ in L["path"]) if s0 < f < s0 + n - 1}
            keys = sorted({s0 + f_ for f_ in ck} | knots)
            last = (1.0, 1.0)
            pts = []
            for f_ in keys:
                s_, o_ = ck.get(f_ - s0, last)
                last = (s_, o_) if f_ - s0 in ck else last
                x_, y_ = pos(L, f_ / FPS, cax, cay, w, h, W, H)
                rx_, ry_ = x_ + cax, y_ + cay
                pts.append((f_, "{:.2f} {:.2f} {:.2f} {:.2f} {:.3f}".format(rx_ - cax * s_, ry_ - cay * s_, w * s_, h * s_, o_)))
            return pts
        keys = sorted({s0, lo, hi, s0 + n - 1} | {f for f in (int(round(t_ * FPS)) for t_, _, _ in L["path"]) if lo < f < hi})
        alpha = lambda f_: 0 if ramp and f_ in (s0, s0 + n - 1) else 1
        return [(f_, "{:.2f} {:.2f} {} {} {}".format(*pos(L, f_ / FPS, cax, cay, w, h, W, H), w, h, alpha(f_))) for f_ in keys]

    def shift(self, L, delta):
        L["start"] += delta
        L["path"] = [(t + delta, x, y) for t, x, y in L["path"]]

    def label(self, L):
        return f"callout {L['title'][:24]!r}"

    def svg(self, L):
        return L.get("title", "")[:22], "ctext"

    def summary(self, L):
        return {"callout": L["title"]}
