"""{"op":"audio","path":"music.mp3","start":0,"volume_db":-14,...}: music / sound effect with fades and ducking under speech."""
import os

from ...config import S
from ...ops import Op, op


@op
class Audio(Op):
    timed = True
    name = "audio"

    def layout(self, o, n, where, st):
        if not isinstance(o.get("loop", False), bool):
            raise ValueError(f"{where}: loop must be true or false")
        nums = {"start": o.get("start"), "in": o.get("in", 0.0), "src_dur": o.get("src_dur"), "volume_db": o.get("volume_db", -14.0),
                "fade_in": o.get("fade_in") if o.get("fade_in") is not None else 0.0, "fade_out": o.get("fade_out") if o.get("fade_out") is not None else 0.0,
                "duck_db": o.get("duck_db", -12.0)}
        for nk, nv in nums.items():
            if not isinstance(nv, (int, float)) or isinstance(nv, bool):
                raise ValueError(f"{where}: {nk} must be a number")
        if o.get("dur") is not None and (not isinstance(o["dur"], (int, float)) or isinstance(o["dur"], bool) or not 0 < o["dur"] <= 3600):
            raise ValueError(f"{where}: dur must be between 0 and 3600 seconds (or omitted: as long as the audio / the timeline)")
        if nums["start"] < 0 or nums["in"] < 0 or nums["fade_in"] < 0 or nums["fade_out"] < 0:
            raise ValueError(f"{where}: start, in and the fades must be >= 0")
        if nums["src_dur"] <= 0 or nums["in"] >= nums["src_dur"]:
            raise ValueError(f"{where}: 'in' ({nums['in']:g}s) is past the end of the audio ({nums['src_dur']:g}s)")
        if not -60 <= nums["volume_db"] <= 6:
            raise ValueError(f"{where}: volume_db must be between -60 and 6")
        if not -40 <= nums["duck_db"] <= 0:
            raise ValueError(f"{where}: duck_db must be between -40 and 0 (how much quieter during speech)")
        duck = o.get("duck") or []
        if not isinstance(duck, list) or len(duck) > 400 or not all(isinstance(iv, (list, tuple)) and len(iv) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in iv) and 0 <= iv[0] < iv[1] for iv in duck):
            raise ValueError(f"{where}: duck must be a list of up to 400 [start_s, end_s] pairs (timeline seconds, start < end)")
        if len(st.audios) >= S.max_audios:
            raise ValueError(f"{where}: at most {S.max_audios} audio ops")
        st.audios.append({"op": n, "path": o.get("path"), "start": float(nums["start"]), "in": float(nums["in"]), "src_dur": float(nums["src_dur"]), "dur": o.get("dur"),
                          "vol": float(nums["volume_db"]), "fade_in": None if o.get("fade_in") is None else float(nums["fade_in"]),
                          "fade_out": None if o.get("fade_out") is None else float(nums["fade_out"]), "loop": bool(o.get("loop", False)),
                          "duck": [(float(a_), float(b_)) for a_, b_ in duck], "duck_db": float(nums["duck_db"]), "name": o.get("name") or os.path.basename(str(o.get("path")))})

    def assets(self, o):
        return [o["asset"]] if o.get("asset") else []

    def files(self, o):
        return [o["path"]] if o.get("path") else []

    def describe(self, o):
        name = o.get("name") or os.path.basename(str(o.get("path")))
        return f"Audio {name} desde {o.get('start', 0):g} s", f"audio {name} @ {o.get('start', 0):g}s"
