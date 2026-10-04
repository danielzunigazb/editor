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
