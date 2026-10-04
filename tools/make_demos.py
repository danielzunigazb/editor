#!/usr/bin/env python3
"""One 14 s demo per template (intro card, footage with lower third / icon / label / text, outro card, music + effects), draft quality.
Usage: tools/make_demos.py [out_dir] [template ...] (all 15 templates). Footage: media_user/clip.mp4 if present, else media/clip_a.mp4.
Audio comes from the asset library (needs the cache or R2_WORKER_URL + R2_UPLOAD_TOKEN)."""
import os, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
OUT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(HERE, "out", "templates")
FOOT = next(p for p in (os.path.join(HERE, "media_user", "clip.mp4"), os.path.join(HERE, "media", "clip_a.mp4")) if os.path.exists(p))

TEXT = {   # template -> (card title, subtitle, lower-third name, role, icon, label, caption, music id, sfx whoosh, sfx accent, footage start)
    "luxury": ("Hotel Aurelia", "Una estancia a la altura del cielo", "Valeria Montoya", "Directora de Experiencias", "star", "Vista panorámica", "Donde el tiempo se detiene", "m-trio-for-piano-cello-and-clarinet", "s-interface-sounds-maximize-003", "s-interface-sounds-glass-004", 5),
    "corporate": ("Informe anual 2026", "Resultados y próximos pasos", "Andrés Quiñones", "Director Financiero", "trending-up", "Nueva sede", "Crecimiento del 18 % interanual", "m-wallpaper", "s-interface-sounds-open-001", "s-interface-sounds-confirmation-001", 12),
    "academic": ("Ciudades y territorio", "Seminario de geografía urbana", "Dra. Inés Paredes", "Universidad de los Andes", "book-open", "Caso de estudio", "«La ciudad es un texto que se lee caminando»", "m-reawakening", "s-interface-sounds-scroll-001", "s-interface-sounds-glass-001", 20),
    "sketch": ("¡Mi viaje en dron!", "Apuntes de campo", "Lucía y Tomás", "Cuaderno de ruta", "doodle-star", "¡Mira esto!", "Dibujado a mano alzada", "m-sergio-s-magic-dustbin", "s-interface-sounds-scroll-001", "s-interface-sounds-pluck-001", 28),
    "tech": ("NEXUS // Vuelo 01", "Telemetría en vivo", "Unidad A-07", "Operador de dron", "cpu", "Objetivo detectado", "Señal estable · 5G", "m-delightful-d", "s-digital-audio-powerup5", "s-interface-sounds-question-002", 36),
    "minimal": ("Altura", "Una mirada tranquila", "Marta Ríos", "Fotografía", "map-pin", "Mirador", "Menos, pero mejor", "m-reminiscing", "s-interface-sounds-open-001", "s-interface-sounds-close-001", 44),
    "neobrutalism": ("¡Vuelo directo!", "Sin filtros, sin adornos", "Ana Beltrán", "Jefa de Operaciones", "zap", "Sin rodeos", "Directo al punto", "m-shaving-mirror", "s-interface-sounds-open-001", "s-interface-sounds-toggle-001", 8),
    "terracotta": ("Tierra y barro", "Un viaje por talleres artesanos", "Rosa Quintana", "Ceramista", "leaf", "Taller", "Hecho a mano, cocido al sol", "m-morning", "s-interface-sounds-scroll-001", "s-interface-sounds-glass-001", 16),
    "cinema": ("EL VUELO", "Una historia desde el cielo", "Mateo Vidal", "Dirección", "play", "Escena 01", "Nada volverá a ser igual", "m-undaunted", "s-interface-sounds-maximize-003", "s-impact-sounds-impactmetal-heavy-000", 24),
    "terminal": ("player --demo", "Telemetría en la terminal", "dev@nexus", "Ingeniero de sistemas", "terminal", "Objetivo fijado", "build ok · 0 errores", "m-exit-the-premises", "s-digital-audio-phaserup1", "s-interface-sounds-question-002", 32),
    "arcade": ("¡NIVEL 1!", "Prepárate para jugar", "Jugador 1", "Piloto de dron", "trophy", "¡Bonus!", "Insertar moneda para continuar", "m-bit-quest", "s-digital-audio-phaserup1", "s-rpg-audio-handlecoins", 40),
    "riso": ("Zine de vuelo", "Impreso en dos tintas", "Nora y Leo", "Editores", "music", "Detalle", "Tinta rosa, tinta azul", "m-funin-and-sunin", "s-interface-sounds-scroll-001", "s-interface-sounds-pluck-001", 48),
    "saas": ("Panel de control", "Métricas en tiempo real", "Carla Núñez", "Product Manager", "chart-bar", "Nueva región", "Despliegue estable · 99,98 %", "m-pleasant-porridge", "s-interface-sounds-close-001", "s-interface-sounds-confirmation-001", 56),
    "glass": ("Aurora", "Un diseño translúcido", "Iván Soto", "Diseñador", "sparkles", "Mirador", "Claridad en cada capa", "m-floating-cities", "s-interface-sounds-maximize-003", "s-interface-sounds-glass-004", 3),
    "playful": ("¡Vamos a volar!", "Aventura en el aire", "Capitán Sol", "Piloto de nubes", "party-popper", "¡Guau!", "¡Qué día tan divertido!", "m-carefree", "s-interface-sounds-select-005", "s-digital-audio-powerup5", 52),
}


