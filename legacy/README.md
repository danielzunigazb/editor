# legacy/

Scripts from the first proof of concept and the early benchmarks. They are **not part of the editor** (nothing in `mltedit/` imports
them) and are kept only because REPORT.md cites their measurements.

| script | what it was |
|---|---|
| `poc.py` | the original single-file POC: `gen \| build \| bench \| preview \| export \| measure` |
| `bench_4k.py` | 4K/1080p benchmark through the real MCP server (`--res`, `--media`, `--prefix`, `--out`) |
| `build_demo.py`, `demo_session.py` | the first demo session and its HTML page |
| `stress_1080p.py` | 1080p overlay stress test with raw MLT |

They resolve the repo root as the parent of this folder, so run them from anywhere: `.venv/bin/python legacy/poc.py gen`.
For current tooling see `tools/` (reads its data from `data/*.json` and takes arguments).
