#!/usr/bin/env python3
"""Build the audio library once: pick tracks per template from three sources, download them, check the licence AT THE SOURCE, normalise,
hash, and write assets/manifest.json (+ stage files in assets_stage/ for upload). Not run by users; `fetch_assets.py` is.

Sources (all verified reachable; licences are read from each source's own pages/catalogue, never assumed):
  incompetech.com  Kevin MacLeod, CC BY 4.0 (catalogue pieces.json: feel, instruments, length) -> credit line required
  kenney.nl        CC0 sound packs (each pack page states "License Creative Commons CC0")
  opengameart.org  only items whose page lists CC0 as the licence
Dropped: FreePD (closed permanently in 2025: its home page says so), Pixabay/Mixkit (no public API, terms against bulk download).
Usage: python3 tools/curate_assets.py [music|oga|sfx|batch2|all] [--sources data/asset_sources.json] [--assets-dir D] [--stage-dir D]
(batch2 extends manifest.json; `all` does not run it). Sources, picks, moods and URLs live in data/asset_sources.json."""
import hashlib, html, json, os, re, subprocess, sys, time, urllib.parse, urllib.request, zipfile

import argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mltedit.config import S  # noqa: E402

ap = argparse.ArgumentParser(description="Build the audio library: pick, download, licence-check, normalise, hash.")
ap.add_argument("what", nargs="?", default="all", choices=("music", "oga", "sfx", "batch2", "all"))
ap.add_argument("--sources", default=os.path.join(S.data_root, "data", "asset_sources.json"), help="catalogue of sources, picks and moods (JSON)")
ap.add_argument("--assets-dir", default=os.path.dirname(S.assets_manifest), help="folder with manifest.json; stage_*.json files are written here")
ap.add_argument("--stage-dir", default=os.path.join(S.data_root, "assets_stage"), help="download and normalised-output work folder")
ARGS = ap.parse_args() if __name__ == "__main__" else ap.parse_args([])
ASSETS = ARGS.assets_dir
STAGE = ARGS.stage_dir
RAW, OUT = os.path.join(STAGE, "raw"), os.path.join(STAGE, "out")
SRC = json.load(open(ARGS.sources, encoding="utf-8"))
UA = SRC["user_agent"]
INC, OGA, KEN = SRC["incompetech"], SRC["opengameart"], SRC["kenney"]
for d in (RAW, OUT):
    os.makedirs(d, exist_ok=True)


def get(url, binary=False, tries=3):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60) as r:
                data = r.read()
            time.sleep(0.7)                                       # be polite to small free services
            return data if binary else data.decode("utf-8", "replace")
        except Exception as e:
            if k == tries - 1:
                raise RuntimeError(f"GET {url}: {e}")
            time.sleep(2 ** k)


def secs(l):
    h, m, s = (int(x) for x in l.split(":")); return h * 3600 + m * 60 + s


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:48]


