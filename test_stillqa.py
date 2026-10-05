#!/usr/bin/env python3
"""Still QA: get_still / get_contact_sheet say when a frame is black or blown out or when text has no contrast against its surroundings, say nothing when the picture
is fine, and answer the same from the cache. Run: python3 test_stillqa.py"""
import os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["MLT_EDITOR_HOME"] = tempfile.mkdtemp(prefix="sq_")
os.environ["MLT_LOG"] = "off"
import server  # noqa: E402
from mltedit import stillqa  # noqa: E402
from mcp.server.fastmcp import Image  # noqa: E402

ok = bad = 0


def check(name, cond, detail=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:300]}]" if not cond and detail else ""))


def frame(rgb, w=320, h=180):
    return (w, h, bytes(rgb) * (w * h))


# --- the pixel rules on synthetic frames
check("black frame is reported", any("almost black" in n for n in stillqa.notes(frame((0, 0, 0)), 1.0)))
check("white frame is reported", any("blown out" in n for n in stillqa.notes(frame((255, 255, 255)), 1.0)))
check("a mid-grey frame is fine", stillqa.notes(frame((120, 120, 120)), 1.0) == [])
layer = {"kind": "text", "start": 0.0, "dur": 2.0, "pos": "bottom", "text": "Hola"}
flat = stillqa.notes(frame((200, 200, 200)), 1.0, [layer])
check("text on a flat band of the same tone is reported as hard to read", any("hard to read" in n and "Hola" in n for n in flat), flat)
w, h = 320, 180
rows = bytearray()
for y in range(h):
    for x in range(w):
        rows += bytes((255, 255, 255)) if (y > 0.8 * h and (x // 6) % 2) else bytes((20, 20, 20))
check("text-like contrast in its band is fine", stillqa.notes((w, h, bytes(rows)), 1.0, [layer]) == [])
check("a text layer that is not on screen at that time is ignored", not any("hard to read" in n for n in stillqa.notes(frame((200, 200, 200)), 5.0, [layer])))

# --- through the tools
tmp = tempfile.mkdtemp()
white, dark = os.path.join(tmp, "white.mp4"), os.path.join(tmp, "dark.mp4")
for path, col in ((white, "white"), (dark, "0x202020")):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={col}:s=640x360:r=25:d=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", path], check=True)
server.new_project(640, 360, 25)
server.import_clip(white, "W"); server.import_clip(dark, "D")
server.add_clip("W", 0, 2); server.add_clip("D", 0, 2)
server.add_text("Invisible", 0.2, 1.5, position="bottom", color="#ffffff", box=False, style="classic")
server.add_text("Visible", 2.2, 1.5, position="bottom", color="#ffffff", box=False, style="classic")
r_bad = server.get_still(1.0)
check("a still of a blown-out shot comes with a note (the engine draws a shadow under text, so the text itself stays legible: the contrast rule is covered above)",
      isinstance(r_bad, list) and isinstance(r_bad[0], Image) and "blown out" in r_bad[1], r_bad if not isinstance(r_bad, list) else r_bad[1])
r_ok = server.get_still(3.0)
check("a still with white text on a dark shot is just the image (no tokens spent on 'all good', and no false alarm on legible text)", isinstance(r_ok, Image), "" if isinstance(r_ok, Image) else r_ok[1])
r_again = server.get_still(1.0)
check("the same still from the cache gives the same note", isinstance(r_again, list) and r_again[1] == r_bad[1] and r_again[0].data == r_bad[0].data)
sheet = server.get_contact_sheet(4)
check("the contact sheet carries the notes of the frames that have them", isinstance(sheet, list) and "blown out" in sheet[1], sheet if not isinstance(sheet, list) else sheet[1])
print(f"\n{ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
