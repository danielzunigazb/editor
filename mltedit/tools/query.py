"""Asking the project questions without dumping all of it: what is on screen at a moment, and a short description for long sessions."""
from .. import engine as live
from .. import ops as O
from .. import server as sv
from ..errors import EditError
from . import tool


def _num(v, name):
    if not isinstance(v, (int, float)) or isinstance(v, bool) or v != v or abs(v) > 1e7:
        raise EditError("INVALID_ARGUMENT", f"{name} must be a number of seconds", field=name)
    return float(v)


@tool
def query(at_s: float | None = None, start_s: float | None = None, end_s: float | None = None) -> dict:
    """What is on the timeline at a moment (`at_s`) or during an interval (`start_s`..`end_s`): the clips with the source time showing, the active
    transition, every overlay and audio clip with its edit id and anchor, and any fade. Use it to find the id of an edit before update_op / move_op /
    remove_op, or to check what a cut did to the overlays, without reading the whole timeline."""
    if at_s is None and (start_s is None or end_s is None):
        raise EditError("INVALID_ARGUMENT", "give at_s, or both start_s and end_s")
    t0, t1 = (_num(at_s, "at_s"),) * 2 if at_s is not None else (_num(start_s, "start_s"), _num(end_s, "end_s"))
    if t1 < t0:
        raise EditError("INVALID_ARGUMENT", "end_s is before start_s")
    st = sv.load()
    sv.bind(st)
    m = live.layout(st["ops"])
    if not (0 <= t0 and t1 <= m["total"] + 1e-6):
        raise EditError("OUT_OF_RANGE", f"outside the timeline (0-{m['total']:g}s)")
    ids = [o["id"] for o in st["ops"]]
    point = at_s is not None
    hit = (lambda a, b: a <= t0 < b) if point else (lambda a, b: a < t1 and b > t0)       # noqa: E731
    clips = [{"clip_id": e["id"], "source": e["src"], "index": i, "start_s": round(e["start"], 3), "end_s": round(e["start"] + e["dur"], 3),
              **({"source_time_s": round(e["in"] + (t0 - e["start"]), 3)} if point else {})}
             for i, e in enumerate(m["entries"]) if hit(e["start"], e["start"] + e["dur"])]
    xf = []
    for a, d in sorted(m["xfades"].items()):
        s = m["entries"][a + 1]["start"]
        if hit(s, s + d):
            xf.append({"between": [m["entries"][a]["id"], m["entries"][a + 1]["id"]], "style": m["xstyles"].get(a), "start_s": round(s, 3), "end_s": round(s + d, 3),
                       **({"progress": round((t0 - s) / d, 3)} if point else {})})
    overlays = []
    for L in m["layers"]:
        if hit(L["start"], L["start"] + L["dur"]):
            overlays.append({"op_id": ids[L["op"]], "kind": L["kind"], "start_s": round(L["start"], 3), "end_s": round(L["start"] + L["dur"], 3), "track": L["track"],
                             **({"anchor": sv._anchor_view(L["anchor"])} if L.get("anchor") else {}), **O.get_layer(L["kind"]).summary(L)})
    audio = [{"op_id": ids[a["op"]], "name": a["name"], "start_s": round(a["start"], 3), "end_s": round(a["start"] + a["dur_eff"], 3), "volume_db": a["vol"]}
             for a in m["audios"] if hit(a["start"], a["start"] + a["dur_eff"])]
    fade = None
    if m["fade"] and point:
        fi, fo = m["fade"]["in"], m["fade"]["out"]
        if fi and t0 < fi:
            fade = {"in": round(t0 / fi, 3)}
        elif fo and t0 > m["total"] - fo:
            fade = {"out": round((t0 - (m["total"] - fo)) / fo, 3)}
    return {"revision": st.get("revision", 0), **({"at_s": t0} if point else {"start_s": t0, "end_s": t1}), "duration_s": round(m["total"], 3), "clips": clips,
            **({"transitions": xf} if xf else {}), "overlays": overlays, "audio": audio, **({"fade": fade} if fade else {})}


@tool
def describe_project(budget: str = "compact") -> dict:
    """A short description of the whole project for long sessions, instead of the full timeline: format, template, length, the clips with their ids, the
    overlays and audio grouped, the warnings. budget="compact" (default, about 25 lines: long lists are cut with a count) | "full" (every item).
    The edit ids are there, so you can go on editing from it; `query` zooms into a moment."""
    if budget not in ("compact", "full"):
        raise EditError("INVALID_ARGUMENT", "budget must be 'compact' or 'full'", field="budget", allowed=["compact", "full"])
    st = sv.load()
    s = sv.summary(st, full=True)
    cap = (lambda items, n: items) if budget == "full" else (lambda items, n: items[:n])          # noqa: E731
    ids = [o["id"] for o in st["ops"]]
    lines = [f"{st['width']}x{st['height']}@{st['fps']}, template {s['template']}{' (motion on)' if s.get('motion') else ''}, {s['duration_s']:g} s, revision {s['revision']}, {s['op_count']} edits"]
    src = {k: v for k, v in st["sources"].items()}
    lines.append("sources: " + ", ".join(f"{k} ({v['duration_s']:g} s{', audio' if v.get('has_audio') else ''})" for k, v in cap(list(src.items()), 8)) + (f" … +{len(src) - 8}" if budget == "compact" and len(src) > 8 else ""))
    for e in cap(s["entries"], 10):
        lines.append(f"clip {e['index']} [{e['id']}] {e['source']} {e['start_s']:g}-{e['end_s']:g} s (source from {e['source_in_s']:g} s)")
    if budget == "compact" and len(s["entries"]) > 10:
        lines.append(f"… +{len(s['entries']) - 10} more clips")
    if s["crossfades"]:
        lines.append("transitions: " + ", ".join(f"{c['between'][0]}→{c['between'][1]} {c['dur_s']:g} s" for c in s["crossfades"]))
    if s["fade"]:
        lines.append(f"fade in {s['fade']['in']:g} s, out {s['fade']['out']:g} s")
    groups = {}
    for o in s["overlays"]:
        groups.setdefault(o["kind"], []).append(o)
    for kind, items in groups.items():
        lines.append(f"{kind} x{len(items)}: " + "; ".join(f"[{ids[o['op']]}] {o.get('text') or o.get('callout') or o.get('graphic') or o.get('image') or o.get('source') or ''} {o['start_s']:g}-{o['end_s']:g} s".replace("  ", " ")
                                                         for o in cap(items, 6)) + (f" … +{len(items) - 6}" if budget == "compact" and len(items) > 6 else ""))
    if s["audio"]:
        lines.append(f"audio x{len(s['audio'])}: " + "; ".join(f"[{ids[a['op']]}] {a['name']} {a['start_s']:g}-{a['end_s']:g} s {a['volume_db']:g} dB" for a in cap(s["audio"], 5)) + (f" … +{len(s['audio']) - 5}" if budget == "compact" and len(s["audio"]) > 5 else ""))
    for w in cap(s["warnings"], 4):
        lines.append("warning: " + w)
    return {"revision": s["revision"], "op_count": s["op_count"], "duration_s": s["duration_s"], "text": "\n".join(lines)}
