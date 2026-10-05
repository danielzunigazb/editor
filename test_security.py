#!/usr/bin/env python3
"""Security of the engine with its DEFAULT settings (the fence is on): path escapes, hostile files, resource limits. Every attempt must be refused with a code and
leave no trace. Each scenario runs in its own process, because the fence and the limits are read when the server starts. Run: python3 test_security.py"""
import json, os, subprocess, sys, tempfile, textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
A = os.path.join(HERE, "media", "clip_a.mp4")
TMP = tempfile.mkdtemp(prefix="sec_")
ok = bad = 0


def check(name, cond, detail=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:400]}]" if not cond and detail else ""))


def run(code, home, **env):
    """Run `code` (it can use `server`, `H` = the project folder and `try_(fn)` -> 'OK' or the error code) in a fresh process; returns the dict it printed."""
    e = {k: v for k, v in os.environ.items() if not k.startswith("MLT_") and k not in ("R2_WORKER_URL", "R2_UPLOAD_TOKEN")}
    e.update(MLT_EDITOR_HOME=home, MLT_LOG="off", **env)
    prog = textwrap.dedent("""
        import json, os, sys
        sys.path.insert(0, %r)
        import server
        from mltedit.errors import EditError
        H = os.environ["MLT_EDITOR_HOME"]
        def try_(fn):
            try:
                fn(); return "OK"
            except EditError as e:
                return e.code
            except Exception as e:
                return "RAW:" + type(e).__name__ + ":" + str(e)[:120]
        out = {}
    """ % HERE) + textwrap.dedent(code) + "\nprint('RESULT ' + json.dumps(out))\n"
    r = subprocess.run(["xvfb-run", "-a", PY, "-c", prog] if not os.environ.get("DISPLAY") else [PY, "-c", prog], env=e, capture_output=True, text=True, timeout=300)
    line = next((x for x in r.stdout.splitlines() if x.startswith("RESULT ")), None)
    if not line:
        raise SystemExit(f"scenario failed: {r.stderr[-600:]}")
    return json.loads(line[7:])


def mk(path, *args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, path], check=True)


# ---------------------------------------------------------------------------------------------------------------- the fence
home = os.path.join(TMP, "proj"); os.makedirs(home)
outside = os.path.join(TMP, "outside"); os.makedirs(outside)
secret = os.path.join(outside, "secret.mp4"); mk(secret, "-i", A, "-t", "2", "-c", "copy")
inside = os.path.join(home, "inside.mp4"); mk(inside, "-i", A, "-t", "2", "-c", "copy")
os.symlink(secret, os.path.join(home, "link.mp4"))                                    # inside the project, points outside
os.symlink(outside, os.path.join(home, "dir_link"))
png = os.path.join(outside, "x.png"); mk(png, "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1", "-frames:v", "1")
srt = os.path.join(outside, "x.srt"); open(srt, "w").write("1\n00:00:00,000 --> 00:00:01,000\nhi\n")
r = run(f"""
server.new_project(640, 360, 25)
out["inside"] = try_(lambda: server.import_clip({inside!r}, "A"))
out["absolute_outside"] = try_(lambda: server.import_clip({secret!r}, "B"))
out["etc_passwd"] = try_(lambda: server.import_clip("/etc/passwd", "C"))
out["dotdot"] = try_(lambda: server.import_clip(os.path.join(H, "..", "outside", "secret.mp4"), "D"))
out["symlink_file"] = try_(lambda: server.import_clip(os.path.join(H, "link.mp4"), "E"))
out["symlink_dir"] = try_(lambda: server.import_clip(os.path.join(H, "dir_link", "secret.mp4"), "F"))
out["tilde"] = try_(lambda: server.import_clip("~/anything.mp4", "G"))
out["nul"] = try_(lambda: server.import_clip("a\\0b.mp4", "H"))
out["image"] = try_(lambda: server.add_image(0, 1, path={png!r}))
out["srt"] = try_(lambda: server.add_subtitles(srt_path={srt!r}))
server.add_clip("A", 0, 2)
out["export_outside"] = try_(lambda: server.export({os.path.join(outside, "stolen.mp4")!r}, "draft"))
out["export_dotdot"] = try_(lambda: server.export(os.path.join(H, "..", "outside", "stolen2.mp4"), "draft"))
out["audio_outside"] = try_(lambda: server.add_audio(0, 1, path={secret!r}))
out["export_inside"] = try_(lambda: server.export(os.path.join(H, "ok.mp4"), "draft"))
out["sources"] = sorted(server.load()["sources"])
""", home)
check("a file inside the project folder is accepted (the default fence is not a wall around everything)", r["inside"] == "OK", r)
for k in ("absolute_outside", "etc_passwd", "dotdot", "symlink_file", "symlink_dir", "tilde"):
    check(f"fence by default: import_clip refuses {k}", r[k] == "PATH_NOT_ALLOWED", r[k])
