#!/usr/bin/env python3
"""The live viewer: segment hashes, the HTTP/HLS surface, incremental re-rendering, continuous audio, frame accuracy, speed, and the page.
Run: python3 test_viewer.py     (the browser part needs `pip install playwright` and a Chromium; it is skipped without them)"""
import json, os, re, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
HOME = tempfile.mkdtemp(prefix="vw_")
os.environ["MLT_EDITOR_HOME"] = HOME
import server  # noqa: E402
from mltedit import viewer as vw  # noqa: E402
from mltedit.preview import segments  # noqa: E402
from mltedit.tools import review  # noqa: E402

A, B = os.path.join(HERE, "media", "clip_a.mp4"), os.path.join(HERE, "media", "clip_b.mp4")
ok = bad = 0


def check(name, cond, detail=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:300]}]" if not cond and detail else ""))


def get(url, timeout=120):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), {}


def probe(path_or_url, entries="stream=codec_name,codec_type,width,height,nb_read_frames:format=start_time,duration", count=False):
    cmd = ["ffprobe", "-v", "error", *(["-count_frames"] if count else []), "-show_entries", entries, "-of", "json", path_or_url]
    return json.loads(subprocess.run(cmd, capture_output=True, text=True).stdout)


def base_project():
    server.new_project(640, 360, 25)
    server.import_clip(A, "A"); server.import_clip(B, "B")
    server.add_clip("A", 0, 4); server.add_clip("B", 0, 4)
    server.crossfade(0, 0.5)
    server.add_text("middle of segment 2", 4.6, 0.6, position="center", size=0.1)


# ------------------------------------------------------------------------------------------------ segment hashes (no rendering)
base_project()
st = server.load()
P1 = segments.plan(st, server.HOME)
n_seg = len(P1["segs"])
check("the timeline is cut into 2-second segments aligned to frames", n_seg == 4 and all(s["b"] - s["a"] == 50 for s in P1["segs"][:-1]) and P1["segs"][-1]["b"] == P1["total_f"], [(s["a"], s["b"]) for s in P1["segs"]])
check("equal projects give equal hashes", segments.plan(server.load(), server.HOME)["hash"] == P1["hash"])
text_id = next(o["id"] for o in server.get_timeline()["ops"] if o["op"] == "text")
server.update_op(text_id, {"text": "changed text"})
P2 = segments.plan(server.load(), server.HOME)
diff = [i for i in range(n_seg) if P1["segs"][i]["hash"] != P2["segs"][i]["hash"]]
check("changing an overlay changes the hash of the segment it is in and no other", diff == [2], diff)
check("...and not the audio", P1["audio_hash"] == P2["audio_hash"])
music = os.path.join(HOME, "music.wav")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=10:sample_rate=48000", "-c:a", "pcm_s16le", music], check=True)
server.add_audio(0.0, 7.0, path=music, volume_db=-6.0, fade_in_s=0.0, fade_out_s=0.0)
P3 = segments.plan(server.load(), server.HOME)
check("adding sound changes the audio hash and no video segment", P3["audio_hash"] != P2["audio_hash"] and [s["hash"] for s in P3["segs"]] == [s["hash"] for s in P2["segs"]])
server.update_op(text_id, {"start": 0.2})
P4 = segments.plan(server.load(), server.HOME)
diff = [i for i in range(n_seg) if P3["segs"][i]["hash"] != P4["segs"][i]["hash"]]
check("moving an overlay changes the segment it left and the one it entered", diff == [0, 2], diff)
server.cut_clip(0, 3.0)
P5 = segments.plan(server.load(), server.HOME)
check("cutting the first clip: the timeline is 1 s shorter (163 frames = 3 segments and a short one) and the segments after the cut changed",
      len(P5["segs"]) == 4 and P5["segs"][-1]["b"] - P5["segs"][-1]["a"] == 13 and P5["segs"][2]["hash"] != P4["segs"][2]["hash"], [(s_["a"], s_["b"]) for s_ in P5["segs"]])

