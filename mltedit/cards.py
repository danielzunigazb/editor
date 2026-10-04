"""Title / section / quote / list / stat / outro cards in the project's template.

A card is a full-frame picture (procedural background + the theme's type) turned by ffmpeg into a short mp4 with a silent audio
track, so the editor treats it as any other source: add it with add_clip, dissolve into it with crossfade. No engine changes.
Everything is drawn with PIL (no numpy, no external images) and is deterministic: same card -> same pixels."""
import hashlib, os, random, subprocess

from PIL import Image, ImageDraw

from . import registry, shapes
from .render import text as T

LAYOUTS = ("title", "section", "quote", "list", "stat", "outro", "bento")
MAX_ITEMS, MAX_ITEM_CHARS = 5, 60
c_ = shapes._c


def _card(th):
    """The theme's card options with their defaults (theme.json -> card)."""
    c = th.card
    return {"title": c.get("title", th.ink), "sub": c.get("sub", th.muted), "accent": c.get("accent", th.accent), "effects": c.get("effects", False),
            "align": c.get("align", "center"), "vertical_bar": c.get("vertical_bar", False), "rule": c.get("rule", "bar"),
            "list_role": c.get("list_role", "sub"), "stat_role": c.get("stat_role", "accent"),
            "bento": {"title_y": 0.07, "top": 0.28, "bottom": 0.90, **c.get("bento", {})}}


# ------------------------------------------------------------------------------------------------ backgrounds
def background(W, H, th):
    """The theme's card background: the card_bg plugin it names (plugins/card_bgs), seeded per theme and size so it is deterministic."""
    return registry.get("card_bg", th.card_bg)(W, H, th, random.Random(f"bg|{th.name}|{W}x{H}"))


# ------------------------------------------------------------------------------------------------ text helpers
def _block(text, style, px, max_w, max_lines, strict=True):
    """Largest font <= px (down to 55%) in which `text` wraps into <= max_lines lines of max_w. -> (font, track, lines)."""
    st = T.STYLES[style]
    if st["upper"]:
        text = text.upper()
    cur = px
    while True:
        f = T.make_font(style, cur)
        track = st["tracking"] * f.size
        lines = T._wrap(text, f, max_w, track, 0)
        if len(lines) <= max_lines:
            return f, track, lines
        if cur * 0.93 < px * T.MIN_SHRINK:
            if strict:
                raise ValueError(f"'{text[:40]}…' does not fit in {max_lines} line(s) at this size; shorten it")
            return f, track, lines[:max_lines]
        cur *= 0.93


def _gradient_text(size, xy, text, font, track, stops):
    mask = Image.new("L", size, 0)
    T.draw_tracked(ImageDraw.Draw(mask), xy[0], xy[1], text, font, track, 255)
    top, hgt = int(xy[1]), max(1, int(sum(font.getmetrics())))
    g = Image.new("RGBA", size, (0, 0, 0, 0)); g.paste(T.gradient(size[0], hgt, stops), (0, top)); g.putalpha(mask)
    return g


def _line(canvas, th, xy, text, style, font, track, role, align, W):
    """Draw one line (left edge at xy[0] for left-aligned, centred on xy[0] otherwise). role: 'title' | 'sub' | 'accent'.
    A card colour may be a #rrggbb, null (the style's own colour and effects) or a list of gradient stops."""
    cp = _card(th)
    w = T.text_width(text, font, track)
    x = xy[0] - (w / 2 if align == "center" else 0)
    col = cp["accent"] if role == "accent" else cp["title" if role == "title" else "sub"]
    if isinstance(col, tuple):                                                    # gradient stops
        layer = _gradient_text(canvas.size, (x, xy[1]), text, font, track, col)
    else:
        layer = shapes.text_layer(canvas.size, (x, xy[1]), text, style, font, track, c_(col) if col else None, cp["effects"])
    canvas.alpha_composite(layer)
    return w


def _heights(font, n):
    return (sum(font.getmetrics())) * 1.1 * n


