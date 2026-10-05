#!/usr/bin/env python3
"""Validation of the editor with a real model on real footage. Each (task, model, repetition) is a `claude -p` run against a fresh project folder through the MCP
server; afterwards dump.py reads what the model left and tasks.py judges it. Metrics per run: success (every check), turns, tool calls, errors by code, get_still /
get_contact_sheet calls, USD, seconds, and for a failure which checks failed. Nothing runs without being asked, every run has a hard --max-usd and the whole batch a
--budget-usd: when the total spent reaches it the remaining runs are skipped (and said so).

  run.py --reference                       build each task's reference solution through the server API and check it (no model, no cost): proves the checks hold
  run.py --models claude-sonnet-5-5 --reps 1 [--task a b] [--jobs 2] [--budget-usd 25] [--max-usd 1.5] [--out file.json] [--code-root dir] [--toolset core]
  run.py --summary file.json [more.json]   print the table of a finished batch"""
import argparse, concurrent.futures as cf, json, os, shutil, subprocess, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import corpus, tasks  # noqa: E402

CODES = ("REVISION_CONFLICT", "OUT_OF_RANGE", "UNKNOWN_OP", "TIMELINE_CONFLICT", "TEXT_DOES_NOT_FIT", "INVALID_ARGUMENT", "UNKNOWN_SOURCE", "UNKNOWN_NAME", "PATH_NOT_ALLOWED",
         "LIMIT_EXCEEDED", "SOURCE_CHANGED", "SOURCE_MISSING", "NOTHING_TO_DO", "INTERNAL")
PY = os.path.join(ROOT, ".venv", "bin", "python")
if not os.path.exists(PY):
    PY = sys.executable


def sh_env(home, extra=None):
    return {**os.environ, "MLT_EDITOR_HOME": home, "MLT_LOG": "off", "MLT_EDITOR_ROOTS": ROOT, **(extra or {})}     # the fence stays ON: footage lives under poc_mlt


def with_display(cmd):
    return cmd if os.environ.get("DISPLAY") else ["xvfb-run", "-a"] + cmd


def server_snippet(c, code_root, body, out=""):
    paths = {k: v["abs"] for k, v in c["clips"].items()}
    return (f"import os, sys\nsys.path.insert(0, {code_root!r})\nimport server\nP = {paths!r}\nSRT = {c['srt_abs']!r}\nOUT = {out!r}\n{body}\n")


def read_state(home, code_root):
    r = subprocess.run(with_display([PY, os.path.join(HERE, "dump.py"), home]), capture_output=True, text=True, cwd=code_root, env=sh_env(home), timeout=600)
    line = next((x for x in r.stdout.splitlines() if x.startswith("STATE ")), None)
    if not line:
        return None, (r.stderr or r.stdout)[-400:]
    return json.loads(line[6:]), ""


def prepare(home, c, code_root, body, out=""):
    r = subprocess.run(with_display([PY, "-c", server_snippet(c, code_root, body, out)]), capture_output=True, text=True, cwd=code_root, env=sh_env(home), timeout=600)
    return r.returncode == 0, (r.stderr or "")[-400:]


def judge(task, home, c, code_root):
    state, err = read_state(home, code_root)
    if state is None:
        return False, [{"name": "read_project", "ok": False, "detail": err}], None
    try:
        res = task["check"](state, c)
    except Exception as e:     # a check that cannot evaluate is a failure of the run, not a crash of the batch
        res = [("check_raised", False, repr(e))]
    return all(ok for n, ok, _ in res if not n.startswith("~")), [{"name": n, "ok": bool(ok), "detail": d} for n, ok, d in res], state


def reference(names, c):
    code_root, bad = ROOT, 0
    for name in names:
        t = tasks.TASKS[name]
        home = tempfile.mkdtemp(prefix=f"ref_{name}_")
        out = os.path.join(home, "eval_out.mp4")
        ok, err = prepare(home, c, code_root, t["prep"] + "\n" + t["ref"], out) if (t["prep"] or t["ref"]) else (False, "no reference")
        if not ok:
            print(f"{name:20s} REFERENCE FAILED TO BUILD: {err}")
            bad += 1
            continue
        good, res, _ = judge(t, home, c, code_root)
        print(f"{name:20s} {'ok' if good else 'FAIL'}  " + ("" if good else json.dumps([r for r in res if not r['ok']], ensure_ascii=False, default=str)[:500]))
        bad += not good
        shutil.rmtree(home, ignore_errors=True)
    return bad


