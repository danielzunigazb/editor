# Arquitectura del editor

Un editor de video no lineal (MLT + FFmpeg) que maneja un LLM a través de un servidor MCP. Todo lo que cambia de un proyecto a otro es una **pieza desmontable**:
se agrega copiando un archivo o una carpeta, se quita borrándolo, y el resto sigue funcionando. Ninguna ruta, nombre de plantilla ni nombre de transición está escrito
en el motor (`test_modularity.py` lo comprueba).

## Mapa

```
poc_mlt/
  mltedit/                    el paquete
    config.py                 TODA la configuración: rutas, límites, interruptores (variables MLT_*, archivo, valores por defecto)
    registry.py               un registro por tipo de pieza: register / get / names / unregister; carga plugins y packs una vez
    core/                     lo que no es plugin: EngineContext, modelo de timeline (cuadrícula de cuadros), caché/recorte de PNG, render
    engine.py                 orquesta: layout() deja que cada op valide, build() arma el tractor MLT
    server.py                 estado del proyecto (bloqueo, guardar, bind) + FastMCP; instala lo registrado en tools/
    ops/__init__.py           clases base Op / Layer, LayoutState y helpers
    tools/*.py                herramientas MCP por área (project, timeline, overlays, audio, cards, review)
    plugins/<tipo>/*.py       piezas que se registran solas al importarse (ver tabla)
    packs/themes/<nombre>/    plantillas: theme.json (+ fonts/ opcional)
  data/*.json                 datos de las herramientas del repo (fuentes de audio, textos de demos, notas de licencia)
  tools/                      curaduría, demos, licencias, QA: leen data/*.json y reciben argumentos
  legacy/                     el POC original y los benchmarks tempranos (no son parte del editor)
  live.py, server.py, themes.py, cards.py, …   fachadas de una línea: `import live` sigue funcionando
```

## Configuración (`config.py`)

Orden de resolución: variable de entorno `MLT_*` > archivo (`MLT_EDITOR_CONFIG`, TOML o JSON) > valor por defecto del paquete. Se lee en cada acceso (`S.home`, `S.fonts_dirs`…),
así que un test o un usuario puede cambiarla sin reiniciar. Los secretos (`R2_WORKER_URL`, `R2_UPLOAD_TOKEN`) vienen **solo** del entorno.

| Ajuste | Variable | Para qué |
|---|---|---|
| `home` | `MLT_EDITOR_HOME` | carpeta del proyecto del servidor |
| `roots` | `MLT_EDITOR_ROOTS` | carpetas permitidas para leer/escribir |
| `packs_dirs` | `MLT_PACKS_DIRS` | carpetas con packs de plantillas |
| `plugin_dirs` | `MLT_PLUGIN_DIRS` | carpetas con plugins `*.py` extra |
| `fonts_dirs`, `icons_dirs`, `assets_manifest`, `assets_cache` | `MLT_*` | recursos |
| `max_ops`, `max_layers`, `max_audios`… | `MLT_MAX_*` | límites |
| `default_transition`, `anim_default_preset`, `anim_keys_ease`… | `MLT_DEFAULT_TRANSITION`… | valores por defecto del motor |

La lista completa está en `SPEC` (`config.py`).

## Tipos de pieza

