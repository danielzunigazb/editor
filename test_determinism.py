#!/usr/bin/env python3
"""Determinism and robustness of the edit pipeline.

1. Random sequences of edit-tool calls (valid and absurd values: nan, inf, huge, negative, empty, emoji): every call either succeeds or raises an EditError
   with a catalogued code - never another exception - and after every success the layout obeys its invariants.
2. layout() is idempotent and does not depend on the process (PYTHONHASHSEED): the same project lays out to the same bytes in three processes.
3. The defaults an op plugin declares are the defaults its layout code uses (laying out with normalisation switched off gives the same timeline).
4. Malformed raw ops (missing fields, wrong types) are INVALID_ARGUMENT errors, not KeyError/TypeError.
Run: python3 test_determinism.py   (DETERMINISM_EXAMPLES=n sets the number of random sequences, default 60)"""
import copy, json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["MLT_EDITOR_HOME"] = tempfile.mkdtemp(prefix="det_")
import server  # noqa: E402
from hypothesis import HealthCheck, given, settings, strategies as hs  # noqa: E402
from mltedit import engine, errors, ops as O  # noqa: E402
from mltedit import project as P  # noqa: E402

A, B = os.path.join(HERE, "media", "clip_a.mp4"), os.path.join(HERE, "media", "clip_b.mp4")
ok = bad = 0
CALLS = {"n": 0, "accepted": 0, "refused": 0, "codes": {}}


def check(name, cond, detail=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:400]}]" if not cond and detail else ""))


# ------------------------------------------------------------------------------------------------ a project to start every example from
server.new_project(640, 360, 25)
server.import_clip(A, "A")
server.import_clip(B, "B")
BASE = json.load(open(os.path.join(os.environ["MLT_EDITOR_HOME"], "project.json")))


def reset():
    st = copy.deepcopy(BASE)
    st["ops"], st["undo"], st["redo"] = [], [], []
    with server.locked():
        P.save(server.PROJECT, st, {"kind": "test_reset"})


# ------------------------------------------------------------------------------------------------ strategies
GOOD = hs.floats(min_value=0, max_value=5, allow_nan=False).map(lambda x: round(x, 2))
nums = hs.one_of(*[GOOD] * 5, hs.floats(min_value=-5, max_value=12, allow_nan=False), hs.sampled_from([0.0, -0.0, 1e-9, 0.04, 1e9, -1e9, float("nan"), float("inf"), float("-inf")]), hs.integers(-3, 20))
texts = hs.one_of(hs.sampled_from(["Hola", "Ana", "Director", "Texto de prueba"]), hs.text(max_size=40), hs.sampled_from(["", " ", "Hola", "¿Quién trae el balón?", "x" * 300, "a\nb\nc\nd\ne\nf", "😀", "漢字", "\x00"]))
fracs = hs.one_of(hs.floats(min_value=0.05, max_value=0.95, allow_nan=False), hs.floats(min_value=-0.5, max_value=1.5, allow_nan=False), hs.sampled_from([float("nan"), 0.0, 1.0]))
srcs = hs.sampled_from(["A", "B", "A", "B", "Z", ""])
idx = hs.integers(-1, 3)


def call(tool, **kw):
    return hs.fixed_dictionaries(kw).map(lambda d: (tool, d))


CALL = hs.one_of(
    call("add_clip", source=srcs, start_s=nums, end_s=hs.one_of(hs.none(), nums)),
    call("cut_clip", index=idx, at_s=nums),
    call("crossfade", first_index=idx, dur_s=nums, style=hs.sampled_from(["", "dissolve", "wipe-right", "iris-out", "slide-left", "auto", "nope"])),
    call("set_fades", fade_in_s=nums, fade_out_s=nums),
    call("add_pip", source=srcs, start_s=nums, dur_s=nums, position=hs.sampled_from(["top-right", "bottom-left", "middle"]), scale=fracs, opacity=fracs),
    call("add_text", text=texts, start_s=nums, dur_s=nums, position=hs.sampled_from(["bottom", "center", "top", "left"]), size=hs.one_of(fracs, hs.just(0.06))),
    call("add_graphic", kind=hs.sampled_from(["frame", "letterbox", "vignette", "lower_third", "sparkle"]), start_s=nums, dur_s=nums, amount=hs.one_of(hs.none(), fracs)),
    call("add_lower_third", title=texts, subtitle=texts, start_s=nums, dur_s=nums, align=hs.sampled_from(["left", "right", "up"])),
    call("add_callout", title=texts, track=hs.lists(hs.tuples(nums, fracs, fracs).map(list), max_size=4), start_s=hs.one_of(hs.none(), nums), dur_s=hs.one_of(hs.none(), nums), side=hs.sampled_from(["auto", "ne", "zz"])),
    call("add_subtitles", cues=hs.lists(hs.fixed_dictionaries({"start": nums, "end": nums, "text": texts}), max_size=3), offset_s=nums),
    call("trim_clip", index=idx, in_s=nums, end_s=nums),
    call("move_clip", index=idx, to_index=idx),
    call("remove_op", index=idx, cascade=hs.booleans(), reanchor=hs.sampled_from(["", "timeline", "clip"])),
    call("move_op", pick=hs.integers(0, 40), start_s=nums),
    call("update_op", pick=hs.integers(0, 40), patch=hs.fixed_dictionaries({"start": nums}) | hs.fixed_dictionaries({"dur": nums}) | hs.just({"anim": None})),
    call("undo"), call("redo"),
)


