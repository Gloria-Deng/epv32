#!/usr/bin/env python3
"""把正文 5.5「交付顶层 P&R 未拿到」改成一条实测判决，详细结论进附件 A.23。

本轮真跑了 `flow/make_soc_arm.py` + `flow/run_soc_td.sh`：TD 走通
`import_device`/`read_verilog`（`MARK-010/020-OK`）后，把 `epv32_ram` 的
65536x8 推成 `Infer 5 Logic DRAMs, 0 Logic BRAMs`，此后 1827 秒无新输出、
`gate.area` 未产出，人工终止并留档 `results/pnr/soc_attempt_20260925T165352Z/`。

真话从"没做"变成"做了、被具体结构原因挡住、修法已知"。正文只加短指针，并
从 5.5 别处扣等量字数（测试总结 1986/2000）。
"""
import io
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join("docs", "参赛项目设计报告.md")
ATTACH = os.path.join("docs", "附件A_缺陷与门禁台账.md")
ATT = "results/pnr/soc_attempt_20260925T165352Z"

A23 = """## A.23 交付顶层（epv32_soc）过一遍后端：实测到哪一步、被什么挡住

**为什么要跑这一档**：此前 `results/pnr/` 里所有布线后读数都来自裸核面积档封装
（`rtl/synth_wrappers.v`）与引脚压缩替身（`results/pnr/pnr_tops.v`），
所以正文 5.5 把"交付顶层的布线后资源 / SoC·RAM 器件数据"记成**未拿到**。
替身档已经是 180 PAD ≤ 188，评审建议的"把调试口做成编译期可选、release 档只留必要引脚"
在这套不变量下没有额外空间：观测位一旦被 `opt` 认为无人读，整个核会被当死代码删掉
（本机实测过一次 2 wires / 0 cells 而退出码仍为 0）。真正没测过的是**交付顶层本身**。

**这一趟做了什么**（`flow/make_soc_arm.py` 建臂，`flow/run_soc_td.sh` 执行）：
把 `epv32_soc` 连同 RAM/UART/计时器一起喂给 TD 6.2.1，器件 EG4S20BG256，
步骤表复用 `flow/make_td_arms.py` 里那一份（不另开第二套流程定义），
按 `bitgen` 已知会被 BIT-8123 拒的实测结论**摘掉 step 100**，只取综合与布线后资源。

**实测到哪一步**：

| 步 | 判决 | 出处 |
|---|---|---|
| `import_device` | `MARK-010-import_device-OK` | 留档 log |
| `read_verilog`（8 个文件 + defs） | `MARK-020-read_verilog-OK` | 留档 log |
| RTL 优化中的 RAM 推断 | `SYN-1023 : Infer 5 Logic DRAMs, 0 Logic BRAMs`，逐条 `read 65536x8, write 65536x8` | 留档 log |
| 之后 | **1827 秒无新输出**，`gate.area` 未产出，`MARK-061` 从未出现 | `VERDICT.txt` |

**为什么这条路上走不通（不是"工具不给力"）**：65536x8 = 512 Kbit 被推成分布式逻辑
RAM 而不是 BRAM9K。EG4S20 有 64 个 BRAM9K（8 KB 一个，装得下 512 Kbit），
但**分布式**实现按 16 位/LUT 量级摊，需要上万个 LUT，而器件总共 19600 LUT ——
所以即便优化跑完，`place` 也放不下。根因在 `rtl/epv32_ram.v` 的写法不匹配
TD 的 BRAM 推断模板（读端口是组合输出，缺注册读输出与字节使能的模板形状）。

**什么能翻这一格**：把 `epv32_ram.v` 改成 BRAM 可推断模板（注册读输出 +
按字节写使能），再跑 `bash flow/run_soc_td.sh`。**代价要说清**：这会改 `rtl/`，
而 `rtl/` 的哈希是 PNR、综合、isco、bench 等一批归档的门禁键 ⇒ 整个交付要换代
重认证（台账里那条「布线后频率不许归给单独改动」的纪律同样适用于这次改动）。

**归属与留档**：本趟不改动 `summary2.json` 的任何键（臂目录与作业清单都不并入
`tools/pnr_archive.py` 的 `ARMS`，那份清单同时驱动 `run_td_refresh.sh`）；
失败趟次按 UTC 另存于 `results/pnr/soc_attempt_20260925T165352Z/`
（`run_soc_synth.out` + `soc_synth_flow.tcl` + TD 自身 log + `VERDICT.txt`），
`flow/check_package_hygiene.py` 不把它当中间件（它不是可再生产物，是一次观测）。
"""

