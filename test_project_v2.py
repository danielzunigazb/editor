#!/usr/bin/env python3
"""Project model v2: stable op ids, clip references by id, revisions (REVISION_CONFLICT), undo/redo patches, update_op, the history journal, v1 migration,
and crash safety. Run: python3 test_project_v2.py"""
import copy, json, os, signal, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
HOME = tempfile.mkdtemp(prefix="pv2_")
os.environ["MLT_EDITOR_HOME"] = HOME
import server  # noqa: E402
from mltedit import project as P  # noqa: E402
from mltedit import engine  # noqa: E402

A, B = os.path.join(HERE, "media", "clip_a.mp4"), os.path.join(HERE, "media", "clip_b.mp4")
ok = bad = 0


def check(name, cond, detail=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:300]}]" if not cond and detail else ""))


def err(fn, *a, **k):
    try:
        fn(*a, **k)
        return None
    except Exception as e:  # noqa: BLE001
        return str(e)


def fresh():
    server.new_project(640, 360, 25)
    server.import_clip(A, "A")
    server.import_clip(B, "B")


def ops():
    return server.get_timeline()["ops"]


# ------------------------------------------------------------------------------------------------ ids
fresh()
r = server.add_clip("A", 0, 4)
check("an edit answers with the id of the op it made and the revision", r["op_id"].startswith("op_") and isinstance(r["revision"], int), r)
server.add_clip("B", 0, 3)
server.add_text("uno", 1.0, 1.0)
r = server.add_text("dos", 2.0, 1.0)
keep = r["op_id"]
tl = server.get_timeline()
check("get_timeline lists ids for ops and for entries", all(o.get("id") for o in tl["ops"]) and all(e.get("id") for e in tl["entries"]), tl["entries"])
first_text = next(o["id"] for o in tl["ops"] if o.get("text") == "uno")
server.remove_op(op_id=first_text)
now = {o["id"]: o for o in ops()}
check("an id keeps meaning the same op after an earlier op is removed", keep in now and now[keep]["text"] == "dos" and first_text not in now)
check("remove_op also works by position, as before", err(server.remove_op, index=len(ops()) - 1) is None and keep not in {o["id"] for o in ops()})

# clips are referenced by id
fresh()
server.add_clip("A", 0, 4)
server.add_clip("B", 0, 4)
ids = [e["id"] for e in server.get_timeline()["entries"]]
server.cut_clip(1, 2.0)
cut = next(o for o in ops() if o["op"] == "cut")
check("a cut by position is stored with the id of the clip it cuts", cut["clip"] == ids[1], cut)
server.crossfade(0, 0.5)
xf = next(o for o in ops() if o["op"] == "crossfade")
check("a crossfade is stored with the ids of its two clips", xf["between"] == ids, xf)
e = err(server.remove_op, op_id=ids[0])
check("removing a clip that a crossfade refers to is refused, naming the crossfade", e and "depend on" in e and xf["id"] in e, e)
tl = server.get_timeline()
check("...and nothing was changed", len(tl["ops"]) == 4 and [x["id"] for x in tl["entries"]] == ids)

fresh()
server.add_clip("A", 0, 3)
server.add_clip("B", 0, 4)
server.add_clip("A", 3, 5)
ids = [e["id"] for e in server.get_timeline()["entries"]]
server.cut_clip(0, 1.0, clip_id=ids[2])                      # clip_id wins over the position
tl = server.get_timeline()
check("cut_clip by clip_id cuts that clip", abs(tl["entries"][2]["end_s"] - tl["entries"][2]["start_s"] - 1.0) < 1e-6, tl["entries"])
e = err(server.crossfade, 0, 0.5, first_clip_id=ids[1])
tl = server.get_timeline()
x = next(o for o in tl["ops"] if o["op"] == "crossfade")
check("crossfade by first_clip_id pairs it with the clip that follows", e is None and x["between"] == [ids[1], ids[2]], (e, x))

# ------------------------------------------------------------------------------------------------ revisions
fresh()
server.add_clip("A", 0, 3)
rev = server.get_timeline()["revision"]
server.add_text("someone else", 0.5, 1.0)
before = server.get_timeline()
e = err(server.add_text, "mine", 1.5, 1.0, expected_revision=rev)
after = server.get_timeline()
check("an edit made against a stale revision is refused with REVISION_CONFLICT", e and "REVISION_CONFLICT" in e and str(rev) in e, e)
check("...and the project did not change at all", before["revision"] == after["revision"] and before["ops"] == after["ops"])
e = err(server.add_text, "mine", 1.5, 1.0, expected_revision=after["revision"])
check("with the current revision the same edit is accepted", e is None, e)
check("every tool that changes the project takes expected_revision", all("expected_revision" in __import__("inspect").signature(getattr(server, t)).parameters for t in
      ("add_clip", "cut_clip", "crossfade", "set_fades", "add_pip", "add_text", "add_subtitles", "add_graphic", "add_lower_third", "add_image", "add_callout", "add_audio",
       "add_card", "animate", "apply_ops", "set_template", "remove_op", "update_op", "undo", "redo", "new_project", "import_clip")))
