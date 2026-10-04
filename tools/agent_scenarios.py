#!/usr/bin/env python3
"""Scenarios an LLM agent actually runs into, as sequences of tool calls with assertions (no model involved). Each scenario returns (ok, detail).
A scenario that needs a feature that does not exist yet is marked xfail("P3"): it must FAIL until that phase lands, then it must PASS and the
mark is removed (an unexpected pass is reported as XPASS and fails the run, so a finished feature cannot keep a stale mark).
Usage: tools/agent_scenarios.py [name ...]    Exit code 1 on FAIL or XPASS."""
import json, os, shutil, sys, tempfile, time

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


@scenario()
def overlay_follows_its_clip_when_an_earlier_clip_is_cut():
    """Text placed on clip B (timeline 4-5 s) must still sit on the same moment of B after clip A is shortened by 1 s."""
    fresh()
    server.add_clip("A", 0, 4)
    server.add_clip("B", 0, 4)
    server.add_text("on B", 5.0, 1.0)
    server.cut_clip(0, 3.0)                                        # A loses its last second: B now starts 1 s earlier
    tl = server.get_timeline()
    return abs(overlay_start(tl, "text") - 4.0) < 0.05, overlay_start(tl, "text")


@scenario()
def op_ids_survive_removing_an_earlier_op():
    """The agent keeps the id of an op it made; removing another op must not change what that id means."""
    fresh()
    server.add_clip("A", 0, 4)
    server.add_text("first", 0.5, 1.0)
    r = server.add_text("second", 2.0, 1.0)
    keep = r.get("op_id")
    if keep is None:
        return False, "the edit response has no op_id"
    server.remove_op(1)
    ops = server.get_timeline()["ops"]
    return any(o.get("id") == keep and o.get("text") == "second" for o in ops), ops


@scenario()
def a_stale_revision_is_refused():
    fresh()
    server.add_clip("A", 0, 3)
    rev = server.get_timeline().get("revision")
    server.add_text("someone else", 0.5, 1.0)
    e = err(server.add_text, "mine", 1.5, 1.0, expected_revision=rev) if rev is not None else "no revision"
    return e is not None and "REVISION_CONFLICT" in e, e


@scenario()
def dry_run_changes_nothing_and_reports_the_result():
    fresh()
    server.add_clip("A", 0, 3)
    r = err(server.add_text, "ghost", 0.5, 1.0, dry_run=True)
    n = server.get_timeline()["op_count"]
    return n == 1 and r is None, (r, n)


@scenario()
def a_retry_with_the_same_request_id_adds_one_edit():
    fresh()
    server.add_clip("A", 0, 3)
    e1 = err(server.add_text, "once", 0.5, 1.0, request_id="r1")
    e2 = err(server.add_text, "once", 0.5, 1.0, request_id="r1")
    n = server.get_timeline()["op_count"]
    return e1 is None and e2 is None and n == 2, (e1, e2, n)


@scenario()
def errors_carry_a_stable_code():
    fresh()
    e = err(server.add_clip, "A", 0, 99)
    return e is not None and "OUT_OF_RANGE" in e, e


def ids_of(kind):
    return [o["id"] for o in server.get_timeline()["ops"] if o["op"] == kind]


def two_clips_with_text(start=5.0):
    fresh()
    server.add_clip("A", 0, 4)
    server.add_clip("B", 0, 4)
    return server.add_text("on B", start, 1.0)["op_id"]


@scenario()
def dry_run_diff_shows_what_would_move():
    tid = two_clips_with_text()
    rev = server.get_timeline()["revision"]
    r = server.cut_clip(0, 3.0, dry_run=True)
    moved = [m["id"] for m in r.get("diff", {}).get("moved", [])]
    tl = server.get_timeline()
    return tid in moved and tl["revision"] == rev and tl["duration_s"] == 8.0 and r["duration_s"] == 7.0, r


@scenario()
def dry_run_of_a_bad_edit_still_fails_with_its_code():
    fresh()
    server.add_clip("A", 0, 3)
    e = err(server.add_text, "x", 99, 1, dry_run=True)
    return e is not None and "TIMELINE_CONFLICT" in str(e), e


