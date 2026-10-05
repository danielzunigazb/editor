"""Cheap checks of a rendered still, so the model is told what it would otherwise have to notice by eye. Pure Python on the RGB bytes already rendered (a ~80x45
luma sample, a few milliseconds). Three things only, all statements about pixels:
  black      the frame is almost black (fine inside a fade or a dip, a missing clip otherwise)
  blown out  the frame is almost white
  low contrast text   in the band where a text layer that is on screen at that time sits (top / center / bottom), the luma barely varies: the text is probably
             hard to read (a heuristic: the text is already drawn in the frame, so this measures its surroundings, it does not read it)
Not covered, and said so wherever these notes are shown: text over a person's face or over the subject, colour casts, sharpness, sync."""

BANDS = {"top": (0.0, 0.30), "center": (0.35, 0.65), "middle": (0.35, 0.65), "bottom": (0.70, 1.0)}
MAX_SAMPLE = 96


def luma_sample(frame, y0=0.0, y1=1.0, dense=False):
    """Sorted luma values of a grid over rows y0..y1 (fractions) of an (w, h, rgb bytes) frame, central 80 % of the width. dense: every pixel (the text band:
    letter strokes are 1-3 px thin and a sparse grid steps over them)."""
    w, h, rgb = frame
    step = 1 if dense else max(1, w // MAX_SAMPLE)
    xs = range(int(w * 0.1), int(w * 0.9), step)
    ys = range(int(h * y0), max(int(h * y0) + 1, int(h * y1)), 1 if dense else max(1, step))
    out = []
    for y in ys:
        base = y * w * 3
        for x in xs:
            i = base + x * 3
            out.append((rgb[i] * 299 + rgb[i + 1] * 587 + rgb[i + 2] * 114) // 1000)
    out.sort()
    return out


def pct(v, p):
    return v[min(len(v) - 1, int(len(v) * p))]


def notes(frame, t, layers=(), black=14, white=240, contrast=45):
    """What is wrong in this frame at timeline time t. `layers`: the layout's layers (dicts with kind, start, dur, pos, text)."""
    v = luma_sample(frame)
    mean = sum(v) / len(v)
    out = []
    if mean < black and pct(v, 0.95) < 3 * black:
        out.append(f"{t:g}s: the frame is almost black (expected only in a fade or dip to black; otherwise a clip or card is missing here)")
    elif mean > white and pct(v, 0.05) > 200:
        out.append(f"{t:g}s: the frame is almost white (blown out)")
    for L in layers:
        if L.get("kind") != "text" or not (L["start"] <= t < L["start"] + L["dur"]):
            continue
        band = BANDS.get(L.get("pos"))
        if not band:
            continue
        b = luma_sample(frame, *band, dense=True)
        if pct(b, 0.99) - pct(b, 0.01) < contrast:
            out.append(f"{t:g}s: the text '{str(L.get('text', ''))[:40]}' may be hard to read: almost no contrast in its {L['pos']} band (luma range {pct(b, 0.99) - pct(b, 0.01)})")
    return out