r0 = server.get_timeline()["revision"]
e = err(server.apply_ops, [{"tool": "add_text", "text": "x", "start_s": 0.5, "dur_s": 1}], expected_revision=r0 - 1)
check("apply_ops honours expected_revision too", e and "REVISION_CONFLICT" in e and server.get_timeline()["revision"] == r0, e)
rev_before = server.get_timeline()["revision"]
server.new_project(640, 360, 25)
check("new_project never takes the revision back (a stale agent still notices the reset)", server.get_timeline()["revision"] > rev_before)

# ------------------------------------------------------------------------------------------------ undo / redo / update_op
fresh()
server.add_clip("A", 0, 4)
server.add_text("t1", 1.0, 1.0)
r = server.add_text("t2", 2.0, 1.0)
t2 = r["op_id"]
u = server.undo()
check("undo takes back the last edit (and reports it)", u["removed"]["id"] == t2 and len(ops()) == 2, u.get("removed"))
rd = server.redo()
check("redo puts it back, with the same id, in the same place", ops()[-1]["id"] == t2 and rd["op_count"] == 3)
server.undo()
server.add_text("t3", 2.0, 1.0)
check("a new edit ends the redo branch", err(server.redo) is not None and "nothing to redo" in err(server.redo))

t1 = next(o["id"] for o in ops() if o.get("text") == "t1")
server.remove_op(op_id=t1)
check("removing an op is undoable", err(server.undo) is None and [o.get("text") for o in ops()][1] == "t1", [o.get("text") for o in ops()])

u = server.update_op(t1, {"start": 2.5, "dur": 0.5})
o = next(o for o in ops() if o["id"] == t1)
check("update_op changes fields of an op by id", o["start"] == 2.5 and o["dur"] == 0.5, o)
e = err(server.update_op, t1, {"dur": 0})
check("update_op validates the whole timeline and changes nothing when it is not valid", e and next(o for o in ops() if o["id"] == t1)["dur"] == 0.5, e)
check("update_op refuses to change the kind or the id", err(server.update_op, t1, {"op": "cut"}) is not None and err(server.update_op, t1, {"id": "x"}) is not None)
server.undo()
check("an update is undoable", next(o for o in ops() if o["id"] == t1)["start"] == 1.0)
check("update_op deletes a field with null", err(server.update_op, t1, {"color": None}) is None)

fresh()
server.add_clip("A", 0, 4)
server.add_text("anim", 0.5, 2.0)
tid = ops()[-1]["id"]
server.animate(op_id=tid, anim={"in": "pop"})
check("animate works by op_id", next(o for o in ops() if o["id"] == tid).get("anim", {}).get("in") == "pop")
server.undo()
check("...and its undo removes the animation", "anim" not in next(o for o in ops() if o["id"] == tid) or not next(o for o in ops() if o["id"] == tid)["anim"])

fresh()
server.add_clip("A", 0, 4)
n = len(ops())
server.apply_ops([{"tool": "add_text", "text": "a", "start_s": 0.5, "dur_s": 1}, {"tool": "add_text", "text": "b", "start_s": 1.5, "dur_s": 1}, {"tool": "add_graphic", "kind": "vignette", "start_s": 0, "dur_s": 3}])
check("one undo takes back a whole apply_ops batch", len(ops()) == n + 3 and err(server.undo) is None and len(ops()) == n)
server.redo()
check("...and one redo puts it all back", len(ops()) == n + 3)

server.set_template("minimal")
server.undo()
check("a template change is undoable", server.get_timeline()["template"] == "luxury")
server.redo()
check("...and redoable", server.get_timeline()["template"] == "minimal")
check("undo with nothing to undo is an error", (lambda: (server.new_project(320, 180, 25), err(server.undo))[1])() is not None)