def normalise_music(src, dst):
    """-16 LUFS integrated, true peak -1.5 dB, 48 kHz stereo MP3 160 kbit/s."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vn", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000", "-ac", "2", "-c:a", "libmp3lame", "-b:a", "160k", dst],
                       capture_output=True, text=True, timeout=300)
    if r.returncode or not os.path.exists(dst):
        raise RuntimeError("ffmpeg: " + r.stderr[-200:])


# ------------------------------------------------------------------------------------------------ incompetech (CC BY 4.0)
BAD_FEELS = {t: set(v) for t, v in SRC["bad_feels"].items()}     # moods that clash with a template
BAD_WORDS = SRC["bad_words"]
PICKS = {t: (set(v["feels"]), v["instruments"], v["refuse"], set(v["genres"]) if v["genres"] else None, v["count"]) for t, v in SRC["picks"].items()}
# theme: (feels any, instrument substrings wanted, instrument substrings refused, genre ids allowed, how many). Substrings, lower-case.


def curate_incompetech():
    pieces = json.loads(get(INC["pieces"]))
    chosen, used = [], set()
    for theme, (feels, ins_any, ins_none, genres, n) in PICKS.items():
        cands = []
        for p in pieces:
            L = secs(p["length"]); fe = {f.strip() for f in (p.get("feel") or "").split(",")}; ins = (p.get("instruments") or "").lower()
            desc = (p.get("description") or "").lower()
            if (not INC["length_s"][0] <= L <= INC["length_s"][1] or p["title"] in used or any(s in ins for s in ins_none) or (genres and p.get("genre") not in genres)
                    or (fe & BAD_FEELS.get(theme, set())) or any(w in desc for w in BAD_WORDS.get(theme, []))):
                continue
            wanted = [s for s in ins_any if s in ins]
            score = 2 * len(fe & feels) + 3 * len(wanted)
            if score >= 5 and fe & feels and wanted:
                cands.append((score, -abs(L - 150), p))
        cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
        for _, _, p in cands[:n]:
            used.add(p["title"]); chosen.append((theme, p))
    items = []
    for theme, p in chosen:
        fn = p["filename"]
        raw = os.path.join(RAW, "inc_" + slug(p["title"]) + ".mp3")
        if not os.path.exists(raw):
            open(raw, "wb").write(get(INC["mp3_base"] + urllib.parse.quote(fn), binary=True))
        if os.path.getsize(raw) < 300_000 or probe(raw) < 60:
            print("skip (bad download):", p["title"]); continue
        out = os.path.join(OUT, "m-" + slug(p["title"]) + ".mp3")
        if not os.path.exists(out):
            normalise_music(raw, out)
        moods = sorted({f.strip().lower() for f in (p.get("feel") or "").split(",") if f.strip()})
        items.append({"id": "m-" + slug(p["title"]), "kind": "music", "title": p["title"], "author": INC["author"], "source": INC["source"], "source_url": INC["mp3_base"] + urllib.parse.quote(fn),
                      "license": INC["license"], "license_url": INC["license_url"],
                      "license_evidence": INC["license_evidence"],
                      "attribution": INC["attribution"].format(title=p['title']),
                      "moods": moods, "themes": [t for t, q in chosen if q["title"] == p["title"]], "instruments": p.get("instruments"), "description": p.get("description"),
                      "duration_s": round(probe(out), 1), "bytes": os.path.getsize(out), "sha256": sha(out), "file": os.path.basename(out)})
    json.dump(items, open(os.path.join(ASSETS, "stage_music_ccby.json"), "w"), indent=1, ensure_ascii=False)
    return items


# ------------------------------------------------------------------------------------------------ OpenGameArt (CC0 only)
OGA_QUERIES = [tuple(q) for q in OGA["queries"]]


def curate_oga():
    items, seen_files = [], set()
    for theme, kw in OGA_QUERIES:
        url = OGA["search"].format(kw=urllib.parse.quote(kw))
        listing = get(url)
        links = []
        for l in re.findall(r'href="(/content/[^"#?]+)"', listing):
            if l not in links and l != "/content/faq":
                links.append(l)
        picked = None
        for link in links[:10]:
            page = get(OGA["base"] + link)
            blk = page[page.find("field-name-field-art-licenses"):][:700] if "field-name-field-art-licenses" in page else ""
            lic = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", blk)))
            if "CC0" not in lic.split("Collections")[0]:
                continue                                          # the page must list CC0 as ITS licence (not just a collection tag)
            files = [f for f in re.findall(r'href="(https://opengameart.org/sites/default/files/[^"]+\.(?:mp3|ogg))"', page) if f not in seen_files]
            if not files:
                continue
            title = html.unescape(re.sub(r"\s*\|\s*OpenGameArt.org.*", "", re.findall(r"<title>([^<]+)</title>", page)[0])).strip()
            users = re.findall(r'class="username"[^>]*>([^<]+)<', page)
            author = (users[1] if len(users) > 1 and users[0].startswith("Anonymous") else users[0]).strip() if users else "unknown"
            raw = os.path.join(RAW, "oga_" + slug(title) + os.path.splitext(files[0])[1])
            if not os.path.exists(raw):
                open(raw, "wb").write(get(files[0], binary=True))
            d = probe(raw)
            if not 45 <= d <= 300 or os.path.getsize(raw) > 14_000_000:
                continue
            picked = (link, title, author, files[0], raw, d, lic[:60]); seen_files.add(files[0]); break
        if not picked:
            print(f"no CC0 item found for {theme}/{kw}"); continue
        link, title, author, furl, raw, d, lic = picked
        out = os.path.join(OUT, "m-" + slug(title) + ".mp3")
        if not os.path.exists(out):
            normalise_music(raw, out)
        items.append({"id": "m-" + slug(title), "kind": "music", "title": title, "author": author, "source": "opengameart.org", "source_url": furl, "page_url": OGA["base"] + link,
                      "license": OGA["license"], "license_url": OGA["license_url"], "license_evidence": f"{(OGA['base'] + link)}: 'License(s): CC0'",
                      "attribution": None, "moods": [kw], "themes": [theme], "duration_s": round(probe(out), 1), "bytes": os.path.getsize(out), "sha256": sha(out), "file": os.path.basename(out)})
    json.dump(items, open(os.path.join(ASSETS, "stage_music_cc0.json"), "w"), indent=1, ensure_ascii=False)
    return items


# ------------------------------------------------------------------------------------------------ Kenney (CC0 packs)
KENNEY = KEN["packs"]
SFX = [tuple(x) for x in KEN["sfx"]]   # (pack, file stem, tags, themes)


def curate_sfx():
    items, packs = [], {}
    for name, page in KENNEY.items():
        t = get(page)
        txt = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S))))
        i = txt.find("License")
        evidence = txt[i:i + 34].strip()
        if "CC0" not in evidence:
            raise RuntimeError(f"{name}: the page does not state CC0 ({evidence!r}); refusing to use this pack")
        z = re.findall(r'https://kenney.nl/media/[^"]*\.zip', t)[0]
        zp = os.path.join(RAW, f"kenney_{name}.zip")
        if not os.path.exists(zp):
            open(zp, "wb").write(get(z, binary=True))
        packs[name] = (zipfile.ZipFile(zp), evidence, page)
    for pack, stem, tags, themes in SFX:
        zf, evidence, page = packs[pack]
        member = next((n for n in zf.namelist() if os.path.basename(n) == stem + ".ogg"), None)
        if not member:
            print("missing in pack:", pack, stem); continue
        out = os.path.join(OUT, f"s-{slug(pack)}-{slug(stem)}.ogg")
        open(out, "wb").write(zf.read(member))
        if probe(out) < 0.1:                                     # the editor refuses audio under 0.1 s, so such a piece would be unusable
            os.remove(out); print("too short, skipped:", pack, stem); continue
        items.append({"id": f"s-{slug(pack)}-{slug(stem)}", "kind": "sfx", "title": f"{stem} ({pack})", "author": KEN["author"], "source": "kenney.nl", "source_url": page,
                      "license": KEN["license"], "license_url": KEN["license_url"], "license_evidence": f"{page}: '{evidence}'", "attribution": None,
                      "moods": tags, "themes": themes, "duration_s": round(probe(out), 2), "bytes": os.path.getsize(out), "sha256": sha(out), "file": os.path.basename(out)})
    json.dump(items, open(os.path.join(ASSETS, "stage_sfx.json"), "w"), indent=1, ensure_ascii=False)
    return items


# ------------------------------------------------------------------------------------------------ batch 2: audio for the second set of templates
EXTRA_MUSIC, IMPACT_PICKS, TAG_THEMES, MUSIC_THEMES = SRC["extra_music"], KEN["impact_picks"], SRC["tag_themes"], SRC["music_themes"]


def curate_batch2():
    """Add the batch-2 pieces to assets/manifest.json (kept as it is) and tag existing pieces for the new templates. Idempotent."""
    mp = os.path.join(ASSETS, "manifest.json")
    man = json.load(open(mp, encoding="utf-8"))
    items = man["assets"]
    have = {i["id"] for i in items}
    new = []
    pieces = {p["title"]: p for p in json.loads(get(INC["pieces"]))}
    for title, themes in EXTRA_MUSIC.items():
        p = pieces.get(title)
        if not p:
            print("not in catalogue:", title); continue
        iid = "m-" + slug(title)
        if iid in have:
            it = next(i for i in items if i["id"] == iid)
            it["themes"] = sorted(set(it["themes"]) | set(themes)); continue
        raw = os.path.join(RAW, "inc_" + slug(title) + ".mp3")
        if not os.path.exists(raw):
            open(raw, "wb").write(get(INC["mp3_base"] + urllib.parse.quote(p["filename"]), binary=True))
        if os.path.getsize(raw) < 300_000 or probe(raw) < 60:
            print("skip (bad download):", title); continue
        out = os.path.join(OUT, iid + ".mp3")
        if not os.path.exists(out):
            normalise_music(raw, out)
        new.append({"id": iid, "kind": "music", "title": title, "author": INC["author"], "source": INC["source"],
                    "source_url": INC["mp3_base"] + urllib.parse.quote(p["filename"]),
                    "license": INC["license"], "license_url": INC["license_url"],
                    "license_evidence": INC["license_evidence"],
                    "attribution": INC["attribution"].format(title=title),
                    "moods": sorted({f.strip().lower() for f in (p.get("feel") or "").split(",") if f.strip()}), "themes": themes, "instruments": p.get("instruments"),
                    "description": p.get("description"), "duration_s": round(probe(out), 1), "bytes": os.path.getsize(out), "sha256": sha(out), "file": os.path.basename(out),
                    "r2_key": f"assets/music/{os.path.basename(out)}"})
    page = KEN["impact_page"]
    t = get(page)
    txt = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>|<style.*?</style>", "", t, flags=re.S))))
    evidence = txt[txt.find("License"):txt.find("License") + 34].strip()
    if "CC0" not in evidence:
        raise RuntimeError(f"impact-sounds: the page does not state CC0 ({evidence!r}); refusing to use this pack")
    zp = os.path.join(RAW, "kenney_impact-sounds.zip")
    if not os.path.exists(zp):
        open(zp, "wb").write(get(re.findall(r'https://kenney.nl/media/[^"]*\.zip', t)[0], binary=True))
    zf = zipfile.ZipFile(zp)
    for stem in IMPACT_PICKS:
        iid = f"s-impact-sounds-{slug(stem)}"
        member = next((n for n in zf.namelist() if os.path.basename(n) == stem + ".ogg"), None)
        if iid in have or not member:
            print("skip impact:", stem, "(have)" if iid in have else "(missing in pack)"); continue
        out = os.path.join(OUT, f"{iid}.ogg")
        open(out, "wb").write(zf.read(member))
        if probe(out) < 0.1:
            os.remove(out); print("too short, skipped:", stem); continue
        new.append({"id": iid, "kind": "sfx", "title": f"{stem} (impact-sounds)", "author": KEN["author"], "source": "kenney.nl", "source_url": page,
                    "license": KEN["license"], "license_url": KEN["license_url"], "license_evidence": f"{page}: '{evidence}'", "attribution": None,
                    "moods": ["hit"], "themes": ["cinema"], "duration_s": round(probe(out), 2), "bytes": os.path.getsize(out), "sha256": sha(out), "file": os.path.basename(out),
                    "r2_key": f"assets/sfx/{os.path.basename(out)}"})
    items.extend(new)
    for it in items:                                           # tag existing pieces for the new templates
        extra = set()
        if it["kind"] == "sfx":
            for tag in it.get("moods", []):
                extra |= set(TAG_THEMES.get(tag, []))
        else:
            for th in list(it.get("themes", [])):
                extra |= set(MUSIC_THEMES.get(th, []))
        it["themes"] = sorted(set(it.get("themes", [])) | extra)
    json.dump(man, open(mp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(new, open(os.path.join(ASSETS, "stage_batch2.json"), "w"), indent=1, ensure_ascii=False)
    return new


if __name__ == "__main__":
    for name, fn in (("music", curate_incompetech), ("oga", curate_oga), ("sfx", curate_sfx), ("batch2", curate_batch2)):
        if ARGS.what == name or (ARGS.what == "all" and name != "batch2"):
            res = fn()
            print(f"{name}: {len(res)} items")
