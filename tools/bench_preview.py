#!/usr/bin/env python3
"""Latency of what an agent uses to LOOK at an edit: get_still (cold = first call after an edit, hot = same edit again), get_contact_sheet(6) and
render_preview, at 720p / 1080p / 4K sources. Calls the server's tool functions in-process (same code path as MCP).
Usage: tools/bench_preview.py [--res 720p,1080p,4k] [--out FILE] [--check BASELINE.json] [--runs N]
--out writes the numbers (default tests_data/bench_baseline.json when --save is given); --check compares against a file and exits 1 over a threshold."""
import argparse, json, os, statistics, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from mltedit.config import S  # noqa: E402

SOURCES = {"720p": ("media", ["clip_a.mp4", "clip_b.mp4"], (1280, 720, 25)),
           "1080p": ("media_1080", ["cam_h264_1080p30.mp4", "cam_h264_1080p30.mp4"], (1920, 1080, 30)),
           "4k": ("media_4k", ["cam_h264_4k30.mp4", "cam_h264_4k30.mp4"], (3840, 2160, 30))}


def child(res):
    """Run inside a fresh process: build the project, time the calls, print one JSON line."""
    folder, files, (w, h, fps) = SOURCES[res]
    os.environ["MLT_EDITOR_HOME"] = tempfile.mkdtemp(prefix=f"bench_{res}_")
    sys.path.insert(0, HERE)
    import server  # noqa: E402

    def t(fn, *a, **k):
        t0 = time.perf_counter(); r = fn(*a, **k); return (time.perf_counter() - t0), r

    server.new_project(w, h, fps)
    for i, f in enumerate(files):
        server.import_clip(os.path.join(HERE, folder, f), "AB"[i])
    server.add_clip("A", 0.0, 4.0)
    server.add_clip("B", 0.0, 4.0)
    server.crossfade(0, 0.5)
    server.add_graphic("frame", 0.0, 7.0)
    server.add_lower_third("Valeria Montoya", "Directora", 1.0, 3.0)
    server.add_text("Un texto de prueba", 4.5, 2.0, position="center")
    out = {"resolution": f"{w}x{h}@{fps}"}
    runs = int(os.environ.get("BENCH_RUNS", "3"))
    cold, hot, sheet = [], [], []
    for r in range(runs):
        server.add_text(f"edit {r}", 5.0 + 0.1 * r, 0.5, position="top")           # a new edit: the next still is cold
        cold.append(t(server.get_still, 1.5)[0])
        hot.append(statistics.median(t(server.get_still, 1.5 + 0.2 * k)[0] for k in range(5)))   # other times of the same edit
        sheet.append(t(server.get_contact_sheet, 6)[0])
    out["still_cold_s"], out["still_hot_s"], out["contact_sheet_s"] = (round(statistics.median(x), 3) for x in (cold, hot, sheet))
    out["render_preview_s"] = round(t(server.render_preview)[0], 2)
    print("RESULT " + json.dumps(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--res", default="720p,1080p,4k")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--save", action="store_true", help="write tests_data/bench_baseline.json")
    ap.add_argument("--out", default="")
    ap.add_argument("--check", default="", help="baseline JSON to compare with (fail if a time is more than 1.25x worse)")
    ap.add_argument("--child", default="")
    a = ap.parse_args()
    if a.child:
        return child(a.child)
    results = {}
    for res in a.res.split(","):
        folder = os.path.join(HERE, SOURCES[res][0])
        if not all(os.path.exists(os.path.join(folder, f)) for f in SOURCES[res][1]):
            print(f"{res}: media missing in {folder}, skipped"); continue
        r = subprocess.run([sys.executable, __file__, "--child", res], capture_output=True, text=True, timeout=1800, env={**os.environ, "BENCH_RUNS": str(a.runs)})
        line = next((ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")), None)
        if not line:
            print(f"{res}: FAILED\n{(r.stderr or r.stdout)[-500:]}"); continue
        results[res] = json.loads(line[7:])
        print(res, results[res], flush=True)
    meta = {"cpus": os.cpu_count(), "note": "median of runs; cold = first still after an edit, hot = other times of the same edit"}
    dest = a.out or (os.path.join(S.data_root, "tests_data", "bench_baseline.json") if a.save else "")
    if dest:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        json.dump({"meta": meta, "results": results}, open(dest, "w"), indent=1)
        print("wrote", dest)
    if a.check:
        base = json.load(open(a.check))["results"]
        bad = [(res, k, base[res][k], v) for res, r in results.items() if res in base for k, v in r.items() if k.endswith("_s") and v > 1.25 * base[res][k] + 0.05]
        for b in bad:
            print("REGRESSION", b)
        sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
