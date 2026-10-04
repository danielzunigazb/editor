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
    motion: dict = field(default_factory=dict, compare=False)   # {overlay kind: anim spec} used when the project's motion is on and the op names no anim
    transition: str = "dissolve"       # default style of crossfade(style="auto") when the project's motion is on (transitions.py)
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
    Theme("neobrutalism", "Yellow, violet and cream; Space Grotesk; square panels with thick ink borders and hard offset shadows",
          ink="#1c293c", paper="#fbfbf9", accent="#fdc800", accent2="#432dd7", muted="#4a5568",
          title_style="brut-title", subtitle_style="brut-body", caption_style="brut-body", shape="brutal", radius=0.0, stroke=0.0045,
          decor="frame", card_bg="brutal", moods=("funk", "indie", "upbeat"), sfx=("click", "pop", "whoosh")),
    Theme("terracotta", "Clay, cream and coffee; DM Serif Display; warm paper panels with a clay rule and grain; travel, food, craft",
          ink="#3e2b1e", paper="#f3e9d8", accent="#c56a3c", accent2="#8a5a3b", muted="#7a6552",
          title_style="terra-title", subtitle_style="terra-body", caption_style="terra-body", shape="rule", radius=0.004, stroke=0.0012,
          decor="page", card_bg="grain", moods=("acoustic", "warm", "calm"), sfx=("page-turn", "bell", "pluck")),
    Theme("cinema", "Black, white and rose; Oswald condensed capitals with wide tracking; no panels; trailers and reels",
          ink="#ffffff", paper="#09090b", accent="#f43f5e", accent2="#8b5cf6", muted="#a1a1aa",
          title_style="cine-title", subtitle_style="cine-body", caption_style="cine-body", shape="cinema", radius=0.0, stroke=0.0015,
          decor="none", card_bg="black", moods=("cinematic", "epic", "dramatic"), sfx=("hit", "whoosh-low", "rise")),
    Theme("terminal", "Black with phosphor green; Space Mono + IBM Plex Mono; square panels, brackets and scanlines; dev and software demos",
          ink="#b6f5d8", paper="#0b0c14", accent="#2db58a", accent2="#37f712", muted="#5f8f7c",
          title_style="term-title", subtitle_style="term-mono", caption_style="term-mono", shape="term", radius=0.0, stroke=0.0022,
          decor="brackets", card_bg="scanlines", moods=("electronic", "dark", "synth"), sfx=("click", "bleep", "glitch")),
    Theme("arcade", "Black, blue and yellow; Press Start 2P and VT323 pixel type with no smoothing; block borders and dither; games and retro",
          ink="#ffffff", paper="#05060f", accent="#ffda14", accent2="#2a3fe5", muted="#f4b9b0",
          title_style="arc-title", subtitle_style="arc-body", caption_style="arc-body", shape="pixel", radius=0.0, stroke=0.0055,
          decor="frame", card_bg="dither", moods=("chiptune", "retro", "game"), sfx=("power-up", "bleep", "coin")),
    Theme("riso", "Pink and blue risograph inks on warm paper; Space Grotesk + Space Mono; misregistered offsets and halftone; zines, music",
          ink="#2c40a7", paper="#f6efe2", accent="#f237a1", accent2="#2c40a7", muted="#6b6f9a",
          title_style="riso-title", subtitle_style="riso-body", caption_style="riso-body", shape="riso", radius=0.0, stroke=0.003,
          decor="frame", card_bg="halftone", moods=("indie", "funk", "lofi"), sfx=("pop", "pluck", "click")),
    Theme("saas", "Near-black with a blue accent; IBM Plex Sans/Mono; flat dark panels with a 1 px line; product demos and dashboards",
          ink="#fafafa", paper="#111114", accent="#2f81f7", accent2="#3f3f46", muted="#a1a1aa",
          title_style="saas-title", subtitle_style="saas-body", caption_style="saas-body", shape="flat", radius=0.006, stroke=0.0012,
          decor="brackets", card_bg="blobs", moods=("corporate", "electronic", "ambient"), sfx=("click", "ding", "whoosh")),
    Theme("glass", "Frosted tinted glass over an indigo gradient; Plus Jakarta Sans; luminous borders (imitated: no real blur of the video)",
          ink="#ffffff", paper="#0d1b4c", accent="#6ea8ff", accent2="#b78cff", muted="#d0dcff",
          title_style="glass-title", subtitle_style="glass-body", caption_style="glass-body", shape="frost", radius=0.02, stroke=0.0014,
          decor="frame", card_bg="blobs", moods=("ambient", "soft", "electronic"), sfx=("chime", "whoosh", "ding")),
)}
# per-template default transition (used by crossfade(style="auto") in projects with motion on)
TRANSITIONS = {"luxury": "dissolve", "corporate": "wipe-right", "academic": "dissolve", "sketch": "iris-in", "tech": "blinds-v", "minimal": "dissolve", "playful": "iris-out",
               "neobrutalism": "slide-left", "terracotta": "dissolve", "cinema": "dissolve", "terminal": "blinds-h", "arcade": "blinds-v", "riso": "diagonal",
               "saas": "slide-up", "glass": "dissolve"}