INFRA_MARKS = ("hit your session limit", "usage limit", "rate limit", "rate_limit", "overloaded", "credit balance", "Invalid API key", "authentication")


def infra_problem(final, calls):
    """A run that never reached the model (account limit, API error) says nothing about the editor: returns what happened, else ''. Such runs are labelled
    infra, left out of every success rate, and stop the rest of the batch."""
    txt = (final.get("result") or "") if isinstance(final.get("result"), str) else ""
    if any(m.lower() in txt.lower()[:200] for m in INFRA_MARKS):        # also in the middle of a run: whatever the model did before is not a result
        return txt[:160]
    if not final:
        return "no result event from claude -p"
    return ""


class Budget:
    def __init__(self, total):
        self.total, self.spent, self.lock, self.blocked = total, 0.0, threading.Lock(), ""

    def add(self, x):
        with self.lock:
            self.spent += x

    def exhausted(self):
        with self.lock:
            return self.spent >= self.total or bool(self.blocked)


def parse_stream(stdout):
    calls = errors = 0
    kinds, tools, final = {}, {}, {}
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "assistant":
            for blk in ev["message"]["content"]:
                if blk.get("type") == "tool_use":
                    calls += 1
                    n = blk["name"].split("__")[-1]
                    tools[n] = tools.get(n, 0) + 1
        elif ev.get("type") == "user":
            for blk in (ev.get("message", {}).get("content") or []):
                if isinstance(blk, dict) and blk.get("type") == "tool_result" and blk.get("is_error"):
                    errors += 1
                    txt = blk.get("content")
                    txt = txt if isinstance(txt, str) else " ".join(x.get("text", "") for x in txt if isinstance(x, dict))
                    key = next((k for k in CODES if k in txt), "other")
                    kinds[key] = kinds.get(key, 0) + 1
        elif ev.get("type") == "result":
            final = ev
    return calls, errors, kinds, tools, final


