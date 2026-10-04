"""Facade kept for existing imports and scripts: the engine lives in mltedit/engine.py. Importing this module returns that module itself,
so attribute changes made through either name (live.W, live.THEME...) are the same object. Run as a script it is the live-edit CLI."""
import importlib, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_m = importlib.import_module("mltedit.engine")
if __name__ == "__main__":
    _m.main()
else:
    sys.modules[__name__] = _m
