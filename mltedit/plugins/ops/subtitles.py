"""{"op":"subtitles","cues":[{"start":0,"end":2,"text":"..."}],...}: timed text cues (one text layer each), boxed by default."""
from ...ops import Op, op
from ...ops.common import check_text_fits, clean, resolve_style, text_style


@op
class Subtitles(Op):
    name = "subtitles"
    defaults = {"pos": "bottom", "size": 0.05, "fade": 0.0, "style": "auto", "ornament": "none"}
    subtitle_style = True                        # style "auto" -> the template's subtitle style
    box_default = True

    def placement_error(self, o, total):
        if all(c["start"] >= total - 1e-6 for c in o["cues"]):
            return (f"every subtitle starts after the end of the timeline ({total:g}s); add the clips first "
                    f"or check offset_s")

    def layout(self, o, n, where, st):
        cues = o.get("cues")
        if not isinstance(cues, list) or not 1 <= len(cues) <= 300:
            raise ValueError(f"{where}: needs 1-300 cues")
        style = text_style({"pos": "bottom", "size": 0.05, "fade": 0.0, "style": "auto",
                            "ornament": "none", **{k: v for k, v in o.items() if v is not None}}, where, st.ctx)
        for ci, c in enumerate(cues):
            try:
                st_, en_ = float(c["start"]), float(c["end"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"{where}: cue {ci} needs numeric start and end")
            if st_ < 0 or en_ <= st_:
                raise ValueError(f"{where}: cue {ci} has an invalid time range {st_:g}-{en_:g}s")
            st.layers.append({"kind": "text", "op": n, "sub": ci, "start": st_, "dur": en_ - st_, **style,
                              "text": clean(c.get("text"), f"{where} cue {ci}", style["style"])})

    def describe(self, o):
        return f"Subtítulos ({len(o['cues'])} líneas)", f"subtitles x{len(o['cues'])}"

    def check_new(self, o, ctx):
        check_text_fits([(c.get("text"), o.get("size", 0.05)) for c in o.get("cues", [])], resolve_style(o, ctx), o.get("uppercase"), ctx, cue_prefix=True)
