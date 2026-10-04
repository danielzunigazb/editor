"""The built-in card layouts: title, section, quote, list, stat, outro, bento."""
from PIL import ImageDraw

from ...cards import layout, _heights, MAX_ITEMS, MAX_ITEM_CHARS, c_
from ...render import text as T
from ... import shapes


def _title(c):
    """A big title, the template's divider and an optional subtitle (also the outro)."""
    W, H, cp = c.W, c.H, c.cp
    tf, ttr, tl = c.block(c.title, c.ts, 0.115 * H, c.maxw, 3)
    sf, sstr, sl = c.block(c.subtitle, c.ss, 0.045 * H, 0.7 * W if c.align == "center" else c.maxw, 2) if c.subtitle else (None, 0, [])
    gap = 0.05 * H
    total = _heights(tf, len(tl)) + (gap + _heights(sf, len(sl)) if sl else 0)
    y = (H - total) / 2 - 0.02 * H
    if cp["vertical_bar"]:                                                        # a vertical accent bar to the left of the block
        ImageDraw.Draw(c.canvas).rectangle([c.ml - 0.03 * W, y, c.ml - 0.03 * W + max(5, 0.006 * H), y + total], fill=c_(cp["accent"]))
    y = c.put(tl, tf, ttr, y, "title", c.ts)
    if sl:
        c.boundary()
        wmax = max(T.text_width(l, tf, ttr) for l in tl)
        c.rule(c.cx, y + gap * 0.45, min(wmax, 0.34 * W))
        c.boundary()
        c.put(sl, sf, sstr, y + gap, "sub", c.ss)


@layout("title")
def title(c):
    _title(c)


@layout("section")
def section(c):
    W, H = c.W, c.H
    nf, ntr, nl = c.block(c.number or "01", c.ts, 0.30 * H, 0.5 * W, 1)
    tf, ttr, tl = c.block(c.title, c.ts, 0.09 * H, c.maxw, 2)
    sf, sstr, sl = c.block(c.subtitle, c.ss, 0.04 * H, 0.7 * W, 2) if c.subtitle else (None, 0, [])
    total = _heights(nf, 1) + 0.02 * H + _heights(tf, len(tl)) + (0.03 * H + _heights(sf, len(sl)) if sl else 0)
    y = (H - total) / 2
    y = c.put(nl, nf, ntr, y, "accent", c.ts) + 0.02 * H
    c.boundary()
    y = c.put(tl, tf, ttr, y, "title", c.ts)
    if sl:
        c.boundary()
        c.put(sl, sf, sstr, y + 0.03 * H, "sub", c.ss)


@layout("quote")
def quote(c):
    W, H, th = c.W, c.H, c.th
    qstyle = th.subtitle_style if th.subtitle_style in T.STYLES else c.ts
    qf = T.make_font(c.ts, 0.22 * H)
    tf, ttr, tl = c.block(c.title, qstyle, 0.062 * H, 0.74 * W if c.align == "center" else 0.7 * W, 5)
    af, astr, al = c.block(c.author, c.ss, 0.035 * H, 0.6 * W, 1) if c.author else (None, 0, [])
    total = 0.15 * H + _heights(tf, len(tl)) + (0.05 * H + _heights(af, 1) if al else 0)
    y = (H - total) / 2
    c.line((c.cx, y - 0.05 * H), "“", c.ts, qf, 0, "accent")
    c.boundary()
    y = c.put(tl, tf, ttr, y + 0.15 * H, "title", qstyle)
    if al:
        c.boundary()
        c.put([("— " + c.author)], af, astr, y + 0.05 * H, "sub", c.ss)


@layout("list")
def list_(c):
    W, H, th, cp = c.W, c.H, c.th, c.cp
    its = [T.clean(i, th.caption_style) for i in c.items]
    if not 1 <= len(its) <= MAX_ITEMS or any(len(i) > MAX_ITEM_CHARS or not i for i in its):
        raise ValueError(f"a list card needs 1-{MAX_ITEMS} items of at most {MAX_ITEM_CHARS} characters")
    tf, ttr, tl = c.block(c.title, c.ts, 0.085 * H, c.maxw, 2)
    rows = []
    for it in its:
        f, tr, ls = c.block(it, c.ss, 0.052 * H, 0.66 * W, 1)
        rows.append((f, tr, ls[0]))
    gap = 0.032 * H
    total = _heights(tf, len(tl)) + 0.05 * H + sum(_heights(r[0], 1) + gap for r in rows)
    y = (H - total) / 2
    y = c.put(tl, tf, ttr, y, "title", c.ts) + 0.05 * H
    left = (W * 0.5 - 0.33 * W) if c.align == "center" else c.ml
    acc = c_(cp["accent"])
    for f, tr, ln in rows:
        c.boundary()
        d = ImageDraw.Draw(c.canvas)                                               # a new layer per row: draw on the current one
        hh = sum(f.getmetrics())
        rr = 0.011 * H
        d.ellipse([left, y + hh * 0.5 - rr, left + 2 * rr, y + hh * 0.5 + rr], fill=acc)
        c.line((left + 0.045 * H + 2 * rr, y), ln, c.ss, f, tr, cp["list_role"], "left")
        y += hh * 1.1 + gap


