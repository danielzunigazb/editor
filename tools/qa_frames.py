#!/usr/bin/env python3
"""Motion QA of a rendered video: reads every frame at low resolution and reports what a viewer would notice.

  hard_cuts   one or two frames that differ from the previous one by more than --cut (mean absolute difference, 0-255 scale): a cut with no transition
  fast_motion a run of 3+ consecutive frames above --cut: the whole picture moving fast (a slide transition, a whip pan), not a cut
  blips       one frame that differs from both neighbours while those two are alike (A, B, A): a flash or a flicker
  frozen      runs of identical frames longer than --freeze seconds (a stuck frame or an unintended hold)
  steps       the largest frame-to-frame change that is NOT a hard cut, with its time (how smooth the busiest transition is)
Usage: tools/qa_frames.py video.mp4 [--cut 14] [--freeze 2.0] [--json]. Pure Python (no numpy); a 14 s clip takes about a second."""
import argparse, json, subprocess

GW, GH = 64, 36


def read_frames(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", path], capture_output=True, text=True)
    num, den = (p.stdout.strip() or "25/1").split("/")
    fps = float(num) / float(den)
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", f"scale={GW}:{GH}:flags=area,format=gray", "-f", "rawvideo", "-"], capture_output=True)
    if r.returncode or not r.stdout:
        raise SystemExit(f"cannot decode {path}: {r.stderr.decode(errors='replace')[-200:]}")
    n = GW * GH
    data = r.stdout
    return [data[i:i + n] for i in range(0, len(data) - n + 1, n)], fps


def mad(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def analyse(path, cut=14.0, freeze=2.0):
    frames, fps = read_frames(path)
    d = [mad(frames[i - 1], frames[i]) for i in range(1, len(frames))]          # d[i-1] = change into frame i
    big = [i for i, v in enumerate(d, 1) if v > cut]
    runs, cur = [], []
    for i in big:
        if cur and i != cur[-1] + 1:
            runs.append(cur); cur = []
        cur.append(i)
    if cur:
        runs.append(cur)
    cuts = [i for r in runs if len(r) < 3 for i in r]
    fast = [r for r in runs if len(r) >= 3]
    blips = []
    for i in range(1, len(frames) - 1):
        into, out_, around = d[i - 1], d[i], mad(frames[i - 1], frames[i + 1])
        if into > 6 and out_ > 6 and around < 0.35 * min(into, out_) and i not in cuts:
            blips.append(i)
    frozen, run = [], 0
    for i, v in enumerate(d, 1):
        run = run + 1 if v < 0.004 else 0
        if run == int(freeze * fps):
            frozen.append(i - run)
    smooth = [(v, i) for i, v in enumerate(d, 1) if i not in cuts and not any(i in r for r in fast)]
    step = max(smooth) if smooth else (0.0, 0)
    return {"frames": len(frames), "fps": fps, "duration_s": round(len(frames) / fps, 2),
            "hard_cuts": [round(i / fps, 2) for i in cuts], "fast_motion": [[round(r[0] / fps, 2), round(r[-1] / fps, 2)] for r in fast], "blips": [round(i / fps, 2) for i in blips],
            "frozen_from_s": [round(i / fps, 2) for i in frozen], "largest_smooth_step": {"mad": round(step[0], 2), "t_s": round(step[1] / fps, 2)}}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--cut", type=float, default=14.0)
    ap.add_argument("--freeze", type=float, default=2.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = analyse(a.video, a.cut, a.freeze)
    print(json.dumps(res) if a.json else "\n".join(f"{k}: {v}" for k, v in res.items()))
