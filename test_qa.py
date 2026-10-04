#!/usr/bin/env python3
"""The render checks itself: export and render_preview report unexpected hard cuts and one-frame flashes (and only those), judged against what the timeline
says should be there. Run: python3 test_qa.py"""
import os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["MLT_EDITOR_HOME"] = tempfile.mkdtemp(prefix="qa_")
import server  # noqa: E402

A, B = os.path.join(HERE, "media", "clip_a.mp4"), os.path.join(HERE, "media", "clip_b.mp4")
ok = bad = 0


def check(name, cond, detail=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:300]}]" if not cond and detail else ""))


server.new_project(640, 360, 25)
server.import_clip(A, "A"); server.import_clip(B, "B")
server.add_clip("A", 0, 3); server.add_clip("B", 0, 3)
r = server.render_preview()
check("a hard cut between two clips with no transition is what was asked for: no findings", r["qa"]["ok"] and r["qa"]["findings"] == [] and r["qa"]["intended_cuts"] == [3.0], r["qa"])
check("...and the cut itself was seen (the check is not blind)", any(abs(t - 3.0) < 0.1 for t in r["qa"]["hard_cuts"]), r["qa"])
server.add_graphic("letterbox", 1.0, 0.04, opacity=1.0, fade_s=0.0)
r2 = server.render_preview()
check("a one-frame flash that nobody asked for is reported with its time", not r2["qa"]["ok"] and any("1s" in f or "1.04s" in f for f in r2["qa"]["findings"]), r2["qa"])
server.undo()
server.crossfade(0, 0.8, style="dissolve")
r3 = server.render_preview()
check("a crossfade replaces the cut: still no findings, and nothing intended is left to find", r3["qa"]["ok"] and r3["qa"]["intended_cuts"] == [], r3["qa"])
out = os.path.join(tempfile.mkdtemp(), "x.mp4")
e = server.export(out, quality="draft")
check("export reports the same check on the exported file", "qa" in e and e["qa"]["ok"], e.get("qa"))
print(f"\n{ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