def _m(lt, tx, im, pip=None):
    return {"lower_third": lt, "text": tx, "image": im, "pip": pip or {"in": "fade", "out": "fade", "in_s": 0.4, "out_s": 0.4}}


MOTION = {   # the template's own character: how its overlays arrive and leave (anim specs, see anim.py); explicit anim on an op always wins
    "luxury": _m({"in": "slide-left", "out": "fade", "in_s": 0.8, "out_s": 0.6, "ease_in": "out"}, {"in": "fade", "out": "fade", "in_s": 0.7, "out_s": 0.6}, {"in": "zoom", "out": "fade", "in_s": 0.7, "out_s": 0.5}),
    "corporate": _m({"in": "slide-left", "out": "slide-left", "in_s": 0.45, "out_s": 0.35}, {"in": "rise", "out": "fade", "in_s": 0.5, "out_s": 0.35}, {"in": "fade", "out": "fade", "in_s": 0.4, "out_s": 0.3}),
    "academic": _m({"in": "fade", "out": "fade", "in_s": 0.6, "out_s": 0.5}, {"in": "fade", "out": "fade", "in_s": 0.6, "out_s": 0.5}, {"in": "fade", "out": "fade", "in_s": 0.5, "out_s": 0.4}),
    "sketch": _m({"in": "pop", "out": "zoom", "in_s": 0.45, "out_s": 0.3}, {"in": "pop", "out": "fade", "in_s": 0.4, "out_s": 0.3}, {"in": "spin", "out": "zoom", "in_s": 0.5, "out_s": 0.3}),
    "tech": _m({"in": "wipe", "out": "wipe", "in_s": 0.4, "out_s": 0.3}, {"in": "wipe", "out": "fade", "in_s": 0.5, "out_s": 0.3}, {"in": "zoom", "out": "zoom", "in_s": 0.3, "out_s": 0.25}),
    "minimal": _m({"in": "rise", "out": "fade", "in_s": 0.6, "out_s": 0.5, "ease_in": "out"}, {"in": "fade", "out": "fade", "in_s": 0.8, "out_s": 0.6}, {"in": "fade", "out": "fade", "in_s": 0.6, "out_s": 0.5}),
    "playful": _m({"in": "drop", "out": "zoom", "in_s": 0.6, "out_s": 0.3}, {"in": "pop", "out": "pop", "in_s": 0.45, "out_s": 0.3}, {"in": "spin", "out": "pop", "in_s": 0.55, "out_s": 0.3}),
    "neobrutalism": _m({"in": "slide-left", "out": "slide-left", "in_s": 0.3, "out_s": 0.25, "ease_in": "back"}, {"in": "slide-bottom", "out": "slide-bottom", "in_s": 0.3, "out_s": 0.25}, {"in": "pop", "out": "zoom", "in_s": 0.3, "out_s": 0.25}),
    "terracotta": _m({"in": "rise", "out": "fade", "in_s": 0.7, "out_s": 0.5}, {"in": "fade", "out": "fade", "in_s": 0.7, "out_s": 0.5}, {"in": "zoom", "out": "fade", "in_s": 0.6, "out_s": 0.4}),
    "cinema": _m({"in": "fade", "out": "fade", "in_s": 0.9, "out_s": 0.8}, {"in": "fade", "out": "fade", "in_s": 1.0, "out_s": 0.8}, {"in": "fade", "out": "fade", "in_s": 0.8, "out_s": 0.7}),
    "terminal": _m({"in": "wipe", "out": "wipe", "in_s": 0.35, "out_s": 0.25}, {"in": "wipe", "out": "wipe", "in_s": 0.6, "out_s": 0.3}, {"in": "pop", "out": "fade", "in_s": 0.2, "out_s": 0.2, "ease_in": "linear"}),
    "arcade": _m({"in": "wipe", "out": "wipe", "in_s": 0.4, "out_s": 0.3}, {"in": "wipe", "out": "wipe", "in_s": 0.5, "out_s": 0.3}, {"in": "drop", "out": "zoom", "in_s": 0.5, "out_s": 0.25}),
    "riso": _m({"in": "slide-right", "out": "slide-right", "in_s": 0.4, "out_s": 0.3}, {"in": "rise", "out": "fade", "in_s": 0.5, "out_s": 0.3}, {"in": "spin", "out": "zoom", "in_s": 0.5, "out_s": 0.3}),
    "saas": _m({"in": "rise", "out": "fade", "in_s": 0.4, "out_s": 0.3}, {"in": "rise", "out": "fade", "in_s": 0.45, "out_s": 0.3}, {"in": "fade", "out": "fade", "in_s": 0.3, "out_s": 0.25}),
    "glass": _m({"in": "rise", "out": "fade", "in_s": 0.6, "out_s": 0.5}, {"in": "fade", "out": "fade", "in_s": 0.7, "out_s": 0.5}, {"in": "zoom", "out": "fade", "in_s": 0.6, "out_s": 0.4}),
}
CARD_MOTION = {   # how a card's element groups arrive: (enter seconds, stagger seconds, rise = start offset in frame heights (+ below, - above), dx = start offset in frame widths)
    "luxury": (0.9, 0.22, 0.02, 0.0), "corporate": (0.5, 0.12, 0.03, 0.0), "academic": (0.7, 0.18, 0.0, 0.0), "sketch": (0.5, 0.15, 0.05, 0.0),
    "tech": (0.4, 0.10, 0.0, -0.04), "minimal": (0.8, 0.20, 0.015, 0.0), "playful": (0.55, 0.14, -0.05, 0.0), "neobrutalism": (0.35, 0.10, 0.0, -0.06),
    "terracotta": (0.8, 0.20, 0.02, 0.0), "cinema": (1.1, 0.30, 0.0, 0.0), "terminal": (0.3, 0.10, 0.0, -0.03), "arcade": (0.35, 0.12, -0.04, 0.0),
    "riso": (0.5, 0.12, 0.0, 0.06), "saas": (0.45, 0.10, 0.03, 0.0), "glass": (0.7, 0.16, 0.025, 0.0)}
