"""Design templates: one Theme per look. A project has ONE active theme (project.json "theme"); every overlay that does not
name a style/theme explicitly follows it, so switching the template restyles the whole edit without redoing it.

The luxury theme reproduces the original look exactly (golden.py proves it pixel by pixel). A theme is data only:
the renderers (textrender.py, graphics.py, cards.py) read it. `accent` can be overridden per project (a client's brand colour)."""
import re
from dataclasses import dataclass, field, replace

COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


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
    title_style: str                  # textrender style for titles ("auto" text)
    subtitle_style: str               # textrender style for subtitles ("auto" subtitles)
    caption_style: str                # lower-third second line, callout subtitle
    shape: str                        # glass | flat | rule | sketch | neon | bubble : the shape language of panels
    radius: float = 0.0               # panel corner radius, fraction of frame height
    stroke: float = 0.0               # outline width, fraction of frame height (0 = none)
    decor: str = "frame"              # default frame decoration (graphics kind) for this theme
    card_bg: str = "gradient"         # cards.py background generator
    moods: tuple = ()                 # music moods that suit it (assets manifest)
    sfx: tuple = ()                   # sound-effect tags that suit it
    swatches: tuple = field(default=(), compare=False)


THEMES = {t.name: t for t in (
    Theme("luxury", "Gold on dark glass; Playfair + Cormorant; diamonds and keylines (the original look)",
          ink="#f6ecd6", paper="#08080b", accent="#d9b25a", accent2="#e8c25f", muted="#c9b48a",
          title_style="luxury", subtitle_style="champagne", caption_style="modern", shape="glass", radius=0.012, stroke=0.0012,
          decor="frame", card_bg="marble", moods=("elegant", "cinematic", "piano"), sfx=("chime", "whoosh-low")),
    Theme("corporate", "Navy and blue, Inter, flat panels with an accent bar; clean business look",
          ink="#0b2545", paper="#ffffff", accent="#1f6feb", accent2="#0b2545", muted="#5b6770",
          title_style="corp-title", subtitle_style="corp-body", caption_style="corp-body", shape="flat", radius=0.006, stroke=0.0,
          decor="brackets", card_bg="gradient", moods=("corporate", "upbeat", "inspiring"), sfx=("click", "whoosh", "ding")),
    Theme("academic", "Paper and ink with a burgundy rule; Source Serif; footnote-style labels; lectures and papers",
          ink="#1e2a3a", paper="#f7f3e8", accent="#7a1f2b", accent2="#b08d57", muted="#5a6472",
          title_style="acad-title", subtitle_style="acad-body", caption_style="acad-body", shape="rule", radius=0.004, stroke=0.0012,
          decor="page", card_bg="paper", moods=("calm", "piano", "acoustic"), sfx=("page-turn", "bell")),
    Theme("sketch", "Hand-drawn: wobbly marker lines, highlighter underlines, Caveat lettering; friendly explainers",
          ink="#222222", paper="#fbfaf5", accent="#e4572e", accent2="#2e86de", muted="#555555",
          title_style="sketch-title", subtitle_style="sketch-body", caption_style="sketch-body", shape="sketch", radius=0.02, stroke=0.0035,
          decor="doodle", card_bg="dots", moods=("ukulele", "light", "playful"), sfx=("pencil", "paper", "pop")),
    Theme("tech", "Dark navy with cyan/magenta neon, Space Grotesk + mono, corner brackets and glow",
          ink="#e8fbff", paper="#0a0f1c", accent="#00e5ff", accent2="#ff2bd6", muted="#7fa6b3",
          title_style="tech-title", subtitle_style="tech-mono", caption_style="tech-mono", shape="neon", radius=0.008, stroke=0.002,
          decor="brackets", card_bg="dark_grid", moods=("electronic", "synth", "futuristic"), sfx=("glitch", "bleep", "power-up")),
    Theme("minimal", "White and black with one orange accent; Manrope; hairlines and air; works for almost anything",
          ink="#111111", paper="#ffffff", accent="#ff5a36", accent2="#111111", muted="#777777",
          title_style="min-title", subtitle_style="min-body", caption_style="min-body", shape="flat", radius=0.0, stroke=0.0008,
          decor="none", card_bg="paper", moods=("ambient", "soft", "minimal"), sfx=("swoosh", "tick")),
    Theme("playful", "Coral, yellow, green and blue; Fredoka; thick outlines and sticker shadows; kids and social",
          ink="#2b2d42", paper="#fff8e7", accent="#ff6b6b", accent2="#4d96ff", muted="#6b6f8a",
          title_style="kids-title", subtitle_style="kids-body", caption_style="kids-body", shape="bubble", radius=0.03, stroke=0.004,
          decor="doodle", card_bg="confetti", moods=("happy", "bouncy", "playful"), sfx=("pop", "boing", "applause", "ding")),
)}
DEFAULT = "luxury"
NAMES = tuple(THEMES)


def get(spec=None):
    """Theme for a project's `theme` field ({"name":..., "accent":...} | name | None). Unknown names raise ValueError."""
    if not spec:
        return THEMES[DEFAULT]
    name, accent = (spec, None) if isinstance(spec, str) else (spec.get("name", DEFAULT), spec.get("accent"))
    if name not in THEMES:
        raise ValueError(f"unknown template '{name}'; choose one of {NAMES}")
    t = THEMES[name]
    if accent:
        if not COLOR_RE.match(accent):
            raise ValueError("accent must look like #RRGGBB")
        t = replace(t, accent=accent)
    return t


def describe():
    return {n: t.label for n, t in THEMES.items()}
