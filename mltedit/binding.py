"""Point the engine at a project: its sources (or their proxies for a small preview), resolution, cache folder, template and motion flag.
Used by the server and by the preview worker process (which must not import the server: it owns stdout)."""
import os

from . import engine as live
from . import themes
from .media import proxy as proxies


def bind(st, home, scale=1.0):
    """scale < 1 => a preview: proxies are read where they are ready, and the frame is smaller."""
    live.CLIPS = proxies.media_for_preview(home, st["sources"], scale)
    live.CLIP_LEN = {k: v["duration_s"] for k, v in st["sources"].items()}
    live.W = max(2, int(st["width"] * scale) // 2 * 2)
    live.H = max(2, int(st["height"] * scale) // 2 * 2)
    live.FPS = st["fps"]
    live.CACHE = os.path.join(home, "cache")
    live.THEME = themes.get(st.get("theme"))              # projects saved before templates existed have no theme: luxury, as always
    live.MOTION = bool(st.get("motion"))                  # opt-in: projects saved before it existed behave exactly as they did
