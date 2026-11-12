#!/usr/bin/env python3
"""为**交付顶层 epv32_soc** 建立安路 TD 臂（综合档 + 布局布线档）。

此前 results/pnr/ 里布线后读数都来自裸核面积档封装与引脚压缩替身, 交付正文 5.5
因此把「交付顶层布线后资源 / SoC 器件数据」记成未拿到。epv32_soc 是真正要交付的
顶层 (含 UART / 计时器 / SYSC / RAM), 端口位数远小于 188, place/route 不需引脚压缩替身。

bitgen 不在本档: 现有 log 从未给出 MARK-100-bitgen-OK (两臂都 BIT-8123 unconstrained
io → FAILED), 本安装无可靠 ADC 约束写法, 故不宣称出比特流, 只取综合/布线后的资源、
频率读数。
"""
import io
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "flow"))

# 步骤表与 SDC 全部复用 flow/make_td_arms.py —— 开第二份流程定义就是
# 让"替身档跑的那套"和"SoC 档跑的这套"各自漂移。
import make_td_arms as A            # noqa: E402
import pnr_archive as P             # noqa: E402

SOC_TOP = "epv32_soc"
# 核的六个文件 + SoC 本体 + RAM + defs；defs.vh 里的 `include 需要它在同目录。
SOC_FILES = A.CORE_FILES + ["epv32_ram.v", "epv32_soc.v"]
DEFS = "epv32_defs.vh"

SYNTH_DIR = os.path.join("results", "pnr", "run_soc_synth")
PNR_DIR = os.path.join("results", "pnr", "run_soc_pnr")
SYNTH_TCL = "soc_synth_flow.tcl"
PNR_TCL = "soc_pnr_flow.tcl"

# SoC 档不写 bitgen：步骤表按 PNR_STEPS 逐条复用，只把 step 100 摘掉。
def pnr_steps_no_bitgen():
    lines = [l for l in A.PNR_STEPS.splitlines() if "100-bitgen" not in l]
    return "\n".join(lines) + "\n"


def main():
    os.chdir(ROOT)
    # 生成件必须由当前 RTL 重新产一次（与 make_td_arms.main 同一纪律），
    # 否则本档读到的是上一版封装，而 run_id 会指着旧源码。
    rc = A.regen_inputs()
    rtl = P.rtl_hashes()
    gen = {k: v[0] for k, v in P.gen_inputs(ROOT).items()}
    rid = A.run_id_of(rtl, gen)

    n = 0
    for d, tcl, steps, top in (
            (SYNTH_DIR, SYNTH_TCL, A.SYNTH_STEPS, SOC_TOP),
            (PNR_DIR, PNR_TCL, pnr_steps_no_bitgen(), SOC_TOP)):
        os.makedirs(os.path.join(ROOT, d), exist_ok=True)
        os.makedirs(os.path.join(ROOT, d, "rtl"), exist_ok=True)
        for f in SOC_FILES + [DEFS]:
            src = os.path.join("rtl", f)
            if not os.path.isfile(os.path.join(ROOT, src)):
                raise SystemExit("SOC_ARM_FAIL: 找不到 %s" % src)
            # 逐字节 copyfile：归档器拿副本 sha256 与源文件比，
            # 文本模式重写行尾会让指纹永远核不上。
            shutil.copyfile(os.path.join(ROOT, src),
                            os.path.join(ROOT, d, "rtl", f))
            n += 1
        with io.open(os.path.join(ROOT, d, "top.sdc"), "w",
                     encoding="utf-8", newline="\n") as fh:
            fh.write(A.SDC)
        files = ["./rtl/%s" % f for f in SOC_FILES] + ["./rtl/%s" % DEFS]
        A.write_tcl(os.path.join(ROOT, d, tcl), top, files, steps, rid, bit="")
    print("SOC_ARM_OK files=%d run_id=%s synth=%s pnr=%s"
          % (n, rid, SYNTH_DIR, PNR_DIR))
    return 0


if __name__ == "__main__":
    sys.exit(main())
