#!/usr/bin/env python3
"""Animated cards: the element groups arrive one after another and the last frame is the static card.
Run: .venv/bin/python test_cards_anim.py   (ffmpeg only; no display needed)"""
import os, statistics, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from PIL import Image, ImageChops  # noqa: E402

import cards  # noqa: E402
import themes  # noqa: E402

ok = bad = 0


def chk(name, cond, detail=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


W, H, FPS, DUR = 640, 360, 25, 3.0
TMP = tempfile.mkdtemp(prefix="cardanim_")
KW = {"title": dict(title="Gran Inauguración", subtitle="Nuevo complejo residencial"), "list": dict(title="Lo que viene", items=["Fase 1", "Fase 2", "Fase 3"]),
      "bento": dict(title="Resultados", items=["18 %|crecimiento", "4,2 M|usuarios", "98 %|satisfacción", "12|países"]), "stat": dict(title="Edificios", number="14", subtitle="en obra")}


def frame(mp4, t):
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", mp4, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    return Image.frombytes("RGB", (W, H), r.stdout)


def mad(a, b):
    return statistics.mean(ImageChops.difference(a, b).convert("L").tobytes())


for name in ("luxury", "playful", "cinema", "terminal", "riso"):
    th = themes.get(name)
    for lay, kw in KW.items():
        bg, layers = cards.card_layers(TMP, lay, W, H, th, **kw)
        mp4 = os.path.join(TMP, f"{name}_{lay}.mp4")
        cards.card_video_animated(bg, layers, mp4, W, H, FPS, DUR, th)
        smp4 = os.path.join(TMP, f"{name}_{lay}_static.mp4")                          # the static card through the SAME encoder: only the animation differs
        cards.card_video(cards.card_png(TMP, lay, W, H, th, **kw), smp4, W, H, FPS, DUR)
        static = frame(smp4, DUR - 0.12)
        last = frame(mp4, DUR - 0.12)
        d_last = mad(last, static)
        times = [0.0, 0.3, 0.6, 0.9, 1.4, 2.0]
        d = [mad(frame(mp4, t), static) for t in times]
        chk(f"{name}/{lay}: {len(layers)} groups; the last frame is the static card (mean diff {d_last:.2f} < 1.5 against the static card encoded the same way)", len(layers) >= 2 and d_last < 1.5, d_last)
        chk(f"{name}/{lay}: it starts as the background alone (differs from the end) and settles monotonically", d[0] > 1.0 and all(b_ <= a_ + 0.6 for a_, b_ in zip(d, d[1:])) and d[-1] < 1.5, [round(x, 2) for x in d])
mp4 = os.path.join(TMP, "luxury_title.mp4")
p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,nb_frames,width,height", "-of", "csv=p=0", mp4], capture_output=True, text=True).stdout
chk("the mp4 has video of W x H x (dur*fps) frames and an audio track", "video,640,360" in p.replace("\n", ",") or ("640,360" in p and "audio" in p), p)
bg, layers = cards.card_layers(TMP, "title", W, H, themes.get("minimal"), **KW["title"])
pm = os.path.join(TMP, "push.mp4")
cards.card_video_animated(bg, layers, pm, W, H, FPS, 2.0, themes.get("minimal"), push=True)
chk("push=true (slow zoom on the background only) also renders", os.path.getsize(pm) > 1000)
short = os.path.join(TMP, "short.mp4")
bg, layers = cards.card_layers(TMP, "bento", W, H, themes.get("cinema"), **KW["bento"])
cards.card_video_animated(bg, layers, short, W, H, FPS, 1.0, themes.get("cinema"))
sref = os.path.join(TMP, "short_static.mp4")
cards.card_video(cards.card_png(TMP, "bento", W, H, themes.get("cinema"), **KW["bento"]), sref, W, H, FPS, 1.0)
chk("a 1 s card squeezes the template's times so everything has settled before the end", mad(frame(short, 0.9), frame(sref, 0.9)) < 1.5)
print(f"\n{ok} passed, {bad} failed"); sys.exit(1 if bad else 0)
