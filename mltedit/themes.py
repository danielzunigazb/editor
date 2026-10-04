"""Design templates. A template is DATA: a pack folder with a theme.json (see packs/__init__.py for the format), loaded into the registry
under kind "theme". Nothing in the engine names a template: the default one is the pack marked "default": true, and everything a renderer
needs (palette, text styles, shape and its options, card layout options, icon plate, motion, transition, sounds) is read from the Theme.

A project has ONE active theme (project.json "theme"); every overlay that does not name a style/theme explicitly follows it, so switching the
template restyles the whole edit. `accent` can be overridden per project (a client's brand colour)."""
import re
from dataclasses import dataclass, field, replace

from . import registry

COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
PALETTE = ("ink", "paper", "accent", "accent2", "muted")


def rgb(hex_):
    return tuple(int(hex_[i:i + 2], 16) for i in (1, 3, 5))


@dataclass(frozen=True)
class Theme:
    name: str
    label: str                        # one line shown to the model (list_styles / set_template)
    ink: str                          # main text colour on the theme's own panels
    paper: str                        # panel / card background
    accent: str                       # the theme's signature colour (rules, bars, rings, icons)
    accent2: str                      # secondary colour
    muted: str                        # secondary text
    title_style: str                  # text style for titles ("auto" text)
    subtitle_style: str               # text style for subtitles ("auto" subtitles)
    caption_style: str                # lower-third second line, callout subtitle
    shape: str                        # the shape plugin that draws panels, labels, frames, plates (plugins/shapes)
    radius: float = 0.0               # panel corner radius, fraction of frame height
    stroke: float = 0.0               # outline width, fraction of frame height (0 = none)
    decor: str = "frame"              # default frame decoration (graphics kind) for this theme
    card_bg: str = "gradient"         # card background plugin (plugins/card_bgs)
    moods: tuple = ()                 # music moods that suit it (assets manifest)
    sfx: tuple = ()                   # sound-effect tags that suit it
    motion: dict = field(default_factory=dict, compare=False)   # {overlay kind: anim spec} used when the project's motion is on and the op names no anim
    transition: str = "dissolve"      # default style of crossfade(style="auto") when the project's motion is on
    card: dict = field(default_factory=dict, compare=False)     # card options: bg_options, colours, align, rule, roles, bento geometry
    plate: dict = field(default_factory=dict, compare=False)    # icon plate: fill, outline, width, color (colour refs, see color())
    options: dict = field(default_factory=dict, compare=False)  # per-component options: {"lower_third": {...}, "callout": {...}}
    origin: str = field(default="", compare=False)              # the pack folder it came from
    extra: tuple = ()                 # more colours of the palette (confetti, dots...): palette.extra in theme.json

    def opt(self, section, key, default=None):
        return self.options.get(section, {}).get(key, default)

    def color(self, ref, alpha=None):
        """A colour reference -> RGBA: a palette name (ink, paper, accent, accent2, muted) or #rrggbb, optionally '@alpha' (0-255)."""
        if ref is None:
            return None
        base, _, a = str(ref).partition("@")
        hex_ = getattr(self, base) if base in PALETTE else base
        return rgb(hex_) + (int(a) if a else (255 if alpha is None else alpha),)


def _all():
    return dict(registry.items("theme"))


def __getattr__(name):                  # THEMES / NAMES / DEFAULT come from the registry (the loaded packs), never from a list in the code
    if name == "THEMES":
        return _all()
    if name == "NAMES":
        return tuple(_all())
    if name == "DEFAULT":
        return default_name()
    raise AttributeError(name)


def default_name():
    t = _all()
    if not t:
        raise RuntimeError("no theme packs are loaded (setting packs_dirs); the editor needs at least one")
    marked = [n for n, th in t.items() if th.options.get("default")]
    return marked[0] if marked else next(iter(t))


def get(spec=None):
    """Theme for a project's `theme` field ({"name":..., "accent":...} | name | None). Unknown names raise ValueError."""
    themes_ = _all()
    if not spec:
        return themes_[default_name()]
    name, accent = (spec, None) if isinstance(spec, str) else (spec.get("name") or default_name(), spec.get("accent"))
    if name not in themes_:
        raise ValueError(f"unknown template '{name}'; choose one of {tuple(themes_)}")
    t = themes_[name]
    if accent:
        if not COLOR_RE.match(accent):
            raise ValueError("accent must look like #RRGGBB")
        t = replace(t, accent=accent)
    return t


def describe():
    return {n: t.label for n, t in _all().items()}
