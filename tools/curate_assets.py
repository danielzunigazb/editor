#!/usr/bin/env python3
"""Build the audio library once: pick tracks per template from three sources, download them, check the licence AT THE SOURCE, normalise,
hash, and write assets/manifest.json (+ stage files in assets_stage/ for upload). Not run by users; `fetch_assets.py` is.

Sources (all verified reachable; licences are read from each source's own pages/catalogue, never assumed):
  incompetech.com  Kevin MacLeod, CC BY 4.0 (catalogue pieces.json: feel, instruments, length) -> credit line required
  kenney.nl        CC0 sound packs (each pack page states "License Creative Commons CC0")
  opengameart.org  only items whose page lists CC0 as the licence
Dropped: FreePD (closed permanently in 2025: its home page says so), Pixabay/Mixkit (no public API, terms against bulk download).
Usage: python3 tools/curate_assets.py music|oga|sfx|batch2|all (batch2 extends assets/manifest.json; `all` does not run it)        (writes assets_stage/raw, assets_stage/out, assets/stage_*.json)"""
import hashlib, html, json, os, re, subprocess, sys, time, urllib.parse, urllib.request, zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGE = os.path.join(HERE, "assets_stage")
RAW, OUT = os.path.join(STAGE, "raw"), os.path.join(STAGE, "out")
UA = "Mozilla/5.0 (X11; Linux x86_64) mlt-poc-asset-curator"
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
BAD_FEELS = {   # moods that clash with a template (a title card about a building should not sound like a horror trailer)
    "luxury": {"Suspenseful", "Aggressive", "Eerie", "Unnerving", "Dark"}, "corporate": {"Dark", "Eerie", "Unnerving", "Aggressive", "Mysterious", "Suspenseful"},
    "academic": {"Eerie", "Unnerving", "Dark", "Aggressive", "Suspenseful", "Mysterious"}, "sketch": {"Dark", "Eerie", "Unnerving", "Aggressive"},
    "tech": {"Dark", "Eerie", "Unnerving", "Aggressive", "Somber"}, "minimal": {"Mysterious", "Dark", "Eerie", "Unnerving", "Somber", "Suspenseful"},
    "playful": {"Dark", "Eerie", "Unnerving", "Aggressive", "Somber", "Suspenseful"}}
BAD_WORDS = {"corporate": ["folk", "country", "vampire", "horror"], "luxury": ["folk", "vampire", "horror"], "academic": ["folk", "vampire", "horror"], "minimal": ["folk", "vampire", "horror"], "tech": ["vampire", "horror", "folk"]}
FOLK = ["banjo", "fiddle", "tuba", "accordion", "harmonica", "mandolin", "bagpipe", "polka"]
PICKS = {   # theme: (feels any, instrument substrings wanted, instrument substrings refused, genre ids allowed, how many). Substrings, lower-case.
    "luxury": ({"Somber", "Calming", "Epic"}, ["piano", "strings", "cello", "violin"], ["drum", "percussion", "guitar", "synth"] + FOLK, None, 3),
    "corporate": ({"Bright", "Uplifting", "Driving"}, ["guitar", "piano", "synth"], ["horn", "choir", "orchestra"] + FOLK, {"5", "13"}, 3),
    "academic": ({"Calming", "Relaxed"}, ["piano", "harp", "flute", "cello"], ["drum", "percussion", "guitar", "synth", "bass"] + FOLK, None, 2),
    "sketch": ({"Bouncy", "Humorous", "Relaxed"}, ["ukulele", "whistle", "pizzicato", "xylophone", "banjo", "glockenspiel"], ["synth"], None, 2),
    "tech": ({"Driving", "Grooving", "Intense", "Bright"}, ["synth"], ["orchestra", "choir", "strings", "horn"] + FOLK, {"7", "13"}, 3),
    "minimal": ({"Calming", "Relaxed"}, ["piano", "synth"], ["drum", "percussion", "guitar", "choir"] + FOLK, None, 2),
    "playful": ({"Bouncy", "Bright", "Humorous", "Uplifting"}, ["xylophone", "ukulele", "glockenspiel", "marimba", "whistle"], ["choir"], None, 3),
}


