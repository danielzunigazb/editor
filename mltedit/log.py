"""Structured logs: one JSON line on stderr per tool call (never stdout: that is the MCP channel), so a client's log shows what an agent did, how long it
took and what failed. Set MLT_LOG=off to silence it."""
import json, sys, time

from .config import S


def event(**kw):
    if S.log == "off":
        return
    try:
        sys.stderr.write(json.dumps({"ts": round(time.time(), 3), **kw}, default=str, ensure_ascii=False) + "\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass
