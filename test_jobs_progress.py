#!/usr/bin/env python3
"""Long renders never block a tool call: over MLT_BLOCK_MAX_S an export/preview runs as a job, job_status(wait_s) holds the call instead of a polling loop, and a
running job reports percent and eta_s. Run: python3 test_jobs_progress.py"""
import os, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["MLT_EDITOR_HOME"] = tempfile.mkdtemp(prefix="jp_")
os.environ["MLT_BLOCK_MAX_S"] = "5"
os.environ["MLT_LOG"] = "off"
import server  # noqa: E402

A, B = os.path.join(HERE, "media", "clip_a.mp4"), os.path.join(HERE, "media", "clip_b.mp4")
ok = bad = 0


def check(name, cond, detail=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:300]}]" if not cond and detail else ""))


server.new_project(640, 360, 25)
server.import_clip(A, "A")
server.add_clip("A", 0, 4)
out = os.path.join(tempfile.mkdtemp(), "short.mp4")
r = server.export(out, quality="draft")
check("a short video (under the limit) still exports in the call", "job_id" not in r and os.path.exists(out), r)
server.add_clip("A", 0, 4)
out2 = os.path.join(tempfile.mkdtemp(), "long.mp4")
r = server.export(out2, quality="draft")
check("a video over the limit starts a job and says why", r.get("state") == "running" and "job_id" in r and "over 5 s" in r.get("note", ""), r)
seen = []
t0 = time.time()
while time.time() - t0 < 120:
    s = server.job_status(r["job_id"], wait_s=0.5)
    seen.append(s)
    if s["state"] != "running":
        break
check("the job finishes and the result is the same as a blocking export", s["state"] == "done" and abs(s["result"]["duration_s"] - 8.0) < 0.2 and os.path.exists(out2), s)
check("while it ran it reported percent (0-100) and never above 99 before finishing", all(0 <= x.get("percent", 0) < 100 for x in seen if x["state"] == "running"), [x.get("percent") for x in seen])
check("job_status(wait_s) held the call (fewer polls than a 0 s loop would need)", len(seen) < 40, len(seen))
t = time.time()
s = server.job_status(r["job_id"], wait_s=30)
check("waiting on a finished job returns at once", time.time() - t < 2 and s["state"] == "done")
rp = server.render_preview()
check("a preview of a long video is also a job", rp.get("state") == "running" and "job_id" in rp, rp)
s = server.job_status(rp["job_id"], wait_s=45)
check("...and waiting for it returns its result", s["state"] == "done" and "qa" in s["result"], s)
# cancelling a render in the middle leaves nothing behind (its FIFO dir used to stay in /tmp: SIGTERM cannot run a `finally` while MLT is in C code)
import glob
before = set(glob.glob(os.path.join(tempfile.gettempdir(), "mltfifo_*")))
for _ in range(6):
    server.add_clip("A", 0, 4)                              # long enough that the render is still going when we look
rc = server.export(os.path.join(tempfile.mkdtemp(), "cancelled.mp4"), quality="draft")
time.sleep(2)
mid = glob.glob(os.path.join(os.environ["MLT_EDITOR_HOME"], "jobs", "*.tmp", "mltfifo_*"))
check("while a job renders, its FIFO dir is inside the job's own temp dir (not loose in /tmp)", len(mid) == 1 and set(glob.glob(os.path.join(tempfile.gettempdir(), "mltfifo_*"))) == before, mid)
server.cancel_job(rc["job_id"])
time.sleep(1)
check("cancel_job leaves no FIFO dir and no job temp dir behind", set(glob.glob(os.path.join(tempfile.gettempdir(), "mltfifo_*"))) == before
      and not glob.glob(os.path.join(os.environ["MLT_EDITOR_HOME"], "jobs", "*.tmp")), glob.glob(os.path.join(tempfile.gettempdir(), "mltfifo_*")))
print(f"\n{ok} passed, {bad} failed")
sys.exit(1 if bad else 0)
