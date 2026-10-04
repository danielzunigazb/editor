"""Text overlays (text and subtitles ops): a full-frame transparent PNG from render/text.py."""
from ...ops import Layer, layer
from ...render import text as textrender


@layer
class Text(Layer):
    name = "text"
    croppable = True

    def source(self, L, ctx):
        return textrender.render_text_png(L["text"], ctx.W, ctx.H, L["pos"], L["size"], L["color"], L["box"], cache_dir=ctx.CACHE, strict=False,
                                          style=L["style"], uppercase=L["uppercase"], ornament=L["ornament"]), None

    def place(self, L, ctx, n, crop, extra):
        x, y, w, h = crop[1:] if crop else (0, 0, ctx.W, ctx.H)
        return x, y, w, h, L.get("opacity", 1.0), min(ctx.fr(L["fade"]), (n - 1) // 2)

    def label(self, L):
        return f"{'subtitle' if 'sub' in L else 'text'} {L['text'][:24]!r}"

    def zone(self, L, ctx):
        fw, fh = max(ctx.W, 1), max(ctx.H, 1)
        lines_w = len(L["text"].split("\n")) and max(len(x) for x in L["text"].split("\n"))
        px = L["size"] * fh
        wide = textrender.STYLES[L["style"]].get("tracking", 0) >= 0.1             # letter-spaced styles run wider
        width = min(0.9, max(0.1, lines_w * 0.52 * px / fw * (1.0 + (0.16 if wide else 0.03))))
        n_lines = max(1, -(-int(len(L["text"]) * 0.52 * px) // int(0.9 * fw))) if "\n" not in L["text"] else len(L["text"].split("\n"))
        h = (n_lines * 1.12 + 0.6) * L["size"] + (0.8 * L["size"] if L["box"] else 0)
        y0 = {"top": 0.08, "center": 0.5 - h / 2, "bottom": 0.92 - h}[L["pos"]]
        return (0.5 - width / 2, y0, 0.5 + width / 2, y0 + h)

    def svg(self, L):
        return L.get("text", "").replace("\n", " ")[:22], "ctext"