check("a path with a NUL byte is refused, not passed to the OS", r["nul"] not in ("OK",) and not r["nul"].startswith("RAW"), r["nul"])
check("fence by default: add_image / add_subtitles / add_audio outside are refused", r["image"] == r["srt"] == r["audio_outside"] == "PATH_NOT_ALLOWED", (r["image"], r["srt"], r["audio_outside"]))
check("fence by default: exporting outside (absolute or with ..) is refused and writes nothing", r["export_outside"] == r["export_dotdot"] == "PATH_NOT_ALLOWED"
      and not os.path.exists(os.path.join(outside, "stolen.mp4")) and not os.path.exists(os.path.join(outside, "stolen2.mp4")), (r["export_outside"], r["export_dotdot"]))
check("exporting inside the project folder works", r["export_inside"] == "OK", r["export_inside"])
check("none of the refused files became a source", r["sources"] == ["A"], r["sources"])

extra = run(f"""
server.new_project(640, 360, 25)
out["extra_root"] = try_(lambda: server.import_clip({secret!r}, "B"))
""", os.path.join(TMP, "p2"), MLT_EDITOR_ROOTS=outside)
check("a folder named in MLT_EDITOR_ROOTS is allowed (and only that one)", extra["extra_root"] == "OK", extra)
openf = run(f"""
server.new_project(640, 360, 25)
try:
    server.import_clip("/etc/hostname", "B"); out["open"] = "OK"
except EditError as e:
    out["open"] = str(e)
out["any_video"] = try_(lambda: server.import_clip({secret!r}, "C"))
""", os.path.join(TMP, "p3"), MLT_EDITOR_ROOTS="*")
check("MLT_EDITOR_ROOTS='*' turns the fence off explicitly (the file is then judged on its content, not its place)", openf["any_video"] == "OK" and "outside the allowed folders" not in openf["open"], openf)

# ---------------------------------------------------------------------------------------------------------------- hostile files
hp = os.path.join(TMP, "hostile"); os.makedirs(hp)
corrupt = os.path.join(hp, "corrupt.mp4"); open(corrupt, "wb").write(os.urandom(5000))
textfile = os.path.join(hp, "notes.mp4"); open(textfile, "w").write("this is not a video " * 100)
empty = os.path.join(hp, "empty.mp4"); open(empty, "wb").close()
truncated = os.path.join(hp, "truncated.mp4"); open(truncated, "wb").write(open(A, "rb").read()[:3000])
bomb = os.path.join(hp, "wide.mp4"); mk(bomb, "-f", "lavfi", "-i", "color=c=gray:s=8400x64:r=5:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p")
ok_clip = os.path.join(hp, "fine.mp4"); mk(ok_clip, "-i", A, "-t", "3", "-c", "copy")
r = run(f"""
server.new_project(640, 360, 25)
for name, p in (("corrupt", {corrupt!r}), ("text", {textfile!r}), ("empty", {empty!r}), ("truncated", {truncated!r}), ("bomb", {bomb!r})):
    out[name] = try_(lambda p=p: server.import_clip(p, "X"))
out["bad_id"] = try_(lambda: server.import_clip({ok_clip!r}, "../../etc/x"))
out["bad_id2"] = try_(lambda: server.import_clip({ok_clip!r}, "a b"))
out["sources"] = sorted(server.load()["sources"])
""", os.path.join(TMP, "p4"), MLT_EDITOR_ROOTS=hp)
for k in ("corrupt", "text", "empty", "truncated"):
    check(f"a {k} file is refused with a clean error (no crash, no raw exception)", r[k] not in ("OK",) and not r[k].startswith("RAW"), r[k])