@scenario()
def a_request_id_reused_for_a_different_edit_is_refused():
    fresh()
    server.add_clip("A", 0, 3)
    server.add_text("one", 0.5, 1.0, request_id="r9")
    e = err(server.add_text, "two", 1.5, 1.0, request_id="r9")
    ok_ = e is not None and "already used by a different call" in str(e)
    try:
        trailer = json.loads(str(e).splitlines()[-1])
    except ValueError:
        trailer = {}
    return ok_ and trailer.get("code") == "INVALID_ARGUMENT" and server.get_timeline()["op_count"] == 2, e


@scenario()
def a_replayed_edit_names_the_op_it_made_and_leaves_the_revision_alone():
    fresh()
    server.add_clip("A", 0, 3)
    first = server.add_text("once", 0.5, 1.0, request_id="r1")
    rev = server.get_timeline()["revision"]
    again = server.add_text("once", 0.5, 1.0, request_id="r1")
    return again.get("replayed") and again["op_ids"] == [first["op_id"]] and server.get_timeline()["revision"] == rev, again


@scenario()
def after_a_revision_conflict_the_agent_reads_and_retries():
    fresh()
    server.add_clip("A", 0, 3)
    rev = server.get_timeline()["revision"]
    server.add_text("other agent", 0.5, 1.0)
    e = err(server.add_text, "mine", 1.5, 1.0, expected_revision=rev)
    rev2 = server.get_timeline()["revision"]
    e2 = err(server.add_text, "mine", 1.5, 1.0, expected_revision=rev2)
    return e is not None and "REVISION_CONFLICT" in str(e) and e2 is None, (e, e2)


@scenario()
def one_undo_takes_back_a_whole_batch():
    fresh()
    server.add_clip("A", 0, 3)
    server.apply_ops([{"tool": "add_text", "text": "a", "start_s": 0.5, "dur_s": 1}, {"tool": "add_text", "text": "b", "start_s": 1.5, "dur_s": 1}])
    server.undo()
    return server.get_timeline()["op_count"] == 1, server.get_timeline()["op_count"]


@scenario()
def query_finds_the_id_and_update_op_changes_it():
    fresh()
    server.add_clip("A", 0, 4)
    server.add_text("hello", 1.0, 1.0)
    found = server.query(at_s=1.5)["overlays"]
    if not found:
        return False, found
    server.update_op(found[0]["op_id"], {"text": "changed"})
    return next(o for o in server.get_timeline()["ops"] if o["id"] == found[0]["op_id"])["text"] == "changed", found


@scenario()
def describe_project_stays_short_for_a_big_project():
    fresh()
    server.add_clip("A", 0, 6)
    server.apply_ops([{"tool": "add_text", "text": f"t{i}", "start_s": 0.1 * i, "dur_s": 0.1} for i in range(1, 40)])
    short, full = server.describe_project()["text"], server.describe_project(budget="full")["text"]
    return len(short.splitlines()) <= 14 and len(full.splitlines()) > len(short.splitlines()) and "more" in short or "+" in short, (len(short.splitlines()), len(full.splitlines()))


@scenario()
def query_confirms_the_overlay_followed_the_cut():
    two_clips_with_text(5.0)
    server.cut_clip(0, 3.0)
    at = server.query(at_s=4.5)
    return any(o["kind"] == "text" for o in at["overlays"]) and not any(o["kind"] == "text" for o in server.query(at_s=5.9)["overlays"]) or False, at["overlays"]


@scenario()
def removing_a_clip_with_overlays_is_refused_then_cascade_clears_them():
    tid = two_clips_with_text()
    clip_b = server.get_timeline()["entries"][1]["id"]
    e = err(server.remove_op, op_id=clip_b)
    server.remove_op(op_id=clip_b, cascade=True)
    ids = [o["id"] for o in server.get_timeline()["ops"]]
    return e is not None and tid in str(e) and tid not in ids and clip_b not in ids, e


