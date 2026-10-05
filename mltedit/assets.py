"""Runtime side of the asset library (music + sound effects): the manifest, lazy download from the R2 gateway with SHA-256 verification,
filtering for list_assets, and the credit lines that CC-BY pieces require.

manifest (assets/manifest.json, committed, small): one entry per piece with title, author, source, licence (+ the evidence read at the source),
ready-made attribution text, moods, themes, duration, bytes, sha256 and the R2 key. The audio itself is NOT in git: it lives in the R2 bucket
and is fetched on first use into assets_cache/ (MLT_ASSETS_CACHE to move it). Needs R2_WORKER_URL + R2_UPLOAD_TOKEN in the environment."""
import hashlib, json, os, urllib.error, urllib.parse, urllib.request

from .config import S


def manifest():
    try:
        with open(S.assets_manifest, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise RuntimeError(f"asset manifest {S.assets_manifest} is missing or unreadable ({e})")


def by_id():
    return {it["id"]: it for it in manifest()["assets"]}


def find(asset_id):
    it = by_id().get(asset_id)
    if it is None:
        import difflib
        close = difflib.get_close_matches(str(asset_id), list(by_id()), n=4, cutoff=0.5)
        raise ValueError(f"unknown asset '{asset_id}'" + (f"; did you mean {', '.join(close)}?" if close else "") + " (list_assets lists them)")
    return it


def _download(it, dest):
    base, token = S.r2_url.rstrip("/"), S.r2_token
    if not base or not token:
        raise RuntimeError(f"asset '{it['id']}' is not in {S.assets_cache} and R2_WORKER_URL / R2_UPLOAD_TOKEN are not set, so it cannot be fetched "
                           f"(set both in the environment; the audio lives in the R2 bucket, key {it['r2_key']})")
    req = urllib.request.Request(f"{base}/{urllib.parse.quote(it['r2_key'])}", headers={"Authorization": f"Bearer {token}", "User-Agent": S.r2_user_agent})   # Cloudflare answers 403/1010 to urllib's default UA
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"R2 gateway answered HTTP {e.code} for {it['r2_key']}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"could not reach the R2 gateway ({e})")
    if hashlib.sha256(data).hexdigest() != it["sha256"]:
        raise RuntimeError(f"asset '{it['id']}' failed its SHA-256 check after download (corrupt or replaced); not using it")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + f".{os.getpid()}.tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dest)


def path(asset_id):
    """Local path of an asset, downloading it (and verifying its hash) on first use. A cached file whose hash no longer matches is refetched."""
    it = find(asset_id)
    dest = os.path.join(S.assets_cache, it["kind"], it["file"])
    if os.path.isfile(dest) and hashlib.sha256(open(dest, "rb").read()).hexdigest() == it["sha256"]:
        return dest
    _download(it, dest)
    return dest


def listing(kind=None, theme="", mood="", license="", query="", limit=40):
    out = []
    for it in manifest()["assets"]:
        if kind and it["kind"] != kind:
            continue
        if theme and theme not in it.get("themes", []):
            continue
        if mood and mood.lower() not in [m.lower() for m in it.get("moods", [])]:
            continue
        if license and not it["license"].lower().startswith(license.lower()):
            continue
        if query and query.lower() not in (it["id"] + " " + it["title"]).lower():
            continue
        have = os.path.isfile(os.path.join(S.assets_cache, it["kind"], it["file"])) or bool(S.r2_url and S.r2_token)
        out.append({"id": it["id"], "title": it["title"], "author": it["author"], "duration_s": it["duration_s"], "license": it["license"],
                    "credit_required": bool(it.get("attribution")), "themes": it.get("themes", []), "moods": it.get("moods", []), "available": have})
    out.sort(key=lambda x: not x["available"])               # what can be used right now first; the others would fail at export (not in the cache, no R2 access)
    return out[:limit], len(out)


def credit_lines(asset_ids):
    """Attribution text for the CC-BY assets among `asset_ids` (deduplicated, in order). CC0 needs none."""
    seen, lines = set(), []
    for aid in asset_ids:
        it = by_id().get(aid)
        if it and it.get("attribution") and aid not in seen:
            seen.add(aid); lines.append(it["attribution"])
    return lines


def auto_sfx(theme):
    """The effect crossfade(sfx='auto') puts on a transition for a template: a whoosh/swoosh if one is tagged for it, else a pop, else a click,
    else any effect tagged for it (lowest id each time, so the choice is stable)."""
    for mood in ("whoosh", "swoosh", "pop", "click", ""):
        rows, _ = listing("sfx", theme=theme, mood=mood, limit=500)
        if rows:
            return sorted(r["id"] for r in rows)[0]
    raise ValueError(f"no sound effect is tagged for the '{theme}' template; name one with sfx=<asset id> (list_assets(kind='sfx'))")
