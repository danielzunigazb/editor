#!/usr/bin/env python3
"""Turn detector output (detect.py json) into `apply_ops` callouts: one pinned label per tracked object.
No dependencies. Usage:
  python3 annotate.py <spec.json> <out_ops.json> [--src-in 0] [--src-out 66.64] [--max-concurrent 3]
spec.json: {"detections": ["detections.json", ...],
            "targets": [{"queries": ["building under construction"], "title": "Edificio", "subtitle": "en construcción",
                         "max": 3, "min_len": 4, "min_score": 0.2, "max_area": 0.3}, ...]}
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec"); ap.add_argument("out")
    ap.add_argument("--src-in", type=float, default=0.0); ap.add_argument("--src-out", type=float, default=1e9)
    ap.add_argument("--max-concurrent", type=int, default=3)
    a = ap.parse_args()
    spec = json.load(open(a.spec))
    cands = []                                                 # (score, start, end, op)
    for tgt in spec["targets"]:
        by_t = load(spec["detections"], tgt["queries"])
        by_t = {t: v for t, v in by_t.items() if a.src_in <= t <= a.src_out}
        tracks = []
        for tr in track(by_t, tgt.get("min_score", 0.2), tgt.get("max_area", 0.3)):
            dur = tr[-1][0] - tr[0][0]
            ms = sum(p[5] for p in tr) / len(tr)
            if dur >= tgt.get("min_len", 3.0) and len(tr) >= 3:
                tracks.append((dur * ms, tr))
        tracks.sort(key=lambda x: -x[0])
        for k, (score, tr) in enumerate(tracks[: tgt.get("max", 1)]):
            pts = [[round(t - a.src_in, 3), x, y] for t, x, y in smooth(tr)]
            op = {"tool": "add_callout", "title": tgt["title"], "_number": bool(tgt.get("number")), "track": pts, "side": tgt.get("side", "auto")}
            if tgt.get("subtitle"):
                op["subtitle"] = tgt["subtitle"]
            op["dur_s"] = round(pts[-1][0] - pts[0][0] + tgt.get("hold", 0.6), 3)
            cands.append((score, pts[0][0], pts[0][0] + op["dur_s"], op))
    kept = []                                                  # cap how many labels are on screen at once (best first)
    for c in sorted(cands, key=lambda c: -c[0]):
        overlap = [k for k in kept if min(k[2], c[2]) - max(k[1], c[1]) > 0.3]
        if all(sum(1 for k2 in kept if k2[1] < t < k2[2]) < a.max_concurrent for t in {c[1], c[2] - 0.01, *(k[1] for k in overlap)} if c[1] <= t < c[2]):
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
