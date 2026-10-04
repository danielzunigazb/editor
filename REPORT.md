# POC: MLT como motor de preview para un editor no lineal

**Veredicto corto:** MLT sirve como motor para este proyecto, con reservas concretas
(abajo). El modelo de timeline es el correcto y el rendimiento sobró en las pruebas, pero
la API de Python tiene trampas silenciosas que consumieron la mayor parte del tiempo del POC,
y el scrubbing con seeks aleatorios en H.264 de GOP largo es lento sin proxies.

Todo se hizo en un contenedor Linux headless (4 cores, 16 GB RAM, sin GPU, sin pantalla
ni tarjeta de audio). Eso condiciona la parte de "preview" (ver limitaciones).

## 1. Estado inicial del entorno y qué se instaló

| Componente | Antes | Después |
|---|---|---|
| `ffmpeg` / `ffprobe` | 6.1.1 instalado | sin cambios |
| `melt` (CLI de MLT) | **no instalado** | 7.22.0 |
| Binding Python (`mlt7`) | **no instalado** | 7.22.0 (`python3-mlt`) |
| Xvfb (`xvfb-run`) | no instalado | instalado (para el preview headless) |

Instalación (Ubuntu 24.04):

```bash
sudo apt-get update            # sin esto apt dio 404 en varios paquetes (índices viejos)
sudo apt-get install -y melt python3-mlt xvfb
```

**Problema de entorno:** el binding de apt está compilado solo para el Python del
sistema (`_mlt7.cpython-312-...so`). El `python3` por defecto de este contenedor es 3.11 y
falla con `ModuleNotFoundError: No module named '_mlt7'`. Hay que usar
`/usr/bin/python3.12` explícitamente. En un proyecto real habría que fijar ese intérprete
(o compilar el binding contra otro Python); un `venv` normal no ve el binding de apt.

## 2. Qué hace el script

`legacy/poc.py` (un solo archivo, subcomandos `gen | build | bench | preview | export | measure`):

1. `gen`: genera con ffmpeg dos clips de 720p25 (A: `testsrc2` 6 s + tono 440 Hz;
   B: `smptehdbars` 5 s + tono 880 Hz).
2. Timeline: **A cortado en t=3.0 s** (`split_at` + `remove`) → **B pegado después** →
   **crossfade de 1 s** (transición `luma` para video, `mix` para audio) → **fade-in de
   0.5 s y fade-out de 1 s** (filtros `brightness` y `volume`). Duración final: 175
   frames = 7.00 s.
3. `preview`: reproducción en tiempo real con el consumer `sdl2`.
4. `export`: MLT renderiza la composición como NUT (video raw + PCM) a un FIFO y el CLI de
   **ffmpeg hace la codificación final** (x264 CRF 20 + AAC) → `out/final.mp4`.
5. `measure`: ejecuta un subcomando como hijo y muestrea CPU/RSS desde `/proc` cada 200 ms.

Uso: `/usr/bin/python3.12 poc.py gen && /usr/bin/python3.12 poc.py export`
(para `preview` sin pantalla: `xvfb-run -a /usr/bin/python3.12 poc.py preview`).

Verificación del resultado exportado (`out/final.mp4`, h264 + aac, 1280x720, 7.000 s):

| Punto | Esperado | Medido |
|---|---|---|
| Luma t=0.0 s / t=6.9 s (fades) | casi negro | 4.7 / 4.7 (vs. 128 en t=1.0) |
| Crossfade t=2.5 s | mezcla A/B | luma 114 (A≈128, B≈102); además revisé el frame a ojo |
| Audio t=0–0.1 s / 6.9–7 s | silencio | -82.5 dB / -81.9 dB |
| Audio t=1–2.8 s | nivel pleno | -24.1 dB (igual que el clip fuente) |

**No verificado:** el crossfade de *audio* (`mix_add`). Ambos tonos tienen el mismo nivel,
así que `volumedetect` no distingue mezcla de no mezcla. Solo comprobé que no rompe nada.

## 3. Lo que falló (cronológico, sin maquillar)

Cuatro problemas distintos, y tres de ellos **fallaron en silencio**: ningún error, salida
"exitosa", resultado incorrecto. Los detecté solo porque inspeccioné frames y niveles de
audio del archivo exportado; las métricas de rendimiento por sí solas habrían salido
"perfectas" con un timeline roto.

1. **Off-by-one en el corte.** `split_at(74)` deja 74 frames, no 75, así que el timeline
   duraba 174 frames en vez de 175. Semántica de la API: `position` es el nº de frames que
   conserva la parte izquierda. Mío, por no leer; barato de arreglar.
2. **El fade-out de video no se aplicaba.** Los filtros adjuntos con `playlist.attach()`
   quedan con `in=0 out=0`; el fade-in funcionaba pero los keyframes lejanos se ignoraban
   sin aviso. Arreglo: `filter.set_in_and_out(0, total-1)` antes de adjuntar.
   *Mi primera hipótesis fue que dos filtros `brightness` apilados interferían; era
   incorrecta y la descarté midiendo `in/out` de cada filtro.*
3. **Audio del fade sin efecto (dos causas encadenadas):**
   - `volume.level` está en **dB**, no en ganancia lineal. Mis keyframes 0→1 eran 0→1 dB.
     (`gain` está deprecado y se ignora si hay `level`.)
   - Además introduje un bug propio: un comentario `#` mal puesto se comió
     `pl.attach(vo)`, así que el filtro de volumen ni siquiera estaba adjunto.
4. **El más grave: audio corrupto al usar `Producer(profile, "avformat", path)`.** Desde
   Python el audio exportado era ruido de escala completa (muestras ±32768, -5.3 dB
   constante) y los filtros de audio no hacían nada, mientras que `melt clip.mp4` con
   exactamente las mismas propiedades de consumer daba audio correcto (-24.1 dB). Causa: el
   CLI usa el producer `loader`, que inserta normalizadores de audio/video; instanciar
   `avformat` directo se los salta. Arreglo: `mlt7.Producer(profile, path)` (sin nombre de
   servicio). Tardé en encontrarlo porque bisecté: producer solo → playlist → filtro →
   tractor → XML recargado, y `melt` vs. Python con propiedades idénticas.

Otros tropiezos menores:
- `Frame.get_audio()` no es utilizable desde Python (SWIG no mapea el argumento por
  referencia `mlt_audio_format&`): `TypeError`. Para inspeccionar audio hay que renderizar
  a archivo y analizarlo con ffmpeg.
- El export a NUT imprime en **cada ejecución** (también en la versión final) los avisos
  `Timestamps are unset in a packet for stream 1. This is deprecated and will stop working
  in the future` / `Encoder did not produce proper pts, making some up`. Es el muxer NUT
  de MLT recibiendo audio PCM sin timestamps; ffmpeg se los inventa. El resultado tiene
  duración correcta (7.000 s) y los niveles de audio por tramo coinciden, pero **no medí
  la sincronía A/V fina** y el propio aviso dice que dejará de funcionar. Pendiente: probar
  otro contenedor de intercambio (p. ej. `matroska`/`mpegts`) o ruta alternativa.
- Al arrancar `melt`/MLT sale `No LADSPA plugins were found!` (ruido, inofensivo).

## 4. Mediciones

Las tres pedidas. Entorno: 4 cores, sin GPU. Contenido: clips sintéticos pequeños, así
que **trátese de un techo optimista**, salvo la prueba de estrés de 1080p.

### 4.1 Facilidad de manipular el timeline con la API de MLT

**Lo bueno:** el modelo mental encaja con un editor NLE. `Playlist` = pista;
`append(producer, in, out)` = clip con recorte; `split_at` / `remove` / `insert` /
`mix` = operaciones de edición; `Tractor`/`Multitrack` para pistas paralelas; filtros y
transiciones como objetos. Las operaciones del timeline cuestan prácticamente nada:

| Operación | ms |
|---|---|
| `Factory.init` (una vez) | ~56 |
| Abrir clip (probe + loader) | 9–17 |
| Cortar (`split_at`+`remove`) | 0.03 |
| Crossfade (`mix`+`mix_add`) | 0.1–0.2 |
| Fades (2 filtros) | 0.06 |

Serializa/deserializa a XML (`.mlt`) sin esfuerzo (`consumer "xml"` / `Producer(...,"xml")`),
lo cual es ideal para un futuro servidor MCP (estado del proyecto = archivo).

**Lo malo (mi valoración: facilidad media-baja):**
- Muchas propiedades son strings sin tipado (`"0=0;12=1"`) y las unidades cambian entre
  servicios (dB vs. lineal) sin que nada avise.
- Fallos silenciosos frecuentes (sección 3: 3 de los 4 problemas no dieron ninguna señal).
- Semántica de índices fácil de errar (`split_at`, `mix` consume frames del clip anterior,
  los clips con mezcla se convierten en un `<tractor>` interno con su propia entrada).
- Documentación escasa: la verdad estaba en `melt -query filter=<nombre>` y en prueba y error.
- El binding Python es SWIG genérico: API tipo C++, parcialmente inutilizable
  (`get_audio`), sin type hints ni autocompletado útil.
- Atado a Python 3.12 del sistema vía apt.

Estimación honesta: la lógica de timeline en sí son pocas líneas y se escribe rápido, pero
la mayor parte del tiempo del POC se fue en depurar las trampas de la sección 3. Una vez
conocidas, escribir ediciones es directo; el costo está en descubrirlas. Para un MCP, conviene envolver MLT en una capa propia
que valide y encapsule esas trampas.

### 4.2 Fluidez del preview

| Prueba | Resultado |
|---|---|
| Preview real-time (`sdl2`, Xvfb, audio `dummy`), 720p25, 7 s | **0 frames descartados** (`drop_count`), duración 7.04 s (reloj ok) |
| Decodificación secuencial 720p25 (175 frames, sin display) | p50 1.7 ms, p95 4.6 ms, p99 24.6 ms, máx 28.5 ms; **0 frames sobre el presupuesto de 40 ms**; ~400 fps |
| Secuencial 1080p30 high-motion con dissolve (210 frames) | p50 4.3 ms, p95 11 ms, máx 89 ms; **2 de 210 sobre 33.3 ms** (picos al abrir/seek) |
| **Seek aleatorio 720p25** (scrubbing) | p50 18 ms, p95 55 ms, máx 77 ms |
| **Seek aleatorio 1080p30, GOP=250** | **p50 130 ms, p95 330 ms, máx 408 ms; 85 de 100 sobre presupuesto** |

Conclusión: la **reproducción** es holgada (el motor rinde 10x+ tiempo real en esta
máquina). El **scrubbing** en H.264 de GOP largo **no es fluido**: cada salto aleatorio
decodifica desde el keyframe anterior. No es defecto exclusivo de MLT (es inherente al
formato), pero un editor real tendría que resolverlo con proxies intra-frame / GOP corto
o caché de frames. MLT trae soporte de proxy en Shotcut/Kdenlive, no lo probé aquí.

### 4.3 CPU y RAM

Medido por muestreo de `/proc` del proceso hijo y sus descendientes (100% = 1 core).

| Modo | Duración | CPU media | CPU pico | RSS pico | RSS medio |
|---|---|---|---|---|---|
| Decode secuencial (bench), 720p | 0.6 s | 116% | 215% | 166 MB | 97 MB |
| **Preview real-time**, 720p25 | 7.4 s | **43%** (de 1 core) | 145% | **383 MB** | 353 MB |
| Export (MLT→NUT→ffmpeg x264 medium) | 2.8 s | 229% | 359% | 489 MB | 369 MB |

El export de 7 s de 720p tardó ~2.6 s (≈2.7x tiempo real, con 4 cores, x264 `medium`).
El RSS del preview (~380 MB) es en buena parte el overhead base de SDL2/Xvfb/MLT; no
medí cómo crece con más pistas o clips.

## 5. Limitaciones de este POC (léanse antes de fiarse de los números)

