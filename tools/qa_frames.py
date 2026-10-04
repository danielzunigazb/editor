#!/usr/bin/env python3
"""Motion QA of a rendered video (see mltedit/qa.py for what it reports).
Usage: tools/qa_frames.py video.mp4 [--cut 14] [--freeze 2.0] [--json]."""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mltedit.qa import analyse  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--cut", type=float, default=14.0)
    ap.add_argument("--freeze", type=float, default=2.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = analyse(a.video, a.cut, a.freeze)
    print(json.dumps(res) if a.json else "\n".join(f"{k}: {v}" for k, v in res.items()))
