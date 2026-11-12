#!/bin/bash
# lint_rtl.sh -- 用 Verilator 对 rtl/ 全量做语法/语义检查 (只 lint 不综合)
# 用法: bash flow/lint_rtl.sh            顶层 = epv32_soc (含 EPV)
#       bash flow/lint_rtl.sh --no-epv   额外跑 EPV_ENABLE=0 分支
set -u
export PATH="/e/msys64/ucrt64/bin:/e/msys64/usr/bin:$PATH"
export VERILATOR_ROOT="E:/msys64/ucrt64/share/verilator"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/results/lint"
mkdir -p "$OUT"
RTL=$(ls "$ROOT"/rtl/*.v)

verilator_bin.exe --lint-only -Wall -Wno-UNUSED -Wno-DECLFILENAME -Wno-fatal \
  -I"$ROOT/rtl" --top-module epv32_core $RTL > "$OUT/lint_core.log" 2>&1
rc_core=$?

verilator_bin.exe --lint-only -Wall -Wno-UNUSED -Wno-DECLFILENAME -Wno-fatal \
  -I"$ROOT/rtl" --top-module epv32_core -GEPV_ENABLE=0 $RTL > "$OUT/lint_noepv.log" 2>&1
rc_noepv=$?

echo "LINT_CORE_RC=$rc_core"
echo "LINT_NOEPV_RC=$rc_noepv"
if [ "${1:-}" != "--quiet" ]; then
  sed -n '1,80p' "$OUT/lint_core.log"
fi
[ $rc_core -eq 0 ]
