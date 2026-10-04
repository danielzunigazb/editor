"""Background jobs (long exports, preview renders): each runs in its own process, on a snapshot of the project taken when it was started, so an edit made
meanwhile cannot change it and a tool call is never held up. State lives in HOME/jobs/<id>.json (written by the job when it ends) next to the spec.
Run by `python -m mltedit.jobs <spec.json>`; started, polled and cancelled through start/status/cancel."""
import json, os, signal, subprocess, sys, time, uuid

_PROCS = {}


def _dir(home):
    d = os.path.join(home, "jobs")
    os.makedirs(d, exist_ok=True)
    return d


def _read(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(path, data):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def start(home, kind, st, args):
    jid = f"job_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    spec = os.path.join(_dir(home), jid + ".spec.json")
    status = os.path.join(_dir(home), jid + ".json")
    _write(spec, {"id": jid, "kind": kind, "home": home, "state": st, "args": args})
    _write(status, {"id": jid, "kind": kind, "state": "running", "started": time.time(), "args": args})
    proc = subprocess.Popen([sys.executable, "-m", "mltedit.jobs", spec], cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=open(os.path.join(_dir(home), jid + ".log"), "w"), env={**os.environ, "MLT_EDITOR_HOME": home})
    _PROCS[jid] = proc
    _write(status, {"id": jid, "kind": kind, "state": "running", "started": time.time(), "pid": proc.pid, "args": args})
    return {"job_id": jid, "state": "running", "kind": kind, "note": "poll job_status(job_id); cancel_job(job_id) stops it"}


def status(home, jid):
    path = os.path.join(_dir(home), jid + ".json")
    s = _read(path)
    if s is None:
        from .errors import EditError
        raise EditError("UNKNOWN_OP", f"no job '{jid}'", hint="list_jobs")
    if s["state"] == "running":
        proc = _PROCS.get(jid)
        alive = proc.poll() is None if proc is not None else _pid_alive(s.get("pid"))
        if not alive:                                        # it died without writing its result (killed, or the machine restarted)
            s = _read(path) or s
            if s["state"] == "running":
                s = {**s, "state": "failed", "error": "the job process ended without a result (see the job log)"}
                _write(path, s)
        s["elapsed_s"] = round(time.time() - s["started"], 1)
    return {k: v for k, v in s.items() if k != "started"} | ({"elapsed_s": s.get("elapsed_s")} if "elapsed_s" in s else {})


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def cancel(home, jid):
    s = status(home, jid)
    if s["state"] != "running":
        return {**s, "note": "the job was not running; nothing to cancel"}
    path = os.path.join(_dir(home), jid + ".json")
    full = _read(path)
    try:
        os.killpg(os.getpgid(full["pid"]), signal.SIGTERM)
    except (OSError, KeyError):
        pass
    proc = _PROCS.get(jid)
    if proc is not None:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    out = (full.get("args") or {}).get("out")
    if out and os.path.exists(out):                          # a half-written file must not look like a result
        os.remove(out)
    full.update(state="cancelled")
    _write(path, full)
    return {"job_id": jid, "state": "cancelled"}


def list_all(home):
    out = []
    for n in sorted(os.listdir(_dir(home)), reverse=True):
        if n.endswith(".json") and not n.endswith(".spec.json"):
            try:
                out.append(status(home, n[:-5]))
            except ValueError:
                pass
    return out


def main(spec_path):
    with open(spec_path) as f:
        spec = json.load(f)
    os.environ["MLT_EDITOR_HOME"] = spec["home"]
    status_path = spec_path.replace(".spec.json", ".json")
    base = _read(status_path) or {}
    try:
        import server                                       # noqa: E402  (a separate process: it may own its stdout)
        from mltedit.tools import review                    # noqa: E402
        st, args = spec["state"], spec["args"]
        res = review.do_export(st, args["out"], args["quality"], args["master"]) if spec["kind"] == "export" else review.do_preview(st)
        _ = server
        _write(status_path, {**base, "state": "done", "result": res})
    except Exception as e:  # noqa: BLE001
        _write(status_path, {**base, "state": "failed", "error": f"{type(e).__name__}: {e}"})
        raise


if __name__ == "__main__":
    main(sys.argv[1])