# ------------------------------------------------------------------------------------------------ the HTTP surface
base_project()
v = vw.start(server.HOME)
check("the viewer listens on 127.0.0.1 and the URL carries a private token", v.url.startswith("http://127.0.0.1:") and len(v.token) == 16, v.url)
code, _, _ = get(f"http://127.0.0.1:{v.port}/plan.json")
check("without the token there is nothing (404)", code == 404, code)
code, _, _ = get(f"http://127.0.0.1:{v.port}/{'0' * 16}/plan.json")
check("with a wrong token there is nothing (404)", code == 404, code)
code, body, _ = get(v.url)
check("the page is served", code == 200 and b"hls.min.js" in body and b"Visor" in body)
code, body, hd = get(v.url + "vendor/hls.min.js")
check("hls.js is served from this machine (no CDN)", code == 200 and len(body) > 100_000 and "javascript" in hd.get("Content-Type", ""))
t0 = time.perf_counter()
code, body, _ = get(v.url + "plan.json?since=-1")
plan = json.loads(body)
check("plan.json describes the edit: revision, master playlist, timeline svg, ops with ids", code == 200 and plan["segments"] == 4 and plan["svg"].startswith("<svg") and all(o["id"] for o in plan["ops"]) and plan["master"].endswith(".m3u8"), plan.keys())
master = f"http://127.0.0.1:{v.port}{plan['master']}"
code, mtxt, _ = get(master)
mtxt = mtxt.decode()
check("the master playlist names the video playlist (and an audio rendition because the clips have sound)", code == 200 and "#EXT-X-STREAM-INF" in mtxt and 'TYPE=AUDIO' in mtxt, mtxt)
vurl = f"http://127.0.0.1:{v.port}" + [ln for ln in mtxt.splitlines() if ln.startswith("/")][-1]
code, vtxt, _ = get(vurl)
vtxt = vtxt.decode()
segs = [ln for ln in vtxt.splitlines() if ln.endswith(".ts")]
check("the video playlist lists every segment from the start, VOD, with exact durations", code == 200 and len(segs) == 4 and "#EXT-X-ENDLIST" in vtxt and vtxt.count("#EXTINF:2.000000") >= 3, vtxt)
cold = v.renderer.stats["segments_rendered"]
t0 = time.perf_counter()
code, ts0, hd = get(f"http://127.0.0.1:{v.port}{segs[0]}")
first = time.perf_counter() - t0
check(f"the first segment is ready {first:.2f} s after the request (worker already started by plan.json)", code == 200 and len(ts0) > 1000 and first < 2.0, first)
check("segments are immutable (cacheable forever: their name is their hash)", "immutable" in hd.get("Cache-Control", ""))
p0 = os.path.join(HOME, "seg0.ts"); open(p0, "wb").write(ts0)
j = probe(p0)
check("a segment is H.264 video only, 2 s, starting at its place on the timeline", [s["codec_type"] for s in j["streams"]] == ["video"] and abs(float(j["format"]["duration"]) - 2.0) < 0.05 and abs(float(j["format"]["start_time"])) < 0.01, j)
code, ts2, _ = get(f"http://127.0.0.1:{v.port}{segs[2]}")
p2 = os.path.join(HOME, "seg2.ts"); open(p2, "wb").write(ts2)
check("segment 2 starts at 4.0 s (continuous timestamps, so segments join without a seam)", abs(float(probe(p2)["format"]["start_time"]) - 4.0) < 0.02, probe(p2)["format"])
for s in segs:
    get(f"http://127.0.0.1:{v.port}{s}")
check("scrubbing: segments can be asked for in any order (each is made when asked)", v.renderer.stats["segments_rendered"] - cold == 4, v.renderer.stats)

