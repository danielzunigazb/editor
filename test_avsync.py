#!/usr/bin/env python3
"""Audio/video sync of an export: the sound of a clip in the export is compared with its source (envelope cross-correlation) and the offset is reported in
qa.av_offset_ms; an export whose sound is shifted is caught, one with music over the speech is not judged. Run: python3 test_avsync.py"""
import os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["MLT_EDITOR_HOME"] = tempfile.mkdtemp(prefix="av_")
os.environ["MLT_LOG"] = "off"
import server  # noqa: E402
from mltedit import qa  # noqa: E402

ok = bad = 0


def check(name, cond, detail=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:300]}]" if not cond and detail else ""))


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


tmp = tempfile.mkdtemp()
# a picture with two incommensurate trains of sound pulses (so the correlation has one clear peak inside +-500 ms)
src = os.path.join(tmp, "pulses.mp4")
ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=10", "-f", "lavfi", "-i",
   "aevalsrc='sin(2*PI*440*t)*lt(mod(t,1.7),0.15)+0.8*sin(2*PI*660*t)*lt(mod(t,2.3),0.1)':s=48000:d=10", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src)

server.new_project(640, 360, 25)
server.import_clip(src, "P")
server.add_clip("P", 1, 9)
out = os.path.join(tmp, "out.mp4")
res = server.export(out, quality="draft")
q = res["qa"]
check("an export reports where its sound is against the source", "av_offset_ms" in q and q.get("correlation", 0) > 0.6, q)
check("...and an untouched clip is in sync (within the tolerance)", abs(q.get("av_offset_ms", 999)) <= 60, q.get("av_offset_ms"))
check("...so no sync finding", not any("against the picture" in f for f in q["findings"]), q["findings"])

# a deliberately late sound track: the same export with its audio delayed 250 ms
late = os.path.join(tmp, "late.mp4")
ff("-i", out, "-itsoffset", "0.25", "-i", out, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", late)
entries = [{"path": src, "in": 1.0, "start": 0.0, "dur": 8.0, "has_audio": True}]
m = qa.av_offset(late, entries)
check("a sound delayed by 250 ms is measured as about +250 ms", 200 <= m.get("av_offset_ms", 0) <= 300, m)
early = os.path.join(tmp, "early.mp4")
ff("-itsoffset", "0.25", "-i", out, "-i", out, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", early)
m2 = qa.av_offset(early, entries)
check("a picture delayed by 250 ms (sound early) is about -250 ms", -300 <= m2.get("av_offset_ms", 0) <= -200, m2)

# music over the clip: nothing is claimed instead of a wrong number
music = os.path.join(tmp, "music.wav")
ff("-f", "lavfi", "-i", "anoisesrc=d=10:c=pink:r=48000:a=0.8", music)
noisy = os.path.join(tmp, "noisy.mp4")
ff("-i", out, "-i", music, "-filter_complex", "[1:a]volume=3[n];[0:a][n]amix=inputs=2:duration=first:normalize=0[a]", "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", noisy)
m3 = qa.av_offset(noisy, entries)
check("with loud music over it the check says it cannot measure, and does not invent an offset", "av_offset_ms" not in m3 and "av_unmeasured" in m3, m3)
silent_entries = [{"path": src, "in": 1.0, "start": 0.0, "dur": 8.0, "has_audio": False}]
check("a project with no audio clips: not measurable, said so", "av_unmeasured" in qa.av_offset(out, silent_entries))

# real footage, when it is there: a talk through the engine
talk = os.path.join(HERE, "media_user", "charla.mp4")
if os.path.exists(talk):
    server.new_project(640, 360, 25)
    server.import_clip(talk, "T")
    server.add_clip("T", 20, 40)
    r2 = server.export(os.path.join(tmp, "talk.mp4"), quality="draft")["qa"]
    check("real talk footage (two voices, 29.97 fps source into 25 fps) exports in sync", abs(r2.get("av_offset_ms", 999)) <= 60 or "av_unmeasured" in r2, r2)
    print("   charla.mp4 ->", {k: r2.get(k) for k in ("av_offset_ms", "correlation", "av_unmeasured")})
else:
    print(f"SKIP the real-footage check (a talk through the engine): {talk} is not here (personal footage, not in the repository), so it was NOT run")
print(f"\n{ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
