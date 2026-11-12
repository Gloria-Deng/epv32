#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_encoding_consistency.py -- EPV 编码三方一致性门禁

三方是:
  A. rtl/epv32_defs.vh          —— 硬件读哪个 funct3 / funct7
  B. sw/inc/epv32_hw.h          —— 编译器侧 intrinsic 实际编出的机器码
  C. tb/golden/epv_iss.py       —— 黄金模型读哪个 funct3 / funct7

本门核的是 **三张表不许各自演化**: 从 A、C 抽常量表, 把 B 的每条 intrinsic
真汇编一遍、反汇编回 32bit 字, 再解出 (opcode, funct3, funct7), 三方逐条比对。
缺任何一条都算红 —— 分母取自 A 的表, 不取自本脚本里手写的条数。

**本门不核"字段角色"** (向量号到底放 rd 还是放 rs2)。那一半由功能测试兜住:
epv_bisect 的搬运回环与 epv_selftest 的 17 条比对只要读错字段就会立刻不等。
把这句话写在这里, 是因为"一致性检查"最容易被误当成它没做的事。

注入自检: --selftest 会把 A 的一个 funct7 改掉一位, 断言本门必须报红。
"""
import io
import os
import re
import subprocess
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TC   = r"E:\msys64\ucrt64\bin"
CC   = os.path.join(TC, "riscv64-unknown-elf-gcc.exe")
OD   = os.path.join(TC, "riscv64-unknown-elf-objdump.exe")
MARCH, MABI = "rv32im_zicsr_zifencei", "ilp32"

OP_C0, OP_C1 = 0x0B, 0x2B


def die(msg):
    print("ENCODING_GATE_FAIL: " + msg)
    sys.exit(1)


def parse_rtl_table(path):
    """从 epv32_defs.vh 抽 EPV_F3_* / EPV_F7_* -> {name: (group, sel)}"""
    t = io.open(path, encoding="utf-8", errors="replace").read()
    tab = {}
    for m in re.finditer(r"`define\s+(EPV_F3_\w+)\s+(\d+)'d(\d+)", t):
        tab[m.group(1)] = (OP_C0, int(m.group(3)))
    for m in re.finditer(r"`define\s+(EPV_F7_\w+)\s+(\d+)'d(\d+)", t):
        tab[m.group(1)] = (OP_C1, int(m.group(3)))
    # 同一条 funct 值被两个名字占用 = 表自己坏了
    seen = {}
    for k, v in tab.items():
        if v in seen:
            die("rtl 表内部冲突: %s 与 %s 都是 %r" % (k, seen[v], v))
        seen[v] = k
    if len(tab) < 17:
        die("rtl 表只解析到 %d 条, 分母可疑 (至少应为 17)" % len(tab))
    return tab


def parse_iss_table(path):
    t = io.open(path, encoding="utf-8", errors="replace").read()
    tab = {}
    # 单条赋值与元组赋值都要吃: 本机 ISS 写的是
    #   EPV_F3_LVB, EPV_F3_SVB, EPV_F3_MVX = 0, 1, 2
    for m in re.finditer(r"^\s*(EPV_F[37]_\w+(?:\s*,\s*EPV_F[37]_\w+)*\s*)"
                         r"=\s*([\d,\s]+)$", t, re.M):
        names = [x for x in re.findall(r"EPV_F[37]_\w+", m.group(1))]
        vals  = [int(x) for x in re.findall(r"\d+", m.group(2))]
        if len(names) != len(vals):
            line_no = t[:m.start()].count(chr(10)) + 1
            die("ISS 第 %d 行名字数 %d 与值数 %d 不符" % (line_no, len(names), len(vals)))
        for n, v in zip(names, vals):
            if n in tab:
                die("ISS 里 %s 被赋值两次 (%s 与 %s) —— 第二处会静默覆盖第一处"
                    % (n, tab[n], v))
            tab[n] = str(v)
    # 单条赋值形式也要吃, 否则 ISS 改回一行一条时本门会瞎掉
    for m in re.finditer(r"^\s*(EPV_F[37]_\w+)\s*=\s*(\d+)\s*$", t, re.M):
        if m.group(1) in tab and tab[m.group(1)] != m.group(2):
            die("ISS 里 %s 两处不一致 (%s 与 %s)"
                % (m.group(1), tab[m.group(1)], m.group(2)))
        tab[m.group(1)] = m.group(2)
    if not tab:
        die("黄金 ISS 里没解析到任何 EPV_F*_ 常量 —— 是它改名了还是本门的正则过期了")
    return tab


def probe_c(tab):
    """为每条指令生成一次调用, 用可辨识的字面量操作数。"""
    L = ['#include "epv32_hw.h"',
         "uint32_t v0[16] __attribute__((aligned(16)));",
         "void probe(void) {",
         "  uint32_t base = (uint32_t)(uintptr_t)v0;",
         "  uint32_t g = base;",
         "  uint32_t r;",
         "  uint32_t off = 4;"]
    for name in sorted(tab):
        vd, vs1, vs2 = 5, 2, 3
        if name == "EPV_F3_LVB":
            L.append("  epv_lvb(%d, off, g);" % vd)
        elif name == "EPV_F3_SVB":
            L.append("  epv_svb(%d, off, g);" % vd)
        elif name == "EPV_F3_MVX":
            L.append("  epv_mvx(%d, g);" % vd)
        elif name == "EPV_F3_XMV":
            L.append("  epv_xmv(r, %d); g += r;" % vs2)
        elif name == "EPV_F3_CFG":
            L.append("  epv_cfg(g);")
        elif name == "EPV_F3_CFGR":
            L.append("  epv_cfgr(r); g += r;")
        elif name in ("EPV_F7_VDOT8",):
            L.append("  epv_vdot8(r, %d, %d); g += r;" % (vs1, vs2))
        elif name in ("EPV_F7_VREQ4", "EPV_F7_VADD8", "EPV_F7_VSUB8",
                      "EPV_F7_VMAX8", "EPV_F7_VMIN8", "EPV_F7_VMAC4"):
            L.append("  epv_%s(%d, %d, %d);" % (name[7:].lower(), vd, vs1, vs2))
        elif name in ("EPV_F7_VRELU8", "EPV_F7_VSRA4", "EPV_F7_VPOOL2"):
            L.append("  epv_%s(%d, %d);" % (name[7:].lower(), vd, vs1))
        elif name == "EPV_F7_VZERO":
            L.append("  epv_vzero(%d);" % vd)
        else:
            die("probe 覆盖不到 %s —— 新增指令必须同时新增一条探针调用" % name)
    L += ["  v0[0] = g;", "}"]
    return "\n".join(L) + "\n"


def tool_env():
    """把工具链目录钉进子进程 PATH。MSYS2 的 gcc 找不回同目录的 as/ld 时会
    「退 1 且零输出」, 那种失败长得像本脚本的 bug。与 flow/rt.py 同一处理。"""
    e = dict(os.environ)
    e["PATH"] = TC + os.pathsep + e.get("PATH", "")
    return e


def assembled_words(cfile):
    """返回出现在 .text 里的 (opcode, funct3, funct7) 集合, 含每条出现次数。"""
    out = subprocess.run([CC, "-march=" + MARCH, "-mabi=" + MABI, "-O1",
                          "-I", os.path.join(ROOT, "sw", "inc"),
                          "-c", cfile, "-o", cfile + ".o"],
                         capture_output=True, env=tool_env())
    if out.returncode != 0 or not os.path.isfile(cfile + ".o"):
        msg = out.stdout.decode("utf-8", "replace").strip()
        die("探针编译失败 rc=%d 产物存在=%s\n%s"
            % (out.returncode, os.path.isfile(cfile + ".o"), msg[-1500:] or "(工具零输出)"))
    d = subprocess.run([OD, "-d", cfile + ".o"], capture_output=True, env=tool_env())
    txt = d.stdout.decode("utf-8", "replace")
    got = {}
    for line in txt.splitlines():
        m = re.match(r"\s*[0-9a-f]+:\s+([0-9a-f]{8})\s", line)
        if not m:
            continue
        w = int(m.group(1), 16)
        op = w & 0x7F
        if op not in (OP_C0, OP_C1):
            continue
        f3 = (w >> 12) & 7
        f7 = (w >> 25) & 0x7F
        key = (op, f3 if op == OP_C0 else f7)
        got[key] = got.get(key, 0) + 1
    if not got:
        die("反汇编里一条 CUSTOM0/CUSTOM1 都没有 —— 头文件被改坏, 或探针没编进 .text")
    return got


def main():
    mutate = "--selftest" in sys.argv
    rtl_p = os.path.join(ROOT, "rtl", "epv32_defs.vh")
    iss_p = os.path.join(ROOT, "tb", "golden", "epv_iss.py")
    if not os.path.isfile(iss_p):
        die("黄金 ISS 不存在: %s —— 分母不该悄悄缩成两方" % iss_p)

    rtl = parse_rtl_table(rtl_p)
    iss = parse_iss_table(iss_p)
    words = assembled_words(write_probe(rtl))

    # ---- 注入必须发生在所有比较**之前**。原先这一段放在 bad/missing 算完之后,
    # 于是 --selftest 永远「期望报红却没有」—— 它抓到的是本门自己的时序 bug。
    if mutate:
        victim = min(rtl, key=lambda k: (rtl[k][1] or 99, k))
        old = rtl[victim][1]
        rtl[victim] = (rtl[victim][0], old ^ 0x01)
        print("[selftest] 注入 %s: sel %d -> %d, 本门必须报红" % (victim, old, old ^ 1))

    # ---- A vs C: 逐条比 funct 值
    bad = []
    for name, (op, sel) in sorted(rtl.items()):
        if name not in iss:
            bad.append("ISS 缺 %s" % name)
            continue
        iop = OP_C0 if name.startswith("EPV_F3_") else OP_C1
        try:
            isel = int(iss[name])
        except Exception:
            bad.append("ISS 的 %s 不是整数: %r" % (name, iss[name]))
            continue
        if (iop, isel) != (op, sel):
            bad.append("%s: RTL=(op=0x%02X,sel=%d) 而 ISS=(op=0x%02X,sel=%d)"
                       % (name, op, sel, iop, isel))
    # ISS 里多出来的名字同样要报 —— 否则它可以慢慢长出第二套真源
    for name in sorted(set(iss) - set(rtl)):
        bad.append("ISS 有而 RTL 表没有的名字: %s" % name)

    # ---- A vs B: 每条都必须在编译产物里出现
    missing = []
    for name, (op, sel) in sorted(rtl.items()):
        if (op, sel) not in words:
            missing.append("%s (期望 op=0x%02X sel=%d)" % (name, op, sel))
    # 编译产物里出现但表里没有的 custom 指令 = 头文件自己造了条指令
    known = {(op, sel) for op, sel in rtl.values()}
    extra = [k for k in sorted(words) if k not in known]

    print("三方表条目: RTL=%d  ISS=%d  编译产物中 custom 字=%d (去重 %d)"
          % (len(rtl), len(iss), sum(words.values()), len(words)))
    if bad or missing or extra:
        for x in bad:     print("  表不一致  ", x)
        for x in missing: print("  未被编译产物覆盖", x)
        for x in extra:   print("  头文件产出表中无的编码", x)
        die("共 %d 处" % (len(bad) + len(missing) + len(extra)))
    if mutate:
        die("--selftest 期望报红却没有: 注入后三方仍一致, 说明本门没在看注入的那张表")
    print("ENCODING_GATE_OK: %d 条 EPV 编码在 RTL 表 / 头文件机器码 / 黄金 ISS 三方一致"
          % len(rtl))
    return 0


def write_probe(tab):
    p = os.path.join(ROOT, "build", "enc_probe.c")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    io.open(p, "w", encoding="utf-8", newline="\n").write(probe_c(tab))
    return p


if __name__ == "__main__":
    sys.exit(main())
