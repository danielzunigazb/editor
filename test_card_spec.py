#!/usr/bin/env python3
"""A card's content is kept in the project (the video only shows it): list_sources says what each card says. Run: python3 test_card_spec.py"""
import os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["MLT_EDITOR_HOME"] = tempfile.mkdtemp(prefix="cs_")
os.environ["MLT_LOG"] = "off"
import server  # noqa: E402

ok = bad = 0


def check(name, cond, detail=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:300]}]" if not cond and detail else ""))


server.new_project(640, 360, 25)
server.add_card("title", title="Resumen del trimestre", subtitle="Ventas", dur_s=3.0)
server.add_card("list", title="Prioridades", items=["Uno", "Dos"], dur_s=3.0)
src = server.list_sources()
c1, c2 = src["CARD1"]["card"], src["CARD2"]["card"]
check("list_sources says what a title card says", c1["title"] == "Resumen del trimestre" and c1["subtitle"] == "Ventas" and c1["layout"] == "title", c1)
check("...and what a list card says", c2["items"] == ["Uno", "Dos"] and c2["layout"] == "list" and c2["title"] == "Prioridades", c2)
check("...with its template and length", c1["theme"] and c1["dur_s"] == 3.0, c1)
print(f"\n{ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
