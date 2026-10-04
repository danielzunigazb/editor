"""{"op":"callout","title":"Aquí","path":[[t,x,y],...],"start":1,"dur":3,"side":"auto"}: a ring pinned to a (moving) point plus a flag."""
from ... import graphics, themes
from ...ops import Op, op
from ...ops.common import anim, clean, theme_key


@op
class Callout(Op):
    timed = True
    order = 60
    name = "callout"
    defaults = {"side": "auto", "size": 1.0, "fade": 0.3, "subtitle": ""}
    animatable = True

    def layout(self, o, n, where, st):
        if o.get("side", "auto") not in graphics.CALLOUT_SIDES:
            raise ValueError(f"{where}: side must be one of {graphics.CALLOUT_SIDES}")
        if not (o.get("start", -1) >= 0 and 0 < o.get("dur", 0) <= 3600):
            raise ValueError(f"{where}: needs start>=0 and 0 < dur <= 3600")
        path, pts = o.get("path"), []
        if not isinstance(path, list) or not 1 <= len(path) <= 600:
            raise ValueError(f"{where}: path must be a list of 1-600 points [t_s, x, y] (x, y = fractions 0-1 of the frame)")
        for i_, pt in enumerate(path):
            if not (isinstance(pt, (list, tuple)) and len(pt) == 3 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in pt)):
                raise ValueError(f"{where}: path point {i_} must be [t_s, x, y] with numbers")
            if not (0 <= pt[1] <= 1 and 0 <= pt[2] <= 1):
                raise ValueError(f"{where}: path point {i_} x,y must be between 0 and 1 (fractions of the frame; 0,0 = top-left)")
            if pts and pt[0] <= pts[-1][0]:
                raise ValueError(f"{where}: path times must increase (point {i_})")
            pts.append((float(pt[0]), float(pt[1]), float(pt[2])))
        tk = theme_key(o, where, st.ctx)
        th_ = themes.get(tk)
        size_ = o.get("size", 1.0)
        if not isinstance(size_, (int, float)) or isinstance(size_, bool) or not 0.7 <= size_ <= 2.0:
            raise ValueError(f"{where}: size must be a number between 0.7 and 2.0 (1 = normal)")
        title, sub = clean(o.get("title"), where, th_.title_style), (clean(o["subtitle"], where, th_.caption_style) if o.get("subtitle") else "")
        if not title or "\n" in title or "\n" in sub or len(title) > graphics.CALLOUT_TITLE_MAX or len(sub) > graphics.CALLOUT_SUB_MAX:
            raise ValueError(f"{where}: callout needs a single-line title (1-{graphics.CALLOUT_TITLE_MAX} characters) and a subtitle of at most {graphics.CALLOUT_SUB_MAX}")
        st.layers.append({"kind": "callout", "op": n, "start": float(o["start"]), "dur": float(o["dur"]), "title": title, "sub": sub,
                          "side": o.get("side", "auto"), "path": pts, "fade": float(o.get("fade", 0.3)), "theme": tk,
                          "size": float(size_), "anim": anim(o, where, st.ctx, callout=True)})

    def describe(self, o):
        return f"Callout “{o['title'][:24]}” de {o['start']:g} a {o['start']+o['dur']:g} s", f"callout @ {o['start']:g}s"

    def legibility(self, o, i, st):
        H = st["height"]
        sz = o.get("size", 1.0) if isinstance(o.get("size"), (int, float)) else 1.0
        if o.get("subtitle") and 0.0135 * H * sz < 11:
            return [f"op {i} (callout) has a subtitle only {0.0135 * H * sz:.0f} px tall at {st['width']}x{H}: use size={min(2.0, 11 / (0.0135 * H)):.1f} or more"]
        return []

    def check_new(self, o, ctx):
        graphics.callout(ctx.W, ctx.H, o["title"], o.get("subtitle", ""), "ne", strict=True, theme=theme_key(o, "callout", ctx))