| Tipo (registro) | Dónde vive | Cómo se agrega | Qué hace |
|---|---|---|---|
| `theme` | `packs/themes/<n>/theme.json` | copiar una carpeta | paleta, estilos de texto, forma, opciones de tarjeta, movimiento, transición, sfx |
| `shape` | `plugins/shapes/*.py` | subclase de `shapes.Shape` + `@register` | cómo se dibujan paneles, tercios inferiores, callouts, marcos, placas |
| `card_bg` / `card_rule` | `plugins/card_bgs`, `card_rules` | `@cardkit.card_bg("n")` | fondo de tarjeta / divisor entre título y subtítulo |
| `card_layout` | `plugins/card_layouts` | `@cards.layout("n")` | un tipo de tarjeta (title, quote, bento…) |
| `transition` | `plugins/transitions` | `registry.register("transition", n, Mask(fn, soft))` | máscara luma o deslizamiento |
| `anim_preset` / `easing` | `plugins/anim` | `Preset(...)` / `Easing(fn)` | entradas y salidas animadas; banderas `reveal`, `callout`, `on_video`… |
| `graphic_kind` | `plugins/graphic_kinds` | subclase de `graphics.GraphicKind` + `@kind` | frame, letterbox, vignette… |
| `op` | `plugins/ops` | subclase de `ops.Op` + `@op` | una edición: valida, describe, comprueba legibilidad y colocación |
| `layer` | `plugins/layers` | subclase de `ops.Layer` + `@layer` | un tipo de overlay: fuente, colocación, keyframes, etiqueta, zona |
| `tool` / `builder` | `tools/*.py` | `@tool` / `@builder("n")` | herramienta MCP y su constructor de op (lo usa `apply_ops`) |

Las listas que ve el LLM (plantillas, transiciones, presets, easings, kinds, layouts, herramientas de `apply_ops`) salen del registro: los docstrings de las herramientas usan
marcadores `<<templates>>`, `<<transitions>>`… que se rellenan al arrancar el servidor.

### Agregar una plantilla
Copia una carpeta de `packs/themes/` (o crea una en una carpeta de `MLT_PACKS_DIRS`), cambia `name`, la paleta y **los nombres de los estilos de texto** (son globales: un pack no puede
repetir los de otro). Un pack inválido se omite y queda en `registry.problems()` y en `list_styles → problems`; no tumba el servidor. Exactamente un pack lleva `"default": true`.

### Agregar una pieza de código
Pon un `.py` en una carpeta de `MLT_PLUGIN_DIRS` que llame a `registry.register(...)` (o use los decoradores). Ejemplo (el que usa `test_modularity.py`):

```python
from mltedit import registry, cards
from mltedit.transitions import Mask
registry.register("transition", "spiral", Mask(lambda u, v, x, y, cx, cy, far: ..., 0.08))

@cards.layout("tagline")
def tagline(c):                      # c = cards.Ctx: c.block, c.put, c.boundary, c.rule, c.tile…
    ...
```
Un plugin que lanza una excepción se informa por archivo y no impide arrancar.

### Quitar una pieza
Borra el archivo o la carpeta. Pedir lo que falta da un error que lo nombra y lista lo que existe (`unknown op; known: …`, `no theme named …`).

## Flujo de una edición

1. Una herramienta (`tools/overlays.py`…) arma el op con su builder y llama a `server.commit(op)`.
2. `server._validate` hace `engine.layout(ops + [op])`: para cada op, `ops.get_op(k).layout(o, n, where, state)` valida y rellena el `LayoutState`;
   `core/timeline.resolve` lo pasa a la cuadrícula de cuadros, recorta overlays, asigna pistas y avisa de choques (cada layer plugin da su `zone`).
3. `engine.build` arma MLT: para cada layer, `plugin.source / place / keys / crop_rect` dan la fuente, la posición y los keyframes.

El estado mutable (`W, H, FPS, CLIPS, CACHE, THEME, MOTION`) vive en un `EngineContext` (`engine.CTX`); `live.W = 1920` sigue funcionando (propiedades del módulo).

## Fachadas

`live.py`, `server.py`, `themes.py`, `cards.py`, `graphics.py`, `icons.py`, `anim.py`, `transitions.py`, `textrender.py`, `sketch.py`, `assets_lib.py` hacen
`sys.modules[__name__] = importlib.import_module("mltedit.X")`: son el mismo objeto módulo, no una copia, así que `live.THEME = …` o `server.HOME = …` modifican el módulo real.
No contienen lógica.

## Pruebas

