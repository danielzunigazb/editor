"""Edit errors with a stable code, so an agent can react to what went wrong without parsing prose.

EditError is a ValueError (everything that catches ValueError keeps working). str(e) is the human message with the code in front, then one JSON
line the agent can parse:
    OUT_OF_RANGE: op 1 (add): range 5-13s outside source 'A' (0-6s)
    {"code": "OUT_OF_RANGE", "op": 1, "field": null, ...}
Codes come from the raise site (EditError("CODE", ...)); a plain ValueError raised deeper in the renderers is classified by its text (CLASSIFY), so
nothing reaches the agent without a code."""
import json, re

CODES = {
    "INVALID_ARGUMENT": "a value has the wrong type or is outside its allowed set",
    "OUT_OF_RANGE": "a time, size or amount is outside what the source or the format allows",
    "UNKNOWN_SOURCE": "the source id was never imported",
    "UNKNOWN_NAME": "a template, style, icon, transition, preset or asset that does not exist",
    "UNKNOWN_OP": "no such edit, op id or clip id",
    "TEXT_DOES_NOT_FIT": "the text cannot be drawn legibly in the frame at that size",
    "PATH_NOT_ALLOWED": "the file is outside the allowed folders, missing, or not usable",
    "TIMELINE_CONFLICT": "the edit does not fit the timeline as it is (starts after the end, overlaps too much, depends on something removed)",
    "LIMIT_EXCEEDED": "a project limit (edits, sources, overlays, audio clips)",
    "REVISION_CONFLICT": "the project changed since the revision this edit was made against",
    "SOURCE_CHANGED": "a file the project depends on changed on disk since it was imported",
    "SOURCE_MISSING": "a file the project depends on is gone",
    "NOTHING_TO_DO": "nothing to undo/redo",
    "INTERNAL": "a bug: report it",
}

CLASSIFY = [                                              # (regex on the message, code); first match wins
    (r"REVISION_CONFLICT", "REVISION_CONFLICT"),
    (r"does not fit|too small to read|cannot fit|characters the font lacks|wrap", "TEXT_DOES_NOT_FIT"),
    (r"unknown source", "UNKNOWN_SOURCE"),
    (r"no clip with id|no op with id|no timeline entry|no op \d|unknown op", "UNKNOWN_OP"),
    (r"unknown (template|style|icon|graphic|transition|asset|easing)|must be one of|did you mean|choose one of", "UNKNOWN_NAME"),
    (r"nothing to (undo|redo)", "NOTHING_TO_DO"),
    (r"outside the allowed folders|not found|not a file|not an SVG|not a readable image|larger than|no audio stream|ffprobe", "PATH_NOT_ALLOWED"),
    (r"the limit|at most \d+ audio|more than \d+ (overlays|audio)|limit is", "LIMIT_EXCEEDED"),
    (r"starts at .* but the timeline|after the end|crossfades need|every subtitle starts|adjacent|overlap", "TIMELINE_CONFLICT"),
    (r"outside source|outside entry|range |is past the end|outside the timeline|longer than the|between \d|must be (in|>=|>|<=|<)|needs start|shorter than one frame|sub-frame", "OUT_OF_RANGE"),
]


def code_for(message):
    for rx, code in CLASSIFY:
        if re.search(rx, message):
            return code
    return "INVALID_ARGUMENT"


class EditError(ValueError):
    def __init__(self, code, where_or_message, message=None, *, field=None, hint=None, allowed=None):
        """EditError("CODE", "op 3 (cut)", "no timeline entry 7") or EditError("CODE", "message"); code None = classify from the text."""
        where, msg = (where_or_message, message) if message is not None else ("", where_or_message)
        self.where, self.message = where, msg
        self.code = code or code_for(f"{where}: {msg}")
        self.field, self.hint, self.allowed = field, hint, allowed
        super().__init__(self.text())

    def text(self):
        human = f"{self.where}: {self.message}" if self.where else self.message
        if human.startswith(self.code):
            human = human[len(self.code):].lstrip(": ")
        meta = {"code": self.code, **({"where": self.where} if self.where else {}), **({"field": self.field} if self.field else {}),
                **({"hint": self.hint} if self.hint else {}), **({"allowed": self.allowed} if self.allowed else {})}
        return f"{self.code}: {human}\n{json.dumps(meta, ensure_ascii=False, default=str)}"

    def with_prefix(self, prefix, suffix=""):
        """The same error (same code) with a prefix on its message, e.g. the batch item it came from."""
        return EditError(self.code, f"{prefix}{self.where}" if self.where else prefix.rstrip(": "), self.message + suffix, field=self.field, hint=self.hint, allowed=self.allowed)


def as_edit_error(e, where=""):
    """Any ValueError as an EditError (an EditError is returned as it is)."""
    if isinstance(e, EditError):
        return e
    msg = str(e)
    if where and msg.startswith(where + ": "):
        msg = msg[len(where) + 2:]
    return EditError(None, where, msg)
