#!/usr/bin/env python3
"""One demo per template (animated intro card, template transition, footage with lower third / icon / label / text, transition, outro card, music + effects),
project motion ON, high quality, loudnorm master. Texts, music ids and timing come from a JSON file (default data/demos/templates.json).
Usage: tools/make_demos.py [--out DIR] [--footage FILE] [--data FILE] [--quality high|draft] [template ...]   (default: every template in the data file)
Audio comes from the asset library (needs the cache or R2_WORKER_URL + R2_UPLOAD_TOKEN)."""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from mltedit.config import S  # noqa: E402

ap = argparse.ArgumentParser(description="One demo video per template.")
ap.add_argument("templates", nargs="*", help="templates to build (default: all in the data file)")
ap.add_argument("--out", default=os.path.join(S.data_root, "out", "demos"), help="output folder")
ap.add_argument("--footage", default="", help="footage clip (default: the first of the demo_clips setting that exists)")
ap.add_argument("--data", default=os.path.join(S.data_root, "data", "demos", "templates.json"), help="texts and timing (JSON)")
ap.add_argument("--quality", default="high", choices=("high", "draft"))
ARGS = ap.parse_args() if __name__ == "__main__" else ap.parse_args([])
OUT = os.path.abspath(ARGS.out)
FOOT = ARGS.footage or next((p for p in S.demo_clips.values() if os.path.exists(p)), "")


def load_texts(path):
    """Per-template demo texts and the timing of the demo (data/demos/templates.json)."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)




DATA = load_texts(ARGS.data) if os.path.exists(ARGS.data) else {"steps": {}, "templates": {}}


def build(theme):
    """The project's motion is ON: the template's own transitions between card and footage, animated cards, per-template overlay motion,
    transition sound effects, ducking-ready music and a normalised master."""
    st, tx = DATA["steps"], DATA["templates"][theme]
    home = os.path.join(OUT, "_home", theme)
    os.environ["MLT_EDITOR_HOME"] = home
    import importlib, server
    importlib.reload(server)
    X, t0 = st["transition_s"], tx["footage_start_s"]
    intro, outro = st["intro_s"], st["outro_s"]
    server.new_project(st["width"], st["height"], st["fps"], motion=True)
    server.set_template(theme)
    server.add_card("title", tx["card_title"], tx["card_subtitle"], dur_s=intro)
    server.import_clip(FOOT, "A")
    server.add_clip("A", t0, t0 + st["footage_s"])
    if theme in st["bento_templates"]:                                               # show the bento layout in some of the demos
        server.add_card("bento", st["bento"]["title"], items=st["bento"]["items"], dur_s=outro, push=True)
    else:
        server.add_card("outro", st["outro_title"], tx["card_subtitle"], dur_s=outro, push=True)   # the slow zoom lives in the background only, so the end is never a frozen picture
    server.crossfade(0, X, "auto", sfx="auto")                                       # card -> footage with the template's transition and whoosh
    server.crossfade(1, X, "auto", sfx="auto")                                       # footage -> outro
    s0 = intro + 0.2                                                                 # first overlay: just after the intro card has gone
    server.add_lower_third(tx["lower_third_name"], tx["lower_third_role"], start_s=s0, dur_s=3.6)      # no anim given: the template's motion supplies it
    server.add_image(s0 + 0.4, 3.6, icon=tx["icon"], position="top-right", scale=0.11)
    server.add_callout(tx["label"], [[s0 + 2.4, 0.55, 0.55], [s0 + 5.2, 0.5, 0.52]], start_s=s0 + 2.4, dur_s=2.8, size=1.3)
    server.add_text(tx["caption"], s0 + 4.4, 1.6, position="bottom")
    server.add_audio(0.0, None, asset=tx["music"], volume_db=st["music_db"])
    server.add_audio(s0, asset=tx["sfx_accent"], volume_db=st["sfx_db"])
    server.add_audio(s0 + 2.4, asset=tx["sfx_accent"], volume_db=st["sfx_db"])
    out = os.path.join(OUT, f"{theme}.mp4")
    r = server.export(out, quality=ARGS.quality, overwrite=True, master="loudnorm")
    tl = server.get_timeline()
    r["credits_required"] = tl.get("credits_required", [])
    r["warnings"] = tl.get("warnings", [])
    return r


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for t in (ARGS.templates or list(DATA["templates"])):
        r = build(t)
        print(t, r["duration_s"], f"{r['size_kb']} KB", f"{r['render_s']}s", f"{r.get('loudness_lufs')} LUFS", "credits:", len(r["credits_required"]), "warnings:", r["warnings"], flush=True)
