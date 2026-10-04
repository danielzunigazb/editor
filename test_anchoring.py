#!/usr/bin/env python3
"""Timeline anchoring: an overlay or an audio clip placed on a clip FOLLOWS that clip when earlier clips are cut, trimmed, moved or removed.
Every case checks that the edit still sits on the same frame of the SOURCE file (not 'about the same time'), and one case checks real pixels.
Run: python3 test_anchoring.py"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
HOME = tempfile.mkdtemp(prefix="anc_")
os.environ["MLT_EDITOR_HOME"] = HOME
import server  # noqa: E402
from mltedit import engine as live  # noqa: E402

A, B = os.path.join(HERE, "media", "clip_a.mp4"), os.path.join(HERE, "media", "clip_b.mp4")
MUSIC = os.path.join(HOME, "music.wav")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=10:sample_rate=48000", "-c:a", "pcm_s16le", MUSIC], check=True)
FPS = 25
ok = bad = 0


def check(name, cond, detail=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:350]}]" if not cond and detail else ""))


def err(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except Exception as e:  # noqa: BLE001
        return e


def base():
    """A on 0-4 s, B on 4-8 s (A's source 0-4 s, B's 0-4 s)."""
    server.new_project(640, 360, FPS)
    server.import_clip(A, "A")
    server.import_clip(B, "B")
    server.add_clip("A", 0, 4)
    server.add_clip("B", 0, 4)
    return [e["id"] for e in server.get_timeline()["entries"]]


# the overlay/audio kinds, each placed at TIMELINE 5.0 s = frame 25 of clip B's source (1 s into B)
KINDS = {
    "text": lambda: server.add_text("anchored", 5.0, 1.5),
    "lower_third": lambda: server.add_lower_third("Ana", "Directora", 5.0, 1.5),
    "callout": lambda: server.add_callout("Aqui", [[5.0, 0.3, 0.4], [6.0, 0.6, 0.5]]),
    "pip": lambda: server.add_pip("A", 5.0, 1.5, source_in_s=0.0),
    "image": lambda: server.add_image(5.0, 1.5, icon="star"),
    "subtitles": lambda: server.add_subtitles(cues=[{"start": 5.0, "end": 5.8, "text": "uno"}, {"start": 6.0, "end": 6.8, "text": "dos"}]),
    "audio": lambda: server.add_audio(5.0, 1.5, path=MUSIC),
}


def shown(op_id):
    """(start of the edit on the timeline in seconds, source-B frame under it, layout) or (None, None, layout) if it is not shown."""
    st = server.load()
    server.bind(st)
    m = live.layout(st["ops"])
    idx = next(i for i, o in enumerate(st["ops"]) if o["id"] == op_id)
    starts = [x["start"] for x in list(m["layers"]) + list(m["audios"]) if x["op"] == idx]
    if not starts:
        return None, None, m
    t = min(starts)
    eb = next((e for e in m["entries"] if e["id"] == B_ID), None)
    frame = None if eb is None else round((t - eb["start"]) * FPS + eb["in"] * FPS)
    return t, frame, m


SCEN = {
    "cut A to 3 s": (lambda ids: server.cut_clip(0, 3.0, clip_id=ids[0]), 4.0),
    "trim A's head (1-4 s)": (lambda ids: server.trim_clip(0, 1.0, 4.0, clip_id=ids[0]), 4.0),
    "trim A's tail (0-2 s)": (lambda ids: server.trim_clip(0, 0.0, 2.0, clip_id=ids[0]), 2.0 + 1.0),
    "move B to the front": (lambda ids: server.move_clip(1, 0, clip_id=ids[1]), 1.0),
    "insert a clip before (a new clip moved to the front)": (lambda ids: (server.add_clip("A", 0, 3), server.move_clip(2, 0, clip_id=server.get_timeline()["entries"][-1]["id"])), 3.0 + 4.0 + 1.0),
    "remove the earlier clip": (lambda ids: server.remove_op(op_id=ids[0]), 1.0),
}

for kind, make in KINDS.items():
    for name, (act, expect_start) in SCEN.items():
        ids = base()
        B_ID = ids[1]
        r = make()
        op_id = r["op_id"]
        t0, f0, _ = shown(op_id)
        e = err(act, ids)
        t1, f1, m = shown(op_id)
        good = e is None and t1 is not None and f1 == f0 == FPS and abs(t1 - expect_start) < 1e-6
        check(f"{kind:12} {name}: it stays on frame 25 of B ({t0:g}s -> {t1 if t1 is None else round(t1, 3):g}s)", good, (e, t0, t1, f0, f1, expect_start))
        if kind == "callout" and good:
            L = next(x for x in m["layers"] if x["kind"] == "callout")
            check(f"{kind:12} {name}: the callout's path moved with it", abs(L["path"][0][0] - L["start"]) < 1e-6 and abs(L["path"][-1][0] - (L["start"] + 1.0)) < 1e-6, L["path"])
        if kind == "subtitles" and good:
            st = server.load(); server.bind(st)
            cues = sorted((x for x in live.layout(st["ops"])["layers"] if x["kind"] == "text"), key=lambda x: x["start"])
            check(f"{kind:12} {name}: every cue moved by the same amount", len(cues) == 2 and abs((cues[1]["start"] - cues[0]["start"]) - 1.0) < 1e-6, [c["start"] for c in cues])
        if kind == "audio" and good:
            st = server.load(); server.bind(st)
            a = live.layout(st["ops"])["audios"][0]
            check(f"{kind:12} {name}: the audio clip's frames moved too", a["start_f"] == round(t1 * FPS), (a["start_f"], t1))

# ------------------------------------------------------------------------------------------------ the moment is cut away
ids = base(); B_ID = ids[1]
r = server.add_text("will vanish", 5.0, 1.5)
server.trim_clip(1, 2.0, 4.0, clip_id=ids[1])                 # B now starts at source second 2: the anchored moment (second 1) is gone
tl = server.get_timeline()
check("an overlay whose anchored moment was trimmed away is hidden, with an ANCHOR_LOST warning", not tl["overlays"] and any("ANCHOR_LOST" in w for w in tl["warnings"]), (tl["overlays"], tl["warnings"]))
server.undo()
check("...and undo brings it back", len(server.get_timeline()["overlays"]) == 1)

# ------------------------------------------------------------------------------------------------ anchor="timeline"
ids = base(); B_ID = ids[1]
r = server.add_text("pinned", 5.0, 1.5, anchor="timeline")
op = next(o for o in server.get_timeline()["ops"] if o["id"] == r["op_id"])
server.cut_clip(0, 3.0, clip_id=ids[0])
t, _, _ = shown(r["op_id"])
check("anchor='timeline' keeps the edit at its timeline time whatever happens to the clips", "timeline_f" in op["anchor"] and abs(t - 5.0) < 1e-6, (op["anchor"], t))
e = err(server.add_text, "x", 5.0, 1.0, anchor="sideways")
check("an unknown anchor mode is refused", e is not None and "anchor must be" in str(e), e)
ids = base()
r = server.add_text("on A", 1.0, 1.0)
check("the default is clip anchoring, to the clip on screen at that moment",
      (lambda a: a.get("clip") == ids[0] and a["src_f"] == 25)(next(o for o in server.get_timeline()["ops"] if o["id"] == r["op_id"])["anchor"]))
r = server.add_text("on B", 4.5, 1.0)
anc = next(o for o in server.get_timeline()["ops"] if o["id"] == r["op_id"])["anchor"]
check("a moment in clip B 0.5 s in is source frame 12 or 13 of B (0.5 s * 25 fps)", anc["clip"] == ids[1] and anc["src_f"] in (12, 13), anc)
ov = [o for o in server.get_timeline()["overlays"] if o["op"] == len(server.get_timeline()["ops"]) - 1]
check("get_timeline shows the anchor of every overlay", ov and "anchor" in ov[0] and "t0_f" not in ov[0]["anchor"], ov)

# inside a transition the incoming clip wins
ids = base()
server.crossfade(0, 1.0)
tl = server.get_timeline()
t_mid = tl["entries"][1]["start_s"] + 0.4                       # inside the 1 s overlap of A's end and B's start
r = server.add_text("in the fade", t_mid, 0.5)
anc = next(o for o in server.get_timeline()["ops"] if o["id"] == r["op_id"])["anchor"]
check("inside a crossfade the edit anchors to the incoming clip", anc["clip"] == ids[1], anc)

# ------------------------------------------------------------------------------------------------ sound effect follows its transition
ids = base(); B_ID = ids[1]
server.crossfade(0, 0.5)
xf = next(o for o in server.get_timeline()["ops"] if o["op"] == "crossfade")
snd = server.add_audio(3.5, 1.0, path=MUSIC)["op_id"]
server.update_op(snd, {"anchor": {"transition": xf["id"], "t0_f": round(3.5 * FPS)}})
server.cut_clip(0, 3.0, clip_id=ids[0])                       # the transition now starts 1 s earlier
t, _, _ = shown(snd)
check("audio anchored to a transition moves with it", t is not None and abs(t - 2.5) < 1e-6, t)
server.remove_op(op_id=xf["id"], cascade=True)
check("removing the transition with cascade removes the sound that followed it", not any(o["op"] == "audio" for o in server.get_timeline()["ops"]))

# ------------------------------------------------------------------------------------------------ removing a clip that others depend on
ids = base()
server.add_clip("A", 0, 4)                                    # a third clip, so there is still picture at 5 s after B goes
r = server.add_text("depends on B", 5.0, 1.0)
e = err(server.remove_op, op_id=ids[1])
check("removing a clip with anchored edits is refused, listing them", e is not None and r["op_id"] in str(e) and "cascade" in str(e), e)
check("...and nothing was removed", server.get_timeline()["op_count"] == 4)
server.remove_op(op_id=ids[1], reanchor="timeline")
tl = server.get_timeline()
t_text = next(o for o in tl["overlays"] if o["kind"] == "text")["start_s"]
check("reanchor='timeline' removes the clip and keeps the overlay at the time it had", abs(t_text - 5.0) < 1e-6 and tl["op_count"] == 3, (t_text, tl["op_count"]))
anc = next(o for o in tl["ops"] if o["op"] == "text")["anchor"]
check("...its anchor is now the timeline", "timeline_f" in anc, anc)
server.undo()
check("one undo brings back the clip and the overlay's clip anchor", server.get_timeline()["op_count"] == 4 and "clip" in next(o for o in server.get_timeline()["ops"] if o["op"] == "text")["anchor"])
server.remove_op(op_id=ids[1], cascade=True)
check("cascade removes the clip and everything that depends on it", server.get_timeline()["op_count"] == 2 and not any(o["op"] == "text" for o in server.get_timeline()["ops"]))
server.undo()
check("one undo brings all of it back", server.get_timeline()["op_count"] == 4)

# cut / crossfade / trim / move reference clips too
ids = base()
server.cut_clip(0, 2.0, clip_id=ids[1])
e = err(server.remove_op, op_id=ids[1], reanchor="timeline")
check("reanchor does not hide that a cut of the clip still needs cascade", e is not None and "cascade" in str(e), e)

# ------------------------------------------------------------------------------------------------ move_op and update_op
ids = base()
r = server.add_text("movable", 1.0, 1.0)
server.move_op(r["op_id"], 5.0)
anc = next(o for o in server.get_timeline()["ops"] if o["id"] == r["op_id"])["anchor"]
check("move_op moves an edit and re-anchors it to the clip at the new time", anc["clip"] == ids[1] and anc["src_f"] == 25, anc)
server.update_op(r["op_id"], {"start": 2.0})
anc = next(o for o in server.get_timeline()["ops"] if o["id"] == r["op_id"])["anchor"]
check("update_op of the start re-anchors too", anc["clip"] == ids[0] and anc["src_f"] == 50, anc)
r2 = server.add_callout("c", [[1.0, 0.3, 0.3], [2.0, 0.5, 0.5]])
server.move_op(r2["op_id"], 5.0)
c = next(o for o in server.get_timeline()["ops"] if o["id"] == r2["op_id"])
check("move_op moves everything timed inside the edit (a callout's path)", abs(c["start"] - 5.0) < 1e-9 and abs(c["path"][0][0] - 5.0) < 1e-9 and abs(c["path"][1][0] - 6.0) < 1e-9, c)
e = err(server.move_op, ids[0], 1.0)
check("move_op refuses what is not an overlay or audio edit", e is not None and "only overlay and audio" in str(e), e)
check("move_op beyond the end of the timeline is refused and changes nothing", err(server.move_op, r["op_id"], 99.0) is not None and next(o for o in server.get_timeline()["ops"] if o["id"] == r["op_id"])["start"] == 2.0)

# crossfade joins specific clips
ids = base()
server.add_clip("A", 0, 2)
server.crossfade(0, 0.5)
e = err(server.move_clip, 1, 2, clip_id=ids[1])
check("moving a clip away from its crossfade partner is rejected with the reason", e is not None and "no longer next to each other" in str(e), e)
check("...and nothing changed", [x["id"] for x in server.get_timeline()["entries"]][:2] == ids)

# ------------------------------------------------------------------------------------------------ real pixels
def region_diff(img_a, img_b):
    (_, _, a), (_, _, b) = img_a, img_b
    n = sum(1 for i in range(0, len(a), 3 * 7) if abs(a[i] - b[i]) + abs(a[i + 1] - b[i + 1]) + abs(a[i + 2] - b[i + 2]) > 30)
    return n


from mltedit.tools import review  # noqa: E402


def frames_at(t):
    return review._frames(server.load(), [t], 1.0)[0]


ids = base()
r = server.add_text("PIXELS PIXELS", 5.0, 1.5, position="center", size=0.12)
server.cut_clip(0, 3.0, clip_id=ids[0])                     # B (and the text) now start 1 s earlier: the text shows 4.0-5.5 s
with_text = frames_at(4.5)
server.remove_op(op_id=r["op_id"])
without = frames_at(4.5)
check("pixels: after cutting the earlier clip the text is on screen at 4.5 s (it would have started at 5.0 s)", region_diff(with_text, without) > 200, region_diff(with_text, without))
ids = base()
r = server.add_text("PIXELS PIXELS", 5.0, 1.5, position="center", size=0.12, anchor="timeline")
server.cut_clip(0, 3.0, clip_id=ids[0])
with_text = frames_at(4.5)
server.remove_op(op_id=r["op_id"])
without = frames_at(4.5)
check("pixels: with anchor='timeline' the text is NOT yet on screen at 4.5 s", region_diff(with_text, without) < 20, region_diff(with_text, without))

# ------------------------------------------------------------------------------------------------ old projects keep their meaning
v1 = {"sources": {"A": {"path": A, "duration_s": 6.0}, "B": {"path": B, "duration_s": 5.0}}, "width": 320, "height": 180, "fps": 25, "theme": {"name": "luxury", "accent": None},
      "ops": [{"op": "add", "src": "A", "in": 0.0, "end": 4.0}, {"op": "add", "src": "B", "in": 0.0, "end": 4.0},
              {"op": "text", "text": "old", "start": 5.0, "dur": 1.0, "pos": "bottom", "size": 0.06, "style": "auto", "color": None, "box": None, "uppercase": None, "ornament": None, "fade": 0.15, "anim": None}]}
json.dump(v1, open(os.path.join(HOME, "project.json"), "w"))
server.cut_clip(0, 3.0)
t = next(o for o in server.get_timeline()["overlays"] if o["kind"] == "text")["start_s"]
check("a project made before anchors keeps its timeline times (migrated as absolute)", abs(t - 5.0) < 1e-6, t)
check("the old project file migrated without an anchor on the old edit", "anchor" not in next(o for o in json.load(open(os.path.join(HOME, "project.json")))["ops"] if o["op"] == "text"))

print(f"\n{ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