- **Preview nunca visto ni oído por una persona.** Corrió en Xvfb con driver de audio
  `dummy`. `drop_count=0` y el reloj correcto indican que MLT cumplió el tiempo real, pero
  no valida sincronía A/V percibida, tearing ni latencia de display real. Hay que probarlo en
  un escritorio real.
- **Contenido mayormente sintético y pequeño** en las secciones 2-4 (testsrc2, barras,
  mandelbrot). La sección 8 añade 3 clips reales de celular; siguen sin probarse 4K, 10-bit,
  clips largos y múltiples pistas con composición pesada.
- **Sin GPU:** no probé Movit (filtros GPU) ni rendimiento con efectos pesados.
- **Una sola máquina de 4 cores**: sin comparativa con hardware modesto.
- **Una pista de video**. No probé multipista, composición (`composite`), cambios de
  velocidad, clips con distinto fps/resolución ni edición (insert/ripple) con el timeline
  ya reproduciéndose.
- El **crossfade de audio** no está verificado (sección 2).
- La medición de CPU es por muestreo a 200 ms: los picos cortos pueden estar subestimados.
- Un solo run por medición; no hay varianza reportada. Las latencias p99/máx de las pruebas
  cortas (175 muestras) son poco estables.

## 6. Recomendación

**Sí, seguir con MLT para el motor, con estas condiciones:**

1. **Por qué:** modelo de timeline idéntico al de un NLE, edición de coste casi cero,
   serialización a XML lista, reproducción holgada con una pista (pero **no** con 3 capas a 1080x1920 en CPU, ver sección 9), y exportación desacoplable
   a ffmpeg tal como querías (funcionó vía NUT/FIFO, 7.000 s exactos). Es el mismo motor
   de Shotcut y Kdenlive, así que está probado en producción.
2. **Condición clave: encapsular MLT detrás de nuestra propia capa** (y por tanto detrás
   del futuro MCP). Esa capa debe: usar siempre el `loader`, fijar `in/out` de los
   filtros, hacer explícitas las unidades, y **validar el resultado renderizando y
   comprobando** (frames/niveles), porque MLT falla en silencio.
3. **Resolver el scrubbing y el rendimiento multicapa** antes de prometer UX de editor:
   proxies de baja resolución (GOP corto/intra) y/o caché de frames. Sin eso los seeks
   aleatorios en 1080p rondan 130 ms, y con 3 capas a 1080x1920 (sección 9) el preview ya
   descarta frames en CPU. Probar Movit/GPU y proxies es el siguiente paso, antes de decidir.
4. **Resolver el despliegue:** `qtblend` (único compositor probado que respeta la opacidad)
   exige X11/`xvfb-run` incluso en servidor. Además, el binding de apt solo sirve con el Python
   del sistema (3.12 aquí). Decidir entre fijar ese intérprete, compilar el binding, o
   evitar el binding y hablar con MLT por XML + `melt`.
5. **Alternativas a evaluar si esas condiciones molestan** (no probé ninguna):
   - **GStreamer + GES (GStreamer Editing Services)**: también modelo de timeline; más
     piezas móviles y API más compleja.
   - **FFmpeg puro con filtergraphs** generados desde un modelo propio: sin preview
     interactivo ni seek eficiente, pero control total y cero binding. Valdría solo si el
     "preview" pasa a ser renders cortos por demanda (a veces suficiente para un flujo
     dirigido por un agente/MCP).
   - Un motor propio sobre PyAV/OpenGL: máximo control, máximo costo; no lo recomiendo
     para un POC.

   Si el uso real es sobre todo agente → timeline → render, FFmpeg puro merece una
   comparación rápida de costo/beneficio *antes* de comprometerse. Si se quiere preview
   interactivo, MLT es la mejor opción de las tres.

## 8. Prueba con clips reales (añadida después)

Tres videos de celular que subió el usuario (el cuarto, `VID-20260814-WA0016.mp4`, no llegó a
probarse: no se compartió su enlace). Se ejecuta con `POC_MODE=real /usr/bin/python3.12 poc.py <cmd>`
y los archivos deben estar en `media_real/` (ignorado por git: son videos personales).

| Clip | Códec | Resolución | FPS prom. | Audio |
|---|---|---|---|---|
| real1 | H.264 Constrained Baseline | 1088x1936 vertical | ~23.7 (VFR) | mono 44.1 kHz |
| real2 | H.264 High | 474x850 vertical | ~18.8 (VFR) | mono 44.1 kHz |
| real3 | **HEVC** Main | 1080x1920 vertical | ~24 (VFR) | estéreo 44.1 kHz |

Timeline: real1[0-3 s] → disolvencia 0.5 s → real2[0-4 s] → disolvencia 0.5 s → real3[0-3 s],
con fade-in/out, en un perfil personalizado 1080x1920@24 (216 frames = 9.00 s).

**Resultado: funcionó a la primera con la capa ya corregida.** Mezcla sin problemas
resoluciones distintas (reescalado sin deformar), códecs H.264/HEVC, mono/estéreo, 44.1 kHz→48 kHz
y VFR→24 fps constante. `final_real.mp4`: h264+aac, 1080x1920, 9.000 s exactos. Revisé frames a
ojo en cada tramo (fade desde negro, clip 1, disolvencia, clip 2, clip 3, fade a negro) y
audio con `volumedetect` (-87 dB al inicio, -68 dB al final, niveles intermedios acordes a cada
clip). No hubo errores nuevos de API. Los avisos de timestamps de NUT siguen apareciendo.

| Medida (1080x1920@24, 4 cores, sin GPU) | Resultado |
|---|---|
| Abrir 3 clips | 179 ms (los sintéticos: 9-17 ms c/u) |
| Decodificación secuencial, sin display (216 frames) | p50 11.7 ms, p95 24.6 ms, p99 70 ms, máx 172 ms; **4 de 216 sobre 41.7 ms**; 68 fps |
| Preview real-time (sdl2, Xvfb 1080x1920, audio dummy) | 9.11 s de reloj para 9.0 s, **0 descartados** |
| Seek aleatorio (scrubbing), 80 saltos | **p50 47 ms, p95 237 ms, máx 297 ms; 48 de 80 sobre presupuesto** |
| CPU/RAM bench secuencial | 193% media, 314% pico (de 1 core); RSS pico 297 MB |
| CPU/RAM export (MLT→NUT→ffmpeg x264 medium) | 330% media, 399% pico; **RSS pico 910 MB**; 13 s para 9 s de video (~1.4x tiempo real) |

Lectura honesta, comparada con la prueba sintética:
- Con video real vertical 1080p la reproducción sigue cumpliendo, pero **con mucho menos
  margen**: p50 de 11.7 ms por frame (antes 1.7 ms) y 4 frames fuera de presupuesto en
  decodificación pura. Un solo núcleo ya iba al límite en picos; en una máquina más modesta
  o con más pistas no daría.
- **El scrubbing es el punto débil confirmado**: la mitad de los saltos aleatorios supera
  el presupuesto de un frame, sobre todo por HEVC y H.264 de GOP largo. Hace falta proxy.
- **El export es ~1.4x tiempo real** y usa casi todos los cores y ~0.9 GB de RAM. Pasa de
  2.7x en 720p sintético a 1.4x aquí.
- El "preview" sigue siendo headless (no se ha visto ni oído en una pantalla real).
- Sigue sin cubrir: multipista con composición, 4K, clips largos, 10-bit, rotación por metadata
  (estos clips ya venían en vertical sin flag de rotación), el cuarto video.

## 9. Prueba multipista con composición (añadida después)

`POC_MODE=multi xvfb-run -a /usr/bin/python3.12 poc.py <cmd>` (usa los clips de `media_real/`).
Timeline de 6.00 s (144 frames) a 1080x1920@24 con **3 pistas** en un `Tractor`:

| Pista | Contenido | Composición |
|---|---|---|
| 0 (base) | real1[0-3 s] → real3[0-3 s] | pantalla completa |
| 1 (PiP A) | real2, de 1 s a 5 s | entra deslizándose por la derecha (1 s), se queda arriba a la derecha, y al final se encoge y se desvanece |
| 2 (PiP B) | real3 (HEVC), de 2.5 s a 4.5 s | abajo a la izquierda, fijo, 60% de opacidad |

Video con transiciones `qtblend` (rect + opacidad con keyframes) y audio con `mix` (`sum=1`)
entre pistas; fade-in/out de video sobre el tractor. `final_multi.mp4`: h264+aac, 1080x1920,
6.000 s exactos. Verificado viendo frames a lo largo del timeline (entrada del PiP A, PiP B
translúcido, desvanecido final) y con `volumedetect`. Siguen sin verificarse la sincronía A/V
fina ni la mezcla de audio entre pistas más allá de que los niveles suben al sumar pistas
(-31 dB solo base → -9 dB con las tres).

### Trampas nuevas (otra vez fallos silenciosos)
1. **Los keyframes de una transición son relativos a su frame `in`**, no frames absolutos del
   timeline. Con frames absolutos la animación del PiP llegaba 1 s tarde, sin error.
2. **`composite` y `affine` ignoran la opacidad** en esta configuración (probado aislado: 50% se
   renderiza opaco, 0% transparente; solo "todo o nada"). **`qtblend` sí la respeta** (50% → 128;
   animada 1→0 a mitad → 128). Descubrí esto porque el "fade" del PiP no se veía: el tamaño se
   animaba pero la opacidad seguía al 100%.
