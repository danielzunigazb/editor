# Usar el editor desde Claude Code

La configuración ya está en `.mcp.json` (raíz del repo, ámbito de proyecto):

```json
{ "mcpServers": { "mlt-video-editor": {
    "type": "stdio",
    "command": "poc_mlt/.venv/bin/python",
    "args": ["poc_mlt/server.py"],
    "env": { "MLT_EDITOR_HOME": "poc_mlt/out/mcp" } } } }
```

## Pasos
1. Instalar dependencias una vez: `cd poc_mlt && ./setup.sh` (apt: melt, python3-mlt, ffmpeg, xvfb; venv con `--system-site-packages`; `mcp<2`).
2. Generar clips de prueba: `cd poc_mlt && /usr/bin/python3.12 poc.py gen` (crea `media/clip_a.mp4` y `clip_b.mp4`).
3. **Abrir Claude Code desde la raíz del repo** (las rutas del `.mcp.json` son relativas a ella).
4. **Aprobar el servidor.** Claude Code pide aprobación manual para los servidores de un `.mcp.json`
   de proyecto (protección contra código ajeno). Se aprueba en el aviso al abrir, o con `/mcp`.
   Hasta entonces `claude mcp list` lo muestra como "Pending approval". No se aprueba por archivo a propósito.
5. Comprobar: `/mcp` debe listar `mlt-video-editor` con 21 herramientas (clips, cortes, fundidos, PiP, **texto y subtítulos con 6 estilos, gráficos de lujo, tercio inferior, imágenes**, vista y export). `list_styles` describe los estilos.

## Ejemplo de prompt
> Con mlt-video-editor: crea un proyecto, importa `poc_mlt/media/clip_a.mp4` (id A) y `clip_b.mp4` (id B),
> deja A en sus primeros 4 s, añade B, fundido cruzado de 1 s, fade out de 1 s, y revisa con la hoja de
> contactos antes de exportar a `poc_mlt/out/final.mp4`.

## Alternativas
- Solo para ti, sin tocar el repo: `claude mcp add --scope user mlt-video-editor -- /ruta/abs/.venv/bin/python /ruta/abs/server.py`
- Otro cliente MCP (Claude Desktop, etc.): `poc_mlt/mcp.example.json` con rutas absolutas (edítalas; trae las de este contenedor).

## Verificado y no verificado
- Verificado: el servidor conecta y funciona cargado por Claude Code (`claude -p --mcp-config .mcp.json`):
  un modelo real encadenó `new_project`, `import_clip`, `add_clip`, `crossfade`, `set_fades` y
  `get_contact_sheet`, y reportó la duración correcta (8 s) y el contenido de la hoja de contactos.
  Esa ejecución además destapó un bug (frame 0 negro con `fade_in=0`), ya corregido y con prueba de regresión.
- No verificado: la aprobación interactiva del servidor, el flujo con `/mcp` en la TUI, ni una sesión larga
  de edición (solo una corrida corta de un modelo).
