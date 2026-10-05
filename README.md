# Editor de video MLT + FFmpeg manejado por un LLM

Un editor de video no lineal sin interfaz: un modelo lo maneja solo a través de un servidor MCP (stdio). Cada edición es una operación declarativa que se valida al instante;
el render ocurre bajo demanda (cuadros, hoja de contactos, preview, export). Motor: **MLT** (composición) + **FFmpeg** (codificación H.264/AAC).

Se usa de dos formas: **como aplicación web con chat** (`docker compose up`, ver abajo) o **como servidor MCP** desde Claude Code / Claude Desktop.

## Qué hace
- **Edición:** clips, cortes, 15 transiciones (`dissolve`, barridos, iris, persianas, deslizamientos…), fades, picture-in-picture, subtítulos (`.srt` o cues), texto con estilos.
- **Overlays:** tercios inferiores, callouts anclados a un punto que se mueve, iconos (≈100 de Lucide + garabatos a mano), imágenes y marcos/viñeta/letterbox, con animaciones (`fade`, `slide-*`, `pop`, `zoom`, `spin`, `drop`, `rise`, `wipe`, `draw`).
- **Plantillas:** 15 (luxury, corporate, academic, sketch, tech, minimal, playful, neobrutalism, terracotta, cinema, terminal, arcade, riso, saas, glass). Cambiar de plantilla restila todo el proyecto. Tarjetas de título, sección, cita, lista, cifra, cierre y *bento*.
- **Audio:** biblioteca de música y efectos con licencia verificada (CC0 / CC-BY, créditos automáticos), ducking bajo la voz, normalización a −16 LUFS al exportar.
- **Seguro y robusto:** validación con mensajes que nombran la edición, bloqueo de archivo del proyecto, control de rutas activado por defecto (solo la carpeta del proyecto y las de `MLT_EDITOR_ROOTS`), límites de tamaño, duración, disco y tiempo de render, nada se guarda si una edición es inválida.

