#!/bin/bash
# flow/demo.sh -- 方案 Demo 的「一条命令跑完并留证」脚本
#
# 赛题要求第一阶段提交「设计报告 + 软硬件设计源代码 + 方案 Demo」，Demo 视频
# 时长 ≤10 分钟、文件 ≤100MB。**录屏这件事只能由人做**（要出镜讲解、要录屏幕），
# 本脚本负责把录屏时需要现场跑的东西一次跑完，并把每一步的产物按序号落到
# results/demo/，这样录出来的片子每一个画面都能回溯到一份归档。
#
# 用法:
#   bash flow/demo.sh            # 跑完整演示序列（约 3-6 分钟机器时间）
#   bash flow/demo.sh --check    # 只检查产物齐全，不重跑
set -u
export PYTHONDONTWRITEBYTECODE=1

export PATH="/c/Users/lenovo/AppData/Local/Programs/Python/Python311:/e/msys64/ucrt64/bin:$PATH"
export VERILATOR_ROOT="E:/msys64/ucrt64/share/verilator"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
OUT="results/demo"; mkdir -p "$OUT"
PY=python
log(){ printf '\n=== [%s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$OUT/run.log"; }
fail(){ printf 'DEMO_STEP_FAILED: %s\n' "$*" | tee -a "$OUT/run.log"; exit 1; }

if [ "${1:-}" = "--check" ]; then
  need=(01_compliance_rv32ui.txt 02_compliance_rv32um.txt 03_epv_selftest.txt
        04_epv_bisect.txt 05_nn_bench.txt 05b_nn_net.txt 06_wave.vcd.gz
        06b_wave_archive.json 07_synthesis.txt 08_encoding_gate.txt
        09_matrix.txt 10_docx.txt 11_isco_trace.txt 12_device_pnr.txt
        13_misalign_probe.txt 14_video.txt)
  miss=0
  for f in "${need[@]}"; do
    if [ -s "$OUT/$f" ]; then printf 'OK    %-30s %s bytes\n' "$f" "$(wc -c <"$OUT/$f")"
    else printf 'MISS  %s\n' "$f"; miss=$((miss+1)); fi
  done
  [ $miss -eq 0 ] || fail "缺 $miss 个产物"
  # 产物在不在只是第一层：每一步还必须**自己印出成功那一句**。
  # 起因是真实的一次假完整 —— 11 步里两条命令写错了参数（argparse 报
  # "unrecognized arguments"），文件非空所以 --check 全绿，画面上其实什么都没有。
  # 判据只认形状不认具体计数（[0-9]+/[0-9]+），复跑改了分母也不会把这里带红。
  mmiss=0
  while IFS='|' read -r f pat; do
    case "$f" in ''|'#'*) continue ;; esac
    if grep -Eq "$pat" "$OUT/$f" 2>/dev/null; then
      printf 'MARK  OK    %-28s %s\n' "$f" "$pat"
    else
      printf 'MARK  MISS  %-28s 期望匹配: %s\n' "$f" "$pat"; mmiss=$((mmiss+1))
    fi
  done <<'MARKS'
01_compliance_rv32ui.txt|COMPLIANCE rv32ui: [0-9]+/[0-9]+ PASS  all_pass=True
02_compliance_rv32um.txt|COMPLIANCE rv32um: [0-9]+/[0-9]+ PASS  all_pass=True
03_epv_selftest.txt|VERDICT=PASS
04_epv_bisect.txt|VERDICT=PASS
05_nn_bench.txt|bit_exact=True
05b_nn_net.txt|NET_ARCHIVE_OK
06_wave_probe.txt|^vars_total [0-9]+ deltalines [0-9]+ interest [0-9]+
06_wave_probe.txt|c0_lvb
06_wave_probe.txt|^--- source ---
07_synthesis.txt|WROTE results/synth/summary.json
08_encoding_gate.txt|ENCODING_GATE_OK
08_encoding_gate.txt|^rc=1$
09_matrix.txt|MATRIX  [0-9]+/[0-9]+  all_green=True
10_docx.txt|DOCX_VERIFY_OK
11_isco_trace.txt|ISCO_SELFTEST_OK
11_isco_trace.txt|ISCO rv32ui: [0-9]+/[0-9]+
12_device_pnr.txt|PNR_ARCHIVE_OK
13_misalign_probe.txt|VERDICT=PASS
14_video.txt|DEMO_VIDEO_SELFTEST_OK
14_video.txt|DEMO_VIDEO_OK
MARKS
  # 上一条 11 步的 "unrecognized arguments" 是**禁令**：出现即红。
  # 用 while 循环统一处理会把禁令和许可混在一起，所以单独判一次。
  if grep -q "unrecognized arguments" "$OUT/11_isco_trace.txt" 2>/dev/null; then
    printf 'MARK  MISS  %-28s 画面里出现了 argparse 的用法错误\n' "11_isco_trace.txt"
    mmiss=$((mmiss+1))
  fi
  [ $mmiss -eq 0 ] || fail "$mmiss 个产物没有印出自己的成功判据"
  # 成片的两条硬指标（时长/体积）与「屏上数字都有出处」直接读 sidecar 字段，
  # 不靠人眼看文件属性 —— 属性看着对，字段可能写着另一趟运行。
  $PY - <<'PY' || fail "demo_video 的时长/体积/出处不合规"
