#!/bin/bash
# Parameter sweep for the MTE2 queueing model.
#
# Each point is one process: K back-to-back DataCopyPads of S bytes on B cores.
# Every core also gets its own 1MB source slice from a rotating window, so all
# copies are cold HBM reads.
#
# Usage: run_model.sh <out.csv> <tag>
#   tag = train | valid
set -u
OUT=${1:-/root/noop-mte2-bus/model.csv}
TAG=${2:-train}
BIN=/root/noop-mte2-bus/mte2_model
REPS=${REPS:-8}
cd /root/noop-mte2-bus || exit 1
source /home/developer/Ascend/cann-9.3.0/set_env.sh >/dev/null 2>&1

# (K, S, B) triples. Constraint: K*S <= 200KB (UB 248KB, minus stamp buffer).
if [ "$TAG" = "valid" ]; then
    # held-out combinations, deliberately not on the grids used for fitting
    POINTS="
2 61440 56
3 40960 56
2 24576 42
4 20480 28
6 16384 42
3 81920 28
1 131072 21
5 32768 56
2 16384 14
8 20480 56
1 8192 56
4 49152 14
"
else
    POINTS="
1 102400 7
1 102400 14
1 102400 21
1 102400 28
1 102400 42
1 102400 56
2 102400 7
2 102400 14
2 102400 28
2 102400 42
2 102400 56
1 65536 56
2 65536 56
3 65536 56
1 32768 56
2 32768 56
3 32768 56
4 32768 56
6 32768 56
1 16384 56
2 16384 56
4 16384 56
6 16384 56
8 16384 56
1 8192 56
4 8192 56
8 8192 56
4 32768 14
4 32768 28
8 16384 14
8 16384 28
2 65536 28
3 65536 14
"
fi

echo "launch,core,t_begin,t_issue0,t_issue1,t_issue2,t_issue3,t_issue4,t_issue5,t_issue6,t_issue7,t_done0,t_done1,t_done2,t_done3,t_done4,t_done5,t_done6,t_done7,K,S,B" > "$OUT"
li=0
# deterministic shuffle so points are visited in a different order than the CSV lists them
case "$TAG" in
    valid) SEED=777 ;;
    *)     SEED=4242 ;;
esac
SHUF=$(echo "$POINTS" | awk -v seed="$SEED" 'BEGIN{srand(seed)}{if(NF==3)print rand() "\t" $0}' | sort -n | cut -f2-)

echo "$SHUF" | while read -r K S B; do
    [ -z "${K:-}" ] && continue
    part=$(mktemp)
    MTE2_OFFSET_SLOT=$(( (li * 7) % 24 )) "$BIN" pointcsv "$part" "$K" "$S" "$B" "$REPS" >/dev/null 2>&1
    awk -F, -v l="$li" -v k="$K" -v s="$S" -v b="$B" '
        NR==1 {next}
        { printf "%s,%s,%s,%s,%s", $1 + l*1000, $2, $3, $4, $5
          for (i = 6; i <= 19; i++) printf ",%s", $i
          printf ",%s,%s,%s\n", k, s, b }' "$part" >> "$OUT"
    rm -f "$part"
    li=$((li + 1))
    echo "  [$li] K=$K S=$S B=$B"
done
echo "done -> $OUT"
