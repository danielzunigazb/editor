"""The 12 validation tasks. Each has: `prompt(c)` (what the model is told; never names an engine feature), optional `prep` (python run through the server API
before the model starts), `ref` (a reference solution through the server API: proves the checks can be satisfied), and `check(state, c)` returning a list of
(name, ok, detail). `state` is what dump.py reads from the project the model left (layout, project settings, exports probed with ffprobe/qa/ebur128).
Checks are about outcomes (what is on the timeline, how long the export is), never about which tools were used. Times are in seconds, tolerance 0.06 s (a frame
at 25 fps is 0.04) unless said."""

TOL = 0.06


def near(a, b, tol=TOL):
    return a is not None and abs(a - b) <= tol


def ent(state, src):
    return [e for e in state["layout"]["entries"] if e["src"] == src]


def texts(state, kind="text"):
    return [L for L in state["layout"]["layers"] if L["kind"] == kind]


def cards(state):
    return sorted([e for e in state["layout"]["entries"] if e["src"].startswith("CARD")], key=lambda e: e["start"])


def graphics(state, gk):
    return [L for L in state["layout"]["layers"] if L["kind"] == "graphic" and L.get("gk") == gk]


def export_ok(state, dur, tol=0.25, size=None, fps=None):
    ex = state["exports"]
    if not ex:
        return [("export_exists", False, "no exported file")]
    e = ex[0]
    out = [("export_exists", True, e["file"]), ("export_duration", near(e["duration_s"], dur, tol), f'{e["duration_s"]} vs {dur}'),
           ("~export_qa_findings", not e["qa_findings"], e["qa_findings"][:3])]
    if size:
        out.append(("export_size", (e["width"], e["height"]) == tuple(size), (e["width"], e["height"])))
    if fps:
        out.append(("export_fps", near(e["fps"], fps, 0.05), e["fps"]))
    return out


def T(name, prompt, check, ref="", prep="", kind="build", out=False):
    return {"name": name, "prompt": prompt, "check": check, "ref": ref, "prep": prep, "kind": kind, "needs_out": out}


# ---------------------------------------------------------------------------------------------------------------------------------------------------------

def t_montage3():
    def prompt(c):
        return (f"Using the video editor tools: make a vertical 1080x1920 25 fps project from three phone clips: {c['clips']['R1']['abs']} as R1, {c['clips']['R3']['abs']} as R3 and "
                f"{c['clips']['R2']['abs']} as R2. Timeline: the first 3 s of R1, then 3 s of R3, then 3 s of R2, with a 0.5 s crossfade between each pair. Add the title "
                f"'Mi fin de semana' on screen during the first 2 seconds. Do not export. Say how long the whole video is.")

    def check(s, c):
        order = sorted(s["layout"]["entries"], key=lambda e: e["start"])
        t = [x for x in texts(s) if "Mi fin de semana" in x.get("text", "")]
        return [("size_1080x1920", (s["project"]["width"], s["project"]["height"]) == (1080, 1920), (s["project"]["width"], s["project"]["height"])),
                ("order_R1_R3_R2", [e["src"] for e in order] == ["R1", "R3", "R2"], [e["src"] for e in order]),
                ("each_3s", all(near(e["dur"], 3.0) for e in order), [e["dur"] for e in order]),
                ("total_8s", near(s["layout"]["total"], 8.0, 0.1), s["layout"]["total"]),
                ("two_crossfades", len(s["layout"]["xfades"]) == 2, s["layout"]["xfades"]),
                ("title_in_first_2s", len(t) == 1 and t[0]["start"] <= 0.5 and t[0]["start"] + t[0]["dur"] >= 1.5, [(x["start"], x["dur"]) for x in t])]
    ref = ("server.new_project(1080,1920,25)\nserver.import_clip(P['R1'],'R1');server.import_clip(P['R3'],'R3');server.import_clip(P['R2'],'R2')\n"
           "server.add_clip('R1',0,3);server.add_clip('R3',0,3);server.add_clip('R2',0,3);server.crossfade(0,0.5);server.crossfade(1,0.5)\nserver.add_text('Mi fin de semana',0.2,1.8)")
    return T("montage3", prompt, check, ref)


