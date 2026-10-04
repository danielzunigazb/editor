"""Entrance/exit presets. transform(p, x, y, w, h, W, H) -> (dx, dy, scale, rotation, opacity multiplier); p: 0 hidden -> 1 at rest.
slide-* name the SIDE OF THE SCREEN: as an entrance the item comes from that side, as an exit it leaves toward it."""
from ... import registry
from ...anim import Preset

_still = lambda p, x, y, w, h, W, H: (0.0, 0.0, 1.0, 0.0, 1.0)
_clip = lambda v: min(max(v, 0.0), 1.0)

for name, pre in {
    "none": Preset(_still, still=True, callout=True),
    "fade": Preset(lambda p, x, y, w, h, W, H: (0.0, 0.0, 1.0, 0.0, _clip(p)), timing="item", callout=True),
    "slide-left": Preset(lambda p, x, y, w, h, W, H: (-(1 - p) * (x + w), 0.0, 1.0, 0.0, min(1.0, p * 4))),
    "slide-right": Preset(lambda p, x, y, w, h, W, H: ((1 - p) * (W - x), 0.0, 1.0, 0.0, min(1.0, p * 4))),
    "slide-top": Preset(lambda p, x, y, w, h, W, H: (0.0, -(1 - p) * (y + h), 1.0, 0.0, min(1.0, p * 4))),
    "slide-bottom": Preset(lambda p, x, y, w, h, W, H: (0.0, (1 - p) * (H - y), 1.0, 0.0, min(1.0, p * 4))),
    "drop": Preset(lambda p, x, y, w, h, W, H: (0.0, -(1 - p) * (y + h), 1.0, 0.0, min(1.0, p * 4)), ease_in="bounce"),
    "pop": Preset(lambda p, x, y, w, h, W, H: (0.0, 0.0, 0.55 + 0.45 * p, 0.0, min(1.0, max(p, 0.0) * 3)), ease_in="back", callout=True),
    "zoom": Preset(lambda p, x, y, w, h, W, H: (0.0, 0.0, 1.6 - 0.6 * p, 0.0, _clip(p)), callout=True),
    "spin": Preset(lambda p, x, y, w, h, W, H: (0.0, 0.0, 0.4 + 0.6 * p, -200.0 * (1 - p), min(1.0, max(p, 0.0) * 2)), ease_in="back"),
    "rise": Preset(lambda p, x, y, w, h, W, H: (0.0, (1 - p) * 0.06 * H, 1.0, 0.0, _clip(p))),   # drifts up 6% of the frame height while it fades in
    "wipe": Preset(_still, reveal="wipe", ease_in="linear", ease_out="linear", on_video=False),     # revealed left to right (a crop)
    "draw": Preset(_still, reveal="draw", callout=True, callout_only=True),                          # callouts: unfolds from the ring (a crop)
}.items():
    registry.register("anim_preset", name, pre)