import json, io, os, sys
d = json.load(io.open("results/demo/demo_video.json", encoding="utf-8"))
print("  video %-9s duration=%ss/%ss size=%.1f/%s MB 屏上无出处数字=%s"
      % (d["resolution"], d["duration_s"], d["limit_duration_s"],
         d["size_bytes"] / 1048576.0, d["limit_size_bytes"] / 1048576.0,
         d["number_provenance"]["unsourced_numbers_on_screen"]))
sys.exit(0 if (d["duration_s"] <= d["limit_duration_s"]
               and d["size_bytes"] <= d["limit_size_bytes"]
               and d["number_provenance"]["unsourced_numbers_on_screen"] == 0
               and os.path.isfile(d["file"])) else 1)
PY
  # 波形页是截窗运行：它自己的归档必须带 tag，认证归档必须干净。
  # 这一条只查文件在不在是查不住的，所以直接跑那道同源门禁本体。
  $PY tools/check_archive_clobber.py || fail "归档与矩阵不同源"
  echo "DEMO_ARTIFACTS_COMPLETE"; exit 0
fi

: > "$OUT/run.log"

log "0 构建仿真器"
bash flow/build_sim.sh > "$OUT/00_build.log" 2>&1 || fail build_sim
[ -x build/sim/epv32sim.exe ] || fail "仿真器产物不存在"

log "1 官方 rv32ui 合规（录屏：42 项逐条跑）"
$PY flow/rt.py riscv rv32ui > "$OUT/01_compliance_rv32ui.txt" 2>&1 || true

log "2 官方 rv32um 合规"
$PY flow/rt.py riscv rv32um > "$OUT/02_compliance_rv32um.txt" 2>&1 || true

log "3 EPV 17 条指令自测（基线值由 C 现算）"
$PY flow/rt.py bench sw/bench/epv_selftest.c --max-cyc 400000 \
   > "$OUT/03_epv_selftest.txt" 2>&1 || true

log "4 EPV 通路二分（搬运回环 / 广播 / 点积 / MAC4）"
$PY flow/rt.py bench sw/bench/epv_bisect.c --max-cyc 400000 \
   > "$OUT/04_epv_bisect.txt" 2>&1 || true

log "5 基线 vs EPV 的 INT8 卷积累加（核心一页）"
$PY flow/rt.py bench sw/bench/nn_bench.c --max-cyc 4000000 \
   > "$OUT/05_nn_bench.txt" 2>&1 || true
$PY tools/bench_archive.py >> "$OUT/05_nn_bench.txt" 2>&1 || true

log "5b 网络级 INT8 推理（三层卷积 + 池化 + 全连接，3 个样本）"
$PY flow/rt.py bench sw/bench/nn_net.c --max-cyc 9000000 \
   > "$OUT/05b_nn_net.txt" 2>&1 || true
$PY tools/net_archive.py >> "$OUT/05b_nn_net.txt" 2>&1 || true

log "5c 边界探针：非对齐取指目标按 IALIGN=32 陷入"
$PY flow/rt.py bench sw/bench/misalign_bisect.c --max-cyc 400000 \
   > "$OUT/13_misalign_probe.txt" 2>&1 || true

log "6 打开波形：EPV 分派与搬运时序"
# 这一页只截前 4200 拍（波形文件才有几百 MB 以内），所以它不是认证运行。
# 必须 --tag wave：不带它的话本步会把已认证的 epv_bisect.json 覆盖成
# VERDICT=TIMEOUT —— 归档一旦被截窗实验写过，后面所有读它的人拿到的都是假结论。
EPV_WAVE=1 EPV_WAVE_FILE="$ROOT/$OUT/06_wave.vcd" \
  $PY flow/rt.py bench sw/bench/epv_bisect.c --max-cyc 4200 --tag wave \
  > "$OUT/06_wave_run.txt" 2>&1 || true
