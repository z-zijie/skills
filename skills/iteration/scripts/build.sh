#!/bin/bash
# build.sh <bench.asc> <out-binary> [-Dflags...]
#
# One bisheng invocation. No wheel, no pip, no python.
# Sets up ASCEND_HOME_PATH + bisheng on PATH, and turns the one failure mode that
# reports success (missing ASCEND_HOME_PATH exits 0) into a real exit code.
set -uo pipefail

SRC=${1:?usage: build.sh <bench.asc> <out-binary> [-Dflags...]}
OUT=${2:?usage: build.sh <bench.asc> <out-binary> [-Dflags...]}
shift 2

: "${ASCEND_HOME_PATH:=/home/developer/Ascend/cann-9.3.0}"
export ASCEND_HOME_PATH
if [ -f "$ASCEND_HOME_PATH/set_env.sh" ]; then
    # shellcheck disable=SC1090
    source "$ASCEND_HOME_PATH/set_env.sh" >/dev/null 2>&1
fi
export PATH="$ASCEND_HOME_PATH/tools/bisheng_compiler/bin:$PATH"

command -v bisheng >/dev/null || { echo "build.sh: bisheng not found under $ASCEND_HOME_PATH" >&2; exit 2; }

err=$(mktemp)
trap 'rm -f "$err"' EXIT

# bisheng exits 0 even when it cannot find ASCEND_HOME_PATH, so the product is
# the verdict, not the exit code.
bisheng -fPIC --npu-arch=dav-3510 -O3 --asc-aicore-lang "$@" \
    "$SRC" -o "$OUT" -lm -ldl -lplatform -ltiling_api -lmsprofiler 2>"$err"

if [ ! -x "$OUT" ]; then
    echo "build.sh: build failed (no product at $OUT)" >&2
    grep -E "error" "$err" | head -n 8 >&2
    exit 1
fi
grep -E "error" "$err" >&2 || true
echo "ok $OUT"
