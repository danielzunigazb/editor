#!/usr/bin/env python3
"""Upload a file to the R2 gateway Worker (r2_worker/). Env: R2_WORKER_URL, R2_UPLOAD_TOKEN. Usage: r2_upload.py <file> [key]
Files under 90 MB go in one PUT; bigger ones in 50 MiB parts (each retried 3x), then the size is checked against the local file."""
import hashlib, json, os, sys, time, urllib.error, urllib.parse, urllib.request

PART = 50 * 1024 * 1024
SINGLE_MAX = 90 * 1000 * 1000


def call(method, url, token, data=None, ctype="application/octet-stream", tries=3):
    for k in range(tries):
        req = urllib.request.Request(url, data=data, method=method, headers={"Authorization": f"Bearer {token}", "Content-Type": ctype,
                                                                                    "User-Agent": "mlt-poc-uploader/1.0"})   # Cloudflare answers 403/1010 to urllib's default UA
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            if e.code < 500 or k == tries - 1:
                raise SystemExit(f"{method} {url.split('?')[0]} -> HTTP {e.code}: {e.read()[:300].decode(errors='replace')}")
        except (urllib.error.URLError, TimeoutError) as e:
            if k == tries - 1:
                raise SystemExit(f"{method} {url.split('?')[0]} failed: {e}")
        time.sleep(2 ** k)


def upload(path, key, base, token):
    size = os.path.getsize(path)
    q = urllib.parse.quote(key)
    base = base.rstrip("/")
    if size <= SINGLE_MAX:
        with open(path, "rb") as f:
            res = call("PUT", f"{base}/{q}", token, f.read())
    else:
        up = call("POST", f"{base}/{q}?action=mpu-create", token, b"", ctype="video/mp4" if path.lower().endswith(".mp4") else "application/octet-stream")
        parts = []
        try:
            with open(path, "rb") as f:
                n = 0
                while chunk := f.read(PART):
                    n += 1
                    r = call("PUT", f"{base}/{q}?action=mpu-uploadpart&uploadId={up['uploadId']}&partNumber={n}", token, chunk)
                    parts.append({"partNumber": r["partNumber"], "etag": r["etag"]})
                    print(f"  part {n} ({len(chunk) / 1e6:.0f} MB) ok", file=sys.stderr)
            res = call("POST", f"{base}/{q}?action=mpu-complete&uploadId={up['uploadId']}", token, json.dumps({"parts": parts}).encode(), ctype="application/json")
        except BaseException:
            try:
                call("DELETE", f"{base}/{q}?action=mpu-abort&uploadId={up['uploadId']}", token, tries=1)
            except BaseException:
                pass
            raise
    if res.get("size") != size:
        raise SystemExit(f"uploaded size {res.get('size')} != local size {size}")
    return res


if __name__ == "__main__":
    if len(sys.argv) < 2 or not os.environ.get("R2_WORKER_URL") or not os.environ.get("R2_UPLOAD_TOKEN"):
        raise SystemExit("usage: R2_WORKER_URL=... R2_UPLOAD_TOKEN=... r2_upload.py <file> [key]")
    src = sys.argv[1]
    k = sys.argv[2] if len(sys.argv) > 2 else os.path.basename(src)
    r = upload(src, k, os.environ["R2_WORKER_URL"], os.environ["R2_UPLOAD_TOKEN"])
    print(json.dumps({**r, "sha256_local": hashlib.sha256(open(src, "rb").read()).hexdigest()}))
