#!/usr/bin/env python3
"""External object detector for the editor: finds named things in a video and writes a SMALL json of boxes over time.
It runs outside the model (CPU is enough), so the LLM only reads the json, never the frames.
Run with the detector venv:  .venv_det/bin/python detect.py <video> <out.json> [--fps 1] [--queries "arch,fountain"]
Output: {"video":..,"fps_sampled":..,"w":..,"h":..,"tracks":{label:[[t_s,cx,cy,w,h,score],...]}}  (x,y,w,h normalised 0-1, t in seconds)
Model: OWLv2 (open-vocabulary, text queries)."""
import argparse, json, sys, time
import cv2, numpy as np, torch
from PIL import Image
from transformers import Owlv2ForObjectDetection, Owlv2Processor

DEFAULT = {"triumphal arch": 1, "fountain": 1, "building under construction": 3, "house": 3, "curved road": 1, "mountain": 1}

ap = argparse.ArgumentParser()
ap.add_argument("video"); ap.add_argument("out")
ap.add_argument("--fps", type=float, default=1.0)
ap.add_argument("--queries", default="", help="comma list; each may be 'label:topk'")
ap.add_argument("--thr", type=float, default=0.15)
ap.add_argument("--start", type=float, default=0.0); ap.add_argument("--end", type=float, default=1e9)
a = ap.parse_args()
queries = dict(DEFAULT)
if a.queries:
    queries = {}
    for q in a.queries.split(","):
        n, _, k = q.partition(":"); queries[n.strip()] = int(k or 1)
labels = list(queries)

proc = Owlv2Processor.from_pretrained("google/owlv2-base-patch16-ensemble")
model = Owlv2ForObjectDetection.from_pretrained("google/owlv2-base-patch16-ensemble").eval()
torch.set_num_threads(4)
cap = cv2.VideoCapture(a.video)
vfps, nfr = cap.get(cv2.CAP_PROP_FPS), int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
dur = nfr / vfps
times = [round(t, 3) for t in np.arange(a.start, min(a.end, dur - 0.05), 1 / a.fps)]
S = max(W, H)
tracks = {l: [] for l in labels}
t0 = time.time()
for i, t in enumerate(times):
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * vfps)))
    ok, bgr = cap.read()
    if not ok:
        continue
    img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    inp = proc(text=[[f"a photo of a {l}" for l in labels]], images=img, return_tensors="pt")
    with torch.no_grad():
        out = model(**inp)
    res = proc.post_process_grounded_object_detection(out, threshold=a.thr, target_sizes=torch.tensor([[S, S]]), text_labels=[labels])[0]   # OWLv2 pads to a square
    for li, l in enumerate(labels):
        m = (res["labels"] == li)
        sc, bx = res["scores"][m], res["boxes"][m]
        for j in sc.argsort(descending=True)[: queries[l]]:
            x0, y0, x1, y1 = [float(v) for v in bx[j]]
            x0, x1, y0, y1 = max(0, x0), min(W, x1), max(0, y0), min(H, y1)
            if x1 > x0 and y1 > y0:
                tracks[l].append([t, round((x0 + x1) / 2 / W, 4), round((y0 + y1) / 2 / H, 4), round((x1 - x0) / W, 4), round((y1 - y0) / H, 4), round(float(sc[j]), 3)])
    print(f"{i + 1}/{len(times)} t={t:g}s  {time.time() - t0:.0f}s elapsed", file=sys.stderr, flush=True)
json.dump({"video": a.video, "fps_sampled": a.fps, "w": W, "h": H, "duration": round(dur, 3), "tracks": tracks}, open(a.out, "w"), separators=(",", ":"))
print("done", {l: len(v) for l, v in tracks.items()}, file=sys.stderr)
