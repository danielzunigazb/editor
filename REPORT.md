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
- **Contenido sintético y pequeño** (testsrc2, barras, mandelbrot). Video real (cámara,
  4K, HEVC, 10-bit, VFR, múltiples pistas con compositing pesado) no se probó. El único
  contenido "difícil" es el 1080p mandelbrot de ~8 s.
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
   serialización a XML lista, reproducción muy holgada de CPU, y exportación desacoplable
   a ffmpeg tal como querías (funcionó vía NUT/FIFO, 7.000 s exactos). Es el mismo motor
   de Shotcut y Kdenlive, así que está probado en producción.
2. **Condición clave: encapsular MLT detrás de nuestra propia capa** (y por tanto detrás
   del futuro MCP). Esa capa debe: usar siempre el `loader`, fijar `in/out` de los
   filtros, hacer explícitas las unidades, y **validar el resultado renderizando y
   comprobando** (frames/niveles), porque MLT falla en silencio.
3. **Resolver el scrubbing** antes de prometer UX de editor: proxies (GOP corto/intra) o
   caché de frames. Sin eso los seeks aleatorios en 1080p rondan 130 ms.
4. **Resolver el despliegue de Python:** el binding de apt solo sirve con el Python
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

## 7. Archivos

- `poc.py`: el POC (gen/build/bench/preview/export/measure).
- `stress_1080p.py`: prueba extra de estrés 1080p (secuencial vs. seek aleatorio).
- `media/`, `out/`: clips y resultados generados (ignorados por git; se regeneran con `gen` y `export`).
