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
completa (viñeta, marco, título, tercio inferior, subtítulos), a través del servidor MCP real (`bench_4k.py`).

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

Se leyeron íntegros los ~3.800 líneas del proyecto (`live.py`, `server.py`, `textrender.py`, `graphics.py`, `poc.py`, las tres suites, los
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

## 10. Archivos

- `poc.py`: el POC (gen/build/bench/preview/export/measure).
- `POC_MODE=multi` en `poc.py`: multipista con composición (sección 9; requiere `xvfb-run`).
- `POC_MODE=real` en `poc.py`: timeline con clips reales de `media_real/` (sección 8).
- `bench_4k.py`: benchmark 4K a través del servidor MCP (sección 13).
- `graphics.py`, `fonts/`: recursos de lujo y fuentes OFL (sección 11.3).
- `textrender.py`, `test_text.py`, `test_engine.py`: texto/subtítulos y pruebas cuadro a cuadro (sección 11.2).
- `server.py`, `test_mcp.py`, `setup.sh`, `requirements.txt`, `mcp.example.json`: servidor MCP y su prueba (sección 11).
- `live.py`, `viewer_template.html`: motor declarativo y visor de la sesión en vivo.
- `stress_1080p.py`: prueba extra de estrés 1080p (secuencial vs. seek aleatorio).
- `media/`, `out/`: clips y resultados generados (ignorados por git; se regeneran con `gen` y `export`).
