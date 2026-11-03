#!/bin/bash
# eval_op.sh <op_id> <source_dir> [tag]
#
# Evaluate one operator and record its score. Thin alias over the per-case path:
# this used to call the full run_evaluation.sh harness, whose per-case fan-out is
# what wedges this single-card box, so it now delegates to evaluate.sh with every
# case run one process at a time.
set -uo pipefail
ROOT=/workspace/cannbench
OP=${1:?op}
SRC=${2:?src}
TAG=${3:-adhoc}

exec "$ROOT/loop/scripts/evaluate.sh" "$OP" "$SRC" "$TAG" all
