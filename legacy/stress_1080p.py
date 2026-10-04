#!/usr/bin/python3.12
"""Extra stress test: 1080p30, high-motion (mandelbrot), long GOP (g=250), dissolve + fade.
Reports sequential-decode vs random-seek (scrub) latency of the MLT playlist."""
import os, random, subprocess, time, mlt7

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # repo root (this script lives in legacy/)
HD = os.path.join(HERE, "media", "hd.mp4")
if not os.path.exists(HD):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "mandelbrot=size=1920x1080:rate=30",
                    "-t", "8", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-g", "250", HD], check=True)
mlt7.Factory.init(); p = mlt7.Profile("atsc_1080p_30")
a, b = mlt7.Producer(p, HD), mlt7.Producer(p, HD)
pl = mlt7.Playlist(p); pl.append(a, 0, 119); pl.append(b, 0, 119)
pl.mix(0, 30, mlt7.Transition(p, "luma"))
f = mlt7.Filter(p, "brightness"); f.set("level", "0=0;15=1"); f.set_in_and_out(0, pl.get_playtime() - 1); pl.attach(f)
n = pl.get_playtime(); pl.set("eof", "pause")


def go(order, label):
    lat = []
    for i in order:
        pl.seek(i); s = time.perf_counter()
        pl.get_frame().get_image(mlt7.mlt_image_yuv422, 1920, 1080)
        lat.append((time.perf_counter() - s) * 1000)
    lat.sort()
    print(f"{label}: n={len(lat)} p50 {lat[len(lat)//2]:.1f} p95 {lat[int(len(lat)*.95)]:.1f} "
          f"max {lat[-1]:.1f} ms (budget 33.3) over={sum(x > 33.3 for x in lat)}")


go(range(n), "1080p30 sequential w/ dissolve")
random.seed(2); go([random.randrange(n) for _ in range(100)], "1080p30 random seek")