def t_interview_lt():
    def prompt(c):
        return (f"Using the video editor tools: from the talk {c['clips']['C']['abs']} (import it as C) make a 1920x1080 25 fps video of the segment from 20 s to 50 s of the "
                f"talk. Two seconds into that segment show a lower third that says 'María Pérez' with the subtitle 'Directora de producto', for 4 seconds. Export a draft "
                f"quality file to {{out}} and say how long the exported video is.")

    def check(s, c):
        e = ent(s, "C")
        lt = graphics(s, "lower_third")
        return ([("one_clip_20_to_50", len(e) == 1 and near(e[0]["in"], 20.0) and near(e[0]["dur"], 30.0), [(x["in"], x["dur"]) for x in e]),
                 ("lower_third_at_2s_for_4s", len(lt) == 1 and near(lt[0]["start"], 2.0) and near(lt[0]["dur"], 4.0), [(x["start"], x["dur"]) for x in lt])]
                + export_ok(s, 30.0, size=(1920, 1080), fps=25))
    ref = ("server.new_project(1920,1080,25);server.import_clip(P['C'],'C');server.add_clip('C',20,50);server.add_lower_third('María Pérez','Directora de producto',2,4)\n"
           "server.export(OUT,'draft')")
    return T("interview_lt", prompt, check, ref, out=True)


def t_subtitles_srt():
    def prompt(c):
        return (f"Using the video editor tools: from the talk {c['clips']['C']['abs']} (import it as C) make a 1920x1080 25 fps video of its first 40 seconds, with subtitles taken "
                f"from the subtitle file {c['srt_abs']}. Check with a contact sheet that the subtitles are readable and inside the picture. Do not export. Say how many "
                f"subtitle lines the video has.")

    def check(s, c):
        subs = [x for x in texts(s) if x.get("op") is not None and x.get("sub") is not None]
        tot = s["layout"]["total"]
        inside = [x for x in subs if x["start"] >= -0.01 and x["start"] + x["dur"] <= tot + 0.05]
        want = srt_cues(c["srt_abs"], 40.0)
        return [("total_40s", near(tot, 40.0), tot), ("every_srt_line_present", len(subs) == want, f"{len(subs)} vs {want} in the file"),
                ("all_lines_inside_video", len(inside) == len(subs), len(subs) - len(inside)),
                ("first_line_at_6_77s", bool(subs) and near(min(x["start"] for x in subs), 6.773, 0.1), min([x["start"] for x in subs] or [None]))]
    ref = "server.new_project(1920,1080,25);server.import_clip(P['C'],'C');server.add_clip('C',0,40);server.add_subtitles(srt_path=SRT)"
    return T("subtitles_srt", prompt, check, ref)


def t_vertical_reel():
    def prompt(c):
        return (f"Using the video editor tools: make a vertical reel 1080x1920 at 30 fps from {c['clips']['R1']['abs']} (R1) and {c['clips']['R3']['abs']} (R3): first 4 s of R1, "
                f"then first 4 s of R3 with a 0.4 s crossfade, the text 'Día 1' at the top during the first 1.5 s, and a fade out over the last 0.5 s. Export a draft file "
                f"to {{out}} and say how long it is.")

    def check(s, c):
        t = [x for x in texts(s) if "Día 1" in x.get("text", "")]
        return ([("two_clips_in_order", [e["src"] for e in sorted(s["layout"]["entries"], key=lambda e: e["start"])] == ["R1", "R3"], None),
                 ("title_top_first_1_5s", len(t) == 1 and t[0]["start"] <= 0.3 and t[0].get("pos") == "top" and near(t[0]["dur"], 1.5, 0.3), [(x["start"], x["dur"], x.get("pos")) for x in t]),
                 ("fade_out_set", bool(s["layout"]["fade"]), s["layout"]["fade"])] + export_ok(s, 7.6, size=(1080, 1920), fps=30))
    ref = ("server.new_project(1080,1920,30);server.import_clip(P['R1'],'R1');server.import_clip(P['R3'],'R3');server.add_clip('R1',0,4);server.add_clip('R3',0,4);server.crossfade(0,0.4)\n"
           "server.add_text('Día 1',0.1,1.5,position='top');server.set_fades(0.0,0.5);server.export(OUT,'draft')")
    return T("vertical_reel", prompt, check, ref, out=True)


