# Pasarela de subida a R2 (Worker)

Sirve para que el editor suba vídeos al bucket `mlt-poc` sin claves S3: solo necesita la URL del Worker y un token.

1. `cd poc_mlt/r2_worker && npx wrangler login`
2. Elige un token largo y aleatorio y guárdalo como secreto del Worker: `npx wrangler secret put UPLOAD_TOKEN`
3. `npx wrangler deploy` → imprime la URL `https://mlt-poc-upload.<tu-subdominio>.workers.dev`
4. En la configuración del entorno de Claude Code añade, como variables/secretos de entorno, `R2_WORKER_URL` (esa URL) y `R2_UPLOAD_TOKEN` (el mismo token), y permite el dominio `*.workers.dev` en Network access.
5. Subida: `python3 poc_mlt/r2_upload.py ruta/al/video.mp4 [clave_en_el_bucket]` (partes de 50 MiB; verifica el tamaño al terminar).

No se ha desplegado ni probado contra Cloudflare real (ver REPORT). El cliente sí se probó contra un simulador local del mismo protocolo (`test_r2_upload.py`).