$PY tools/vcd_probe.py > "$OUT/06_wave_probe.run.log" 2>&1
probe_rc=$?
log "  vcd_probe rc=$probe_rc（明文 .vcd 不在时它自己会读 .vcd.gz 并打印读的是哪份）"
[ "$probe_rc" = 0 ] || fail "第 6 段波形解析没跑成，见 $OUT/06_wave_probe.run.log"
# vcd_probe 写的是 results/wave/probe.txt；顺手拷一份到 demo 目录。
# 这里以前是 `|| true` + `cp ... || true`：解析失败时 06_wave_probe.txt 里留下的
# 是一屏 traceback，或者上一趟的残留，而 --check 只看"非空 + 有 vars_total 行"，
# 两种故障都长得像成功。现在拷不上就停手。
cp -f results/wave/probe.txt "$OUT/06_wave_probe.txt" || fail "拷不到 results/wave/probe.txt"
# 波形页自己那一趟运行的归档也收进包：它证明这一页来自一次 4200 拍的截窗运行，
# 而不是一次认证运行 —— 认证归档 results/sim/epv_bisect.json 没被它写过。
cp -f results/sim/epv_bisect_wave.json "$OUT/06b_wave_archive.json" 2>/dev/null || true
# 波形是录屏用的主产物。随包只留**压缩件**（17.8 MB 的明文 VCD 由本脚本重跑生成，
# 压缩前用"回读解压 == 原件 sha256 且行数相等"验过，见 flow/compress_artifact.py），
# 所以这里两种形态都接受，但都必须非空到可信的大小。
$PY - <<'PY' || fail "06_wave.vcd(.gz) 缺失或过小"
import gzip,os,sys
plain = "results/demo/06_wave.vcd"
packed = plain + ".gz"
ok = False
if os.path.isfile(plain):
    ok = os.path.getsize(plain) > 100000
elif os.path.isfile(packed):
    n = sum(1 for _ in gzip.open(packed, "rt", errors="replace"))
    ok = os.path.getsize(packed) > 20000 and n > 5000
    print("WAVE_AS_GZ lines=%d gz_bytes=%d (录屏前 bash flow/demo.sh 会重生明文)"
          % (n, os.path.getsize(packed)))
sys.exit(0 if ok else 1)
PY

log "6z 把明文波形收成压缩件（>10 MB 的明文产物不随包：尺寸判据见 flow/check_package_hygiene.py）"
# 这一条排在波形页之后，所以"重跑 demo.sh 就能再生压缩件"这句话是真的：
# 上面那段仿真只写明文 .vcd，若不收，包卫生门下一趟会把 17.8 MB 的明文抓成命中。
$PY flow/compress_artifact.py results/demo/06_wave.vcd > "$OUT/06c_compress.txt" 2>&1 \
  || fail "波形压缩的回读核对没过（见 $OUT/06c_compress.txt），明文原件仍在盘上"
grep -Eq 'COMPRESSED|SKIP-MISSING' "$OUT/06c_compress.txt" \
  || fail "压缩步没印出自己的判据（见 $OUT/06c_compress.txt）"

log "7 Yosys 面积三档 + 归档"
for s in epv noepv; do
  /d/yosys_build/bucrt/yosys.exe -s flow/synth_core_$s.ys > results/synth/core_$s.txt 2>&1 || true
done
/d/yosys_build/bucrt/yosys.exe -s flow/synth_epv.ys > results/synth/epv_only.txt 2>&1 || true
{ $PY tools/synth_archive.py; grep -h -A14 "Printing statistics" \
    results/synth/core_epv.txt results/synth/core_noepv.txt results/synth/epv_only.txt \
    | grep -E "=== |cells$|\\\$adff|wires$"; } > "$OUT/07_synthesis.txt" 2>&1 || true

log "8 编码三方一致性门禁（含注入自检）"
{ $PY tools/check_encoding_consistency.py
  echo "--- 注入自检（应当报红） ---"
  $PY tools/check_encoding_consistency.py --selftest; echo "rc=$?"
} > "$OUT/08_encoding_gate.txt" 2>&1 || true

log "9 认证矩阵"
$PY flow/run_all.py > "$OUT/09_matrix.txt" 2>&1 || true

log "10 docx 生成 + 排版回读校验"
{ $PY flow/build_docx.py; $PY flow/verify_docx.py; } > "$OUT/10_docx.txt" 2>&1 || true

log "11 黄金 ISS 与核的逐条退休轨迹比对（独立实现，不是同一段 RTL）"
{ $PY flow/iss_compare.py --selftest
  $PY flow/iss_compare.py rv32um
  $PY flow/iss_compare.py rv32ui
} > "$OUT/11_isco_trace.txt" 2>&1 || true

log "12 器件口径：安路 TD 综合与布局布线归档（未上板）"
$PY tools/pnr_archive.py > "$OUT/12_device_pnr.txt" 2>&1 || true

log "13 生成无声归档回放片（人讲版另录）"
{ $PY flow/make_demo_video.py --selftest
  $PY flow/make_demo_video.py
} > "$OUT/14_video.txt" 2>&1 || true

log "完成"
grep -hE "COMPLIANCE|VERDICT|speedup|MISMATCH|GATE|MATRIX|DOCX_VERIFY|cells|WROTE" \
  "$OUT"/*.txt 2>/dev/null | sed 's/^/  /' | head -40 | tee -a "$OUT/run.log"
bash flow/demo.sh --check