# ------------------------------------------------------------------------------------------------ invariants of a layout
def invariants(m):
    msgs = []
    tracks = {}
    for L in m["layers"]:
        tracks.setdefault(L["track"], []).append(L)
        if not (L["start"] >= -1e-9 and L["start"] < m["total"] + 1e-6 and L["dur"] > 0):
            msgs.append(f"layer {L['kind']} outside the timeline: {L['start']}+{L['dur']} of {m['total']}")
    if sorted(tracks) != list(range(1, len(tracks) + 1)):
        msgs.append(f"tracks not contiguous: {sorted(tracks)}")
    for t, ls in tracks.items():
        ls.sort(key=lambda L: L["start"])
        for a, b in zip(ls, ls[1:]):
            if a["start"] + a["dur"] > b["start"] + 1e-6:
                msgs.append(f"overlap on track {t}")
    for e in m["entries"]:
        if e["dur_f"] < 1:
            msgs.append("entry shorter than a frame")
    if m["entries"]:
        end = max(e["start_f"] + e["dur_f"] for e in m["entries"])
        if end != m["total_f"]:
            msgs.append(f"total_f {m['total_f']} != end of entries {end}")
    for a in m["audios"]:
        if a["n_f"] < 1 or a["start_f"] < 0:
            msgs.append("audio clip with no frames")
    return msgs


def canon(m):
    return json.dumps(m, sort_keys=True, default=str)


@settings(max_examples=int(os.environ.get("DETERMINISM_EXAMPLES", "80")), deadline=None, suppress_health_check=list(HealthCheck), database=None)
@given(hs.lists(CALL, min_size=3, max_size=14))
def random_sessions(calls):
    reset()
    for tool, kw in [("add_clip", {"source": "A", "start_s": 0.0, "end_s": 4.0}), ("add_clip", {"source": "B", "start_s": 0.0, "end_s": 3.0})] + calls:
        CALLS["n"] += 1
        if "pick" in kw:                                     # an existing op, chosen by position in the current list
            kw = dict(kw)
            ids_now = [o["id"] for o in server.load()["ops"]] or ["op_none"]
            kw["op_id"] = ids_now[kw.pop("pick") % len(ids_now)]
        try:
            getattr(server, tool)(**kw)
            CALLS["accepted"] += 1
        except errors.EditError as e:
            CALLS["refused"] += 1
            CALLS["codes"][e.code] = CALLS["codes"].get(e.code, 0) + 1
            assert e.code in errors.CODES and e.code != "INTERNAL", f"{tool}{kw}: {e}"
            assert '{"code":' in str(e), "no parseable trailer"
        st = server.load()
        server.bind(st)
        m1, m2 = engine.layout(st["ops"]), engine.layout(copy.deepcopy(st["ops"]))
        assert canon(m1) == canon(m2), f"layout not idempotent after {tool}"
        bad_ = invariants(m1)
        assert not bad_, f"{tool}{kw}: {bad_}"
        assert len({o["id"] for o in st["ops"]}) == len(st["ops"]), "duplicate op ids"


try:
    random_sessions()
    check(f"random edit sessions: {CALLS['n']} tool calls ({CALLS['accepted']} accepted, {CALLS['refused']} refused), no exception but EditError, layout invariants hold", CALLS["n"] >= 500, CALLS)
except AssertionError as e:
    check("random edit sessions", False, e)
