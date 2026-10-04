"""The timeline model, in pure Python on the FRAME GRID: MLT rounds each clip to whole frames, so computing in raw seconds drifted
(40 entries of 0.1 s: layout said 100 frames, MLT built 80) and overlays landed on the wrong frame."""
from .. import ops as O
from ..config import S

DUCK_RAMP_S = 0.3                       # how fast the music dips/recovers around speech


def check_finite(v, where):
    """NaN/inf compare False with everything, so they slip through range checks (`not 0 <= x < y`) and then poison the
    frame grid; reject them up front. Walks nested dicts/lists (subtitle cues)."""
    if isinstance(v, float) and v != v or isinstance(v, float) and v in (float("inf"), float("-inf")):
        raise ValueError(f"{where}: numbers must be finite (got {v})")
    if isinstance(v, dict):
        for x in v.values():
            check_finite(x, where)
    elif isinstance(v, list):
        for x in v:
            check_finite(x, where)



def pack_audio(audios):
    """Audio clips -> the fewest MLT tracks with no two clips of a track overlapping (first fit by start frame). Clips need start_f and n_f."""
    tracks = []
    for a in sorted(audios, key=lambda a: (a["start_f"], a["op"])):
        for t in tracks:
            if t[-1]["start_f"] + t[-1]["n_f"] <= a["start_f"]:
                t.append(a)
                break
        else:
            tracks.append([a])
    return tracks


def gain_curve(a):
    """Keyframes [(frame, dB)] for an audio op (absolute timeline frames): fade in, fade out and speech ducking, as ONE piecewise-linear
    curve in dB (linear in dB = a smooth, natural-sounding ramp). `a` has start_f, n_f, vol, fi_f, fo_f, duck_f (frame intervals),
    duck_db, ramp_f. Returns [] when the level is constant (the caller then sets a plain level)."""
    s, e = a["start_f"], a["start_f"] + a["n_f"] - 1
    vol, lo, fi, fo, ramp = a["vol"], -60.0, a["fi_f"], a["fo_f"], max(1, a["ramp_f"])
    base_pts = [(s, lo if fi else vol)] + ([(s + fi, vol)] if fi else []) + ([(e - fo, vol)] if fo else []) + [(e, lo if fo else vol)]
    iv = []
    for d0, d1 in sorted(a["duck_f"]):                            # clip to the audio, merge gaps shorter than two ramps (no crossing curves)
        d0, d1 = max(d0, s), min(d1, e)
        if d1 <= d0:
            continue
        if iv and d0 - iv[-1][1] < 2 * ramp:
            iv[-1][1] = max(iv[-1][1], d1)
        else:
            iv.append([d0, d1])
    if not iv and fi == 0 and fo == 0:
        return []
    def interp(pts, f):
        if f <= pts[0][0]:
            return pts[0][1]
        for (f0, v0), (f1, v1) in zip(pts, pts[1:]):
            if f <= f1:
                return v0 if f1 == f0 else v0 + (v1 - v0) * (f - f0) / (f1 - f0)
        return pts[-1][1]
    dips = []
    for d0, d1 in iv:
        dips.append([(d0 - ramp, 0.0), (d0, a["duck_db"]), (d1, a["duck_db"]), (d1 + ramp, 0.0)])
    def dip(f):
        return min([interp(p, f) for p in dips if p[0][0] <= f <= p[-1][0]] + [0.0])
    frames = sorted({f for f, _ in base_pts} | {min(max(f, s), e) for p in dips for f, _ in p})
    keys = [(f, round(interp(base_pts, f) + dip(f), 3)) for f in frames]          # every breakpoint is needed: dropping 'equal' ones would bend the ramps
    return keys



def label(L):
    return O.get_layer(L["kind"]).label(L)


def collisions(layers, ctx, max_warnings=4):
    """Warnings for overlays from DIFFERENT edits that are on screen at the same time in overlapping places
    (e.g. subtitles at the bottom while a lower third is shown), so the editor hears about it right after the edit
    instead of only by looking at a frame. Each layer plugin gives its rough zone (a heuristic: it errs toward 'may overlap')."""
    zoned = [(L, O.get_layer(L["kind"]).zone(L, ctx)) for L in layers]
    zoned = [(L, z) for L, z in zoned if z]
    seen, out = set(), []
    for i, (a, za) in enumerate(zoned):
        for b, zb in zoned[i + 1:]:
            if a["op"] == b["op"] or (a["op"], b["op"]) in seen:
                continue
            t0, t1 = max(a["start"], b["start"]), min(a["start"] + a["dur"], b["start"] + b["dur"])
            if t1 - t0 < 0.1:
                continue
            ox, oy = min(za[2], zb[2]) - max(za[0], zb[0]), min(za[3], zb[3]) - max(za[1], zb[1])
            if ox > -0.015 and oy > 0.01:          # touching or within 1.5% of the frame counts as crowding
                seen.add((a["op"], b["op"]))
                out.append(f"{label(a)} and {label(b)} may overlap or touch on screen from {t0:g}s to {t1:g}s; "
                           f"move one (position) or change its timing")
    if len(out) > max_warnings:
        out = out[:max_warnings] + [f"... and {len(out) - max_warnings} more overlapping pairs"]
    return out


