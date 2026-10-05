"""Settings of the web application, all from MLT_APP_* environment variables (nothing is read from a file: this holds a token). The editor engine has its own
settings (MLT_*, see mltedit/config.py); the ones the application passes on to it are listed in `engine_env`."""
import json, os, secrets
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# USD per million tokens: (input, output, cache read, cache write 5 min). From Anthropic's published prices for the models the application is meant to run; override with
# MLT_APP_PRICES='{"model-id": [in, out, cache_read, cache_write]}'. A model that is not listed is priced at the highest rate here, so a spending cap errs on the safe side.
PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.50),
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.00),
    "claude-haiku-4-5": (1.0, 5.0, 0.10, 1.25),
    "claude-haiku-4-5-20251001": (1.0, 5.0, 0.10, 1.25),
}
UNKNOWN_MODEL_PRICE = (10.0, 50.0, 1.0, 12.5)


def _num(name, default, cast=float):
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return cast(raw)
    except ValueError:
        raise SystemExit(f"{name}={raw!r} is not a valid {cast.__name__}")


@dataclass
class AppSettings:
    data_dir: str = "data"
    token: str = ""
    host: str = "127.0.0.1"
    port: int = 8080
    model: str = "claude-sonnet-5-5"
    max_procs: int = 4                 # engine processes alive at once (one per open project); the least recently used idle one is stopped to make room
    idle_s: float = 600.0              # an engine process nobody called for this long is stopped (it restarts on the next call, the project is on disk)
    max_turns: int = 30                # model calls for one user message
    max_usd_message: float = 1.0       # spending cap for one user message
    max_usd_project: float = 10.0      # spending cap for all the messages of one project
    max_upload_mb: int = 2048
    tool_timeout_s: float = 600.0
    cache: bool = True                 # top-level prompt caching of the conversation (cuts the cost of the long tool list + history on every turn)
    code_root: str = HERE
    prices: dict = field(default_factory=dict)
    token_generated: bool = False

    def price(self, model):
        return tuple(self.prices.get(model) or PRICES.get(model) or UNKNOWN_MODEL_PRICE)

    @classmethod
    def from_env(cls):
        token = os.environ.get("MLT_APP_TOKEN", "")
        generated = False
        if not token:
            token, generated = secrets.token_urlsafe(24), True       # never run open: a fresh one each start, printed once by main
        prices = json.loads(os.environ["MLT_APP_PRICES"]) if os.environ.get("MLT_APP_PRICES") else {}
        return cls(data_dir=os.path.abspath(os.environ.get("MLT_APP_DATA", "data")), token=token, token_generated=generated,
                   host=os.environ.get("MLT_APP_HOST", "127.0.0.1"), port=_num("MLT_APP_PORT", 8080, int), model=os.environ.get("MLT_APP_MODEL", "claude-sonnet-5-5"),
                   max_procs=_num("MLT_APP_MAX_PROCS", 4, int), idle_s=_num("MLT_APP_IDLE_S", 600.0), max_turns=_num("MLT_APP_MAX_TURNS", 30, int),
                   max_usd_message=_num("MLT_APP_MAX_USD_MESSAGE", 1.0), max_usd_project=_num("MLT_APP_MAX_USD_PROJECT", 10.0),
                   max_upload_mb=_num("MLT_APP_MAX_UPLOAD_MB", 2048, int), tool_timeout_s=_num("MLT_APP_TOOL_TIMEOUT_S", 600.0),
                   cache=os.environ.get("MLT_APP_CACHE", "1") not in ("0", "false", "no"), prices=prices)

    def engine_env(self, project_dir):
        """The environment of an engine process: ours minus the application's own secrets, with the project folder as its home and its only allowed folder."""
        drop = ("ANTHROPIC_", "MLT_APP_", "MLT_EDITOR_HOME", "MLT_EDITOR_ROOTS", "MLT_ROOTS", "MLT_HOME")
        env = {k: v for k, v in os.environ.items() if not k.startswith(drop)}
        env.update(MLT_EDITOR_HOME=project_dir, MLT_EDITOR_ROOTS=project_dir)
        return env