@layout("stat", needs_title=False)
def stat(c):
    """A big figure and its label."""
    W, H, th = c.W, c.H, c.th
    fig = T.clean(c.number if c.number else c.title, th.title_style)
    label = c.subtitle if c.subtitle else (c.title if c.number else "")
    if not fig or len(fig) > 12:
        raise ValueError("a stat card needs a short figure (number) of at most 12 characters")
    nf, ntr, nl = c.block(fig, c.ts, 0.30 * H, 0.8 * W, 1)
    lf, lstr, ll = c.block(label, c.ss, 0.052 * H, 0.7 * W, 2) if label else (None, 0, [])
    total = _heights(nf, 1) + (0.01 * H + _heights(lf, len(ll)) if ll else 0)
    y = (H - total) / 2
    y = c.put(nl, nf, ntr, y, c.cp["stat_role"], c.ts)
    if ll:
        c.boundary()
        c.put(ll, lf, lstr, y - 0.03 * H, "sub", c.ss)


@layout("outro")
def outro(c):
    _title(c)


def _grid(n, gx0, gx1, gy0, gy1, gap):
    gw, gh = gx1 - gx0, gy1 - gy0
    if n == 1:
        return [(gx0, gy0, gx1, gy1)]
    if n == 2:
        return [(gx0, gy0, gx0 + (gw - gap) / 2, gy1), (gx0 + (gw + gap) / 2, gy0, gx1, gy1)]
    if n == 3:
        return [(gx0, gy0, gx0 + (gw - gap) / 2, gy1), (gx0 + (gw + gap) / 2, gy0, gx1, gy0 + (gh - gap) / 2), (gx0 + (gw + gap) / 2, gy0 + (gh + gap) / 2, gx1, gy1)]
    cw_, ch_ = (gw - gap) / 2, (gh - gap) / 2
    return [(gx0 + i * (cw_ + gap), gy0 + j * (ch_ + gap), gx0 + i * (cw_ + gap) + cw_, gy0 + j * (ch_ + gap) + ch_) for j in range(2) for i in range(2)]


@layout("bento")
def bento(c):
    """Up to 4 tiles 'figure | label' in the template's own panel style."""
    W, H, th, cp = c.W, c.H, c.th, c.cp
    its = [T.clean(i, th.caption_style) for i in c.items]
    if not 1 <= len(its) <= 4 or any(not i or len(i) > 50 for i in its):
        raise ValueError("a bento card needs 1-4 items of the form 'figure | label' (at most 50 characters each; the figure up to 10)")
    tiles = []
    for i in its:
        fig_, _, lab_ = (p_.strip() for p_ in i.partition("|")) if "|" in i else ("", "", i)
        if len(fig_) > 10:
            raise ValueError(f"bento figure '{fig_}' is longer than 10 characters")
        tiles.append((fig_, lab_))
    tf, ttr, tl = c.block(c.title, c.ts, 0.075 * H, c.maxw, 1)
    c.put(tl, tf, ttr, cp["bento"]["title_y"] * H, "title", c.ts)
    c.boundary()
    rects = _grid(len(tiles), 0.08 * W, 0.92 * W, cp["bento"]["top"] * H, cp["bento"]["bottom"] * H, 0.025 * H)
    for k, ((fig_, lab_), rect) in enumerate(zip(tiles, rects)):
        tcol, scol, eff = c.tile(rect, f"bento|{k}|{fig_}|{lab_}")
        pad = 0.03 * H
        iw, ih = rect[2] - rect[0] - 2 * pad, rect[3] - rect[1] - 2 * pad
        fpx, lpx = min(0.2 * H, ih * 0.55), min(0.05 * H, ih * 0.3)
        for _ in range(6):                                                         # shrink until the figure and the label fit the tile's height
            ff, ftr, fl = c.block(fig_, c.ts, fpx, iw, 1) if fig_ else (None, 0, [])
            lf, ltr, ll = c.block(lab_, c.ss, lpx, iw, 2)
            if (sum(ff.getmetrics()) * 1.05 if fig_ else 0) + sum(lf.getmetrics()) * 1.1 * len(ll) <= ih:
                break
            fpx, lpx = fpx * 0.88, lpx * 0.88
        y = rect[1] + pad
        if fig_:
            c.canvas.alpha_composite(shapes.text_layer(c.canvas.size, (rect[0] + pad, y), fl[0], c.ts, ff, ftr, tcol, eff))
            y += sum(ff.getmetrics()) * 1.05
        for ln in ll:
            c.canvas.alpha_composite(shapes.text_layer(c.canvas.size, (rect[0] + pad, y), ln, c.ss, lf, ltr, scol, eff))
            y += sum(lf.getmetrics()) * 1.1
        c.boundary()
