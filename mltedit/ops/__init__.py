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
    order = 100                                  # position in lists shown to the LLM (lower first, then by name)
    defaults = {}                                # static defaults of the op's fields: stored with the op on commit, so replaying never depends on the code's defaults
    anchorable = False                           # an overlay/audio edit placed on the timeline by `start`: it is anchored to the clip under that moment
    makes_clip = False                           # adds an entry to the base track (an entry's id is the id of the op that made it)
    animatable = False                           # accepts an `anim` spec
    timed = False                                # has a `start` on the timeline (an overlay or audio): it must begin before the timeline ends

    def layout(self, o, n, where, st):
        """Validate op `o` (index n; `where` prefixes error messages) and add what it makes to st (a LayoutState). Raise ValueError."""
        raise NotImplementedError

    def describe(self, o):
        """(Spanish title, short code) for the viewer's step list."""
        return o.get("op", "?"), o.get("op", "?")

    def check_new(self, o, ctx):
        """Extra validation of a freshly added op at the EXPORT size (ctx.W/H). Raise ValueError."""

    def placement_error(self, o, total):
        """Message if a freshly added op (against a timeline `total` seconds long) would never be seen, else None."""
        if self.timed and o["start"] >= total - 1e-6:
            return (f"{o['op']} starts at {o['start']:g}s but the timeline is only {total:g}s long "
                    f"(add the clips first, or start it earlier)")

    def normalize(self, o, ctx=None):
        """The op with every static default filled in (the canonical form that is stored). Defaults that follow the template (style "auto", box None)
        stay as they are on purpose. `ctx` (the EngineContext) lets an op fill defaults that depend on the sources."""
        return {**self.defaults, **o}

    def freeze(self, o, ctx):
        """Facts about the world the op depends on, measured once at commit and stored in it (image aspect, file signatures), so that
        laying the project out again never reads the disk. Returns the op with them added."""
        return o

    def start_of(self, o):
        """Timeline second at which the edit begins (what an anchor is computed from), or None if it has none."""
        return o.get("start")

    def shifted(self, o, delta):
        """The edit moved `delta` seconds later on the timeline: its start and every time that is relative to the timeline moves with it."""
        return {**o, "start": o["start"] + delta}

    def clip_refs(self, o):
        """Ids of the base-track clips this edit depends on (a cut's clip, a crossfade's two clips, an anchor's clip)."""
        a = o.get("anchor")
        return [a["clip"]] if isinstance(a, dict) and a.get("clip") else []

    def refs(self, o):
        """Ids of the ops this edit depends on: its clips (clip_refs) and the transition an anchor points at."""
        a = o.get("anchor")
        return self.clip_refs(o) + ([a["transition"]] if isinstance(a, dict) and a.get("transition") else [])

    def migrate_refs(self, o, clip_ids):
        """v1 -> v2: the op with its clip positions replaced by clip ids (`clip_ids` = ids of the entries made by the ops before it)."""
        return o

    def resolve_refs(self, o, entries):
        """The op with its references to base-track clips turned into clip ids (`entries` = the layout's entries, each with an `id`). Used when
        the op is committed, so what is stored never depends on positions that later edits shift."""
        return o

    def assets(self, o):
        """Library asset ids the op uses (for credit lines)."""
        return []

    def files(self, o):
        """Files on disk the op reads (a built timeline depends on them)."""
        return []

    def legibility(self, o, i, st):
        """Warnings (strings) when the op's type would be too small at the project's export size st['width'] x st['height']."""
        return []


def project_assets(ops):
    """Library asset ids used by a list of ops, in order."""
    return [a for o in ops for a in (get_op(o.get("op")).assets(o) if get_op(o.get("op")) else [])]


def project_files(ops):
    """Files read by a list of ops."""
    return [f for o in ops for f in (get_op(o.get("op")).files(o) if get_op(o.get("op")) else [])]


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

    def group(self, L):
        """Name of the group this layer collapses into in the timeline summary (e.g. the cues of one subtitle file), or None."""
        return None

    def shift(self, L, delta):
        """The layer moved `delta` seconds later (an anchor resolved to a different moment): its start and times that are relative to the timeline."""
        L["start"] += delta

    def summary(self, L):
        """Extra fields of the layer in the timeline summary (what it shows)."""
        return {}


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
    return tuple(n for n, o in sorted(registry.items("op"), key=lambda i: (i[1].order, i[0])) if o.animatable)


class LayoutState:
    """What the ops of a project build up while they are laid out, in timeline order."""

    def __init__(self, ctx):
        self.ctx, self.fps, self.fr = ctx, ctx.FPS, ctx.fr
        self.entries, self.fade, self.layers, self.audios = [], None, [], []
        self.xfade_pairs = {}              # id(entry a) -> {a, b, frames, style, op}: tied to the entries themselves, so moving clips cannot mix them up

    def clip_index(self, ref, where, what="clip"):
        """Position in the base track of a clip given as an entry position (int) or as the id of the `add` op that made it (str)."""
        if isinstance(ref, str):
            for i, e in enumerate(self.entries):
                if e.get("id") == ref:
                    return i
            raise ValueError(f"{where}: no clip with id '{ref}' (clips: {[e.get('id') for e in self.entries]})")
        if not isinstance(ref, int) or isinstance(ref, bool) or not 0 <= ref < len(self.entries):
            raise ValueError(f"{where}: no timeline entry {ref} (have {len(self.entries)})")
        return ref
