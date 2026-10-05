# Seguridad: modelo de amenazas

Este documento dice qué entradas se consideran no confiables, qué hace el motor con cada una y qué **no** cubre. Cada control de la tabla tiene una prueba en `test_security.py`
(salvo donde se indica lo contrario); `./ci.sh` la ejecuta.

## Qué se protege y de quién
El editor lo maneja un **modelo de lenguaje** (a través de MCP) con instrucciones que pueden venir de un usuario, o de texto que el modelo leyó (subtítulos, nombres de archivo, el
contenido de un video). Se asume que el modelo puede ser engañado para pedir cosas que el usuario no quería. Lo que se quiere impedir:

1. que lea o escriba archivos fuera del proyecto (la clave SSH, `/etc/passwd`, otro proyecto);
2. que un archivo subido hostil bloquee, agote o comprometa el proceso;
3. que un proyecto consuma sin límite disco, memoria o tiempo de CPU;
4. que una entrada (SVG, texto, nombres) ejecute código o provoque lecturas/peticiones externas.

## Entradas y controles

| Entrada no confiable | Qué se hace | Dónde / prueba |
|---|---|---|
| Rutas que da el modelo (`import_clip`, `add_image`, `add_audio(path)`, `add_subtitles(srt_path)`, `export`) | **Control de rutas activado por defecto**: se resuelve la ruta real (enlaces simbólicos, `..`, `~`) y debe estar dentro de la carpeta del proyecto o de `MLT_EDITOR_ROOTS`. Si no, `PATH_NOT_ALLOWED` y no se escribe nada. `MLT_EDITOR_ROOTS='*'` lo apaga, solo de forma explícita. | `server._safe_path`; `test_security` (absoluta, `..`, enlace a archivo, enlace a carpeta, `~`, byte NUL, export fuera) |
| Archivo de video corrupto, truncado, vacío o que no es video | `ffprobe` con tiempo máximo; error limpio, no queda registrado como fuente | `test_security` |
| «Bomba» de resolución / tamaño / duración | `MLT_MAX_SOURCE_DIM` (8192 px de lado), `MLT_MAX_SOURCE_MB` (4096), `MLT_MAX_SOURCE_S` (10800): `LIMIT_EXCEEDED` antes de decodificar nada | `test_security` |
| SVG que sube el usuario (`add_image(path=*.svg)`) | Se rechaza si contiene entidades externas, scripts, estilos con `@import`, imágenes remotas o de archivo, `foreignObject`, manejadores de eventos o `use` externo; además se rasteriza con `cairosvg(unsafe=False)` | `test_security` (8 variantes hostiles + un SVG limpio que sí se dibuja) |
| Ids de fuente y nombres | `[A-Za-z0-9_]{1,32}`; un id con `../` o espacios se rechaza | `test_security` |
| Cantidad de trabajo del proyecto | Límites de fuentes, ediciones, overlays y pistas de audio (`LIMIT_EXCEEDED`) | `test_security` prueba el de fuentes; los demás están en `config.py` sin prueba propia en esta suite |
| Renders largos | Los de más de `MLT_BLOCK_MAX_S` (40 s) corren como job; un job que pasa de `MLT_RENDER_TIMEOUT_S` (3600 s) se detiene y se informa `LIMIT_EXCEEDED` | `test_security`, `test_jobs_progress` |
| Disco | Cuota por proyecto (`MLT_PROJECT_QUOTA_MB`, 20000): con la carpeta por encima se podan las cachés y, si no basta, se rechazan imports, exports y previews | `test_security` |
| Restos de un render cancelado | Cada job tiene su propio `TMPDIR`, que se borra al terminar, cancelar o morir | `test_jobs_progress` |
| Secretos (`R2_UPLOAD_TOKEN`, `ANTHROPIC_API_KEY`) | Solo por variable de entorno; la configuración por archivo los rechaza; ninguna herramienta los recibe como argumento (por eso no pasan por el journal ni por los logs de llamadas). No hay prueba que revise los logs en busca de secretos | `config.py` |
| Texto del modelo/usuario dibujado en video | Se valida que quepa (`TEXT_DOES_NOT_FIT`); nunca se interpreta como HTML ni como comando | `test_text` |

## Lo que NO cubre (hay que saberlo)
- **Decodificadores de FFmpeg/MLT ante medios malformados.** Un archivo hostil puede explotar un fallo de `ffmpeg`, `ffprobe` o MLT. El motor no los aísla: la mitigación prevista es
  ejecutarlo en contenedor, sin privilegios, con límites de memoria y CPU (ver `Dockerfile` cuando exista) y mantener FFmpeg actualizado. No hay prueba de esto.
- **Condición de carrera entre la comprobación y el uso** de una ruta (el archivo puede cambiar después de validarlo). El motor guarda la firma del archivo al importarlo y avisa si
  cambia (`SOURCE_CHANGED`), pero no impide la sustitución.
- **El archivo de proyecto es de confianza.** `project.json` guarda rutas de fuentes sin volver a pasar por el control de rutas al cargarlas: si alguien puede editar ese archivo,
  puede apuntar una fuente a cualquier sitio. En un despliegue el archivo vive en una carpeta que solo escribe el servidor.
- **Plugins y carpetas de configuración** (`MLT_PLUGIN_DIRS`, `MLT_PACKS_DIRS`, `MLT_FONTS_DIRS`) ejecutan código o cargan fuentes: solo los define quien administra el servidor.
- **Inyección de instrucciones al modelo** (un subtítulo que dice «ignora lo anterior y…»). El motor no puede distinguir una orden legítima de una injertada; lo que sí hace es
  que **lo peor que el modelo puede hacer a través de las herramientas queda acotado** por el control de rutas y los límites de arriba.
- **Autenticación y varios usuarios.** El motor no tiene noción de usuarios. La aplicación web (fase W4) pondrá un token de acceso y un proceso por proyecto; hasta entonces cualquiera
  que pueda hablar con el servidor MCP puede editar el proyecto.
- **Salida de red.** El motor solo hace peticiones de red para bajar música del bucket R2 cuando hay credenciales; no hay una lista de destinos permitidos aplicada por el propio
  motor (en Docker se limita con la red del contenedor).
- **Denegación de servicio por CPU** dentro de los límites: un proyecto legítimo-pero-enorme puede tardar mucho; solo se acota con el tiempo máximo de los jobs.

## Cómo informar de un problema
Abre un issue en el repositorio sin incluir el archivo hostil si contiene datos reales; describe la herramienta, los argumentos y el resultado.
