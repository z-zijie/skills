#!/bin/bash
# evaluate.sh <op_id> <project_dir> [tag] [case_spec]
#
# Builds + evaluates one operator and prints one JSON record.
#
# The full `run_evaluation.sh` fans its correctness and batch-performance
# stages out to several worker processes. This entry instead runs exactly one
# case per OS process through the harness's own `eval-child`, so accuracy,
# timing and the Eq.3 per-case score still come from the official code.
#
# No NPU lock here: the driver queues kernels in task-submission order. Running
# two evaluations at once is safe, but each one's kernel time is then inflated
# by the other's queueing, so compare performance numbers one run at a time.
#
# case_spec (default "1"):
#   1        one representative case -- the fast accept/reject signal
#   1,2,5    a small explicit subset
#   all      every case, still one process per case; only this yields a score
#            directly comparable with the leaderboard
#
# The JSON line keeps the historical shape (op/rc/score/pass_rate/avg_speedup)
# so existing readers of state/scores.jsonl keep working. `score` is null for a
# sampled subset, because a partial pass cannot produce a real operator score.
set -uo pipefail
source /workspace/cannbench/loop/scripts/lib.sh

OP=${1:?op}
SRC=${2:?src}
TAG=${3:-loop}
CASES=${4:-1}

RUN=$ROOT/reports/${TAG}_${OP}_$(date +%Y%m%d_%H%M%S)
mkdir -p "$RUN"

OUT=$RUN/eval.out
"$ROOT/loop/scripts/percase_eval.sh" "$OP" "$SRC" "$CASES" "$TAG" > "$OUT" 2>&1
RC=$?

python3 - "$OP" "$SRC" "$RC" "$RUN" "$OUT" "$CASES" <<'PY'
import json, os, sys, datetime
op, src, rc, run, out_path, cases = (sys.argv[1], sys.argv[2], int(sys.argv[3]),
                                     sys.argv[4], sys.argv[5], sys.argv[6])
rec = {"op": op, "src": src, "rc": rc, "run": run, "cases": cases,
       "at": datetime.datetime.now().isoformat(timespec="seconds"),
       "score": None, "pass_rate": None, "avg_speedup": None, "operators": []}

lines = []
if os.path.exists(out_path):
    lines = [l.strip() for l in open(out_path) if l.strip().startswith("{")]
rows = []
for line in lines:
    try:
        d = json.loads(line)
    except Exception:
        continue
    if "operator" in d:
        rows.append(d)

per_case = [r for r in rows if r.get("perf_score") is not None]
if rows:
    rec["pass_rate"] = sum(1 for r in rows if r.get("ok")) / len(rows)
    if per_case:
        rec["avg_speedup"] = sum(r.get("speedup") or 0 for r in per_case) / len(per_case)

agg = next((json.loads(l) for l in reversed(lines) if l.startswith('{"op"')), None)
if agg:
    rec["partial"] = not agg.get("full_operator")
    if agg.get("full_operator"):
        # Every case ran: harness-weighted operator score.
        rec["score"] = agg.get("projected_operator_score")
    rec["mean_perf_score"] = agg.get("mean_perf_score")
    rec["n_cases"] = agg.get("n_cases")
    if rows:
        rec["operators"] = [{
            "operator": rows[0].get("operator"),
            "score": rec["score"],
            "passed": sum(1 for r in rows if r.get("ok")),
            "total": len(rows),
            "avg_speedup": rec["avg_speedup"],
        }]

with open("/workspace/cannbench/state/scores.jsonl", "a") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
rec.setdefault("partial", True)
print(json.dumps({k: rec.get(k) for k in
                  ("op", "rc", "score", "pass_rate", "avg_speedup", "partial")},
                 ensure_ascii=False))
PY
