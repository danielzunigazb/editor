#!/usr/bin/env python3
"""Pure-python checks of transcribe.py's cue building (no WhisperX needed). Run: python3 test_transcribe.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import transcribe as T
ok = bad = 0
def chk(name, cond, detail=""):
    global ok, bad
    ok += bool(cond); bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if not cond and detail else ""))

W = lambda w, s, e, spk="SPEAKER_00": {"word": w, "start": s, "end": e, "speaker": spk}
words = [W("Hola,", 0.0, 0.4), W("¿cómo", 0.5, 0.8), W("estás?", 0.8, 1.2), W("Bien,", 1.5, 1.9, "SPEAKER_01"), W("gracias.", 1.9, 2.4, "SPEAKER_01"),
         W("Me", 2.5, 2.6), W("alegro.", 2.6, 3.1)]
c = T.make_cues(words)
chk("a speaker change always starts a new cue", [x["speaker"] for x in c] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"], [x["speaker"] for x in c])
chk("cue text and times come from the words", c[0]["text"] == "Hola, ¿cómo estás?" and c[0]["start"] == 0.0 and c[0]["end"] == 1.2, c[0])
long = [W(f"palabra{i}", i * 0.3, i * 0.3 + 0.25) for i in range(30)]
cl = T.make_cues(long, max_chars=40)
chk("long speech is split to the character limit", len(cl) > 1 and all(len(x["text"]) <= 40 for x in cl), [len(x["text"]) for x in cl])
chk("a pause longer than 0.8 s splits a cue", len(T.make_cues([W("uno", 0, 0.3), W("dos", 2.0, 2.3)])) == 2)
chk("a flash shorter than 0.4 s is stretched so it can be read", T.make_cues([W("sí", 1.0, 1.1)])[0]["end"] == 1.4)
miss = T.fill_missing([{"word": "a", "start": 0.0, "end": 0.3}, {"word": "2024"}, {"word": "b", "start": 0.8, "end": 1.0}])
chk("words without timestamps (numbers) borrow them from their neighbours", miss[1]["start"] == 0.3 and miss[1]["end"] == 0.8, miss[1])
ops, spk = T.to_ops(c, {}, offset=10.0)
chk("one add_subtitles per speaker, each with its own colour", len(ops) == 2 and ops[0]["color"] != ops[1]["color"] and spk == ["SPEAKER_00", "SPEAKER_01"])
chk("the offset places cues on the timeline", ops[0]["cues"][0]["start"] == 10.0 and ops[1]["cues"][0]["start"] == 11.5, ops[0]["cues"][0])
chk("ops are valid apply_ops items", all(o["tool"] == "add_subtitles" and all({"start", "end", "text"} <= set(q) for q in o["cues"]) for o in ops))
chk("SRT timestamps format", T.srt_time(3723.456) == "01:02:03,456")
print(f"\n{ok} passed, {bad} failed"); sys.exit(1 if bad else 0)