def run_one(name, model, rep, c, a, budget):
    t = tasks.TASKS[name]
    base = {"task": name, "model": model, "rep": rep, "toolset": a.toolset or "default"}
    if budget.exhausted():
        return {**base, "skipped": budget.blocked or "budget exhausted"}
    home = tempfile.mkdtemp(prefix=f"val_{name}_")
    out = os.path.join(home, "eval_out.mp4")
    if t["prep"]:
        ok, err = prepare(home, c, a.code_root, t["prep"])
        if not ok:
            return {**base, "success": False, "failure_class": "harness", "detail": "could not prepare: " + err}
    env = {"MLT_EDITOR_HOME": home, "MLT_LOG": "off", "MLT_EDITOR_ROOTS": ROOT, **({"MLT_TOOLSET": a.toolset} if a.toolset else {})}
    cfg = os.path.join(home, "mcp.json")
    json.dump({"mcpServers": {"mlt": {"type": "stdio", "command": PY, "args": [os.path.join(a.code_root, "server.py")], "env": env}}}, open(cfg, "w"))
    prompt = t["prompt"](c).replace("{out}", out)
    cmd = ["claude", "-p", prompt, "--model", model, "--mcp-config", cfg, "--strict-mcp-config", "--allowedTools", "mcp__mlt__*", "--tools", "", "--output-format", "stream-json",
           "--verbose", "--max-budget-usd", str(a.max_usd), "--no-session-persistence"]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=a.timeout, stdin=subprocess.DEVNULL, cwd=tempfile.gettempdir())
        stdout, timed_out = r.stdout, False
    except subprocess.TimeoutExpired as e:
        stdout, timed_out = (e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")), True
    secs = round(time.time() - t0, 1)
    calls, errors, kinds, tools, final = parse_stream(stdout)
    cost = round(final.get("total_cost_usd") or 0, 3)
    budget.add(cost)
    problem = infra_problem(final, calls)
    if problem:
        budget.blocked = budget.blocked or f"stopped by an infrastructure problem: {problem}"
        shutil.rmtree(home, ignore_errors=True)
        return {**base, "infra": problem, "success": None, "seconds": secs}
    good, results, state = judge(t, home, c, a.code_root)
    failed = [r["name"] for r in results if not r["ok"] and not r["name"].startswith("~")]
    res = {**base, "success": good, "turns": final.get("num_turns"), "tool_calls": calls, "tool_errors": errors, "error_kinds": kinds, "tools": tools,
           "stills": tools.get("get_still", 0) + tools.get("get_contact_sheet", 0), "cost_usd": cost, "seconds": secs, "stopped": "timeout" if timed_out else final.get("subtype"),
           "failed_checks": failed, "checks": results, "answer": (final.get("result") or "")[:400],
           "export": (state or {}).get("exports", [{}])[0] if (state or {}).get("exports") else None}
    if not good:
        res["failure_class"] = "timeout" if timed_out else ("budget" if final.get("subtype", "").startswith("error_max_budget") else "outcome")
    if a.keep or not good:
        res["home"] = home          # kept for inspection; the harness never deletes a failed run
    else:
        shutil.rmtree(home, ignore_errors=True)
    return res


def summary(files):
    rows = []
    for f in files:
        rows += json.load(open(f))["runs"]
    infra = [r for r in rows if r.get("infra")]
    done = [r for r in rows if "skipped" not in r and not r.get("infra")]
    print(f"{len(done)} runs counted ({len(rows) - len(done) - len(infra)} skipped, {len(infra)} lost to infrastructure problems and NOT counted); total ${sum(r.get('cost_usd', 0) for r in done):.2f}")
    keys = sorted({(r["model"], r.get("toolset", "default")) for r in done})
    print(f"{'task':20s} " + " ".join(f"{m.split('-')[1][:6]+'/'+ts[:4]:>22s}" for m, ts in keys))
    for name in tasks.TASKS:
        cells = []
        for m, ts in keys:
            rs = [r for r in done if r["task"] == name and r["model"] == m and r.get("toolset", "default") == ts]
            cells.append("-" if not rs else f"{sum(bool(r['success']) for r in rs)}/{len(rs)} {sum(r.get('turns') or 0 for r in rs)/len(rs):.0f}t ${sum(r['cost_usd'] for r in rs)/len(rs):.2f}")
        print(f"{name:20s} " + " ".join(f"{x:>22s}" for x in cells))
    for m, ts in keys:
        rs = [r for r in done if r["model"] == m and r.get("toolset", "default") == ts]
        print(f"{m} [{ts}]: success {sum(bool(r['success']) for r in rs)}/{len(rs)}, tool errors {sum(r.get('tool_errors', 0) for r in rs)} in {sum(r.get('tool_calls', 0) for r in rs)} calls, "
              f"${sum(r.get('cost_usd', 0) for r in rs):.2f}, {sum(r.get('seconds', 0) for r in rs)/max(1, len(rs)):.0f}s avg")
    for r in done:
        if not r["success"]:
            print("FAIL", r["task"], r["model"], r.get("failure_class"), r.get("failed_checks"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", action="store_true")
    ap.add_argument("--summary", nargs="*")
    ap.add_argument("--task", nargs="*", default=list(tasks.TASKS))
    ap.add_argument("--models", nargs="*", default=["claude-sonnet-5-5"])
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--max-usd", type=float, default=1.5, help="hard cap of one run")
    ap.add_argument("--budget-usd", type=float, default=25.0, help="stop starting runs once this much was spent")
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--code-root", default=ROOT)
    ap.add_argument("--toolset", default="")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if a.summary:
        return summary(a.summary)
    c = corpus.load()
    if a.reference:
        sys.exit(1 if reference(a.task, c) else 0)
    budget = Budget(a.budget_usd)
    jobs = [(n, m, r) for r in range(a.reps) for n in a.task for m in a.models]
    runs = []
    with cf.ThreadPoolExecutor(a.jobs) as ex:
        futs = {ex.submit(run_one, n, m, r, c, a, budget): (n, m, r) for n, m, r in jobs}
        for f in cf.as_completed(futs):
            res = f.result()
            runs.append(res)
            print(json.dumps({k: v for k, v in res.items() if k in ("task", "model", "rep", "success", "turns", "tool_calls", "tool_errors", "cost_usd", "seconds", "failed_checks", "skipped", "infra")}, ensure_ascii=False), flush=True)
            if a.out:
                json.dump({"args": vars(a), "spent_usd": round(budget.spent, 3), "runs": runs}, open(a.out, "w"), indent=1, default=str, ensure_ascii=False)
    print(f"spent ${budget.spent:.2f}")


if __name__ == "__main__":
    main()
