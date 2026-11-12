#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/ab_m6_m7.py -- 把「M7 那条 IALIGN 判据到底花了多少 LUT」测出来, 而不是猜出来。

背景: 改完 rtl/epv32_core.v 的 br_err/jr_err (加上 IALIGN=32 判据) 之后重跑 TD,
综合口径从 7959/3008 变成 8388/3123 —— 两档各涨约 4~5%。一条两个输入的 OR
项不该值这么多, 但「不该」不是证据。本脚本做的是受控对照:
    A 臂 = 当前核心 (M7), B 臂 = 上一版核心 (M6), 其余全部逐字节相同
    (同一份 tcl、同一个 10 ns 约束、同一版 synth_wrappers.v、同一 top 模块名)。
唯一自由度是 epv32_core.v 那一个文件, 而且脚本会**先把这个不变量核一遍**:
除它以外任何一份输入不同, 直接退出, 不许把这组数当对照。

M6 那份源码从哪儿来: results/pnr/run2s_*/rtl/epv32_core.v —— 那是上一轮刷新
留下的臂副本, 哈希正是旧 summary2 记的 19af2b9582bd。

产物只写带 tag 的归档 results/pnr/ab_m6_vs_m7.json, **不碰** summary2.json:
额外实验不许覆盖认证档 (这条规则是上一轮被一次截断运行烧出来的)。
"""
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "flow"))
from atomic_json import write_json_atomic      # 归档落盘的唯一实现
RUN = r"E:\Anlogic\TD_6.2.1_Engineer_6.2.168.116\bin\run.exe"
SRC_M6 = "results/pnr/run2s_epv/rtl/epv32_core.v"
M6_HASH = "19af2b9582bd"           # 旧 summary2 记的前 12 位, 只用来核对副本没拿错
PAIRS = [("epv", "results/pnr/run3s_epv", "results/pnr/ab_m6_epv"),
         ("noepv", "results/pnr/run3s_noepv", "results/pnr/ab_m6_noepv")]
OUT = os.path.join("results", "pnr", "ab_m6_vs_m7.json")


def sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def die(msg):
    raise SystemExit("AB_FAIL: " + msg)


def build_arm(live_dir, ab_dir):
    """把现成综合臂复制成 B 臂, 再把核心换成 M6 那份, 并**删掉带过来的报告**。

    删报告这一步不是洁癖: 不删的话, TD 万一没跑成, 目录里还留着上一臂(M7)的
    gate.area, 读它就等于把 M7 的数印成 M6 的。删了才会 die。
    """
    if os.path.isdir(ab_dir):
        shutil.rmtree(ab_dir)
    shutil.copytree(live_dir, ab_dir)
    for junk in ("gate.area", "gate_timing.rpt", "rtl.area", "gate.qor",
                 "epv_flow.out", "noepv_flow.out",
                 "epv_flow.ab.out", "noepv_flow.ab.out",
                 "epv_flow.ab.err", "noepv_flow.ab.err"):
        p = os.path.join(ab_dir, junk)
        if os.path.isfile(p):
            os.remove(p)
    m6 = io.open(SRC_M6, encoding="utf-8").read()
    if not sha(SRC_M6).startswith(M6_HASH):
        die("副本 %s 的哈希 %s 与旧归档记的 %s... 不符, 拿错源码了"
            % (SRC_M6, sha(SRC_M6)[:12], M6_HASH))
    with io.open(os.path.join(ab_dir, "rtl", "epv32_core.v"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write(m6)
    return ab_dir


def inputs_of(arm_dir, tcl):
    t = io.open(os.path.join(arm_dir, tcl), encoding="utf-8",
                errors="replace").read()
    m = re.search(r"(?m)^set\s+FILES\s+\{([^}]*)\}", t)
    if not m:
        die("%s 里没有 set FILES" % tcl)
    out = {}
    for rel in m.group(1).split():
        p = os.path.join(arm_dir, rel.lstrip("./").replace("/", os.sep))
        out[rel] = (sha(p), os.path.getsize(p))
    return out


def run_arm(arm_dir, tcl):
    name = os.path.splitext(tcl)[0]
    with io.open(os.path.join(arm_dir, name + ".ab.out"), "w",
                 encoding="utf-8", errors="replace") as fo:
        with io.open(os.path.join(arm_dir, name + ".ab.err"), "w",
                     encoding="utf-8", errors="replace") as fe:
            rc = subprocess.call([RUN, "-b", tcl], cwd=arm_dir,
                                 stdout=fo, stderr=fe, stdin=subprocess.DEVNULL)
    log = io.open(os.path.join(arm_dir, name + ".ab.out"), encoding="utf-8",
                  errors="replace").read()
    return rc, log


def read_gate(log_dir, log_name, need_fmax=True):
    t = io.open(os.path.join(log_dir, "gate.area"), encoding="utf-8",
                errors="replace").read()
    m = re.search(r"(?m)^#Total_luts\s+(\d+)", t)
    if not m:
        die("%s 里没有 #Total_luts" % log_dir)
    if "MARK-061-report_area_gate-OK" not in io.open(
            os.path.join(log_dir, log_name), encoding="utf-8",
            errors="replace").read():
        die("%s 没打 MARK-061-report_area_gate-OK, 这份 gate.area 不算跑成"
            % log_dir)
    tt = io.open(os.path.join(log_dir, "gate_timing.rpt"), encoding="utf-8",
                 errors="replace").read()
    fm = [float(x) for x in re.findall(r"(?m)^Fmax\s*:\s*([\d.]+)\s*MHz", tt)]
    if not fm and need_fmax:
        die("%s 的 gate_timing.rpt 里没有 Fmax 行" % log_dir)
    d = {"total_luts": int(m.group(1))}
    if fm:
        d["fmax_mhz"] = min(fm)
    return d


def nosdc_variant():
    """正文那句「综合阶段喂不喂约束会改变面积」的两个数, 必须在**当前这一版
    源码**上重测, 不许沿用上一版核的 7908/7959。做法: 只把 top.sdc 换成不含
    create_clock 的说明行, 其余逐字节相同 (read_sdc 这一步照常执行)。"""
    out = {}
    for tag, live in (("epv", "results/pnr/run3s_epv"),
                      ("noepv", "results/pnr/run3s_noepv")):
        tcl = "epv_flow.tcl" if "noepv" not in live else "noepv_flow.tcl"
        ab = live + "_nosdc"
        if os.path.isdir(ab):
            shutil.rmtree(ab)
        shutil.copytree(live, ab)
        for junk in ("gate.area", "gate_timing.rpt", "rtl.area", "gate.qor",
                     "epv_flow.out", "noepv_flow.out",
                     "epv_flow.ab.out", "noepv_flow.ab.out"):
            if os.path.isfile(os.path.join(ab, junk)):
                os.remove(os.path.join(ab, junk))
        with io.open(os.path.join(ab, "top.sdc"), "w", encoding="utf-8",
                     newline="\n") as f:
            f.write("# deliberately empty: no create_clock in this arm\n")
        # TD 的 read_sdc 对「只含注释」的 sdc 直接失败且错误信息为空(实测),
        # 所以无约束档只能用「干脆没有这一步」来构造 —— 与当年 run2s_* 的做法一致。
        tp = os.path.join(ab, tcl)
        t = io.open(tp, encoding="utf-8").read()
        kept = [ln for ln in t.splitlines() if not ln.startswith("step 050-read_sdc")]
        if len(kept) == len(t.splitlines()):
            die("%s 里找不到 step 050-read_sdc, 无从构造无约束臂" % tcl)
        with io.open(tp, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(kept) + "\n")
        a = inputs_of(live, tcl)
        b = inputs_of(ab, tcl)
        diff = sorted(k for k in a if a[k][0] != b[k][0])
        if diff:
            die("无约束臂与有约束臂连输入都没对上: %s" % diff)
        name = os.path.splitext(tcl)[0]
        rc, log = run_arm(ab, tcl)
        if "MARK-050-read_sdc" in log:
            die("无约束臂里 read_sdc 还是跑了, 这一组对照作废 (rc=%d)" % rc)
        if "MARK-061-report_area_gate-OK" not in log:
            die("无约束臂没打到 MARK-061, 取不到 gate.area (rc=%d)" % rc)
        now = read_gate(ab, name + ".ab.out", need_fmax=False)
        had = read_gate(live, name + ".out")
        out[tag] = {"with_sdc_10ns": had, "no_sdc": now,
                    "delta_luts": had["total_luts"] - now["total_luts"]}
        print("%-6s 无约束=%-5d 10ns约束=%-5d 差 %+d  Fmax(有约束) %.3f"
              % (tag, now["total_luts"], had["total_luts"],
                 had["total_luts"] - now["total_luts"], had["fmax_mhz"]))
    p = os.path.join("results", "pnr", "ab_sdc_sensitivity.json")
    write_json_atomic(p, {"what": "同一版 RTL、同一份 tcl, 只把 top.sdc 清空, 看综合口径 "
                                  "#Total_luts 变不变",
                          "note": "Fmax 在无约束臂没有意义 (没有时钟定义), 只取有约束臂那条",
                          "arms": out})
    print("AB_NOSDC_OK -> results/pnr/ab_sdc_sensitivity.json")
    return 0


def main():
    if "--nosdc" in sys.argv[1:]:
        return nosdc_variant()
    if "--dry" in sys.argv[1:]:
        for _, live, ab in PAIRS:
            tcl = "noepv_flow.tcl" if "noepv" in live else "epv_flow.tcl"
            build_arm(live, ab)
            a, b = inputs_of(live, tcl), inputs_of(ab, tcl)
            print("DRY %s/%s: %d 份输入, 差异文件 %s"
                  % (live, ab, len(a),
                     sorted(k for k in a if a[k][0] != b[k][0])))
        print("AB_DRY_OK")
        return 0

    res = {}
    for tag, live, ab in PAIRS:
        # 注意: "run3s_noepv".endswith("epv") 也是 True, 所以必须先看 noepv。
        tcl = "noepv_flow.tcl" if "noepv" in live else "epv_flow.tcl"
        build_arm(live, ab)
        ia, ib = inputs_of(live, tcl), inputs_of(ab, tcl)
        if set(ia) != set(ib):
            die("两臂输入清单不一致: %s vs %s" % (sorted(ia), sorted(ib)))
        diff = sorted(k for k in ia if ia[k][0] != ib[k][0])
        if diff != ["./rtl/epv32_core.v"]:
            die("对照不成立 —— 除了 epv32_core.v 还有这些也不同: %s" % diff)
        rc, _ = run_arm(ab, tcl)
        b = read_gate(ab, os.path.splitext(tcl)[0] + ".ab.out")
        a = read_gate(live, os.path.splitext(tcl)[0] + ".out")
        res[tag] = {
            "held_fixed": {k: ia[k][0] for k in ia if k not in diff},
            "varying": {"./rtl/epv32_core.v":
                        {"M7_current": ia["./rtl/epv32_core.v"][0],
                         "M6_copy": ib["./rtl/epv32_core.v"][0]}},
            "m7": dict(a, dir=live, td_rc_note="rc 不是判据, 看 MARK 行"),
            "m6": dict(b, dir=ab, rc=rc)}
        res[tag]["delta_m7_minus_m6_luts"] = a["total_luts"] - b["total_luts"]
        print("%-6s M6=%-5d M7=%-5d  d=%+d   Fmax %.3f -> %.3f MHz"
              % (tag, b["total_luts"], a["total_luts"],
                 a["total_luts"] - b["total_luts"],
                 b["fmax_mhz"], a["fmax_mhz"]))
    out = {"what": "同一套 TD 流程下, 只换 rtl/epv32_core.v 一个文件 (M6 vs M7) "
                   "的综合口径对照",
           "why": "M7 之后正文两档 LUT 都涨了 ~4%, 这个涨幅必须由对照测出, "
                  "不许写成『两个 OR 项不该涨这么多』这种推断",
           "tool": "Anlogic TD V6.2.168116, EG4S20BG256, 约束 10.000 ns",
           "run_rc_note": "TD 在写完报告后会 SIGSEGV, 所以 rc 一律不采信; "
                          "取数条件是 MARK-061-report_area_gate-OK 与报告文件同时存在",
           "arms": res}
    write_json_atomic(OUT, out)
    print("AB_OK -> results/pnr/ab_m6_vs_m7.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
