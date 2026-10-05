"""The model behind the chat: one interface, a real implementation (the Anthropic SDK) and a scripted one for tests.

    async for ev in model.step(system=..., messages=..., tools=...):
        {"type": "text", "text": "..."}                                         a piece of the answer as it is generated
        {"type": "final", "content": [blocks], "stop_reason": "...", "usage": {input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens}}
    `content` is the assistant message (text / tool_use / thinking blocks as plain dicts) to append to the conversation as it is; exactly one `final` ends a step.
"""
import asyncio, itertools
from typing import AsyncIterator


class ModelError(Exception):
    """The model could not be reached or refused the request: `code` is what the UI shows (RATE_LIMITED, API_ERROR, CONNECTION, AUTH, REFUSED)."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


class ModelClient:
    async def step(self, *, system: str, messages: list, tools: list) -> AsyncIterator[dict]:
        raise NotImplementedError
        yield {}


class AnthropicModel(ModelClient):
    """Claude through the official SDK, streaming. Tool inputs are small JSON objects (the engine validates every argument), so no eager input streaming."""
    def __init__(self, model, api_key=None, cache=True, max_tokens=16000):
        import anthropic
        self.anthropic = anthropic
        self.client = anthropic.AsyncAnthropic(api_key=api_key) if api_key else anthropic.AsyncAnthropic()
        self.model, self.cache, self.max_tokens = model, cache, max_tokens

    async def step(self, *, system, messages, tools):
        a = self.anthropic
        kw = dict(model=self.model, max_tokens=self.max_tokens, system=system, tools=tools, messages=messages)
        if self.cache:
            kw["cache_control"] = {"type": "ephemeral"}
        try:
            async with self.client.messages.stream(**kw) as stream:
                async for text in stream.text_stream:
                    yield {"type": "text", "text": text}
                msg = await stream.get_final_message()
        except TypeError as e:                       # the SDK raises this at the first request when it finds no credentials at all
            if "authentication" in str(e).lower() or "api_key" in str(e).lower():
                raise ModelError("AUTH", "no API credentials: set ANTHROPIC_API_KEY for the application") from None
            raise
        except a.AuthenticationError as e:
            raise ModelError("AUTH", f"the API key was refused ({e.status_code})") from None
        except a.RateLimitError:
            raise ModelError("RATE_LIMITED", "the model API is rate limiting this key; try again in a moment") from None
        except a.APIConnectionError:
            raise ModelError("CONNECTION", "could not reach the model API") from None
        except a.APIStatusError as e:
            raise ModelError("API_ERROR", f"the model API answered {e.status_code}: {str(e.message)[:200]}") from None
        u = msg.usage
        if msg.stop_reason == "refusal":
            raise ModelError("REFUSED", "the model declined to continue with this request")
        yield {"type": "final", "stop_reason": msg.stop_reason, "content": [b.model_dump(mode="json", exclude_none=True) for b in msg.content],
               "usage": {"input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
                         "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
                         "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0}}


class ScriptedModel(ModelClient):
    """A model that follows a script, for tests: `turns` is a list; each item is a list of blocks ({"type":"text",...} / {"type":"tool_use","name","input"[, "id"]}),
    or a function (messages) -> blocks, so a script can use what the tools answered. `usage` is reported with every step. Once the script ends it says "done"."""
    def __init__(self, turns, usage=None, delay=0.0):
        self.turns, self.usage, self.delay = list(turns), usage or {"input_tokens": 1000, "output_tokens": 100}, delay
        self.calls = []                   # what the chat sent: (system, messages, tool names), for assertions
        self._ids = itertools.count(1)

    async def step(self, *, system, messages, tools):
        self.calls.append({"system": system, "messages": messages, "tools": [t["name"] for t in tools]})
        turn = self.turns.pop(0) if self.turns else [{"type": "text", "text": "done"}]
        blocks = turn(messages) if callable(turn) else turn
        out = []
        for b in blocks:
            if b["type"] == "tool_use":
                b = {"id": f"toolu_{next(self._ids):04d}", **b}
            out.append(b)
        for b in out:
            if b["type"] == "text":
                for i in range(0, len(b["text"]), 12):
                    if self.delay:
                        await asyncio.sleep(self.delay)
                    yield {"type": "text", "text": b["text"][i:i + 12]}
        yield {"type": "final", "content": out, "stop_reason": "tool_use" if any(b["type"] == "tool_use" for b in out) else "end_turn", "usage": dict(self.usage)}


def cost_usd(usage, price):
    """USD of one step from its usage and the model's price tuple (input, output, cache read, cache write per million tokens)."""
    pin, pout, pread, pwrite = price
    return (usage.get("input_tokens", 0) * pin + usage.get("output_tokens", 0) * pout + usage.get("cache_read_input_tokens", 0) * pread
            + usage.get("cache_creation_input_tokens", 0) * pwrite) / 1e6
