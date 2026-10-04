#!/usr/bin/env python3
"""Proxies and the still cache: the proxy is frame-exact (same frame count, frame N is frame N), previews use it and exports never do, a changed file never
shows an old proxy, caches are keyed on what a picture depends on, pruning keeps the folder small, and the speed targets hold on real 1080p / 4K media.
Run: python3 test_proxy.py    (the speed checks need media_1080/ and media_4k/; they are skipped when absent)"""
import hashlib, json, os, re, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
HOME = tempfile.mkdtemp(prefix="prx_")
os.environ["MLT_EDITOR_HOME"] = HOME
import server  # noqa: E402
from mltedit import engine as live  # noqa: E402
from mltedit.media import proxy as px  # noqa: E402
from mltedit.tools import review  # noqa: E402

M720 = os.path.join(HERE, "media", "clip_a.mp4")
M1080 = os.path.join(HERE, "media_1080", "cam_h264_1080p30.mp4")
M4K = os.path.join(HERE, "media_4k", "cam_h264_4k30.mp4")
ok = bad = 0


def check(name, cond, detail=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:300]}]" if not cond and detail else ""))


def frames_of(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames,r_frame_rate,height", "-of", "json", path], capture_output=True, text=True)
    s = json.loads(r.stdout)["streams"][0]
    return int(s["nb_read_frames"]), s["r_frame_rate"], int(s["height"])


def psnr(a, b, h, n=60):
    f = f"[1:v]trim=end_frame={n},setpts=PTS-STARTPTS,scale=-2:{h}:flags=bicubic[o];[0:v]trim=end_frame={n},setpts=PTS-STARTPTS[p];[p][o]psnr"
    r = subprocess.run(["ffmpeg", "-i", a, "-i", b, "-lavfi", f, "-f", "null", "-"], capture_output=True, text=True)
    return float(re.search(r"average:([\d.]+)", r.stderr).group(1))


# ------------------------------------------------------------------------------------------------ which sources get a proxy
server.new_project(1280, 720, 25)
r = server.import_clip(M720, "S")
check("a 720p source gets a proxy (taller than 540)", r.get("proxy") in ("pending", "ready"), r)
check("a source no taller than the proxy does not (nothing to gain)", px.wanted({"height": 540}) is False and px.wanted({"height": 360}) is False)
states = server.wait_for_proxies(60)["states"]
check("wait_for_proxies waits and reports ready", states == {"S": "ready"}, states)
src = server.load()["sources"]["S"]
pp = px.path_for(server.HOME, src)
n_p, fr_p, h_p = frames_of(pp)
n_o, fr_o, h_o = frames_of(M720)
check("the proxy has exactly as many frames as the original, at the same frame rate", n_p == n_o and fr_p == fr_o, (n_p, n_o, fr_p, fr_o))
check("the proxy is 540 px tall", h_p == 540, h_p)
q = psnr(pp, M720, 540)
check(f"the proxy matches the original scaled down: frame for frame, PSNR {q:.1f} dB >= 35", q >= 35, q)
keyframes = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey", "-count_frames", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", pp], capture_output=True, text=True).stdout.strip()
check("every frame of the proxy is a keyframe (any frame decodes alone)", keyframes == str(n_p), (keyframes, n_p))
check("no half-written proxy is left behind", not [f for f in os.listdir(os.path.dirname(pp)) if ".part" in f])
lst = server.list_sources()
check("list_sources reports the proxy state", lst["S"]["proxy"] == "ready", lst)

# ------------------------------------------------------------------------------------------------ MLT reads the same frames from the proxy
server.add_clip("S", 0, 5)
st = server.load()


def still_rgb(st_, t, scale):
    review._TRACTORS.clear()
    w, h, data = review._frames(st_, [t], scale)[0]
    return w, h, data


def png_psnr(f1, f2):
    w, h, a = f1
    _, _, b = f2
    d = tempfile.mkdtemp()
    for n, data in (("a", a), ("b", b)):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-i", "-", os.path.join(d, n + ".png")], input=data, check=True)
    r = subprocess.run(["ffmpeg", "-i", os.path.join(d, "a.png"), "-i", os.path.join(d, "b.png"), "-lavfi", "psnr", "-f", "null", "-"], capture_output=True, text=True)
    return float(re.search(r"average:([\d.]+|inf)", r.stderr).group(1).replace("inf", "99"))


with_proxy = still_rgb(st, 2.0, 0.5)
os.environ["MLT_PROXY"] = "0"
without = still_rgb(st, 2.0, 0.5)
adj_t = 2.0 + 1 / 25
without_adj = still_rgb(st, adj_t, 0.5)
del os.environ["MLT_PROXY"]
same, adj = png_psnr(with_proxy, without), png_psnr(with_proxy, without_adj)
check(f"the proxy shows MLT the same frame: PSNR {same:.1f} dB with the original's frame vs {adj:.1f} dB with the next one", same >= 20 and same > adj + 3, (same, adj))
check("a preview at full size never uses a proxy", all(v == st["sources"][k]["path"] for k, v in px.media_for_preview(server.HOME, st["sources"], 1.0).items()))
check("a preview smaller than full size does", all(v == px.path_for(server.HOME, st["sources"][k]) for k, v in px.media_for_preview(server.HOME, st["sources"], 0.5).items()))

# ------------------------------------------------------------------------------------------------ exports do not depend on proxies
out_a, out_b = os.path.join(HOME, "a.mp4"), os.path.join(HOME, "b.mp4")
server.export(out_a, quality="draft")
os.environ["MLT_PROXY"] = "0"
server.export(out_b, quality="draft")
del os.environ["MLT_PROXY"]


