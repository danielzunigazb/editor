#!/usr/bin/env python3
"""Tests r2_upload.py against a local simulator of the Worker protocol (not Cloudflare): single PUT, multipart, auth, a failing part
(abort), size check. Run: python3 test_r2_upload.py"""
import hashlib, json, os, sys, tempfile, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import r2_upload

TOKEN, STORE, MPU, EVENTS = "s3cret", {}, {}, []
FAIL_PART = {"n": None}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, obj, code=200):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def _body(self): return self.rfile.read(int(self.headers.get("Content-Length") or 0))
    def handle_any(self):
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self._body(); return self._send({"error": "unauthorized"}, 401)
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query); key = urllib.parse.unquote(u.path[1:]); a = (q.get("action") or [None])[0]
        body = self._body(); m = self.command
        if m == "POST" and a == "mpu-create": MPU[key] = {}; return self._send({"key": key, "uploadId": "U1"})
        if m == "PUT" and a == "mpu-uploadpart":
            n = int(q["partNumber"][0])
            if FAIL_PART["n"] == n: return self._send({"error": "boom"}, 400)
            MPU[key][n] = body; return self._send({"partNumber": n, "etag": hashlib.md5(body).hexdigest()})
        if m == "POST" and a == "mpu-complete":
            parts = json.loads(body)["parts"]; data = b"".join(MPU[key][p["partNumber"]] for p in parts); STORE[key] = data
            return self._send({"key": key, "size": len(data), "etag": "x"})
        if m == "DELETE" and a == "mpu-abort": MPU.pop(key, None); EVENTS.append("abort"); return self._send({"aborted": True})
        if m == "PUT" and not a: STORE[key] = body; return self._send({"key": key, "size": len(body), "etag": "x"})
        self._send({"error": "unsupported"}, 405)
    do_GET = do_PUT = do_POST = do_DELETE = handle_any


srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"
passed = failed = 0
def chk(name, cond, detail=""):
    global passed, failed
    passed += bool(cond); failed += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if not cond and detail else ""))

tmp = tempfile.mkdtemp()
small = os.path.join(tmp, "small.mp4"); open(small, "wb").write(os.urandom(300_000))
r = r2_upload.upload(small, "a/small.mp4", base, TOKEN)
chk("small file goes up in one PUT with identical bytes", STORE["a/small.mp4"] == open(small, "rb").read() and r["size"] == 300_000)
r2_upload.PART, r2_upload.SINGLE_MAX = 1_000_000, 2_500_000          # shrink the thresholds so the multipart path runs on a small file
big = os.path.join(tmp, "big.mp4"); open(big, "wb").write(os.urandom(4_200_000))
r = r2_upload.upload(big, "big.mp4", base, TOKEN)
chk("multipart (5 parts, last one smaller) reassembles to identical bytes", hashlib.sha256(STORE["big.mp4"]).hexdigest() == hashlib.sha256(open(big, "rb").read()).hexdigest() and r["size"] == 4_200_000)
try: r2_upload.upload(small, "x.mp4", base, "wrong"); e = None
except SystemExit as ex: e = str(ex)
chk("a wrong token is rejected with a clear message", e and "401" in e and "x.mp4" not in STORE, e)
FAIL_PART["n"] = 3
try: r2_upload.upload(big, "fail.mp4", base, TOKEN); e = None
except SystemExit as ex: e = str(ex)
chk("a failing part aborts the multipart upload and reports the error", e and "400" in e and EVENTS == ["abort"] and "fail.mp4" not in STORE, (e, EVENTS))
FAIL_PART["n"] = None
chk("keys with spaces/accents are URL-encoded", r2_upload.upload(small, "ñandú y más.mp4", base, TOKEN)["size"] == 300_000 and "ñandú y más.mp4" in STORE)
print(f"\n{passed} passed, {failed} failed"); sys.exit(1 if failed else 0)
