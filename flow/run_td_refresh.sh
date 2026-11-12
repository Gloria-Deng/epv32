#!/bin/bash
# 对当前 rtl/ 树重跑 4 条 Anlogic TD 臂。
#
# 作业清单由 tools/pnr_archive.py 的 ARMS 现算，不写死在这里
# (曾因本脚本跑 run2s、归档器读 run3s，导致"刷新过"≠"被读的报告已更新")。
#
# 顺序: python flow/make_td_arms.py   (铺臂/写 tcl/登记 run_id)
#       bash   flow/run_td_refresh.sh (真跑 TD)
#       python tools/pnr_archive.py   (出 results/pnr/summary2.json)
set -u
export PYTHONDONTWRITEBYTECODE=1

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"
RUN=/e/Anlogic/TD_6.2.1_Engineer_6.2.168.116/bin/run.exe
BASE=results/pnr
LOG=$BASE/refresh3.log
JOBS=$BASE/jobs.txt

# ---- 单实例闸 -------------------------------------------------------------
# 防同臂目录被两个 TD 进程交替写、报告说不清是哪趟写的污染(不报错、只让归档悄悄变脏)。
# mkdir 作原子锁，锁内写 PID 与起始时刻；超过 STALE_MIN 视为残留锁，点名后接管。
LOCK=$BASE/.refresh.lock
STALE_MIN=90
if ! mkdir "$LOCK" 2>/dev/null; then
  holder=$(cat "$LOCK/owner" 2>/dev/null)
  age_min=$(( ( $(date +%s) - $(stat -c %Y "$LOCK" 2>/dev/null || echo 0) ) / 60 ))
  if [ "$age_min" -lt "$STALE_MIN" ]; then
    echo "TD_LOCK_HELD by '${holder:-unknown}' age_min=$age_min limit=$STALE_MIN"
    echo "TD_REFUSE: 已有刷新在跑 —— 两趟并发写同一批臂目录会互相污染。"
    echo "确认那趟已经死掉就手动 rmdir $LOCK 再来。"
    exit 3
  fi
  echo "TD_LOCK_STALE age_min=$age_min holder='${holder:-unknown}' -> 接管"
fi
printf 'pid=%s start=%s\n' "$$" "$(date -u +%FT%TZ)" > "$LOCK/owner"
trap 'rm -rf "$LOCK"' EXIT
# ---- 单实例闸结束 ---------------------------------------------------------

: > "$LOG"
# 作业清单由 python 落盘并按 LF 写: shell 重定向接 print 会得 CRLF, "$d/$tcl" 带 \r 报 MISSING-TCL。
python - "$JOBS" <<'PY' || { echo "JOB_LIST_FAIL" >> "$LOG"; exit 1; }
import io, sys
sys.path.insert(0, "tools")      # cwd 已经是仓库根 (上面 cd 过)
import pnr_archive as P
with io.open(sys.argv[1], "w", encoding="utf-8", newline="\n") as f:
    for arm, cfg in P.ARMS.items():
        for kind in ("synth", "pnr"):
            f.write("%s %s\n" % (cfg[kind + "_dir"], cfg[kind + "_tcl"]))
PY

n=0
bad=0
while IFS=' ' read -r d tcl || [ -n "$d" ]; do
  tcl=${tcl%$'\r'}
  d=${d%$'\r'}
  [ -n "$d" ] || continue
  name=${tcl%.tcl}
  if [ ! -f "$d/$tcl" ]; then
    echo "MISSING-TCL $d/$tcl (先跑 python flow/make_td_arms.py)" >> "$LOG"
    bad=$((bad+1))
    n=$((n+1))
    continue
  fi
  ( cd "$d" && "$RUN" -b "$tcl" > "$name.out" 2> "$name.err" < /dev/null )
  rc=$?
  echo "RC $d $name $rc $(date +%H:%M:%S)" >> "$LOG"
  # TD 写完常 SIGSEGV(rc=139)，rc 不作判据；但"一个臂压根没跑"必须判——少跑一臂即沿用旧报告。
  n=$((n+1))
done < "$JOBS"
want=$(grep -c '' "$JOBS")
echo "JOBS_RUN $n OF $want" >> "$LOG"
if [ "$n" -ne "$want" ] || [ "$bad" -ne 0 ]; then
  echo "RUN_TD_REFRESH_FAIL 跳过 $bad 个臂" >> "$LOG"
  exit 1
fi
echo "ALL_DONE $(date +%H:%M:%S)" >> "$LOG"
exit 0
