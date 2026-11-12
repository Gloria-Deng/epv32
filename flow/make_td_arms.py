#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flow/make_td_arms.py -- 把「当前这一版 RTL」铺成安路 TD 的四个作业臂。

为什么要有这个文件: 器件口径(LUT / DSP / Fmax)那一份归档, 之前是把 rtl/ **手工**
拷进 results/pnr/<arm>/rtl/ 再手写 tcl 跑的。于是出现过两件事:
  1. flow/run_td_refresh.sh 跑的是 run2s_*, 而 tools/pnr_archive.py 读的是
     run3s_* —— 驱动与归档各说各话, 跑完刷新的根本不是被读的那份;
  2. 顶层那个 verified_against_source 是写死的 True, 也就是说「报告对应哪版
     RTL」从来没人核过。
本脚本把臂的建立变成一条命令, 并钉一个 RUN-ID: 它由当前 RTL 与生成件的哈希算出,
写进每个 tcl 的 `puts ARM-RUN-ID-...`, 于是 TD 的 log 自己带着「我是哪版源码跑出来的」
这句话。归档器随后要求 log 里的 id == 本脚本登记的 id, 旧 RTL 留下的 log 一律不认。
不含时间戳: RTL 没变时重跑得到同一个 id, 不会因为一次合法复跑就作废。

跑法: python flow/make_td_arms.py        然后   bash flow/run_td_refresh.sh
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import pnr_archive as P     # 臂目录 / top / tcl 名的唯一真源
from atomic_json import write_json_atomic   # 归档落盘的唯一实现

MANIFEST = os.path.join(ROOT, "results", "pnr", "arms_manifest.json")
SDC = "create_clock -name clk -period 10.000 [get_ports {clk}]\n"

CORE_FILES = ["epv32_alu.v", "epv32_rf.v", "epv32_muldiv.v", "epv32_csr.v",
              "epv32_epv.v", "epv32_core.v"]

STEP_PROC = '''proc step {mark body} {
    if {[catch {uplevel 1 $body} err]} { puts "MARK-$mark-FAILED :: $err"; return 1 }
    puts "MARK-$mark-OK"
    return 0
}
'''

# 综合档: optimize_gate 之后 report_area + report_timing_summary 就够归档器取数。
SYNTH_STEPS = """step 010-import_device {import_device eagle_s20.db -package EG4S20BG256}
step 020-read_verilog  {read_verilog -file $FILES -top $TOP}
step 030-optimize_rtl  {optimize_rtl}
step 040-report_area_rtl {report_area -file rtl.area}
step 050-read_sdc      {read_sdc $SDC}
step 060-optimize_gate {optimize_gate}
step 061-report_area_gate {report_area -file gate.area}
step 062-report_qor_gate  {report_qor -step gate -file gate.qor}
step 064-report_timing_summary_gate {report_timing_summary -file gate_timing.rpt}
"""

# P&R 档: 交付顶层 537 个 I/O 放不进 188 个用户 IO PHY-9016, 所以这一档是
# results/pnr/pnr_tops.v 那个引脚压缩替身(核原样例化, 观测口 XOR 折叠)。
PNR_STEPS = """step 010-import_device {import_device eagle_s20.db -package EG4S20BG256}
step 020-read_verilog  {read_verilog -file $FILES -top $TOP}
step 030-optimize_rtl  {optimize_rtl}
step 040-report_area_rtl {report_area -file rtl.area}
step 050-read_sdc      {read_sdc $SDC}
step 060-optimize_gate {optimize_gate}
step 061-report_area_gate {report_area -file gate.area}
step 062-report_qor_gate  {report_qor -step gate -file gate.qor}
step 063-export_db_gate   {export_db ./gate.db}
step 070-legalize_phy_inst {legalize_phy_inst}
step 080-place  {place}
step 081-report_area_place {report_area -file place.area}
step 082-report_qor_place  {report_qor -step place -file place.qor}
step 083-update_timing_manhattan {update_timing -mode manhattan}
step 084-report_timing_status_place {report_timing_status -file place.ts}
step 085-report_timing_summary_place {report_timing_summary -file place_timing.rpt}
step 090-route  {route}
step 091-report_area_route {report_area -file route.area}
step 092-report_qor_route  {report_qor -step route -file route.qor}
step 093-update_timing_final {update_timing -mode final}
step 094-report_timing_status_route {report_timing_status -file route.ts}
step 095-report_timing_summary_route {report_timing_summary -file route_timing.rpt}
step 096-report_ram_utilization {report_ram_utilization -file ram_util.rpt}
step 097-report_route_status {report_route_status -file route_status.rpt}
step 098-export_db_route {export_db ./route.db}
step 100-bitgen {bitgen -bit ./%s}
"""


def sha(path):
    return P.file_sha(path)


