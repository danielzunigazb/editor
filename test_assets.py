#!/usr/bin/env python3
"""Asset library: manifest validity, hash verification, filters, credits, and add_audio(asset=...) end to end through the MCP server.
Uses the staged audio (assets_stage/out) through a throw-away cache, so it needs no R2 access.
Run: .venv/bin/python test_assets.py"""
import asyncio, hashlib, json, os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE = os.path.join(HERE, "assets_stage", "out")
TMP = tempfile.mkdtemp(prefix="assets_test_")
CACHE = os.path.join(TMP, "cache")
os.environ["MLT_ASSETS_CACHE"] = CACHE
os.environ.pop("R2_WORKER_URL", None); os.environ.pop("R2_UPLOAD_TOKEN", None)
sys.path.insert(0, HERE)
import assets_lib  # noqa: E402

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def seed(it):
    dest = os.path.join(CACHE, it["kind"], it["file"])
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy(os.path.join(STAGE, it["file"]), dest)
    return dest


def unit():
    items = assets_lib.manifest()["assets"]
    check("manifest has music and sfx", {i["kind"] for i in items} == {"music", "sfx"} and len(items) >= 80, len(items))
    check("ids unique", len({i["id"] for i in items}) == len(items))
    need = ("id", "kind", "title", "author", "source_url", "license", "license_url", "license_evidence", "sha256", "bytes", "duration_s", "file", "r2_key", "themes", "moods")
    check("every asset has licence, evidence, sha256 and an r2 key", all(all(i.get(k) not in (None, "", []) or k == "moods" for k in need) for i in items))
    check("nothing shorter than the 0.1 s the engine accepts", all(i["duration_s"] >= 0.1 for i in items), min(i["duration_s"] for i in items))
    check("sha256 looks like sha256", all(len(i["sha256"]) == 64 for i in items))
    check("only CC0 / CC-BY-4.0", {i["license"] for i in items} <= {"CC0-1.0", "CC-BY-4.0"}, {i["license"] for i in items})
    check("CC-BY always carries attribution text, CC0 never needs it", all(bool(i.get("attribution")) == i["license"].startswith("CC-BY") for i in items))
    check("r2 key follows kind/file", all(i["r2_key"] == f"assets/{i['kind']}/{i['file']}" for i in items))
    import themes
    check("auto_sfx finds an effect for every one of the 15 templates (whoosh/swoosh, else pop, click or any)", all(assets_lib.find(assets_lib.auto_sfx(t_))["kind"] == "sfx" for t_ in themes.NAMES))
    check("auto_sfx is stable (same id twice)", assets_lib.auto_sfx("tech") == assets_lib.auto_sfx("tech"))
    check("every template (all 15) has at least 2 pieces of music and 3 effects", all(assets_lib.listing("music", theme=t)[1] >= 2 and assets_lib.listing("sfx", theme=t)[1] >= 3 for t in themes.NAMES), [t for t in themes.NAMES if assets_lib.listing("music", theme=t)[1] < 2 or assets_lib.listing("sfx", theme=t)[1] < 3])
    check("staged files match the manifest hash", all(hashlib.sha256(open(os.path.join(STAGE, i["file"]), "rb").read()).hexdigest() == i["sha256"] for i in items if os.path.isfile(os.path.join(STAGE, i["file"]))))

    rows, total = assets_lib.listing("music", license="CC-BY")
    check("list filter by licence", total >= 10 and all(r["license"] == "CC-BY-4.0" and r["credit_required"] for r in rows))
    rows, _ = assets_lib.listing("sfx", license="CC0")
    check("sfx are CC0 with no credit needed", rows and all(not r["credit_required"] for r in rows))
    check("list filter by theme and query", assets_lib.listing("music", theme="tech")[1] >= 2 and assets_lib.listing("sfx", query="click")[1] >= 1)
    check("limit caps the rows but not the total", len(assets_lib.listing("sfx", limit=5)[0]) == 5 and assets_lib.listing("sfx", limit=5)[1] > 5)

    try:
        assets_lib.find("m-no-such")
        check("unknown asset rejected", False)
    except ValueError as e:
        check("unknown asset rejected with suggestions", "unknown asset" in str(e))

    cc0 = next(i for i in items if i["kind"] == "sfx")
    ccby = next(i for i in items if i["license"] == "CC-BY-4.0")
    p = seed(cc0)
    check("cached asset resolves without network", assets_lib.path(cc0["id"]) == p)
    open(p, "ab").write(b"x")                                    # corrupt the cache: it must be refetched, and with no credentials that is an error, not a silent use
    try:
        assets_lib.path(cc0["id"])
        check("corrupt cache is not used", False)
    except RuntimeError as e:
        check("corrupt cache is not used (refetch needs R2 credentials)", "R2_WORKER_URL" in str(e), str(e))
    seed(cc0)
    check("credit lines: CC-BY only, deduplicated", assets_lib.credit_lines([cc0["id"], ccby["id"], ccby["id"], "zzz"]) == [ccby["attribution"]])