def _decor_rule(canvas, th, x, y, w, align, H):
    """The template's signature divider between title and subtitle (x = centre for centred cards, left edge otherwise): its card_rule plugin."""
    ov = Image.new("RGBA", canvas.size, (0, 0, 0, 0))                          # translucent strokes on their own layer (ImageDraw replaces alpha)
    x0 = x - w / 2 if align == "center" else x
    registry.get("card_rule", _card(th)["rule"])(ImageDraw.Draw(ov), th, x, x0, y, w, H, c_(_card(th)["accent"]), max(2, int(0.004 * H)))
    canvas.alpha_composite(ov)


def _tile(canvas, rect, th, H, key):
    """One bento tile in the theme's shape -> (title colour, sub colour, effects). Painted on its own transparent layer and composited:
    ImageDraw on the opaque card would REPLACE the alpha of translucent fills."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    res = shapes.of(th).tile(layer, rect, th, H, key)
    canvas.alpha_composite(layer)
    return res


# ------------------------------------------------------------------------------------------------ the layouts
def render_card(layout, W, H, th, title="", subtitle="", items=(), number="", author="", strict=True, split=False):
    """Full-frame card as an RGBA PIL image. Raises ValueError (with a message to show verbatim) if text does not fit.
    split=True returns (background, [foreground layers]) instead: one transparent layer per element group (title, rule, subtitle, each list row or
    bento tile) so the card can be animated; compositing them over the background gives the same card."""
    if layout not in LAYOUTS:
        raise ValueError(f"layout must be one of {LAYOUTS}")
    title, subtitle, author = (T.clean(title, th.title_style) if title else ""), (T.clean(subtitle, th.caption_style) if subtitle else ""), (T.clean(author, th.caption_style) if author else "")
    if not title and layout != "stat":
        raise ValueError("a card needs a title")
    layers = []
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0)) if split else background(W, H, th)

    def boundary():                                                                # split mode: close the current group, start a fresh transparent layer
        nonlocal canvas
        if split:
            layers.append(canvas)
            canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cp = _card(th)
    align = cp["align"]
    ml = 0.10 * W                                                                  # left margin for left-aligned cards
    cx = ml if align == "left" else W / 2                                          # anchor x
    maxw = (0.80 * W) if align == "center" else (0.78 * W)
    ts, ss = th.title_style, th.caption_style

    def put(lines, font, track, y, role, style):
        for ln in lines:
            _line(canvas, th, (cx, y), ln, style, font, track, role, align, W)
            y += sum(font.getmetrics()) * 1.1
        return y

    if layout in ("title", "outro"):
        tf, ttr, tl = _block(title, ts, 0.115 * H, maxw, 3, strict)
        sf, sstr, sl = _block(subtitle, ss, 0.045 * H, 0.7 * W if align == "center" else maxw, 2, strict) if subtitle else (None, 0, [])
        gap = 0.05 * H
        total = _heights(tf, len(tl)) + (gap + _heights(sf, len(sl)) if sl else 0)
        y = (H - total) / 2 - 0.02 * H
        if cp["vertical_bar"]:                                                    # a vertical accent bar to the left of the block
            ImageDraw.Draw(canvas).rectangle([ml - 0.03 * W, y, ml - 0.03 * W + max(5, 0.006 * H), y + total], fill=c_(cp["accent"]))
        y = put(tl, tf, ttr, y, "title", ts)
        if sl:
            boundary()
            wmax = max(T.text_width(l, tf, ttr) for l in tl)
            _decor_rule(canvas, th, cx, y + gap * 0.45, min(wmax, 0.34 * W), align, H)
            boundary()
            put(sl, sf, sstr, y + gap, "sub", ss)
    elif layout == "section":
        nf, ntr, nl = _block(number or "01", ts, 0.30 * H, 0.5 * W, 1, strict)
        tf, ttr, tl = _block(title, ts, 0.09 * H, maxw, 2, strict)
        sf, sstr, sl = _block(subtitle, ss, 0.04 * H, 0.7 * W, 2, strict) if subtitle else (None, 0, [])
        total = _heights(nf, 1) + 0.02 * H + _heights(tf, len(tl)) + (0.03 * H + _heights(sf, len(sl)) if sl else 0)
        y = (H - total) / 2
        y = put(nl, nf, ntr, y, "accent", ts) + 0.02 * H
        boundary()
        y = put(tl, tf, ttr, y, "title", ts)
        if sl:
            boundary()
            put(sl, sf, sstr, y + 0.03 * H, "sub", ss)
    elif layout == "quote":
        qf = T.make_font(ts, 0.22 * H)
        tf, ttr, tl = _block(title, th.subtitle_style if th.subtitle_style in T.STYLES else ts, 0.062 * H, 0.74 * W if align == "center" else 0.7 * W, 5, strict)
        af, astr, al = _block(author, ss, 0.035 * H, 0.6 * W, 1, strict) if author else (None, 0, [])
        total = 0.15 * H + _heights(tf, len(tl)) + (0.05 * H + _heights(af, 1) if al else 0)
        y = (H - total) / 2
        _line(canvas, th, (cx, y - 0.05 * H), "“", ts, qf, 0, "accent", align, W)
        boundary()
        y = put(tl, tf, ttr, y + 0.15 * H, "title", th.subtitle_style if th.subtitle_style in T.STYLES else ts)
        if al:
            boundary()
            put([("— " + author)], af, astr, y + 0.05 * H, "sub", ss)
    elif layout == "list":
        its = [T.clean(i, th.caption_style) for i in items]
        if not 1 <= len(its) <= MAX_ITEMS or any(len(i) > MAX_ITEM_CHARS or not i for i in its):
            raise ValueError(f"a list card needs 1-{MAX_ITEMS} items of at most {MAX_ITEM_CHARS} characters")
        tf, ttr, tl = _block(title, ts, 0.085 * H, maxw, 2, strict)
        rows = []
        for it in its:
            f, tr, ls = _block(it, ss, 0.052 * H, 0.66 * W, 1, strict)
            rows.append((f, tr, ls[0]))
        gap = 0.032 * H
        total = _heights(tf, len(tl)) + 0.05 * H + sum(_heights(r[0], 1) + gap for r in rows)
        y = (H - total) / 2
        y = put(tl, tf, ttr, y, "title", ts) + 0.05 * H
        left = (W * 0.5 - 0.33 * W) if align == "center" else ml
        acc = c_(cp["accent"])
        for f, tr, ln in rows:
            boundary()
            d = ImageDraw.Draw(canvas)                                             # a new layer per row: draw on the current one
            hh = sum(f.getmetrics())
            rr = 0.011 * H
            d.ellipse([left, y + hh * 0.5 - rr, left + 2 * rr, y + hh * 0.5 + rr], fill=acc)
            _line(canvas, th, (left + 0.045 * H + 2 * rr, y), ln, ss, f, tr, cp["list_role"], "left", W)
            y += hh * 1.1 + gap
    elif layout == "bento":                                                        # up to 4 tiles "figure | label" in the template's own panel style
        its = [T.clean(i, th.caption_style) for i in items]
        if not 1 <= len(its) <= 4 or any(not i or len(i) > 50 for i in its):
            raise ValueError("a bento card needs 1-4 items of the form 'figure | label' (at most 50 characters each; the figure up to 10)")
        tiles = []
        for i in its:
            fig_, _, lab_ = (p_.strip() for p_ in i.partition("|")) if "|" in i else ("", "", i)
            if len(fig_) > 10:
                raise ValueError(f"bento figure '{fig_}' is longer than 10 characters")
            tiles.append((fig_, lab_))
        tf, ttr, tl = _block(title, ts, 0.075 * H, maxw, 1, strict)
        put(tl, tf, ttr, cp["bento"]["title_y"] * H, "title", ts)
        boundary()
        gx0, gx1, gy0, gy1, gap = 0.08 * W, 0.92 * W, cp["bento"]["top"] * H, cp["bento"]["bottom"] * H, 0.025 * H
        gw, gh = gx1 - gx0, gy1 - gy0
        n_ = len(tiles)
        if n_ == 1:
            rects = [(gx0, gy0, gx1, gy1)]
        elif n_ == 2:
            rects = [(gx0, gy0, gx0 + (gw - gap) / 2, gy1), (gx0 + (gw + gap) / 2, gy0, gx1, gy1)]
        elif n_ == 3:
            rects = [(gx0, gy0, gx0 + (gw - gap) / 2, gy1), (gx0 + (gw + gap) / 2, gy0, gx1, gy0 + (gh - gap) / 2), (gx0 + (gw + gap) / 2, gy0 + (gh + gap) / 2, gx1, gy1)]
        else:
            cw_, ch_ = (gw - gap) / 2, (gh - gap) / 2
            rects = [(gx0 + i * (cw_ + gap), gy0 + j * (ch_ + gap), gx0 + i * (cw_ + gap) + cw_, gy0 + j * (ch_ + gap) + ch_) for j in range(2) for i in range(2)]
        for k, ((fig_, lab_), rect) in enumerate(zip(tiles, rects)):
            tcol, scol, eff = _tile(canvas, rect, th, H, f"bento|{k}|{fig_}|{lab_}")
            pad = 0.03 * H
            iw, ih = rect[2] - rect[0] - 2 * pad, rect[3] - rect[1] - 2 * pad
            fpx, lpx = min(0.2 * H, ih * 0.55), min(0.05 * H, ih * 0.3)
            for _ in range(6):                                                     # shrink until the figure and the label fit the tile's height
                ff, ftr, fl = _block(fig_, ts, fpx, iw, 1, strict) if fig_ else (None, 0, [])
                lf, ltr, ll = _block(lab_, ss, lpx, iw, 2, strict)
                if (sum(ff.getmetrics()) * 1.05 if fig_ else 0) + sum(lf.getmetrics()) * 1.1 * len(ll) <= ih:
                    break
                fpx, lpx = fpx * 0.88, lpx * 0.88
            y = rect[1] + pad
            if fig_:
                canvas.alpha_composite(shapes.text_layer(canvas.size, (rect[0] + pad, y), fl[0], ts, ff, ftr, tcol, eff))
                y += sum(ff.getmetrics()) * 1.05
            for ln in ll:
                canvas.alpha_composite(shapes.text_layer(canvas.size, (rect[0] + pad, y), ln, ss, lf, ltr, scol, eff))
                y += sum(lf.getmetrics()) * 1.1
            boundary()
    else:                                                                          # stat: a big figure and its label
        fig = T.clean(number if number else title, th.title_style)
        label = subtitle if subtitle else (title if number else "")
        if not fig or len(fig) > 12:
            raise ValueError("a stat card needs a short figure (number) of at most 12 characters")
        nf, ntr, nl = _block(fig, ts, 0.30 * H, 0.8 * W, 1, strict)
        lf, lstr, ll = _block(label, ss, 0.052 * H, 0.7 * W, 2, strict) if label else (None, 0, [])
        total = _heights(nf, 1) + (0.01 * H + _heights(lf, len(ll)) if ll else 0)
        y = (H - total) / 2
        y = put(nl, nf, ntr, y, cp["stat_role"], ts)
        if ll:
            boundary()
            put(ll, lf, lstr, y - 0.03 * H, "sub", ss)
    if split:
        layers.append(canvas)
        return background(W, H, th), layers
    return canvas


# ------------------------------------------------------------------------------------------------ png + mp4
def card_png(cache_dir, layout, W, H, th, **kw):
    key = repr((layout, W, H, th.name, th.accent, sorted(kw.items())))
    out = os.path.join(cache_dir, f"card_{hashlib.sha1(('v4|' + key).encode()).hexdigest()[:16]}.png")
    if not os.path.exists(out):
        img = render_card(layout, W, H, th, **kw)
        os.makedirs(cache_dir, exist_ok=True)
        tmp = out + f".{os.getpid()}.tmp"
        img.convert("RGB").save(tmp, format="PNG")
        os.replace(tmp, out)
    return out


def card_video(png, out, W, H, fps, dur_s, push=False):
    """PNG -> mp4 (H.264 + silent AAC) of dur_s seconds. push=True adds a slow 6% zoom-in on a 2x supersampled copy."""
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    tmp = out + f".{os.getpid()}.tmp.mp4"
    n = max(1, int(round(dur_s * fps)))
    vf = (f"scale={W * 2}:{H * 2}:flags=lanczos,zoompan=z='min(zoom+{0.06 / n:.6f},1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={fps}"
          if push else f"scale={W}:{H}:flags=lanczos")
    cmd = ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", str(fps), "-i", png, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
           "-vf", vf + ",format=yuv420p", "-frames:v", str(n), "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-c:a", "aac", "-b:a", "96k",
           "-shortest", "-movflags", "+faststart", tmp]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode or not os.path.exists(tmp):
        raise RuntimeError("ffmpeg could not make the card video: " + r.stderr.strip()[-300:])
    os.replace(tmp, out)
    return out


# ------------------------------------------------------------------------------------------------ animated cards
def card_layers(cache_dir, layout, W, H, th, **kw):
    """(background PNG, [foreground layer PNGs]) of a card, cached; compositing them gives the card (diff <= 1 level, measured)."""
    key = hashlib.sha1(('v4|' + repr((layout, W, H, th.name, th.accent, sorted(kw.items())))).encode()).hexdigest()[:16]
    bgp = os.path.join(cache_dir, f"cardbg_{key}.png")
    probe = os.path.join(cache_dir, f"cardfg_{key}_0.png")
    if os.path.exists(bgp) and os.path.exists(probe):
        n = 0
        while os.path.exists(os.path.join(cache_dir, f"cardfg_{key}_{n}.png")):
            n += 1
        return bgp, [os.path.join(cache_dir, f"cardfg_{key}_{i}.png") for i in range(n)]
    bg, layers = render_card(layout, W, H, th, split=True, **kw)
    os.makedirs(cache_dir, exist_ok=True)
    paths = []
    for i, L in enumerate(layers):
        p = os.path.join(cache_dir, f"cardfg_{key}_{i}.png")
        tmp = p + f".{os.getpid()}.tmp"
        L.save(tmp, format="PNG"); os.replace(tmp, p); paths.append(p)
    tmp = bgp + f".{os.getpid()}.tmp"
    bg.convert("RGB").save(tmp, format="PNG"); os.replace(tmp, bgp)
    return bgp, paths


def animated_timing(th, n_layers, dur_s):
    """(enter, stagger, start) in seconds for a card of dur_s: the template's own times, squeezed so the last layer has settled 0.4 s before the end."""
    m = th.motion.get("card", {"enter_s": 0.5, "stagger_s": 0.12, "rise": 0.03, "dx": 0.0})
    start = 0.2
    end = start + (n_layers - 1) * m["stagger_s"] + m["enter_s"]
    k = min(1.0, max(0.25, (dur_s - 0.4) / end)) if end > 0 else 1.0
    return m["enter_s"] * k, m["stagger_s"] * k, start * k, m["rise"], m["dx"]