# ------------------------------------------------------------------------------------------------ what plays is the edit
full = f"ffmpeg -v error -i {master} -f null -"
r = subprocess.run(full.split(), capture_output=True, text=True)
check("ffmpeg plays the whole HLS stream (video + audio rendition) without errors", r.returncode == 0 and not r.stderr.strip(), r.stderr[-200:])
j = probe(master, count=True)
vid = next(s for s in j["streams"] if s["codec_type"] == "video")
check("the stream has exactly the timeline's frames (200 - 12 of the 0.5 s crossfade = 188)", int(vid["nb_read_frames"]) == 188 and any(s["codec_type"] == "audio" for s in j["streams"]), vid)
stt = server.load()
server.bind(stt)
total = plan["segments"]
want = review._frames(stt, [1.0], 1.0)[0]
png = subprocess.run(["ffmpeg", "-v", "error", "-i", master, "-vf", "select=eq(n\\,25)", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
mse = sum((a - b) ** 2 for a, b in zip(png[::97], want[2][::97])) / max(1, len(png[::97]))
check(f"the viewer's frame at 1.0 s is the edit's frame (compressed: mean squared error {mse:.0f} < 150)", len(png) == len(want[2]) and mse < 150, (len(png), len(want[2]), mse))

# ------------------------------------------------------------------------------------------------ re-rendering only what an edit touches
before = v.renderer.stats["segments_rendered"]
server.update_op(next(o["id"] for o in server.get_timeline()["ops"] if o["op"] == "text"), {"text": "edited in segment two"})
code, body, _ = get(v.url + f"plan.json?since={plan['revision']}")
plan2 = json.loads(body)
code, mtxt, _ = get(f"http://127.0.0.1:{v.port}{plan2['master']}")
vurl2 = f"http://127.0.0.1:{v.port}" + [ln for ln in mtxt.decode().splitlines() if ln.startswith("/")][-1]
segs2 = [ln for ln in get(vurl2)[1].decode().splitlines() if ln.endswith(".ts")]
for s in segs2:
    get(f"http://127.0.0.1:{v.port}{s}")
new = v.renderer.stats["segments_rendered"] - before
check("an edit inside one segment renders exactly that segment again; the other three come from disk", new == 1 and len(set(segs) & set(segs2)) == 3, (new, len(set(segs) & set(segs2))))
check("the viewer follows the edit: a new revision and a new playlist", plan2["revision"] > plan["revision"] and plan2["hash"] != plan["hash"])

# long poll: it waits for the next change
def edit_later():
    time.sleep(1.2)
    server.add_text("later", 0.5, 0.5, position="top")


rev_now = plan2["revision"]
th = threading.Thread(target=edit_later); th.start()
t0 = time.perf_counter()
code, body, _ = get(v.url + f"plan.json?since={rev_now}")
waited = time.perf_counter() - t0
th.join()
check(f"plan.json waits for the next change ({waited:.1f} s) and answers with it", 1.0 < waited < 5 and json.loads(body)["revision"] > rev_now, waited)

# ------------------------------------------------------------------------------------------------ continuous audio, no seams
server.new_project(640, 360, 25)
silent = os.path.join(HOME, "silent.mp4")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=640x360:r=25:d=10", "-c:v", "libx264", "-pix_fmt", "yuv420p", silent], check=True)
server.import_clip(silent, "Q")
server.add_clip("Q", 0, 10)
server.add_audio(0.0, 10.0, path=music, volume_db=-6.0, fade_in_s=0.0, fade_out_s=0.0)
_, body, _ = get(v.url + "plan.json?since=-1")
mp = f"http://127.0.0.1:{v.port}{json.loads(body)['master']}"
pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", mp, "-vn", "-f", "s16le", "-ac", "1", "-ar", "48000", "-"], capture_output=True).stdout
import array  # noqa: E402
x = array.array("h"); x.frombytes(pcm[: len(pcm) // 2 * 2])
n = len(x)
d2 = [abs(x[i] - 2 * x[i - 1] + x[i - 2]) / 32768 for i in range(2, n)]
amp = max(abs(s) for s in x) / 32768
bound = amp * (2 * 3.14159 * 440 / 48000) ** 2
worst = max(d2[1500:-1500])                                   # skip the encoder's start/end
check(f"{n / 48000:.1f} s of sound, 5 segments of it: no click at the joins (second difference {worst:.4f} vs {bound:.4f} for the pure tone)", n / 48000 > 9.5 and worst < bound * 6 + 0.01, (worst, bound, n))
bounds_at = [int(k * 2.0 * 48000) for k in range(1, 5)]
worst_join = max(max(d2[b - 40:b + 40]) for b in bounds_at)
check(f"...specifically at the 2 s, 4 s, 6 s, 8 s marks ({worst_join:.4f})", worst_join < bound * 6 + 0.01, worst_join)

# ------------------------------------------------------------------------------------------------ speed: a one-minute edit
server.new_project(1280, 720, 25)
server.import_clip(A, "A")
for _ in range(10):
    server.add_clip("A", 0, 6)
server.add_lower_third("Ana", "Directora", 3, 4)
server.add_text("subtitle-ish", 10, 3, position="bottom")
server.add_graphic("frame", 0, 60)
v2 = vw.Viewer(server.HOME)
_, body, _ = get(v2.url + "plan.json?since=-1")
p60 = json.loads(body)
master60 = f"http://127.0.0.1:{v2.port}{p60['master']}"
vurl60 = f"http://127.0.0.1:{v2.port}" + [ln for ln in get(master60)[1].decode().splitlines() if ln.startswith("/")][-1]
segs60 = [ln for ln in get(vurl60)[1].decode().splitlines() if ln.endswith(".ts")]
t0 = time.perf_counter()
for s in segs60:
    get(f"http://127.0.0.1:{v2.port}{s}")
spent = time.perf_counter() - t0
check(f"a 60 s edit at 540p: all {len(segs60)} segments in {spent:.1f} s = {60 / spent:.1f}x real time (>= 1.0x needed to play without stalls)", len(segs60) == 30 and 60 / spent >= 1.0, spent)
print(f"  segment render times: median {sorted(v2.renderer.stats['segment_seconds'])[15]:.2f} s for 2 s of video")

# ------------------------------------------------------------------------------------------------ the page in a browser
try:
    from playwright.sync_api import sync_playwright
    chrome = next((p for p in ("/opt/pw-browsers/chromium-1194/chrome-linux/chrome",) if os.path.exists(p)), None)
except ImportError:
    chrome = None
if chrome is None:
    print("SKIP the browser checks: playwright or Chromium not available")
else:
    base_project()
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
        pg = b.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(v.url)
        pg.wait_for_selector("#ops li", timeout=60000)
        rev_txt = pg.inner_text("#rev")
        n_ops = pg.locator("#ops li").count()
        check("the page lists the edits with their ids and the revision", n_ops == len(server.get_timeline()["ops"]) and "revisión" in rev_txt and re.search(r"op_[0-9a-f]{6}", pg.inner_text("#ops")), (n_ops, rev_txt))
        check("the page draws the timeline", pg.locator("#svg svg").count() == 1)
        before = pg.inner_text("#rev")
        server.add_text("from the agent", 1.0, 1.0, position="top")
        pg.wait_for_function("(b) => document.getElementById('rev').textContent !== b", arg=before, timeout=30000)
        check("the page follows the edit by itself (no reload): the list gains the new edit", pg.locator("#ops li").count() == n_ops + 1)
        check("no JavaScript errors", not errors, errors)
        h264 = pg.evaluate("() => document.createElement('video').canPlayType('video/mp4; codecs=\"avc1.42E01E\"')")
        print(f"  this Chromium can play H.264: {bool(h264)} - " + ("playback is checked" if h264 else "so actual playback in a browser is NOT verified here (ffmpeg plays the same HLS: see above)"))
        if h264:
            pg.evaluate("() => document.getElementById('v').play()")
            time.sleep(4)
            check("the video advances", pg.evaluate("() => document.getElementById('v').currentTime") > 1.0)
        b.close()

print(f"\n{ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
