#!/usr/bin/env python3
"""Pure-python checks of annotate.py's tracker (no OpenCV needed). Run: python3 test_annotate.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import annotate
ok = bad = 0
def chk(name, cond, detail=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if not cond and detail else ""))

# two buildings drifting slowly across the frame, one detection per second: must come out as two tracks, not one or four
by_t = {t: [(0.2 + 0.02 * t, 0.5, 0.1, 0.1, 0.5), (0.7 - 0.02 * t, 0.4, 0.1, 0.1, 0.4)] for t in range(6)}
tr = annotate.track(by_t, 0.2, 0.3)
chk("two moving objects give two tracks of 6 samples", sorted(len(t) for t in tr) == [6, 6], [len(t) for t in tr])
chk("each track keeps its own object (no identity swap when they approach)", all(abs(t[0][1] - t[-1][1]) < 0.2 for t in tr))
by_t2 = {0: [(0.5, 0.5, 0.1, 0.1, 0.5)], 1: [(0.5, 0.5, 0.1, 0.1, 0.5)], 5: [(0.5, 0.5, 0.1, 0.1, 0.5)], 6: [(0.5, 0.5, 0.1, 0.1, 0.5)]}
chk("a 4 s gap splits a track (the object was lost)", sorted(len(t) for t in annotate.track(by_t2, 0.2, 0.3)) == [2, 2])
by_t3 = {t: [(0.1 + 0.6 * t, 0.5, 0.1, 0.1, 0.5)] for t in range(3)}
chk("a jump of 0.6 of the frame in one second is a different object", len(annotate.track(by_t3, 0.2, 0.3)) == 3)
chk("boxes below the score floor or above the area cap are ignored", annotate.track({0: [(0.5, 0.5, 0.1, 0.1, 0.1), (0.5, 0.5, 0.9, 0.9, 0.9)]}, 0.2, 0.3) == [])
sm = annotate.smooth([(0, 0.0, 0.0, 0, 0, 0), (1, 0.3, 0.0, 0, 0, 0), (2, 0.0, 0.0, 0, 0, 0)])
chk("smoothing damps a one-sample spike but keeps the times", sm[1][1] == 0.1 and [p[0] for p in sm] == [0, 1, 2], sm)
print(f"\n{ok} passed, {bad} failed"); sys.exit(1 if bad else 0)
