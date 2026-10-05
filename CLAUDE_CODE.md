# Usar el editor desde Claude Code

La configuración ya está en `.mcp.json` (raíz del repo, ámbito de proyecto):

```json
{ "mcpServers": { "mlt-video-editor": {
    "type": "stdio",
    "command": ".venv/bin/python",
    "args": ["server.py"],
    "env": { "MLT_EDITOR_HOME": "out/mcp" } } } }
```

## Pasos
1. Instalar dependencias una vez: `./setup.sh` (apt: melt, python3-mlt, ffmpeg, xvfb; venv con `--system-site-packages`; `mcp<2`).
2. Generar clips de prueba: `/usr/bin/python3.12 poc.py gen` (crea `media/clip_a.mp4` y `clip_b.mp4`).
3. **Abrir Claude Code desde la raíz del repo** (las rutas del `.mcp.json` son relativas a ella).
4. **Aprobar el servidor.** Claude Code pide aprobación manual para los servidores de un `.mcp.json`
   de proyecto (protección contra código ajeno). Se aprueba en el aviso al abrir, o con `/mcp`.
   Hasta entonces `claude mcp list` lo muestra como "Pending approval". No se aprueba por archivo a propósito.
5. Comprobar: `/mcp` debe listar `mlt-video-editor` con 22 herramientas (clips, cortes, fundidos, PiP, **texto y subtítulos con 6 estilos, gráficos de lujo, tercio inferior, imágenes**, vista y export). `list_styles` describe los estilos.

## Ejemplo de prompt
> Con mlt-video-editor: crea un proyecto, importa `media/clip_a.mp4` (id A) y `clip_b.mp4` (id B),
> deja A en sus primeros 4 s, añade B, fundido cruzado de 1 s, fade out de 1 s, y revisa con la hoja de
> contactos antes de exportar a `out/final.mp4`.

## Alternativas
- Solo para ti, sin tocar el repo: `claude mcp add --scope user mlt-video-editor -- /ruta/abs/.venv/bin/python /ruta/abs/server.py`
- Otro cliente MCP (Claude Desktop, etc.): `mcp.example.json` con rutas absolutas (edítalas: son marcas de posición).

## Verificado y no verificado
- Verificado: el servidor conecta y funciona cargado por Claude Code (`claude -p --mcp-config .mcp.json`):
  un modelo real encadenó `new_project`, `import_clip`, `add_clip`, `crossfade`, `set_fades` y
  `get_contact_sheet`, y reportó la duración correcta (8 s) y el contenido de la hoja de contactos.
  Esa ejecución además destapó un bug (frame 0 negro con `fade_in=0`), ya corregido y con prueba de regresión.
- No verificado: la aprobación interactiva del servidor, el flujo con `/mcp` en la TUI, ni una sesión larga
  de edición (solo una corrida corta de un modelo).

## Eficiencia
Para varias ediciones seguidas conviene `apply_ops` (una llamada, todo o nada) en vez de una llamada por edición: en la prueba con un modelo real bajó de 19 a 11 turnos y de 0.21 a 0.15 USD (sección 17 del reporte). Las respuestas de edición incluyen `warnings`, que avisan de elementos que pueden pisarse en pantalla.

Las respuestas de edición son compactas (sin el listado de `ops`, solo `op_count`); `get_timeline`, `undo` y `remove_op` devuelven el listado numerado. Mirar varios cuadros seguidos (`get_still`, `get_contact_sheet`) reutiliza el timeline ya construido mientras no cambie la edición ni los archivos (a 4K: 2.8 s → 0.46 s por cuadro, sección 18).

## Seguridad y robustez (variables de entorno)
- `MLT_EDITOR_ROOTS=/ruta/a:/ruta/b`: carpetas desde/hacia las que se pueden leer y escribir archivos (`import_clip`, `add_image`, `add_audio(path)`, `add_subtitles(srt_path)` y `export`; se resuelven enlaces simbólicos y `..`). **El control está activado por defecto**: sin esta variable solo se aceptan archivos dentro de la carpeta del proyecto (`MLT_EDITOR_HOME`); las rutas que declares se suman a esa carpeta. `MLT_EDITOR_ROOTS='*'` lo desactiva de forma explícita (solo para un uso local de confianza). Por eso el `.mcp.json` de este repo declara `"MLT_EDITOR_ROOTS": "."`.
- `MLT_TRACTOR_CACHE=0` desactiva la reutilización del timeline en los cuadros de revisión.
- El estado del proyecto se guarda con bloqueo de archivo (`project.lock`): dos procesos sobre el mismo `MLT_EDITOR_HOME` no se pierden ediciones.