def md5(p):
    return hashlib.md5(subprocess.run(["ffmpeg", "-v", "error", "-i", p, "-map", "0:v:0", "-f", "framemd5", "-"], capture_output=True, text=True).stdout.encode()).hexdigest()


check("the export is identical with proxies on and off (it reads the originals)", md5(out_a) == md5(out_b))

# ------------------------------------------------------------------------------------------------ the still cache
calls = {"n": 0}
real_build = live.build


def counting_build(*a, **k):
    calls["n"] += 1
    return real_build(*a, **k)


live.build = counting_build
review._TRACTORS.clear()
i1 = server.get_still(1.0)
n1 = calls["n"]
t0 = time.perf_counter(); i2 = server.get_still(1.0); dt = time.perf_counter() - t0
check("the same still again is served from the cache: identical bytes, no MLT build", i1.data == i2.data and calls["n"] == n1 and dt < 0.05, (calls["n"], n1, dt))
server.add_text("x", 0.5, 1.0)
i3 = server.get_still(1.0)
check("after an edit the still is made again (the key follows the project)", calls["n"] == n1 + 1 and i3.data != i1.data, (calls["n"], n1))
server.get_contact_sheet(4)
n2 = calls["n"]
s2 = server.get_contact_sheet(4)
check("a contact sheet is cached too", calls["n"] == n2 and len(s2.data) > 1000)
server.undo()
i4 = server.get_still(1.0)
check("after undo the earlier still comes straight from the cache", i4.data == i1.data and calls["n"] == n2, (calls["n"], n2))
live.build = real_build

# a replaced file: no stale proxy, no stale still
alt = os.path.join(HOME, "alt.mp4")
shutil.copy(M720, alt)
server.new_project(1280, 720, 25)
server.import_clip(alt, "R")
server.add_clip("R", 0, 3)
server.wait_for_proxies(60)
a_png = server.get_still(1.0).data
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=1280x720:r=25:d=6", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", alt + ".new.mp4"], check=True)
os.replace(alt + ".new.mp4", alt)
b_png = server.get_still(1.0).data
check("a source replaced on disk shows its new picture (neither the old proxy nor the old still)", a_png != b_png)
server.refresh_source("R")
server.wait_for_proxies(60)
c_png = server.get_still(1.0).data
check("...also after refresh_source, with the new proxy", c_png != a_png and len(c_png) > 1000)

# ------------------------------------------------------------------------------------------------ pruning and switches
d = os.path.join(HOME, "pr")
os.makedirs(os.path.join(d, "proxies"))
for i in range(5):
    p = os.path.join(d, "proxies", f"p{i}.mp4")
    open(p, "wb").write(b"0" * 1_000_000)
    os.utime(p, (1000 + i, 1000 + i))
removed = px.prune(d, 3.0)
left = sorted(os.listdir(os.path.join(d, "proxies")))
check("pruning removes the least recently used proxies first until the folder fits", removed == 2 and left == ["p2.mp4", "p3.mp4", "p4.mp4"], (removed, left))
os.environ["MLT_PROXY"] = "0"
check("MLT_PROXY=0 switches proxies off", px.wanted({"height": 2160}) is False and px.state(server.HOME, {"height": 2160, "path": "x"}) == "none")
del os.environ["MLT_PROXY"]
bad_src = {"path": "/no/such/file.mp4", "height": 2160}
f = px.ensure(server.HOME, bad_src)
px.wait(server.HOME, {"x": bad_src}, 30)
check("a proxy that cannot be made is 'failed', never 'ready'", f == "pending" and px.state(server.HOME, bad_src) == "failed", px.state(server.HOME, bad_src))

# ------------------------------------------------------------------------------------------------ speed on real media
for label, media in (("1080p", M1080), ("4K", M4K)):
    if not os.path.exists(media):
        print(f"SKIP speed on {label}: {media} not found")
        continue
    server.new_project(*((1920, 1080, 30) if label == "1080p" else (3840, 2160, 30)))
    server.import_clip(media, "A"); server.import_clip(media, "B")
    server.add_clip("A", 0, 4); server.add_clip("B", 0, 4); server.crossfade(0, 0.5)
    server.add_graphic("frame", 0, 7); server.add_lower_third("Valeria", "Directora", 1, 3); server.add_text("t", 4.5, 2, position="center")
    server.wait_for_proxies(300)
    cold, hot = [], []
    for r in range(3):
        server.add_text(f"edit {r}", 5.0 + 0.1 * r, 0.5, position="top")
        review._TRACTORS.clear()
        t0 = time.perf_counter(); server.get_still(1.5); cold.append(time.perf_counter() - t0)
        ts = []
        for k in range(1, 6):
            t0 = time.perf_counter(); server.get_still(1.5 + 0.2 * k); ts.append(time.perf_counter() - t0)
        hot.append(sorted(ts)[2])
    server.add_text("sheet", 6.0, 0.5, position="top")
    t0 = time.perf_counter(); server.get_contact_sheet(6); sheet = time.perf_counter() - t0
    cold, hot = sorted(cold)[1], sorted(hot)[1]
    print(f"  {label}: still cold {cold:.3f}s, hot {hot:.3f}s, contact sheet(6) {sheet:.3f}s")
    check(f"{label}: a still right after an edit takes < 0.4 s (was {'1.2' if label == '1080p' else '4.3'} s)", cold < 0.4, cold)
    check(f"{label}: another still of the same edit takes < 0.15 s", hot < 0.15, hot)
    check(f"{label}: a 6-frame contact sheet takes < 1 s (was {'2.2' if label == '1080p' else '7.9'} s)", sheet < 1.0, sheet)

print(f"\n{ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