check("a 8400 px wide 'video' is refused as LIMIT_EXCEEDED (resolution bomb)", r["bomb"] == "LIMIT_EXCEEDED", r["bomb"])
check("a source id with ../ or spaces is refused (INVALID_ARGUMENT)", r["bad_id"] == r["bad_id2"] == "INVALID_ARGUMENT", (r["bad_id"], r["bad_id2"]))
check("nothing hostile was registered as a source", r["sources"] == [], r["sources"])

# ---------------------------------------------------------------------------------------------------------------- limits
lim = run(f"""
server.new_project(640, 360, 25)
out["size"] = try_(lambda: server.import_clip({ok_clip!r}, "A"))
""", os.path.join(TMP, "p5"), MLT_EDITOR_ROOTS=hp, MLT_MAX_SOURCE_MB="0")
check("a file over MLT_MAX_SOURCE_MB is refused (LIMIT_EXCEEDED)", lim["size"] == "LIMIT_EXCEEDED", lim)
lim = run(f"""
server.new_project(640, 360, 25)
out["dur"] = try_(lambda: server.import_clip({ok_clip!r}, "A"))
""", os.path.join(TMP, "p6"), MLT_EDITOR_ROOTS=hp, MLT_MAX_SOURCE_S="1")
check("a source longer than MLT_MAX_SOURCE_S is refused (LIMIT_EXCEEDED)", lim["dur"] == "LIMIT_EXCEEDED", lim)
lim = run(f"""
server.new_project(640, 360, 25)
out["a"] = try_(lambda: server.import_clip({ok_clip!r}, "A"))
out["b"] = try_(lambda: server.import_clip({ok_clip!r}, "B"))
out["c"] = try_(lambda: server.import_clip({ok_clip!r}, "C"))
""", os.path.join(TMP, "p7"), MLT_EDITOR_ROOTS=hp, MLT_MAX_SOURCES="2")
check("the source count limit (MLT_MAX_SOURCES) is enforced with LIMIT_EXCEEDED", (lim["a"], lim["b"], lim["c"]) == ("OK", "OK", "LIMIT_EXCEEDED"), lim)
qhome = os.path.join(TMP, "p8")
run(f"""
server.new_project(640, 360, 25)
server.import_clip({ok_clip!r}, "A"); server.add_clip("A", 0, 2)
""", qhome, MLT_EDITOR_ROOTS=hp)
lim = run("""
out["quota"] = try_(lambda: server.export(os.path.join(H, "q.mp4"), "draft"))
out["preview"] = try_(lambda: server.render_preview())
out["import"] = try_(lambda: server.import_clip(os.path.join(H, "none.mp4"), "Z"))
out["exists"] = os.path.exists(os.path.join(H, "q.mp4"))
""", qhome, MLT_EDITOR_ROOTS=hp, MLT_PROJECT_QUOTA_MB="0.0001")
check("a project over its disk quota cannot start an export or a preview (LIMIT_EXCEEDED), and nothing is written", lim["quota"] == lim["preview"] == "LIMIT_EXCEEDED" and not lim["exists"], lim)
lim = run(f"""
import time
server.new_project(640, 360, 25)
server.import_clip({A!r}, "A")
for _ in range(13): server.add_clip("A", 0, 4)            # 52 s: over the 40 s that block
j = server.export(os.path.join(H, "slow.mp4"), "draft")
s = server.job_status(j["job_id"], wait_s=40)
for _ in range(20):
    if s["state"] != "running": break
    s = server.job_status(j["job_id"], wait_s=5)
out["state"], out["error"] = s["state"], s.get("error", "")
out["file"] = os.path.exists(os.path.join(H, "slow.mp4"))
""", os.path.join(TMP, "p9"), MLT_EDITOR_ROOTS=os.path.dirname(A), MLT_RENDER_TIMEOUT_S="4")
check("a background render that outlasts MLT_RENDER_TIMEOUT_S is stopped and reported as LIMIT_EXCEEDED", lim["state"] == "failed" and "LIMIT_EXCEEDED" in lim["error"], lim)

