"""MCP tools as removable pieces. Each module in this package defines the tools (and the op builders) of one area with the @tool and
@builder decorators; `install()` registers them with the FastMCP server and on the server module, so deleting a module removes its
tools. Helpers shared by all tools (project state, locking, binding the engine) live in mltedit/server.py and are reached as `sv`.

Docstrings may use <<placeholders>> (see DOC_VARS): they are filled in from the registries when the server starts, so a tool never
lists templates, transitions or presets by hand."""
import functools, hashlib, importlib, inspect, json, pkgutil, time

from .. import errors, log, registry


def _coded(fn):
    """Wrap a tool so that every ValueError reaching the agent is an EditError with a code (see errors.py)."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except ValueError as e:
            raise errors.as_edit_error(e) from None
    return wrapper


def tool(fn):
    """Register an MCP tool under its function name."""
    w = _coded(fn)
    registry.register("tool", fn.__name__, w)
    return w


def edit_tool(fn=None, *, anchor=False):
    """Register an MCP tool that changes the project. It gets the arguments every edit takes:
      expected_revision: the revision the agent made this edit against (see `revision` in every response); a stale one is refused with REVISION_CONFLICT.
      dry_run: true = change nothing, answer with what the edit WOULD do (a diff: ops added/removed/changed, things moved or hidden, the new duration,
               warnings that appear or go away); the real call is then the same call without dry_run.
      request_id: a name for this edit. Making the same call again with the same request_id (a retry after a timeout) applies nothing a second time and
               answers {"replayed": true, ...}; the same id on a different call is an error.
    With anchor=True (overlay and audio edits) also:
      anchor: "clip" (default) = the edit is anchored to the clip that is on screen at start_s and moves with it when earlier clips are cut, trimmed,
              moved or removed; "timeline" = it stays at that timeline time whatever happens to the clips.
    They are passed to the server through a per-call context (server.CALL), so the tool body never sees them."""
    def deco(fn):
        sig = inspect.signature(fn)
        P_ = inspect.Parameter
        extra = ([P_("anchor", P_.POSITIONAL_OR_KEYWORD, default="clip", annotation=str)] if anchor else []) + \
                [P_("expected_revision", P_.POSITIONAL_OR_KEYWORD, default=None, annotation=int | None),
                 P_("dry_run", P_.POSITIONAL_OR_KEYWORD, default=False, annotation=bool),
                 P_("request_id", P_.POSITIONAL_OR_KEYWORD, default=None, annotation=str | None)]

        @functools.wraps(fn)
        def wrapper(*a, expected_revision=None, dry_run=False, request_id=None, **kw):
            from .. import server as sv
            args = json.dumps([fn.__name__, [str(x) for x in a], {k: kw[k] for k in sorted(kw)}], default=str, sort_keys=True)
            ctx = {"tool": fn.__name__, "expected_revision": expected_revision, "anchor": kw.pop("anchor", None) if anchor else None, "dry_run": bool(dry_run),
                   "request_id": request_id, "args": hashlib.sha1(args.encode()).hexdigest()[:16]}
            tok = sv.CALL.set(ctx)
            t0, outcome = time.perf_counter(), {"ok": True}
            try:
                before = sv.load() if dry_run else None
                result = fn(*a, **kw)
                if isinstance(result, dict):
                    outcome.update({k: result[k] for k in ("op_id", "op_ids", "revision") if k in result})
                return sv.dry_run_report(before, ctx, result) if dry_run else result
            except sv.Replayed as rp:
                outcome["replayed"] = True
                return rp.result
            except ValueError as e:
                err = errors.as_edit_error(e)
                outcome = {"ok": False, "code": err.code}
                raise err from None
            finally:
                sv.CALL.reset(tok)
                log.event(tool=fn.__name__, ms=round((time.perf_counter() - t0) * 1000, 1), dry_run=bool(dry_run), request_id=request_id, expected_revision=expected_revision, **outcome)
        wrapper.__signature__ = sig.replace(parameters=list(sig.parameters.values()) + extra)
        doc = (fn.__doc__ or "") + "\n    Also takes expected_revision, dry_run, request_id" + (", anchor" if anchor else "") + " (see the server instructions)."
        wrapper.__doc__ = doc
        registry.register("tool", fn.__name__, wrapper)
        return wrapper
    return deco(fn) if fn is not None else deco


def builder(name, anchor=False):
    """Register the op builder of an edit tool: (tool arguments) -> engine op. apply_ops uses it too, so a batch behaves like single calls.
    anchor=True: the builder also takes anchor="clip"|"timeline" (see edit_tool)."""
    def deco(fn):
        if anchor:
            @functools.wraps(fn)
            def wrapped(*a, anchor="clip", **kw):
                op = fn(*a, **kw)
                if anchor not in ("clip", "timeline"):
                    raise errors.EditError("INVALID_ARGUMENT", f"anchor must be 'clip' or 'timeline' (got {anchor!r})")
                return {**op, "anchor": anchor} if anchor == "timeline" else op
            sig = inspect.signature(fn)
            wrapped.__signature__ = sig.replace(parameters=list(sig.parameters.values()) + [inspect.Parameter("anchor", inspect.Parameter.POSITIONAL_OR_KEYWORD, default="clip", annotation=str)])
            registry.register("builder", name, wrapped)
            return wrapped
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
        "edit_tools": ", ".join(registry.names("builder")), "error_codes": ", ".join(errors.CODES),
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