CALLOUT_MOTION = {
    "luxury": {"in": "draw", "out": "fade", "in_s": 0.7, "out_s": 0.4}, "corporate": {"in": "pop", "out": "fade", "in_s": 0.35, "out_s": 0.3},
    "academic": {"in": "draw", "out": "fade", "in_s": 0.6, "out_s": 0.4}, "sketch": {"in": "pop", "out": "zoom", "in_s": 0.45, "out_s": 0.3},
    "tech": {"in": "draw", "out": "draw", "in_s": 0.35, "out_s": 0.25}, "minimal": {"in": "fade", "out": "fade", "in_s": 0.5, "out_s": 0.4},
    "playful": {"in": "pop", "out": "pop", "in_s": 0.5, "out_s": 0.3}, "neobrutalism": {"in": "pop", "out": "zoom", "in_s": 0.3, "out_s": 0.25},
    "terracotta": {"in": "draw", "out": "fade", "in_s": 0.6, "out_s": 0.4}, "cinema": {"in": "fade", "out": "fade", "in_s": 0.8, "out_s": 0.6},
    "terminal": {"in": "draw", "out": "draw", "in_s": 0.3, "out_s": 0.2}, "arcade": {"in": "pop", "out": "zoom", "in_s": 0.3, "out_s": 0.25, "ease_in": "linear"},
    "riso": {"in": "draw", "out": "fade", "in_s": 0.45, "out_s": 0.3}, "saas": {"in": "pop", "out": "fade", "in_s": 0.3, "out_s": 0.25},
    "glass": {"in": "draw", "out": "fade", "in_s": 0.5, "out_s": 0.4}}
for _n, _c in CALLOUT_MOTION.items():
    MOTION[_n]["callout"] = _c
for _n, _spec in CARD_MOTION.items():
    MOTION[_n]["card"] = dict(zip(("enter_s", "stagger_s", "rise", "dx"), _spec))
THEMES = {n: replace(t, transition=TRANSITIONS[n], motion=MOTION[n]) for n, t in THEMES.items()}
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
