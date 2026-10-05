#!/bin/bash
# Everything that must be green before a change is pushed. Run from poc_mlt/:  ./ci.sh   (needs the venv from setup.sh and either a DISPLAY or xvfb-run)
# BENCH=1 ./ci.sh also checks the preview speed against tests_data/bench_baseline.json (slow: renders 720p/1080p/4K).
cd "$(dirname "$0")"
PY=.venv/bin/python
RUN="$PY"; [ -z "$DISPLAY" ] && RUN="xvfb-run -a $PY"
fail=0
# The older suites read the repo's media from temporary project folders, so they run with the path fence off ('*'); test_security clears this and tests the default (fence on).
export MLT_EDITOR_ROOTS='*'
step() { printf '%-22s' "$1:"; shift; out=$("$@" 2>&1); rc=$?; echo "$out" | tail -1; echo "$out" | grep -E "^SKIP" | sed 's/^/                      /'; [ $rc -ne 0 ] && { fail=1; echo "$out" | grep -E "^FAIL|Error|Traceback" | head -5; }; }
step pyflakes            $PY -m pyflakes mltedit *.py tools legacy
step snapshot            $RUN snapshot.py check
step golden              $RUN golden.py
for t in test_anim test_text test_assets test_cards_anim test_transitions test_engine test_modularity test_project_v2 test_determinism test_anchoring test_proxy test_viewer test_qa test_stillqa test_avsync test_until test_card_spec test_jobs_progress test_annotate test_mcp test_security; do
  step $t $RUN $t.py
done
step agent_scenarios     $RUN tools/agent_scenarios.py
[ "$BENCH" = 1 ] && step bench_preview $RUN tools/bench_preview.py --check tests_data/bench_baseline.json
[ $fail -eq 0 ] && echo "ALL GREEN" || { echo "FAILED"; exit 1; }
