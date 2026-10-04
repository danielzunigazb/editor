#!/usr/bin/env python3
"""Facade kept for existing configs (.mcp.json) and imports: the MCP server lives in mltedit/server.py.
Run (stdio):  .venv/bin/python server.py   (settings: see mltedit/config.py; e.g. MLT_EDITOR_HOME=<project dir>)."""
import importlib, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_m = importlib.import_module("mltedit.server")
if __name__ == "__main__":
    _m.main()
else:
    sys.modules[__name__] = _m
