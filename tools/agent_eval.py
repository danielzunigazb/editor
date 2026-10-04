#!/usr/bin/env python3
"""Does a real model edit more reliably with this server than with an older one? Runs `claude -p` on a few editing tasks against a server version and
measures what it can: whether the result is right (checked on the project the model left, with that version's own engine), the number of turns, the
tool calls that came back as errors, and the cost. The same prompt goes to both versions; it never mentions features of either.
Usage: tools/agent_eval.py --code-root <dir with server.py> --label before|after [--task NAME ...] [--max-usd 0.8] [--out results.json]
It spends API money (a task is typically 0.1-0.5 USD); nothing is run without being asked."""
import argparse, json, os, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEDIA = os.path.join(HERE, "media")
A, B = os.path.join(MEDIA, "clip_a.mp4"), os.path.join(MEDIA, "clip_b.mp4")

PREP = {   # tasks that start from a project that already exists (built through the version's own tools before the model sees it)
    "edit_a_finished_project": f"""
server.new_project(1280, 720, 25); server.import_clip({A!r}, "A"); server.import_clip({B!r}, "B")
server.add_clip("A", 0, 4); server.add_clip("B", 0, 4); server.crossfade(0, 0.5)
server.add_lower_third("Ana Ruiz", "Directora", 4.5, 3.0)          # 1 s into B
server.add_text("Gracias", 6.0, 1.0, position="top")                # 2.5 s into B
server.set_fades(0.0, 0.5)
""",
}

TASKS = {
    "edit_a_finished_project": (
        "Using the video editor tools: the project is already built (look at it first). Shorten clip A so that it is only 3 seconds long. The lower third "
        "and the text 'Gracias' were placed over specific moments of clip B: after your change they must still appear at those same moments of B (not at "
        "the same clock time). Do not rebuild the project from scratch and do not export. Finish by saying what you changed.",
        "finished_project_edit"),
    "overlay_survives_a_cut": (
        f"Using the video editor tools: create a 1280x720 25 fps project, import {A} as A and {B} as B. Put the first 4 s of A and then the first 4 s of B on the "
        f"timeline with a 0.5 s crossfade between them. Add a lower third (title 'Ana Ruiz', subtitle 'Directora') that appears exactly 1 s after clip B "
        f"starts and lasts 3 s. THEN shorten clip A so it is only 3 s long. The lower third must still appear 1 s after B starts. Do not export. "
        f"When done, say in one sentence at what timeline time the lower third starts.",
        "lower_third_one_second_into_B"),
    "fix_the_right_edit": (
        f"Using the video editor tools: create a 1280x720 25 fps project, import {A} as A. Put the first 5 s of A on the timeline. Add four text captions "
        f"'Uno' at 0.5 s, 'Dos' at 1.5 s, 'Tres' at 2.5 s and 'Cuatro' at 3.5 s, each lasting 1 s, then remove the caption 'Dos' and "
        f"change 'Tres' to say 'Tres y medio' without changing when it appears. Do not export. Finish by listing the captions that remain.",
        "captions_uno_tres_y_medio_cuatro"),
    "short_piece": (
        f"Using the video editor tools: create a 1280x720 25 fps project, import {A} as A and {B} as B. Timeline: first 3 s of A, then 3 s of B with a 0.5 s "
        f"crossfade, a text 'Fin del viaje' near the end of B for 1.5 s, and fade out for the last 0.5 s. Check the result with a contact sheet, "
        f"and export a draft quality file to {{out}}. Say how long the exported video is.",
        "exported_about_5_5_seconds"),
}


def check_state(code_root, home, which):
    """Evaluate the project the model left, with that version's own code (a subprocess in code_root). Returns (ok, detail)."""
    code = f'''
import json, os, sys
os.environ["MLT_EDITOR_HOME"] = {home!r}
sys.path.insert(0, {code_root!r})
import server, live
st = server.load(); server.bind(st); m = live.layout(st["ops"])
texts = sorted((L["text"], round(L["start"], 2)) for L in m["layers"] if L["kind"] == "text")
lt = [L for L in m["layers"] if L["kind"] == "graphic" and L["gk"] == "lower_third"]
entries = [(e["src"], round(e["start"], 3), round(e["dur"], 3)) for e in m["entries"]]
print(json.dumps({{"texts": texts, "lt": [round(L["start"], 3) for L in lt], "entries": entries, "total": round(m["total"], 3)}}))
'''
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=code_root)
    try:
        d = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, f"could not read the project: {r.stderr[-200:]}"
    if which == "lower_third_one_second_into_B":
        b = next((e for e in d["entries"] if e[0] == "B"), None)
        a = next((e for e in d["entries"] if e[0] == "A"), None)
        good = bool(b and len(d["lt"]) == 1 and abs(d["lt"][0] - (b[1] + 1.0)) < 0.05 and a and abs(a[2] - 3.0) < 0.05)
        return good, d
    if which == "finished_project_edit":
        b = next((e for e in d["entries"] if e[0] == "B"), None)
        a = next((e for e in d["entries"] if e[0] == "A"), None)
        tx = {x[0]: x[1] for x in d["texts"]}
        good = bool(a and b and abs(a[2] - 3.0) < 0.05 and len(d["lt"]) == 1 and abs(d["lt"][0] - (b[1] + 1.0)) < 0.05 and "Gracias" in tx and abs(tx["Gracias"] - (b[1] + 2.5)) < 0.05)
        return good, d
    if which == "captions_uno_tres_y_medio_cuatro":
        t = {x[0]: x[1] for x in d["texts"]}
        return set(t) == {"Uno", "Tres y medio", "Cuatro"} and abs(t["Tres y medio"] - 2.5) < 0.05 and abs(t["Uno"] - 0.5) < 0.05 and abs(t["Cuatro"] - 3.5) < 0.05, d
    if which == "exported_about_5_5_seconds":
        outs = [f for f in os.listdir(home) if f.endswith(".mp4") and f.startswith("eval_out")]
        if not outs:
            return False, "no exported file"
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", os.path.join(home, outs[0])], capture_output=True, text=True).stdout.strip() or 0)
        return abs(dur - 5.5) < 0.2 and any(x[0] == "Fin del viaje" for x in d["texts"]), {**d, "export_s": dur}
    return False, "unknown task"


def run(label, code_root, name, max_usd, timeout):
    prompt, which = TASKS[name]
    home = tempfile.mkdtemp(prefix=f"eval_{label}_")
    out = os.path.join(home, "eval_out.mp4")
    cfg = os.path.join(home, "mcp.json")
    json.dump({"mcpServers": {"mlt": {"type": "stdio", "command": sys.executable, "args": [os.path.join(code_root, "server.py")], "env": {"MLT_EDITOR_HOME": home}}}}, open(cfg, "w"))
    if name in PREP:
        r0 = subprocess.run([sys.executable, "-c", f"import os, sys\nos.environ['MLT_EDITOR_HOME']={home!r}\nsys.path.insert(0, {code_root!r})\nimport server\n{PREP[name]}"],
                            capture_output=True, text=True, cwd=code_root)
        if r0.returncode:
            return {"label": label, "task": name, "correct": False, "detail": "could not prepare the project: " + r0.stderr[-300:], "tool_calls": 0, "tool_errors": 0, "turns": 0}
    t0 = time.time()
    cmd = ["claude", "-p", prompt.replace("{out}", out), "--mcp-config", cfg, "--strict-mcp-config", "--allowedTools", "mcp__mlt__*", "--tools", "", "--output-format", "stream-json",
           "--verbose", "--max-budget-usd", str(max_usd), "--no-session-persistence"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, cwd=tempfile.gettempdir())
    calls = errors = 0
    kinds, final = {}, {}
    for line in r.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "assistant":
            for blk in ev["message"]["content"]:
                if blk.get("type") == "tool_use":
                    calls += 1
        elif ev.get("type") == "user":
            for blk in (ev.get("message", {}).get("content") or []):
                if isinstance(blk, dict) and blk.get("type") == "tool_result" and blk.get("is_error"):
                    errors += 1
                    txt = blk.get("content")
                    txt = txt if isinstance(txt, str) else " ".join(x.get("text", "") for x in txt if isinstance(x, dict))
                    key = next((c for c in ("REVISION_CONFLICT", "OUT_OF_RANGE", "UNKNOWN_OP", "TIMELINE_CONFLICT", "TEXT_DOES_NOT_FIT", "INVALID_ARGUMENT", "UNKNOWN_SOURCE", "UNKNOWN_NAME") if c in txt), "other")
                    kinds[key] = kinds.get(key, 0) + 1
        elif ev.get("type") == "result":
            final = ev
    good, detail = check_state(code_root, home, which)
    return {"label": label, "task": name, "correct": good, "tool_calls": calls, "tool_errors": errors, "error_kinds": kinds, "turns": final.get("num_turns"),
            "cost_usd": round(final.get("total_cost_usd") or 0, 3), "seconds": round(time.time() - t0, 1), "stopped": final.get("subtype"), "detail": detail,
            "answer": (final.get("result") or "")[:300]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-root", default=HERE)
    ap.add_argument("--label", default="after")
    ap.add_argument("--task", nargs="*", default=list(TASKS))
    ap.add_argument("--max-usd", type=float, default=0.8)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    results = []
    for t in a.task:
        r = run(a.label, a.code_root, t, a.max_usd, a.timeout)
        results.append(r)
        print(json.dumps({k: v for k, v in r.items() if k not in ("detail", "answer")}), flush=True)
    if a.out:
        json.dump(results, open(a.out, "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
