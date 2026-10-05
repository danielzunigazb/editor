"""Motion QA of a rendered video: reads every frame at low resolution and reports what a viewer would notice.

  hard_cuts   one or two frames that differ from the previous one by more than `cut` (mean absolute difference, 0-255 scale): a cut with no transition
  fast_motion a run of 3+ consecutive frames above `cut`: the whole picture moving fast (a slide transition, a whip pan), not a cut
  blips       one frame that differs from both neighbours while those two are alike (A, B, A): a flash or a flicker
  frozen      runs of identical frames longer than `freeze` seconds (a stuck frame or an unintended hold)
  steps       the largest frame-to-frame change that is NOT a hard cut, with its time (how smooth the busiest transition is)
Pure Python (no numpy); a 14 s clip takes about a second. `check()` is what export / render_preview run on their result."""
import subprocess

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


def source_has_cut(entries, t, cut, half=0.3):
    """True if the SOURCE footage itself has a hard cut at the moment of the timeline that is at time `t`: a cut the editor did not make and cannot remove (a
    multi-shot talk, a screen recording). `entries`: [{"path", "in", "start", "dur"}] in seconds. Only a window of ±`half` s of the source is decoded."""
    e = next((x for x in entries if x["start"] - 1e-6 <= t < x["start"] + x["dur"]), None)
    if e is None:
        return False
    src_t = e["in"] + (t - e["start"])
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, src_t - half):.3f}", "-i", e["path"], "-t", f"{2 * half:.3f}", "-an",
                        "-vf", f"scale={GW}:{GH}:flags=area,format=gray", "-f", "rawvideo", "-"], capture_output=True)
    n = GW * GH
    fr = [r.stdout[i:i + n] for i in range(0, len(r.stdout) - n + 1, n)]
    return any(mad(a, b) > cut * 0.6 for a, b in zip(fr, fr[1:]))


def check(path, expected_cuts_s=(), cut=14.0, freeze=2.0, tol_s=0.12, entries=None, max_source_checks=80):
    """The QA of a video, judged against what the timeline says should be there: a hard cut where two clips meet with no transition is intended; a
    cut anywhere else, a one-frame flash, or a frozen stretch is a finding. Returns {ok, findings: [...], ...measurements}."""
    r = analyse(path, cut, freeze)
    unexpected = [t for t in r["hard_cuts"] if not any(abs(t - e) <= tol_s for e in expected_cuts_s)]
    in_source = []
    if entries and len(unexpected) <= max_source_checks:     # a cut the footage already had is not the editor's doing
        in_source = [t for t in unexpected if source_has_cut(entries, t, cut)]
        unexpected = [t for t in unexpected if t not in in_source]
    findings = [f"unexpected hard cut at {t:g}s" for t in unexpected] + [f"one-frame flash/flicker at {t:g}s" for t in r["blips"]]
    notes = [f"picture still from {t:g}s for over {freeze:g}s (fine for a title card or a static shot; a stuck frame otherwise)" for t in r["frozen_from_s"]]
    if in_source:
        notes.append(f"{len(in_source)} cut(s) at {', '.join(f'{t:g}' for t in in_source[:6])}{'...' if len(in_source) > 6 else ''} s are already in the source footage (not an editing error)")
    return {"ok": not findings, "findings": findings, **({"notes": notes} if notes else {}), "hard_cuts": r["hard_cuts"], "intended_cuts": [round(e, 2) for e in expected_cuts_s], "fast_motion": r["fast_motion"],
            "largest_smooth_step": r["largest_smooth_step"]}


# ---------------------------------------------------------------------------------------------------------------------------------------------------------
# Audio/video sync of an export

ENV_HZ = 500          # the loudness envelopes are compared at this rate: 2 ms per step


