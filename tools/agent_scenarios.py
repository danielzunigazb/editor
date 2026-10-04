#!/usr/bin/env python3
"""Scenarios an LLM agent actually runs into, as sequences of tool calls with assertions (no model involved). Each scenario returns (ok, detail).
A scenario that needs a feature that does not exist yet is marked xfail("P3"): it must FAIL until that phase lands, then it must PASS and the
mark is removed (an unexpected pass is reported as XPASS and fails the run, so a finished feature cannot keep a stale mark).
Usage: tools/agent_scenarios.py [name ...]    Exit code 1 on FAIL or XPASS."""
import os, sys, tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ.setdefault("MLT_EDITOR_HOME", tempfile.mkdtemp(prefix="scen_"))
import server  # noqa: E402

A, B = os.path.join(HERE, "media", "clip_a.mp4"), os.path.join(HERE, "media", "clip_b.mp4")
SCENARIOS, XFAIL = {}, {}


def scenario(xfail=""):
    def deco(fn):
        SCENARIOS[fn.__name__] = fn
        if xfail:
            XFAIL[fn.__name__] = xfail
        return fn
    return deco


def fresh():
    server.new_project(1280, 720, 25)
    server.import_clip(A, "A")
    server.import_clip(B, "B")


def err(fn, *a, **k):
    """The error message of a tool call, or None if it succeeded."""
    try:
        fn(*a, **k)
        return None
    except Exception as e:                                         # noqa: BLE001 (the message is what the agent sees)
        return str(e)


def overlay_start(tl, kind):
    return next((o["start_s"] for o in tl["overlays"] if o["kind"] == kind), None)


@scenario()
def apply_ops_is_all_or_nothing():
    fresh()
    e = err(server.apply_ops, [{"tool": "add_clip", "source": "A", "end_s": 3}, {"tool": "add_text", "text": "x", "start_s": 99, "dur_s": 1}])
    n = server.get_timeline()["op_count"]
    return e is not None and "item 1" in e and n == 0, (e, n)


@scenario()
def undo_restores_previous_timeline():
    fresh()
    server.add_clip("A", 0, 3)
    before = server.get_timeline()["duration_s"]
    server.add_clip("B", 0, 2)
    server.undo()
    return server.get_timeline()["duration_s"] == before, before


@scenario()
def overlay_after_the_end_is_rejected_with_a_hint():
    fresh()
    server.add_clip("A", 0, 3)
    e = err(server.add_text, "late", 10, 1)
    return e is not None and "timeline is only" in e, e


@scenario()
def error_names_the_op_and_the_range():
    fresh()
    e = err(server.add_clip, "A", 0, 99)
    return e is not None and "outside source" in e, e


@scenario()
def a_second_overlay_in_the_same_place_warns():
    server.new_project(1080, 1920, 24)                              # portrait: the zones of a lower third and bottom subtitles meet
    server.import_clip(A, "A")
    server.add_clip("A", 0, 4)
    server.add_lower_third("Señor Muñoz", "Director de Proyecto", 1, 2)
    r = server.add_subtitles(cues=[{"start": 1.2, "end": 2.5, "text": "¿Quién trae el balón?"}])
    return any("overlap" in w for w in r["warnings"]), r["warnings"]


@scenario(xfail="P3")
def overlay_follows_its_clip_when_an_earlier_clip_is_cut():
    """Text placed on clip B (timeline 4-5 s) must still sit on the same moment of B after clip A is shortened by 1 s."""
    fresh()
    server.add_clip("A", 0, 4)
    server.add_clip("B", 0, 4)
    server.add_text("on B", 5.0, 1.0)
    server.cut_clip(0, 3.0)                                        # A loses its last second: B now starts 1 s earlier
    tl = server.get_timeline()
    return abs(overlay_start(tl, "text") - 4.0) < 0.05, overlay_start(tl, "text")


@scenario(xfail="P1")
def op_ids_survive_removing_an_earlier_op():
    """The agent keeps the id of an op it made; removing another op must not change what that id means."""
    fresh()
    server.add_clip("A", 0, 4)
    server.add_text("first", 0.5, 1.0)
    r = server.add_text("second", 2.0, 1.0)
    keep = r["ops"][-1]["id"] if "ops" in r and "id" in r["ops"][-1] else None
    if keep is None:
        return False, "ops have no id"
    server.remove_op(1)
    ops = server.get_timeline()["ops"]
    return any(o.get("id") == keep and o.get("text") == "second" for o in ops), ops


@scenario(xfail="P1")
def a_stale_revision_is_refused():
    fresh()
    server.add_clip("A", 0, 3)
    rev = server.get_timeline().get("revision")
    server.add_text("someone else", 0.5, 1.0)
    e = err(server.add_text, "mine", 1.5, 1.0, expected_revision=rev) if rev is not None else "no revision"
    return e is not None and "REVISION_CONFLICT" in e, e


@scenario(xfail="P6")
def dry_run_changes_nothing_and_reports_the_result():
    fresh()
    server.add_clip("A", 0, 3)
    r = err(server.add_text, "ghost", 0.5, 1.0, dry_run=True)
    n = server.get_timeline()["op_count"]
    return n == 1 and r is None, (r, n)


@scenario(xfail="P6")
def a_retry_with_the_same_request_id_adds_one_edit():
    fresh()
    server.add_clip("A", 0, 3)
    e1 = err(server.add_text, "once", 0.5, 1.0, request_id="r1")
    e2 = err(server.add_text, "once", 0.5, 1.0, request_id="r1")
    n = server.get_timeline()["op_count"]
    return e1 is None and e2 is None and n == 2, (e1, e2, n)


@scenario(xfail="P2")
def errors_carry_a_stable_code():
    fresh()
    e = err(server.add_clip, "A", 0, 99)
    return e is not None and "OUT_OF_RANGE" in e, e


def main(names):
    ok_all = True
    for name in names or SCENARIOS:
        try:
            ok, detail = SCENARIOS[name]()
        except Exception as e:                                       # noqa: BLE001
            ok, detail = False, f"{type(e).__name__}: {e}"
        x = XFAIL.get(name)
        status = ("XPASS" if ok else "XFAIL") if x else ("PASS" if ok else "FAIL")
        ok_all &= status in ("PASS", "XFAIL")
        print(f"{status:5} {name}" + (f" [{x}]" if x else "") + ("" if ok or x else f"  {str(detail)[:300]}"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