## Todo son piezas desmontables
Plantillas (`theme.json`), formas, fondos de tarjeta, transiciones, presets de animación, operaciones, capas y herramientas MCP son plugins que se registran solos. Se agrega una copiando un archivo o carpeta,
se quita borrándola, y no hay rutas ni nombres de plantilla escritos en el motor (lo comprueba `test_modularity.py`). Toda la configuración sale de variables `MLT_*` o un archivo.
Detalles y cómo crear cada pieza: [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Aplicación web con chat (Docker)
Una página donde se suben los videos, se conversa con el editor (un modelo de Claude maneja las herramientas), se ve el timeline y la vista previa en vivo, se deshace/rehace y se exporta. Un proceso del motor por proyecto, con la carpeta del proyecto como único lugar permitido.
```bash
export MLT_APP_TOKEN='una-clave-larga'          # la contraseña de la página (obligatoria)
export ANTHROPIC_API_KEY='sk-ant-...'           # la usa el chat
docker compose up --build                       # luego abre http://127.0.0.1:8080 e ingresa el token
```
Variables (todas `MLT_APP_*`, ver `app/config.py`): `MODEL` (por defecto `claude-sonnet-5-5`), `MAX_USD_MESSAGE` (1.0) y `MAX_USD_PROJECT` (10.0) topes de gasto, `MAX_PROCS`, `IDLE_S`, `MAX_TURNS` (30), `MAX_UPLOAD_MB` (2048). La música y los efectos de la biblioteca **no** vienen en la imagen: monta la carpeta con `MLT_ASSETS_DIR` o da acceso al bucket con `R2_WORKER_URL`/`R2_UPLOAD_TOKEN`.
El puerto solo se publica en `127.0.0.1`; si lo expones a una red, pon HTTPS delante (el token viaja en una cookie). Modelo de amenazas y límites: [`SECURITY.md`](SECURITY.md).

## Instalación sin Docker (Ubuntu 24.04)
```bash
./setup.sh      # apt: melt, python3-mlt, ffmpeg, xvfb, fuentes; crea .venv con Python 3.12 y las dependencias
```
El binding de MLT solo existe para el Python 3.12 del sistema, por eso el venv usa `--system-site-packages`. El compositor `qtblend` necesita X11: el servidor arranca un Xvfb por su cuenta si no hay `DISPLAY`.

## Usarlo con Claude Code
El `.mcp.json` de la raíz ya registra el servidor `mlt-video-editor`:
```json
{"mcpServers": {"mlt-video-editor": {"type": "stdio", "command": ".venv/bin/python", "args": ["server.py"], "env": {"MLT_EDITOR_HOME": "out/mcp"}}}}
```
Comprueba con `/mcp` y pídele, por ejemplo: *«crea un proyecto, importa `media/clip_a.mp4` y `clip_b.mp4`, fundido cruzado de 1 s, un tercio inferior y exporta a `out/final.mp4`»*.
Otros clientes (Claude Desktop…): `mcp.example.json`. Guía completa: [`CLAUDE_CODE.md`](CLAUDE_CODE.md).

Edición confiable: ids estables, revisiones (`expected_revision`), `dry_run` con diff, `request_id`, overlays anclados a su clip, proxies y caché para stills rápidos, un visor en vivo (`open_viewer`), export en segundo plano y QA automático del render (ver `ARCHITECTURE.md` y `REPORT.md` §23).

Herramientas principales: `new_project`, `import_clip`, `add_clip`, `cut_clip`, `crossfade`, `set_fades`, `add_pip`, `add_text`, `add_subtitles`, `add_graphic`, `add_lower_third`, `add_image`, `add_callout`, `add_card`, `add_audio`,
`animate`, `trim_clip`, `move_clip`, `move_op`, `update_op`, `set_template`, `apply_ops` (varias ediciones en una llamada, todo o nada), `get_timeline`, `describe_project`, `query`, `undo`, `redo`, `remove_op`, `get_still`, `get_contact_sheet`, `render_preview`, `export`, `job_status`, `cancel_job`, `open_viewer`, `verify_sources`, `list_styles`, `list_assets`, `list_sources` (42 herramientas en total).

## Pruebas
```bash
export MLT_EDITOR_ROOTS='*'             # las suites antiguas leen medios del repo desde carpetas temporales: sin control de rutas (ci.sh ya lo hace; test_security prueba el valor por defecto)
./ci.sh                                 # todo lo siguiente y más, en ~5 min (necesita DISPLAY o xvfb-run)
.venv/bin/python snapshot.py check      # 686 hashes de píxeles/claves: la salida no cambió
.venv/bin/python golden.py              # 15 renders de referencia
.venv/bin/python test_modularity.py     # agregar/quitar piezas, sin nombres ni rutas fijas
for t in test_engine test_mcp test_text test_anim test_transitions test_cards_anim test_assets; do .venv/bin/python $t.py; done
```
Las que renderizan necesitan un `DISPLAY` (p. ej. `xvfb-run -a …`).

## Documentación
| Archivo | Contenido |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | mapa del paquete, configuración, tipos de pieza, cómo agregar/quitar |
| [`REPORT.md`](REPORT.md) | informe de cada fase con lo medido y lo no verificado |
| [`SECURITY.md`](SECURITY.md) | modelo de amenazas: entradas no confiables, controles, lo que NO cubre |
| [`CLAUDE_CODE.md`](CLAUDE_CODE.md) | conectarlo a Claude Code, variables de entorno, seguridad |
| [`legacy/`](legacy/) | el POC original y los benchmarks tempranos |

## Licencias
Íconos: Lucide (ISC). Fuentes: SIL OFL 1.1. Audio: CC0 o CC-BY según la pieza (lista en `assets/LICENSES.md`; las CC-BY exigen crédito y el editor escribe `<video>.credits.txt` al exportar).
