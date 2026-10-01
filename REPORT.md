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

`poc.py` (un solo archivo, subcomandos `gen | build | bench | preview | export | measure`):

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

## 10. Archivos

- `poc.py`: el POC (gen/build/bench/preview/export/measure).
- `POC_MODE=multi` en `poc.py`: multipista con composición (sección 9; requiere `xvfb-run`).
- `POC_MODE=real` en `poc.py`: timeline con clips reales de `media_real/` (sección 8).
- `server.py`, `test_mcp.py`, `setup.sh`, `requirements.txt`, `mcp.example.json`: servidor MCP y su prueba (sección 11).
- `live.py`, `viewer_template.html`: motor declarativo y visor de la sesión en vivo.
- `stress_1080p.py`: prueba extra de estrés 1080p (secuencial vs. seek aleatorio).
- `media/`, `out/`: clips y resultados generados (ignorados por git; se regeneran con `gen` y `export`).
