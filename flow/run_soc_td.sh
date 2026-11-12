#!/usr/bin/env bash
# 跑交付顶层 epv32_soc 的两条 TD 臂（综合档 + 布线档）。
# 与 flow/run_td_refresh.sh 分开：那个驱动按 pnr_archive.ARMS 展开作业清单，SoC 档故意不进，
# 只补"SoC 顶层的布线后读数"缺口，不动 summary2.json 现有键（被正文锚点钉着）。
#
# 退出码不是证据：run.exe 写完报告会 SIGSEGV（rc=139）；证据是 log 里 MARK-*-OK / ARTIFACT-*-SIZE 行，由 tools/soc_pnr_archive.py 核。
set -u
export PYTHONDONTWRITEBYTECODE=1

cd "$(dirname "$0")/.."
RUN=/e/Anlogic/TD_6.2.1_Engineer_6.2.168.116/bin/run.exe
[ -x "$RUN" ] || { echo "SOC-TD-NO-RUNEXE $RUN"; exit 3; }

python flow/make_soc_arm.py || { echo "SOC-TD-SETUP-FAIL"; exit 4; }

rc_all=0
for spec in "run_soc_synth soc_synth_flow" "run_soc_pnr soc_pnr_flow"; do
  set -- $spec
  d="results/pnr/$1"; t="$2.tcl"
  [ -f "$d/$t" ] || { echo "SOC-TD-MISSING-TCL $d/$t"; rc_all=1; continue; }
  echo "=== SOC-TD-START $1 $(date -u +%FT%TZ) ==="
  ( cd "$d" && "$RUN" -b "$t" > "$1.out" 2> "$1.err" </dev/null )
  echo "SOC-TD-RC $1 rc=$?  (rc 不是证据，只看 log 里的 MARK/ARTIFACT 行)"
done
echo "SOC-TD-DONE $(date -u +%FT%TZ) rc_all=$rc_all"