| Suite | Qué prueba |
|---|---|
| `snapshot.py check` | 686 hashes de píxeles/claves (15 plantillas × componentes, máscaras, keyframes de animación): la salida no cambió con la modularización |
| `golden.py` | 15 renders de la plantilla luxury idénticos al original |
| `test_modularity.py` | agregar/quitar plantillas, plugins externos, pack roto, quitar transición/op, y que no haya nombres ni rutas escritos en el código |
| `test_engine`, `test_mcp`, `test_text`, `test_anim`, `test_transitions`, `test_cards_anim`, `test_assets` | comportamiento del motor y de las herramientas |

## Proyecto v2, anclaje y revisiones (`mltedit/project/`)

`project.json` (schema 2): `sources`, `ops`, formato, `theme`, `motion`, más `revision`, `undo`, `redo`.
* **Ids estables.** Cada op tiene `id` (`op_` + 6 hex); un clip de la pista base se identifica con el id de su op `add`. `cut`, `crossfade`, `trim`, `move` y los anclajes guardan ids de clip, nunca posiciones: borrar o insertar antes no cambia a qué apunta nada. Los proyectos v1 se migran en memoria de forma determinista (mismos ids siempre) y producen exactamente el mismo timeline.
* **Revisión.** Cada guardado suma 1 y añade una línea a `history.jsonl` (auditoría: la verdad es `project.json`, escrito de forma atómica). Las herramientas de edición aceptan `expected_revision` (si el proyecto cambió: `REVISION_CONFLICT`, sin cambios), `dry_run` (no escribe; responde con un diff) y `request_id` (un reintento no aplica nada dos veces). `new_project` abre un journal nuevo.
* **Undo/redo** son pilas de parches (`pop`, `insert`, `replace`, `set`, `batch`); aplicar un parche devuelve su inverso. Un `apply_ops` o un `remove_op` con cascade se deshace de una vez.
* **Anclaje.** Los overlays y el audio se anclan por defecto al fotograma de la FUENTE del clip que está en pantalla en `start_s` (el clip entrante gana dentro de una transición): `{"clip": id, "src_f": n}`; `anchor="timeline"` los deja en tiempo absoluto; un sfx de `crossfade` sigue a su transición. `core/timeline.resolve` recalcula el inicio desde donde está el clip AHORA; si el momento anclado se recortó, el edit se oculta con un aviso `ANCHOR_LOST`. Subtítulos, `path` de un callout e intervalos de ducking se mueven con su edit. `trim_clip`, `move_clip`, `move_op` y `remove_op(cascade | reanchor="timeline")` operan sobre esto.

## Validación determinista

`errors.EditError` (un `ValueError` con código estable + una línea JSON) llega al agente en todos los fallos; una op mal formada es `INVALID_ARGUMENT`, nunca `KeyError`. Las ops se guardan **normalizadas** (`Op.defaults`) y con los hechos del mundo medidos al hacer commit (`Op.freeze`: aspecto de imagen, firma de archivos), así que `layout()` es puro y no lee el disco. `layout_hash(proyecto)` identifica un timeline. `verify_sources` / `refresh_source`: un archivo ausente bloquea los renders (`SOURCE_MISSING`); uno cambiado avisa. `test_determinism.py` (hypothesis) comprueba que ninguna secuencia de llamadas escapa del catálogo de errores, que el layout es idempotente y que da los mismos bytes en procesos distintos.

## Previews: proxies, caché y visor

* **Proxies** (`media/proxy.py`): las fuentes más altas que `proxy_height` (540) obtienen en segundo plano una copia H.264 intra-only (mismo número de cuadros y fps, audio conservado, rename atómico, nombrada por la identidad actual del archivo). Stills, hoja de contactos y `render_preview` la usan; `export` siempre lee los originales. Un still espera hasta `proxy_wait_s` a un proxy pendiente para que lo que se ve no dependa del azar.
* **Caché de stills** en disco, con clave = `layout_hash` + firmas de archivo + escala + uso de proxy; poda LRU.
* **Visor** (`open_viewer`): servidor HTTP en 127.0.0.1 con token aleatorio; HLS con segmentos de 2 s nombrados por un hash de lo que puede cambiar sus píxeles (`preview/segments.py`); un proceso worker (`preview/worker.py`) renderiza cada segmento cuando se pide (`tractor.set_in_and_out`, verificado idéntico al render completo) y el siguiente por adelantado; el audio es un solo encode continuo cortado en partes HLS (sin costuras). Una edición re-renderiza solo los segmentos cuyo hash cambió; la página (hls.js local) recarga al mismo tiempo de reproducción cuando cambia la revisión.

