#!/usr/bin/env python3
"""until="clip_end": an overlay that lasts until the end of the clip it is anchored to, and still does after that clip is trimmed or moved. Run: python3 test_until.py"""
import os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["MLT_EDITOR_HOME"] = tempfile.mkdtemp(prefix="un_")
os.environ["MLT_LOG"] = "off"
import server, live  # noqa: E402
from mltedit.errors import EditError  # noqa: E402

A, B = os.path.join(HERE, "media", "clip_a.mp4"), os.path.join(HERE, "media", "clip_b.mp4")
ok = bad = 0


def check(name, cond, detail=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:300]}]" if not cond and detail else ""))


def lay():
    st = server.load(); server.bind(st)
    return live.layout(st["ops"])


def text(m, t):
    return next(L for L in m["layers"] if L.get("text") == t)


server.new_project(640, 360, 25)
server.import_clip(A, "A"); server.import_clip(B, "B")
server.add_clip("A", 0, 4); server.add_clip("B", 0, 4)
server.add_text("Hasta el final", 5.0, 0.5, until="clip_end")      # 1 s into B; dur_s is only a first guess
server.add_text("Fija", 5.0, 0.5)
m = lay()
b = next(e for e in m["entries"] if e["src"] == "B")
check("the overlay lasts until the end of its clip (not the 0.5 s asked as the first guess)", abs(text(m, "Hasta el final")["dur"] - (b["start"] + b["dur"] - 5.0)) < 0.05, text(m, "Hasta el final")["dur"])
check("an overlay without until keeps its own duration", abs(text(m, "Fija")["dur"] - 0.5) < 0.01)
server.trim_clip(1, 0, 2.5)
m = lay()
b = next(e for e in m["entries"] if e["src"] == "B")
t = text(m, "Hasta el final")
check("after the clip is shortened the overlay ends where the clip ends", abs(t["start"] + t["dur"] - (b["start"] + b["dur"])) < 0.05, (t["start"], t["dur"], b["start"], b["dur"]))
server.trim_clip(0, 0, 3)
m = lay()
b = next(e for e in m["entries"] if e["src"] == "B")
t = text(m, "Hasta el final")
check("and when an earlier clip is shortened it moves with its clip and still ends with it", abs(t["start"] - (b["start"] + 1.0)) < 0.05 and abs(t["start"] + t["dur"] - (b["start"] + b["dur"])) < 0.05, (t["start"], t["dur"], b["start"], b["dur"]))
for bad_call, what in ((lambda: server.add_text("x", 1, 1, until="forever"), "an unknown until"), (lambda: server.add_text("x", 1, 1, until="clip_end", anchor="timeline"), "until with a timeline anchor")):
    try:
        bad_call(); check(what + " is refused", False)
    except EditError as e:
        check(what + " is refused with INVALID_ARGUMENT", e.code == "INVALID_ARGUMENT", e.code)
server.apply_ops([{"tool": "add_text", "text": "En lote", "start_s": 3.2, "dur_s": 0.3, "until": "clip_end"}])
m = lay()
b = next(e for e in m["entries"] if e["src"] == "B")
t = text(m, "En lote")
check("apply_ops items take until too", abs(t["start"] + t["dur"] - (b["start"] + b["dur"])) < 0.05, (t["start"], t["dur"], b["start"], b["dur"]))
server.undo()
check("it is one undo step like any edit", not any(L.get("text") == "En lote" for L in lay()["layers"]))
print(f"\n{ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
