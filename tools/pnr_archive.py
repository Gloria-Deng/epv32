#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/pnr_archive.py -- 把安路 TD 两档报告汇成「与当前 RTL 同源」的器件口径归档。

为什么要单独一个归档器: RTL 一改(例如 M6 加了硬件非对齐访存), 上一版 TD 报告
就不再描述现在这份代码。原稿把数写在 results/pnr/summary.json 里, 而那份文件
**没记它对应哪份 RTL** —— 于是"正文里的 LUT 数"和"当前核的 LUT 数"可以悄悄脱钩
(今天真的发生了: 改完 RTL 后旧 summary 仍然全绿)。本归档器做两件事:
  1. 产 results/pnr/summary2.json, 逐文件记 rtl 的 sha256 作为新鲜度键,
     并沿用 summary.json 的字段形状(正文锚点只认一套 schema);
  2. 只认 MARK 行: 报告文件存在 **且** 产生它的那一步打了 MARK-...-OK 才取数,
     否则 die —— TD 在写完报告之后会 SIGSEGV(rc=139), 反过来"崩之前没写完"
     也是真的, 所以退出码和文件存在性都不是判据。

跑法: 先 bash flow/run_td_refresh.sh, 再 python tools/pnr_archive.py
"""
import hashlib
import io
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "flow"))
from atomic_json import write_json_atomic      # 归档落盘的唯一实现
sys.dont_write_bytecode = True  # 交付包内不留 __pycache__（flow/check_package_hygiene.py 判红）

DEVICE = "EG4S20BG256"
DEVICE_DB = "eagle_s20.db"
TD_VERSION = "V6.2.168116"
CLOCK_NS = 10.0
OUT = os.path.join("results", "pnr", "summary2.json")

# 交付顶层(综合 + 综合口径时序) / 引脚压缩替身档(完整 place+route)
# 每个臂同时记 tcl 文件名: flow/run_td_refresh.sh 与本文件都从这里取作业清单,
# 免得驱动跑 run2s_*、归档读 run3s_*, 两边各说各话(这一条真的错过一次, 见下)。
ARMS = {
    "epv": {"synth_dir": "results/pnr/run3s_epv", "synth_out": "epv_flow.out",
            "synth_tcl": "epv_flow.tcl",
            "top": "epv32_core_epv",
            "pnr_dir": "results/pnr/run2_epv", "pnr_out": "pnr_epv_flow.out",
            "pnr_tcl": "pnr_epv_flow.tcl",
            "pnr_top": "epv32_core_epv_pnr"},
    "baseline": {"synth_dir": "results/pnr/run3s_noepv", "synth_out": "noepv_flow.out",
                 "synth_tcl": "noepv_flow.tcl",
                 "top": "epv32_core_noepv",
                 "pnr_dir": "results/pnr/run2_noepv", "pnr_out": "pnr_noepv_flow.out",
                 "pnr_tcl": "pnr_noepv_flow.tcl",
                 "pnr_top": "epv32_core_noepv_pnr"},
}



def bitgen_verdict(plog, d):
    """从替身档 P&R log 里现读 bitgen 的判决。正文 §3.6 只许抄这个字段。

    为什么单独立一个字段（而不是让散文自己说）：报告原先写"bitgen 在替身档两臂
    各出过一次 `.bit`"，而两臂 log 实际印的是 `BIT-8123 unconstrained io` →
    `MARK-100-bitgen-FAILED` 与 `ARTIFACT-pnr_*.bit-MISSING`，全包没有任何一份
    log 给过 `MARK-100-bitgen-OK`。步骤表里 step 100 一直在跑，但**没有任何判据
    看过它的结果**，于是文档可以怎么说都行。这一条是评审也没抓到的一处虚构风险。
    缺 MARK-100 行时直接 die：那说明这一档连 bitgen 都没进，更不许沿用说法。
    """
    m = re.search(r"MARK-100-bitgen-([A-Za-z]+)", plog)
    if not m:
        die("%s 的 P&R log 里没有 MARK-100-bitgen-* 行 —— 这一档没跑到 bitgen，"
            "正文不许出现任何比特流说法" % d)
    verdict = m.group(1)
    reason = ""
    if verdict != "OK":
        rr = re.search(r"(BIT-\d+[^\n]*)", plog)
        reason = rr.group(1).strip() if rr else "失败原因未在 log 里印出"
    am = re.search(r"ARTIFACT-([A-Za-z0-9_.]+\.bit)-(MISSING|SIZE=(\d+))", plog)
    artifact = (am.group(1) + "=" + am.group(2)) if am else "无 .bit 产物行"
    on_disk = sorted(f for f in os.listdir(d) if f.endswith(".bit"))
    return {"mark": "MARK-100-bitgen-" + verdict,
            "reason": reason,
            "log_artifact_line": artifact,
            "bit_files_in_arm_dir": on_disk,
            "bitstream_produced": verdict == "OK" and bool(on_disk),
            "flashed": False,
            "how_to_read": "bitstream_produced=false 时正文只许写"
                           "「未拿到比特流」；这张表由本函数现读 log，不改阈值。"}


def die(msg):
    raise SystemExit("PNR_ARCHIVE_FAIL: " + msg)


def rtl_hashes():
    out = {}
    for fn in sorted(os.listdir("rtl")):
        if fn.endswith((".v", ".vh")):
            h = hashlib.sha256()
            with open(os.path.join("rtl", fn), "rb") as f:
                for blk in iter(lambda: f.read(65536), b""):
                    h.update(blk)
            out[fn] = h.hexdigest()
    if not out:
        die("rtl/ 里一个源文件都没有")
    return out


def read(path):
    if not os.path.isfile(path):
        die("缺文件 %s" % path)
    return io.open(path, encoding="utf-8", errors="replace").read()


# ----------------------------------------------------------- 源码同一性核对
# 为什么必须有这一段: TD 每个臂吃的不是 <项目>/rtl, 而是它自己目录下那份
# **副本**(arm/rtl/*.v)。副本是 flow/make_td_arms.py 拷进去的 —— 也就是说
# 「报告描述的是哪一版 RTL」这件事, 光看 rtl_sha256 字段完全证不了:
# 那份字段是归档器按**当前工作树**算的, 而报告数字来自**副本**。
# 于是原先顶层那个 "verified_against_source": True 是一个写死的字面量,
# flow/run_all.py 那道口 `is True` 永远绿 —— 它一次都没有核过任何东西。
def file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return h.hexdigest()


def tcl_files(tcl_text, src):
    """把 arm 的 tcl 里 `set FILES {...}` 那一行摊开 —— 那才是 TD 实际读进去的清单。"""
    m = re.search(r"(?m)^set\s+FILES\s+\{([^}]*)\}", tcl_text)
    if not m:
        die("%s 里找不到 set FILES {...} 那一行" % src)
    return [t.strip() for t in m.group(1).split() if t.strip()]


def check_arm_sources(arm_dir, tcl_rel, cur_hashes, gen_files, root):
    """逐个核 arm 的输入副本 == 当前树; 任何一个不同就 die 并点名文件。

    cur_hashes: 当前 rtl/ 的逐文件哈希; gen_files: {相对路径: 哈希} covering
    由脚本生成的输入(rtl/synth_wrappers.v 与 results/pnr/pnr_tops.v)。
    返回 {rel: sha}, 供归档里当证据记下。
    """
    tcl_path = os.path.join(root, arm_dir, tcl_rel)
    got = {}
    for rel in tcl_files(read(tcl_path), tcl_rel):
        arm_p = os.path.normpath(os.path.join(root, arm_dir, rel))
        if not os.path.isfile(arm_p):
            die("%s 的 tcl 列了 %s, 但臂目录里没有这份副本" % (arm_dir, rel))
        key = os.path.basename(rel)
        if rel.startswith("./rtl/"):
            want = cur_hashes.get(key)
        elif key in gen_files:
            want, _origin = gen_files[key]
        else:
            die("%s 读了 %s, 但这份输入没有登记出处, 无法证明它对应哪版源码"
                % (arm_dir, rel))
        if want is None:
            die("%s 读了 %s, 当前树里却没有这份文件" % (arm_dir, rel))
        have = file_sha(arm_p)
        if have != want:
            die("%s 里那份 %s 与当前源码不同 (副本 %.12s vs 当前 %.12s) —— "
                "这份 TD 报告描述的不是现在这版 RTL, 先跑 flow/make_td_arms.py "
                "再跑 TD" % (arm_dir, rel, have, want))
        got[rel] = have
    if not got:
        die("%s 一个输入文件都没核 —— 这道核对是空的" % arm_dir)
    return got


def gen_inputs(root=ROOT):
    """本归档器允许出现在 tcl FILES 里的「生成件」: 文件名 -> (哈希, 相对路径)。"""
    rels = {"synth_wrappers.v": os.path.join("rtl", "synth_wrappers.v"),
            "pnr_tops.v": os.path.join("results", "pnr", "pnr_tops.v")}
    out = {}
    for name, rel in rels.items():
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            die("生成件 %s 不存在 —— 先跑 tools/make_wrappers.py 与 "
                "results/pnr/make_pnr_tops.py" % rel)
        out[name] = (file_sha(p), rel)
    return out


MANIFEST_REL = os.path.join("results", "pnr", "arms_manifest.json")


def load_manifest(root=ROOT):
    """flow/make_td_arms.py 登记的那份 run_id。没有它就没法证明 log 是哪版源码跑的。"""
    p = os.path.join(root, MANIFEST_REL)
    if not os.path.isfile(p):
        die("缺 %s —— 先跑 python flow/make_td_arms.py (它把当前 RTL 铺成四个臂, "
            "并把 run_id 写进每个 tcl 的 ARM-RUN-ID 行)" % MANIFEST_REL.replace(os.sep, "/"))
    m = json.load(io.open(p, encoding="utf-8"))
    if not m.get("run_id"):
        die("%s 里没有 run_id" % MANIFEST_REL.replace(os.sep, "/"))
    return m


def check_run_id(log_text, run_id, src):
    """TD log 必须自己带着「我是哪版源码跑出来的」。

    副本哈希对得上只说明**现在**臂目录里是这份源码; log 是上一次跑留下的也会这样。
    run_id 由 RTL+生成件的哈希算出、由 make_td_arms 写进 tcl, TD 把它 echo 进 log,
    于是「旧 RTL 的 log」在这一条上必红, 而 RTL 没变时重跑又不会误伤。
    """
    have = set(re.findall(r"ARM-RUN-ID-([0-9a-f]+)", log_text))
    if not have:
        die("%s 里没有 ARM-RUN-ID 行 —— 这份 log 不是 flow/make_td_arms.py 建立的臂"
            "跑出来的(多半是手工建的旧臂); 重跑 make_td_arms + run_td_refresh" % src)
    extra = sorted(have - {run_id})
    if extra:
        if run_id in have:
            die("%s 的 log 里同时印了两个 run_id (%s 与 %s) —— 一次运行只该有一个,"
                " 这份报告的来历不唯一" % (src, run_id, extra[0]))
        die("%s 的 ARM-RUN-ID=%s 与 arms_manifest 的 %s 不同 —— log 描述的是另一版"
            "源码, 不许当现在的数用" % (src, sorted(have)[0], run_id))
    return run_id




def mark_ok(log_text, stage):
    return ("MARK-%s-OK" % stage) in log_text


def parse_area(t, top, src, needs=("total_luts", "lut_sites", "ff", "bram", "dsp")):
    head = "***Report Model: %s " % top
    if head not in t:
        die("%s 的 Report Model 不是 %s —— 拿错档了" % (src, top))

    def num(pat, name, cast=int, opt=None):
        m = re.search(pat, t)
        if not m:
            if name.split()[0].lstrip("#") not in needs:
                return opt
            die("%s 里找不到 %s (TD 换了措辞?)" % (src, name))
        return cast(m.group(1))

    d = {}
    if "total_luts" in needs:
        d["total_luts"] = num(r"(?m)^#Total_luts\s+(\d+)", "#Total_luts")
    d.update({
        "lut_sites": num(r"(?m)^#lut\s+(\d+)\s+out of", "#lut 占位"),
        "lut_pct":   num(r"(?m)^#lut\s+\d+\s+out of\s+\d+\s+([\d.]+)%", "lut 占用率", float),
        "ff":        num(r"(?m)^#reg\s+(\d+)\s+out of", "#reg"),
        "bram":      num(r"(?m)^#bram\s+(\d+)\s+out of", "#bram"),
        "dsp":       num(r"(?m)^#dsp\s+(\d+)\s+out of\s+\d+\s+[\d.]+%", "#dsp")})
    m = re.search(r"(?m)^#dsp\s+\d+\s+out of\s+(\d+)\s+([\d.]+)%", t)
    if not m:
        die("%s 读不到 DSP 分母" % src)
    d["dsp_of"] = int(m.group(1))
    d["dsp_pct"] = float(m.group(2))
    for pat, key in ((r"(?m)^#lut\s+\d+\s+out of\s+(\d+)", "lut_capacity"),
                     (r"(?m)^#reg\s+\d+\s+out of\s+(\d+)", "ff_capacity"),
                     (r"(?m)^#bram\s+\d+\s+out of\s+(\d+)", "bram9k_capacity"),
                     (r"(?m)^#IO\s+\d+\s+out of\s+(\d+)", "io_capacity")):
        d[key] = int(re.search(pat, t).group(1))
    for pat, key in ((r"(?m)^#le\s+(\d+)", "le"),
                     (r"(?m)^#IO\s+(\d+)\s+out of\s+(\d+)", "io_used")):
        m2 = re.search(pat, t)
        if m2:
            d[key] = int(m2.group(1))
    return d


def parse_fmax(t, src):
    """只认 report_timing_summary 的 'Fmax : n MHz' 一行; 多时钟组取最差那条。"""
    hits = [float(x) for x in re.findall(r"(?m)^Fmax\s*:\s*([\d.]+)\s*MHz", t)]
    if not hits:
        die("%s 里没有 'Fmax : n MHz' 行 —— 是没跑成, 还是 TD 改了措辞?" % src)
    wn = re.findall(r"(?m)^WNS\s*:\s*(-?[\d.]+)", t)
    tn = re.findall(r"(?m)^TNS\s*:\s*(-?[\d.]+)", t)
    return {"fmax_mhz": min(hits),
            "wns_ns": (float(wn[0]) / 1000.0) if wn else None,
            "tns_ns": (float(tn[0]) / 1000.0) if tn else None,
            "all_groups_mhz": sorted(hits)}


def fmax_pct(e, b):
    return round(100.0 * (e["fmax_mhz"] / b["fmax_mhz"] - 1.0), 2)


def area_delta(e, b):
    d = {}
    for k in ("total_luts", "lut_sites", "ff", "bram", "dsp"):
        if k in e and k in b:
            d[k + "_abs"] = e[k] - b[k]
    if "total_luts_abs" in d:
        d["total_luts_pct"] = round(100.0 * d["total_luts_abs"] / b["total_luts"], 2)
    if "lut_sites_abs" in d:
        d["lut_sites_pct"] = round(100.0 * d["lut_sites_abs"] / b["lut_sites"], 2)
    if "ff_abs" in d:
        d["ff_pct"] = round(100.0 * d["ff_abs"] / b["ff"], 2)
    return d


def main():
    hs = rtl_hashes()
    # 先把「报告到底描述了哪一版源码」核掉: 每个臂的输入副本必须与本树逐字节相同。
    # 这一步放在取数之前 —— 取数成功了又怎样, 那些数说的是另一版设计。
    gen = gen_inputs()
    man = load_manifest()
    verif, n_files = {}, 0
    for arm, cfg in ARMS.items():
        verif[arm] = {}
        for kind in ("synth", "pnr"):
            got = check_arm_sources(cfg[kind + "_dir"], cfg[kind + "_tcl"], hs, gen, ROOT)
            verif[arm][kind] = got
            n_files += len(got)
    syn, par, proof, module_luts = {}, {}, {}, {}
    for arm, cfg in ARMS.items():
        slog = read(os.path.join(cfg["synth_dir"], cfg["synth_out"]))
        check_run_id(slog, man["run_id"], "%s 综合档的 TD log" % arm)
        if not mark_ok(slog, "061-report_area_gate"):
            die("%s 档综合阶段没打 MARK-061-report_area_gate-OK" % arm)
        gate_area = read(os.path.join(cfg["synth_dir"], "gate.area"))
        syn[arm] = parse_area(gate_area, cfg["top"], "gate.area")
        if not mark_ok(slog, "064-report_timing_summary_gate"):
            die("%s 档没跑综合口径时序(步骤 064) —— 本版不许沿用上一版的 Fmax" % arm)
        syn[arm].update(parse_fmax(read(os.path.join(cfg["synth_dir"], "gate_timing.rpt")),
                                   "gate_timing.rpt"))
        syn[arm]["timing_confidence"] = "Synthesized"

        plog_path = os.path.join(cfg["pnr_dir"], cfg["pnr_out"])
        if os.path.isfile(plog_path):
            plog = read(plog_path)
            check_run_id(plog, man["run_id"], "%s P&R 档的 TD log" % arm)
            if not mark_ok(plog, "091-report_area_route"):
                die("%s 档替身 P&R 没打 MARK-091-report_area_route-OK" % arm)
            par[arm] = parse_area(read(os.path.join(cfg["pnr_dir"], "route.area")),
                                  cfg["pnr_top"], "route.area",
                                  needs=("lut_sites", "ff", "bram", "dsp"))
            par[arm].update(parse_fmax(read(os.path.join(cfg["pnr_dir"], "route_timing.rpt")),
                                       "route_timing.rpt"))
            par[arm]["timing_confidence"] = "Routed"
            par[arm]["bitgen_verdict"] = bitgen_verdict(plog, cfg["pnr_dir"])
        else:
            die("%s 档替身 P&R 还没跑 (缺 %s)" % (arm, plog_path))

        # 归因: epv32_epv 只能出现在 EPV 档的展开模块表里。
        # TD 的层级行长这样: "|    G_EPV$u_epv |epv32_epv      |5607 |1463 |..."
        # —— 实例路径与模块名之间没有 "dut" 这个词, 别按人名去锚。
        m = re.search(r"(?m)^\|[^|\n]*\|\s*epv32_epv\s*\|([^\n]*)", gate_area)
        proof[arm] = bool(m)
        if m:
            cols = [c for c in m.group(1).split("|") if c.strip().isdigit()]
            if cols:
                module_luts[arm] = int(cols[0])
    if not proof["epv"] or proof["baseline"]:
        die("两档的展开模块表分不开 (epv=%s baseline=%s) —— 增量无法归因于扩展"
            % (proof["epv"], proof["baseline"]))

    # 用户 IO 上限取 TD 在 route.area 里自己印的那一分母 (#IO n out of 188),
    # 不去 grep PHY-9016 那句报错 —— 综合档不跑 place, 那句话根本不会出现。
    if "io_capacity" not in par["epv"]:
        die("route.area 里没有 '#IO n out of N' 行, 读不到用户 IO 上限")
    pads = type("M", (), {"group": staticmethod(lambda i, v=par["epv"]["io_capacity"]: v)})()
    cap = {"luts": syn["epv"]["lut_capacity"], "regs_of": syn["epv"]["ff_capacity"],
           "bram9k": syn["epv"]["bram9k_capacity"], "bram32k": 16,
           "dsp": syn["epv"]["dsp_of"], "user_io_pads": int(pads.group(1))}
    sd = area_delta(syn["epv"], syn["baseline"])
    sd["fmax_pct"] = fmax_pct(syn["epv"], syn["baseline"])
    pd = area_delta(par["epv"], par["baseline"])
    pd["fmax_pct"] = fmax_pct(par["epv"], par["baseline"])

    out = {
        "revision": "M6 之后 (硬件非对齐访存) 的器件口径复跑",
        "supersedes": "results/pnr/summary.json",
        "why_superseded": "summary.json 没有 rtl_sha256 键, 无法证明它对应哪一版 RTL; "
                          "本文件逐文件记哈希, 门禁按哈希选档。",
        "verified_against_source": {
            "method": "把每个臂目录里的输入副本(epv_flow.tcl 的 set FILES 那一行列出的"
                      "每一份)现算 sha256, 与本树逐文件比; 任一份不同即 die, 不出归档",
            "all_match": True,
            "all_match_is_a_postcondition_of": "check_arm_sources(): 它对每个文件都算过、"
                      "且不一致就 die, 所以走到写归档这一行时「全同」是已经成立的事实; "
                      "files_checked=0 会被它自己挡下",
            "files_checked": n_files,
            "td_run_id": man["run_id"],
            "td_run_id_source": "results/pnr/arms_manifest.json; 每份 TD log 里都必须"
                                "原样印着这一条 (ARM-RUN-ID-...), 否则 die",
            "per_arm": verif},
        "device": DEVICE, "device_db": DEVICE_DB, "package": DEVICE,
        "speed": "NA", "td_version": TD_VERSION,
        "clock_constraint_ns": CLOCK_NS,
        "flow_driver": "flow/run_td_refresh.sh + tools/pnr_archive.py",
        "device_capacity_from_report_area": cap,
        "rtl_sha256": hs,
        "comparison_same_platform": {
            "part": "%s (Anlogic Eagle/EG4, TD 的 report_area 自己印的分母)" % DEVICE,
            "common_to_both_arms": {
                "td_version": TD_VERSION, "device_db": DEVICE_DB,
                "package": DEVICE, "speed": "NA",
                "clock_constraint_ns": CLOCK_NS,
                "only_difference": "EPV_ENABLE=1 vs 0 (tools/make_wrappers.py 钉在 rtl/synth_wrappers.v); "
                                   "两档都带 M6 的硬件非对齐访存"},
            "post_synthesis_report_area": {
                "note": "TD report_area @ optimize_gate + report_timing_summary(综合口径), 交付顶层。",
                "epv": syn["epv"], "baseline": syn["baseline"], "delta": sd},
            "post_place_and_route_report_area": {
                "note": "引脚压缩替身档 (pnr_tops.v) 的 route 结果; 交付顶层本身放不下 "
                        "(537 I/O > %d 用户 IO, PHY-9016)。" % cap["user_io_pads"],
                "epv": par["epv"], "baseline": par["baseline"], "delta": pd},
            "epv_module_presence_proof": {
                "how_to_read": "gate.area 的层级表里 dut | epv32_epv 只该出现在 EPV 档",
                "epv32_epv_in_elaborated_modules": {
                    "epv_delivered_top": proof["epv"],
                    "baseline_delivered_top": proof["baseline"]},
                "epv32_epv_own_area_row": module_luts,
                "assertion": "PASS"},
            "headline_readings": [
                "综合口径: EPV %d vs 基线 %d #Total_luts (%+d, +%.2f%%), FF %+d, DSP %d/%d"
                % (syn["epv"]["total_luts"], syn["baseline"]["total_luts"],
                   sd["total_luts_abs"], sd["total_luts_pct"], sd["ff_abs"],
                   syn["epv"]["dsp"], cap["dsp"]),
                "布线后: %.3f vs %.3f MHz (%+.2f%%)"
                % (par["epv"]["fmax_mhz"], par["baseline"]["fmax_mhz"], pd["fmax_pct"]),
                "绑定约束仍是 DSP: TD 印 %d out of %d (%.2f%%)"
                % (syn["epv"]["dsp"], cap["dsp"], syn["epv"]["dsp_pct"])],
        },
        "bitstream_policy": "替身档两臂各跑过一次 bitgen(在 pnr_*_flow.tcl 的 step 100 内); "
                            "没有任何一次烧写。",
    }
    write_json_atomic(OUT, out)
    print("PNR_ARCHIVE_OK -> results/pnr/summary2.json")
    print("  综合: %d vs %d LUT (%+d, +%.2f%%), FF %+d, DSP %d/%d, Fmax %.3f vs %.3f"
          % (syn["epv"]["total_luts"], syn["baseline"]["total_luts"],
             sd["total_luts_abs"], sd["total_luts_pct"], sd["ff_abs"],
             syn["epv"]["dsp"], cap["dsp"], syn["epv"]["fmax_mhz"],
             syn["baseline"]["fmax_mhz"]))
    print("  布线后: %d vs %d lut 占位, Fmax %.3f vs %.3f MHz (%+.2f%%)"
          % (par["epv"]["lut_sites"], par["baseline"]["lut_sites"],
             par["epv"]["fmax_mhz"], par["baseline"]["fmax_mhz"], pd["fmax_pct"]))
    return 0


# ---------------------------------------------------------------- 注入自检
# 每条用例都必须核对**为什么红**, 不许只判「有没有抛异常」: 一个抛了别的原因的
# 注入会伪装成"门在工作"。healthy 那条也不是走过场 —— 它断言核到的文件数,
# 否则 check_arm_sources 退化成「一个文件都没看」时, 上面五条全都照样红。
def selftest():
    import shutil
    import tempfile
    fails = []
    fired = []

    def build(root):
        os.makedirs(os.path.join(root, "rtl"))
        os.makedirs(os.path.join(root, "results", "pnr"))
        for name in ("a.v", "b.v", "synth_wrappers.v"):
            with open(os.path.join(root, "rtl", name), "w") as f:
                f.write("// %s v1\n" % name)
        with open(os.path.join(root, "results", "pnr", "pnr_tops.v"), "w") as f:
            f.write("// pnr_tops v1\n")
        os.makedirs(os.path.join(root, "arm", "rtl"))
        for name in ("a.v", "b.v"):
            shutil.copyfile(os.path.join(root, "rtl", name),
                            os.path.join(root, "arm", "rtl", name))
        shutil.copyfile(os.path.join(root, "rtl", "synth_wrappers.v"),
                        os.path.join(root, "arm", "synth_wrappers.v"))
        with open(os.path.join(root, "arm", "flow.tcl"), "w") as f:
            f.write("set TOP t\nset FILES {./rtl/a.v ./rtl/b.v ./synth_wrappers.v}\n")
        return root

    def run(root, tcl="flow.tcl"):
        cur = {fn: file_sha(os.path.join(root, "rtl", fn))
               for fn in os.listdir(os.path.join(root, "rtl"))}
        return check_arm_sources("arm", tcl, cur, gen_inputs(root), root)

    def fresh(mutate=None, tcl="flow.tcl"):
        """每条注入都从一份**新**的健康树开始: 共用一棵树的话, 前一条留下的
        损坏会让后一条死在先那条的理由上, 看着像"门很灵"其实后一条从没被检验。"""
        root = tempfile.mkdtemp(prefix="pnr_inj_")
        try:
            build(root)
            if mutate:
                mutate(root)
            return run(root, tcl)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def expect_die(tag, want_sub, mutate=None, tcl="flow.tcl"):
        try:
            fresh(mutate, tcl)
        except SystemExit as e:
            msg = str(e)
            if want_sub not in msg:
                fails.append("%s: 红了, 但理由不是 %r (实际: %s)" % (tag, want_sub, msg[:200]))
            else:
                fired.append(tag)
                print("  inj %-24s -> 红且理由对" % tag)
            return
        except Exception as e:
            fails.append("%s: 抛的是 %s: %s, 不是 die" % (tag, type(e).__name__, e))
            return
        fails.append("%s: 没有红 —— 这道核对对这种改动是盲的" % tag)

    root = tempfile.mkdtemp(prefix="pnr_selftest_")
    try:
        build(root)
        got = run(root)
        if len(got) != 3:
            fails.append("healthy: 核到的文件数 %d != 3 —— 判据没覆盖全部输入" % len(got))
        else:
            print("  healthy                    -> 核了 3 份输入, 全同")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    def doctor(r):
        with open(os.path.join(r, "arm", "rtl", "a.v"), "w") as f:
            f.write("// a.v v2 DOCTORED\n")
    expect_die("copy-doctored", "./rtl/a.v", doctor)

    def rm_copy(r):
        os.remove(os.path.join(r, "arm", "rtl", "b.v"))
    expect_die("copy-missing", "没有这份副本", rm_copy)

    def unreg(r):
        with open(os.path.join(r, "arm", "mystery.v"), "w") as f:
            f.write("// x\n")
        with open(os.path.join(r, "arm", "flow2.tcl"), "w") as f:
            f.write("set FILES {./mystery.v}\n")
    expect_die("input-unregistered", "没有登记出处", unreg, tcl="flow2.tcl")

    def nofiles(r):
        with open(os.path.join(r, "arm", "flow3.tcl"), "w") as f:
            f.write("set TOP t\n")
    expect_die("tcl-without-FILES", "set FILES", nofiles, tcl="flow3.tcl")

    def rmgen(r):
        os.remove(os.path.join(r, "results", "pnr", "pnr_tops.v"))
    expect_die("gen-input-missing", "生成件", rmgen)

    # run_id 这一段是纯文本判据, 直接喂三种 log。
    RID = "abcd1234abcd1234"
    if check_run_id("puts ARM-RUN-ID-%s\n" % RID, RID, "healthy") != RID:
        fails.append("run-id-healthy: 没返回那个 id")
    else:
        print("  run-id healthy             -> 认了")
    expect_die("run-id-absent", "没有 ARM-RUN-ID 行",
               lambda r: check_run_id("MARK-999-DONE\n", RID, "absent"))
    expect_die("run-id-stale", "与 arms_manifest 的",
               lambda r: check_run_id("ARM-RUN-ID-0000000000000000\n", RID, "stale"))
    expect_die("run-id-two", "同时印了两个",
               lambda r: check_run_id("ARM-RUN-ID-%s\nARM-RUN-ID-1111111111111111\n"
                                      % RID, RID, "two"))

    if fails:
        for f in fails:
            print("SELFTEST-FAIL " + f)
        print("PNR_SELFTEST_FAIL %d" % len(fails))
        return 1
    print("PNR_SELFTEST_OK %d 条注入各自点名了理由, healthy 核到 3 份输入 + 1 条 run_id"
          % len(fired))
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv[1:] else main())