## Para agentes

`query(at_s | start_s..end_s)` dice qué hay en pantalla/audible con ids y anclajes; `describe_project(budget)` resume sin volcar todo. `export` y `render_preview` aceptan `background=true` (proceso aparte sobre una instantánea del proyecto; `job_status`, `cancel_job`, `list_jobs`) y devuelven `qa`: el archivo renderizado se revisa (`mltedit/qa.py`) buscando cortes duros inesperados y destellos de un cuadro; un corte entre dos clips sin crossfade es lo pedido y no se reporta. La semántica común de las herramientas de edición está una vez en las instrucciones del servidor, no repetida en cada docstring.

## Operación

`MLT_LOG=json` (por defecto) escribe una línea JSON por llamada a herramienta en stderr (herramienta, ms, ids, revisión, código de error); `MLT_LOG=off` la silencia. `./ci.sh` corre todo lo que debe estar en verde (pyflakes, snapshot, golden y todas las suites; `BENCH=1` añade el benchmark de preview). `pyproject.toml` empaqueta `mltedit` con el comando `mltedit-server`; instalado, `MLT_DATA_ROOT` debe apuntar a la carpeta con `fonts/` y `assets/`.



## Aplicación web (`app/`)
```
navegador ──HTTP/SSE──▶ app/api.py (starlette+uvicorn) ── token (Bearer o cookie HttpOnly SameSite=Strict), límite de intentos de login
                          ├─ app/projects.py   carpeta por proyecto: meta.json, chat.json, usage.json, uploads/, exports/ + los archivos del motor; ids `p_xxxxxxxx` validados en CADA uso
                          ├─ app/host.py       UN proceso `server.py` por proyecto (MCP stdio), cada uno en su propia tarea asyncio (los contextos anyio no se pueden abrir y cerrar
                          │                    desde tareas distintas); tope de procesos (se detiene el menos usado), apagado por inactividad, reinicio en la llamada siguiente
                          ├─ app/chat.py       bucle de herramientas: mensaje → modelo → herramientas del motor → resultados → ... con topes de turnos y de USD (por mensaje y por proyecto)
                          └─ app/model.py      `ModelClient`: AnthropicModel (SDK, streaming, caché de prompt) y ScriptedModel (pruebas)
```
- **Aislamiento:** el proceso del motor recibe `MLT_EDITOR_HOME` = `MLT_EDITOR_ROOTS` = la carpeta del proyecto y NO recibe `ANTHROPIC_*` ni `MLT_APP_*`; el fence de rutas del motor (activado por defecto) impide que un modelo lea otro proyecto o `/etc/passwd`.
- **Subidas:** `PUT /api/projects/<id>/uploads/<nombre>` con el archivo como cuerpo (sin multipart): se escribe a un `.part`, se renombra al terminar, un video se importa con `import_clip` (que aplica los límites y valida con ffprobe) y si falla se borra.
- **Visor en vivo:** el visor del motor sirve todo bajo `/<token>/…`; la aplicación lo reenvía en su raíz (proxy con la cookie de la app) y registra el token por proyecto.
- **Docker:** `Dockerfile` (ubuntu 24.04, usuario no root, `HEALTHCHECK`), `docker-compose.yml` (límites de memoria/CPU/procesos, `cap_drop: ALL`, `no-new-privileges`, puerto solo en 127.0.0.1, volumen `/data`).
- **Pruebas:** `test_app.py` (servidor real + motor real + modelo guionizado), `test_ui.py` (Chromium con Playwright), `tools/e2e_session.py` (sesión con metraje real, no entra en `ci.sh`).
