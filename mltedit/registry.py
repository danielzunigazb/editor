"""One registry for every removable piece of the editor. A piece is registered under (kind, name); the engine only ever asks the registry,
so deleting a plugin file or a pack folder removes that piece and nothing else.

Kinds used by the engine: theme, text_style, shape, card_bg, card_layout, transition, anim_preset, easing, op, tool, asset_source.
Built-in plugins live in mltedit/plugins/<kind>/*.py and register themselves when imported; `load()` imports them all (sorted by name,
so the order is deterministic), then any *.py in the configured plugin_dirs, then the theme packs. Errors in a third-party plugin or pack
are collected in `problems()` instead of stopping the editor."""
import importlib, importlib.util, os, pkgutil

_REG = {}
_PROBLEMS = []
_LOADED = {"done": False}


def register(kind, name, obj, replace=False, origin=""):
    if not isinstance(name, str) or not name:
        raise ValueError(f"{kind}: a piece needs a non-empty string name")
    bucket = _REG.setdefault(kind, {})
    if name in bucket and not replace:
        raise ValueError(f"{kind} '{name}' is already registered (by {bucket[name][1] or 'the engine'}); pass replace=True to override it")
    bucket[name] = (obj, origin)
    return obj


def unregister(kind, name):
    _REG.get(kind, {}).pop(name, None)


def get(kind, name, default=KeyError):
    load()
    try:
        return _REG[kind][name][0]
    except KeyError:
        if default is not KeyError:
            return default
        raise KeyError(f"no {kind} named '{name}'; available: {names(kind)}")


def has(kind, name):
    load()
    return name in _REG.get(kind, {})


def names(kind):
    load()
    return tuple(_REG.get(kind, {}))


def items(kind):
    load()
    return [(n, v[0]) for n, v in _REG.get(kind, {}).items()]


def origin(kind, name):
    return _REG.get(kind, {}).get(name, (None, ""))[1]


def problems():
    load()
    return list(_PROBLEMS)


def note_problem(msg):
    _PROBLEMS.append(msg)


def decorator(kind, name=None, **kw):
    """@registry.decorator("transition", "wipe-right") above a function or class registers it."""
    def wrap(obj):
        register(kind, name or obj.__name__, obj, **kw)
        return obj
    return wrap


def _import_builtin():
    from . import plugins
    for mod in sorted(pkgutil.walk_packages(plugins.__path__, plugins.__name__ + "."), key=lambda m: m.name):
        importlib.import_module(mod.name)


def _import_external(dirs):
    for d in dirs:
        if not os.path.isdir(d):
            _PROBLEMS.append(f"plugin dir {d} does not exist")
            continue
        for f in sorted(os.listdir(d)):
            if not f.endswith(".py") or f.startswith("_"):
                continue
            p = os.path.join(d, f)
            try:
                spec = importlib.util.spec_from_file_location(f"mltedit_ext_{abs(hash(p))}_{f[:-3]}", p)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
            except Exception as e:                                      # a broken third-party plugin must not take the editor down
                _PROBLEMS.append(f"plugin {p}: {type(e).__name__}: {e}")


def load(force=False):
    """Import built-in plugins, external plugin dirs and theme packs (once per process unless force)."""
    if _LOADED["done"] and not force:
        return
    _LOADED["done"] = True
    if force:
        _REG.clear(); _PROBLEMS.clear()
    from .config import S
    _import_builtin()
    _import_external(S.plugin_dirs)
    from . import packs
    packs.load_all(S.packs_dirs)


def reload():
    """Forget everything and load again (tests that add or remove pieces on disk)."""
    load(force=True)
