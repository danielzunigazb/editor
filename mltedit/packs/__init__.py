"""Theme packs: one folder per template under each configured packs_dir (setting packs_dirs), holding a theme.json and optionally a fonts/
folder (searched for the pack's fonts, after the global fonts_dirs). Delete the folder and the template is gone; copy and edit one to make a
new template. A broken pack is skipped and reported in registry.problems() (and list_styles) instead of stopping the editor.

theme.json:
  name, label, default (true on exactly one pack: the template of new projects), accent_locked (a note: the template ignores a project accent)
  palette        {ink, paper, accent, accent2, muted} (#rrggbb) + optional extra: [more colours] (confetti, dots...)
  styles         {title, subtitle, caption}: names of text styles (this pack's own or any other loaded one)
  text_styles    {style name: style definition}  (keys as in render/text.py; lists become tuples)
  shape          {name: a registered shape plugin, radius, stroke, + options the shape reads (e.g. glass: gradient stops)}
  lower_third    {scrim: bool}             callout {flag: "panel" | "plate"}
  decor          default frame decoration;  card {bg, bg_options, title, sub, accent, effects, align, vertical_bar, rule, list_role, stat_role, bento, motion}
  plate          {fill, outline, width, color}  (colour refs: palette name or #rrggbb, "@alpha")
  motion         {overlay kind: anim spec};  transition: a registered transition;  music_moods, sfx: tags for the asset library"""
import json, os

from .. import registry


def _tuples(v):
    if isinstance(v, list):
        return tuple(_tuples(x) for x in v)
    if isinstance(v, dict):
        return {k: _tuples(x) for k, x in v.items()}
    return v


def _check(cond, path, msg):
    if not cond:
        raise ValueError(f"{path}: {msg}")


def parse(folder):
    """theme.json of `folder` -> (Theme, {style name: style}). Raises ValueError naming the file and the field."""
    from ..themes import COLOR_RE, PALETTE, Theme
    path = os.path.join(folder, "theme.json")
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    for k in ("name", "label", "palette", "styles", "shape"):
        _check(k in d, path, f"missing '{k}'")
    name = d["name"]
    _check(isinstance(name, str) and name == os.path.basename(os.path.normpath(folder)), path, f"name '{name}' must match the folder name")
    pal = d["palette"]
    for k in PALETTE:
        _check(isinstance(pal.get(k), str) and COLOR_RE.match(pal[k]), path, f"palette.{k} must be #rrggbb")
    shape = d["shape"]
    _check(registry.has("shape", shape.get("name")), path, f"shape '{shape.get('name')}' is not a registered shape plugin (have {registry.names('shape')})")
    styles = {k: _tuples(v) for k, v in d.get("text_styles", {}).items()}
    card = _tuples(d.get("card", {}))
    th = Theme(name=name, label=d["label"], ink=pal["ink"], paper=pal["paper"], accent=pal["accent"], accent2=pal["accent2"], muted=pal["muted"], extra=tuple(pal.get("extra", ())),
               title_style=d["styles"]["title"], subtitle_style=d["styles"]["subtitle"], caption_style=d["styles"]["caption"],
               shape=shape["name"], radius=float(shape.get("radius", 0.0)), stroke=float(shape.get("stroke", 0.0)),
               decor=d.get("decor", "frame"), card_bg=card.get("bg", ""), moods=tuple(d.get("music_moods", ())), sfx=tuple(d.get("sfx", ())),
               motion={**d.get("motion", {}), **({"card": card["motion"]} if card.get("motion") else {})}, transition=d.get("transition", "dissolve"),
               card=card, plate=d.get("plate", {}),
               options={"lower_third": d.get("lower_third", {}), "callout": d.get("callout", {}), "default": bool(d.get("default")),
                        "accent_locked": d.get("accent_locked") or "",
                        "shape": _tuples({k: v for k, v in shape.items() if k not in ("name", "radius", "stroke")})}, origin=folder)
    return th, styles


def validate_loaded(th):
    """Checks that need every pack loaded first (styles may live in another pack)."""
    from .. import anim
    from ..render import text as T
    where = os.path.join(th.origin, "theme.json")
    for role in ("title_style", "subtitle_style", "caption_style"):
        _check(getattr(th, role) in T.STYLES, where, f"styles.{role.split('_')[0]} = '{getattr(th, role)}' is not a loaded text style")
    _check(not th.card_bg or registry.has("card_bg", th.card_bg), where, f"card.bg '{th.card_bg}' is not a registered card background")
    _check(not th.card.get("rule") or registry.has("card_rule", th.card["rule"]), where, f"card.rule '{th.card.get('rule')}' is not a registered card rule")
    _check(registry.has("transition", th.transition), where, f"transition '{th.transition}' is not a registered transition")
    for k, spec in th.motion.items():
        if k != "card":
            anim.validate(spec, f"{where} motion.{k}", None)


def load_all(dirs):
    from ..render import text as T
    loaded = []
    for root in dirs:
        if not os.path.isdir(root):
            registry.note_problem(f"packs dir {root} does not exist")
            continue
        for sub in sorted(os.listdir(root)):
            folder = os.path.join(root, sub)
            if not os.path.isfile(os.path.join(folder, "theme.json")):
                continue
            try:
                th, styles = parse(folder)
                for sname, sdef in styles.items():
                    if sname in T.STYLES.raw():
                        raise ValueError(f"{folder}/theme.json: text style '{sname}' is already defined (by another pack or the engine)")
                for sname, sdef in styles.items():
                    T.STYLES.raw()[sname] = sdef
                fonts = os.path.join(folder, "fonts")
                if os.path.isdir(fonts):
                    T.FONT_DIRS_EXTRA.append(fonts)
                registry.register("theme", th.name, th, origin=folder)
                loaded.append(th)
            except Exception as e:                                       # a broken pack is reported, not fatal
                registry.note_problem(f"theme pack {folder}: {e}")
    for th in loaded:
        try:
            validate_loaded(th)
        except Exception as e:
            registry.unregister("theme", th.name)
            registry.note_problem(f"theme pack {th.origin}: {e}")
    return loaded
