#!/usr/bin/env python3
"""Turn detector output (detect.py json) into `apply_ops` callouts: one pinned label per tracked object.
No dependencies. Usage:
  python3 annotate.py <spec.json> <out_ops.json> [--src-in 0] [--src-out 66.64] [--max-concurrent 3]
spec.json: {"detections": ["detections.json", ...],
            "targets": [{"queries": ["building under construction"], "title": "Edificio", "subtitle": "en construcción",
                         "max": 3, "min_len": 4, "min_score": 0.2, "max_area": 0.3}, ...]}
With --video (run under the detector venv, needs OpenCV) every selected track is refined: the anchor is locked to the
structure with optical flow (similarity transform of the tracked features, so it follows zoom/rotation too) between the
1 fps detections, smoothed, and cut as soon as the structure is lost or the detector stops seeing it.
Detections are matched into tracks frame to frame (nearest box, same label group), smoothed, ranked by length x score, and
written as [t_s, x, y] points in TIMELINE seconds (source time minus --src-in)."""
import argparse, json, math, sys


def load(paths, queries):
    by_t = {}
    for p in paths:
        d = json.load(open(p))
        for q in queries:
            for t, cx, cy, w, h, s in d["tracks"].get(q, []):
                by_t.setdefault(t, []).append((cx, cy, w, h, s))
    return by_t


def track(by_t, min_score, max_area, gap_max=1.6, speed=0.16):
    """Greedy nearest-box association. A box may move `speed` (fraction of the frame) per second, plus a base slack."""
    done, active = [], []
    for t in sorted(by_t):
        dets = sorted((d for d in by_t[t] if d[4] >= min_score and d[2] * d[3] <= max_area), key=lambda d: -d[4])
        taken = set()
        for d in dets:
            best, bd = None, 1e9
            for i, tr in enumerate(active):
                if i in taken:
                    continue
                lt, lx, ly = tr[-1][0], tr[-1][1], tr[-1][2]
                dt = t - lt
                dist = math.hypot(d[0] - lx, d[1] - ly)
                if 0 < dt <= gap_max and dist <= 0.05 + speed * dt and dist < bd:
                    best, bd = i, dist
            if best is None:
                active.append([(t, d[0], d[1], d[2], d[3], d[4])]); taken.add(len(active) - 1)
            else:
                active[best].append((t, d[0], d[1], d[2], d[3], d[4])); taken.add(best)
        still = []
        for tr in active:
            (done if t - tr[-1][0] > gap_max else still).append(tr)
        active = still
    return done + active


def smooth(tr):
    """3-point moving average of the anchor (detector boxes jitter by a few pixels from frame to frame)."""
    out = []
    for i, p in enumerate(tr):
        a, b = tr[max(0, i - 1)], tr[min(len(tr) - 1, i + 1)]
        out.append((p[0], round((a[1] + p[1] + b[1]) / 3, 4), round((a[2] + p[2] + b[2]) / 3, 4)))
    return out


# ------------------------------------------------------------------ optical-flow lock (needs cv2 + numpy)
EXTEND_S = 2.0                           # the lock may outlive the first/last detection of its track by this long
TRACK_FPS, SCALE = 25.0, 0.5            # tracked at 25 fps on a half-size grey image: ~5 ms per step, plenty for a drone


def load_frames(video, t0, t1):
    import cv2
    cap = cv2.VideoCapture(video)
    vfps = cap.get(cv2.CAP_PROP_FPS)
    step = max(1, int(round(vfps / TRACK_FPS)))
    first = max(0, int(max(0.0, t0) * vfps)) // step * step
    cap.set(cv2.CAP_PROP_POS_FRAMES, first)
    frames, i = {}, first
    while i / vfps <= t1:
        ok, bgr = cap.read()
        if not ok:
            break
        if (i - first) % step == 0:
            g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            frames[round(i / vfps * TRACK_FPS)] = cv2.resize(g, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)
        i += 1
    return frames