except Exception as e:  # noqa: BLE001
    check("random edit sessions (a non-EditError escaped)", False, f"{type(e).__name__}: {e}")
print("refusals by code:", dict(sorted(CALLS["codes"].items())))
check("the refusals use several distinct codes (the catalogue is in use)", len(CALLS["codes"]) >= 4, CALLS["codes"])

# ------------------------------------------------------------------------------------------------ same bytes in other processes
child = f'''
import hashlib, json, os, sys, tempfile
sys.path.insert(0, {HERE!r})
os.environ["MLT_EDITOR_HOME"] = tempfile.mkdtemp()
import server
from mltedit import engine
server.new_project(640, 360, 25)
server.import_clip({A!r}, "A"); server.import_clip({B!r}, "B")
server.apply_ops([{{"tool": "add_clip", "source": "A", "end_s": 3}}, {{"tool": "add_clip", "source": "B"}}, {{"tool": "crossfade", "first_index": 0, "dur_s": 0.5, "style": "iris-out"}},
  {{"tool": "add_text", "text": "Hola", "start_s": 0.5, "dur_s": 2}}, {{"tool": "add_lower_third", "title": "Ana", "subtitle": "Directora", "start_s": 1, "dur_s": 2}},
  {{"tool": "add_callout", "title": "Aqui", "track": [[1, 0.3, 0.4], [3, 0.6, 0.5]]}}, {{"tool": "add_graphic", "kind": "frame", "start_s": 0, "dur_s": 4}}])
st = server.load(); server.bind(st)
m = engine.layout(st["ops"])
m["entries"] = [{{k: v for k, v in e.items() if k != "id"}} for e in m["entries"]]       # ids are random per process; everything else must be identical
for it in list(m["layers"]) + list(m["audios"]):
    if isinstance(it.get("anchor"), dict) and "clip" in it["anchor"]:
        it["anchor"] = {{**it["anchor"], "clip": "c"}}
strip = lambda o: {{k: v for k, v in o.items() if k != "id"}}
ops = [strip(o) if o.get("op") != "cut" else o for o in st["ops"]]
for o in ops:
    if "between" in o: o["between"] = ["c", "c"]
    if isinstance(o.get("anchor"), dict) and "clip" in o["anchor"]: o["anchor"] = {{**o["anchor"], "clip": "c"}}
print(json.dumps({{"layout": hashlib.sha1(json.dumps(m, sort_keys=True, default=str).encode()).hexdigest(), "ops": hashlib.sha1(json.dumps(ops, sort_keys=True).encode()).hexdigest()}}))
'''
outs = []
for seed in ("0", "1", "4242"):
    r = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True, env={**os.environ, "PYTHONHASHSEED": seed}, timeout=300)
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("{")]
    outs.append(line[-1] if line else r.stderr[-300:])
check("the same project lays out to the same bytes in three processes with different PYTHONHASHSEED", len(set(outs)) == 1 and outs[0].startswith("{"), outs)

st = server.load()
h1 = P.layout_hash(st)
check("layout_hash is stable and changes with the ops", P.layout_hash(copy.deepcopy(st)) == h1 and (P.layout_hash({**st, "ops": st["ops"][:-1]}) != h1 if st["ops"] else True))

# ------------------------------------------------------------------------------------------------ declared defaults = defaults the layout uses
server.new_project(640, 360, 25)
server.import_clip(A, "A"); server.import_clip(B, "B")
server.apply_ops([{"tool": "add_clip", "source": "A", "end_s": 4}, {"tool": "add_clip", "source": "B", "end_s": 3}, {"tool": "crossfade", "first_index": 0, "dur_s": 0.5},
                  {"tool": "set_fades", "fade_in_s": 0.0, "fade_out_s": 0.0}, {"tool": "add_pip", "source": "B", "start_s": 1, "dur_s": 1},
                  {"tool": "add_text", "text": "t", "start_s": 0.5, "dur_s": 1}, {"tool": "add_subtitles", "cues": [{"start": 1, "end": 2, "text": "s"}]},
                  {"tool": "add_graphic", "kind": "vignette", "start_s": 0, "dur_s": 3}, {"tool": "add_lower_third", "title": "T", "start_s": 1, "dur_s": 2},
                  {"tool": "add_callout", "title": "C", "track": [[1, 0.5, 0.5]]}])