def card_video_animated(bg_png, layer_pngs, out, W, H, fps, dur_s, th, push=False):
    """Card mp4 whose element groups fade/slide in one after another over the background (ease-out cubic), then hold. One ffmpeg graph."""
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    enter, stagger, start, rise, dx = animated_timing(th, len(layer_pngs), dur_s)
    tmp = out + f".{os.getpid()}.tmp.mp4"
    n = max(1, int(round(dur_s * fps)))
    bgf = (f"scale={W * 2}:{H * 2}:flags=lanczos,zoompan=z='min(zoom+{0.06 / n:.6f},1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={fps}"
           if push else f"scale={W}:{H}:flags=lanczos")
    cmd = ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", str(fps), "-i", bg_png]
    for p in layer_pngs:
        cmd += ["-loop", "1", "-framerate", str(fps), "-i", p]
    cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    graph = [f"[0:v]{bgf}[b0]"]
    for i in range(len(layer_pngs)):
        t0 = start + i * stagger
        prog = f"(1-min(1,max(0,(t-{t0:.4f})/{enter:.4f})))"
        graph.append(f"[{i + 1}:v]format=rgba,fade=t=in:st={t0:.4f}:d={enter:.4f}:alpha=1[l{i}]")
        graph.append(f"[b{i}][l{i}]overlay=x='{dx * W:.3f}*pow({prog},3)':y='{rise * H:.3f}*pow({prog},3)':eval=frame:format=auto[b{i + 1}]")
    graph.append(f"[b{len(layer_pngs)}]format=yuv420p[v]")
    cmd += ["-filter_complex", ";".join(graph), "-map", "[v]", "-map", f"{len(layer_pngs) + 1}:a", "-frames:v", str(n), "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
            "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart", tmp]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if r.returncode or not os.path.exists(tmp):
        raise RuntimeError("ffmpeg could not make the animated card video: " + r.stderr.strip()[-300:])
    os.replace(tmp, out)
    return out
