"""MCP tools as removable pieces. Each module in this package defines the tools (and the op builders) of one area with the @tool and
@builder decorators; `install()` registers them with the FastMCP server and on the server module, so deleting a module removes its
tools. Helpers shared by all tools (project state, locking, binding the engine) live in mltedit/server.py and are reached as `sv`.

Docstrings may use <<placeholders>> (see DOC_VARS): they are filled in from the registries when the server starts, so a tool never
lists templates, transitions or presets by hand."""
import importlib, pkgutil

from .. import registry


def tool(fn):
    """Register an MCP tool under its function name."""
    registry.register("tool", fn.__name__, fn)
    return fn


def builder(name):
    """Register the op builder of an edit tool: (tool arguments) -> engine op. apply_ops uses it too, so a batch behaves like single calls."""
    def deco(fn):
        registry.register("builder", name, fn)
        return fn
    return deco


def _doc_vars():
    from .. import anim, cards, graphics, themes, transitions
    from ..config import S
    return {
        "templates": " | ".join(themes.NAMES), "transitions": " | ".join(transitions.STYLES), "default_transition": S.default_transition,
        "presets": " | ".join(anim.PRESETS), "easings": " | ".join(anim.EASES), "graphics": " | ".join(graphics.KINDS),
        "callout_presets": " | ".join(n for n in anim.PRESETS if anim.preset(n).callout), "layouts": " | ".join(cards.LAYOUTS),
        "edit_tools": ", ".join(registry.names("builder")),
    }


def fill_doc(doc):
    if not doc or "<<" not in doc:
        return doc
    for k, v in _doc_vars().items():
        doc = doc.replace(f"<<{k}>>", v)
    return doc


def load():
    """Import every tool module of this package (sorted, so registration order is deterministic)."""
    for m in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        importlib.import_module(f"{__name__}.{m.name}")


def install(server_module, mcp):
    """Register the tools with FastMCP and expose them (and the builders as _b_<name>) on the server module, as before the split."""
    load()
    server_module.BUILDERS.clear()
    for name, fn in registry.items("builder"):
        server_module.BUILDERS[name] = fn
        setattr(server_module, "_b_" + name, fn)
    for name, fn in registry.items("tool"):
        fn.__doc__ = fill_doc(fn.__doc__)
        mcp.tool()(fn)
        setattr(server_module, name, fn)
