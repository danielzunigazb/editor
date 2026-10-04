// Upload/download gateway to the R2 bucket bound as BUCKET. Every request needs `Authorization: Bearer <UPLOAD_TOKEN>`.
// Workers cap a request body at 100 MB on the Free/Pro plans, so big files go up in parts (R2 multipart, >= 5 MiB each except the last):
//   POST   /<key>?action=mpu-create                       -> {key, uploadId}
//   PUT    /<key>?action=mpu-uploadpart&uploadId=&partNumber=N   (body = the part) -> {partNumber, etag}
//   POST   /<key>?action=mpu-complete&uploadId=           (body = {"parts":[{partNumber, etag}]}) -> {key, size, etag}
//   DELETE /<key>?action=mpu-abort&uploadId=
//   PUT    /<key>                                          small file in one request (< 100 MB)
//   GET    /<key>                                          download     |   GET /?action=list  -> keys
const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json" } });

async function authorized(request, env) {
  if (!env.UPLOAD_TOKEN) return false;
  const enc = new TextEncoder();
  const [a, b] = await Promise.all([
    crypto.subtle.digest("SHA-256", enc.encode(request.headers.get("Authorization") || "")),
    crypto.subtle.digest("SHA-256", enc.encode(`Bearer ${env.UPLOAD_TOKEN}`)),
  ]);
  return crypto.subtle.timingSafeEqual(a, b);                 // equal-length digests: constant-time compare
}

export default {
  async fetch(request, env) {
    if (!(await authorized(request, env))) return json({ error: "unauthorized" }, 401);
    const url = new URL(request.url);
    const action = url.searchParams.get("action");
    const key = decodeURIComponent(url.pathname.slice(1));
    if (request.method === "GET" && !key && action === "list") {
      const l = await env.BUCKET.list({ limit: 1000 });
      return json(l.objects.map((o) => ({ key: o.key, size: o.size, uploaded: o.uploaded })));
    }
    if (!key || key.length > 200 || key.includes("..") || key.startsWith("/")) return json({ error: "bad key" }, 400);
    const uploadId = url.searchParams.get("uploadId");
    try {
      if (request.method === "POST" && action === "mpu-create") {
        const mpu = await env.BUCKET.createMultipartUpload(key, { httpMetadata: { contentType: request.headers.get("content-type") || "application/octet-stream" } });
        return json({ key: mpu.key, uploadId: mpu.uploadId });
      }
      if (request.method === "PUT" && action === "mpu-uploadpart") {
        const n = parseInt(url.searchParams.get("partNumber"), 10);
        if (!uploadId || !(n >= 1 && n <= 10000) || !request.body) return json({ error: "uploadId, partNumber (1-10000) and a body are required" }, 400);
        const part = await env.BUCKET.resumeMultipartUpload(key, uploadId).uploadPart(n, request.body);
        return json({ partNumber: part.partNumber, etag: part.etag });
      }
      if (request.method === "POST" && action === "mpu-complete") {
        const { parts } = await request.json();
        if (!uploadId || !Array.isArray(parts) || !parts.length) return json({ error: "uploadId and parts are required" }, 400);
        const obj = await env.BUCKET.resumeMultipartUpload(key, uploadId).complete(parts);
        return json({ key: obj.key, size: obj.size, etag: obj.httpEtag });
      }
      if (request.method === "DELETE" && action === "mpu-abort") {
        await env.BUCKET.resumeMultipartUpload(key, uploadId).abort();
        return json({ aborted: true });
      }
      if (request.method === "PUT" && !action) {
        const obj = await env.BUCKET.put(key, request.body, { httpMetadata: { contentType: request.headers.get("content-type") || "application/octet-stream" } });
        return json({ key: obj.key, size: obj.size, etag: obj.httpEtag });
      }
      if (request.method === "GET" && !action) {
        const obj = await env.BUCKET.get(key);
        if (!obj) return json({ error: "not found" }, 404);
        const h = new Headers(); obj.writeHttpMetadata(h); h.set("etag", obj.httpEtag);
        return new Response(obj.body, { headers: h });
      }
      return json({ error: "unsupported request" }, 405);
    } catch (e) {
      return json({ error: String(e && e.message || e) }, 500);
    }
  },
};