EDITS = [
    # 正文 5.5 的 ①：加指针
    ("① 真器件 LUT/Fmax/布局布线 **已落地、仍未上板**；",
     "① 真器件 LUT/Fmax/布局布线 **已落地、仍未上板**，交付顶层本身过后端"
     "这一趟**已实测被阻**（原因与修法见附件 A.23）；"),
    # 等量扣字：⑤ 与 ⑥ 收紧（不删事实，只删赘字）
    ("⑤ `SVB` 尾组越界 **`nn_cifar` 已修并复认证**，`nn_net`/`nn_bench` 同一写法仍在；"
     "⑥ PMP 与计数器写语义 **已落地并实测**；",
     "⑤ `SVB` 尾组越界 **`nn_cifar` 已修并复认证**，`nn_net`/`nn_bench` 仍在；"
     "⑥ PMP 与计数器写语义 **已实测**；"),
    ("② 黄金 ISS（自检 356 条断言）与 RTL 的逐条轨迹比对 **已接线、"
     "66 个程序零已知分歧**，EPV 侧仍未接；",
     "② 黄金 ISS（自检 356 条断言）与 RTL 逐条轨迹比对 **已接线、66 程序零分歧**，"
     "EPV 侧仍未接；"),
    ("③ 合成权重整网推理 **已落地**；④ CIFAR-10 精度与在环 **已落地**，能耗仍未做；",
     "③ 合成权重整网推理 **已落地**；④ CIFAR-10 精度与在环 **已落地**，能耗未做；"),
]


def main():
    os.chdir(ROOT)
    assert os.path.isfile(os.path.join(ATT, "VERDICT.txt")), "留档不在，先核路径"
    rep = io.open(REPORT, encoding="utf-8").read()
    for old, new in EDITS:
        n = rep.count(old)
        if n != 1:
            raise SystemExit("R8-FAIL: 命中 %d 次: %s" % (n, old[:40]))
        rep = rep.replace(old, new)
    att = io.open(ATTACH, encoding="utf-8").read()
    if "## A.23" in att:
        raise SystemExit("R8-FAIL: A.23 已存在（幂等保护）")
    att = att.rstrip("\n") + "\n\n" + A23
    sys.path.insert(0, os.path.join("flow"))
    import run_all as RA
    a = RA.pnr_anchors()
    iss = RA.jload(os.path.join("results", "iss", "selftest.json"))
    a.append(("ISS 自身断言数", "%d" % iss["assertions"], "ISS"))
    for ui in ("rv32ui", "rv32um", "rv32mi"):
        d = RA.jload(os.path.join("results", "compliance", ui + ".json"))
        a.append((ui + " 通过数", "%d / %d" % (d["n_pass"], d["n_tests"]), None))
    bad = RA.doc_check(rep, a)
    if bad:
        raise SystemExit("R8-FAIL: 锚点掉档 %s" % bad)
    io.open(REPORT, "w", encoding="utf-8", newline="\n").write(rep)
    io.open(ATTACH, "w", encoding="utf-8", newline="\n").write(att)
    print("R8_OK edits=%d A23_appended anchors=%d after_bad=0" % (len(EDITS), len(a)))
    for cmd in (["python", "tools/check_word_limits.py"],
                ["python", "tools/word_style_count.py"]):
        rc = os.spawnv(os.P_WAIT, sys.executable, cmd)
        print("R8-RERUN %s rc=%d" % (" ".join(cmd), rc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
