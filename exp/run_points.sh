#!/bin/bash
# Point-by-point sweep in randomized order, each point a fresh process.
# This rules out the ordering artefact that showed up between n=16 and n=24 when
# all points shared one process (and therefore one rotating source window).
#
# Usage: run_points.sh <out.csv> <reps> [blocks] [order-seed]
set -u
OUT=${1:-/root/noop-mte2-bus/points.csv}
REPS=${2:-20}
BLOCKS=${3:-56}
SEED=${4:-12345}
BIN=${BIN:-/root/noop-mte2-bus/bench3}
cd /root/noop-mte2-bus || exit 1
source /home/developer/Ascend/cann-9.3.0/set_env.sh >/dev/null 2>&1

NOPS="0 4 8 16 24 32 48 64 80 96 112 128 144 160 176 192 208 224 240 256 272 288 304 320 352 384 400 416 448"

# build the point list and shuffle it reproducibly
TMP=$(mktemp)
for n in $NOPS; do
    echo "1 $n 102400 0" >> "$TMP"   # two copies
    echo "0 $n 0 0"      >> "$TMP"   # one copy
done
for c in 1024 4096 16384 32768 51200 76800 102400; do
    echo "2 0 $c 0" >> "$TMP"        # ablation
done
echo "4 0 204800 0" >> "$TMP"
for n in 0 32 64 128 256; do
    echo "3 $n 102400 8" >> "$TMP"   # pipeline
done
for n in 0 32 64 128; do
    echo "5 $n 102400 0" >> "$TMP"   # three-way
done
for n in 0 64 256 1024; do
    echo "6 $n 0 0" >> "$TMP"        # clock ladder
done

# deterministic shuffle (awk rand with fixed seed)
awk -v seed="$SEED" 'BEGIN{srand(seed)} {print rand() "\t" $0}' "$TMP" | sort -n | cut -f2- > "${TMP}.shuf"

echo "launch,idx,point,mode,nops,copybytes,tiles,blocks,core,t_begin,t_issue1,t_end1,t_issue2,t_end2" > "$OUT"
i=0
while read -r mode nops cbytes tiles; do
    [ -z "${mode:-}" ] && continue
    part=$(mktemp)
    "$BIN" pointcsv "$part" "$mode" "$nops" "$cbytes" "$tiles" "$BLOCKS" "$REPS" >/dev/null 2>&1
    # renumber launch ids so every point keeps its own id range
    awk -F, -v base="$((i * REPS))" -v idx="$i" -v m="$mode" -v n="$nops" -v c="$cbytes" \
        -v t="$tiles" -v b="$BLOCKS" '
        /^#/ {next}
        NR==1 {next}
        { $1 = $1 + base; $2 = idx; $3 = idx; $4 = m; $5 = n; $6 = c; $7 = t; $8 = b; print $0 }' \
        OFS=, "$part" >> "$OUT"
    rm -f "$part"
    i=$((i + 1))
    echo "  [$i] mode=$mode n=$nops c=$cbytes K=$tiles"
done < "${TMP}.shuf"
rm -f "$TMP" "${TMP}.shuf"
echo "done -> $OUT"
