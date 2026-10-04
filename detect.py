#!/usr/bin/env python3
"""External object detector for the editor: finds named things in a video and writes a SMALL json of boxes over time.
It runs outside the model (CPU is enough), so the LLM only reads the json, never the frames.
Run with the detector venv:  .venv_det/bin/python detect.py <video> <out.json> [--fps 1] [--queries "arch,fountain"] [--workers 4]
The sampled times are split between --workers processes (one model each, cores/workers torch threads): small-batch inference scales far better
across processes than across the threads of one.
Output: {"video":..,"fps_sampled":..,"w":..,"h":..,"tracks":{label:[[t_s,cx,cy,w,h,score],...]}}  (x,y,w,h normalised 0-1, t in seconds)
Model: OWLv2 (open-vocabulary, text queries)."""
import argparse, json, multiprocessing as mp, os, sys, time

DEFAULT = {"triumphal arch": 1, "fountain": 1, "building under construction": 3, "house": 3, "curved road": 1, "mountain": 1}
_M = {}


def init(threads):
    import torch
    from transformers import Owlv2ForObjectDetection, Owlv2Processor
    torch.set_num_threads(threads)
    _M["proc"] = Owlv2Processor.from_pretrained("google/owlv2-base-patch16-ensemble")
    _M["model"] = Owlv2ForObjectDetection.from_pretrained("google/owlv2-base-patch16-ensemble").eval()


def work(job):
    """Detect on a list of times. Returns {label: [[t, cx, cy, w, h, score], ...]}."""
    import cv2, torch
    from PIL import Image
    video, times, labels, queries, thr = job
    proc, model = _M["proc"], _M["model"]
    cap = cv2.VideoCapture(video)
    vfps = cap.get(cv2.CAP_PROP_FPS)
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    S = max(W, H)
    tracks = {l: [] for l in labels}
    for t in times:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * vfps)))
        ok, bgr = cap.read()
        if not ok:
            continue
        img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        inp = proc(text=[[f"a photo of a {l}" for l in labels]], images=img, return_tensors="pt")
        with torch.no_grad():
            out = model(**inp)
        res = proc.post_process_grounded_object_detection(out, threshold=thr, target_sizes=torch.tensor([[S, S]]), text_labels=[labels])[0]   # OWLv2 pads to a square
        for li, l in enumerate(labels):
            m = (res["labels"] == li)
            sc, bx = res["scores"][m], res["boxes"][m]
            for j in sc.argsort(descending=True)[: queries[l]]:
                x0, y0, x1, y1 = [float(v) for v in bx[j]]
                x0, x1, y0, y1 = max(0, x0), min(W, x1), max(0, y0), min(H, y1)
                if x1 > x0 and y1 > y0:
                    tracks[l].append([t, round((x0 + x1) / 2 / W, 4), round((y0 + y1) / 2 / H, 4), round((x1 - x0) / W, 4), round((y1 - y0) / H, 4), round(float(sc[j]), 3)])
    return tracks


def main():
    import cv2, numpy as np
    ap = argparse.ArgumentParser()
    ap.add_argument("video"); ap.add_argument("out")
    ap.add_argument("--fps", type=float, default=1.0)
    ap.add_argument("--queries", default="", help="comma list; each may be 'label:topk'")
    ap.add_argument("--thr", type=float, default=0.15)
    ap.add_argument("--start", type=float, default=0.0); ap.add_argument("--end", type=float, default=1e9)
    ap.add_argument("--workers", type=int, default=1, help="processes (each loads the model, ~0.6 GB RAM); threads per process = cores/workers")
    a = ap.parse_args()
    queries = dict(DEFAULT)
    if a.queries:
        queries = {}
        for q in a.queries.split(","):
            n, _, k = q.partition(":"); queries[n.strip()] = int(k or 1)
    labels = list(queries)
    cap = cv2.VideoCapture(a.video)
    vfps, nfr = cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    dur = nfr / vfps
    times = [round(float(t), 3) for t in np.arange(a.start, min(a.end, dur - 0.05), 1 / a.fps)]
    n = max(1, min(a.workers, len(times), os.cpu_count() or 1))
    chunks = [times[k::n] for k in range(n)]                    # interleaved: every worker gets the same mix of easy/hard frames
    jobs = [(a.video, c, labels, queries, a.thr) for c in chunks]
    t0 = time.time()
    with mp.get_context("spawn").Pool(n, initializer=init, initargs=(max(1, (os.cpu_count() or 1) // n),)) as pool:
        parts = pool.map(work, jobs, chunksize=1)
    tracks = {l: sorted((p for part in parts for p in part[l]), key=lambda p: p[0]) for l in labels}
    json.dump({"video": a.video, "fps_sampled": a.fps, "w": W, "h": H, "duration": round(dur, 3), "tracks": tracks}, open(a.out, "w"), separators=(",", ":"))
    print(f"done {len(times)} frames, {n} workers x {max(1, (os.cpu_count() or 1) // n)} threads, {time.time() - t0:.1f} s", {l: len(v) for l, v in tracks.items()}, file=sys.stderr)


if __name__ == "__main__":
    main()
