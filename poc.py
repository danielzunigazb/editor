#!/usr/bin/python3.12
"""POC: non-linear editing timeline on MLT (preview) + FFmpeg (final export).

Timeline built:  A[0..CUT_S]  --1s luma crossfade-->  B   + fade-in at start + fade-out at end

Subcommands:
  gen      generate small test clips with ffmpeg
  build    build timeline, print structure + API timings, save timeline.mlt (XML)
  bench    pull frames as fast as possible (no display), report per-frame latency
  preview  real-time playback (sdl2 consumer; use xvfb-run when headless), report drops
  export   MLT renders raw NUT into a FIFO, ffmpeg CLI encodes the final mp4
  measure  run another subcommand as a child and sample its CPU/RSS from /proc

Must run with the interpreter that matches the apt binding: /usr/bin/python3.12.
"""
import argparse, json, os, statistics, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
MEDIA = os.path.join(HERE, "media")
CLIP_A = os.path.join(MEDIA, "clip_a.mp4")
CLIP_B = os.path.join(MEDIA, "clip_b.mp4")
OUT = os.path.join(HERE, "out")
PROFILE = "atsc_720p_25"  # 1280x720 @ 25fps
FPS = 25
CUT_S = 3.0
XFADE_S = 1.0
FADE_IN_S = 0.5
FADE_OUT_S = 1.0


def sh(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def gen():
    os.makedirs(MEDIA, exist_ok=True)
    common = ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest"]
    sh(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6", *common, CLIP_A])
    sh(["ffmpeg", "-y", "-f", "lavfi", "-i", "smptehdbars=size=1280x720:rate=25:duration=5",
        "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=5", *common, CLIP_B])
    print("generated", CLIP_A, CLIP_B)


def build():
    """Returns (profile, playlist, timings_ms). All edits go through the Playlist API."""
    import mlt7
    t = {}

    def timed(name, fn):
        s = time.perf_counter()
        r = fn()
        t[name] = round((time.perf_counter() - s) * 1000, 2)
        return r

    timed("Factory.init", mlt7.Factory.init)
    profile = mlt7.Profile(PROFILE)
    # IMPORTANT: use the default loader (Producer(profile, path)), NOT "avformat" directly.
    # The loader auto-inserts audio/video normalizers; with raw "avformat" the audio came
    # out as full-scale noise and the volume filter had no effect (see REPORT.md).
    a = timed("open clip A", lambda: mlt7.Producer(profile, CLIP_A))
    b = timed("open clip B", lambda: mlt7.Producer(profile, CLIP_B))
    assert a.is_valid() and b.is_valid(), "failed to open a clip"

    pl = mlt7.Playlist(profile)
    pl.append(a)                                   # whole A (150 frames)
    cut_frame = int(CUT_S * FPS)
    timed("cut: split_at", lambda: pl.split_at(cut_frame, True))  # A -> [0..74][75..149] (left keeps `position` frames)
    timed("cut: remove tail", lambda: pl.remove(1))                   # drop the tail of A
    pl.append(b)                                   # B after the cut
    n = int(XFADE_S * FPS)

    def crossfade():
        pl.mix(0, n, mlt7.Transition(profile, "luma"))      # video dissolve
        pl.mix_add(0, mlt7.Transition(profile, "mix"))      # audio crossfade
    timed("crossfade (mix+mix_add)", crossfade)

    total = pl.get_playtime()

    def fades():
        # NOTE: attached filters default to in=0/out=0; without set_in_and_out the
        # keyframes past frame ~0 were silently ignored (see REPORT.md).
        # `volume.level` is in dB (not linear gain), so audio gets its own keyframes.
        fi, fo = int(FADE_IN_S * FPS), int(FADE_OUT_S * FPS)
        vid = f"0=0;{fi}=1;{total-fo}=1;{total-1}=0"
        aud = f"0=-60;{fi}=0;{total-fo}=0;{total-1}=-60"
        for service, prop, kf in (("brightness", "level", vid), ("volume", "level", aud)):
            f = mlt7.Filter(profile, service)
            f.set(prop, kf)
            f.set_in_and_out(0, total - 1)
            pl.attach(f)
    timed("fade in/out (2 filters)", fades)
    return profile, pl, t


def cmd_build(_):
    import mlt7
    profile, pl, t = build()
    print(f"profile {PROFILE}; playlist clips={pl.count()} length={pl.get_playtime()} frames "
          f"({pl.get_playtime()/FPS:.2f}s)")
    for i in range(pl.count()):
        info = pl.clip_info(i)
        print(f"  entry {i}: resource={os.path.basename(info.resource or '<mix>')} "
              f"in={info.frame_in} out={info.frame_out} len={info.frame_count}")
    os.makedirs(OUT, exist_ok=True)
    xml = mlt7.Consumer(profile, "xml", os.path.join(OUT, "timeline.mlt"))
    xml.connect(pl)
    xml.run()
    print("API timings (ms):", json.dumps(t, indent=2))


def cmd_bench(_):
    """Pull frames at max speed with get_image (decode+filters+compositing, no display)."""
    import mlt7
    profile, pl, _t = build()
    pl.set("eof", "pause")
    lat = []
    start = time.perf_counter()
    for i in range(pl.get_playtime()):
        pl.seek(i)
        s = time.perf_counter()
        fr = pl.get_frame()
        fr.get_image(mlt7.mlt_image_yuv422, 1280, 720)
        lat.append((time.perf_counter() - s) * 1000)
    wall = time.perf_counter() - start
    lat.sort()
    q = lambda p: lat[min(len(lat) - 1, int(len(lat) * p))]
    print(json.dumps({
        "frames": len(lat), "wall_s": round(wall, 2),
        "throughput_fps": round(len(lat) / wall, 1), "realtime_budget_ms": 40,
        "ms_p50": round(q(.5), 2), "ms_p95": round(q(.95), 2),
        "ms_p99": round(q(.99), 2), "ms_max": round(lat[-1], 2),
        "frames_over_budget": sum(x > 40 for x in lat)}, indent=2))


def cmd_preview(args):
    import mlt7
    profile, pl, _t = build()
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    c = mlt7.Consumer(profile, "sdl2")
    if not c.is_valid():
        sys.exit("sdl2 consumer unavailable")
    c.set("real_time", "1")        # allow frame dropping to stay in sync
    c.set("terminate_on_pause", 1)
    c.connect(pl)
    start = time.perf_counter()
    c.start()
    while not c.is_stopped():
        time.sleep(0.05)
    wall = time.perf_counter() - start
    c.stop()
    print(json.dumps({"timeline_s": pl.get_playtime() / FPS, "wall_s": round(wall, 2),
                      "drop_count": c.get_int("drop_count"),
                      "consumer_real_time": c.get_int("real_time")}, indent=2))


def cmd_export(args):
    """MLT renders the composited timeline as NUT (raw video + pcm) into a FIFO;
    the ffmpeg CLI does the final encode."""
    import mlt7
    profile, pl, _t = build()
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, "final.mp4")
    fifo = os.path.join(OUT, "pipe.nut")
    if os.path.exists(fifo):
        os.remove(fifo)
    os.mkfifo(fifo)
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-i", fifo,
                           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                           "-c:a", "aac", "-b:a", "160k", out])
    c = mlt7.Consumer(profile, "avformat", fifo)
    c.set("f", "nut"); c.set("vcodec", "rawvideo"); c.set("acodec", "pcm_s16le")
    c.set("real_time", "0")        # render as fast as possible, no dropping
    c.connect(pl)
    start = time.perf_counter()
    c.run()
    c.stop()
    ff.wait()
    os.remove(fifo)
    print(f"exported {out} in {time.perf_counter()-start:.2f}s (ffmpeg rc={ff.returncode})")


