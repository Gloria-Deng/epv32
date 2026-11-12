#!/usr/bin/env python3
"""独立核对 minstret 有没有把多周期指令的冻结拍算成退休指令。

为什么要这条: 报告正文与 results/bench/*.json 里有两条数是从 minstret 直接来的
(EPV 臂 CPI、两份实现的"指令数比")。如果多周期指令在每个冻结拍都被记一次退休,
那这两条数就不是"指令数"而是"指令数 + 冻结拍数", 正文用它讲"编译器替我们排了指令"
就不成立。这条核对不读 CSR, 而是用**测试台自己写的退休轨迹**当独立见证:
轨迹每行 R 是一次退休, 按 PC 回到装载镜像解出那条指令的 opcode/funct, 于是
VDOT8 这类"写通用寄存器的多周期指令"到底退休了几次, 与 minstret 说几次, 可以
直接对撞。

判决形状:
  R_RECORDS  = 轨迹里的 R 行数(测试台口径的退休数)
  INSTRET    = 同一次运行 sim 归档里的 total_retired(CSR 口径来自 RTL)
  VDOT8_TRACE= 按镜像解码出来的 VDOT8 退休次数
  VDOT8_ASM  = 按源代码循环结构解析式数出来的 VDOT8 应执行次数
后者与前者相等, 而 minstret 明显更大 => 计数把冻结拍算进去了。

本工具只读, 不改任何归档。
"""
import hashlib
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
BASE = 0x80000000


def classify(w):
    op = w & 0x7F
    f3 = (w >> 12) & 7
    f7 = (w >> 25) & 0x7F
    if op == 0x2B and f3 == 0:
        return {1: "VDOT8", 2: "VMAC4", 3: "VADD8", 4: "VREQ4", 5: "VMAX8",
                6: "VRELU8", 7: "VSRA4", 8: "VZERO", 9: "VPOOL2",
                10: "VSUB8", 11: "VMIN8"}.get(f7, "CUSTOM1?")
    if op == 0x0B:
        return {0: "LVB", 1: "SVB", 2: "MVX", 3: "XMV",
                4: "CFG", 5: "CFGR"}.get(f3, "CUSTOM0?")
    return None


def probe(arc, trace, image, expect=None):
    r = json.load(io.open(arc, encoding="utf-8"))
    img = io.open(image, "rb").read()
    nw = len(img) // 4
    counts = {}
    nrec = outside = 0
    with io.open(trace, encoding="utf-8", errors="replace") as f:
        for ln in f:
            if not ln.startswith("R "):
                continue
            nrec += 1
            p = ln.split()
            if len(p) < 2:
                outside += 1
                continue
            idx = (int(p[1], 16) - BASE) >> 2
            if idx < 0 or idx >= nw:
                outside += 1
                continue
            k = classify(int.from_bytes(img[idx * 4:idx * 4 + 4], "little"))
            if k:
                counts[k] = counts.get(k, 0) + 1
    print("%-12s trace_R=%d  archive_total_retired=%d  outside=%d" %
          (os.path.basename(arc), nrec, r.get("total_retired", -1), outside))
    for k in sorted(counts):
        line = "             %-8s retired_in_trace=%d" % (k, counts[k])
        if expect and k in expect:
            line += "  expected_from_source=%d  %s" % (
                expect[k], "MATCH" if expect[k] == counts[k] else "MISMATCH")
        print(line)
    epv_inst = r.get("epv_instret")
    epv_cyc = r.get("epv_cycles")
    if epv_inst and epv_cyc:
        print("             CSR 口径 EPV_INST=%s EPV_CYC=%s CPI=%.3f"
              % (epv_inst, epv_cyc, epv_cyc / float(epv_inst)))
    return counts, nrec, r


if __name__ == "__main__":
    # nn_bench: 3x3 卷积 16 输出像素, 每像素 9 个位置 x 4 个输出通道 = 36 条 VDOT8
    # (见 sw/bench/nn_bench.c 的循环结构与 5.4 那条"每条 VDOT8 约 10 拍")
    probe(os.path.join("results", "sim", "nn_bench.json"),
          os.path.join("results", "trace", "nn_bench.txt"),
          os.path.join("build", "elf", "nn_bench.bin"),
          expect={"VDOT8": 16 * 9 * 4, "VREQ4": 16 * 1})
    print()
    probe(os.path.join("results", "sim", "nn_net.json"),
          os.path.join("results", "trace", "nn_net.txt"),
          os.path.join("build", "elf", "nn_net.bin"))
    print()
    if os.path.isfile(os.path.join("results", "sim", "nn_cifar.json")):
        probe(os.path.join("results", "sim", "nn_cifar.json"),
              os.path.join("results", "trace", "nn_cifar.txt"),
              os.path.join("build", "elf", "nn_cifar.bin"))
