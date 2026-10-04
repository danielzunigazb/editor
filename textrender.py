"""Facade kept for existing imports and scripts: the code lives in mltedit/render/text.py. Importing this module returns that module itself,
so attribute changes made through either name are the same object (no copy, no logic here)."""
import importlib, sys

sys.modules[__name__] = importlib.import_module("mltedit.render.text")
