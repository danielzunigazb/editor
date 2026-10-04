"""Shared placement of boxed overlays (pip, image): scaled, in a corner/centre with a 4% margin, or centred on an exact point."""
import os

MARGIN = 0.04


def place_box(L, W, H, h):
    w = W * L["scale"]
    x = W * MARGIN if "left" in L["pos"] else (W - w) / 2 if L["pos"] == "center" else W * (1 - MARGIN) - w
    y = H * MARGIN if "top" in L["pos"] else (H - h) / 2 if L["pos"] == "center" else H * (1 - MARGIN) - h
    if L.get("xy"):                                   # centred on an exact point of the frame (kept inside it)
        x, y = min(max(W * L["xy"][0] - w / 2, 0), W - w), min(max(H * L["xy"][1] - h / 2, 0), H - h)
    return x, y, w, h


def zone_box(L, fw, fh, hf):
    sc = L["scale"]
    x0 = MARGIN if "left" in L["pos"] else (0.5 - sc / 2 if L["pos"] == "center" else 1 - MARGIN - sc)
    y0 = MARGIN if "top" in L["pos"] else (0.5 - hf / 2 if L["pos"] == "center" else 1 - MARGIN - hf)
    if L.get("xy"):
        x0, y0 = min(max(L["xy"][0] - sc / 2, 0), 1 - sc), min(max(L["xy"][1] - hf / 2, 0), 1 - hf)
    return (x0, y0, x0 + sc, y0 + hf)


def name(L):
    return L.get("src") or L.get("icon") or os.path.basename(L.get("path", ""))
