#!/usr/bin/env python3
"""Write LICENSES.md from the asset manifest (audio) plus the notes for icons and fonts.
Usage: tools/make_licenses.py [--manifest FILE] [--out FILE] [--notes FILE]   (defaults: the assets_manifest setting, LICENSES.md beside it,
data/licence_notes.json)"""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mltedit.config import S  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write LICENSES.md from the asset manifest.")
    ap.add_argument("--manifest", default=S.assets_manifest)
    ap.add_argument("--out", default="", help="default: LICENSES.md next to the manifest")
    ap.add_argument("--notes", default=os.path.join(S.data_root, "data", "licence_notes.json"), help="JSON with the intro lines and the icon/font notes")
    a = ap.parse_args(argv)
    out_path = a.out or os.path.join(os.path.dirname(a.manifest), "LICENSES.md")
    notes = json.load(open(a.notes, encoding="utf-8"))
    items = json.load(open(a.manifest, encoding="utf-8"))["assets"]
    man = os.path.join(os.path.basename(os.path.dirname(a.manifest)), os.path.basename(a.manifest))
    out = [notes["title"], "",
           notes["intro"].format(tool="tools/make_licenses.py", manifest=man), ""] + [f"* {n}" for n in notes["notes"]] + [
           ""]
    for kind, title in (("music", "Music"), ("sfx", "Sound effects")):
        out += [f"## {title}", "", "| id | title | author | licence | source |", "|---|---|---|---|---|"]
        for it in sorted((i for i in items if i["kind"] == kind), key=lambda i: (i["license"], i["id"])):
            out.append(f"| {it['id']} | {it['title']} | {it['author']} | {it['license']} | {it.get('page_url') or it['source_url']} |")
        out.append("")
    open(out_path, "w", encoding="utf-8").write("\n".join(out))
    print(out_path + ":", len(items), "assets")


if __name__ == "__main__":
    main()
