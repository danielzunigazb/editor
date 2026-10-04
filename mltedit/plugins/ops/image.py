"""{"op":"image","path":"logo.png"|"icon":"star","start":1,"dur":3,"pos":"center","scale":0.3}: a picture, SVG or bundled icon."""
import os

from ... import icons, themes
from ...ops import Op, op
from ...ops.common import POS_IMG, anim, image_aspect, theme_key
from ...render import text as textrender


@op
class Image(Op):
    timed = True
    order = 20
    name = "image"
    animatable = True

    def layout(self, o, n, where, st):
        if o.get("pos", "center") not in POS_IMG:
            raise ValueError(f"{where}: pos must be one of {POS_IMG}")
        if not 0 < o.get("scale", 0.3) <= 1 or not 0 <= o.get("opacity", 1.0) <= 1:
            raise ValueError(f"{where}: scale must be in (0,1] and opacity in [0,1]")
        if not (o.get("start", -1) >= 0 and 0 < o.get("dur", 0) <= 3600):
            raise ValueError(f"{where}: needs start>=0 and 0 < dur <= 3600")
        xy = o.get("at")
        if xy is not None:
            if not (isinstance(xy, (list, tuple)) and len(xy) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 for v in xy)):
                raise ValueError(f"{where}: at must be [x, y], two fractions of the frame between 0 and 1 (0,0 = top-left)")
            xy = (float(xy[0]), float(xy[1]))
        icon, path = o.get("icon") or "", o.get("path") or ""
        if bool(icon) == bool(path):
            raise ValueError(f"{where}: give exactly one of icon (a name from list_assets) or path (an image or .svg file)")
        color = o.get("color") or None
        if color is not None and not textrender.COLOR_RE.match(str(color)):
            raise ValueError(f"{where}: color must look like #RRGGBB")
        base = {"kind": "image", "op": n, "start": float(o["start"]), "dur": float(o["dur"]), "pos": o.get("pos", "center"), "xy": xy,
                "scale": float(o.get("scale", 0.3)), "opacity": float(o.get("opacity", 1.0)), "anim": anim(o, where, st.ctx)}
        if icon or path.lower().endswith(".svg"):
            try:
                if icon:
                    svg = icons.icon_path(icon)
                else:
                    svg = os.path.abspath(os.path.expanduser(path))
                    if not os.path.isfile(svg):
                        raise ValueError(f"image not found: {svg}")
                    icons.check_svg(svg)
                aspect = 1.0 if (icon or svg is None) else icons.svg_aspect(svg)
            except ValueError as e:
                raise ValueError(f"{where}: {e}")
            tk = theme_key(o, where, st.ctx)
            plate = o.get("plate", True if icon else False)
            if not isinstance(plate, bool):
                raise ValueError(f"{where}: plate must be true or false")
            default_color = icons.plate_style(themes.get(tk))[3] if plate else tk["accent"]
            st.layers.append({**base, "icon": icon or None, "svg": svg, "path": path or "", "aspect": aspect, "color": color or default_color, "plate": plate, "theme": tk})
        else:
            st.layers.append({**base, "path": path, "aspect": image_aspect(path, where)})

    def files(self, o):
        return [o["path"]] if o.get("path") else []

    def describe(self, o):
        name = o.get("icon") or os.path.basename(o.get("path") or "")
        return f"Imagen {name} de {o['start']:g} a {o['start']+o['dur']:g} s", f"image @ {o['start']:g}s {o['dur']:g}s"