3. **`qtblend` exige un entorno X11** aunque no haya ventana: sin `DISPLAY` el `Transition` se
   crea inválido (hay que comprobar `is_valid()`; imprime "The MLT Qt module requires a X11
   environment"). Para exportar en un servidor hace falta `xvfb-run` o similar. Esto complica
   el despliegue de un backend/MCP headless.

### Rendimiento (1080x1920@24, 4 cores, sin GPU; compárese con la sección 8, 1 pista)

| Medida | 1 pista, real (sec. 8) | **3 pistas + qtblend** |
|---|---|---|
| Decodificación secuencial, p50 / p95 / máx | 11.7 / 24.6 / 172 ms | **35.0 / 70.0 / 456 ms** |
| Frames sobre presupuesto (41.7 ms) | 4 de 216 | **38 de 144 (26%)** |
| Throughput sin display | 68 fps | **27 fps** |
| Preview real-time (sdl2, Xvfb), 3 corridas | 0 descartados | **6, 7 y 11 descartados de 144** (4-8%); 6.2-6.5 s de reloj para 6.0 s |
| Scrubbing (80 saltos), p50 / p95 | 47 / 237 ms | **112 / 579 ms**; 66 de 80 sobre presupuesto |
| CPU media preview / export | -- / 330% | 176-185% / 299% |
| RAM pico preview / export | -- / 910 MB | **~760 MB / 1.18 GB** |
| Export (MLT→NUT→ffmpeg), 6 s de video | 13 s por 9 s (~1.4x) | **11.5-12 s por 6 s (~0.5x tiempo real, 2x más lento que el video)** |

Lectura honesta: **con 3 capas a 1080x1920 en CPU, MLT deja de cumplir tiempo real en esta
máquina.** El preview descarta frames visiblemente (el consumer `real_time=1` los suelta para
mantener el reloj) y la mitad del preview "pelea" con el presupuesto de frame. El motor sigue
siendo correcto (el export es exacto, sin errores), pero un editor interactivo con varias capas
a esta resolución necesitaría **proxies de baja resolución para el preview** (p. ej. 540x960),
caché de frames o composición por GPU (Movit), nada de lo cual probé. Parte del costo puede ser
`qtblend` (Qt en CPU sobre Xvfb): no medí `composite` por separado porque ignora la opacidad.
La grabación con x11grab (a 1080x1920) añade carga: con ella el preview descartó 20 frames (vs. 6-11 sin ella). El video `preview_capture_multi.mp4` está, por tanto, grabado bajo carga y no es representativo del mejor caso.

## 11. Servidor MCP (añadido a petición del usuario)

`server.py` envuelve el motor (`live.py`) como servidor MCP por stdio. El LLM es el único
control; no hay UI. Instalación: `./setup.sh`; configuración de cliente: `mcp.example.json`.
Prueba end-to-end con un cliente MCP real (SDK, proceso aparte): `.venv/bin/python test_mcp.py`
-> **30 de 30 comprobaciones pasan**, incluidos los errores esperados.

**Herramientas (15):** `new_project`, `import_clip`, `list_sources`, `add_clip` (con rango
`start_s..end_s`), `cut_clip`, `crossfade`, `set_fades`, `add_pip`, `get_timeline`, `undo`,
`remove_op`, `get_still`, `get_contact_sheet`, `render_preview`, `export`.

**Decisiones de diseño**
- **Edición instantánea, render bajo demanda.** Cada edición se valida contra el timeline
  completo con un modelo en Python puro (sin MLT) y solo entonces se guarda; una edición
  inválida se rechaza con un mensaje accionable y deja el estado intacto (se probaron 7
  casos: corte fuera de rango, entrada inexistente, fundido más largo que el clip, fade más
  largo que el timeline, posición de PiP inválida, PiP fuera de la fuente, rango fuera de la
  fuente). `remove_op` se rechaza si ediciones posteriores dependen de la quitada.
- **El LLM "ve" con `get_contact_sheet` y `get_still`**, que devuelven imágenes PNG. Una hoja
  de 6 frames equidistantes muestra cortes, fundidos, fades y overlays de un vistazo
  (revisada a ojo: negro, A, mezcla A/B + PiP translúcido, B + PiP, B, negro).
- **Estado declarativo:** el timeline siempre se reconstruye reproduciendo la lista de ops, así
  `undo`/`remove_op` son triviales y el estado es un JSON legible (`MLT_EDITOR_HOME/project.json`).
- **Higiene de stdout:** MLT/ffmpeg pueden escribir en el fd 1 y romper el protocolo stdio; el
  servidor redirige el fd 1 a stderr y deja el protocolo en una copia del fd original.
- **X11 autogestionado:** `qtblend` exige X11; si no hay `DISPLAY` el servidor arranca su
  propio `Xvfb` (`-displayfd`) y lo cierra al salir. La prueba corre sin `DISPLAY` para demostrarlo.

**Latencias medidas** (clips sintéticos 720p, 4 cores, sin GPU, a través del protocolo MCP):

| Herramienta | Tiempo |
|---|---|
| `get_still` (primera llamada: construye + 1 frame) | 0.29 s |
| `get_still` (media de 30 seguidas) | 183 ms |
| `get_contact_sheet` (6 frames) | 1.0 s |
| `export` draft (7 s de video, 1280x720) | 2.8 s |

**Limitaciones y cosas no probadas (leer antes de confiar)**
- **No se probó con un LLM real conectado**, solo con el cliente del SDK de MCP. Que un
  modelo use bien las herramientas (nombres, descripciones, orden de llamadas) es una hipótesis
  sin validar. Las respuestas con imagen requieren un cliente que las muestre al modelo.
- **Un solo PiP** (uno nuevo reemplaza al anterior), una sola pista base y un solo conjunto de
  fades; sin cambios de velocidad, texto, ni mezcla de audio por pista. Es el alcance del POC.
- **No hay video por MCP:** `render_preview` devuelve la ruta del mp4, no el video.
- **`export` es bloqueante** (sin progreso ni cancelación) y puede tardar más que el propio
  video a 1080p (secciones 8 y 9). Con 3 capas a 1080x1920 fue ~2x más lento que el video.
- **Un proyecto por proceso**, sin concurrencia ni bloqueos de archivo. `mcp` fijado a `<2`
  porque la 2.x cambió la API. Sin prueba de fugas de memoria en sesiones largas (solo 30
  llamadas seguidas sin problemas en un mismo proceso).
- `setup.sh` no se ejecutó de punta a punta (ya tenía los paquetes apt instalados); sí se
  ejecutaron por separado el `venv` y el `pip install`.
- La prueba usa clips sintéticos; los clips reales verticales/VFR (sección 8) no pasaron por
  el servidor. Los límites de rendimiento de las secciones 8 y 9 aplican igual.

### 11.1 Corrida con un modelo real y clips reales (vía `.mcp.json` de Claude Code)

`claude -p --mcp-config .mcp.json` con los 3 clips reales (vertical, VFR, H.264/HEVC). El modelo
hizo 14 llamadas a herramientas sin ayuda (proyecto 1080x1920@24, 3 imports, 2 `add_clip`,
`crossfade` 0.5 s, `add_pip` abajo a la izquierda, `set_fades`, `get_contact_sheet`, `get_still`
a resolución completa, `export` draft) y no necesitó corregir nada. Duración del timeline 6.5 s y
export draft 6.5 s de render. **Verificación independiente del mp4** (`real_mcp.mp4`):
h264+aac, 1080x1920, 6.500 s exactos; luma 7.7 en t=0 -> 108 en t=1 -> 7.2 en t=6.4 (fade-in y
fade-out presentes); audio -87 dB en ambos extremos y -31 dB en medio; PiP visible abajo a la
izquierda durante el cruce y después; dissolve visible a ojo. El modelo declaró honestamente lo
que no pudo verificar (opacidad, fade-out, audio, archivo final); esas cuatro cosas resultaron
estar correctas al comprobarlas aparte.

Observaciones: el modelo acortó el PiP a 2.5 s por su cuenta (de 2 a 4.5 s, lo pedido);
R2 (474x850) se escala ~2.3x y se ve blando (limitación de la fuente, no del motor).
Sigue siendo **una sola corrida** de **un** modelo.

### 11.2 Texto, subtítulos e imágenes (añadido a petición del usuario: "estable y sin errores")

Tres herramientas nuevas: `add_text`, `add_subtitles` (lista de cues o archivo `.srt`) y `add_image`;
`add_pip` pasó de reemplazar a **acumular**. Ahora hay "capas" (PiP, texto, subtítulos, imagen) con hasta
6 simultáneas. Total: 18 herramientas.

**Decisión de estabilidad: el texto no lo dibuja MLT/Qt (`qtext`), sino Pillow con una fuente fija**
(`textrender.py`, DejaVu Sans Bold) a un PNG transparente que se compone con `qtblend`. Así el mismo texto
da siempre los mismos píxeles, los acentos/ñ/¿¡ están garantizados, y se evita depender de la configuración de
fuentes de Qt. El texto se **ajusta a líneas, se reduce para caber** (máx. 4 líneas, 200 caracteres) y, si no
cabe o la fuente no tiene un glifo (CJK, emoji recientes), se **rechaza con un mensaje claro antes de guardar**.
Una sonda inicial mostró por qué: una frase larga a 36 px se salía del cuadro por ambos lados.
`.srt`: tolera BOM, CRLF, latin-1 y etiquetas `<i>`; es estricto con los tiempos y cita el bloque erróneo.

**Bug de fondo encontrado y corregido: parpadeo negro tras cada capa.** Un modelo real, al revisar su propio
trabajo, notó cuadros negros extraños en la hoja de contactos y declaró no entenderlos. Al escanear **cada frame**
del export: los frames posteriores al final de cada capa salían casi negros (6 de 156). Descarté varias hipótesis
(transiciones apiladas, extender el rango, rellenar huecos con un productor de color: esta última empeoraba
todo) hasta dar con la causa real: **`Playlist.blank(n)` recibe el punto de salida y crea n+1 frames**, así que cada
hueco desplazaba un frame todo lo posterior. La k-ésima capa de una pista quedaba k frames tarde respecto a su
transición, y esos k frames caían fuera del rango. Arreglo: `blank(n-1)`. Además hay una sola transición `qtblend`
por pista con keyframes por capa. (La misma resta de 1 se aplicó al POC multipista original, que tenía el desfase.)

**Pruebas (todas verdes tras el arreglo):**
- `test_text.py`: 25 de 25 (render, ajuste, glifos, `.srt` con BOM/CRLF/latin-1, errores).
- `test_engine.py`: 10 de 10. Compara **cada frame** contra el mismo timeline sin overlays: ningún destello, ningún
  residuo tras terminar una capa, y los cues aparecen y desaparecen **en el frame exacto**. Se comprobó que
  la prueba detecta el bug: con el arreglo revertido fallan 8 de 10.
- `test_mcp.py`: 67 de 67 vía cliente MCP real (texto con acentos que realmente aparece abajo y no arriba,
  7 rechazos de `add_text`, 7 de `add_subtitles`, 3 de `add_image`, capas solapadas, la 7.ª capa simultánea se
  rechaza, un corte que deja subtítulos fuera de rango se acepta con advertencia, 100 cues, export con subtítulos).
- **Modelo real + tus clips:** dos corridas (título amarillo con acentos, 3 subtítulos incluida una línea doble,
  imagen "REC"). Sin errores. En el MP4 final se verificaron los **156 frames**: luma mínima 72, ningún destello, y
  el subtítulo está exactamente en los frames 46-47 y ausente en el 48.

**Limitaciones (honestas):**
- Las capas se colocan en **segundos de la línea de tiempo** y **no se mueven** si luego se editan clips anteriores;
  lo que quede fuera del final se oculta/recorta con una advertencia en `get_timeline` (no rechaza el corte).
- Una sola fuente y estilo (negrita sin cursiva), sin animaciones de texto salvo fade (0.15 s por defecto en
  `add_text`, 0 en subtítulos); sin alineación izquierda/derecha, sin estilos por palabra. Solo escritura latina.
- Imágenes y PiP hacen un fade de borde de 6 frames por diseño (una insignia "todo el video" también entra y sale
  suave, como notó el modelo).
- Texto muy largo se rechaza en vez de truncarse. Máx. 300 cues por llamada y 6 capas simultáneas.
- Rendimiento: contact sheet de 6 frames con 100 cues ≈ 1.3 s (720p). No se midió a 1080p vertical con muchas capas
  ni se repitió la medición de CPU/RAM de las secciones 8 y 9 con texto.
- El MP4 se verificó por frames y luma, no a oído ni en un reproductor real. `-ss` de ffmpeg puede mostrar el frame
  vecino al muestrear tiempos (lo confundí una vez); la verificación exacta usa el índice de frame.

## 12. Estabilidad y escalabilidad: mediciones (añadido tras la pregunta del usuario)

Medido en el motor (`live.py`), 640x360, 4 cores, sin GPU, clips cortos repetidos (solo 2 archivos fuente distintos).

| Prueba | Resultado |
|---|---|
| Construir timeline vs. nº de entradas (3 s c/u, sin fundidos) | 10: 143 ms · 50: 415 ms · 200: 1.7 s · 500 (25 min): 4.4 s (~9 ms/entrada, lineal) |
| Con un fundido cruzado entre cada par | 50: 510 ms · 200: 2.2 s |
| Ver un frame de un timeline ya construido | 19-91 ms, casi independiente de la longitud |
| 300 subtítulos sobre 10 min (200 entradas) | construir 4.8 s (~3 s más que sin subtítulos); 1 sola pista de capas |
| 300 x (construir + renderizar un frame) en un mismo proceso | RSS 233 -> 242 MB (+10 MB); tiempo por llamada 146 ms (primeras 10) -> 125 ms (últimas 10); sin fugas visibles |

**Conclusión:** estable para un usuario y videos cortos/medios dentro de lo probado. Cada herramienta de render
**reconstruye el timeline completo** (sin caché), de modo que el costo crece lineal con el tamaño del proyecto.

**Límites conocidos / no probados:** export bloqueante sin progreso ni cancelación (sec. 9 y 11); un proceso = un proyecto
y las llamadas se serializan, sin pruebas de concurrencia ni de múltiples usuarios; solo CPU; las capas usan tiempo
absoluto; la caché de PNG de texto no se limpia; no se probó una sesión de horas, la caída a mitad de un export, 4K, ni
cientos de archivos fuente distintos.

**Si hiciera falta escalar:** (1) cachear el tractor construido entre llamadas e invalidarlo al editar; (2) export en segundo
plano con progreso y cancelación; (3) un proceso/worker por proyecto; (4) proxies de baja resolución para vista previa;
(5) Movit/GPU para composición; (6) límite/limpieza de la caché de texto.

### 11.3 Estilo "lujo": tipografía y recursos gráficos (añadido a petición del usuario)

**Estilos de texto** (`add_text`, `add_subtitles`; `list_styles` los describe): `luxury` (Playfair Display, dorado metálico con
degradado, sombra suave y halo oscuro fino, ornamento de filete con rombo; **por defecto en títulos**), `luxury-italic`,
`champagne` (Cormorant Garamond, marfil, caja de cristal con filete dorado; **por defecto en subtítulos**), `noir` (Cinzel,
capitales con mucho espaciado), `modern` (Montserrat en mayúsculas, muy espaciado) y `classic` (el aspecto original). Las
fuentes son OFL y van empaquetadas en `fonts/` con sus licencias (6 archivos, ~3 MB). Opciones nuevas: `style`, `uppercase`,
`ornament` (none/line/diamond) y `color` opcional (vacío = color propio del estilo; un color explícito reemplaza el degradado).
Las seis fuentes cubren acentos, ñ, ¿¡ y comillas tipográficas.

**Recursos gráficos** dibujados por programa (sin imágenes binarias): `add_graphic` con `frame` (doble filete dorado con rombos en
esquinas y puntos medios), `letterbox` (barras de cine con hilo dorado) y `vignette`; y `add_lower_third` (panel de cristal con
barra dorada lateral, título en dorado y subtítulo en capitales marfil espaciadas, alineable a izquierda/derecha). Total: 21 herramientas.

**Verificación visual:** antes de integrar, galería de los 6 estilos sobre fotogramas reales (uno claro y uno nocturno). Eso mostró
que el dorado perdía legibilidad sobre fondo claro (se añadió un halo oscuro fino) y que el ornamento ensuciaría cada subtítulo
(se desactiva por defecto en subtítulos).

**Bug encontrado por las pruebas:** con un `color` explícito, el código confundía la tupla RGB con los tramos del degradado
(`TypeError`); cualquier `color="#ffdd00"` con el estilo por defecto habría fallado. Corregido, con prueba de regresión.

**Pruebas (todas verdes):** `test_text.py` 70/70 (estilos, acentos, ajuste, color, espaciado, gráficos, tercio inferior y sus
rechazos), `test_engine.py` 13/13 (cuadro por cuadro; incluye pila de lujo, barras de cine, tercios inferiores consecutivos),
`test_mcp.py` 93/93 (cada gráfico verificado en pantalla: viñeta oscurece esquinas, barras arriba, marco en bordes sin tocar el
centro, tercio inferior solo abajo; rechazos de estilo/ornamento/color/amount/kind; pila de lujo exportada). Los umbrales se
recalibraron dos veces por errores de la prueba, no del código (el estilo por defecto es más fino que el blanco grueso anterior).
**Modelo real + tus clips:** pieza completa (viñeta, marco, título, tercio inferior, subtítulos) sin errores de herramientas; el
modelo ajustó por sí mismo el tamaño del título y de los subtítulos tras ver que partían en dos líneas y tapaban al sujeto. En el
MP4 final, 156 frames: sin destellos (luma mínima 16 en el inicio del fade-in).

**Limitaciones:** solo estos 6 estilos (sin fuente propia); color de los gráficos fijo (dorado); estilos de capitales espaciadas
(`noir`, `modern`) rechazan frases largas que `luxury`/`champagne` sí aceptan; el tamaño por defecto (0.06) parte "Gran Inauguración"
en dos líneas en vertical, conviene 0.04-0.05; el halo ayuda pero el texto claro sobre fondos muy brillantes puede seguir cansando.
No se ha evaluado el resultado con una persona más allá de la inspección de fotogramas.

## 13. Pruebas en 4K (añadido a petición del usuario)

**Material:** no se consiguió 4K real. El clip 4K del Drive del usuario es privado (la descarga devuelve una página de inicio de
sesión), Wikimedia bloquea con 429 y las demás fuentes abiertas alcanzables eran 1080p. Se generaron con ffmpeg tres clips 4K con ruido
temporal de sensor (para que decodificar sea exigente como con cámara): H.264 4K30 63 Mbps, H.264 4K60 94 Mbps y HEVC Main10 4K30 40 Mbps
(`media_4k/`, ignorado por git). **Esto no sustituye a metraje de cámara real.**

**Piso de la CPU (solo ffmpeg, sin MLT, 4 núcleos):** 65.5 / 51.6 / 81.5 fps decodificando esos tres clips, usando 3.1-3.5 núcleos.

**Prueba:** timeline 3840x2160@30 de 10.4 s (312 frames), 3 clips (uno a 60 fps) con 2 fundidos cruzados, fades y la pila de lujo
completa (viñeta, marco, título, tercio inferior, subtítulos), a través del servidor MCP real (`legacy/bench_4k.py`).

| Herramienta (servidor, antes de la optimización) | Tiempo | RAM pico | CPU (de 400%) |
|---|---|---|---|
| Edición (validar y guardar) | 0-60 ms (0.29 s el tercio inferior, valida el ajuste a 4K) | n/d | n/d |
| `get_still` 1080p, frío / caliente | 5.9 / 5.1 s | 0.7-0.8 GB | 132-139% |
| `get_still` 4K completo, frío / caliente | 12.4 / 8.7 s | 1.3-1.4 GB | 87-99% |
| `get_contact_sheet` 6 frames, frío / caliente | 12.1 / 10.3 s | 1.8 GB | 214-222% |
| `render_preview` (medio tamaño) | 43.4 s | 3.2 GB | 186% |
| `export` borrador | 73.4 s (7.1x la duración) | 3.1 GB | 165% |
| `export` alta calidad | 92.6 s (8.9x) | 4.8 GB | 332% |

Los exports verificados: 3840x2160, 10.4 s, 312 frames, sin destellos; texto a 100% nítido. El tiempo de "caliente" casi no baja
respecto al de "frío", así que el costo no está en generar los PNG de texto sino en reconstruir y decodificar/componer 4K.

**Investigación (con hipótesis descartadas):**
1. *"La tubería de video crudo hacia ffmpeg es el cuello de botella."* **Descartada:** codificar directo dentro de MLT tardó 65.0 s contra
   73.4 s por la tubería (-11%), con la misma CPU (161%) y menos RAM (2.1 vs 3.1 GB).
2. *"Las capas cuestan."* **Confirmada.** El mismo timeline **sin capas** exporta en 21.0 s (307% de CPU); con las 5 capas, 65 s
   (161%). Coste por capa full-frame a 4K (base 21.0 s): viñeta +27.3 s y marco +24.9 s (ambas durante todo el video), título +5.8 s
   (2.6 s en pantalla), tercio inferior +6.3 s (2.5 s), subtítulos +11.4 s (rango de 8.5 s). Son ~2.4 s de render por cada segundo
   que una capa full-frame está activa. Cada capa es una imagen RGBA 4K que `qtblend` compone y la composición es secuencial (por eso
   la CPU cae del 307% al 161%).
3. *"El render en serie desperdicia núcleos."* **Confirmada y corregida:** el consumer de MLT con `real_time=-N` renderiza N frames en
   paralelo. Con N=2 el export con las 5 capas baja de 65.0 a **35.6 s** (1.8x); con N=4 a 33.7 s pero con 4.1 GB en vez de 2.9 GB.
   Resultado **idéntico** al serial: 0 de 312 frames con luma distinta (>3) y sin destellos.

**Cambio aplicado:** `live.render()` usa 2 hilos por defecto (`MLT_RENDER_THREADS`, 1 = serie). Por el camino real del servidor (MLT
compone, tubería, ffmpeg codifica) el export 4K borrador pasó de **67.5 s a 36.2 s** (1.87x; CPU 176% -> 323%; RAM 2.0 -> 2.9 GB +
0.5 GB de ffmpeg). Las tres suites siguen verdes tras el cambio (70 / 13 / 93); el export de 7 s a 720p pasó de 2.5 s a 2.0 s.
También se corrigió que el servidor dejaba procesos `Xvfb` huérfanos (ahora mueren con él: `PR_SET_PDEATHSIG`, demostrado con un
`SIGKILL`) y un error de mi primer muestreador de memoria (observaba otro proceso); esas cifras se descartaron y se repitió la medición.

**Qué sigue siendo un problema en 4K:**
- Ver un solo frame tarda 5-15 s: no es interactivo. Con 4K haría falta trabajar sobre proxies de baja resolución.
- El export 4K con capas sigue siendo ~3.5x más lento que el video (36 s para 10.4 s) y usa hasta ~3.4 GB; el de alta calidad
  usó 4.8 GB antes del cambio (no se repitió con 2 hilos).
- Las capas de decoración que duran todo el video (viñeta y marco) son las más caras. Ideas **sin probar**: fusionarlas en una sola
  imagen, recortar los PNG de texto a su caja en vez de componer el cuadro completo, o aplanar capas estáticas antes de componer.
- El servidor sigue bloqueado mientras exporta (36-93 s a 4K). **No se verificó** si un cliente MCP real aguanta llamadas de ese
  tamaño sin agotar su tiempo de espera; tampoco se probó un modelo real a 4K.
- Una sola corrida por medición, material sintético, una máquina de 4 núcleos sin GPU.

### 13.1 Comparación con 1080p (añadido tras la pregunta del usuario)

Misma prueba y mismos pasos que en 4K, pero a **1920x1080@30** (clips generados: H.264 16 Mbps, H.264 60 fps 24 Mbps, HEVC 10 bits
10 Mbps, también con ruido de sensor), a través del servidor MCP con la optimización de 2 hilos ya aplicada. 312 frames, misma pila de
cinco capas. Exports verificados: 1920x1080, 10.4 s, 312 frames, sin destellos.

| Medida | 1080p | 4K |
|---|---|---|
| `get_still` a medio tamaño, frío / caliente | 1.6 / 1.3 s | 5.9 / 5.1 s |
| `get_still` a tamaño completo, frío / caliente | 2.6 / 1.9 s | 12.4 / 8.7 s |
| `get_contact_sheet` 6 frames, frío / caliente | 2.9 / 2.9 s | 12.1 / 10.3 s |
| `export` borrador (servidor, 2 hilos) | **9.6 s** (0.9x la duración) | ~36 s (3.5x) |
| `export` alta calidad | 21.0 s (2.0x) | 92.6 s (8.9x, antes de los 2 hilos) |
| `render_preview` | 7.8 s | 43.4 s (antes de los 2 hilos) |
| RAM pico export borrador / alta | 1.15 / 1.9 GB | 3.1 / 4.8 GB (antes de los 2 hilos) |

Las filas de 4K marcadas "antes de los 2 hilos" se midieron antes de la optimización; el borrador a 4K con 2 hilos se midió aparte
(36.2 s). Por eso esas columnas no son estrictamente comparables, y el 4K con 2 hilos tampoco se repitió por el servidor completo.

**Render paralelo a 1080p** (export borrador por el camino del servidor): 1 hilo 15.7 s, **2 hilos 8.5 s (1.85x)**, 4 hilos 9.3 s (peor
que 2 por contención). Dos hilos es el valor correcto en ambas resoluciones.

**Reproducción en tiempo real** (reproductor `sdl2` bajo Xvfb con audio ficticio; frames descartados de 312):
| Caso | Descartados |
|---|---|
| 1080p, sin capas | **0** (0%) |
| 1080p, 5 capas, `real_time=1` (3 corridas) | 80 / 68 / 77 (~24%) |
| 1080p, 5 capas, `real_time=2` (3 corridas) | 26 / 32 / 29 (~9%) |
| 1080p, 5 capas, `real_time=3` (3 corridas) | 23 / 15 / 14 (~5%) |
| 4K, 5 capas, `real_time=1` (1 corrida) | 265 (85%), tarda 18.2 s en reproducir 10.4 s |

Conclusión: **sin capas, 1080p es perfectamente fluido; las capas de lujo son lo que rompe la fluidez** (igual que en el export). Varios
hilos de reproducción reducen el descarte unas 4 veces a 1080p pero no lo eliminan (~5% sigue siendo un tirón perceptible); a 4K no
alcanza. `poc.py preview` ahora usa 3 hilos (`MLT_PLAY_THREADS`). Limitaciones: Xvfb renderiza por software y sin GPU real los números
pueden diferir; la sincronía A/V no se evaluó; una corrida por caso a 4K y tres a 1080p.

## 14. Revisión completa del código (añadido a petición del usuario)

Se leyeron íntegros los ~3.800 líneas del proyecto (`live.py`, `server.py`, `textrender.py`, `graphics.py`, `legacy/poc.py`, las tres suites, los
scripts de benchmark y demo, las plantillas HTML y la configuración). Cada sospecha se **comprobó con un experimento** antes de tocar nada;
análisis estático con `pyflakes` (sin nombres indefinidos), `node --check` sobre el JavaScript de ambas plantillas, `bash -n` y validación
de los JSON de configuración.

**Defectos reales encontrados, con su evidencia, y corregidos:**
| # | Defecto | Evidencia | Arreglo |
|---|---|---|---|
| 1 | Deriva por redondeo: el modelo calculaba en segundos y MLT redondea cada clip a frames | 40 clips de 0.1 s: el modelo decía 100 frames y MLT construía 80 (capas desplazadas 20 frames); 0.3 s x40: 300 vs 320 | el modelo trabaja en una **cuadrícula de frames exacta**; `build()` afirma que modelo y MLT coinciden |
| 2 | Duraciones menores a un frame aceptadas | clip de 0.01 s se convertía en el **clip completo** (275 frames en vez de 125); un fundido de 0.01 s daba **400 frames** en vez de 275 | se rechazan con mensaje claro (clip, corte y fundido) |
| 3 | Fade con entrada + salida = duración total | keyframes repetidos (`88=1;88=1`): el brillo caía de 127 a 102 en pleno video | keyframes deduplicados y ordenados |
| 4 | `render()` no comprobaba a ffmpeg | si ffmpeg no podía escribir, `render()` "terminaba bien" | lanza `RuntimeError` con el mensaje de ffmpeg, no deja FIFO, ffmpeg ni archivo a medias |
| 5 | Caché de texto inútil | el PNG se renderizaba **antes** de mirar si existía: 998 ms en frío y 273 ms "con caché" a 4K | se mira primero: 0.1 ms con caché |
| 6 | `layout()` caro | 1586 ms con 300 subtítulos (se llama varias veces por edición): cargaba la fuente en cada llamada | fuentes y validación de texto memorizadas: ~0 ms en caliente |
| 7 | `export` podía sobrescribir cualquier archivo que eligiera el modelo | `ffmpeg -y` sin preguntar | rechaza archivos existentes salvo `overwrite=true`, exige `.mp4`/`.mov` y rechaza directorios |
| 8 | `import_clip` aceptaba una imagen como "video" | un `.jpg` entraba como clip de 0.04 s | se rechaza y apunta a `add_image`; también duración ilegible |
| 9 | `add_subtitles` con tiempos no numéricos | error genérico de Python | mensaje claro |
| 10 | Un clon nuevo no podía correr las pruebas | `poc.py gen` no creaba `clip_c.mp4` (lo generé a mano) | `gen` lo crea |
| 11 | Benchmark rotulaba "1080p"/"4K" fijo | etiquetas equivocadas al correr el otro tamaño | etiquetas neutras |
| 12 | Limpieza | imports sin uso, una variable muerta, `Xvfb` ausente daba un error crudo | eliminados / mensaje claro |

**Sospechas descartadas (comprobadas, no eran defectos):** un clip **sin audio** con fundido cruzado hacia uno con audio exporta bien
(pista de audio presente, -91 dB en el tramo mudo, fade correcto); lo mismo un clip sin audio solo.

**Una limitación de MLT descubierta:** un timeline construido **solo se puede renderizar una vez** (un segundo render del mismo tractor no
emite frames, aun reposicionando). No afecta al servidor, que reconstruye siempre, y un timeline nuevo tras un render fallido funciona;
queda documentado en `render()`.

**Pruebas tras la revisión:** `test_text.py` 74, `test_engine.py` 31 (18 nuevas de regresión: sub-frame, deriva con 5 patrones de
duración, fade, fallo de ffmpeg y limpieza, caché, rendimiento, clip sin audio), `test_mcp.py` 101 (8 nuevas: sobrescritura, extensión,
directorio, imagen, subtítulos no numéricos, fundido sub-frame). Todas pasan; la demo se regeneró de punta a punta.

**Lo que la revisión NO cubre / sigue abierto:** el servidor es síncrono y un export bloquea el bucle de eventos (no se puede cancelar);
no hay bloqueo de archivo del proyecto entre procesos; `export` acepta cualquier ruta de escritura del usuario que lo ejecuta (sin
sandbox); `import_clip`, `add_image` y `srt_path` leen cualquier archivo local; las pruebas usan clips sintéticos y 3 clips reales;
el audio se verifica por niveles, no a oído.

## 15. Optimización del coste de las capas (añadido a petición del usuario)

Idea: las capas de lujo eran lo que más costaba (secciones 13 y 13.1). Se implementaron dos optimizaciones, cada una con su interruptor
(`MLT_OPT_CROP`, `MLT_OPT_MERGE`; por defecto activas, `0` las apaga) y se midieron por separado y juntas sobre el mismo timeline
(`out/opt_*`, export borrador por el camino del servidor con 2 hilos, un run por celda):

- **Recorte (`crop`)**: el PNG de texto, subtítulos y tercio inferior se recorta a la caja de sus píxeles visibles y `qtblend` compone solo ese
  rectángulo (1:1) en vez del cuadro completo. Si la caja ocupa >60% del cuadro no se recorta.
- **Fusión (`merge`)**: la decoración estática que se muestra **exactamente al mismo tiempo** (viñeta + marco) se pre-compone en una sola imagen,
  así que una sola transición la dibuja. No se fusionan capas con tiempos distintos ni tercios inferiores.

| Export borrador (10.4 s) | 4K | 1080p |
|---|---|---|
| Piso: mismo timeline **sin capas** | 22.1 s | 5.7 s |
| Sin optimizar | 40.3 s (3.9x) | 9.6 s (0.92x) |
| Solo recorte | 31.5 s (-22%) | 8.1 s (-16%) |
| Solo fusión | 31.3 s (-22%) | 8.2 s (-15%) |
| **Recorte + fusión** | **27.6 s (-32%, 2.7x)** | **7.4 s (-23%, 0.71x)** |

Coste que añaden las capas sobre el piso: 4K **18.2 s -> 5.5 s** (-70%); 1080p **3.9 s -> 1.7 s** (-56%).

**Fidelidad:** el recorte es **idéntico al píxel** (diferencia máxima 0 en todos los frames comparados y PSNR infinito en los exports 4K).
La fusión **no es idéntica**: PSNR medio 41-42.6 dB (mínimo 36.6-37.7 dB) y hasta 12/255 en un canal, solo en frames de rampa de fade, porque
dos capas con opacidad animada por separado no suman exactamente igual que una capa fusionada con una sola opacidad. Es un compromiso entre exactitud
y velocidad; se deja activo por defecto pero se puede apagar con `MLT_OPT_MERGE=0`.

**Reproducción en tiempo real a 1080p con las 5 capas** (frames descartados de 312, 3 corridas, reproductor `sdl2` bajo Xvfb):
| | sin optimizar | optimizado |
|---|---|---|
| `real_time=1` | 64 / 65 / 62 (~20%) | 11 / 16 / 10 (~4%) |
| `real_time=3` | 10 / 12 / 20 (~4.5%) | 0 / 11 / 4 (~1.6%) |

**Efectos secundarios:** la memoria pico sube (4K: 3.3 -> 4.1 GB con ambas; 1080p: 0.9 -> 1.2 GB) y el 4K sigue sin ser interactivo (el piso sin capas
ya es 2.1x la duración). Se descubrió además un fallo latente: MLT **se cae (segfault) si las pistas del multitrack no son contiguas**; la
fusión dejaba una pista vacía y lo destapó. `build()` ahora renumera las pistas de forma contigua.

**Pruebas:** `test_engine.py` 38 (7 nuevas: recorte idéntico, fusión y fusión+recorte dentro de tolerancia, `_merge_decor` con tiempos iguales,
distintos y tercios inferiores, pistas contiguas), `test_text.py` 74, `test_mcp.py` 101: todas pasan con las optimizaciones activas.

**No se probó / limitaciones:** no se midió la reproducción en vivo a 4K tras la optimización; una corrida por celda en los exports; la fusión solo
cubre `frame`, `letterbox` y `vignette` con el mismo inicio, duración, opacidad y fade; las formas muy dispersas (como el marco, cuya caja es todo
el cuadro) no se benefician del recorte. Se podrían dividir en bandas, sin probar.

## 16. Prueba con un modelo real tras la revisión y las optimizaciones (eficiencia)

Dos corridas de `claude -p --mcp-config .mcp.json --output-format json` contra el servidor actual, con los clips reales del usuario (1080x1920),
midiendo con los datos que reporta Claude Code. Un modelo, una corrida por caso.

| | A: pedido preciso | B: pedido abierto |
|---|---|---|
| Pedido | R1+R2, fundido, fades, viñeta, marco, título, tercio inferior, 2 subtítulos, revisar una vez | los 3 clips, ~10 s, "acabado de lujo", el modelo decide cortes, estilos y textos |
| Tiempo total | 30.7 s (28.8 s según el cliente) | 33.5 s (31.7 s) |
| Turnos | 19 | 20 |
| Tokens de salida | 2.553 | 2.723 |
| Tokens de entrada procesados | 419.403 (94% de caché) | 419.449 (94%) |
| Costo | 0.212 USD | 0.206 USD |
| Errores de herramientas | ninguno | ninguno |
| Resultado verificado (ffprobe + cada frame) | 1080x1920, 6.5 s, 156 frames, sin destellos | 1080x1920, 9.8 s (3x3.8 - 2x0.8 = 9.8, exacto), 245 frames, sin destellos |

**Comportamiento del modelo:** en A reconoció en la hoja de contactos que los subtítulos tapaban el tercio inferior y los movió al centro por
su cuenta (no volvió a revisar el export final porque se le pidió revisar "una vez"). En B eligió estilos (`luxury` + `champagne`), inventó título y tres
subtítulos con acentos, acortó uno que dejaba una palabra suelta en la segunda línea y declaró lo que no verificó (audio, video exportado).

**Dónde se va el costo (medido):**
- El tiempo es casi todo del modelo: ~22 s de API de ~30 s; el servidor aporta ms por edición, ~0.3 s por hoja de contactos y 1-3 s por export.
- Cada turno procesa ~21.000 tokens de entrada (94% de caché) con ~135 de salida. Las **definiciones de las 21 herramientas pesan ~3.600 tokens por
  turno** (12.466 caracteres; la mayor es `add_text`, 1.815) y las **respuestas del servidor son pequeñas: ~3.000 tokens en total** en 13 pasos (cada
  edición devuelve el estado completo, 37 -> 1.828 caracteres, crece con el proyecto).
- Conclusión: el costo lo marca el **número de turnos** (cada llamada a una herramienta es un turno: ~12 llamadas -> 19-20 turnos), no el peso de las
  respuestas ni la velocidad del servidor.

**Mejoras candidatas (NO implementadas, solo medidas las premisas):** (1) una herramienta de lote `apply_ops(lista)` que ejecute varias ediciones de una
llamada: con la mitad de turnos el costo bajaría en proporción parecida (estimación, no medida); (2) respuestas de edición compactas (solo lo cambiado
y advertencias; hoy repiten `ops` completo); (3) acortar descripciones largas; (4) **avisar de choques en pantalla** (p. ej. subtítulos abajo + tercio inferior
a la vez): hoy solo se ven mirando la imagen.

**Limitaciones:** un modelo y una corrida por caso; el tiempo incluye el arranque del cliente; los números de tokens son los del cliente y dependen del
sistema base de Claude Code (~17K tokens por turno que no controla este servidor).

## 17. `apply_ops` y aviso de choques en pantalla (añadido a petición del usuario)

**`apply_ops(ops)`** (herramienta 22): aplica hasta 50 ediciones en **una sola llamada, todo o nada**. Cada elemento es `{"tool": "<herramienta>", ...sus
argumentos}` con los mismos nombres y argumentos que las herramientas individuales (todas comparten ahora los mismos constructores de operación, así
que un lote se comporta exactamente como las mismas llamadas una a una). Cada elemento se valida en orden contra el timeline que dejan los anteriores;
si alguno falla no se aplica nada y el error nombra el elemento (`item 2 (cut_clip): ...; nothing was applied`). Permite: `add_clip`, `cut_clip`,
`crossfade`, `set_fades`, `add_pip`, `add_text`, `add_subtitles` (incluido `srt_path`), `add_graphic`, `add_lower_third`, `add_image`.

**Aviso de choques:** `layout()` calcula `warnings` cuando dos elementos de **ediciones distintas** están a la vez en pantalla en zonas que pueden
solaparse (texto, subtítulos, tercio inferior, imagen, PiP). Es una heurística a partir de caracteres, tamaño y posición, no una medición; se avisa
también si están a menos de 1.5% del cuadro; se ignoran solapes de <0.1 s, la decoración de cuadro completo (marco, viñeta, barras) y las capas de una
misma edición; máximo 4 avisos + un resumen. Llega en la respuesta de la edición que lo causa y en `get_timeline`. Ejemplo real (el choque de la
corrida A, vertical 1080x1920): `graphic lower_third and subtitle '¿Quién trae el balón?' may overlap or touch on screen from 3.4s to 5.2s; move one
(position) or change its timing`.

**Medición con un modelo real** (mismo pedido que la corrida A de la sección 16, una corrida cada una):
| | A (sin `apply_ops`) | A2 (con `apply_ops` y avisos) | cambio |
|---|---|---|---|
| Turnos | 19 | **11** | -42% |
| Tiempo total | 28.8 s | **22.0 s** | -24% |
| Tokens de entrada procesados | 419.403 | 277.161 | -34% |
| Tokens de salida | 2.553 | 1.895 | -26% |
| Costo | 0.212 USD | **0.152 USD** | -28% |

El modelo usó `apply_ops`, detectó el choque (subtítulos abajo + tercio inferior), movió los subtítulos al centro y afirmó que el editor "ya no marca
solapamientos". Resultado verificado: 1080x1920, 6.5 s, sin destellos. El ahorro es menor que la mitad de turnos que se estimó porque cada
herramienta nueva añade descripción (que se reenvía cada turno) y el modelo sigue gastando turnos en importar, revisar y exportar.

**Pruebas:** `test_mcp.py` 121 (el lote reproduce el mismo proyecto que 9 llamadas sueltas, atomicidad, 8 rechazos, `.srt` en lote, dependencia entre
elementos, avisos en la respuesta de la edición y en `get_timeline`), `test_text.py` 85 (11 casos puros de choques: caso real, centro, otro
momento, decoración, textos en el mismo lugar, solape <0.1 s, PiP, mismas ediciones, tope), `test_engine.py` 38.

**Limitaciones:** una corrida por celda y un solo modelo; el aviso es heurístico (puede avisar de más o de menos, sobre todo con textos muy largos o con
tipografías anchas); no mide el solape real renderizado; `apply_ops` no incluye `import_clip`, `new_project`, `undo` ni `remove_op` (siguen siendo
llamadas aparte).

## 18. Eficiencia, robustez y seguridad (objetivo: "mejora la eficiencia sin sacrificar calidad; código robusto y seguro")

Método: perfilar primero (`cProfile` de un build 4K en caliente: 2.11 s, de los cuales `new_Producer` = 2.11 s en 8 llamadas y `_crop_to_content`
0.43 s, de ellos 0.37 s en `PIL.convert`), cambiar solo lo que el perfil señala, medir antes/después y volver a pasar las tres suites.

### 18.1 Eficiencia (medido en un proyecto 4K 3840x2160@30 con viñeta + marco + título + tercio inferior + subtítulos)
| Cambio | Antes | Después |
|---|---|---|
| `get_still` repetido (reutiliza el timeline construido) | 2.81 s (media de 3) | **0.46 s** (media de 3) → ~6x |
| Build a resolución de exportación con el recorte ya calculado en un `.json` junto al PNG | 1.46 s | **1.10 s** (-0.36 s) |
| Respuesta de cada edición (sin eco de `ops`) | listado completo | solo `op_count`; `get_timeline` conserva el listado |
| Caché de PNG (texto/gráficos/recortes) | crecía sin límite | poda al arrancar: >14 días o por encima de 500 MB |
| 30 `get_still` seguidos a 720p (prueba MCP) | 87 ms c/u | 72 ms c/u |

- La caché guarda como máximo 2 timelines (cuadros a 0.5x y hojas de contacto a 0.25x se alternan). La clave incluye la lista de ediciones, el formato
  y `ruta+mtime+tamaño` de cada fuente e imagen: editar, deshacer o reemplazar un archivo en disco la invalida.
  No se usa para `render_preview`/`export` (un timeline ya renderizado no se puede renderizar de nuevo).
- La hoja de contactos a 4K sigue limitada por decodificar 6 cuadros sueltos del H.264 de 4K (~2.7 s); la caché solo ahorra la construcción.
- El sidecar de recorte solo se confía si es más nuevo que el PNG, y un sidecar corrupto se ignora y se regenera.

### 18.2 Robustez y seguridad
| Riesgo | Antes | Ahora |
|---|---|---|
| Dos procesos editando el mismo proyecto | última escritura gana (ediciones perdidas) | `flock` exclusivo alrededor de leer-modificar-guardar; `fsync` antes del `os.replace`; temporal con pid |
| `project.json` corrupto | traceback sin contexto | error claro ("unreadable"); `new_project` lo repara |
| Rutas arbitrarias (leer fuentes/imágenes/.srt, escribir export) | cualquier ruta | opcional `MLT_EDITOR_ROOTS` (realpath: enlaces simbólicos y `..` no escapan) en las 4 vías de entrada/salida |
| FIFO del render | `<salida>.nut`, nombre predecible | directorio privado `mkdtemp` (0700), borrado siempre |
| `ffprobe`/`ffmpeg` colgado | bloqueaba el servidor | `timeout` de 60 s con error claro |
| NaN / ±inf en tiempos | pasaban las comparaciones (`not 0 <= x`) y envenenaban la rejilla | rechazados con mensaje, también dentro de cues anidadas |
| Sin topes | cantidad ilimitada | 500 ediciones, 50 fuentes, 1000 capas, `new_project` ≤ 7680x4320 y fps 1-120, id de fuente ≤ 32 caracteres |

Pruebas nuevas: `test_mcp.py` 121 → **148** (respuestas compactas; cuadro en caché == cuadro sin caché, con y sin texto; edición y reemplazo de archivo
invalidan; cinco intentos de escape de la valla; dos procesos × 20 ediciones sin perder ninguna; NaN/inf/texto donde va un número; archivo corrupto;
poda; timeout de `ffprobe`; sin directorios FIFO residuales), `test_engine.py` 38 → **49** (sidecar: se crea, evita decodificar, se invalida, `null`,
corrupto; NaN/inf; tope de capas), `test_text.py` 85 sin cambios. `pyflakes` limpio.

**Calidad sin regresión (verificado):** una exportación 720p con crossfade, fades, viñeta, marco, título de lujo, tercio inferior y PiP se renderizó
con el código anterior (commit `6e2e4c5`) y con el nuevo: los 719 hashes de paquete de `ffmpeg -f framemd5` (vídeo + audio) son **idénticos**.
Los cuadros de la caché son idénticos byte a byte a los de un servidor sin caché.

**No verificado / límites:** la valla de rutas es opcional y está desactivada por defecto; no hay límite de memoria ni cancelación de `export`
(sigue siendo síncrono); el `flock` protege procesos en el mismo equipo (no un NFS sin soporte de bloqueo); la caché de timelines mantiene abiertos los
decodificadores de hasta 2 timelines: a 4K el proceso llegó a ~1.07 GB de RSS con la caché y ~0.73 GB sin ella (+~340 MB, medido alternando
cuadros a 0.5x y hojas a 0.25x; `MLT_TRACTOR_CACHE=0` la desactiva); el tope de 8000 px de imágenes ya existía y es lo que
protege de bombas de descompresión (se lee solo la cabecera); no se repitió la prueba con un modelo real tras estos cambios.

## 19. Plantillas de diseño, animación y biblioteca de audio (añadido a petición del usuario)

**Qué hay.** Siete plantillas (`set_template`): lujo (la original, píxel a píxel idéntica; `golden.py` 15/15), corporativa, académica, boceto, tech/neón, minimal e infantil.
Cada una define paleta, tipografías (OFL, `fonts/`), estilos de texto, tercio inferior, etiqueta (`add_callout`), marcos, fondos de tarjeta e icono con placa.
Lo que no nombra estilo propio (`style`/`theme` = `auto`) sigue a la plantilla, así que cambiarla reestiliza toda la edición. `add_card` genera tarjetas
(título, sección, cita, lista, cifra, cierre) como fuente de vídeo. `add_image(icon=…)` pone iconos Lucide (95, ISC) o garabatos propios, teñidos y con placa.
`add_text`, `add_lower_third`, `add_image` y `add_pip` aceptan `anim` (entradas/salidas, easings, keyframes libres, rotación/escala; `anim.py`) y `animate` lo añade después.

**Audio.** Op `audio` en el motor (hasta 8 pistas, fades en dB, bucle, ducking manual o automático con los segmentos de habla). Biblioteca: 64 piezas
(27 de música, 37 efectos; CC0 y CC-BY) con licencia leída en la fuente (`assets/manifest.json`, `assets/LICENSES.md`). El audio vive en el bucket R2
(`assets/music|sfx/…`) y se baja bajo demanda con verificación SHA-256 (`assets_lib.py`; `fetch_assets.py` para precargar). `list_assets(kind=music|sfx, theme, mood, license)`
y `add_audio(asset=id)`. Lo CC-BY sale en `credits_required` de `get_timeline` y `export` escribe `<vídeo>.credits.txt`.

**Medido / verificado.** 64/64 piezas subidas a R2 y descargadas de vuelta con hash correcto; `test_assets.py` 30, `test_mcp.py` 244, `test_engine.py` 166, `test_text.py` 225,
`test_anim.py` 53 en verde. Siete demos de 14 s (`tools/make_demos.py`, 720p draft) en `editados/templates/<plantilla>.mp4` con su `.credits.txt`; revisadas por láminas de contacto.
Hallazgos: el motor rechaza audio de menos de 0.1 s, así que se descartaron 9 efectos de Kenney demasiado cortos; un reemplazo mío borró `add_callout` y `test_mcp` lo detectó.

**No verificado.** La música se eligió por metadatos (género, instrumentos, duración) y nadie la ha escuchado para juzgar si encaja; los demos solo se comprobaron
con `volumedetect` (hay señal, de −37 a −33 dB de media) y por imagen; la normalización a −16 LUFS de piezas CC-BY cuenta como modificación (se conserva la atribución);
el autor de las piezas de OpenGameArt se dedujo de la página con una heurística; los iconos de la lámina a 720p son pequeños y las etiquetas (`callout`) se leen justas.

## 20. Segunda tanda de plantillas, `wipe` y `bento` (añadido a petición del usuario)

**Referencias.** El enlace compartido (`share.google/…`) redirige a **typeui.sh/design-skills**, que está tras un checkpoint anti-bot de Vercel (HTTP 429): no se pudo leer directo.
El mismo catálogo está en el repo público `bergside/awesome-design-skills` (MIT): 67 skills con un `DESIGN.md` cada uno (paleta, tipografías, una línea de intención), que sí se leyeron.
Son sistemas de diseño *web*: unos 30 traen la paleta genérica por defecto y no aportan; se usaron solo como inspiración. Paletas, formas y código son propios; las fuentes son OFL de google/fonts
(las 14 nuevas cubren `áéíóúüñ¿¡€`, comprobado con PIL, así que no hizo falta ningún sustituto).

**Plantillas nuevas (8, ya son 15):** neobrutalism, terracotta, cinema, terminal, arcade (texto pixel sin suavizado, `fontmode="1"`), riso (desregistro de dos tintas), saas, glass.
**glass solo está imitado:** panel translúcido teñido; el overlay es un PNG y no desenfoca el video de debajo.
**Motor:** animación `wipe` (revelado/borrado izquierda→derecha con el filtro `qtcrop` de MLT, rect animado; no disponible en pip). **Tarjetas:** layout `bento` (1–4 teselas `cifra|etiqueta`) con el pintor de paneles de cada plantilla y fondos nuevos
(brutal, grain, black, scanlines, dither, halftone, blobs). **Audio:** +19 piezas (14 de música de incompetech CC-BY elegidas por catálogo, 5 impactos Kenney CC0) y las existentes etiquetadas para las plantillas nuevas: 83 piezas en total,
todas con al menos 2 de música y 3 efectos por plantilla; las 19 nuevas están en R2 y se bajaron de vuelta con hash correcto.

**Medido / verificado.** `wipe`: en un test de píxeles el borde izquierdo no se mueve y el derecho crece a la mitad a mitad de la entrada (y al revés en la salida). Suites en verde: `test_anim` 60, `test_engine` 212, `test_text` 353, `test_mcp` 245, `test_assets` 30, `golden` 15/15.
Hallazgos: `ImageDraw` sobre un RGBA opaco reemplaza el alfa (las teselas de `bento` se pintan en su propia capa); el umbral de alfa del test de callouts (70) era demasiado alto para glass (translúcido), ahora 40.
Ocho demos de 14 s en R2 (`editados/templates/<plantilla>.mp4` + `.credits.txt`), revisados por láminas de contacto.

**No verificado.** La música se eligió por metadatos del catálogo (género, instrumentos, descripción) y nadie la ha escuchado; los demos solo se comprobaron con `volumedetect` e imagen;
`wipe` no se probó en 4K ni encadenado con rotación; el pixel-art a 720p depende del tamaño (los pasos de la fuente no caen siempre en píxeles enteros); las tarjetas `bento` con textos largos se encogen (mínimo 55 %) y pueden quedar pequeñas.

## 21. Pulido: transiciones, movimiento por plantilla, tarjetas animadas, callouts y audio (añadido a petición del usuario)

**Auditoría previa (medida con `tools/qa_frames.py` sobre los demos de la sección 19/20).** Cada demo tenía 2 cortes secos (tarjeta→video→tarjeta, a los 3.0 s y 11.0 s) y más de 2 s de cuadros idénticos en cada tarjeta
(las tarjetas eran una imagen fija). MLT no trae máscaras de barrido (`/usr/share/mlt-7/lumas` no existe), pero `luma` acepta cualquier imagen.

**Qué se hizo.**
- **Transiciones (`transitions.py`):** `crossfade(style=…)` con 15 estilos: dissolve, wipe-right/left/up/down, iris-out/in, blinds-v/h, diagonal, clock (máscaras luma generadas con PIL, cacheadas) y slide-left/right/up/down
  (`composite` con geometría animada); `auto` = la de la plantilla si el movimiento está activo. `crossfade(sfx="auto")` pone además el efecto de la plantilla donde empieza la transición.
- **Movimiento por plantilla, opt-in** (`new_project(motion=True)` / `set_template(motion=…)`): cada plantilla trae animaciones propias para lower third, texto, imagen/icono y callout, más el preset nuevo `rise`; un `anim` explícito manda, `anim={}` = sin animación, los subtítulos nunca se animan.
- **Tarjetas animadas:** cada tarjeta se parte en capas por grupo (título, regla, subtítulo, filas, teselas) que entran escalonadas con su estilo; el último cuadro es la tarjeta estática (medido, comparado con el mismo codificador); `push` mueve solo el fondo.
- **Callouts:** `size` (0.7–2.0; 1 = idéntico al original), `anim` con `draw` (se despliega desde el aro), `pop`, `zoom`, `fade` escalando alrededor del aro (el aro nunca sale del punto, medido en píxeles); avisos de legibilidad (texto < 14 px, subtítulo de callout < 11 px al tamaño de exportación).
- **Audio:** los clips que no se solapan comparten pista (hasta 32 clips, 8 a la vez; antes 8 en total), `export(master="loudnorm")` y `loudness_lufs`/`true_peak_db` medidos en el archivo; con movimiento la música (biblioteca o ≥ 20 s) baja sola bajo la voz.

**Resultado medido (demos v2, `qa_frames`):** 0 cortes secos en los 15 demos y en el de Player (antes 2 por demo); 0 parpadeos; el tramo de cuadros idénticos al inicio desapareció en todos y quedan 3 de 15 con ~2 s de cierre idéntico al final.
Loudness de los 15 demos: entre −18.4 y −16.1 LUFS (normalizados a −16 en una pasada). Los `slide-*` mueven toda la imagen, por eso `qa_frames` los reporta como `fast_motion`, no como cortes.
Tests nuevos/ampliados: `test_transitions` 61, `test_cards_anim` 43, `test_engine` 270, `test_anim` 65, `test_mcp` 268, `test_assets` 38, `test_text` 353, golden 15/15.

**Hallazgos y correcciones.** `composite` sí desliza (error medio 0.9 contra el resultado esperado; una lectura mía de la lámina lo dio por aplastado); las máscaras no pueden llegar a 255 (queda un borde al final: `iris-in`); `playful` no tenía ningún efecto «whoosh» (la elección `auto` cae a pop/click/cualquiera,
con test para las 15 plantillas); los fondos `blobs` mostraban anillos por bandas de 8 bits (ahora con dither con semilla, determinista); la caché de tarjetas no cambiaba de clave al cambiar el código (versionada).

**No verificado.** Las transiciones se juzgaron por cuadros y medidas, no viéndolas en reproducción continua; la música sigue sin escucharse; 4K sin probar; sigue habiendo 3 demos con ~2 s de cierre idéntico y el último tramo de Player tiene ~0.2 s con solo fondo entre la última escena y la tarjeta final;
`slide-*` dentro de `Playlist.mix` se comprobó con clips sintéticos y en los demos, no con todos los formatos; las láminas B y C que te envié se hicieron antes del dither de `glass`/`saas` (el video final ya lo trae).

## 22. Modularización: todo son piezas desmontables (añadido a petición del usuario)

**Pedido:** «modulariza todo, no quiero rutas ni nombres ni nada hardcodeado, que todas sean piezas desmontables dentro del engine». Decisiones del usuario: paquetes de datos (`theme.json`) más plugins Python que se registran solos;
fachadas finas para que `live`, `server`, `cards`… sigan funcionando; el POC antiguo a `legacy/`; las `tools/` del repo leen sus datos de `data/*.json` y reciben argumentos. Diseño y cómo agregar cada pieza: `ARCHITECTURE.md`.

**Auditoría previa (sobre el código de la sección 21):** 37 ramas por nombre de plantilla (`th.name == "minimal"`…), unas 60 tablas indexadas por plantilla, 56 ramas por forma, un `if/elif` por tipo de operación en `layout()`/`build()` (archivo de 1100 líneas),
40 herramientas MCP en un solo archivo, rutas fijas en 15 módulos y URLs/textos fijos en `tools/`.

**Qué se hizo (una fase por commit, cada una con la misma batería de pruebas):**
- **R0 `snapshot.py`:** 686 hashes de píxeles y claves (lower third, callout, marco, 7 tarjetas, icono y textos de las 15 plantillas, máscaras de transición, keyframes de animación) tomados del código *anterior*. Se verificó 686/686 en cada fase; nunca se regeneró.
- **R1 `config.py` + `registry.py`:** toda ruta, límite e interruptor sale a `Settings` (`MLT_*` > archivo > defecto; secretos solo del entorno); un registro por tipo de pieza. El código quedó en el paquete `mltedit/`; los módulos de la raíz son fachadas (`sys.modules[__name__] = …`: el mismo módulo, no una copia).
- **R2 packs:** las 15 plantillas son `packs/themes/<n>/theme.json` (generados con un script desde los diccionarios que ya estaban probados), con sus estilos de texto y opciones. El tema por defecto es el que declara `"default": true`. Un pack roto se omite y se informa (`list_styles → problems`).
- **R3 formas:** 13 plugins de forma (`plugins/shapes`); las 37 ramas por nombre pasaron a opciones del tema (`lower_third.scrim`, `callout.flag`, `card.align`, `card.vertical_bar`, `card.list_role`…).
- **R4:** fondos y divisores de tarjeta, layouts de tarjeta (7), transiciones (15), presets (13) y easings (6) de animación y tipos de gráfico (4) son plugins; los presets llevan banderas (`reveal`, `callout`, `callout_only`, `on_video`, easings por defecto) en vez de que el motor pregunte por su nombre.
- **R5 operaciones:** `engine.layout/build` se partieron en 12 plugins de op y 5 de capa (`plugins/ops`, `plugins/layers`) más `core/` (contexto, timeline, caché de PNG, render). El estado global es un `EngineContext`; `live.W = …` sigue funcionando.
- **R6 herramientas:** `server.py` pasó de 1000 a 257 líneas (estado del proyecto + FastMCP); las 28 herramientas están en `tools/{project,timeline,overlays,audio,cards,review}.py` con `@tool`/`@builder`. Los docstrings ya no listan nombres a mano (`<<templates>>`, `<<transitions>>`…). Resumen, legibilidad, colocación, créditos y archivos de dependencia pasaron a hooks de los plugins.
- **R7 `tools/` y `legacy/`:** `curate_assets` (fuentes, picks, URLs, moods → `data/asset_sources.json`; `--sources/--assets-dir/--stage-dir`), `make_demos` (`--out/--footage/--data/--quality`, textos y tiempos en `data/demos/templates.json`), `player_demo` (escenas en `data/demos/player.json`, `--template/--home/--data`),
  `make_licenses` (`--manifest/--out/--notes`, salida idéntica byte a byte). `poc.py`, `bench_4k.py`, `build_demo.py`, `demo_session.py`, `stress_1080p.py` → `legacy/` con README.
- **R8 `test_modularity.py`** (24 comprobaciones, cada escenario en un proceso nuevo con su propio entorno): agregar una plantilla copiando un pack (disponible por MCP, con sus colores); quitar una (error que la nombra y lista las demás); pack roto y JSON inválido (se omiten, se informan); carpeta de plugins externa con una transición, una forma,
  un preset, un easing y un layout de tarjeta (los docstrings de las herramientas ya listan la transición nueva); un plugin que lanza excepción; quitar una transición y una op; y *greps* de que no queda ningún nombre de plantilla, ruta absoluta, rama por forma, rama por tipo de op/capa ni nombre de preset/easing/transición escrito en el motor.

**Resultado medido (sobre el código final):** snapshot 686/686, golden 15/15, `test_modularity` 24, `test_engine` 270, `test_mcp` 268 (misma lista de herramientas), `test_text` 353, `test_anim` 65, `test_transitions` 61, `test_cards_anim` 43, `test_assets` 38, `test_annotate` 6, `test_transcribe` 12, `test_r2_upload` 5; `pyflakes` limpio.
Arranque del servidor (`import server`, mediana de 5): 0.50 s antes, 0.52 s ahora.

**Hallazgos durante el trabajo.** Los nombres de estilo de texto son globales: copiar un pack sin renombrarlos hace que el segundo se rechace (lo detecta el cargador y lo informa; está documentado en `ARCHITECTURE.md`); un test que copiaba `minimal` lo destapó.
Una versión intermedia de `summary()` agrupaba los callouts como subtítulos (ambos tenían la clave `sub`); lo cazó `test_mcp` y se resolvió con un hook `group()` de la capa.

**No verificado / límites.**
- No se regeneraron en este entorno los 15 demos ni el de Player con el footage real (`media_user/clip.mp4` y el repo `player` no están en este contenedor): `make_demos` se ejecutó con el clip de prueba (plantilla luxury, texto/tiempos de otro footage) y `player_demo` con capturas de relleno; ambos terminaron sin error ni avisos, pero **no se compararon cuadros con los videos ya publicados**. La garantía de «misma salida» es la del snapshot de componentes (686 hashes), no la de un video final completo.
- `curate_assets.py` no se volvió a ejecutar contra la red: se comprobó que las constantes cargadas de `data/asset_sources.json` son idénticas a las originales; los patrones de extracción de páginas (regex sobre el HTML de Kenney/OpenGameArt) siguen en el script porque describen el marcado de esos sitios.
- `legacy/*.py` solo se comprobó que importan y resuelven la raíz del repo (`poc.py` imprime su ayuda); no se corrieron sus benchmarks.
- Los tipos de revelado de animación (`wipe`, `draw`) se implementan en `anim.py` (`wipe_keys`/`draw_keys`) y un preset elige cuál usa: agregar un tercer *tipo de revelado* requiere código en `anim.py`; agregar presets que usen los existentes, no.
- Los nombres de op y de capa que `engine.py` conoce son solo los de los plugins registrados; las herramientas MCP siguen siendo una función por edición (agregar un op nuevo exige también su herramienta en `tools/`, si se quiere exponerlo con argumentos propios; `apply_ops` lo toma de su `@builder`).

## 23. Nivel producción: anclaje, validación determinista, proxies, visor y fiabilidad para agentes (añadido a petición del usuario)

**Pedido:** «llevar este proyecto a nivel producción: prioriza timeline anchoring, proxies/cache para preview fluido, validación determinista y edición confiable por agentes LLM». Decisiones del usuario: overlays anclados al clip por defecto (los proyectos viejos se migran como absolutos); «preview fluido» = stills/hoja de contactos rápidos para el agente y un visor en vivo para una persona. Plan por fases con criterios de éxito: P0–P7, un commit por fase, siempre con `snapshot.py` 686/686 y `golden.py` 15/15.

**Gaps encontrados al analizar el código:** ops referenciadas por posición (borrar o insertar desplazaba todo); sin revisiones ni journal (un reintento duplicaba ediciones, un cambio ajeno pasaba inadvertido); overlays y audio en segundos absolutos («do NOT move if you later edit earlier clips») y sin `trim`/`move` de clips; `layout()` dependía del disco (aspecto de imágenes) y de los defaults del código, errores sin código; preview siempre desde los originales (still en frío a 4K: 4.3 s; hoja de contactos 7.9 s; `render_preview` 20 s); sin `dry_run`, idempotencia, consulta puntual ni export en segundo plano.

**Resultados medidos (4 CPUs, esta máquina):**
| | antes | después |
|---|---|---|
| still tras una edición, 4K / 1080p | 4.31 s / 1.24 s | 0.37 s / 0.25 s |
| otro still de la misma edición, 4K / 1080p | 0.43 s / 0.15 s | 0.13 s / 0.09 s |
| hoja de contactos de 6, 4K / 1080p | 7.9 s / 2.2 s | 0.67 s / 0.57 s |
| `render_preview`, 4K / 1080p | 20.4 s / 5.0 s | 3.8 s / 1.4 s |
| visor: primer segmento listo | n/a | 1.2 s |
| visor: edición de 60 s a 540p, todos los segmentos | n/a | 14.0 s (4.3x tiempo real; 0.45 s por segmento de 2 s) |
| una edición validada con 200 clips + 50 textos (`layout` puro 1.7 ms) | n/d | 18 ms |
| esquema + docs de las herramientas | 28 herramientas, ~6.5k tokens | 42 herramientas, ~11.5k tokens (14.9k antes de acortar los docstrings) |
Un still repetido sale de la caché sin MLT. Los números de antes están en `tests_data/bench_baseline.json`, los de después en `tests_data/bench_p4.json` (`tools/bench_preview.py`).

**Qué se hizo.**
- **P1 modelo v2:** ids estables, clips por id, `revision`, `history.jsonl`, `expected_revision`, undo/redo por parches (un batch = un paso), `update_op`, `remove_op` por id; migración v1 determinista. `kill -9` justo antes del rename deja la revisión anterior entera (test con proceso real).
- **P2 validación determinista:** `EditError` con códigos (catálogo de 14) y línea JSON; ops normalizadas y con hechos congelados (`layout` no lee el disco); `layout_hash`; `verify_sources`/`refresh_source`. Hypothesis: 3 838 llamadas aleatorias (valores absurdos incluidos) sin que escape nada que no sea `EditError`, y encontró un bug real (`OverflowError` con `start=inf`).
- **P3 anclaje:** por defecto al fotograma de la fuente del clip en pantalla; `trim_clip`, `move_clip`, `move_op`, `remove_op(cascade|reanchor)`. Matriz de 6 operaciones × 7 tipos de overlay/audio comprobada contra el cuadro de la fuente, más píxeles reales (`test_anchoring`, 89 checks).
- **P4 proxies y caché:** ver tabla. El proxy conserva número de cuadros y fps (PSNR 42.4 dB contra el original reducido; en MLT, el cuadro correcto da 24.0 dB frente a 17.7 dB del siguiente).
- **P5 visor:** HLS con segmentos por hash, worker aparte, audio continuo; una edición dentro de un segmento re-renderiza exactamente ese (3 se reusan); sin clic en las uniones de audio (segunda diferencia 0.0002 = la del tono puro).
- **P6 agentes:** `dry_run` con diff, `request_id`, `query`, `describe_project`, `background=true` + `job_status`/`cancel_job`/`list_jobs`; 30 escenarios de agente (11 al empezar, 6 en xfail).
- **P7:** logs JSON por llamada, `pyproject.toml` (`mltedit-server`), `ci.sh` (4 min 39 s, todo en verde).
- **QA automático del render** (a raíz de una crítica externa que el usuario compartió): `export` y `render_preview` devuelven `qa` con cortes duros inesperados y destellos de un cuadro (`mltedit/qa.py`).

**Evaluación con un modelo real** (`tools/agent_eval.py`, `claude -p` con el servidor como única herramienta; `tests_data/agent_eval.json`): 14 corridas, 4 tareas, versión anterior (commit a13a39e) frente a la nueva. **Todas correctas en ambas versiones y 0 errores de herramienta en ambas.** Donde sí hay diferencia: «acortar el clip A de un proyecto ya hecho manteniendo lo anclado a B» necesita 2 llamadas / 3 turnos en la nueva y 6–7 llamadas / 7–8 turnos en la anterior (hay que borrar y recolocar a mano). En las otras 3 tareas el número de turnos es igual y el costo no mejora (más esquema de herramientas); el costo de una corrida depende sobre todo del estado de la caché de prompts, así que **no** lo presento como diferencia. Una corrida piloto mostró un error del propio modelo (leyó los ids de un batch desplazados una posición, lo deshizo y lo rehízo): por eso `apply_ops` ahora responde `made: [{item, tool, op_id}]`.

**No cumplido / no verificado — dicho claramente.**
- El criterio «menos errores por tarea que la base» **no se pudo demostrar**: ambas versiones tuvieron 0 errores en estas tareas (cortas, con clips sintéticos). Solo se midió el ahorro de llamadas en la tarea de editar un proyecto terminado.
- n muy pequeño (1–2 corridas por tarea, un solo modelo, clips de prueba); no hay clips reales de cámara en este contenedor.
- **Reproducción real en un navegador:** el Chromium disponible no tiene H.264, así que no se comprobó que el video avance en un navegador. Sí se comprobó, en Chromium, que la página lista las ediciones con sus ids, dibuja el timeline y se actualiza sola, y que `ffmpeg` reproduce el HLS completo (video + audio) sin errores, con exactamente los cuadros del timeline. Safari/Chrome/Firefox reales: no probados.
- `export` en segundo plano informa estado y resultado, **no porcentaje de progreso**.
- Sin probar: 4K con el visor, sesiones de 20+ clips con el modelo, el costo de un `still` tras editar con 200 clips (la validación sí: 18 ms), varios proyectos a la vez en un mismo proceso, instalación del paquete en una máquina limpia (se comprobó que el paquete construido importa y registra sus herramientas).
- El anclaje fija el INICIO: la duración de un overlay no se adapta si el clip cambia de largo (un marco que cubría todo el video no cubre más tras alargar el timeline).
- El QA de render revisa el archivo exportado/preview, no los stills; no mide sincronía A/V ni color.
- No hay planner declarativo («quiero un video de 30 s con esta estructura»): `apply_ops` + `dry_run` + `describe_project` cubren el bucle plan–verificación, pero el LLM sigue decidiendo cada op.
- Sin cambios (decisión del usuario, no del agente): las 15 plantillas, la biblioteca de audio y los iconos siguen; el reporte externo los considera excesivos para un POC.

## 10. Archivos

- `legacy/poc.py`: el POC (gen/build/bench/preview/export/measure).
- `POC_MODE=multi` en `legacy/poc.py`: multipista con composición (sección 9; requiere `xvfb-run`).
- `POC_MODE=real` en `legacy/poc.py`: timeline con clips reales de `media_real/` (sección 8).
- `legacy/bench_4k.py`: benchmark 4K a través del servidor MCP (sección 13).
- `graphics.py`, `fonts/`: gráficos y fuentes OFL (sección 11.3; tras la sección 22 viven en `mltedit/` y los archivos de la raíz son fachadas).
- `textrender.py`, `test_text.py`, `test_engine.py`: texto/subtítulos y pruebas cuadro a cuadro (sección 11.2).
- `server.py`, `test_mcp.py`, `setup.sh`, `requirements.txt`, `mcp.example.json`: servidor MCP y su prueba (sección 11).
- `live.py`, `viewer_template.html`: motor declarativo y visor de la sesión en vivo.
- `stress_1080p.py`: prueba extra de estrés 1080p (secuencial vs. seek aleatorio).
- `themes.py`, `themed.py`, `sketch.py`, `cards.py`, `icons.py`, `anim.py`, `assets_lib.py`, `fetch_assets.py`, `tools/` (curaduría, licencias, demos), `assets/`: plantillas, animación y biblioteca de audio (sección 19).
- `transitions.py`, `tools/qa_frames.py`, `test_transitions.py`, `test_cards_anim.py`: transiciones, QA de movimiento y sus pruebas (sección 21).
- `media/`, `out/`: clips y resultados generados (ignorados por git; se regeneran con `gen` y `export`).
- `mltedit/`, `ARCHITECTURE.md`, `data/`, `test_modularity.py`, `snapshot.py`, `legacy/`: la modularización de la sección 22.
- `ci.sh`, `test_project_v2.py`, `test_determinism.py`, `test_anchoring.py`, `test_proxy.py`, `test_viewer.py`, `test_qa.py`, `tools/agent_scenarios.py`, `tools/agent_eval.py`, `tools/bench_preview.py`, `mltedit/{project,media,preview,viewer}/`, `jobs.py`, `qa.py`, `errors.py`, `log.py`: la sección 23.
