#!/bin/bash
# build_sim.sh -- 用 Verilator 把 SoC + C++ 测试台编成可执行的周期精确仿真器
#
# 依赖 (本机实测路径):
#   verilator_bin.exe : E:\msys64\ucrt64\bin\verilator_bin.exe  (5.050)
#   VERILATOR_ROOT    : E:\msys64\ucrt64\share\verilator
#   g++               : E:\msys64\ucrt64\bin\g++.exe
# 用 verilator_bin.exe 而不是 verilator: 后者是 perl 脚本, 本机 perl 缺
# Pod::Usage 会直接崩, 且崩了之后 shell 看到的还是 0 (踩过)。
set -eu
export PATH="/e/msys64/ucrt64/bin:/e/msys64/usr/bin:$PATH"
export VERILATOR_ROOT="E:/msys64/ucrt64/share/verilator"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p build results/sim

VSIM=${VSIM:-verilator_bin.exe}

# ------------------------------------------------------------------ 两臂分流
# ASSERT=1 → 带结构不变式的那一臂, 落在**另一个**目录与**另一个**可执行文件名。
# 为什么必须分开而不是原地加宏: build/sim/epv32sim.exe 是 flow/rt.py 里 SIM 常量
# 指向的那一份认证仿真器; 覆盖它就让 154 对归档的 sim_sha256 键指向一个盘上
# 不存在对应关系的二进制。两臂同目录 = 认证产物被自检件悄悄换掉。
ASSERT=${ASSERT:-0}
COV=${COV:-0}
if [ "$ASSERT" = "1" ]; then
  MDIR=build/sim_assert
  OUTNAME=epv32sim_assert
  # 必须是 +define+, 不能是 -D: Verilator 的 -D 把宏加到**生成出来的 C++** 上,
  # 不碰 Verilog 预处理器 —— 于是 `ifdef 恒假、.vh 一句都不编, 而构建照样
  # BUILD_SIM_OK 且退出码 0。实测 A/B (同一棵树、同一组文件, 只换这一处写法):
  #   -DEFV_ASSERTIONS    -> 生成物里 'EPVASSERT' 出现 0 次, 0 error
  #   +define+...         -> 出现 20 次
  # 这一格由 flow/assert_selfcheck.py 的 ARMED heartbeat 判据守住: 仿真器不打印
  # 'EPVASSERT CORE_ARMED'/'EPVASSERT EPV_ARMED' 就判 NO-HEARTBEAT 而不是判绿。
  VDEF="+define+EPV_ASSERTIONS"
  BUILDLOG=results/sim/build_assert.log
elif [ "$COV" = "1" ]; then
  # 覆盖率臂: 又是**第三个**目录与可执行名。带 --coverage 重编会把 cover 计数
  # 插进每一句 RTL, 周期数与面积都不再是认证臂的那个形状, 所以它既不能覆写
  # build/sim/epv32sim.exe, 也不能与断言臂共用目录。
  MDIR=build/sim_cov
  OUTNAME=epv32sim_cov
  VDEF=""
  VCOV="--coverage-line --coverage-toggle --coverage-user"
  BUILDLOG=results/sim/build_cov.log
else
  MDIR=build/sim
  OUTNAME=epv32sim
  VDEF=""
  BUILDLOG=results/sim/build.log   # 认证臂的日志名不变: 有读者按这个名字取
fi
VCOV=${VCOV:-}

# FORCE=1 —— 整目录重编，不吃 make 的增量判断。为什么需要它：flow/sim_fingerprint.py
# 按**输入内容**判「这个二进制是不是这几份源编出来的」，一旦判要重编，就必须真的
# 全量重来。实测过 mtime 增量不可信：muldiv.v 比 exe 新 10 秒，而 verilator --build
# 底下那句 make 一声不响地什么都没重链（那一趟链条的 exe 时间戳原地不动）。
if [ "${FORCE:-0}" = "1" ]; then
  echo "BUILD_SIM_FORCE_CLEAN MDIR=$MDIR"
  rm -rf "$MDIR"
fi

if [ ! -x "$MDIR/$OUTNAME" ] && [ ! -f "$MDIR/$OUTNAME.exe" ]; then
  rm -rf "$MDIR"
fi
mkdir -p "$MDIR"

"$VSIM" --cc --exe --build -j 4 \
  --Mdir "$MDIR" \
  -I"$ROOT/rtl" \
  -CFLAGS "-O2 -std=c++17 -I$ROOT/rtl" \
  -Wall -Wno-DECLFILENAME -Wno-UNUSED -Wno-fatal \
  --top-module epv32_soc \
  --x-assign 0 --x-initial 0 --trace \
  $VDEF $VCOV \
  rtl/epv32_soc.v rtl/epv32_core.v rtl/epv32_alu.v rtl/epv32_rf.v \
  rtl/epv32_muldiv.v rtl/epv32_epv.v rtl/epv32_csr.v rtl/epv32_ram.v \
  tb/sim_main.cpp \
  -o "$OUTNAME" 2>&1 | tee "$BUILDLOG" | tail -20

if [ -f "$MDIR/$OUTNAME.exe" ] || [ -x "$MDIR/$OUTNAME" ]; then
  # g++ 给 Verilator 的预编译头 (V*__pch.h.*.gch) 一发就是 130 MB 量级, 会被
  # package_hygiene 的「>10MB 明文产物」判红 —— 它是纯编译器中间件, 删掉只会让
  # 下次重建慢一点, 不影响产物正确性。在**报 OK 之前**删, 免得「构建绿但包里
  # 留着临时件」这种形状再让链条在最后一格里红。
  find "$MDIR" -name "*.gch" -type f -print -delete 2>/dev/null || true
  echo "BUILD_SIM_OK ASSERT=$ASSERT MDIR=$MDIR BIN=$MDIR/$OUTNAME"
else
  echo "BUILD_SIM_FAIL ASSERT=$ASSERT"; exit 1
fi