def cmd_measure(args):
    sub = args.sub
    cmd = [sys.executable, os.path.abspath(__file__), sub]
    if sub == "preview" and not os.environ.get("DISPLAY"):
        cmd = ["xvfb-run", "-a"] + cmd
    clk = os.sysconf("SC_CLK_TCK")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    samples = []

    def rss_cpu(pid):
        try:
            with open(f"/proc/{pid}/stat") as f:
                s = f.read().rsplit(")", 1)[1].split()
            cpu = (int(s[11]) + int(s[12])) / clk
            with open(f"/proc/{pid}/status") as f:
                rss = next(int(l.split()[1]) for l in f if l.startswith("VmRSS")) / 1024
            return cpu, rss
        except (FileNotFoundError, ProcessLookupError, StopIteration):
            return None

    def descendants(root):
        out, todo = [], [root]
        while todo:
            x = todo.pop(); out.append(x)
            try:
                kids = open(f"/proc/{x}/task/{x}/children").read().split()
            except FileNotFoundError:
                kids = []
            todo += [int(k) for k in kids]
        return out

    t0 = time.perf_counter(); last_cpu = {}; last_t = t0; cpu_tot = {}
    outp = []
    th = threading.Thread(target=lambda: outp.append(p.stdout.read())); th.start()
    while p.poll() is None:
        time.sleep(0.2)
        now = time.perf_counter(); rss_sum = 0
        for pid in descendants(p.pid):
            r = rss_cpu(pid)
            if r:
                cpu_tot[pid] = r[0]; rss_sum += r[1]
        samples.append((now - last_t, sum(cpu_tot.values()), rss_sum))
        last_t = now
    th.join()
    wall = time.perf_counter() - t0
    # per-interval CPU% (100% == one full core) from cumulative CPU time
    pct, prev = [], 0.0
    for dt, cum, _ in samples:
        pct.append(max(0.0, (cum - prev) / dt * 100)); prev = cum
    rss = [s[2] for s in samples]
    print(json.dumps({"subcommand": sub, "wall_s": round(wall, 2),
                      "cpu_time_s": round(samples[-1][1], 2) if samples else None,
                      "cpu_pct_avg_of_1core": round(statistics.mean(pct), 1) if pct else None,
                      "cpu_pct_peak_of_1core": round(max(pct), 1) if pct else None,
                      "rss_peak_mb": round(max(rss), 1) if rss else None,
                      "rss_mean_mb": round(statistics.mean(rss), 1) if rss else None,
                      "cores_available": os.cpu_count()}, indent=2))
    print("--- child output ---"); print(outp[0].strip())


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("gen").set_defaults(fn=lambda a: gen())
    for n, f in [("build", cmd_build), ("bench", cmd_bench), ("preview", cmd_preview), ("export", cmd_export)]:
        sp.add_parser(n).set_defaults(fn=f)
    m = sp.add_parser("measure"); m.add_argument("sub", choices=["bench", "preview", "export"])
    m.set_defaults(fn=cmd_measure)
    a = ap.parse_args(); a.fn(a)
