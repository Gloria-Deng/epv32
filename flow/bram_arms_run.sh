#!/usr/bin/env bash
# results/pnr/bram_template_arms/run_arm.sh <arm> [timeout_seconds]
# 单独跑一条 TD 臂：stdout/stderr 进 per-arm 日志，timeout 限时。
# 退出码不是证据（run.exe 写完报告常 SIGSEGV）——证据是 MARK-*/ARTIFACT-* 行与报告文件，由 tools/read_arms.py 读。
set -u
export PYTHONDONTWRITEBYTECODE=1

HERE=$(cd "$(dirname "$0")" && pwd)
ARM="$1"
TMO="${2:-900}"
RUN=/e/Anlogic/TD_6.2.1_Engineer_6.2.168.116/bin/run.exe

D="$HERE/$ARM"
[ -f "$D/$ARM.tcl" ] || { echo "ARM-MISSING-TCL $D/$ARM.tcl"; echo "rc=nomissing" > "$D/$ARM.rc"; exit 4; }
[ -x "$RUN" ] || { echo "ARM-NO-RUNEXE $RUN"; exit 3; }

cd "$D" || exit 5
T0=$(date +%s)
timeout -k 30 "$TMO" "$RUN" -b "$ARM.tcl" > "$ARM.out" 2> "$ARM.err" </dev/null
rc=$?
T1=$(date +%s)
ELAPSED=$((T1 - T0))
echo "ARM_RC $ARM rc=$rc elapsed_s=$ELAPSED limit_s=$TMO start=$(date -u -d "@$T0" +%FT%TZ) end=$(date -u -d "@$T1" +%FT%TZ)"
{
  echo "arm=$ARM"
  echo "rc=$rc"
  echo "elapsed_s=$ELAPSED"
  echo "timeout_limit_s=$TMO"
  echo "timed_out=$([ "$rc" -eq 124 ] || [ "$rc" -eq 137 ] && echo yes || echo no)"
  echo "start_utc=$(date -u -d "@$T0" +%FT%TZ)"
  echo "end_utc=$(date -u -d "@$T1" +%FT%TZ)"
} > "$ARM.rc"
exit 0