def lk_step(prev, cur, a, bw, bh):
    """Move anchor `a` (x, y px) and box size from frame `prev` to `cur`. None when the structure cannot be followed."""
    import cv2, numpy as np
    H, W = prev.shape
    mask = np.zeros_like(prev)
    x0, y0, x1, y1 = int(max(0, a[0] - bw / 2)), int(max(0, a[1] - bh / 2)), int(min(W, a[0] + bw / 2)), int(min(H, a[1] + bh / 2))
    if x1 - x0 < 12 or y1 - y0 < 12:
        return None
    mask[y0:y1, x0:x1] = 255
    p0 = cv2.goodFeaturesToTrack(prev, 150, 0.01, 4, mask=mask)
    if p0 is None or len(p0) < 12:
        return None
    lk = dict(winSize=(21, 21), maxLevel=3, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
    p1, st, _ = cv2.calcOpticalFlowPyrLK(prev, cur, p0, None, **lk)
    pb, stb, _ = cv2.calcOpticalFlowPyrLK(cur, prev, p1, None, **lk)
    ok = (st[:, 0] == 1) & (stb[:, 0] == 1) & (np.linalg.norm(pb - p0, axis=2)[:, 0] < 1.0)
    if ok.sum() < 10:
        return None
    M, inl = cv2.estimateAffinePartial2D(p0[ok], p1[ok], method=cv2.RANSAC, ransacReprojThreshold=2.5)
    if M is None or int(inl.sum()) < 8:
        return None
    s = float(math.sqrt(abs(M[0, 0] * M[1, 1] - M[0, 1] * M[1, 0])))
    if not 0.8 < s < 1.25:
        return None
    na = M @ np.array([a[0], a[1], 1.0])
    if not (4 < na[0] < W - 4 and 4 < na[1] < H - 4):
        return None
    return (float(na[0]), float(na[1])), bw * s, bh * s


def supported(dets, t, ax, ay):
    """True if some detection within +-1.2 s still contains the anchor (box grown 40%): the detector still sees it."""
    for dt_, cx, cy, w, h, _ in dets:
        if abs(dt_ - t) <= 1.2 and abs(ax - cx) <= w * 0.7 and abs(ay - cy) <= h * 0.7:
            return True
    return False


def refine(frames, tr, dets, W_, H_):
    """Dense, locked path [(t, x, y)] (normalised) for detection track `tr`, or None if it cannot be followed."""
    seed = max(tr, key=lambda p: p[5])
    k0 = round(seed[0] * TRACK_FPS)
    if k0 not in frames:
        return None
    h_, w_ = next(iter(frames.values())).shape
    path = {k0: ((seed[1] * w_, seed[2] * h_), seed[3] * w_, seed[4] * h_)}
    k_lo, k_hi = max(0, round((tr[0][0] - EXTEND_S) * TRACK_FPS)), round((tr[-1][0] + EXTEND_S) * TRACK_FPS)    # never wander far past what the detector saw
    for direction in (1, -1):
        k, state, lost_since = k0, path[k0], None
        while True:
            nk = k + direction
            if nk not in frames or k not in frames or not k_lo <= nk <= k_hi:
                break
            r = lk_step(frames[k], frames[nk], *state)
            if r is None:
                break
            (ax, ay), bw, bh = r
            if supported(dets, nk / TRACK_FPS, ax / w_, ay / h_):
                lost_since = None
            else:
                lost_since = lost_since if lost_since is not None else nk
                if abs(nk - lost_since) / TRACK_FPS > 1.2:      # unsupported for too long: the structure is no longer resolvable
                    break
            path[nk], k, state = r, nk, r
        # drop the unsupported tail
        if lost_since is not None:
            for kk in [x for x in path if (x - lost_since) * direction >= 0 and x != k0]:
                del path[kk]
    ks = sorted(path)
    if len(ks) < 8:
        return None
    xs = [path[k][0][0] / w_ for k in ks]; ys = [path[k][0][1] / h_ for k in ks]
    def ma(v, r=2):                                         # light moving average over +-r samples (80 ms): kills pixel jitter
        return [sum(v[max(0, i - r):i + r + 1]) / len(v[max(0, i - r):i + r + 1]) for i in range(len(v))]
    xs, ys = ma(xs), ma(ys)
    keep = max(1, int(round(TRACK_FPS / 10)))               # 10 keyframes per second are enough for linear interpolation
    pts = [(ks[i] / TRACK_FPS, round(xs[i], 4), round(ys[i], 4)) for i in range(0, len(ks), keep)]
    if pts[-1][0] != ks[-1] / TRACK_FPS:
        pts.append((ks[-1] / TRACK_FPS, round(xs[-1], 4), round(ys[-1], 4)))
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec"); ap.add_argument("out")
    ap.add_argument("--src-in", type=float, default=0.0); ap.add_argument("--src-out", type=float, default=1e9)
    ap.add_argument("--max-concurrent", type=int, default=3)
    ap.add_argument("--min-agree", type=float, default=0.5, help="drop a locked track unless its anchor is inside the detector's box in at least this fraction of the detections")
    ap.add_argument("--min-dx", type=float, default=0.16, help="two labels on screen together must differ by at least this much in x (fraction of the frame) OR ...")
    ap.add_argument("--min-dy", type=float, default=0.07, help="... this much in y; otherwise the lower-scored one is dropped")
    ap.add_argument("--video", default="", help="lock tracks to the structure with optical flow (needs the detector venv)")
    a = ap.parse_args()
    spec = json.load(open(a.spec))
    sel = []                                                   # (score, detection track, target, all detections of that group)
    for tgt in spec["targets"]:
        by_t = load(tgt.get("detections", spec["detections"]), tgt["queries"])
        by_t = {t: v for t, v in by_t.items() if a.src_in <= t <= a.src_out}
        tracks = []
        for tr in track(by_t, tgt.get("min_score", 0.2), tgt.get("max_area", 0.3)):
            dur = tr[-1][0] - tr[0][0]
            ms = sum(p[5] for p in tr) / len(tr)
            if dur >= tgt.get("min_len", 3.0) and len(tr) >= 3:
                tracks.append((dur * ms, tr))
        tracks.sort(key=lambda x: -x[0])
        dets = [(t, *d[:4]) + (d[4],) for t, v in by_t.items() for d in v if d[4] >= tgt.get("min_score", 0.2) * 0.8]
        for score, tr in tracks[: tgt.get("max", 1)]:
            sel.append((score, tr, tgt, dets))
    frames = {}
    if a.video and sel:
        frames = load_frames(a.video, min(s[1][0][0] for s in sel) - 1.5, max(s[1][-1][0] for s in sel) + 1.5)
    cands, stats = [], []
    for score, tr, tgt, dets in sel:
        pts_src = smooth(tr)
        if frames:
            locked = refine(frames, tr, dets, 1.0, 1.0)
            if locked:
                inside = [1 for t, cx, cy, w, h, _ in tr if min(locked, key=lambda p: abs(p[0] - t))[1:] and
                          abs(min(locked, key=lambda p: abs(p[0] - t))[1] - cx) <= w / 2 and abs(min(locked, key=lambda p: abs(p[0] - t))[2] - cy) <= h / 2]
                stats.append((tgt["title"], len(tr), len(inside), pts_src[0][0], pts_src[-1][0], locked[0][0], locked[-1][0]))
                if len(inside) < a.min_agree * len(tr):
                    print(f"! {tgt['title']} {tr[0][0]:.0f}-{tr[-1][0]:.0f}s dropped: the lock agrees with the detector in only {len(inside)}/{len(tr)} samples", file=sys.stderr)
                    continue
                pts_src = locked
            else:
                print(f"! {tgt['title']}: could not lock with optical flow, using the detector path", file=sys.stderr)
        pts = [[round(t - a.src_in, 3), x, y] for t, x, y in pts_src]
        op = {"tool": "add_callout", "title": tgt["title"], "_number": bool(tgt.get("number")), "track": pts, "side": tgt.get("side", "auto")}
        if tgt.get("subtitle"):
            op["subtitle"] = tgt["subtitle"]
        op["dur_s"] = round(pts[-1][0] - pts[0][0] + tgt.get("hold", 0.6), 3)
        cands.append((score, pts[0][0], pts[0][0] + op["dur_s"], op))
    for s in stats:
        print(f"lock {s[0]:18s} anchor inside the detector box in {s[2]}/{s[1]} samples; detector span {s[3]:.1f}-{s[4]:.1f}s -> locked {s[5]:.1f}-{s[6]:.1f}s", file=sys.stderr)
    kept = []                                                  # best first; drop a label that would crowd one already kept
    def anchor(op, t):                                         # anchor (x, y) of a candidate at timeline time t (linear, as the editor does)
        p = op["track"]
        if t <= p[0][0]:
            return p[0][1], p[0][2]
        for (t0, x0, y0), (t1, x1, y1) in zip(p, p[1:]):
            if t <= t1:
                u = (t - t0) / (t1 - t0); return x0 + (x1 - x0) * u, y0 + (y1 - y0) * u
        return p[-1][1], p[-1][2]
    def crowds(c, k):
        t0, t1 = max(k[1], c[1]), min(k[2], c[2])
        if t1 - t0 < 0.3:
            return False
        n = max(2, int((t1 - t0) / 0.5))
        for i in range(n + 1):
            t = t0 + (t1 - t0) * i / n
            (x0, y0), (x1, y1) = anchor(c[3], t), anchor(k[3], t)
            if abs(x0 - x1) < a.min_dx and abs(y0 - y1) < a.min_dy:      # flags are wide and short: near in x AND y means they collide
                return True
        return False
    for c in sorted(cands, key=lambda c: -c[0]):
        if any(crowds(c, k) for k in kept):
            continue
        times = {c[1], c[2] - 0.01, *(k[1] for k in kept if k[1] > c[1] and k[1] < c[2])}
        if all(sum(1 for k2 in kept if k2[1] <= t < k2[2]) < a.max_concurrent for t in times):
            kept.append(c)
    ops, seen = [], {}
    for c in sorted(kept, key=lambda c: c[1]):                  # number repeated names in order of appearance, not of rank
        op = c[3]
        if op.pop("_number"):
            seen[op["title"]] = seen.get(op["title"], 0) + 1
            op["title"] += f" {seen[op['title']]}"
        ops.append(op)
    json.dump(ops, open(a.out, "w"), ensure_ascii=False)
    for c in sorted(kept, key=lambda c: c[1]):
        print(f"{c[3]['title']:28s} {c[1]:6.1f}s -> {c[2]:6.1f}s  {len(c[3]['track'])} pts  score {c[0]:.2f}")
    print(f"{len(ops)} callouts (of {len(cands)} candidates) -> {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