def regen_inputs():
    """生成件必须由当前 RTL 重新产一次, 不然臂会读到上一版的封装。"""
    for script in (os.path.join(ROOT, "tools", "make_wrappers.py"),
                   os.path.join(ROOT, "results", "pnr", "make_pnr_tops.py")):
        rc = subprocess.call([sys.executable, script], cwd=ROOT)
        if rc != 0:
            raise SystemExit("MAKE_TD_ARMS_FAIL: 生成件 %s 退出码 %d" % (script, rc))
    for f in ("rtl/synth_wrappers.v", "results/pnr/pnr_tops.v"):
        if not os.path.isfile(os.path.join(ROOT, f)):
            raise SystemExit("MAKE_TD_ARMS_FAIL: %s 没产出来" % f)


def run_id_of(rtl, gen):
    """只由源码哈希决定 —— 同样源码的两次建立应当给出同一个 id。"""
    blob = json.dumps({"rtl": rtl, "gen": gen}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def write_tcl(path, top, files, steps, runid, bit=""):
    body = ("# %s -- 由 flow/make_td_arms.py 生成, 勿手改 (改流程请改那个脚本)\n"
            "# arm top = %s\n"
            "# 每一步都包在 catch 里: 一步失败不许盖掉前面已经产出的报告。\n"
            "# 只有 MARK 行 AND 报告文件都在才算这步跑成 —— run.exe 退 0 不是证据。\n"
            % (os.path.basename(path), top))
    body += 'set TOP %s\nset FILES {%s}\nset SDC ./top.sdc\n' % (top, " ".join(files))
    body += 'puts "ARM-RUN-ID-%s"\n' % runid
    body += STEP_PROC + steps
    arts = [s for s in ("rtl.area", "gate.area", "gate.qor", "gate_timing.rpt")]
    if bit:
        arts += ["place.area", "place.qor", "route.area", "route.qor",
                 "route_timing.rpt", bit]
    body += "foreach f {%s} {\n" % " ".join(arts)
    body += '    if {[file exists $f]} { puts "ARTIFACT-$f-SIZE=[file size $f]" }'
    body += ' else { puts "ARTIFACT-$f-MISSING" }\n}\n'
    body += 'puts "MARK-999-DONE-$TOP"\n'
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)


def main():
    os.chdir(ROOT)
    regen_inputs()
    rtl = P.rtl_hashes()
    gen = {k: v[0] for k, v in P.gen_inputs(ROOT).items()}
    rid = run_id_of(rtl, gen)
    manifest = {"run_id": rid, "rtl_sha256": rtl, "gen_sha256": gen,
                "how_to_read": "tcl 里的 ARM-RUN-ID 与本 run_id 相同, 才说明这份 TD "
                               "log 是照着当前 rtl/ 跑出来的; 之后 tools/pnr_archive.py "
                               "会逐个核臂目录里的输入副本哈希。",
                "arms": {}}

    n_copy = 0
    for arm, cfg in P.ARMS.items():
        for kind in ("synth", "pnr"):
            d = cfg[kind + "_dir"]
            tcl_rel = cfg[kind + "_tcl"]
            top = cfg["top"] if kind == "synth" else cfg["pnr_top"]
            os.makedirs(os.path.join(ROOT, d), exist_ok=True)
            os.makedirs(os.path.join(ROOT, d, "rtl"), exist_ok=True)
            if kind == "synth":
                files = ["./rtl/%s" % f for f in CORE_FILES] + ["./rtl/synth_wrappers.v"]
                steps = SYNTH_STEPS
                bit = ""
            else:
                files = ["./rtl/%s" % f for f in CORE_FILES] + ["./pnr_tops.v"]
                bit = "pnr_%s.bit" % ("epv" if arm == "epv" else "noepv")
                steps = PNR_STEPS % bit
            for rel in files:
                src_name = os.path.basename(rel)
                src = (os.path.join("rtl", src_name) if rel.startswith("./rtl/")
                       else os.path.join("results", "pnr", src_name))
                dst = os.path.normpath(os.path.join(ROOT, d, rel.lstrip("./")))
                # **逐字节 copyfile**: 归档器要拿这份副本的 sha256 与源文件比,
                # 文本模式重写一遍行尾就会让指纹永远核不上(这类坑踩过)。
                shutil.copyfile(os.path.join(ROOT, src), dst)
                n_copy += 1
            with io.open(os.path.join(ROOT, d, "top.sdc"), "w",
                         encoding="utf-8", newline="\n") as f:
                f.write(SDC)
            write_tcl(os.path.join(ROOT, d, tcl_rel), top, files, steps, rid, bit)
            manifest["arms"].setdefault(arm, {})[kind] = {
                "dir": d, "tcl": tcl_rel, "top": top,
                "files": {rel: sha(os.path.join(ROOT, d, rel.lstrip("./")))
                          for rel in files}}
    write_json_atomic(MANIFEST, manifest)
    print("MAKE_TD_ARMS_OK run_id=%s  (%d 份输入副本, 4 个 tcl)" % (rid, n_copy))
    print("  manifest -> results/pnr/arms_manifest.json")
    print("  下一步: bash flow/run_td_refresh.sh  (约 30~40 分钟, 只读工具许可)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