# ---------------------------------------------------------------------------------------------------------------- SVG
blue_red = os.path.join(hp, "evil.svg")
open(blue_red, "w").write(f"""<?xml version="1.0"?>
<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/hostname">]>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="200" height="200" viewBox="0 0 200 200">
  <rect width="200" height="200" fill="#0000ff"/>
  <image x="0" y="0" width="200" height="200" xlink:href="file://{png}"/>
  <image x="0" y="0" width="200" height="200" xlink:href="http://127.0.0.1:9/x.png"/>
  <script>alert(1)</script><text x="10" y="20">&xxe;</text>
</svg>""")
plain = os.path.join(hp, "plain.svg")
open(plain, "w").write('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200"><rect width="200" height="200" fill="#0000ff"/></svg>')
variants = {"entity+script+file image": blue_red}
for name, body in (("script only", "<rect width='9' height='9'/><script>alert(1)</script>"), ("remote image", "<image href='http://127.0.0.1:9/x.png' width='9' height='9'/>"),
                   ("style import", "<style>@import url(http://127.0.0.1:9/x.css);</style>"), ("foreignObject", "<foreignObject width='9' height='9'><div xmlns='http://www.w3.org/1999/xhtml'>x</div></foreignObject>"),
                   ("event handler", "<rect width='9' height='9' onload='alert(1)'/>"), ("use external", "<use href='file:///etc/hostname#a'/>")):
    pth = os.path.join(hp, name.replace(" ", "_").replace("+", "_") + ".svg")
    open(pth, "w").write(f'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200">{body}</svg>')
    variants[name] = pth
r = run(f"""
server.new_project(640, 360, 25)
server.import_clip({ok_clip!r}, "A"); server.add_clip("A", 0, 2)
out["hostile"] = {{k: try_(lambda p=p: server.add_image(0, 1.5, path=p, position="center", scale=0.5, plate=False)) for k, p in {variants!r}.items()}}
out["plain"] = try_(lambda: server.add_image(0, 1.5, path={plain!r}, position="center", scale=0.5, plate=False))
img = server.get_still(0.5)
png = (img[0] if isinstance(img, list) else img).data
from PIL import Image
import io
px = list(Image.open(io.BytesIO(png)).convert("RGB").getdata())
out["blue"] = sum(1 for r_, g, b in px if b > 200 and r_ < 60 and g < 60)
""", os.path.join(TMP, "p10"), MLT_EDITOR_ROOTS=f"{hp}{os.pathsep}{outside}")
check("hostile SVGs (external entity, script, remote or file image, style import, foreignObject, event handler, external use) are all refused with INVALID_ARGUMENT",
      all(v == "INVALID_ARGUMENT" for v in r["hostile"].values()), r["hostile"])
check("a plain SVG is accepted and drawn (the validator is not just refusing everything)", r["plain"] == "OK" and r["blue"] > 100, (r["plain"], r["blue"]))

import shutil
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
