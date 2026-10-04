#!/usr/bin/env python3
"""Demo video for the 'player' repo (Android music player) built with this editor: luxury template (gold on black, like the app's own
design), the repo's real screenshots with callouts on the controls, music from the asset library.
Usage: tools/player_demo.py <player_repo_dir> <out.mp4> [draft|high]. Read-only on the repo. v2: motion on (transitions, animated cards, callout animations, loudnorm)."""
import os, subprocess, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
REPO, OUT = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
QUALITY = sys.argv[3] if len(sys.argv) > 3 else "high"
IMG = os.path.join(REPO, "player-showcase", "public", "img")
HOME = os.path.join(os.path.dirname(OUT), "_player_home")
os.environ["MLT_EDITOR_HOME"] = HOME
import server  # noqa: E402

W, H, FPS = 1920, 1080, 30
SCENE, T0, X = 6.2, 4.0, 0.8                     # scene length; where the scenes start on the timeline; transition length
os.makedirs(HOME, exist_ok=True)
BG = os.path.join(HOME, "bg.mp4")
from PIL import Image, ImageOps  # noqa: E402
glow = ImageOps.colorize(Image.radial_gradient("L").resize((W, H), Image.BICUBIC), black=(76, 62, 40), white=(18, 15, 11))   # warm centre, dark edges
glow.save(os.path.join(HOME, "bg.png"))
subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", str(FPS), "-i", os.path.join(HOME, "bg.png"), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                "-t", "41", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", BG], check=True)


def crop(name, y0, y1):
    """Enlarged part of a screenshot (full width, rows y0..y1 as fractions) saved next to the project; returns (path, aspect = h / w)."""
    im = Image.open(os.path.join(IMG, name)).convert("RGBA")
    c = im.crop((0, int(im.height * y0), im.width, int(im.height * y1)))
    p = os.path.join(HOME, f"crop_{name[:-4]}_{int(y0 * 100)}_{int(y1 * 100)}.png")
    c.save(p)
    return p, c.height / c.width


PH = 0.20                                            # phone width as a fraction of the frame
PW, PHH = PH, PH * W * 2580 / 1200 / H               # its height as a fraction of the frame height
CX, CY = 0.5, 0.59


def pt(px, py, cx=CX, cy=CY, w=PW, h=PHH):
    """Frame point (0-1) from a point inside the picture (0-1), for a picture of fractional size w x h centred at cx, cy."""
    return [round(cx - w / 2 + px * w, 4), round(cy - h / 2 + py * h, 4)]


CW = 0.36                                            # width of an enlarged crop, as a fraction of the frame
SCENES = [   # headline, (file, y0, y1) crop or None, picture width, [(label, sub, point inside the WHOLE screenshot)]
    ("Tu biblioteca, a tu manera", ("dz_home.png", 0.0, 0.5), CW, [("Aleatorio", "toda la biblioteca de un toque", (0.19, 0.275)), ("Búsqueda", "sin importar mayúsculas ni acentos", (0.68, 0.035)), ("Recién añadidas", "listas automáticas", (0.33, 0.38))]),
    ("Reproduce sin ataduras", ("dz_now_playing.png", 0.45, 1.0), CW, [("Controles completos", "aleatorio, repetir, siguiente…", (0.8, 0.82)), ("Temporizador", "por minutos o al terminar la canción", (0.33, 0.953)), ("Ecualizador", "presets y refuerzo de graves", (0.67, 0.953))]),
    ("Letras sincronizadas", ("lyrics_synced.png", 0.1, 0.7), CW, [("Línea actual", "toca una línea para saltar", (0.3, 0.36)), ("Compartir", "mantén presionada una línea", (0.4, 0.555)), ("De dónde salen", "archivo .lrc, incrustadas o LRCLIB", (0.3, 0.12))]),
    ("Una terminal para tu música", ("terminal.png", 0.0, 0.5), CW, [("play soda stereo", "artista, álbum o canción", (0.4, 0.17)), ("now", "progreso y línea de la letra", (0.4, 0.31)), ("share lyric", "comparte una letra", (0.4, 0.405))]),
    ("Monitor web, cifrado de punta a punta", ("monitor.png", 0.0, 1.0), 0.56, [("Vinculado", "con el código del teléfono", (0.17, 0.147)), ("Volumen", "el del teléfono", (0.45, 0.57)), ("Cola", "y búsqueda en tu biblioteca", (0.67, 0.22))]),
    ("Compártelo en tus historias", None, None, []),
]

