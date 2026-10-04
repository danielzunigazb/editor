#!/usr/bin/env python3
"""Showcase video of an app built with this editor: a template, the repo's own screenshots with callouts on the controls, music from the asset
library. Everything specific to one app (screenshots, scene texts, callout points, music) is in a JSON file (default data/demos/player.json).
Usage: tools/player_demo.py <repo_dir> <out.mp4> [--data FILE] [--quality draft|high] [--template NAME] [--home DIR]
Read-only on the repo. Motion on (transitions, animated cards, callout animations, loudnorm)."""
import argparse, json, os, subprocess, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from mltedit.config import S  # noqa: E402

ap = argparse.ArgumentParser(description="Showcase video from an app's screenshots.")
ap.add_argument("repo", help="folder of the app repository (read-only)")
ap.add_argument("out", help="output .mp4")
ap.add_argument("--data", default=os.path.join(S.data_root, "data", "demos", "player.json"), help="scenes, texts and timing (JSON)")
ap.add_argument("--quality", default="high", choices=("draft", "high"))
ap.add_argument("--template", default="", help="template (default: the one in the data file)")
ap.add_argument("--home", default="", help="work folder (default: <out folder>/_player_home)")
ARGS = ap.parse_args()
REPO, OUT = os.path.abspath(ARGS.repo), os.path.abspath(ARGS.out)
D = json.load(open(ARGS.data, encoding="utf-8"))
IMG = os.path.join(REPO, *D["images_subdir"].split("/"))
HOME = os.path.abspath(ARGS.home) if ARGS.home else os.path.join(os.path.dirname(OUT), "_player_home")
os.environ["MLT_EDITOR_HOME"] = HOME
import server  # noqa: E402

W, H, FPS = D["size"]["width"], D["size"]["height"], D["size"]["fps"]
SCENE, T0, X = D["scene_s"], D["intro_s"], D["transition_s"]      # scene length; where the scenes start on the timeline; transition length
CALL = D["callout_size"]
os.makedirs(HOME, exist_ok=True)
BG = os.path.join(HOME, "bg.mp4")
from PIL import Image, ImageOps  # noqa: E402
bgd = D["background"]
glow = ImageOps.colorize(Image.radial_gradient("L").resize((W, H), Image.BICUBIC), black=tuple(bgd["center"]), white=tuple(bgd["edge"]))   # centre to edge
glow.save(os.path.join(HOME, "bg.png"))
subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", str(FPS), "-i", os.path.join(HOME, "bg.png"), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                "-t", str(bgd["length_s"]), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", BG], check=True)


def crop(name, y0, y1):
    """Enlarged part of a screenshot (full width, rows y0..y1 as fractions) saved next to the project; returns (path, aspect = h / w)."""
    im = Image.open(os.path.join(IMG, name)).convert("RGBA")
    c = im.crop((0, int(im.height * y0), im.width, int(im.height * y1)))
    p = os.path.join(HOME, f"crop_{name[:-4]}_{int(y0 * 100)}_{int(y1 * 100)}.png")
    c.save(p)
    return p, c.height / c.width


def callout(label, sub, x, y, s0, dur, side):
    server.add_callout(label, [[s0, round(x, 4), round(y, 4)], [s0 + dur, round(x, 4), round(y, 4)]], subtitle=sub, start_s=s0, dur_s=dur, side=side, size=CALL)


server.new_project(W, H, FPS, motion=True)          # the template's own motion: animated cards, transitions, overlay animations, ducking-ready music
server.set_template(ARGS.template or D["template"])
server.add_card("title", D["intro"]["title"], D["intro"]["subtitle"], dur_s=T0, push=True)
server.import_clip(BG, "BG")
server.add_clip("BG", 0.0, bgd["clip_s"])           # on screen from T0 - X (the intro card dissolves into it)
lc, oc = D["list_card"], D["outro"]
server.add_card("list", lc["title"], items=lc["items"], dur_s=lc["dur_s"])
server.add_card("outro", oc["title"], oc["subtitle"], dur_s=oc["dur_s"], push=True)
for first in (0, 1, 2):                             # card -> footage -> list card -> outro, each with the template's transition and its whoosh
    server.crossfade(first, X, "auto", sfx="auto")

server.add_graphic("frame", T0 - 0.4, SCENE * len(D["scenes"]) + 0.4, opacity=D["frame_overlay"]["opacity"])
for k, sc in enumerate(D["scenes"]):
    t = T0 + k * SCENE
    server.add_text(sc["headline"], t + 0.15, SCENE - 0.3, position="top", size=0.065)      # no anim given: the template's motion supplies it
    pic = sc.get("image")
    if pic:
        y0, y1 = pic["crop"]
        path, asp = crop(pic["file"], y0, y1)
        cw = min(pic["width"], 0.70 * H / (W * asp))          # keep the picture under the headline
        h = cw * W * asp / H                                  # picture height as a fraction of the frame height
        cx, cy = 0.5, 0.6
        server.add_image(t, SCENE, path=path, at=[cx, cy], scale=cw, anim={"in": "slide-bottom", "out": "slide-bottom", "in_s": 0.6, "out_s": 0.5})
        for j, c in enumerate(sc["callouts"]):
            px, py = c["at"]
            # phone crops: the ring sits on the picture's edge at that row, so the flag falls on the empty background
            x = cx - cw / 2 + ((0.02 if px < 0.5 else 0.98) if pic["edge_callouts"] else px) * cw
            y = cy - h / 2 + (py - y0) / (y1 - y0) * h
            side = ("s" if (py - y0) / (y1 - y0) < 0.25 else "n") + ("w" if px < 0.5 else "e")   # flag towards the nearer edge, onto the empty background
            callout(c["label"], c["sub"], x, y, t + 1.2 + j * 1.5, 2.2, side)
    else:
        pair = sc["pair"]
        for i, im in enumerate(pair["images"]):
            server.add_image(t + i * 0.25, SCENE - 0.25 - i * 0.25, path=os.path.join(IMG, im["file"]), at=[im["cx"], 0.6], scale=im["scale"], anim={"in": im["in"], "out": im["out"]})
        for j, c in enumerate(pair["callouts"]):
            callout(c["label"], c["sub"], c["at"][0], c["at"][1], t + 1.5 + j * 1.6, 2.4, "nw" if c["at"][0] < 0.5 else "ne")

server.add_audio(0.0, None, asset=D["music"]["asset"], volume_db=D["music"]["volume_db"])     # fades and ducking are the defaults with motion on
for k in D["sfx"]["scenes"]:
    server.add_audio(T0 + k * SCENE + 0.1, asset=D["sfx"]["asset"], volume_db=D["sfx"]["volume_db"])

tl = server.get_timeline()
print("duration", tl["duration_s"], "warnings", tl["warnings"])
r = server.export(OUT, quality=ARGS.quality, overwrite=True, master="loudnorm")
print(r)