@scenario()
def trimming_the_head_of_an_earlier_clip_keeps_the_overlay_on_its_frame():
    two_clips_with_text(5.0)
    clip_a = server.get_timeline()["entries"][0]["id"]
    server.trim_clip(0, 1.0, 4.0, clip_id=clip_a)                 # A loses its first second: B starts 1 s earlier
    q = server.query(at_s=4.5)
    b = next(c for c in q["clips"] if c["source"] == "B")
    return any(o["kind"] == "text" for o in q["overlays"]) and abs(b["source_time_s"] - 1.5) < 0.05, q


@scenario()
def moving_a_clip_carries_its_overlay():
    two_clips_with_text(5.0)
    clip_b = server.get_timeline()["entries"][1]["id"]
    server.move_clip(1, 0, clip_id=clip_b)
    q = server.query(at_s=1.5)
    return any(o["kind"] == "text" for o in q["overlays"]) and q["clips"][0]["source"] == "B", q


@scenario()
def an_invalid_update_leaves_everything_as_it_was():
    fresh()
    server.add_clip("A", 0, 4)
    tid = server.add_text("keep", 1.0, 1.0)["op_id"]
    rev = server.get_timeline()["revision"]
    e = err(server.update_op, tid, {"dur": -1})
    return e is not None and "OUT_OF_RANGE" in str(e) and server.get_timeline()["revision"] == rev, e


@scenario()
def undo_and_redo_keep_the_same_ids():
    fresh()
    server.add_clip("A", 0, 4)
    tid = server.add_text("a", 1.0, 1.0)["op_id"]
    server.add_text("b", 2.0, 1.0)
    server.undo(); server.undo(); server.redo(); server.redo()
    return tid in ids_of("text") and len(ids_of("text")) == 2, ids_of("text")


@scenario()
def a_background_export_finishes_and_reports_like_the_blocking_one():
    fresh()
    server.add_clip("A", 0, 2)
    out = os.path.join(tempfile.mkdtemp(prefix="scen_exp_"), "bg.mp4")
    j = server.export(out, quality="draft", background=True)
    for _ in range(300):
        s = server.job_status(j["job_id"])
        if s["state"] != "running":
            break
        time.sleep(0.3)
    return s["state"] == "done" and s["result"]["path"] == out and os.path.exists(out) and abs(s["result"]["duration_s"] - 2.0) < 0.1, s


@scenario()
def cancelling_a_job_leaves_no_partial_file():
    fresh()
    server.add_clip("A", 0, 6)
    out = os.path.join(tempfile.mkdtemp(prefix="scen_cx_"), "cancel.mp4")
    j = server.export(out, quality="high", background=True)
    time.sleep(0.5)
    r = server.cancel_job(j["job_id"])
    time.sleep(0.3)
    return r["state"] == "cancelled" and not os.path.exists(out) and server.job_status(j["job_id"])["state"] == "cancelled", r


@scenario()
def a_missing_source_blocks_rendering_with_a_code():
    server.new_project(640, 360, 25)
    gone = os.path.join(tempfile.mkdtemp(prefix="scen_gone_"), "gone.mp4")
    shutil.copy(A, gone)
    server.import_clip(gone, "G")
    server.add_clip("G", 0, 2)
    os.remove(gone)
    e = err(server.export, os.path.join(tempfile.mkdtemp(), "x.mp4"), quality="draft")
    return e is not None and "SOURCE_MISSING" in str(e) and not server.verify_sources()["ok"], e


@scenario()
def a_batch_error_names_the_item_and_keeps_the_code():
    fresh()
    server.add_clip("A", 0, 3)
    e = err(server.apply_ops, [{"tool": "add_text", "text": "ok", "start_s": 0.5, "dur_s": 1}, {"tool": "add_text", "text": "late", "start_s": 50, "dur_s": 1}])
    return e is not None and "item 1" in str(e) and "TIMELINE_CONFLICT" in str(e) and server.get_timeline()["op_count"] == 1, e


@scenario()
def a_failed_edit_does_not_use_up_its_request_id():
    fresh()
    server.add_clip("A", 0, 3)
    e1 = err(server.add_text, "late", 50, 1, request_id="fix-me")
    e2 = err(server.add_text, "on time", 1.0, 1, request_id="fix-me")
    return e1 is not None and e2 is None and server.get_timeline()["op_count"] == 2, (e1, e2)


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
