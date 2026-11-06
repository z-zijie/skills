#!/bin/bash
# Dense (K,S,B) grid: the decisive fix for model accuracy is coverage, not
# model complexity. The hold-out points that dominated the error (S=20/48/60/80KB,
# B=42) were all off-grid, so no interpolator could do better than ~10%.
#
# 12 reps instead of 40 to keep the run bounded; the min-over-launches estimator
# is what the model targets, and with 12 launches it is already stable to ~1.5%.
set -u
OUT=${1:-/root/noop-mte2-bus/dense.csv}
BIN=${BIN:-/root/noop-mte2-bus/mte2_model2}
REPS=${REPS:-12}
cd /root/noop-mte2-bus || exit 1
source /home/developer/Ascend/cann-9.3.0/set_env.sh >/dev/null 2>&1

echo "launch,core,t_begin,t_issue0,t_issue1,t_issue2,t_issue3,t_issue4,t_issue5,t_issue6,t_issue7,t_done0,t_done1,t_done2,t_done3,t_done4,t_done5,t_done6,t_done7,K,S,B" > "$OUT"
li=0
for S in 8192 16384 24576 32768 49152 65536 81920 102400; do
  for K in 1 2 4; do
    [ $((K*S)) -gt 204800 ] && continue
    for B in 14 28 42 56; do
      part=$(mktemp)
      MTE2_OFFSET_SLOT=$(( (li*5) % 20 )) "$BIN" pointcsv "$part" "$K" "$S" "$B" "$REPS" >/dev/null 2>&1
      awk -F, -v l="$li" -v k="$K" -v s="$S" -v b="$B" '
        NR==1{next}
        { printf "%s,%s,%s", $1+l*1000, $2, $3
          for(i=4;i<=19;i++) printf ",%s", $i
          printf ",%s,%s,%s\n", k, s, b }' "$part" >> "$OUT"
      rm -f "$part"; li=$((li+1))
      echo "  [$li] K=$K S=$S B=$B"
    done
  done
done
echo "done -> $OUT"
