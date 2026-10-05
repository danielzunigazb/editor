#!/usr/bin/env python3
"""A simulated user session through the web application's HTTP API with the user's REAL footage (personal, not in the repository): upload a talk and its subtitles, a scripted
model edits in one batch (a segment of the talk, a lower third, the subtitles), looks at a contact sheet, the user exports in the background and downloads. It measures what a
user would wait for and checks the downloaded file. The model is scripted (no API key here), so this is the product's plumbing on real media, not the quality of a model's edit.
Run: python3 tools/e2e_session.py [--json out.json]"""
import argparse, json, os, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["MLT_LOG"] = "off"
os.environ.pop("ANTHROPIC_API_KEY", None)
from app.model import ScriptedModel  # noqa: E402
from app_testlib import Server  # noqa: E402

TALK, SRT = os.path.join(HERE, "media_user", "charla.mp4"), os.path.join(HERE, "media_user", "charla.srt")
PHONE = os.path.join(HERE, "media_real", "real1.mp4")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    missing = [p for p in (TALK, SRT, PHONE) if not os.path.exists(p)]
    if missing:
        print(f"SKIP the real-footage session: {missing} not here (personal footage, not in the repository), so it was NOT run")
        return 0
    S = Server(max_upload_mb=2048)
    res, checks = {}, []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond)))
        print(("PASS " if cond else "FAIL ") + name + (f"  [{str(detail)[:300]}]" if not cond and detail else ""))

    try:
        t0 = time.time()
        pid = S.new_project("session", 1920, 1080, 25)
        res["create_project_s"] = round(time.time() - t0, 2)
        for path, name in ((TALK, "charla.mp4"), (SRT, "charla.srt"), (PHONE, "movil.mp4")):
            t0 = time.time()
            r = S.http.put(f"/api/projects/{pid}/uploads/{name}", content=open(path, "rb").read())
            mb = os.path.getsize(path) / 1e6
            res[f"upload_{name}"] = {"mb": round(mb, 1), "s": round(time.time() - t0, 2), "status": r.status_code}
            check(f"upload {name} ({mb:.0f} MB) accepted", r.status_code == 201, r.text[:200])
        srt_path = os.path.join(S.tmp, "projects", pid, "uploads", "charla.srt")
        model = ScriptedModel([
            [{"type": "text", "text": "Cutting the first 30 s of the talk, a lower third and the subtitles."},
             {"type": "tool_use", "name": "apply_ops", "input": {"ops": [
                 {"tool": "add_clip", "source": "charla", "start_s": 0, "end_s": 30},
                 {"tool": "add_lower_third", "title": "María Pérez", "subtitle": "Directora de producto", "start_s": 2, "dur_s": 4},
                 {"tool": "add_subtitles", "srt_path": srt_path}]}}],
            [{"type": "tool_use", "name": "get_contact_sheet", "input": {"count": 6}}],
            [{"type": "text", "text": "Here is the edit."}]])
        t0 = time.time()
        _, ev = S.chat(pid, "make a 30 s interview cut with subtitles", model)
        res["chat_s"] = round(time.time() - t0, 2)
        tr = [e for e in ev if e["type"] == "tool_result"]
        check("the batch edit and the contact sheet ran", len(tr) == 2 and all(e["ok"] for e in tr), [(e["name"], e["text"][:200]) for e in tr if not e["ok"]])
        check("the contact sheet came back as an image", any(e["images"] for e in tr))
        if not all(e["ok"] for e in tr):
            raise SystemExit("the edit failed; stopping before the export")
        t0 = time.time()
        r = S.http.post(f"/api/projects/{pid}/export", json={"quality": "draft", "name": "entrevista"})
        jid = r.json()["result"]["job_id"]
        pcts = []
        while time.time() - t0 < 900:
            j = S.http.get(f"/api/projects/{pid}/jobs/{jid}").json()["result"]
            if j["state"] != "running":
                break
            pcts.append(j.get("percent", 0))
            time.sleep(0.5)
        res["export_s"] = round(time.time() - t0, 2)
        check("the export job finishes", j["state"] == "done", j)
        res["export_progress_samples"] = len(pcts)
        check("it reported progress while it ran", len(pcts) >= 2 and pcts[-1] > pcts[0], pcts[:6])
        out = os.path.join(tempfile.mkdtemp(), "e.mp4")
        t0 = time.time()
        d = S.http.get(f"/api/projects/{pid}/exports/{r.json()['name']}")
        res["download_s"] = round(time.time() - t0, 2)
        open(out, "wb").write(d.content)
        pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,avg_frame_rate:format=duration", "-of", "json", out], capture_output=True, text=True).stdout)
        dur = float(pr["format"]["duration"])
        v = next(s for s in pr["streams"] if s["codec_type"] == "video")
        res["export"] = {"duration_s": round(dur, 2), "size": f"{v['width']}x{v['height']}", "mb": round(len(d.content) / 1e6, 1)}
        check("the downloaded file is 30 s of 1920x1080 video with sound", abs(dur - 30) < 0.3 and (v["width"], v["height"]) == (1920, 1080) and any(s["codec_type"] == "audio" for s in pr["streams"]), res["export"])
        qa = j["result"]["qa"]
        res["qa"] = {k: qa.get(k) for k in ("ok", "findings", "av_offset_ms", "correlation", "av_unmeasured", "notes")}
        check("the render QA found nothing wrong", qa["ok"] and not qa["findings"], qa)
        check("the sound is in sync with the picture (within 60 ms) or the check said why it could not measure", abs(qa.get("av_offset_ms", 0)) <= 60 or "av_unmeasured" in qa, qa)
        res["loudness_lufs"] = j["result"].get("loudness_lufs")
        res["cost_usd_scripted"] = S.http.get(f"/api/projects/{pid}").json()["usage"]["usd"]
    finally:
        S.stop()
    ok = sum(c for _, c in checks)
    print(f"\n{ok} passed, {len(checks) - ok} failed")
    print(json.dumps(res, indent=1))
    if a.json:
        json.dump({"checks": checks, "measured": res}, open(a.json, "w"), indent=1)
    return 0 if ok == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
