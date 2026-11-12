#!/bin/bash
# Re-run the four Anlogic TD arms against the CURRENT rtl/ tree.
#
# 作业清单**不写死在这里**: 由 tools/pnr_archive.py 的 ARMS 现算。
# 上一版这个文件跑的是 run2s_epv / run2s_noepv, 而归档器读的是 run3s_epv /
# run3s_noepv —— 于是"刷新过了"和"被读的那份报告更新了"是两件事(今天抓到的)。
#
# 顺序: python flow/make_td_arms.py   (铺臂 + 写 tcl + 登记 run_id)
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
# 为什么要有这一闸：今天用 nohup+后台包装把这条链发了两遍，于是同一个臂目录
# 被两个 TD 进程交替写（refresh3.log 里每一条 RC 都出现两遍、时间戳完全相同），
# 跑完的器件报告说不清是哪一趟写的 —— 这类污染不会报错，只会让归档悄悄变脏。
# 闸的形状：mkdir 是原语的，用它当锁；锁里写 PID 与起始时刻；超过 STALE_MIN
# 的锁视为上次崩了留下的，点名后接管（不许静默绕过，也不许永远卡死）。
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
# 作业清单由 python 自己落盘并按 LF 写: 用 shell 重定向接 print 的输出会得到 CRLF,
# 于是 "$d/$tcl" 带着一个尾巴 \r, 四个臂全部报 MISSING-TCL(实测踩过)。
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
  # TD 写完报告之后常常 SIGSEGV(rc=139), 所以 rc 不是判据; 但「一个臂压根没跑」
  # 必须是判据 —— 少跑一个臂就等于沿用上一版报告。
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
