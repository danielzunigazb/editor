"""EngineContext: the mutable state one timeline is laid out and built with (frame size, frame rate, sources, cache folder, template,
motion flag). The engine keeps one shared instance, CTX; `live.W = 1920` and friends still work because the engine module exposes these
fields as properties of CTX (see engine.py)."""
import os

from ..config import S


class EngineContext:
    FIELDS = ("W", "H", "FPS", "CLIPS", "CLIP_LEN", "CACHE", "MOTION", "THEME")

    def __init__(self, W=640, H=360, FPS=25, clips=None, clip_len=None, cache=None, motion=False, theme=None):
        from .. import themes
        self.W, self.H, self.FPS = W, H, FPS
        self.CLIPS = dict(S.demo_clips) if clips is None else clips          # source name -> media path
        self.CLIP_LEN = {} if clip_len is None else clip_len                  # source name -> length in seconds
        self.CACHE = os.path.join(S.live_dir, "cache") if cache is None else cache   # rendered overlay PNGs
        self.MOTION = motion                                                  # template default animations / transitions on
        self.THEME = themes.get(theme)                                        # active template

    def fr(self, s):
        """Seconds -> whole frames at this context's frame rate."""
        return int(round(s * self.FPS))
