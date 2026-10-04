"""Overlay PNG optimisations: crop full-frame PNGs to their visible pixels, and merge static decoration shown at the same time."""
import json, os

from .. import ops as O

_BBOX = {}                              # cropped-overlay cache: png path -> (cropped path, x, y, w, h) or None


def crop_to_content(path, W_, H_):
    """Crop a full-frame RGBA overlay to the bounding box of its visible pixels. qtblend then composites only that
    rectangle instead of a whole 4K frame. Returns (path, x, y, w, h), or None when cropping would not pay off.
    The result is also stored in a `<png>_crop.json` sidecar, so a later process (or a rebuild after the in-memory memo
    is gone) skips decoding the full-size PNG (0.37 s per 4K overlay)."""
    if path in _BBOX:
        return _BBOX[path]
    out, side = path[:-4] + "_crop.png", path[:-4] + "_crop.json"
    try:                                                    # sidecar is only trusted if it is newer than the source PNG
        if os.path.getmtime(side) >= os.path.getmtime(path):
            with open(side) as f:
                d = json.load(f)
            if d is None:
                _BBOX[path] = None
                return None
            if os.path.exists(out):
                _BBOX[path] = res = (out, *[int(d[k]) for k in ("x", "y", "w", "h")])
                return res
    except (OSError, ValueError, KeyError, TypeError):
        pass
    from PIL import Image
    res = None
    with Image.open(path) as im:
        im = im.convert("RGBA")
        bb = im.getchannel("A").getbbox()
        if bb is not None:
            pad = 2
            x0, y0 = max(0, bb[0] - pad), max(0, bb[1] - pad)
            x1, y1 = min(im.width, bb[2] + pad), min(im.height, bb[3] + pad)
            if (x1 - x0) * (y1 - y0) < 0.6 * im.width * im.height:        # a near-full-frame overlay gains nothing
                if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(path):
                    tmp = out + f".{os.getpid()}.tmp"
                    im.crop((x0, y0, x1, y1)).save(tmp, format="PNG")
                    os.replace(tmp, out)
                res = (out, x0, y0, x1 - x0, y1 - y0)
    tmp = side + f".{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(None if res is None else dict(zip("xywh", res[1:])), f)
    os.replace(tmp, side)
    _BBOX[path] = res
    return res



def merge_layers(layers):
    """Layers whose plugin gives the same merge_key (static decoration shown at exactly the same time, e.g. vignette + frame for the whole
    video) are drawn by ONE compositor: the plugin merges them into one layer. Returns the list of layers to build."""
    groups, order = {}, []
    for L in layers:
        key = O.get_layer(L["kind"]).merge_key(L)
        if key is not None:
            key = (L["kind"], key)
            if key not in groups:
                groups[key] = []; order.append(key)
            groups[key].append(L)
    drop, merged = set(), {}
    for key in order:
        grp = groups[key]
        if len(grp) > 1:
            merged[id(grp[0])] = O.get_layer(key[0]).merge(grp)
            drop.update(id(g) for g in grp[1:])
    return [merged.get(id(L), L) for L in layers if id(L) not in drop]
