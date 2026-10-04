"""Op and layer plugins: the engine's timeline is made of removable pieces.

* An **op** (kind "op", plugins/ops) is one edit the LLM sends: {"op": "<name>", ...}. Its plugin validates it and adds what it makes to the
  layout state (base-track entries, crossfades, a fade, overlay layers, audio clips), describes it for the viewer, and may check it at the
  export size (text must fit).
* A **layer** (kind "layer", plugins/layers) is one kind of overlay on the overlay tracks (text, graphic, image, callout, pip). Its plugin
  renders the source, places it on the frame, labels it in warnings and the timeline SVG, and may merge with or crop its neighbours.
Several ops make the same layer kind (text and subtitles -> text; graphic and lower_third -> graphic)."""
from .. import anim as animmod
from .. import registry


class Op:
    """Base for op plugins. Subclasses set `name` and implement layout()."""
    name = ""
    animatable = False                           # accepts an `anim` spec

    def layout(self, o, n, where, st):
        """Validate op `o` (index n; `where` prefixes error messages) and add what it makes to st (a LayoutState). Raise ValueError."""
        raise NotImplementedError

    def describe(self, o):
        """(Spanish title, short code) for the viewer's step list."""
        return o.get("op", "?"), o.get("op", "?")

    def check_new(self, o, ctx):
        """Extra validation of a freshly added op at the EXPORT size (ctx.W/H). Raise ValueError."""


class Layer:
    """Base for overlay layer plugins. A layer dict L always has kind, op, start, dur, track; the rest is the plugin's own."""
    name = ""
    croppable = False                            # full-frame PNG: the engine may crop it to its visible pixels (opt_crop)
    audible = False                              # carries audio (a video clip): its track gets an audio mix
    default_fade = 0.24                          # fade used for the animation windows when the layer has none

    def source(self, L, ctx):
        """(media path or PNG, extra) for the MLT producer; extra is passed back to place()/keys()/crop_rect()."""
        raise NotImplementedError

    def first_frame(self, L, ctx):
        """Frame of the source the layer starts from (video in-point)."""
        return 0

    def place(self, L, ctx, n, crop, extra):
        """(x, y, w, h, opacity, ramp_frames) of the layer on the frame."""
        raise NotImplementedError

    def keys(self, L, ctx, s0, n, extra):
        """Own rect keyframes [(frame, "x y w h opacity")] or None to use the engine's (static ramps, or anim.sample when animated)."""
        return None

    def crop_rect(self, L, ctx, n, extra):
        """qtcrop rect keyframe string (a reveal animation) or "" for none. Default: the 'wipe' reveal."""
        if not L.get("anim"):
            return ""
        wk = animmod.wipe_keys(L["anim"], n, ctx.FPS, min(L.get("fade", self.default_fade), n / ctx.FPS / 2))
        return ";".join(f"{f_}={lo * 100:.3f}%/0%:{(hi - lo) * 100:.3f}%x100%" for f_, lo, hi in wk)

    def merge_key(self, L):
        """Layers with the same non-None key, shown at exactly the same time, are drawn by one compositor (pre-composited PNG)."""
        return None

    def merge(self, group):
        """One layer standing for `group` (all with the same merge_key)."""
        return group[0]

    def label(self, L):
        """Short name in warnings."""
        return L["kind"]

    def zone(self, L, ctx):
        """Rough screen rectangle (x0, y0, x1, y1 fractions) for overlap warnings, or None (full-frame decoration)."""
        return None

    def svg(self, L):
        """(label, css class) in the timeline SVG."""
        return self.label(L), "ctext"


def op(cls):
    """Class decorator registering an op plugin under cls.name."""
    registry.register("op", cls.name, cls())
    return cls


def layer(cls):
    """Class decorator registering a layer plugin under cls.name."""
    registry.register("layer", cls.name, cls())
    return cls


def get_op(name):
    return registry.get("op", name, None)


def get_layer(name):
    return registry.get("layer", name)


def op_names():
    return registry.names("op")


def animatable():
    return tuple(n for n, o in registry.items("op") if o.animatable)


class LayoutState:
    """What the ops of a project build up while they are laid out, in timeline order."""

    def __init__(self, ctx):
        self.ctx, self.fps, self.fr = ctx, ctx.FPS, ctx.fr
        self.entries, self.xfades, self.xstyles, self.fade, self.layers, self.audios = [], {}, {}, None, [], []
