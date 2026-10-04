#!/usr/bin/env python3
"""Pre-download assets from the R2 gateway into assets_cache/ (they are also fetched lazily on first use).
  R2_WORKER_URL=... R2_UPLOAD_TOKEN=... python3 fetch_assets.py [--all | --kind music|sfx | id ...]   Every file is SHA-256 verified."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assets_lib

if __name__ == "__main__":
    args = sys.argv[1:]
    items = assets_lib.manifest()["assets"]
    if "--kind" in args:
        k = args[args.index("--kind") + 1]; ids = [i["id"] for i in items if i["kind"] == k]
    elif "--all" in args or not args:
        ids = [i["id"] for i in items if i["kind"] in ("music", "sfx")]
    else:
        ids = args
    bad = 0
    for aid in ids:
        try:
            print("ok  ", aid, assets_lib.path(aid))
        except Exception as e:
            bad += 1; print("FAIL", aid, e)
    print(f"{len(ids) - bad}/{len(ids)} assets ready in {assets_lib.CACHE}")
    sys.exit(1 if bad else 0)
