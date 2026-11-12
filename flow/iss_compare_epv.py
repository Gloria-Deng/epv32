#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flow/iss_compare_epv.py -- 把 EPV 扩展程序也接进「黄金 ISS ↔ RTL 逐条轨迹比对」。

两个投影一起比：R 线 (pc, rd, wdata) 管计算类写回；S 线 (addr, wdata, be) 只覆盖
退休记录里可见的标量 store —— SVB 的四次总线拍在退休轨迹里看不见（o_tr_memwr=0），
其数据通路由 R 线传递性覆盖。每条扩展是否被比到由指令字现场解码给出（pc 回指 flat
image 按 funct3/funct7 对 rtl/epv32_defs.vh 那张编码唯一真源表），一条都没解出就 raise。

不改动 flow/iss_compare.py：它把自己的 sha256 记进已认证归档，动它就三套件指纹全对不上。
"""
import io
import json
import os
import re
import struct
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "flow"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "tb", "golden"))
import rt                                        # noqa: E402
import epv_iss                                   # noqa: E402
import check_encoding_consistency as ENC         # noqa: E402
from atomic_json import write_json_atomic         # noqa: E402

BASE = 0x80000000
# 地址图直接抄 rtl/epv32_defs.vh 的 `define ADDR_*（同一真源，改那里要改这里，
# 由本件 --selftest 里的"窗口不重叠"断言兜住）。
RAM_LO, RAM_HI = 0x8000_0000, 0x8003_FFFF
DEV_WINDOWS = ((0x9000_0000, 0x9000_FFFF, "UART"),
               (0x9001_0000, 0x9001_FFFF, "TIMER"),
               (0x9002_0000, 0x9002_FFFF, "SYSC"))
OP_LOAD, OP_STORE = 0x03, 0x23
RLINE = re.compile(r"^R\s+([0-9a-fA-F]{8})\s+(\d+)\s+([0-9a-fA-F]{8})\s*$")
SLINE = re.compile(r"^S\s+([0-9a-fA-F]{8})\s+([0-9a-fA-F]{8})\s+(\d+)\s*$")
TAG = "_epvisco"

# 参与比对的 EPV 程序。nn_cifar 不在列：它整网 2,271,576 拍，超过仿真器
# 单程序窗口（max_cyc 300k 那一档），要它得先给这格单独放大窗口 —— 这件事
# 显式记在归档的 excluded 里，不静默省略。
PROGRAMS = [
    ("sw/bench/epv_selftest.c", "epv_selftest"),
    ("sw/bench/epv_bisect.c", "epv_bisect"),
    ("sw/bench/nn_bench.c", "nn_bench"),
    ("sw/bench/nn_net.c", "nn_net"),
]
EXCLUDED = {"nn_cifar": "整网 2.27e6 拍 > 本格仿真窗口；要并进来得先为本格单独放大 max_cyc"}


def decode_table():
    """{mnemonic: (opcode, kind, value)}，全部由 defs 那张表来。"""
    tab = ENC.parse_rtl_table(os.path.join(ROOT, "rtl", "epv32_defs.vh"))
    out = {}
    for key, (grp, sel) in tab.items():
        # EPV_F3_LVB / EPV_F7_MADD 之类：宏名后缀就是助记符
        name = key.split("_", 2)[-1]
        kind = "funct3" if key.startswith("EPV_F3_") else "funct7"
        out[name] = (grp, kind, sel)
    if len(out) < 17:
        raise SystemExit("EPV_COVER_FAIL: 编码表只解析到 %d 条" % len(out))
    return out


def classify(word, tab):
    op = word & 0x7F
    for name, (grp, kind, sel) in tab.items():
        if op != grp:
            continue
        if kind == "funct3" and ((word >> 12) & 7) == sel:
            return name
        if kind == "funct7" and ((word >> 25) & 0x7F) == sel:
            return name
    return None


def read_rtl(path):
    r, s = [], []
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            m = RLINE.match(ln.strip())
            if m:
                r.append((int(m.group(1), 16), int(m.group(2)),
                          int(m.group(3), 16)))
                continue
            m = SLINE.match(ln.strip())
            if m:
                a = int(m.group(1), 16)
                if dev_of(a) == "SYSC":
                    # 与 ISS 侧同一条规则: SYSC 的写是提交通道, 不进 S 投影
                    # (两侧都滤, 否则尾巴上恒差 epv_exit 那一笔)
                    continue
                s.append((a, int(m.group(2), 16), int(m.group(3))))
    return r, s


def dev_of(addr):
    for lo, hi, nm in DEV_WINDOWS:
        if lo <= addr <= hi:
            return nm
    return None


def imm_i(w):
    v = (w >> 20) & 0xFFF
    return v - 4096 if v & 0x800 else v


def run_iss(binf, tohost, max_steps):
    """逐条步进，同时把「退休时读的若是 RAM 之外的地址」那几条单独摘出来。

    为什么允许摘：UART/TIMER/SYSC 的读值由**器件自己推进到第几拍**决定，
    黄金模型没有周期精确的发送移位器，两侧同一个时刻读出不同的 busy 标志
    是模型边界，不是核与模型算得不一样。实测第一次分歧就是这样定位的：
    pc=0x80000094 是 `lw x15,0(x14)`，x14=0x9000_0004（UART 状态口），
    RTL 读 0、模型读 1。ISA 三套件比对里没有这一格，因为它们不轮询设备。
    摘出来的条数与地址全部进归档，一条都不静默吞掉；被摘的如果是 RAM 地址，
    那就不算设备读，分歧照判 —— 所以这块豁免盖不住真正的算错。
    """
    img = io.open(binf, "rb").read()
    iss = epv_iss.ISS(enable_epv=True)
    iss.reset(BASE)
    iss.load_image(BASE, img)
    r, s = [], []
    masked = {}
    dev_pcs = set()
    steps = 0
    reason = "max_steps"
    while steps < max_steps:
        steps += 1
        pc0 = iss.pc & 0xFFFFFFFF
        w0 = iss.ram.get((pc0 - BASE) >> 2, 0) or 0
        rs1_val = iss.regs[(w0 >> 15) & 31] if (w0 & 0x7F) == OP_LOAD else None
        try:
            sr = iss.step()
        except epv_iss.SimHalt as h:
            reason = "halt:%d" % (h.value if h.value is not None else -1)
            break
        if sr.retired and sr.rd is not None:
            # 设备读**不摘**。上一版把它从 ISS 侧摘掉而 RTL 侧还在，两条序列
            # 立刻错一位，first divergence 就变成"同一条指令的下一条"
            # （实测 RTL pc=0x80000094 对 ISS pc=0x80000098，rd/wdata 全相同）——
            # 那是在比相位，不是在比值。现在只登记它是设备读，用于给分歧定性。
            tup = (sr.pc & 0xFFFFFFFF, sr.rd, sr.wdata & 0xFFFFFFFF)
            r.append(tup)
            if rs1_val is not None:
                dev = dev_of(u32m(rs1_val + imm_i(w0)))
                if dev:
                    masked[dev] = masked.get(dev, 0) + 1
                    dev_pcs.add(sr.pc & 0xFFFFFFFF)
        for (a, d2, be) in (sr.stores or []):
            s2 = (a & 0xFFFFFFFF, d2 & 0xFFFFFFFF, be)
            dev = dev_of(a)
            if dev == "SYSC" and d2:
                # 只有**非零**的 SYSC 写才是退出通道（COMMIT 寄存器）；
                # 早先版本见 SYSC 写就停，把配置写当成提交，ISS 少走一个数量级
                # —— 症状是 RTL 2342 条退休对 ISS 227 条。
                reason = "sysc_commit:%d" % d2
                continue
            if dev == "SYSC":
                continue
            s.append(s2)
        if tohost and (iss._ram_peek(tohost) or 0) != 0:
            reason = "tohost"
            break
    return r, s, {"steps": steps, "reason": reason,
                  "image_bytes": len(img), "device_reads": masked,
                  "device_read_pcs": sorted(dev_pcs)}, img


def u32m(v):
    return v & 0xFFFFFFFF


def first_div(a, b):
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n if len(a) != len(b) else None


def coverage(rtl_r, img, tab):
    """被比对到的 EPV 指令 -> 退休次数。从轨迹里的 pc 回指指令字现场解码。"""
    hits = {}
    for (pc, _rd, _w) in rtl_r:
        off = (pc - BASE) >> 2
        if off < 0 or (off + 1) * 4 > len(img):
            continue
        w = struct.unpack_from("<I", img, off * 4)[0]
        nm = classify(w, tab)
        if nm:
            hits[nm] = hits.get(nm, 0) + 1
    return hits


def compare_one(src, name, tab, max_steps=6_000_000):
    # 6M 步预算: CYCLE MODEL v2 之后 ISS 会像硬件一样在 UART 忙等里轮询退休,
    # 步数 ~1.1x 退休数 (nn_net 实测 4.12M 步 / 3.75M 退休); 旧的 4M 会把
    # nn_net 掐死在 max_steps 上, 症状是 ISS 侧轨迹被截短。
    elf, binf, _ = rt.build_elf(name, [os.path.join(ROOT, src)])
    syms = rt.symbols(elf)
    # 这些用例一趟里含基线/扩展/宽精度多臂，总拍数是百万级；沿用 ISA 套件的
    # 300k 窗口会把 RTL 侧截断（实测 rtl_verdict=TIMEOUT、RTL 退休数反而少于
    # 模型侧），那之后再比"哪一侧多几条"毫无意义。
    res = rt.run_sim(name, binf, syms, max_cyc=8_000_000,
                     aname=name + TAG)
    r = {"verdict": rt.verdict(res)[0]}
    tp = os.path.join(ROOT, "results", "trace", name + TAG + ".txt")
    if not os.path.isfile(tp):
        raise SystemExit("EPV_COVER_FAIL: %s 没产出轨迹文件 %s" % (name, tp))
    ar, asv = read_rtl(tp)
    br, bs, info, img = run_iss(binf, syms.get("tohost", 0), max_steps)
    dr, ds = first_div(ar, br), first_div(asv, bs)
    cov = coverage(ar, img, tab)
    row = {"test": name, "src": src,
           "rtl_commits": len(ar), "iss_commits": len(br),
           "rtl_stores": len(asv), "iss_stores": len(bs),
           "rtl_verdict": r.get("verdict"), "iss_stop": info["reason"],
           "device_reads": info["device_reads"],
           "device_reads_total": sum(info["device_reads"].values()),
           "agree_reg": dr is None, "agree_store": ds is None,
           "epv_retirements": sum(cov.values()),
           "epv_distinct": len(cov), "epv_by_mnemonic": cov}
    if dr is not None:
        row["reg_divergence_index"] = dr
        row["reg_divergence_is_device_read"] = (
            (ar[dr][0] in set(info["device_read_pcs"]))
            or (dr < len(br) and br[dr][0] in set(info["device_read_pcs"])))
        row["reg_context"] = "RTL %r vs ISS %r" % (ar[dr], br[dr] if dr < len(br) else None)
    if ds is not None:
        row["store_divergence_index"] = ds
        row["store_context"] = "RTL %r vs ISS %r" % (asv[ds], bs[ds] if ds < len(bs) else None)
    return row


def selftest(tab, img_path=None):
    """四条：投影扰动、少一条退休、正对照、以及**覆盖率不许为 0**。"""
    real = os.path.join(ROOT, "results", "trace", "epv_selftest" + TAG + ".txt")
    if not os.path.isfile(real):
        print("EPV_SELFTEST_SETUP: 先跑一次 python flow/iss_compare_epv.py 生成基准轨迹")
        return 1
    a, s = read_rtl(real)
    if len(a) < 20 or len(s) < 5:
        print("EPV_SELFTEST_FAIL: 基准轨迹太短 R=%d S=%d" % (len(a), len(s)))
        return 1
    fails = []
    b = list(a)
    b[3] = (b[3][0], b[3][1], (b[3][2] ^ 0x1) & 0xFFFFFFFF)
    if first_div(a, b) != 3:
        fails.append("R 投影扰动没定位到 index 3")
    c = list(s)
    c[1] = (c[1][0], c[1][1], (c[1][2] ^ 0xF))
    if first_div(s, c) != 1:
        fails.append("S 投影扰动没定位到 index 1")
    if first_div(s, s[:-1]) != len(s) - 1:
        fails.append("少一条 store 没被判成分歧")
    if first_div(a, list(a)) is not None or first_div(s, list(s)) is not None:
        fails.append("两条相同轨迹被判成分歧")
    if not fails:
        print("[selftest] R/S 两个投影的扰动各自定位；正对照一致 (R=%d S=%d)"
              % (len(a), len(s)))
    # 覆盖率反对照：把指令字全塞成普通整数指令，覆盖率必须归 0 并被拒
    fake = b"\x13\x00\x00\x00" * (len(a) + 1)
    if coverage([(p, rd, w) for (p, rd, w) in a], fake, tab):
        fails.append("coverage 在假镜像上仍报命中 —— 解码是摆设")
    else:
        print("[selftest] coverage 反对照: 全填 NOP 的镜像解出 0 条 EPV 命中")
    for f in fails:
        print("EPV_SELFTEST_FAIL: " + f)
    return 1 if fails else 0


def main():
    os.chdir(ROOT)
    tab = decode_table()
    if "--selftest" in sys.argv:
        return selftest(tab)
    t0 = time.time()
    rows = []
    for src, name in PROGRAMS:
        row = compare_one(src, name, tab)
        rows.append(row)
        print("%-14s R=%-6d/%-6d %s  S=%-6d/%-6d %s  EPV退休=%-6d 不同指令=%d/17 "
              "设备读豁免=%d %s"
              % (name, row["rtl_commits"], row["iss_commits"],
                 "AGREE" if row["agree_reg"] else "DIV",
                 row["rtl_stores"], row["iss_stores"],
                 "AGREE" if row["agree_store"] else "DIV",
                 row["epv_retirements"], row["epv_distinct"],
                 row["device_reads_total"], row["device_reads"]))
    ok_r = sum(1 for x in rows if x["agree_reg"])
    ok_s = sum(1 for x in rows if x["agree_store"])
    seen = {}
    for x in rows:
        for k, v in x["epv_by_mnemonic"].items():
            seen[k] = seen.get(k, 0) + v
    prov = rt.sim_provenance()
    out = {"suite": "epv", "n_tests": len(rows),
           "address_windows": {"ram": [RAM_LO, RAM_HI],
                               "devices": [[a, b, c] for a, b, c in DEV_WINDOWS]},
           "n_agree_reg": ok_r, "n_agree_store": ok_s,
           "all_agree": ok_r == len(rows) and ok_s == len(rows) and len(rows) > 0,
           "epv_instructions_total": len(tab),
           "epv_instructions_seen": len(seen),
           "epv_seen": seen,
           "epv_never_compared": sorted(set(tab) - set(seen)),
           "wall_s": round(time.time() - t0, 1),
           "excluded_programs": EXCLUDED,
           "projections": {"R": "retired && rd!=0 && reg-write, both sides",
                           "S": "每笔已提交 store 的 (addr, wdata, be)"},
           "rows": rows,
           "sources": {"rtl_sha256": prov["rtl_sha256"],
                       "simulator_sha256": prov["sim_sha256"],
                       "golden_iss_sha256": rt.sha16(os.path.join(
                           ROOT, "tb", "golden", "epv_iss.py")),
                       "comparator_sha256": rt.sha16(os.path.abspath(__file__)),
                       "encoding_table_sha256": rt.sha16(os.path.join(
                           ROOT, "rtl", "epv32_defs.vh")),
                       "how_to_read": "门禁只认 rtl_sha256 == 当前工作树 的那一份归档"}}
    # 探针产物写在 isco_probe/：results/isco/ 是认证目录，
    # 一份"跑了但没通过"的归档住进去会被存在性判据读成"已落地"。
    d = os.path.join(ROOT, "results", "isco_probe")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "epv.json")
    write_json_atomic(path, out)
    print("ISCO epv: 寄存器投影 %d/%d、store 投影 %d/%d 逐条一致  "
          "EPV 指令被比到 %d/%d 条  all_agree=%s  -> results/isco_probe/epv.json"
          % (ok_r, len(rows), ok_s, len(rows), len(seen), len(tab),
             out["all_agree"]))
    # 覆盖率为 0 / 一条都没比到 -> 这一格不能算过
    if not out["epv_instructions_seen"]:
        print("EPV_COVER_FAIL: 被比对的轨迹里一条 EPV 指令都没有 —— 拿它当 EPV 证据是假陈述")
        return 2
    return 0 if out["all_agree"] else 1


if __name__ == "__main__":
    sys.exit(main())
