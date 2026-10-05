"""Every path, limit and switch of the editor, in ONE place. Nothing else in the engine knows a path.

Resolution order for each setting (first found wins):
  1. environment variable MLT_<NAME> (upper case; the historical names MLT_EDITOR_HOME, MLT_EDITOR_ROOTS, MLT_ASSETS_* still work)
  2. the config file named by MLT_EDITOR_CONFIG (.toml or .json; keys = the setting names below, lower case)
  3. the default below (paths relative to the data root, which defaults to the folder that contains this package)
Values are read on every access, so a test or a long-lived server can change them without restarting.
Secrets (the R2 token) come ONLY from the environment: a config file is easy to commit by accident."""
import json, os, tempfile

PKG = os.path.dirname(os.path.abspath(__file__))

# name: (env vars, default, type). Paths given as relative strings are relative to data_root; lists of paths use os.pathsep in env vars.
SPEC = {
    "data_root":        (("MLT_DATA_ROOT",), os.path.dirname(PKG), "path"),
    "home":             (("MLT_EDITOR_HOME", "MLT_HOME"), "out/mcp", "path"),                  # project dir of the MCP server
    "live_dir":         (("MLT_LIVE_DIR",), "out/live", "path"),                             # state/preview of the live.py CLI
    "roots":            (("MLT_EDITOR_ROOTS", "MLT_ROOTS"), [], "paths"),                     # extra folders user files may be read/written (the project folder is always allowed); "*" = fence off
    "fonts_dirs":       (("MLT_FONTS_DIRS",), ["fonts"], "paths"),                           # searched in order for a style's font file
    "system_fonts":     (("MLT_SYSTEM_FONTS",), ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                                                  "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                                                  "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"], "paths"),
    "icons_dirs":       (("MLT_ICONS_DIRS",), ["assets/svg"], "paths"),
    "packs_dirs":       (("MLT_PACKS_DIRS",), [os.path.join(PKG, "packs", "themes")], "paths"),   # theme packs (one folder per theme)
    "plugin_dirs":      (("MLT_PLUGIN_DIRS",), [], "paths"),                                 # extra plugin modules (*.py), loaded after the built-in ones
    "assets_manifest":  (("MLT_ASSETS_MANIFEST",), "assets/manifest.json", "path"),
    "assets_cache":     (("MLT_ASSETS_CACHE",), "assets_cache", "path"),
    "viewer_template":  (("MLT_VIEWER_TEMPLATE",), "viewer_template.html", "path"),
    "tmp_dir":          (("MLT_TMP_DIR",), tempfile.gettempdir(), "path"),
    "demo_clips":       (("MLT_DEMO_CLIPS",), {"A": "media/clip_a.mp4", "B": "media/clip_b.mp4", "C": "media/clip_c.mp4"}, "pathmap"),
    "render_threads":   (("MLT_RENDER_THREADS",), 2, "int"),
    "opt_crop":         (("MLT_OPT_CROP",), True, "bool"),
    "opt_merge":        (("MLT_OPT_MERGE",), True, "bool"),
    "tractor_cache":    (("MLT_TRACTOR_CACHE",), True, "bool"),
    "subprocess_timeout": (("MLT_SUBPROCESS_TIMEOUT",), 60, "int"),
    "proxy_enabled":    (("MLT_PROXY",), True, "bool"),                                  # intra-only small copies of big sources for previews (stills, contact sheet, preview mp4)
    "proxy_height":     (("MLT_PROXY_HEIGHT",), 540, "int"),                              # sources taller than this get a proxy of this height
    "proxy_crf":        (("MLT_PROXY_CRF",), 23, "int"),
    "proxy_wait_s":     (("MLT_PROXY_WAIT_S",), 15.0, "float"),                           # a still/sheet waits up to this long for a PENDING proxy, so what you see does not depend on timing
    "proxy_workers":    (("MLT_PROXY_WORKERS",), 2, "int"),                               # proxies made at the same time in the background
    "proxy_cache_mb":   (("MLT_PROXY_CACHE_MB",), 4000, "int"),                           # the proxy folder is pruned (least recently used first) above this size
    "log":              (("MLT_LOG",), "json", "str"),                                           # "json" = one JSON line per tool call on stderr; "off" = silent
    "viewer_page":      (("MLT_VIEWER_PAGE",), os.path.join(PKG, "viewer", "page.html"), "path"),           # the live viewer's page
    "vendor_dir":       (("MLT_VENDOR_DIR",), "assets/vendor", "path"),                                    # third-party browser code served locally (hls.js, Apache-2.0)
    "block_max_s":      (("MLT_BLOCK_MAX_S",), 20.0, "float"),                            # an export/preview of a video longer than this always runs as a job (a blocking call that long outlasts most MCP clients' tool timeout)
    "job_wait_max_s":   (("MLT_JOB_WAIT_MAX_S",), 45.0, "float"),                       # the longest job_status(wait_s=...) holds the call
    "av_tolerance_ms":  (("MLT_AV_TOLERANCE_MS",), 60.0, "float"),                           # an export whose sound is further than this from the source's against the picture gets a QA finding
    "viewer_segment_s": (("MLT_VIEWER_SEGMENT_S",), 2.0, "float"),                       # length of a live-viewer segment (the unit that is re-rendered when an edit touches it)
    "viewer_height":    (("MLT_VIEWER_HEIGHT",), 540, "int"),                              # picture height of the live viewer (smaller projects play at their own size)
    "viewer_port":      (("MLT_VIEWER_PORT",), 0, "int"),                                  # 0 = any free port on 127.0.0.1
    "viewer_prefetch":  (("MLT_VIEWER_PREFETCH",), 3, "int"),                              # segments rendered ahead of the one being played
    "max_source_mb":    (("MLT_MAX_SOURCE_MB",), 4096, "int"),                            # an imported file may not be bigger than this
    "max_source_s":     (("MLT_MAX_SOURCE_S",), 10800, "int"),                            # ... nor longer (3 h)
    "max_source_dim":   (("MLT_MAX_SOURCE_DIM",), 8192, "int"),                           # ... nor wider or taller (a 100000x100000 'video' is a memory bomb, not footage)
    "render_timeout_s": (("MLT_RENDER_TIMEOUT_S",), 3600, "int"),                         # a background job running longer is killed and reported as LIMIT_EXCEEDED
    "project_quota_mb": (("MLT_PROJECT_QUOTA_MB",), 20000, "float"),                        # disk a project folder may use (renders, proxies, cache); the cache is pruned first
    "max_ops":          (("MLT_MAX_OPS",), 500, "int"),
    "max_sources":      (("MLT_MAX_SOURCES",), 50, "int"),
    "max_layer_tracks": (("MLT_MAX_LAYER_TRACKS",), 6, "int"),
    "max_audios":       (("MLT_MAX_AUDIOS",), 32, "int"),
    "max_audio_tracks": (("MLT_MAX_AUDIO_TRACKS",), 8, "int"),
    "max_layers":       (("MLT_MAX_LAYERS",), 1000, "int"),
    "project_defaults": (("MLT_PROJECT_DEFAULTS",), {"width": 1280, "height": 720, "fps": 25}, "json"),
    "xvfb_screen":      (("MLT_XVFB_SCREEN",), "1280x720x24", "str"),
    "default_transition": (("MLT_DEFAULT_TRANSITION",), "dissolve", "str"),                   # crossfade without a style
    "ornament_color":   (("MLT_ORNAMENT_COLOR",), "#d9b25a", "str"),                         # rule under/over text when its style names no orn_color
    "anim_default_preset": (("MLT_ANIM_DEFAULT_PRESET",), "fade", "str"),                    # in/out not given in an anim spec
    "anim_keys_ease":   (("MLT_ANIM_KEYS_EASE",), "inout", "str"),                            # easing between free keyframes
    "server_name":      (("MLT_SERVER_NAME",), "mlt-video-editor", "str"),
    "r2_url":           (("R2_WORKER_URL",), "", "secret"),                                   # env only
    "r2_token":         (("R2_UPLOAD_TOKEN",), "", "secret"),                                 # env only
    "r2_user_agent":    (("MLT_R2_USER_AGENT",), "mlt-poc-asset-fetch/1.0", "str"),
}
_FILE = {"path": None, "mtime": None, "data": {}}


def _file_values():
    p = os.environ.get("MLT_EDITOR_CONFIG")
    if not p:
        return {}
    try:
        mt = os.path.getmtime(p)
    except OSError:
        raise RuntimeError(f"MLT_EDITOR_CONFIG points at {p}, which does not exist")
    if (_FILE["path"], _FILE["mtime"]) != (p, mt):
        with open(p, "rb") as f:
            if p.endswith(".toml"):
                import tomllib
                data = tomllib.load(f)
            else:
                data = json.load(f)
        unknown = set(data) - set(SPEC)
        if unknown:
            raise RuntimeError(f"{p}: unknown setting(s) {sorted(unknown)}; known: {sorted(SPEC)}")
        if any(SPEC[k][2] == "secret" for k in data):
            raise RuntimeError(f"{p}: secrets (r2_url, r2_token) are read from the environment only")
        _FILE.update(path=p, mtime=mt, data=data)
    return _FILE["data"]


def _abs(v, root):
    v = os.path.expanduser(str(v))
    return v if os.path.isabs(v) else os.path.join(root, v)


def get(name):
    if name not in SPEC:
        raise KeyError(f"unknown setting '{name}'; known: {sorted(SPEC)}")
    envs, default, kind = SPEC[name]
    raw, from_env = None, False
    for e in envs:
        if os.environ.get(e) not in (None, ""):
            raw, from_env = os.environ[e], True
            break
    if raw is None and kind != "secret":
        raw = _file_values().get(name)
    if raw is None:
        raw = default
    root = get("data_root") if name != "data_root" else None
    if kind == "path":
        return os.path.abspath(_abs(raw, root)) if root else os.path.abspath(os.path.expanduser(str(raw)))
    if kind == "paths":
        items = raw.split(os.pathsep) if (from_env and isinstance(raw, str)) else list(raw)
        return [i if i == "*" else os.path.abspath(_abs(i, root)) for i in items if i]       # "*" is a switch (see roots), not a path
    if kind == "pathmap":
        m = json.loads(raw) if from_env else dict(raw)
        return {k: os.path.abspath(_abs(v, root)) for k, v in m.items()}
    if kind == "int":
        return int(raw)
    if kind == "float":
        return float(raw)
    if kind == "bool":
        return raw if isinstance(raw, bool) else str(raw).strip().lower() in ("1", "true", "yes", "on")
    if kind == "json":
        return json.loads(raw) if from_env else (json.loads(json.dumps(raw)))
    return str(raw)


class _Settings:
    """Attribute access to the settings: `from mltedit.config import S; S.home`."""
    def __getattr__(self, name):
        try:
            return get(name)
        except KeyError as e:
            raise AttributeError(str(e))

    def describe(self):
        return {k: ("<set>" if SPEC[k][2] == "secret" and get(k) else get(k)) for k in SPEC}


S = _Settings()
