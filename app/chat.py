"""The chat loop: the user's message goes to the model together with the editor's tools (the list the engine itself publishes over MCP); each tool the model calls is run in
the project's engine process and its answer goes back, until the model stops asking or a cap is reached. Stills the model asks for are images it sees, and thumbnails the UI shows.
It yields events (dicts) for the HTTP layer to stream; it never raises for a model/engine problem: it yields an `error` event and ends."""
import copy, json

from .config import PRICES
from .host import HostError
from .model import ModelError, cost_usd

PREFIX = """You are the editing assistant inside a video-editing web app. The user talks to you; you edit their project with the tools and look at the result with get_still / get_contact_sheet.
Be brief: say what you did in a sentence or two, in the user's language. When something is ambiguous ask one short question instead of guessing. After an edit that changes the picture, look at it before saying it is done.
Files the user uploaded are listed below with their full paths; video files are already imported as sources (see list_sources). Never invent a path. Exports are written to the exports folder shown below.
"""
MAX_TOOL_TEXT = 30000      # characters of one tool answer sent to the model (a huge timeline is cut with a note instead of filling the context)
UI_TEXT = 4000             # ... and to the UI


def system_prompt(instructions, uploads_dir, uploads, exports_dir):
    files = "\n".join(f"- {uploads_dir}/{n}" for n in uploads) or "- (none yet)"
    return f"{PREFIX}\nUploaded files:\n{files}\nExports folder: {exports_dir}\n\n--- Editor engine instructions ---\n{instructions}"


def to_blocks(res):
    """A normalized tool answer as the content of a tool_result block: text (cut if huge) and the images."""
    text = res["text"]
    if len(text) > MAX_TOOL_TEXT:
        text = text[:MAX_TOOL_TEXT] + f"\n[... {len(res['text']) - MAX_TOOL_TEXT} more characters not shown]"
    blocks = [{"type": "text", "text": text or ("(no output)" if not res["images"] else "")}] if (text or not res["images"]) else []
    blocks += [{"type": "image", "source": {"type": "base64", "media_type": i["media_type"], "data": i["data"]}} for i in res["images"]]
    return blocks


def trim_images(messages, keep):
    """A copy of the conversation where only the images of the last `keep` tool-result messages are kept; older ones become a short note (images are expensive in tokens
    and the model has already looked at them)."""
    out = copy.deepcopy(messages)
    holders = [i for i, m in enumerate(out) if m["role"] == "user" and isinstance(m["content"], list)
               and any(b.get("type") == "tool_result" and isinstance(b.get("content"), list) and any(c.get("type") == "image" for c in b["content"]) for b in m["content"])]
    for i in holders[:max(0, len(holders) - keep)]:
        for b in out[i]["content"]:
            if b.get("type") == "tool_result" and isinstance(b.get("content"), list):
                b["content"] = [{"type": "text", "text": "[an earlier still, already looked at]"} if c.get("type") == "image" else c for c in b["content"]]
    return out


def valid_tail(messages):
    """The conversation without a trailing assistant turn whose tool calls never got their answers (a cancelled or aborted turn): the API rejects such a history."""
    if messages and messages[-1]["role"] == "assistant" and isinstance(messages[-1]["content"], list) and any(b.get("type") == "tool_use" for b in messages[-1]["content"]):
        return messages[:-1]
    return messages


def revision_of(text):
    try:
        v = json.loads(text)
    except ValueError:
        return None
    return v.get("revision") if isinstance(v, dict) and isinstance(v.get("revision"), int) else None


async def run_chat(host, projects, model, model_name, settings, pid, user_text, metrics=None):
    conn = await host.conn(pid)
    d = projects.dir(pid)
    system = system_prompt(conn.instructions, f"{d}/uploads", projects.uploads(pid), f"{d}/exports")
    messages = valid_tail(projects.load_chat(pid))
    messages.append({"role": "user", "content": user_text})
    price = settings.price(model_name)
    spent, turns, stop = 0.0, 0, "end_turn"
    try:
        while True:
            if turns >= settings.max_turns:
                yield {"type": "error", "code": "MAX_TURNS", "message": f"stopped after {settings.max_turns} model calls for this message; say 'continue' to go on"}
                stop = "max_turns"
                break
            if spent >= settings.max_usd_message:
                yield {"type": "error", "code": "BUDGET_MESSAGE", "message": f"this message has used ${spent:.2f} (the limit is ${settings.max_usd_message:.2f}); say 'continue' to go on"}
                stop = "budget_message"
                break
            if projects.usage(pid)["usd"] >= settings.max_usd_project:
                yield {"type": "error", "code": "BUDGET_PROJECT", "message": f"this project has reached its ${settings.max_usd_project:.2f} limit"}
                stop = "budget_project"
                break
            turns += 1
            final = None
            try:
                async for ev in model.step(system=system, messages=trim_images(messages, keep=1), tools=conn.tools):
                    if ev["type"] == "text":
                        yield {"type": "text_delta", "text": ev["text"]}
                    elif ev["type"] == "final":
                        final = ev
            except ModelError as e:
                yield {"type": "error", "code": e.code, "message": e.message}
                stop = "model_error"
                break
            usd = cost_usd(final["usage"], price)
            spent += usd
            u = projects.add_usage(pid, usd, final["usage"], message=(turns == 1))
            if metrics is not None:
                metrics["model_calls"] = metrics.get("model_calls", 0) + 1
            yield {"type": "usage", "turn": turns, "cost_usd": round(usd, 5), "message_usd": round(spent, 5), "project_usd": u["usd"], "estimated": model_name not in settings.prices and model_name not in PRICES}
            uses = [b for b in final["content"] if b["type"] == "tool_use"]
            if final["stop_reason"] == "max_tokens" and uses:
                yield {"type": "error", "code": "OUTPUT_TOO_LONG", "message": "the model's answer was cut off in the middle of a tool call, so nothing was run"}
                stop = "max_tokens"
                break
            messages.append({"role": "assistant", "content": final["content"]})
            if not uses:
                break
            results = []
            for b in uses:
                yield {"type": "tool_call", "id": b["id"], "name": b["name"], "input": b["input"]}
                try:
                    res = await host.call(pid, b["name"], b["input"])
                except HostError as e:
                    res = {"ok": False, "text": f"{e.code}: {e.message}", "images": []}
                if metrics is not None:
                    metrics.setdefault("tool_calls", {})
                    metrics["tool_calls"][b["name"]] = metrics["tool_calls"].get(b["name"], 0) + 1
                    if not res["ok"]:
                        metrics["tool_errors"] = metrics.get("tool_errors", 0) + 1
                results.append({"type": "tool_result", "tool_use_id": b["id"], "content": to_blocks(res), **({"is_error": True} if not res["ok"] else {})})
                yield {"type": "tool_result", "id": b["id"], "name": b["name"], "ok": res["ok"], "text": res["text"][:UI_TEXT],
                       "images": [f"data:{i['media_type']};base64,{i['data']}" for i in res["images"][:3]]}
                rev = revision_of(res["text"])
                if rev is not None:
                    yield {"type": "project_changed", "revision": rev}
            messages.append({"role": "user", "content": results})
            projects.save_chat(pid, trim_images(messages, keep=0))
    finally:
        projects.save_chat(pid, trim_images(valid_tail(messages), keep=0))
    yield {"type": "done", "stop": stop, "message_usd": round(spent, 5), "project_usd": projects.usage(pid)["usd"], "turns": turns}