async def e2e():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    env = {k: v for k, v in os.environ.items() if k != "DISPLAY"}
    env["MLT_EDITOR_HOME"] = os.path.join(TMP, "home")
    env["MLT_ASSETS_CACHE"] = CACHE
    items = assets_lib.manifest()["assets"]
    ccby = next(i for i in items if i["kind"] == "music" and i["license"] == "CC-BY-4.0" and i["themes"])
    sfx = next(i for i in items if i["kind"] == "sfx" and i["duration_s"] >= 0.3)
    seed(ccby); seed(sfx)
    clip = os.path.join(HERE, "media", "clip_a.mp4")
    params = StdioServerParameters(command=os.path.join(HERE, ".venv", "bin", "python"), args=[os.path.join(HERE, "server.py")], env=env)
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(_tool, **kw):
                res = await s.call_tool(_tool, kw)
                if res.isError:
                    return None, res.content[0].text
                if res.structuredContent is not None:
                    return res.structuredContent.get("result", res.structuredContent), None
                return json.loads(next(c.text for c in res.content if c.type == "text")), None

            _, err = await call("new_project")
            src, err = await call("import_clip", path=clip)
            _, err = await call("add_clip", source="S1", start_s=0.0, end_s=4.0)
            check("setup: clip imported and added", err is None and isinstance(src, dict), err or src)
            res, err = await call("list_assets", kind="music", license="CC-BY")
            check("list_assets(kind=music, license=CC-BY) over MCP", err is None and res["count"] >= 10 and all(i["credit_required"] for i in res["items"]), err or res)
            res, err = await call("list_assets", kind="bogus")
            check("list_assets rejects an unknown kind", err is not None and "kind" in err, err)
            res, err = await call("list_assets", kind="icon", query="arrow")
            check("list_assets still lists icons", err is None and res["count"] >= 2, err)
            res, err = await call("add_audio", asset=ccby["id"], start_s=0.0, dur_s=3.0, volume_db=-10)
            check("add_audio(asset=CC-BY) works and the timeline demands the credit", err is None and res.get("credits_required") == [ccby["attribution"]] and res["audio"][0]["name"] == ccby["title"], err or res)
            res, err = await call("add_audio", asset=sfx["id"], start_s=1.0, volume_db=-6)
            check("a CC0 effect adds no credit line", err is None and len(res["credits_required"]) == 1, err or res)
            _, err = await call("add_audio", asset=sfx["id"], path=clip)
            check("asset and path together are rejected", err is not None and "exactly one" in err, err)
            _, err = await call("add_audio")
            check("neither asset nor path is rejected", err is not None and "exactly one" in err, err)
            _, err = await call("add_audio", asset="m-nope")
            check("unknown asset id is rejected", err is not None and "unknown asset" in err, err)
            out = os.path.join(TMP, "o", "x.mp4")
            res, err = await call("export", output_path=out, quality="draft")
            credit = os.path.splitext(out)[0] + ".credits.txt"
            check("export writes <video>.credits.txt with the CC-BY text", err is None and os.path.isfile(credit) and ccby["attribution"] in open(credit, encoding="utf-8").read() and res.get("credits_file") == credit, err or res)
            a = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name", "-of", "csv=p=0", out], capture_output=True, text=True).stdout.strip()
            check("exported video has an audio stream", a != "", a)
            check("export reports the loudness measured on the file", res.get("loudness_lufs") is not None and -60 < res["loudness_lufs"] < 0, res)
            res_n, err = await call("export", output_path=os.path.join(TMP, "o", "norm.mp4"), quality="draft", master="loudnorm")
            check("export(master=loudnorm) lands within 2 LU of -16 LUFS", err is None and res_n and res_n.get("loudness_lufs") is not None and abs(res_n["loudness_lufs"] + 16.0) <= 2.0, err or res_n)
            _, err = await call("export", output_path=os.path.join(TMP, "o", "bad.mp4"), master="loud")
            check("export rejects an unknown master", err is not None and "master" in err, err)
            await call("undo"); await call("undo")                       # drop both audio ops again
            res2, err = await call("export", output_path=out, quality="draft", overwrite=True)
            check("re-export without CC-BY removes the stale credits file", err is None and not os.path.exists(credit) and "credits_required" not in res2, err or res2)
            wh_ = assets_lib.auto_sfx("tech")
            seed(assets_lib.by_id()[wh_])                                # the effect crossfade(sfx="auto") will pick for the tech template
            await call("new_project", width=1280, height=720, fps=25)
            await call("import_clip", path=clip, id="A"); await call("add_clip", source="A", end_s=3.0); await call("add_clip", source="A", start_s=1.0, end_s=4.0)
            await call("set_template", name="tech")
            res, err = await call("crossfade", first_index=0, dur_s=1.0, style="blinds-v", sfx="auto")
            au_ = res and res.get("audio")
            check("crossfade(sfx='auto') adds the template's whoosh where the transition starts (2.0 s)", err is None and au_ and abs(au_[0]["start_s"] - 2.0) < 0.05 and not res.get("credits_required"), err or res)
            _, err = await call("crossfade", first_index=0, dur_s=1.0, style="wipe-left", sfx="m-nope")
            check("crossfade rejects an unknown sfx id and changes nothing", err is not None and "unknown asset" in err, err)
            tl_, _ = await call("get_timeline")
            check("a rejected crossfade+sfx left the project as it was (still one crossfade and one audio item)", tl_ and len(tl_["audio"]) == 1 and len(tl_["crossfades"]) == 1, tl_)