def t_cut_under_overlays():
    def prompt(c):
        return (f"Using the video editor tools: make a 1920x1080 25 fps project: from the talk {c['clips']['C']['abs']} (C) seconds 0-10, then from {c['clips']['K']['abs']} (K) seconds "
                f"5-15, with a 0.5 s crossfade. Show the text 'Dato clave' for 2 s starting exactly 2 s after clip K starts on the timeline. THEN shorten the clip C so it only "
                f"lasts 6 s. The text must still appear 2 s after K starts. Do not export. Say at what time the text appears.")

    def check(s, c):
        k, cc = ent(s, "K"), ent(s, "C")
        t = [x for x in texts(s) if "Dato clave" in x.get("text", "")]
        return [("C_is_6s", len(cc) == 1 and near(cc[0]["dur"], 6.0), [x["dur"] for x in cc]),
                ("K_kept_10s", len(k) == 1 and near(k[0]["dur"], 10.0), [x["dur"] for x in k]),
                ("text_2s_into_K", len(t) == 1 and len(k) == 1 and near(t[0]["start"], k[0]["start"] + 2.0), (t[0]["start"] if t else None, k[0]["start"] if k else None)),
                ("text_lasts_2s", len(t) == 1 and near(t[0]["dur"], 2.0), [x["dur"] for x in t])]
    ref = ("server.new_project(1920,1080,25);server.import_clip(P['C'],'C');server.import_clip(P['K'],'K');server.add_clip('C',0,10);server.add_clip('K',5,15);server.crossfade(0,0.5)\n"
           "server.add_text('Dato clave',11.5,2.0)\nserver.trim_clip(0,0,6)")
    return T("cut_under_overlays", prompt, check, ref)


def t_long_edit():
    def prompt(c):
        return (f"Using the video editor tools: make a 1920x1080 25 fps video that opens with a 4 s title card (title 'Charla completa', subtitle 'Sesión de preguntas') followed by "
                f"the first 120 seconds of the talk {c['clips']['C']['abs']} (C). Fade in at the start and fade out over the last second. Export a draft file to {{out}} and "
                f"say how long it is.")

    def check(s, c):
        e = ent(s, "C")
        return ([("talk_120s", len(e) == 1 and near(e[0]["in"], 0.0) and near(e[0]["dur"], 120.0), [(x["in"], x["dur"]) for x in e]),
                 ("title_card_4s_first", bool(cards(s)) and near(cards(s)[0]["dur"], 4.0) and cards(s)[0]["start"] <= 0.05, [(e["start"], e["dur"]) for e in cards(s)]),
                 ("~title_text_unverifiable", True, "a card's text is not stored in the project, only rendered"),
                 ("fades", bool(s["layout"]["fade"]), s["layout"]["fade"])] + export_ok(s, 124.0, tol=0.5, size=(1920, 1080), fps=25))
    ref = ("server.new_project(1920,1080,25);server.import_clip(P['C'],'C');server.add_card('title',title='Charla completa',subtitle='Sesión de preguntas',dur_s=4.0)\n"
           "server.add_clip('C',0,120);server.set_fades(0.5,1.0);server.export(OUT,'draft')")
    return T("long_edit", prompt, check, ref, out=True)


def srt_cues(path, until):
    import re
    n = 0
    for m in re.finditer(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)", open(path, encoding="utf-8").read()):
        h, mi, se, ms = (int(x) for x in m.groups()[:4])
        e = int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7)) + int(m.group(8)) / 1000
        n += (h * 3600 + mi * 60 + se + ms / 1000) < until and e <= until + 1e-6
    return n


def json_text(x):
    import json
    return json.dumps(x, ensure_ascii=False)


def t_split_many():
    def prompt(c):
        return (f"Using the video editor tools: make a 1920x1080 25 fps highlights video from the talk {c['clips']['C']['abs']} (C): 24 clips of exactly 4 seconds each, the k-th one "
                f"starting at second 6*k of the talk (k = 0 to 23), joined with plain cuts, no transitions, no text. Do not export. Say the total length of the video.")

    def check(s, c):
        order = sorted(s["layout"]["entries"], key=lambda e: e["start"])
        ok_in = [near(e["in"], 6.0 * i, 0.05) for i, e in enumerate(order)]
        return [("24_clips", len(order) == 24, len(order)), ("each_4s", all(near(e["dur"], 4.0, 0.05) for e in order), [e["dur"] for e in order if not near(e["dur"], 4.0, 0.05)]),
                ("starts_at_6k", len(order) == 24 and all(ok_in), [i for i, o in enumerate(ok_in) if not o]),
                ("total_96s", near(s["layout"]["total"], 96.0, 0.1), s["layout"]["total"]),
                ("no_transitions_or_overlays", not s["layout"]["xfades"] and not s["layout"]["layers"], (s["layout"]["xfades"], len(s["layout"]["layers"])))]
    ref = ("server.new_project(1920,1080,25);server.import_clip(P['C'],'C')\n[server.add_clip('C',6*k,6*k+4) for k in range(24)]")
    return T("split_many", prompt, check, ref)


