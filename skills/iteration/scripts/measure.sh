#!/bin/bash
# measure.sh <bench-binary> [procs=5] [iters=10] [flush=1]
#
# Repeat matrix for one binary: N independent processes x ITERS timed launches,
# per-process medians plus a pooled median with the per-process first sample
# dropped. Prints one summary line and one accuracy line.
#
# Those two lines are the judging surface. Single [timing] values are not.
set -uo pipefail

BIN=${1:?usage: measure.sh <bench-binary> [procs] [iters] [flush]}
PROCS=${2:-5}
ITERS=${3:-10}
FLUSH=${4:-1}
[ -x "$BIN" ] || { echo "measure.sh: not executable: $BIN" >&2; exit 2; }

# The bench binary links the CANN runtime, so it only runs with the environment
# sourced; a login shell does not have it.
: "${ASCEND_HOME_PATH:=/home/developer/Ascend/cann-9.3.0}"
export ASCEND_HOME_PATH
if [ -f "$ASCEND_HOME_PATH/set_env.sh" ]; then
    # shellcheck disable=SC1090
    source "$ASCEND_HOME_PATH/set_env.sh" >/dev/null 2>&1
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
acc=""
for i in $(seq 1 "$PROCS"); do
    out=$(WARMUP=${WARMUP:-5} ITERS="$ITERS" FLUSH="$FLUSH" "$BIN" 2>&1)
    echo "$out" | grep -o '\[timing\].*' | sed 's/\[timing\] //' > "$TMP/r$i"
    acc=$(echo "$out" | grep -o 'accuracy.*' | head -1)
    if [ ! -s "$TMP/r$i" ]; then
        echo "measure.sh: run $i produced no timing line" >&2
        printf '%s\n' "$out" | tail -n 5 >&2
        exit 1
    fi
done

python3 - "$TMP" "$PROCS" "$acc" <<'PY'
import statistics as st, sys, glob, os

tmp, procs, acc = sys.argv[1], int(sys.argv[2]), sys.argv[3]
rows = [[float(x) for x in open(f).read().split()] for f in sorted(glob.glob(os.path.join(tmp, "r*")))]
meds = [st.median(r) for r in rows]
# Every process pays its own first-launch cost (TLB / I-cache fill); that is not
# signal, so the pooled statistic keeps only samples 2..N of each process.
pool = sorted(v for r in rows for v in r[1:])
spread = (max(meds) - min(meds)) / st.median(meds) * 100 if st.median(meds) else 0.0
print(f"procs={procs} per-proc-median={[round(m, 2) for m in meds]} "
      f"median={st.median(pool):.3f} min={pool[0]:.2f} max={pool[-1]:.2f} "
      f"cross-proc-spread={spread:.1f}% n={len(pool)}")
print(acc if acc else "accuracy: missing")
PY