def availability():
    saved = {k: os.environ.pop(k, None) for k in ("MLT_ASSETS_CACHE", "R2_WORKER_URL", "R2_UPLOAD_TOKEN")}
    try:
        os.environ["MLT_ASSETS_CACHE"] = tempfile.mkdtemp(prefix="empty_cache_")
        rows, total = assets_lib.listing("music")
        check("with an empty cache and no R2 access no music is available, and the list says so", total > 0 and not any(r["available"] for r in rows), [r["available"] for r in rows][:3])
        os.environ["R2_WORKER_URL"], os.environ["R2_UPLOAD_TOKEN"] = "https://example.invalid", "x"
        rows, _ = assets_lib.listing("music")
        check("with R2 access configured everything is fetchable, so available", all(r["available"] for r in rows))
        os.environ.pop("R2_WORKER_URL"); os.environ.pop("R2_UPLOAD_TOKEN")
        d = os.path.join(os.environ["MLT_ASSETS_CACHE"], "music"); os.makedirs(d)
        first = assets_lib.manifest()["assets"][0]
        kind = first["kind"]; os.makedirs(os.path.join(os.environ["MLT_ASSETS_CACHE"], kind), exist_ok=True)
        open(os.path.join(os.environ["MLT_ASSETS_CACHE"], kind, first["file"]), "wb").write(b"x")
        rows, _ = assets_lib.listing(kind)
        check("a cached asset is available and listed before the ones that are not", rows[0]["id"] == first["id"] and rows[0]["available"] and not rows[-1]["available"], [(r["id"], r["available"]) for r in rows][:2])
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


unit()
availability()
asyncio.run(e2e())
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{len(passed)} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