def t_music_ducking():
    def prompt(c):
        return (f"Using the video editor tools: make a 1920x1080 25 fps video from seconds 30-60 of the talk {c['clips']['C']['abs']} (C), with calm background music from the "
                f"built-in library under the whole video, quiet enough that the voice stays clear, and fading out at the end. Export a draft file to {{out}} and say which "
                f"track you used.")

    def check(s, c):
        au = s["layout"]["audios"]
        ex = s["exports"][0] if s["exports"] else {}
        return ([("music_track_present", len(au) >= 1, len(s["layout"]["audios"])),
                 ("music_ducks_under_voice", bool(au) and bool(au[0].get("duck")), [a.get("duck") for a in au]),
                 ("music_fades_out", bool(au) and (au[0].get("fade_out") or 0) > 0, [a.get("fade_out") for a in au]),
                 ("music_covers_video", bool(au) and au[0].get("start", 0) <= 0.5 and au[0].get("dur_eff", au[0].get("dur", 0)) >= 28.0, [(a.get("start"), a.get("dur")) for a in au]),
                 ("voice_still_audible_in_mix", ex.get("lufs") is not None and ex["lufs"] > -30, ex.get("lufs"))] + export_ok(s, 30.0, size=(1920, 1080), fps=25))
    ref = ("server.new_project(1920,1080,25);server.import_clip(P['C'],'C');server.add_clip('C',30,60);server.add_audio(0,30,asset='m-carefree',duck_auto=True,fade_out_s=2)\n"
           "server.export(OUT,'draft')")
    return T("music_ducking", prompt, check, ref, out=True)


def t_finished_edit():
    def prompt(c):
        return ("Using the video editor tools: the project is already built (look at it first). Shorten the first clip (C) so that it is only 5 seconds long. The lower third and the "
                "text 'Dato' were placed over specific moments of the second clip (K): after your change they must still appear at those same moments of K (not at the "
                "same clock time). Do not rebuild the project from scratch and do not export. Finish by saying what you changed.")

    def check(s, c):
        k, cc = ent(s, "K"), ent(s, "C")
        lt = graphics(s, "lower_third")
        t = [x for x in texts(s) if "Dato" in x.get("text", "")]
        return [("C_is_5s", len(cc) == 1 and near(cc[0]["dur"], 5.0), [x["dur"] for x in cc]), ("K_untouched_8s", len(k) == 1 and near(k[0]["dur"], 8.0), [x["dur"] for x in k]),
                ("lower_third_1s_into_K", len(lt) == 1 and len(k) == 1 and near(lt[0]["start"], k[0]["start"] + 1.0), (lt[0]["start"] if lt else None, k[0]["start"] if k else None)),
                ("text_2_5s_into_K", len(t) == 1 and len(k) == 1 and near(t[0]["start"], k[0]["start"] + 2.5), (t[0]["start"] if t else None, k[0]["start"] if k else None)),
                ("project_not_rebuilt", s["project"]["revision"] >= 9 and len(s["layout"]["entries"]) == 2, s["project"]["revision"])]
    prep = ("server.new_project(1920,1080,25);server.import_clip(P['C'],'C');server.import_clip(P['K'],'K');server.add_clip('C',0,8);server.add_clip('K',0,8);server.crossfade(0,0.5)\n"
            "server.add_lower_third('Ana Ruiz','Directora',8.5,3.0);server.add_text('Dato',10.0,1.5)")
    return T("finished_edit", prompt, check, ref="server.trim_clip(0,0,5)", prep=prep, kind="modify")