# ------------------------------------------------------------------------------------------------ journal
fresh()
server.add_clip("A", 0, 3)
server.add_text("j", 0.5, 1.0)
rows = P.read_journal(os.path.join(HOME, "history.jsonl"))
check("every saved change writes one journal line with its revision, kind and tool", rows and rows[-1]["kind"] == "commit" and rows[-1]["tool"] == "add_text" and rows[-1]["rev"] == server.get_timeline()["revision"], rows[-2:])
revs = [r_["rev"] for r_ in rows]
check("journal revisions only go up", revs == sorted(revs) and len(set(revs)) == len(revs))

# ------------------------------------------------------------------------------------------------ v1 migration
v1 = {"sources": {"A": {"path": A, "duration_s": 6.0}, "B": {"path": B, "duration_s": 5.0}}, "width": 320, "height": 180, "fps": 25, "theme": {"name": "luxury", "accent": None},
      "ops": [{"op": "add", "src": "A", "in": 0.0, "end": 4.0}, {"op": "add", "src": "B", "in": 0.0}, {"op": "cut", "clip": 1, "at": 3.0},
              {"op": "crossfade", "between": [0, 1], "dur": 0.5}, {"op": "text", "text": "hi", "start": 1.0, "dur": 1.0, "pos": "bottom", "size": 0.06, "style": "auto", "color": None,
               "box": None, "uppercase": None, "ornament": None, "fade": 0.15, "anim": None}]}
m1, m2 = P.migrate(copy.deepcopy(v1)), P.migrate(copy.deepcopy(v1))
check("migration is deterministic: the same v1 file always gets the same ids", [o["id"] for o in m1["ops"]] == [o["id"] for o in m2["ops"]] and len({o["id"] for o in m1["ops"]}) == 5)
check("migration is idempotent", P.migrate(copy.deepcopy(m1))["ops"] == m1["ops"])
check("migration turns entry positions into clip ids", m1["ops"][2]["clip"] == m1["ops"][1]["id"] and m1["ops"][3]["between"] == [m1["ops"][0]["id"], m1["ops"][1]["id"]], m1["ops"][2:4])
engine.CLIPS, engine.CLIP_LEN, engine.W, engine.H, engine.FPS = {"A": A, "B": B}, {"A": 6.0, "B": 5.0}, 320, 180, 25


def shape(m):
    return {k: ([{kk: vv for kk, vv in e.items() if kk != "id"} for e in v] if k == "entries" else v) for k, v in m.items() if k not in ("layers",)} | {"layers": [{k: v for k, v in L.items()} for L in m["layers"]]}


check("a v1 project (positions) and its migrated form (ids) lay out identically", shape(engine.layout(v1["ops"])) == shape(engine.layout(m1["ops"])))
path = os.path.join(HOME, "project.json")
json.dump(v1, open(path, "w"))
tl = server.get_timeline()
check("a v1 file on disk is read, migrated and answers with ids and a revision", tl["op_count"] == 5 and tl["entries"][0]["id"] == m1["ops"][0]["id"] and "revision" in tl, tl["entries"])
server.add_text("after migration", 1.5, 1.0)
saved = json.load(open(path))
check("the next save writes schema 2", saved["schema_version"] == 2 and all("id" in o for o in saved["ops"]))

# ------------------------------------------------------------------------------------------------ crash safety
home2 = tempfile.mkdtemp(prefix="pv2_crash_")
code = f'''
import os, sys, signal
sys.path.insert(0, {HERE!r})
os.environ["MLT_EDITOR_HOME"] = {home2!r}
import server
server.new_project(640, 360, 25)
server.import_clip({A!r}, "A")
server.add_clip("A", 0, 4)
real = os.replace
def die(src, dst):
    os.kill(os.getpid(), signal.SIGKILL)            # the process dies after the new file is written but before it replaces the old one
os.replace = die
server.apply_ops([{{"tool": "add_text", "text": str(i), "start_s": 0.1 * i, "dur_s": 0.5}} for i in range(1, 30)])
'''
r = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=300)
check("the crash test process really was killed", r.returncode == -signal.SIGKILL, r.returncode)
pj = os.path.join(home2, "project.json")
st = json.load(open(pj))
check("after kill -9 in the middle of apply_ops the project file is the previous revision, whole", len(st["ops"]) == 1 and st["ops"][0]["op"] == "add", [o["op"] for o in st["ops"]])
left = [f for f in os.listdir(home2) if f.startswith("project.json.") and f.endswith(".tmp")]
P.clean_stale_tmp(pj)
check("the half-written temp file is left by the crash and cleaned up (its writer is dead)", left and not [f for f in os.listdir(home2) if f.endswith(".tmp")], left)
check("the project still loads and edits after the crash", P.load(pj, server.DEFAULT)["revision"] == st["revision"])

print(f"\n{ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