def _envelope(path, start, dur):
    """Loudness envelope (ENV_HZ) of `dur` s of the audio of `path` from `start` s, or None if there is no audio / it cannot be read."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-i", path, "-t", f"{dur:.3f}", "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], capture_output=True)
    if r.returncode or len(r.stdout) < 8000 * 2 * dur * 0.5:
        return None
    import array
    a = array.array("h")
    a.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
    blk = 8000 // ENV_HZ
    env = [sum(abs(x) for x in a[i:i + blk]) / blk for i in range(0, len(a) - blk + 1, blk)]
    mean = sum(env) / len(env)
    return [e - mean for e in env]


def lag_ms(ref, test, max_lag_ms=500, env_hz=ENV_HZ):
    """How late `test` is relative to `ref` (ms, negative = early) by cross-correlation of two envelopes, and the normalised correlation at that lag (0-1)."""
    n = min(len(ref), len(test))
    ref, test = ref[:n], test[:n]
    norm = (sum(x * x for x in ref) * sum(x * x for x in test)) ** 0.5
    if n < 50 or norm == 0:
        return None, 0.0
    best, best_lag = -2.0, 0
    m = int(max_lag_ms * env_hz / 1000)
    for lag in range(-m, m + 1):
        lo, hi = max(0, -lag), min(n, n - lag)
        c = sum(ref[i] * test[i + lag] for i in range(lo, hi))
        if c > best:
            best, best_lag = c, lag
    return best_lag * 1000 / env_hz, round(best / norm, 3)


def _motion(path, start, dur, fps=25):
    """Motion envelope of `dur` s of the picture of `path` from `start` s: mean absolute difference between consecutive frames (64x36 grey), at `fps`
    (resampled, so a 50 fps source and a 25 fps export are comparable). None if it cannot be read."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-i", path, "-t", f"{dur:.3f}", "-an", "-vf", f"fps={fps},scale={GW}:{GH}:flags=area,format=gray",
                        "-f", "rawvideo", "-"], capture_output=True)
    n = GW * GH
    fr = [r.stdout[i:i + n] for i in range(0, len(r.stdout) - n + 1, n)]
    if r.returncode or len(fr) < fps * dur * 0.5:
        return None
    env = [mad(a, b) for a, b in zip(fr, fr[1:])]
    mean = sum(env) / len(env)
    return [e - mean for e in env]


def av_offset(export_path, entries, window_s=6.0, min_corr=0.6):
    """Is the sound of the export where the picture is? Takes the first timeline entry that has audio and is long enough and compares, for the same window,
    the source with the export on both sides: the sound (loudness envelope) and the picture (motion envelope). av_offset_ms = how late the sound is against
    the picture in the export minus how late it is in the source (so a source that was already out of sync is not blamed on the editor); positive = sound late.
    Also reports audio_lag_ms / video_lag_ms (each against the source). Returns {"av_offset_ms", "correlation", ...} or {"av_unmeasured": why}. The picture side
    needs motion in the window: with a still picture only the sound is compared (video_lag_ms absent, said in `note`). With music over the clip the sound
    correlation drops below min_corr and nothing is claimed. `entries`: [{"path", "in", "start", "dur", "has_audio"}]."""
    for e in entries:
        if not e.get("has_audio") or e["dur"] < 2.0:
            continue
        w = min(window_s, e["dur"] - 0.6)
        ref = _envelope(e["path"], e["in"] + 0.3, w)
        test = _envelope(export_path, e["start"] + 0.3, w)
        if ref is None or test is None:
            continue
        lag, corr = lag_ms(ref, test)
        if lag is None or corr < min_corr:
            return {"av_unmeasured": f"the export's sound does not match the source's closely enough to measure (correlation {corr}): music, a crossfade or silence over that stretch"}
        out = {"audio_lag_ms": round(lag), "correlation": corr}
        vref, vtest = _motion(e["path"], e["in"] + 0.3, w), _motion(export_path, e["start"] + 0.3, w)
        vlag = vcorr = None
        if vref and vtest and (sum(x * x for x in vref) / len(vref)) > 0.1:       # there is motion to follow (the correlation gate below does the real judging)
            vlag, vcorr = lag_ms(vref, vtest, max_lag_ms=480, env_hz=25)
        if vlag is not None and vcorr >= min_corr:
            out.update(video_lag_ms=round(vlag), av_offset_ms=round(lag - vlag))
        else:
            out.update(av_offset_ms=round(lag), note="the picture had too little motion in that stretch to follow: only the sound was compared with the source (a shifted picture would not show)")
        return out
    return {"av_unmeasured": "no clip with audio long enough to compare"}