def build(theme):
    home = os.path.join(OUT, "_home", theme)
    os.environ["MLT_EDITOR_HOME"] = home
    import importlib, server
    importlib.reload(server)
    title, sub, name, role, icon, label, caption, music, wh, acc, t0 = TEXT[theme]
    server.new_project(1280, 720, 25)
    server.set_template(theme)
    server.add_card("title", title, sub, dur_s=3.0, push=True)
    server.import_clip(FOOT, "A")
    server.add_clip("A", t0, t0 + 8)
    if theme in ("saas", "neobrutalism"):                                            # show the bento layout in two of the demos
        server.add_card("bento", "En cifras", items=["18 %|crecimiento", "4,2 M|usuarios", "99,9 %|disponibilidad", "12|países"], dur_s=3.0)
    else:
        server.add_card("outro", "Gracias por ver", sub, dur_s=3.0)
    typed = {"in": "wipe", "out": "wipe"} if theme in ("terminal", "arcade") else None            # typed reveal for the two terminal-like looks
    server.add_lower_third(name, role, start_s=3.4, dur_s=3.6, anim=typed or {"in": "slide-left", "out": "slide-left"})
    server.add_image(4.0, 4.0, icon=icon, position="top-right", scale=0.11, anim={"in": "pop", "out": "zoom"})
    server.add_callout(label, [[6.0, 0.55, 0.55], [9.0, 0.5, 0.52]], start_s=6.0, dur_s=3.0)
    server.add_text(caption, 8.8, 2.0, position="bottom", anim=typed or {"in": "fade", "out": "fade"})
    server.add_audio(0.0, 14.0, asset=music, volume_db=-16, fade_in_s=1.0, fade_out_s=2.0)
    server.add_audio(2.9, asset=wh, volume_db=-8)
    server.add_audio(0.9, asset=acc, volume_db=-8)
    server.add_audio(11.2, asset=acc, volume_db=-8)
    out = os.path.join(OUT, f"{theme}.mp4")
    r = server.export(out, quality="draft", overwrite=True)
    st = server.get_timeline()
    r["credits_required"] = st.get("credits_required", [])
    return r


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for t in (sys.argv[2:] or list(TEXT)):
        r = build(t)
        print(t, r["duration_s"], f"{r['size_kb']} KB", f"{r['render_s']}s", "credits:", len(r["credits_required"]), flush=True)