def curate_incompetech():
    pieces = json.loads(get("https://incompetech.com/music/royalty-free/pieces.json"))
    chosen, used = [], set()
    for theme, (feels, ins_any, ins_none, genres, n) in PICKS.items():
        cands = []
        for p in pieces:
            L = secs(p["length"]); fe = {f.strip() for f in (p.get("feel") or "").split(",")}; ins = (p.get("instruments") or "").lower()
            desc = (p.get("description") or "").lower()
            if (not 100 <= L <= 240 or p["title"] in used or any(s in ins for s in ins_none) or (genres and p.get("genre") not in genres)
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
            open(raw, "wb").write(get("https://incompetech.com/music/royalty-free/mp3-royaltyfree/" + urllib.parse.quote(fn), binary=True))
        if os.path.getsize(raw) < 300_000 or probe(raw) < 60:
            print("skip (bad download):", p["title"]); continue
        out = os.path.join(OUT, "m-" + slug(p["title"]) + ".mp3")
        if not os.path.exists(out):
            normalise_music(raw, out)
        moods = sorted({f.strip().lower() for f in (p.get("feel") or "").split(",") if f.strip()})
        items.append({"id": "m-" + slug(p["title"]), "kind": "music", "title": p["title"], "author": "Kevin MacLeod", "source": "incompetech.com", "source_url": "https://incompetech.com/music/royalty-free/mp3-royaltyfree/" + urllib.parse.quote(fn),
                      "license": "CC-BY-4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
                      "license_evidence": "incompetech.com/music/royalty-free/music.html: 'Creative Commons: By Attribution 4.0 License'",
                      "attribution": f"\"{p['title']}\" Kevin MacLeod (incompetech.com)\nLicensed under Creative Commons: By Attribution 4.0 License\nhttp://creativecommons.org/licenses/by/4.0/",
                      "moods": moods, "themes": [t for t, q in chosen if q["title"] == p["title"]], "instruments": p.get("instruments"), "description": p.get("description"),
                      "duration_s": round(probe(out), 1), "bytes": os.path.getsize(out), "sha256": sha(out), "file": os.path.basename(out)})
    json.dump(items, open(os.path.join(HERE, "assets", "stage_music_ccby.json"), "w"), indent=1, ensure_ascii=False)
    return items


# ------------------------------------------------------------------------------------------------ OpenGameArt (CC0 only)
OGA_QUERIES = [("luxury", "orchestral"), ("academic", "piano"), ("tech", "electronic"), ("playful", "happy"), ("sketch", "ukulele"),
               ("corporate", "upbeat"), ("minimal", "ambient"), ("luxury", "cinematic"), ("playful", "bouncy")]


def curate_oga():
    items, seen_files = [], set()
    for theme, kw in OGA_QUERIES:
        url = ("https://opengameart.org/art-search-advanced?keys=" + urllib.parse.quote(kw) + "&field_art_type_tid%5B%5D=12&field_art_licenses_tid%5B%5D=4&sort_by=count&sort_order=DESC")
        listing = get(url)
        links = []
        for l in re.findall(r'href="(/content/[^"#?]+)"', listing):
            if l not in links and l != "/content/faq":
                links.append(l)
        picked = None
        for link in links[:10]:
            page = get("https://opengameart.org" + link)
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
        items.append({"id": "m-" + slug(title), "kind": "music", "title": title, "author": author, "source": "opengameart.org", "source_url": furl, "page_url": "https://opengameart.org" + link,
                      "license": "CC0-1.0", "license_url": "https://creativecommons.org/publicdomain/zero/1.0/", "license_evidence": f"{('https://opengameart.org' + link)}: 'License(s): CC0'",
                      "attribution": None, "moods": [kw], "themes": [theme], "duration_s": round(probe(out), 1), "bytes": os.path.getsize(out), "sha256": sha(out), "file": os.path.basename(out)})
    json.dump(items, open(os.path.join(HERE, "assets", "stage_music_cc0.json"), "w"), indent=1, ensure_ascii=False)
    return items


# ------------------------------------------------------------------------------------------------ Kenney (CC0 packs)
KENNEY = {"ui-audio": "https://kenney.nl/assets/ui-audio", "interface-sounds": "https://kenney.nl/assets/interface-sounds", "digital-audio": "https://kenney.nl/assets/digital-audio",
          "rpg-audio": "https://kenney.nl/assets/rpg-audio", "music-jingles": "https://kenney.nl/assets/music-jingles"}
SFX = [  # (pack, file stem, tags, themes)
    ("ui-audio", "click1", ["click"], ["corporate", "minimal", "tech"]), ("ui-audio", "click3", ["click"], ["corporate", "minimal"]), ("ui-audio", "rollover2", ["tick", "hover"], ["minimal", "corporate"]),
    ("ui-audio", "switch13", ["tick"], ["minimal", "tech"]),
    ("interface-sounds", "confirmation_001", ["ding", "chime"], ["corporate", "luxury", "academic"]), ("interface-sounds", "confirmation_003", ["ding", "chime"], ["luxury", "academic"]),
    ("interface-sounds", "select_001", ["click"], ["corporate", "minimal"]), ("interface-sounds", "select_005", ["click", "pop"], ["playful", "sketch"]), ("interface-sounds", "tick_001", ["tick"], ["minimal", "academic"]),
    ("interface-sounds", "tick_002", ["tick"], ["minimal", "tech"]), ("interface-sounds", "toggle_001", ["click"], ["corporate", "tech"]), ("interface-sounds", "open_001", ["whoosh", "swoosh"], ["corporate", "minimal"]),
    ("interface-sounds", "close_001", ["whoosh", "swoosh"], ["corporate", "minimal"]), ("interface-sounds", "maximize_003", ["whoosh", "swoosh"], ["minimal", "luxury"]), ("interface-sounds", "minimize_003", ["whoosh", "swoosh"], ["minimal", "luxury"]),
    ("interface-sounds", "glass_001", ["chime", "bell"], ["luxury", "academic"]), ("interface-sounds", "glass_004", ["chime", "bell"], ["luxury", "academic"]), ("interface-sounds", "bong_001", ["bell", "chime"], ["academic", "luxury"]),
    ("interface-sounds", "pluck_001", ["pop", "pluck"], ["sketch", "playful"]), ("interface-sounds", "drop_001", ["pop"], ["playful", "sketch"]), ("interface-sounds", "scratch_001", ["pencil", "scratch"], ["sketch"]),
    ("interface-sounds", "scratch_003", ["pencil", "scratch"], ["sketch"]), ("interface-sounds", "scroll_001", ["paper", "swoosh"], ["sketch", "academic"]), ("interface-sounds", "glitch_001", ["glitch"], ["tech"]),
    ("interface-sounds", "glitch_003", ["glitch"], ["tech"]), ("interface-sounds", "question_002", ["bleep"], ["tech", "playful"]), ("interface-sounds", "error_001", ["buzz"], ["tech"]),
    ("digital-audio", "powerUp1", ["power-up"], ["tech", "playful"]), ("digital-audio", "powerUp5", ["power-up"], ["tech", "playful"]), ("digital-audio", "powerUp9", ["power-up"], ["tech", "playful"]),
    ("digital-audio", "phaserUp1", ["whoosh", "power-up"], ["tech"]), ("digital-audio", "phaserDown1", ["whoosh"], ["tech"]), ("digital-audio", "laser1", ["bleep", "laser"], ["tech"]),
    ("digital-audio", "highUp", ["bleep"], ["tech", "playful"]), ("digital-audio", "lowDown", ["bleep"], ["tech"]), ("digital-audio", "pepSound1", ["boing", "pop"], ["playful"]),
    ("digital-audio", "pepSound3", ["boing", "pop"], ["playful"]), ("digital-audio", "threeTone1", ["ding", "chime"], ["playful", "tech"]), ("digital-audio", "zap1", ["bleep", "zap"], ["tech"]),
    ("rpg-audio", "bookFlip1", ["page-turn", "paper"], ["academic", "sketch"]), ("rpg-audio", "bookFlip2", ["page-turn", "paper"], ["academic", "sketch"]), ("rpg-audio", "bookOpen", ["page-turn", "paper"], ["academic"]),
    ("rpg-audio", "bookClose", ["page-turn", "paper"], ["academic"]), ("rpg-audio", "cloth1", ["swoosh"], ["luxury", "minimal"]), ("rpg-audio", "metalClick", ["click"], ["luxury", "corporate"]),
    ("rpg-audio", "handleCoins", ["coins", "chime"], ["luxury", "corporate"]),
]


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
        items.append({"id": f"s-{slug(pack)}-{slug(stem)}", "kind": "sfx", "title": f"{stem} ({pack})", "author": "Kenney (kenney.nl)", "source": "kenney.nl", "source_url": page,
                      "license": "CC0-1.0", "license_url": "https://creativecommons.org/publicdomain/zero/1.0/", "license_evidence": f"{page}: '{evidence}'", "attribution": None,
                      "moods": tags, "themes": themes, "duration_s": round(probe(out), 2), "bytes": os.path.getsize(out), "sha256": sha(out), "file": os.path.basename(out)})
    json.dump(items, open(os.path.join(HERE, "assets", "stage_sfx.json"), "w"), indent=1, ensure_ascii=False)
    return items


# ------------------------------------------------------------------------------------------------ batch 2: audio for the second set of templates
EXTRA_MUSIC = {   # incompetech title (as in pieces.json) -> templates it suits. Chosen from the catalogue's feel/instruments/description; NOT auditioned.
    "Undaunted": ["cinema"], "Rynos Theme": ["cinema"], "Americana": ["cinema"], "Bit Quest": ["arcade"], "Pixelland": ["arcade"], "Cyborg Ninja": ["arcade"],
    "Morning": ["terracotta"], "Evening": ["terracotta"], "Funin and Sunin": ["riso"], "Pleasant Porridge": ["riso", "saas"],
    "Shaving Mirror": ["neobrutalism"], "Voxel Revolution": ["neobrutalism", "terminal"], "Exit the Premises": ["terminal"], "Floating Cities": ["glass", "saas"]}
IMPACT_PICKS = ["impactBell_heavy_000", "impactMetal_heavy_000", "impactPunch_heavy_000", "impactPlate_heavy_000", "impactSoft_heavy_000"]
TAG_THEMES = {   # sound-effect tag -> templates that also use it
    "click": ["neobrutalism", "terminal", "saas", "riso"], "pop": ["neobrutalism", "riso", "arcade"], "whoosh": ["neobrutalism", "cinema", "saas", "glass"],
    "swoosh": ["glass", "cinema"], "ding": ["saas", "glass"], "chime": ["glass", "terracotta", "saas"], "bell": ["terracotta"], "pluck": ["terracotta", "riso"],
    "page-turn": ["terracotta"], "paper": ["terracotta"], "bleep": ["terminal", "arcade"], "glitch": ["terminal"], "power-up": ["arcade", "terminal"],
    "coins": ["arcade"], "tick": ["saas", "terminal", "glass"], "boing": ["arcade", "neobrutalism"], "hit": ["cinema"]}
MUSIC_THEMES = {"tech": ["terminal", "saas", "arcade"], "corporate": ["saas"], "minimal": ["glass", "saas"], "playful": ["neobrutalism", "riso"], "sketch": ["riso", "terracotta"]}


def curate_batch2():
    """Add the batch-2 pieces to assets/manifest.json (kept as it is) and tag existing pieces for the new templates. Idempotent."""
    mp = os.path.join(HERE, "assets", "manifest.json")
    man = json.load(open(mp, encoding="utf-8"))
    items = man["assets"]
    have = {i["id"] for i in items}
    new = []
    pieces = {p["title"]: p for p in json.loads(get("https://incompetech.com/music/royalty-free/pieces.json"))}
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
            open(raw, "wb").write(get("https://incompetech.com/music/royalty-free/mp3-royaltyfree/" + urllib.parse.quote(p["filename"]), binary=True))
        if os.path.getsize(raw) < 300_000 or probe(raw) < 60:
            print("skip (bad download):", title); continue
        out = os.path.join(OUT, iid + ".mp3")
        if not os.path.exists(out):
            normalise_music(raw, out)
        new.append({"id": iid, "kind": "music", "title": title, "author": "Kevin MacLeod", "source": "incompetech.com",
                    "source_url": "https://incompetech.com/music/royalty-free/mp3-royaltyfree/" + urllib.parse.quote(p["filename"]),
                    "license": "CC-BY-4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
                    "license_evidence": "incompetech.com/music/royalty-free/music.html: 'Creative Commons: By Attribution 4.0 License'",
                    "attribution": f"\"{title}\" Kevin MacLeod (incompetech.com)\nLicensed under Creative Commons: By Attribution 4.0 License\nhttp://creativecommons.org/licenses/by/4.0/",
                    "moods": sorted({f.strip().lower() for f in (p.get("feel") or "").split(",") if f.strip()}), "themes": themes, "instruments": p.get("instruments"),
                    "description": p.get("description"), "duration_s": round(probe(out), 1), "bytes": os.path.getsize(out), "sha256": sha(out), "file": os.path.basename(out),
                    "r2_key": f"assets/music/{os.path.basename(out)}"})
    page = "https://kenney.nl/assets/impact-sounds"
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
        new.append({"id": iid, "kind": "sfx", "title": f"{stem} (impact-sounds)", "author": "Kenney (kenney.nl)", "source": "kenney.nl", "source_url": page,
                    "license": "CC0-1.0", "license_url": "https://creativecommons.org/publicdomain/zero/1.0/", "license_evidence": f"{page}: '{evidence}'", "attribution": None,
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
    json.dump(new, open(os.path.join(HERE, "assets", "stage_batch2.json"), "w"), indent=1, ensure_ascii=False)
    return new


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    for name, fn in (("music", curate_incompetech), ("oga", curate_oga), ("sfx", curate_sfx), ("batch2", curate_batch2)):
        if what == name or (what == "all" and name != "batch2"):
            res = fn()
            print(f"{name}: {len(res)} items")
