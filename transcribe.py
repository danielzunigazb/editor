#!/usr/bin/env python3
"""Speech-to-text with speaker labels, run OUTSIDE the model (WhisperX: faster-whisper + wav2vec2 word alignment + pyannote diarization).
Run with the STT venv:
  .venv_stt/bin/python transcribe.py <audio-or-video> <out_prefix> [--lang es] [--model large-v3-turbo] [--speakers 2]
        [--names "SPEAKER_00=Ana,SPEAKER_01=Luis"] [--no-diarize] [--offset 0.0] [--max-chars 70]
Diarization needs a Hugging Face token (env HF_TOKEN) from an account that accepted the pyannote model terms; without it use --no-diarize.
Writes <out_prefix>.json (words + cues), <out_prefix>.srt ("Name: text") and <out_prefix>_ops.json (apply_ops items: one add_subtitles
per speaker, each with its own colour, so the editor shows who is talking)."""
import argparse, json, os, sys

PALETTE = ["#F2D58A", "#8EC9F2", "#F2A0A0", "#A8E0A8", "#D2A8F0", "#F0C08A"]      # soft gold, sky, rose, mint, lilac, apricot
SENT_END = (".", "?", "!", "…")


def fill_missing(words):
    """Aligned words can lack start/end (numbers, symbols): borrow from the neighbours so no word is lost."""
    for i, w in enumerate(words):
        if "start" not in w or "end" not in w:
            prev = next((words[j] for j in range(i - 1, -1, -1) if "end" in words[j]), None)
            nxt = next((words[j] for j in range(i + 1, len(words)) if "start" in words[j]), None)
            s = prev["end"] if prev else (nxt["start"] if nxt else 0.0)
            e = nxt["start"] if nxt else s + 0.2
            w["start"], w["end"] = s, max(e, s + 0.05)
    return words


def make_cues(words, max_chars=70, max_dur=6.0, gap=0.8):
    """Group words into subtitle cues: never across a speaker change, a long pause, a sentence end once the cue is long enough,
    or the character/duration limits."""
    cues, cur = [], None
    for w in words:
        spk = w.get("speaker", "SPEAKER_00")
        txt = w["word"].strip()
        if not txt:
            continue
        if cur and (spk != cur["speaker"] or w["start"] - cur["end"] > gap or len(cur["text"]) + 1 + len(txt) > max_chars
                    or w["end"] - cur["start"] > max_dur
                    or (cur["text"].endswith(SENT_END) and len(cur["text"]) >= 25)):
            cues.append(cur); cur = None
        if cur is None:
            cur = {"speaker": spk, "start": w["start"], "end": w["end"], "text": txt}
        else:
            cur["text"] += " " + txt; cur["end"] = w["end"]
    if cur:
        cues.append(cur)
    for c in cues:
        c["start"], c["end"] = round(c["start"], 3), round(max(c["end"], c["start"] + 0.4), 3)       # a flash shorter than 0.4 s is unreadable
    return cues


def srt_time(t):
    ms = int(round(t * 1000)); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_ops(cues, names, offset=0.0, size=0.05, position="bottom", turn_gap=3.0):
    """One add_subtitles per speaker (own colour). Cues are shifted by `offset` (timeline = audio time + offset).
    When `names` has a name for a speaker, the first cue of each of that speaker's turns starts with "Name: "."""
    speakers = sorted({c["speaker"] for c in cues})
    ops = []
    starts = {id(c) for i, c in enumerate(cues) if i == 0 or cues[i - 1]["speaker"] != c["speaker"] or c["start"] - cues[i - 1]["end"] > turn_gap}
    for i, s in enumerate(speakers):
        mine = [{"start": round(c["start"] + offset, 3), "end": round(c["end"] + offset, 3),
                 "text": (f"{names[s]}: " if s in names and id(c) in starts else "") + c["text"]} for c in cues if c["speaker"] == s]
        ops.append({"tool": "add_subtitles", "cues": mine, "color": PALETTE[i % len(PALETTE)], "style": "champagne", "size": size,
                    "position": position, "box": True})
    return ops, speakers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("media"); ap.add_argument("out")
    ap.add_argument("--lang", default="es"); ap.add_argument("--model", default="large-v3-turbo")
    ap.add_argument("--speakers", type=int, default=0, help="exact number of speakers if known (0 = let the model decide)")
    ap.add_argument("--min-speakers", type=int, default=0); ap.add_argument("--max-speakers", type=int, default=0)
    ap.add_argument("--names", default="", help='"SPEAKER_00=Ana,SPEAKER_01=Luis"')
    ap.add_argument("--no-diarize", action="store_true")
    ap.add_argument("--offset", type=float, default=0.0); ap.add_argument("--max-chars", type=int, default=70)
    ap.add_argument("--device", default="cpu"); ap.add_argument("--compute", default="int8"); ap.add_argument("--batch", type=int, default=8)
    a = ap.parse_args()
    import whisperx
    names = dict(p.split("=", 1) for p in a.names.split(",") if "=" in p)
    audio = whisperx.load_audio(a.media)
    model = whisperx.load_model(a.model, a.device, compute_type=a.compute, language=a.lang)
    res = model.transcribe(audio, batch_size=a.batch, language=a.lang)
    am, meta = whisperx.load_align_model(language_code=res.get("language", a.lang), device=a.device)
    res = whisperx.align(res["segments"], am, meta, audio, a.device, return_char_alignments=False)
    if not a.no_diarize:
        tok = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        if not tok:
            raise SystemExit("diarization needs a Hugging Face token in HF_TOKEN (and the pyannote model terms accepted); or pass --no-diarize")
        try:
            from whisperx.diarize import DiarizationPipeline
        except ImportError:
            DiarizationPipeline = whisperx.DiarizationPipeline
        try:
            dp = DiarizationPipeline(token=tok, device=a.device)
        except TypeError:
            dp = DiarizationPipeline(use_auth_token=tok, device=a.device)
        kw = {}
        if a.speakers:
            kw = {"num_speakers": a.speakers}
        elif a.min_speakers or a.max_speakers:
            kw = {k: v for k, v in (("min_speakers", a.min_speakers), ("max_speakers", a.max_speakers)) if v}
        res = whisperx.assign_word_speakers(dp(audio, **kw), res)
    words = fill_missing([w for seg in res["segments"] for w in seg.get("words", [])])
    for seg in res["segments"]:                                     # words inherit the segment's speaker if they got none
        for w in seg.get("words", []):
            w.setdefault("speaker", seg.get("speaker", "SPEAKER_00"))
    cues = make_cues(words, a.max_chars)
    label = lambda s: names.get(s, s.replace("SPEAKER_", "Hablante "))
    with open(a.out + ".srt", "w", encoding="utf-8") as f:
        for i, c in enumerate(cues, 1):
            f.write(f"{i}\n{srt_time(c['start'] + a.offset)} --> {srt_time(c['end'] + a.offset)}\n{label(c['speaker'])}: {c['text']}\n\n")
    ops, speakers = to_ops(cues, names, a.offset)
    json.dump({"language": res.get("language", a.lang), "speakers": speakers, "cues": cues, "words": words}, open(a.out + ".json", "w"), ensure_ascii=False)
    json.dump(ops, open(a.out + "_ops.json", "w"), ensure_ascii=False)
    print(f"{len(cues)} cues, {len(speakers)} speaker(s): {', '.join(label(s) for s in speakers)} -> {a.out}.srt / .json / _ops.json", file=sys.stderr)


if __name__ == "__main__":
    main()
