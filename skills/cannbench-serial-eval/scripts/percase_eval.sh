#!/bin/bash
# percase_eval.sh <op_id> <project_dir> [cases] [tag]
#
# One operator, a bounded set of cases, one OS process per case. The full
# `run_evaluation.sh` fans its correctness and batch-performance stages out to
# several processes; this path keeps one case per process so the per-case
# numbers match the official single-case entry.
#
# cases:
#   "1"        one case
#   "1,2,5"    those cases
#   "all"      every case of the operator, still one process at a time
#   omitted    case 1
#
# Builds and installs the project wheel first (a submission wheel registers only
# its own operator, so an unbuilt tree cannot be evaluated), then hands the case
# list to percase.py, which drives the official `kernel_eval.cli eval-child`
# path so accuracy, timing and the per-case Eq.3 score all come from the harness
# itself rather than a lookalike metric.
#
# No NPU lock: the driver queues kernels in submission order, so a concurrent
# caller cannot corrupt this run. It does inflate the measured kernel time, so
# only one sweep should be measuring performance at any moment.
#
# Prints one JSON line per case plus one aggregate line, and appends every case
# record to state/percase_cases.jsonl.
set -uo pipefail
ROOT=/workspace/cannbench
source "$ROOT/loop/scripts/lib.sh"

OP=${1:?op}
SRC=${2:?src}
CASES=${3:-1}
TAG=${4:-pc}

PATH_REL=$(op_path "$OP")
[ -z "$PATH_REL" ] && { echo '{"error":"unknown op"}'; exit 2; }

RUN=$ROOT/reports/${TAG}_${OP}_$(date +%Y%m%d_%H%M%S)
mkdir -p "$RUN"

emit_fail() {
  local stage=$1 rc=${2:-1}
  echo "{\"op\":\"$OP\",\"ok\":false,\"cases\":\"$CASES\",\"stage\":\"$stage\",\"rc\":$rc}"
  printf '{"op":"%s","stage":"%s","rc":%s,"run":"%s","at":"%s"}\n' \
    "$OP" "$stage" "$rc" "$RUN" "$(date -Iseconds)" >> "$ROOT/state/percase_failures.jsonl"
}

if [ "${PC_SKIP_BUILD:-0}" != "1" ]; then
  # The container exposes 128 cores but is quota-limited to 32, so an
  # os.cpu_count()-driven build fans out 4x past the quota and the compiler
  # processes exhaust the 32 GB cgroup (that is what killed earlier sweeps).
  # Cap the fan-out; a single operator's build does not need more.
  ( cd "$SRC" && CNB_CMAKE_JOBS="${CNB_CMAKE_JOBS:-8}" \
      CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL:-8}" \
      MAX_JOBS="${MAX_JOBS:-8}" \
      "$ROOT/bin/cnb-env" bash build.sh ) > "$RUN/build.log" 2>&1
  BRC=$?
  if [ $BRC -ne 0 ]; then emit_fail build $BRC; exit 0; fi
fi

WHL=$(ls -t "$SRC"/dist/cann_bench-*.whl 2>/dev/null | head -1)
if [ -z "$WHL" ]; then emit_fail wheel; exit 0; fi

if [ "${PC_SKIP_INSTALL:-0}" != "1" ]; then
  "$ROOT/bin/cnb-env" "$ROOT/evalenv/bin/pip" install \
      --force-reinstall --no-deps "$WHL" > "$RUN/pip.log" 2>&1 \
    || { emit_fail install; exit 0; }
fi

if [ "$CASES" = "all" ]; then
  CASE_ARGS=(--all)
else
  CASE_ARGS=()
  IFS=',' read -r -a CASE_LIST <<< "$CASES"
  for c in "${CASE_LIST[@]}"; do CASE_ARGS+=(--case "$c"); done
fi

OUT=$RUN/percase.out
"$ROOT/bin/cnb-env" "$ROOT/evalenv/bin/python" \
    "$ROOT/loop/scripts/percase.py" --op "$OP" "${CASE_ARGS[@]}" --out-dir "$RUN" \
    > "$OUT" 2>"$RUN/percase.err"
RC=$?

# Every record line starts with {"operator"; the last {"op" line is the aggregate.
# percase.py also writes results.json, so a killed sweep still leaves the cases
# it already measured instead of losing them with the process.
grep -E '^\{"operator"' "$OUT" 2>/dev/null > "$RUN/cases.jsonl" || true
if [ -s "$RUN/results.json" ]; then
  python3 - "$RUN" <<'PY2' >> "$RUN/cases.jsonl"
import json, sys
for r in json.load(open(sys.argv[1] + "/results.json")):
    print(json.dumps(r, ensure_ascii=False))
PY2
fi
# Dedupe (percase.out and results.json overlap) without reordering: the loop
# reads these in case order.
awk '!seen[$0]++' "$RUN/cases.jsonl" > "$RUN/cases.dedup" && mv "$RUN/cases.dedup" "$RUN/cases.jsonl"
cat "$RUN/cases.jsonl" 2>/dev/null
grep -E '^\{"op"' "$OUT" 2>/dev/null | tail -1 | tee "$RUN/projection.json"
echo
if [ -s "$RUN/cases.jsonl" ] && [ ! -s "$RUN/projection.json" ]; then
  # The sweep died partway (e.g. OOM-killed). Say so; the measured cases are
  # still on disk for the loop to compare against.
  printf '{"op":"%s","full_operator":false,"partial":true,"n_cases":%s,"killed":true}\n' \
    "$OP" "$(wc -l < "$RUN/cases.jsonl")" | tee "$RUN/projection.json"
fi
[ -s "$RUN/cases.jsonl" ] || { emit_fail run $RC; exit 0; }

python3 - "$OP" "$SRC" "$RUN" <<'PY' >> "$ROOT/state/percase_cases.jsonl"
import json, os, sys, datetime
op, src, run = sys.argv[1], sys.argv[2], sys.argv[3]
path = os.path.join(run, "cases.jsonl")
if os.path.exists(path):
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        r["op"] = op
        r["src"] = src
        r["run"] = run
        r["at"] = datetime.datetime.now().isoformat(timespec="seconds")
        print(json.dumps(r, ensure_ascii=False))
PY
exit 0