def resolve(st):
    """Turn what the ops built (a LayoutState) into the layout: entry starts, overlays clipped to the timeline and given tracks, audio
    resolved to frames. Raises ValueError with a message the caller can show verbatim."""
    entries, xfades, fade, layers, audios, fps, fr = st.entries, st.xfades, st.fade, st.layers, st.audios, st.fps, st.fr
    MAX_LAYERS, MAX_LAYER_TRACKS, MAX_AUDIO_TRACKS = S.max_layers, S.max_layer_tracks, S.max_audio_tracks
    for i, e in enumerate(entries):   # a clip must be long enough for the dissolves on both of its sides
        need = xfades.get(i - 1, 0) + xfades.get(i, 0)
        if need > e["dur_f"]:
            raise ValueError(f"entry {i} is {e['dur_f'] / fps:g}s long but its crossfades need {need / fps:g}s")
    t = 0
    for i, e in enumerate(entries):
        e["start_f"] = t
        t += e["dur_f"] - xfades.get(i, 0)   # next entry starts `dur` earlier
        e["in"], e["dur"], e["start"] = e["in_f"] / fps, e["dur_f"] / fps, e["start_f"] / fps
    total_f = max((e["start_f"] + e["dur_f"] for e in entries), default=0)
    total = total_f / fps
    xfades_f, xfades = dict(xfades), {a: x / fps for a, x in xfades.items()}   # seconds for callers, frames for build()
    if fade and fade["in"] + fade["out"] > total + 1e-6:
        raise ValueError(f"fade in+out ({fade['in']+fade['out']:g}s) is longer than the timeline ({total:g}s)")
    # Overlays live in TIMELINE time: they do not move when earlier clips are edited. Anything now beyond the end
    # (e.g. after a cut) is clipped or dropped with a warning instead of rejecting the edit.
    if len(layers) > MAX_LAYERS:
        raise ValueError(f"{len(layers)} overlays (subtitle cues count one each); the limit is {MAX_LAYERS}")
    warnings, kept = [], []
    for L in layers:
        if L["start"] >= total - 1e-6:
            warnings.append(f"{label(L)} starts at {L['start']:g}s, after the timeline end ({total:g}s): not shown")
            continue
        if L["start"] + L["dur"] > total + 1e-6:
            warnings.append(f"{label(L)} runs past the timeline end ({total:g}s): trimmed")
            L["dur"] = total - L["start"]
        kept.append(L)
    ends = []                                   # first-fit track allocation: overlays that overlap get their own track
    for L in sorted(kept, key=lambda L: (L["start"], L["op"], L.get("sub", 0))):
        for ti, e in enumerate(ends):
            if e <= L["start"] + 1e-6:
                L["track"] = ti + 1; ends[ti] = L["start"] + L["dur"]; break
        else:
            if len(ends) >= MAX_LAYER_TRACKS:
                raise ValueError(f"more than {MAX_LAYER_TRACKS} overlays at the same time ({label(L)} at {L['start']:g}s); "
                                 f"stagger them or remove some")
            ends.append(L["start"] + L["dur"]); L["track"] = len(ends)
    heard = []                                                   # audio ops: clip to the timeline, resolve frames
    for a in audios:
        if a["start"] >= total - 1e-6:
            warnings.append(f"audio {a['name']!r} starts at {a['start']:g}s, after the timeline end ({total:g}s): not heard")
            continue
        room = total - a["start"]
        have = float("inf") if a["loop"] else a["src_dur"] - a["in"]
        want = a["dur"] if a["dur"] is not None else min(have, room)
        if a["dur"] is not None and not a["loop"] and a["dur"] > have + 1e-6:
            warnings.append(f"audio {a['name']!r} is only {have:g}s long from {a['in']:g}s but {a['dur']:g}s were asked: it ends early (use loop=true to repeat it)")
            want = have
        if want > room + 1e-6:
            warnings.append(f"audio {a['name']!r} runs past the timeline end ({total:g}s): trimmed")
            want = room
        if a["fade_in"] is None:                                  # not asked for: the usual 1 s / 2 s, shortened for short sounds
            a["fade_in"] = min(1.0, want * 0.25)
        if a["fade_out"] is None:
            a["fade_out"] = min(2.0, want * 0.35)
        if a["fade_in"] + a["fade_out"] > want + 1e-6:
            raise ValueError(f"op {a['op']} (audio): fade in+out ({a['fade_in'] + a['fade_out']:g}s) is longer than the audio on the timeline ({want:g}s)")
        a.update(dur_eff=want, start_f=fr(a["start"]), in_f=fr(a["in"]), n_f=max(1, fr(want)), fi_f=fr(a["fade_in"]), fo_f=fr(a["fade_out"]),
                 duck_f=[(fr(x), fr(y)) for x, y in a["duck"]], ramp_f=fr(DUCK_RAMP_S))
        a["n_f"] = min(a["n_f"], total_f - a["start_f"])
        heard.append(a)
    heard.sort(key=lambda a: (a["start"], a["op"]))
    if len(pack_audio(heard)) > MAX_AUDIO_TRACKS:
        raise ValueError(f"more than {MAX_AUDIO_TRACKS} audio clips play at the same time; stagger them or remove some (clips that do not overlap share a track)")
    kept.sort(key=lambda L: (L["start"], L["op"], L.get("sub", 0)))
    warnings.extend(collisions(kept, st.ctx))
    first_pip = next((L for L in kept if O.get_layer(L["kind"]).audible), None)
    return {"entries": entries, "xfades": xfades, "xfades_f": xfades_f, "xstyles": st.xstyles, "fade": fade, "layers": kept, "pip": first_pip, "audios": heard,
            "warnings": warnings, "total": total, "total_f": total_f}
