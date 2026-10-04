"""Segment plan of a project for the live viewer.

The timeline is cut into segments of `viewer_segment_s` seconds aligned to frames. Each segment gets a HASH of everything that can change its
pictures: the clips and transitions that overlap it (with the media actually read: proxy or original, by file identity), the overlays that overlap it
(their whole resolved layout), the fades if it touches them, the frame size / rate / template, and the layout version. A segment is a file named by its
hash, so an edit invalidates exactly the segments whose hash changed and everything else is served from disk.
Audio is not cut: it is rendered once for the whole timeline (one continuous encode, split into HLS parts), so there are no seams or encoder
priming clicks; its hash covers what changes sound."""
import hashlib, json, math, os

from .. import binding, engine as live
from ..config import S
from ..media import proxy as proxies
from .. import project as P


def _h(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()[:16]


def _sig(path):
    try:
        s = os.stat(path)
        return [path, s.st_size, s.st_mtime_ns]
    except OSError:
        return [path, None, None]


def preview_scale(st):
    """Scale of the viewer picture: viewer_height tall (never above the project's own size)."""
    return min(1.0, S.viewer_height / max(1, st["height"]))


def plan(st, home):
    """The plan for project state `st`: {hash, audio_hash, revision, scale, W, H, fps, total_f, seg_f, segs: [{i, a, b, hash}], has_audio, layout}.
    Binds the engine (a preview binding), so call it from a thread that owns the engine context."""
    scale = preview_scale(st)
    binding.bind(st, home, scale)
    m = live.layout(st["ops"])
    fps, W, H = live.FPS, live.W, live.H
    total_f = m["total_f"]
    seg_f = max(1, round(S.viewer_segment_s * fps))
    media = proxies.media_for_preview(home, st["sources"], scale)
    base = {"v": P.LAYOUT_VERSION, "size": [W, H, fps], "theme": st.get("theme"), "motion": bool(st.get("motion")), "scale": scale}
    fr = live.CTX.fr
    fade = m["fade"]
    fi, fo = (fr(fade["in"]), fr(fade["out"])) if fade else (0, 0)
    xf_f = m["xfades_f"]
    segs = []
    for i, a in enumerate(range(0, total_f, seg_f)):
        b = min(a + seg_f, total_f)
        ents = [[e["src"], _sig(media[e["src"]]), e["in_f"], e["dur_f"], e["start_f"]] for e in m["entries"] if e["start_f"] < b and e["start_f"] + e["dur_f"] > a]
        xfs = [[j, n, m["xstyles"].get(j)] for j, n in sorted(xf_f.items()) if m["entries"][j + 1]["start_f"] < b and m["entries"][j + 1]["start_f"] + n > a]
        lays = []
        for L in m["layers"]:
            s0 = fr(L["start"])
            if s0 - 1 < b and s0 + max(1, fr(L["dur"])) + 1 > a:
                lays.append({k: v for k, v in L.items() if k != "anchor"})
        # an overlay's picture file is part of what it shows: text/graphics are made from the layer dict; images/pip read files
        for L in lays:
            if L.get("path"):
                L["_file"] = _sig(L["path"])
            if L.get("kind") == "pip":
                L["_media"] = _sig(media[L["src"]])
        fd = fade if (a < fi or b > total_f - fo) and fade else None
        segs.append({"i": i, "a": a, "b": b, "hash": _h({**base, "a": a, "b": b, "ents": ents, "xfs": xfs, "lays": lays, "fade": fd, "last": b == total_f and total_f})})
    has_audio = bool(m["audios"]) or any(st["sources"][e["src"]].get("has_audio") for e in m["entries"])
    audio = {**base, "total": total_f, "ents": [[e["src"], _sig(media[e["src"]]), e["in_f"], e["dur_f"], e["start_f"]] for e in m["entries"]], "xf": sorted(xf_f.items()), "fade": fade,
             "audios": [{k: v for k, v in a.items() if k != "anchor"} for a in m["audios"]], "files": [_sig(a["path"]) for a in m["audios"]]}
    return {"hash": _h([s["hash"] for s in segs] + [_h(audio)]), "audio_hash": _h(audio), "revision": st.get("revision", 0), "scale": scale, "W": W, "H": H, "fps": fps,
            "total_f": total_f, "seg_f": seg_f, "segs": segs, "has_audio": has_audio, "layout": m}


# ------------------------------------------------------------------------------------------------ HLS playlists
def video_playlist(pl, seg_url):
    """Media playlist (VOD) of the video segments: every segment is listed from the start with its exact duration; it is made when first requested."""
    fps = pl["fps"]
    target = max(1, math.ceil(max((s["b"] - s["a"]) / fps for s in pl["segs"]))) if pl["segs"] else 1
    out = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{target}", "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-PLAYLIST-TYPE:VOD"]
    for s in pl["segs"]:
        out += [f"#EXTINF:{(s['b'] - s['a']) / fps:.6f},", seg_url(s["hash"])]
    out.append("#EXT-X-ENDLIST")
    return "\n".join(out) + "\n"


def master_playlist(pl, video_url, audio_url):
    bw = 2_500_000
    lines = ["#EXTM3U", "#EXT-X-VERSION:3"]
    if pl["has_audio"]:
        lines.append(f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="audio",DEFAULT=YES,AUTOSELECT=YES,URI="{audio_url}"')
    lines.append(f'#EXT-X-STREAM-INF:BANDWIDTH={bw},RESOLUTION={pl["W"]}x{pl["H"]},FRAME-RATE={pl["fps"]}' + (',AUDIO="aud"' if pl["has_audio"] else ""))
    lines.append(video_url)
    return "\n".join(lines) + "\n"
