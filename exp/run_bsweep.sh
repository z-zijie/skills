#!/bin/bash
# Fine sweep over the number of participating AIV cores, to expose how aggregate
# MTE2 bandwidth scales with core count. B=28 vs B=42 was non-monotone in the
# coarse sweep, which points at a topology (port/die) effect rather than pure
# bandwidth sharing.
#
# Usage: run_bsweep.sh <out.csv>
set -u
OUT=${1:-/root/noop-mte2-bus/bsweep.csv}
BIN=${BIN:-/root/noop-mte2-bus/mte2_model2}
REPS=${REPS:-30}
cd /root/noop-mte2-bus || exit 1
source /home/developer/Ascend/cann-9.3.0/set_env.sh >/dev/null 2>&1

echo "launch,core,t_begin,t_issue0,t_issue1,t_issue2,t_issue3,t_issue4,t_issue5,t_issue6,t_issue7,t_done0,t_done1,t_done2,t_done3,t_done4,t_done5,t_done6,t_done7,K,S,B" > "$OUT"
li=0
for S in 102400; do
  for K in 1 2; do
    for B in 1 2 4 7 10 14 18 21 24 28 32 36 40 42 44 48 52 56; do
        part=$(mktemp)
        MTE2_OFFSET_SLOT=$(( (li * 5) % 24 )) "$BIN" pointcsv "$part" "$K" "$S" "$B" "$REPS" >/dev/null 2>&1
        awk -F, -v l="$li" -v k="$K" -v s="$S" -v b="$B" '
            NR==1 {next}
            { printf "%s,%s,%s,%s,%s", $1 + l*1000, $2, $3, $4, $5
              for (i = 6; i <= 19; i++) printf ",%s", $i
              printf ",%s,%s,%s\n", k, s, b }' "$part" >> "$OUT"
        rm -f "$part"
        li=$((li + 1))
        echo "  [$li] K=$K S=$S B=$B"
    done
  done
done
echo "done -> $OUT"