st = server.load(); server.bind(st)
stripped = []
for o in st["ops"]:
    plug = O.get_op(o["op"])
    d = {**plug.defaults, **({"style": "dissolve"} if o["op"] == "crossfade" else {})}
    stripped.append({k: v for k, v in o.items() if not (k in d and d[k] == v)})
real = engine.layout(st["ops"])
orig = O.Op.normalize
O.Op.normalize = lambda self, o, ctx=None: o                      # no declared defaults: the layout code's own defaults must give the same timeline
try:
    without = engine.layout(stripped)
finally:
    O.Op.normalize = orig
drop = lambda m: json.dumps({k: v for k, v in m.items()}, sort_keys=True, default=str)
check("an op stored without its default fields lays out exactly like the normalised one (declared defaults == code defaults)", drop(real) == drop(without))
check("every stored op carries its static defaults", all(all(k in o for k in O.get_op(o["op"]).defaults) for o in st["ops"] if o["op"] != "crossfade"))

# ------------------------------------------------------------------------------------------------ malformed raw ops
RAW = [{"op": "add"}, {"op": "add", "src": "A", "in": "x"}, {"op": "cut", "clip": 0}, {"op": "cut", "at": 1}, {"op": "crossfade"}, {"op": "crossfade", "between": [0]},
       {"op": "text", "text": 5, "start": 0, "dur": 1}, {"op": "text"}, {"op": "pip", "src": "A"}, {"op": "image"}, {"op": "callout", "title": "x", "path": "no", "start": 0, "dur": 1},
       {"op": "audio", "path": 3}, {"op": "subtitles", "cues": [1]}, {"op": "graphic", "kind": None, "start": 0, "dur": 1}, {"op": "lower_third", "start": 0, "dur": 1}, {"op": 7}, {}, {"op": None}]
engine.CLIPS, engine.CLIP_LEN, engine.W, engine.H, engine.FPS = {"A": A, "B": B}, {"A": 6.0, "B": 5.0}, 320, 180, 25
leaks = []
for o in RAW:
    try:
        engine.layout([{"op": "add", "src": "A"}, o])
        leaks.append((o, "accepted"))
    except errors.EditError as e:
        if e.code == "INTERNAL":
            leaks.append((o, e.code))
    except Exception as e:  # noqa: BLE001
        leaks.append((o, f"{type(e).__name__}: {e}"))
check("malformed raw ops are EditErrors (never KeyError, TypeError...)", not leaks, leaks[:3])
try:
    engine.layout([{"op": "cut", "clip": 0, "at": 1}])
except errors.EditError as e:
    check("an error carries code, message and a parseable trailer", e.code == "UNKNOWN_OP" and json.loads(str(e).splitlines()[-1])["code"] == "UNKNOWN_OP", str(e))

# ------------------------------------------------------------------------------------------------ files the project depends on
home = tempfile.mkdtemp(prefix="det_files_")
img = os.path.join(home, "pic.png")
from PIL import Image  # noqa: E402
Image.new("RGBA", (200, 100), (10, 200, 10, 255)).save(img)
server.new_project(640, 360, 25)
server.import_clip(A, "A")
server.add_clip("A", 0, 4)
server.add_image(0.5, 1.0, path=img)
op = [o for o in server.load()["ops"] if o["op"] == "image"][0]
check("an image edit stores its aspect and file signature when it is made", abs(op["aspect"] - 2.0) < 1e-9 and len(op["file_sig"]) == 2, op)
st = server.load(); server.bind(st)
os.remove(img)
try:
    m = engine.layout(st["ops"])
    check("laying out does not read the image again (it still works with the file gone)", m["total"] > 0)
except Exception as e:  # noqa: BLE001
    check("laying out does not read the image again", False, e)
v = server.verify_sources()
check("verify_sources reports the missing file with its code", not v["ok"] and v["problems"][0]["code"] == "SOURCE_MISSING", v)
e = None
try:
    server.get_still(1.0)
except errors.EditError as ex:
    e = ex
check("a render refuses when a file the project needs is missing (SOURCE_MISSING)", e is not None and e.code == "SOURCE_MISSING", e)
Image.new("RGBA", (50, 50), (200, 10, 10, 255)).save(img)
v = server.verify_sources()
check("a file that changed is reported as SOURCE_CHANGED", v["problems"] and v["problems"][0]["code"] == "SOURCE_CHANGED", v)
check("...and get_timeline warns about it", any("changed on disk" in w for w in server.get_timeline()["warnings"]))

print(f"\n{ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
