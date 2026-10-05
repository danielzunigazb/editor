"""Background jobs (long exports, preview renders): each runs in its own process, on a snapshot of the project taken when it was started, so an edit made
meanwhile cannot change it and a tool call is never held up. State lives in HOME/jobs/<id>.json (written by the job when it ends) next to the spec.
Run by `python -m mltedit.jobs <spec.json>`; started, polled and cancelled through start/status/cancel."""
import json, os, re, shutil, signal, subprocess, sys, time, uuid

from .config import S

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


def scratch(home, jid):
    """The job's own temp dir (its TMPDIR): the render's private FIFO dir lives in it, so however the job ends (done, failed, cancelled by SIGTERM while MLT is
    inside C code and no `finally` can run, killed) deleting this one directory leaves nothing behind in /tmp."""
    return os.path.join(_dir(home), jid + ".tmp")


def start(home, kind, st, args):
    jid = f"job_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    os.makedirs(scratch(home, jid), exist_ok=True)
    spec = os.path.join(_dir(home), jid + ".spec.json")
    status = os.path.join(_dir(home), jid + ".json")
    _write(spec, {"id": jid, "kind": kind, "home": home, "state": st, "args": args})
    _write(status, {"id": jid, "kind": kind, "state": "running", "started": time.time(), "args": args})
    limit = ["timeout", "-k", "10", str(S.render_timeout_s)] if shutil.which("timeout") else []     # the job cannot run longer than MLT_RENDER_TIMEOUT_S
    proc = subprocess.Popen([*limit, sys.executable, "-m", "mltedit.jobs", spec], cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=open(os.path.join(_dir(home), jid + ".log"), "w"), env={**os.environ, "MLT_EDITOR_HOME": home, "TMPDIR": scratch(home, jid)})
    _PROCS[jid] = proc
    _write(status, {"id": jid, "kind": kind, "state": "running", "started": time.time(), "pid": proc.pid, "args": args})
    return {"job_id": jid, "state": "running", "kind": kind, "note": "job_status(job_id, wait_s=30) waits for it and shows percent/eta_s; cancel_job(job_id) stops it"}


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
                took = time.time() - s["started"]
                s = {**s, "state": "failed", "error": (f"LIMIT_EXCEEDED: the job ran for {took:.0f} s and was stopped at the render time limit of {S.render_timeout_s} s "
                                                       f"(MLT_RENDER_TIMEOUT_S)" if took >= S.render_timeout_s - 2 else "the job process ended without a result (see the job log)")}
                _write(path, s)
                shutil.rmtree(scratch(home, jid), ignore_errors=True)
        s["elapsed_s"] = round(time.time() - s["started"], 1)
    if s["state"] == "running":
        s.update(_progress(home, jid, s))
    return {k: v for k, v in s.items() if k not in ("started", "pid")} | ({"elapsed_s": s.get("elapsed_s")} if "elapsed_s" in s else {})


def progress_path(home, jid):
    return os.path.join(_dir(home), jid + ".progress")


def _progress(home, jid, s):
    """{'percent', 'eta_s'} of a running job from the `out_time_us=` lines ffmpeg wrote, against the length the job will produce (args.total_s)."""
    total = (s.get("args") or {}).get("total_s")
    try:
        with open(progress_path(home, jid), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            tail = f.read().decode(errors="replace")
    except OSError:
        return {"percent": 0.0} if total else {}
    done = [int(x) for x in re.findall(r"out_time_us=(\d+)", tail)]
    if not total or not done:
        return {"percent": 0.0} if total else {}
    pct = max(0.0, min(99.0, done[-1] / 1e6 / total * 100))
    el = time.time() - s["started"]
    return {"percent": round(pct, 1), **({"eta_s": round(el * (100 - pct) / pct, 1)} if pct >= 1 else {})}


def wait(home, jid, wait_s, max_s):
    """status(), but held for up to min(wait_s, max_s) seconds while the job is running: one call instead of a polling loop."""
    end = time.time() + max(0.0, min(float(wait_s), max_s))
    s = status(home, jid)
    while s["state"] == "running" and time.time() < end:
        time.sleep(0.5)
        s = status(home, jid)
    return s


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
    shutil.rmtree(scratch(home, jid), ignore_errors=True)    # the FIFO dir of a render that was cut off in the middle
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
        prog = progress_path(spec["home"], spec["id"])
        res = review.do_export(st, args["out"], args["quality"], args["master"], progress=prog) if spec["kind"] == "export" else review.do_preview(st, progress=prog)
        _ = server
        _write(status_path, {**base, "state": "done", "result": res})
    except Exception as e:  # noqa: BLE001
        _write(status_path, {**base, "state": "failed", "error": f"{type(e).__name__}: {e}"})
        raise
    finally:
        shutil.rmtree(scratch(spec["home"], spec["id"]), ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1])
