#!/bin/bash
# Collect msprof kernel duration + AIV pipe breakdown, one row per kernel launch.
# The previous version summed the launches; this one keeps each launch separate so
# the analysis can take a median and reject the warm-up launch.
#
# Usage: run_prof.sh <out.csv>
set -u
OUT=${1:-/root/noop-mte2-bus/prof_summary.csv}
BIN=${2:-/root/noop-mte2-bus/bench2}
cd /root/noop-mte2-bus || exit 1
source /home/developer/Ascend/cann-9.3.0/set_env.sh >/dev/null 2>&1

# mode nops copybytes tiles reps blocks
POINTS="
1 0 102400 0 5 56
1 16 102400 0 5 56
1 32 102400 0 5 56
1 64 102400 0 5 56
1 96 102400 0 5 56
1 128 102400 0 5 56
1 160 102400 0 5 56
1 192 102400 0 5 56
1 224 102400 0 5 56
1 256 102400 0 5 56
1 288 102400 0 5 56
1 320 102400 0 5 56
1 384 102400 0 5 56
1 448 102400 0 5 56
1 512 102400 0 5 56
1 1024 102400 0 5 56
0 0 0 0 5 56
0 64 0 0 5 56
0 256 0 0 5 56
0 1024 0 0 5 56
4 0 204800 0 5 56
3 0 102400 8 5 56
3 64 102400 8 5 56
3 256 102400 8 5 56
6 0 0 0 5 56
6 256 0 0 5 56
6 1024 0 0 5 56
1 0 102400 0 5 14
1 256 102400 0 5 14
1 0 102400 0 5 28
1 256 102400 0 5 28
"

echo "mode,nops,copybytes,tiles,blocks,launch_idx,kernel_us,aiv_time_us,aiv_vec_us,aiv_scalar_us,aiv_mte2_us,aiv_mte2_ratio" > "$OUT"

echo "$POINTS" | while read -r mode nops cbytes tiles reps blocks; do
    [ -z "${mode:-}" ] && continue
    dir=/root/noop-mte2-bus/pr_${mode}_${nops}_${tiles}_${blocks}
    rm -rf "$dir"; mkdir -p "$dir"
    msprof --output="$dir" --python-path=/usr/bin/python3.12 --ai-core=on \
        --aic-metrics=PipeUtilization --task-time=l1 --runtime-api=on --ascendcl=on --parse=on \
        "$BIN" point "$mode" "$nops" "$cbytes" "$tiles" "$reps" "$blocks" >/dev/null 2>&1
    f=$(find "$dir" -name 'op_summary_*.csv' | head -1)
    if [ -z "$f" ]; then
        echo "$mode,$nops,$cbytes,$tiles,$blocks,NA,NA,NA,NA,NA,NA,NA" >> "$OUT"
        echo "  mode=$mode nops=$nops blocks=$blocks -> NO DATA"
        continue
    fi
    # Keep every AI_VECTOR_CORE launch as its own row (column 10 = Task Duration).
    awk -F, -v m="$mode" -v n="$nops" -v c="$cbytes" -v t="$tiles" -v b="$blocks" '
        NR>1 && $8=="AI_VECTOR_CORE" {
            d = $10; at = $37; av = $39; asc = $41; am = $43; ar = $44
            gsub(/[ \t]/, "", d); gsub(/[ \t]/, "", at); gsub(/[ \t]/, "", av)
            gsub(/[ \t]/, "", asc); gsub(/[ \t]/, "", am); gsub(/[ \t]/, "", ar)
            printf "%s,%s,%s,%s,%s,%d,%s,%s,%s,%s,%s,%s\n", m, n, c, t, b, ++k, d, at, av, asc, am, ar
        }' "$f" >> "$OUT"
    n_rows=$(awk -F, -v m="$mode" -v n="$nops" -v c="$cbytes" -v t="$tiles" -v b="$blocks" \
        'NR>1 && $1==m && $2==n && $3==c && $4==t && $5==b' "$OUT" | wc -l)
    echo "  mode=$mode nops=$nops blocks=$blocks -> $n_rows launches"
    rm -rf "$dir"
done
echo "done -> $OUT"