## Plantillas, animación y audio
- 15 plantillas (`list_styles`): luxury, corporate, academic, sketch, tech, minimal, playful, neobrutalism, terracotta, cinema, terminal, arcade, riso, saas, glass. `anim` admite `wipe` (revelado tipo escritura) y `add_card` el layout `bento` (`items=["18 %|crecimiento", …]`).
- `list_styles` muestra las plantillas; `set_template(name, accent)` cambia el aspecto de todo el proyecto. `add_card` para introducciones/cierres, `add_image(icon=…)` para iconos, `anim`/`animate` para movimiento.
- Audio: `list_assets(kind="music"|"sfx", theme=…, license=…)` y `add_audio(asset=id, …)`. Las piezas se bajan de R2 la primera vez: el entorno necesita `R2_WORKER_URL` y `R2_UPLOAD_TOKEN` (configúralos en las variables del entorno de la sesión, no en archivos del repo).
- Si usas algo CC-BY, `get_timeline` lo lista en `credits_required` y `export` escribe `<vídeo>.credits.txt`; ponlo donde publiques el vídeo.

## Pulido: transiciones y movimiento
- `crossfade(first_index, dur_s, style="dissolve", sfx="")`: 15 estilos (`list_styles` → `transitions`), `style="auto"` usa la transición de la plantilla y `sfx="auto"` pone su efecto de sonido. Todo salvo `dissolve` pide `dur_s >= 0.2`.
- `new_project(motion=True)` o `set_template(name, motion=True)` activan el movimiento propio de la plantilla (overlays, tarjetas animadas, callouts, música que baja bajo la voz). Apagado por defecto: nada cambia si no lo pides.
- `add_callout(..., size=1.3, anim={"in": "draw"})`: `size` mejora la lectura a 1080p; `draw` despliega el callout desde el aro. `get_timeline` avisa de textos ilegibles.
- `export(..., master="loudnorm")` normaliza a −16 LUFS; el resultado trae `loudness_lufs` y `true_peak_db`. `tools/qa_frames.py video.mp4` mide cortes secos, parpadeos y cuadros congelados.

## Configuración y piezas (modular)
- Toda ruta, límite e interruptor se configura con variables `MLT_*` o un archivo (`MLT_EDITOR_CONFIG`, TOML o JSON); lista completa en `mltedit/config.py` y `ARCHITECTURE.md`. Los secretos (`R2_*`) solo del entorno.
- Plantillas nuevas: copia una carpeta de `mltedit/packs/themes/` en una carpeta de `MLT_PACKS_DIRS` y edita su `theme.json`. Transiciones, formas, presets, layouts de tarjeta y demás: un `.py` en una carpeta de `MLT_PLUGIN_DIRS`. Quitar una pieza = borrar su archivo. `list_styles` muestra lo disponible y, si algún pack o plugin falló, `problems`.
- Las herramientas del repo (`tools/curate_assets.py`, `make_demos.py`, `player_demo.py`, `make_licenses.py`) leen sus datos de `data/*.json` y aceptan argumentos (`--help`). El POC original está en `legacy/`.

## Edición confiable (ids, revisiones, anclaje, previews)
- Cada edición y cada clip tienen un id estable (`op_id`, `id` en `entries`): úsalos en vez de posiciones (`update_op`, `remove_op(op_id=…)`, `cut_clip(clip_id=…)`). `query` y `describe_project` los muestran sin volcar todo el timeline.
- Todas las respuestas traen `revision`. Las herramientas de edición aceptan `expected_revision` (`REVISION_CONFLICT` si el proyecto cambió), `dry_run=true` (devuelve un diff, no escribe) y `request_id` (un reintento no duplica). Los errores son `CODIGO: mensaje` + una línea JSON.
- Los overlays y el audio nuevos se anclan al clip que está en pantalla y lo siguen si se corta, recorta (`trim_clip`), mueve (`move_clip`) o quita algo antes; `anchor="timeline"` los deja fijos. `remove_op` de un clip con dependientes pide `cascade=true` o `reanchor="timeline"`. `undo`/`redo` cubren todo.
- Previews: las fuentes grandes obtienen un proxy en segundo plano (`MLT_PROXY=0` lo desactiva, `MLT_PROXY_HEIGHT`, `wait_for_proxies`); `get_still`/`get_contact_sheet` salen de caché si nada cambió. `open_viewer` devuelve la URL de un reproductor local que sigue la edición (`MLT_VIEWER_*`).
- `export` y `render_preview` devuelven `qa` (cortes duros inesperados, destellos) y aceptan `background=true` con `job_status`/`cancel_job`. `MLT_LOG=off` silencia los logs JSON de stderr. `./ci.sh` corre toda la batería.



## Límites (todos con `LIMIT_EXCEEDED`)
`MLT_MAX_SOURCE_MB` (4096), `MLT_MAX_SOURCE_S` (10800), `MLT_MAX_SOURCE_DIM` (8192 px de lado), `MLT_MAX_SOURCES` (50), `MLT_MAX_OPS` (500), `MLT_RENDER_TIMEOUT_S` (3600: un job más largo se detiene),
`MLT_PROJECT_QUOTA_MB` (20000: con la carpeta del proyecto por encima se podan las cachés y, si no basta, se rechazan imports, exports y previews).
