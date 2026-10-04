"""MLT -> NUT over a FIFO -> ffmpeg."""
import os, subprocess

from ..config import S


def render(p, tr, out, preset="ultrafast", crf="30", abr="64k", master=""):
    """MLT composes -> NUT over a FIFO -> ffmpeg CLI encodes. Raises RuntimeError (with ffmpeg's message) if the encode
    fails, and never leaves a FIFO, a stray ffmpeg or a half-written output behind.
    A built timeline can be rendered ONCE (a second render of the same tractor emits no frames, verified); build a new
    one per render, as the server does. A fresh build after a failed render works.
    master="loudnorm": the audio goes through ffmpeg's loudnorm (-16 LUFS integrated, -1.5 dB true peak) on its way into the file."""
    import mlt7, shutil, tempfile
    if master not in ("", "loudnorm"):
        raise ValueError("master must be '' or 'loudnorm'")
    fifo_dir = tempfile.mkdtemp(prefix="mltfifo_")           # private (0700) dir: no predictable name next to `out`
    fifo = os.path.join(fifo_dir, "pipe.nut")                # for another user/process to pre-create or race on
    os.mkfifo(fifo, 0o600)
    errlog = tempfile.TemporaryFile()
    ff = None
    try:
        ff = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-i", fifo, "-c:v", "libx264", "-preset", preset,
                               "-crf", str(crf), "-pix_fmt", "yuv420p", *(["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"] if master else []),
                               "-c:a", "aac", "-b:a", abr, "-movflags", "+faststart", out], stderr=errlog, stdin=subprocess.DEVNULL)
        c = mlt7.Consumer(p, "avformat", fifo)
        # real_time=-N renders N frames in parallel (no frame dropping). Measured at 4K with 5 overlay layers: 65 s -> 35.6 s
        # (N=2) with bit-identical luma on all 312 frames; N=4 only reached 33.7 s but used 4.1 GB instead of 2.9 GB.
        threads = S.render_threads
        for k, v in dict(f="nut", vcodec="rawvideo", acodec="pcm_s16le", real_time=str(-threads) if threads > 1 else "0").items():
            c.set(k, v)
        c.connect(tr); c.run(); c.stop()
        try:
            rc = ff.wait(timeout=120)
        except subprocess.TimeoutExpired:
            ff.kill(); rc = ff.wait()
            raise RuntimeError("ffmpeg did not finish within 120 s after the render ended")
        if rc != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
            errlog.seek(0)
            tail = errlog.read().decode(errors="replace").strip().splitlines()[-3:]
            raise RuntimeError(f"ffmpeg failed to write {out} (exit code {rc}): " + " | ".join(tail))
    except BaseException:
        if os.path.isfile(out) and not (ff is not None and ff.returncode == 0):
            os.remove(out)                                   # never leave a partial file that looks like a result
        raise
    finally:
        if ff is not None and ff.poll() is None:
            ff.kill(); ff.wait()
        errlog.close()
        shutil.rmtree(fifo_dir, ignore_errors=True)