server.new_project(W, H, FPS, motion=True)          # the template's own motion: animated cards, transitions, overlay animations, ducking-ready music
server.set_template("luxury")
server.add_card("title", "Player", "Tu música, en tu bolsillo", dur_s=T0, push=True)
server.import_clip(BG, "BG")
server.add_clip("BG", 0.0, 39.0)                    # on screen from T0 - X (the intro card dissolves into it) to T0 - X + 39
server.add_card("list", "Y además", items=["Android Auto y búsqueda por voz", "Widget de pantalla de inicio", "Temas: sistema, claro, oscuro y AMOLED", "Español e inglés", "Tus datos se quedan en el teléfono"], dur_s=5.0)
server.add_card("outro", "Player 1.6.1", "player.danzuniga.xyz  ·  Android 8.0 o superior", dur_s=4.0, push=True)
for first in (0, 1, 2):                             # card -> footage -> list card -> outro, each with the template's transition and its whoosh
    server.crossfade(first, X, "auto", sfx="auto")

server.add_graphic("frame", T0 - 0.4, SCENE * len(SCENES) + 0.4, opacity=0.7)
for k, (head, pic, cw, calls) in enumerate(SCENES):
    t = T0 + k * SCENE
    server.add_text(head, t + 0.15, SCENE - 0.3, position="top", size=0.065)                 # no anim given: luxury motion supplies it
    if pic:
        name, y0, y1 = pic
        path, asp = crop(name, y0, y1)
        cw = min(cw, 0.70 * H / (W * asp))                   # keep the picture under the headline
        h = cw * W * asp / H                                 # picture height as a fraction of the frame height
        cx, cy = 0.5, 0.6
        server.add_image(t, SCENE, path=path, at=[cx, cy], scale=cw, anim={"in": "slide-bottom", "out": "slide-bottom", "in_s": 0.6, "out_s": 0.5})
        for j, (label, sub, (px, py)) in enumerate(calls):
            edge = pic[0] != "monitor.png"                        # phone crops: the ring sits on the picture's edge at that row, so the flag falls on the empty background
            x, y = cx - cw / 2 + ((0.02 if px < 0.5 else 0.98) if edge else px) * cw, cy - h / 2 + (py - y0) / (y1 - y0) * h
            s0 = t + 1.2 + j * 1.5
            side = ("s" if (py - y0) / (y1 - y0) < 0.25 else "n") + ("w" if px < 0.5 else "e")   # flag towards the nearer edge, onto the empty background
            server.add_callout(label, [[s0, round(x, 4), round(y, 4)], [s0 + 2.2, round(x, 4), round(y, 4)]], subtitle=sub, start_s=s0, dur_s=2.2, side=side, size=1.3)
    else:
        for i, (f, cxx) in enumerate((("share_song.png", 0.38), ("share_lyrics.png", 0.62))):
            server.add_image(t + i * 0.25, SCENE - 0.25 - i * 0.25, path=os.path.join(IMG, f), at=[cxx, 0.6], scale=0.19, anim={"in": "pop", "out": "zoom"})
        for j, (label, sub, p) in enumerate((("Tarjetas 9:16", "para Instagram, Snapchat, WhatsApp…", [0.33, 0.4]), ("Carátula o letra", "elige hasta 4 líneas", [0.67, 0.4]))):
            s0 = t + 1.5 + j * 1.6
            server.add_callout(label, [[s0, p[0], p[1]], [s0 + 2.4, p[0], p[1]]], subtitle=sub, start_s=s0, dur_s=2.4, side="nw" if p[0] < 0.5 else "ne", size=1.3)

server.add_audio(0.0, None, asset="m-trio-for-piano-cello-and-clarinet", volume_db=-17)     # fades and ducking are the defaults with motion on
for k in (1, 3, 5):
    server.add_audio(T0 + k * SCENE + 0.1, asset="s-interface-sounds-minimize-003", volume_db=-12)

tl = server.get_timeline()
print("duration", tl["duration_s"], "warnings", tl["warnings"])
r = server.export(OUT, quality=QUALITY, overwrite=True, master="loudnorm")
print(r)
