#!/usr/bin/env python3
"""Read the state of a project a model left behind, with the engine, and print it as JSON: project settings, the layout (entries, layers, audios...), and every
exported `eval_out*.mp4` in the project folder probed with ffprobe, the render QA and ebur128 loudness. Usage: dump.py <project home>."""
import json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)


def probe_export(path, m):
    from mltedit import qa
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,avg_frame_rate,duration:format=duration", "-of", "json", path],
                       capture_output=True, text=True)
    d = json.loads(r.stdout)
    n, den = d["streams"][0]["avg_frame_rate"].split("/")
    joined = set(m["xfades"])
    expected = [e["start"] for i, e in enumerate(m["entries"]) if i > 0 and (i - 1) not in joined]
    res = {"file": os.path.basename(path), "duration_s": round(float(d["format"]["duration"]), 3), "width": d["streams"][0]["width"], "height": d["streams"][0]["height"],
           "fps": round(float(n) / float(den), 3)}
    q = qa.check(path, expected)
    res["qa_findings"], res["qa_notes"] = q["findings"], q.get("notes", [])
    e = subprocess.run(["ffmpeg", "-nostats", "-i", path, "-af", "ebur128", "-f", "null", "-"], capture_output=True, text=True).stderr
    m_ = re.findall(r"I:\s+(-?[\d.]+) LUFS", e)
    res["lufs"] = float(m_[-1]) if m_ else None
    return res


def main(home):
    os.environ["MLT_EDITOR_HOME"] = home
    os.environ["MLT_LOG"] = "off"
    import server, live
    st = server.load()
    server.bind(st)
    m = live.layout(st["ops"])
    exports = [probe_export(os.path.join(home, f), m) for f in sorted(os.listdir(home)) if f.startswith("eval_out") and f.endswith(".mp4")]
    layout = {k: v for k, v in m.items() if k in ("entries", "layers", "audios", "xfades", "fade", "pip", "warnings", "total")}
    print("STATE " + json.dumps({"project": {"width": st["width"], "height": st["height"], "fps": st["fps"], "theme": (st["theme"] or {}).get("name") if isinstance(st["theme"], dict) else st["theme"], "revision": st.get("revision", 0), "ops": len(st["ops"])},
                                 "layout": layout, "exports": exports, "sources": {k: v.get("path") for k, v in st["sources"].items()}}, default=str))


if __name__ == "__main__":
    main(sys.argv[1])
