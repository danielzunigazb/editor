"""Music and sound effects, with fades and ducking under speech."""
import json, os, re, subprocess

from .. import assets as assets_lib
from .. import engine as live
from .. import server as sv
from . import builder, edit_tool

def _probe_audio(path):
    """Duration (s) of the audio in `path` (an audio file, or a video with an audio track). ValueError if it has none."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path], capture_output=True, text=True, timeout=sv.SUBPROCESS_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ValueError(f"ffprobe timed out reading '{path}'")
    if r.returncode:
        raise ValueError(f"ffprobe could not read '{path}': {r.stderr.strip() or 'unknown error'}")
    info = json.loads(r.stdout)
    if not any(s_.get("codec_type") == "audio" for s_ in info.get("streams", [])):
        raise ValueError(f"'{path}' has no audio stream")
    try:
        dur = float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"'{path}' has no readable duration")
    if dur < 0.1:
        raise ValueError(f"'{path}' is too short to use as audio ({dur:g}s)")
    return dur


@builder("add_audio", anchor=True)
def _b_add_audio(start_s=0.0, dur_s=None, path="", source_in_s=0.0, volume_db=-14.0, fade_in_s=None, fade_out_s=None, loop=False,
                 duck_under=None, duck_db=-12.0, asset=""):
    if bool(path) == bool(asset):
        raise ValueError("give exactly one of asset (an id from list_assets(kind='music' or 'sfx')) or path (an audio file, or a video with an audio track)")
    if asset:
        item = assets_lib.find(asset)
        p, name = assets_lib.path(asset), item["title"]                  # downloads from R2 on first use, SHA-256 checked
    else:
        p = sv._safe_path(path, "add_audio")
        name = os.path.basename(p)
    op = {"op": "audio", "path": p, "src_dur": _probe_audio(p), "start": start_s, "in": source_in_s, "dur": dur_s, "volume_db": volume_db,
          "fade_in": fade_in_s, "fade_out": fade_out_s, "loop": loop, "duck": duck_under or [], "duck_db": duck_db, "name": name}
    if asset:
        op["asset"] = asset
    return op


def _speech_intervals(st):
    """Timeline intervals [(start_s, end_s)] where the main track's own audio is not silent (speech), found with ffmpeg silencedetect on each
    entry. Used to duck music under dialogue. Sources without audio contribute nothing."""
    sv.bind(st)
    m = live.layout(st["ops"])
    out = []
    for e in m["entries"]:
        path = st["sources"][e["src"]]["path"]
        if not st["sources"][e["src"]].get("has_audio"):
            continue
        r = subprocess.run(["ffmpeg", "-v", "info", "-ss", f"{e['in']:.3f}", "-t", f"{e['dur']:.3f}", "-i", path, "-vn", "-af", "silencedetect=noise=-35dB:d=0.5", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=sv.SUBPROCESS_TIMEOUT)
        sil, cur = [], None
        for ln in r.stderr.splitlines():
            a = re.search(r"silence_start: (-?[\d.]+)", ln)
            b = re.search(r"silence_end: (-?[\d.]+)", ln)
            if a:
                cur = max(0.0, float(a.group(1)))
            elif b and cur is not None:
                sil.append((cur, float(b.group(1)))); cur = None
        if cur is not None:
            sil.append((cur, e["dur"]))
        pos = 0.0
        for a, b in sil + [(e["dur"], e["dur"])]:
            if a - pos > 0.05:
                out.append((e["start"] + pos, e["start"] + min(a, e["dur"])))
            pos = max(pos, b)
    merged = []
    for a, b in sorted(out):                                    # join pauses shorter than 0.5 s, drop blips shorter than 0.4 s
        if merged and a - merged[-1][1] < 0.5:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in merged if b - a >= 0.4]


@edit_tool(anchor=True)
def add_audio(start_s: float = 0.0, dur_s: float | None = None, path: str = "", source_in_s: float = 0.0, volume_db: float = -14.0,
              fade_in_s: float | None = None, fade_out_s: float | None = None, loop: bool = False, duck_under: list[list[float]] | None = None,
              duck_auto: bool | None = None, duck_db: float = -12.0, asset: str = "") -> dict:
    """Add music or a sound effect (TIMELINE time) mixed under the video's own audio. path: an audio file (mp3/wav/ogg/m4a...) or a
    video with an audio track. start_s: when it begins. dur_s: how long (default: the whole file, or until the timeline ends).
    source_in_s: start inside the file. volume_db: -60..+6 (default -14, a music bed under speech; use -6..0 for effects).
    fade_in_s/fade_out_s: ramps at its ends (default 1 s in / 2 s out, shorter for short sounds; asking for more than fits is an error). loop=true repeats a short file to fill dur_s. Ducking (music dips while someone talks):
    duck_under=[[start_s, end_s], ...] in timeline seconds, or duck_auto=true to find the speech in the clips' own audio now
    (re-add the audio after changing the cut); duck_db is how much quieter (default -12). duck_auto defaults to ON for music (an asset of kind music, or any
    clip of 20 s or more) when the project's motion is on, off otherwise; pass false to stop it. Up to 32 audio items, at most 8 playing at the same time (clips that never overlap share a track)."""
    spec = sv.BUILDERS["add_audio"](start_s, dur_s, path, source_in_s, volume_db, fade_in_s, fade_out_s, loop, duck_under, duck_db, asset)
    if duck_auto is None:                                   # motion on: music (a library piece of kind music, or anything 20 s+) dips under speech by itself
        long_ = (spec.get("dur") or spec["src_dur"] - spec["in"]) >= 20
        duck_auto = bool(sv.load().get("motion") and (long_ or (asset and assets_lib.find(asset)["kind"] == "music")))
    if duck_auto:
        with sv.locked():
            spec["duck"] = [list(iv) for iv in _speech_intervals(sv.load())] + [list(iv) for iv in (duck_under or [])]
    return sv.commit(spec)