def t_mixed_formats():
    def prompt(c):
        return (f"Using the video editor tools: make a landscape 1920x1080 25 fps video that joins three clips of different formats, 3 seconds of each, in this order: "
                f"{c['clips']['R2']['abs']} (R2, a low resolution vertical phone clip), {c['clips']['R3']['abs']} (R3, vertical HEVC) and {c['clips']['K']['abs']} (K, 50 fps), with "
                f"0.5 s crossfades. Vertical clips must not look stretched. Export a draft file to {{out}}, check the result and say how long it is.")

    def check(s, c):
        order = sorted(s["layout"]["entries"], key=lambda e: e["start"])
        return ([("order_R2_R3_K", [e["src"] for e in order] == ["R2", "R3", "K"], [e["src"] for e in order]),
                 ("each_3s", all(near(e["dur"], 3.0) for e in order), [e["dur"] for e in order])] + export_ok(s, 8.0, size=(1920, 1080), fps=25))
    ref = ("server.new_project(1920,1080,25);[server.import_clip(P[k],k) for k in ('R2','R3','K')];[server.add_clip(k,0,3) for k in ('R2','R3','K')]\n"
           "server.crossfade(0,0.5);server.crossfade(1,0.5);server.export(OUT,'draft')")
    return T("mixed_formats", prompt, check, ref, out=True)


def t_cards_theme():
    def prompt(c):
        return (f"Using the video editor tools: make a 1920x1080 25 fps explainer with the 'corporate' look: a 3 s title card 'Resumen del trimestre', then seconds 0-8 of "
                f"{c['clips']['K']['abs']} (K), then a 4 s closing card that says 'Gracias'. Export a draft file to {{out}} and say how long it is.")

    def check(s, c):
        return ([("theme_corporate", s["project"]["theme"] == "corporate", s["project"]["theme"]),
                 ("two_cards_3s_then_4s", [round(e["dur"]) for e in cards(s)] == [3, 4], [(e["start"], e["dur"]) for e in cards(s)]),
                 ("title_first_closing_last", len(cards(s)) == 2 and cards(s)[0]["start"] <= 0.05 and cards(s)[1]["start"] >= 10.0, [e["start"] for e in cards(s)]),
                 ("K_8s", len(ent(s, "K")) == 1 and near(ent(s, "K")[0]["dur"], 8.0), [e["dur"] for e in ent(s, "K")])] + export_ok(s, 15.0, tol=0.5, size=(1920, 1080), fps=25))
    ref = ("server.new_project(1920,1080,25);server.set_template('corporate');server.import_clip(P['K'],'K');server.add_card('title',title='Resumen del trimestre',dur_s=3.0)\n"
           "server.add_clip('K',0,8);server.add_card('outro',title='Gracias',dur_s=4.0);server.export(OUT,'draft')")
    return T("cards_theme", prompt, check, ref, out=True)


def t_pip():
    def prompt(c):
        return (f"Using the video editor tools: make a 1920x1080 25 fps video of seconds 10-30 of the talk {c['clips']['C']['abs']} (C), with the clip {c['clips']['K']['abs']} (K) "
                f"as a small picture-in-picture in the bottom right corner from the start and for the whole 20 seconds. Check with a contact sheet that the small picture "
                f"does not cover the faces or text. Do not export. Say where the small picture is and how big.")

    def check(s, c):
        pip = [x for x in s["layout"]["layers"] if x["kind"] == "pip"]
        return [("main_clip_20s", len(ent(s, "C")) == 1 and near(ent(s, "C")[0]["in"], 10.0) and near(ent(s, "C")[0]["dur"], 20.0), [(e["in"], e["dur"]) for e in ent(s, "C")]),
                ("one_pip_of_K", len(pip) == 1 and pip[0].get("src") == "K", [(p.get("src"), p.get("pos")) for p in pip]),
                ("pip_whole_video", len(pip) == 1 and pip[0]["start"] <= 0.1 and near(pip[0]["dur"], 20.0, 0.2), [(p["start"], p["dur"]) for p in pip]),
                ("pip_bottom_right", len(pip) == 1 and pip[0].get("pos") == "bottom-right", [p.get("pos") for p in pip])]
    ref = "server.new_project(1920,1080,25);server.import_clip(P['C'],'C');server.import_clip(P['K'],'K');server.add_clip('C',10,30);server.add_pip('K',0,20,position='bottom-right',scale=0.25)"
    return T("pip", prompt, check, ref)


TASKS = {t["name"]: t for t in (f() for f in (t_montage3, t_interview_lt, t_subtitles_srt, t_vertical_reel, t_cut_under_overlays, t_long_edit, t_split_many, t_music_ducking,
                                              t_finished_edit, t_mixed_formats, t_cards_theme, t_pip))}
