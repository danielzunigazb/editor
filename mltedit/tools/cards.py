"""Full-screen cards (title, section, quote, list, stat, outro, bento) as sources."""
import hashlib, os

from .. import cards
from .. import themes
from .. import engine as live
from .. import server as sv
from .. import project as P
from . import edit_tool

@edit_tool
def add_card(layout: str, title: str = "", subtitle: str = "", items: list[str] | None = None, number: str = "",
             author: str = "", dur_s: float = 4.0, push: bool = False, append: bool = True, theme: str = "auto", animate: bool | None = None) -> dict:
    """Make a full-screen card in the project's template and (append=true) add it to the END of the main track.
    layout: title (title+subtitle) | section (number+title) | quote (title = the quote, author) | list (title + items, up to 5) |
    stat (number = the figure, title/subtitle = its label) | outro (title+subtitle) | bento (title + 1-4 items 'figure|label', e.g. '18 %|growth'). dur_s 0.5-30. push=true adds a slow zoom-in.
    animate: the card's element groups (title, rule, subtitle, list rows, tiles) arrive one after another in the template's own way; default = on
    when the project's motion is on (set_template/new_project motion), otherwise a still picture. The card also becomes a source (CARD1, CARD2...) usable with add_clip/crossfade. It is appended like any clip, so add an INTRO
    card before the clips and an OUTRO after them; dissolve into it with crossfade. Not available inside apply_ops."""
    if not 0.5 <= dur_s <= 30:
        raise ValueError("dur_s must be between 0.5 and 30 seconds")
    with sv.locked():
        st = sv.load()
        sv.check_revision(st)
        sv.bind(st)
        th = themes.get(live._theme_key({"theme": theme}, "add_card"))
        if layout not in cards.LAYOUTS:
            raise ValueError(f"layout must be one of {cards.LAYOUTS}")
        W, H, fps = st["width"] // 2 * 2, st["height"] // 2 * 2, st["fps"]
        kw = dict(title=title, subtitle=subtitle, items=tuple(items or ()), number=number, author=author)
        os.makedirs(os.path.join(sv.HOME, "cards"), exist_ok=True)
        if live.MOTION if animate is None else animate:
            bg_png, layer_pngs = cards.card_layers(os.path.join(sv.HOME, "cache"), layout, W, H, th, **kw)
            key = hashlib.sha1(f"anim|{bg_png}|{len(layer_pngs)}|{fps}|{dur_s}|{push}|{th.motion.get('card')}".encode()).hexdigest()[:16]
            mp4 = cards.card_video_animated(bg_png, layer_pngs, os.path.join(sv.HOME, "cards", f"card_{key}.mp4"), W, H, fps, dur_s, th, push)
        else:
            png = cards.card_png(os.path.join(sv.HOME, "cache"), layout, W, H, th, **kw)
            key = hashlib.sha1(f"{png}|{fps}|{dur_s}|{push}".encode()).hexdigest()[:16]
            mp4 = cards.card_video(png, os.path.join(sv.HOME, "cards", f"card_{key}.mp4"), W, H, fps, dur_s, push)
        sid = next((k for k, v in st["sources"].items() if v["path"] == mp4), None)
        if sid is None:
            if len(st["sources"]) >= sv.MAX_SOURCES:
                raise ValueError(f"the project already has {sv.MAX_SOURCES} sources (the limit)")
            n = 1
            while f"CARD{n}" in st["sources"]:
                n += 1
            sid = f"CARD{n}"
            st["sources"][sid] = {"path": mp4, "sig": P.file_sig(mp4), **sv._probe(mp4)}
        if append:
            if len(st["ops"]) >= sv.MAX_OPS:
                raise ValueError(f"the project already has {sv.MAX_OPS} edits (the limit)")
            op = sv.prepare(st, sv.BUILDERS["add_clip"](sid))
            sv._validate(st, op)
            st["ops"].append(op)
            sv.push_undo(st, {"k": "pop", "id": op["id"]})
        sv.save(st, {"kind": "add_card", "source": sid, **({"op": op["id"]} if append else {})})
        return {"card": sid, "duration_s": st["sources"][sid]["duration_s"], "appended": append, **({"op_id": op["id"]} if append else {}), **sv.summary(st)}
