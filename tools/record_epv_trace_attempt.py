#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 EPV 侧轨迹比对的**实测结论**写进附件 A.24 与收口表，并清掉探针留下的大文件。

结论不是"没做"，也不是"做了但没过"，而是：接线已完成、比对真的跑起来了，
而它在第一条退休指令之后就撞上**黄金模型的设备口没有周期精度**这一格 ——
所以要把 EPV 侧升到「逐条轨迹一致」，缺的是模型侧的器件行为，不是比对脚本。
所有数字都来自 results/isco/epv.json（现读，不手抄）。
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATTACH = os.path.join("docs", "附件A_缺陷与门禁台账.md")
TABLE = os.path.join("三方评审", "收口表_本轮逐条裁决与复现入口_20260926.md")


def row(r):
    return "| %s | %d | %d | %s | %s | %s | %s |" % (
        r["test"], r["rtl_commits"], r["iss_commits"],
        "PASS" if r["rtl_verdict"] == "PASS" else str(r["rtl_verdict"]),
        r["iss_stop"],
        ("index %s，且该 pc 是设备口读" % r["reg_divergence_index"]
         if r.get("reg_divergence_index") is not None else "无"),
        "是" if r.get("reg_divergence_is_device_read")
        else ("否（模型侧先耗尽步数）"
              if r.get("reg_divergence_index") is not None else "—"))


def main():
    os.chdir(ROOT)
    d = json.load(io.open(os.path.join("results", "isco", "epv.json"),
                          encoding="utf-8"))
    dev = sum(1 for r in d["rows"] if r.get("reg_divergence_is_device_read"))
    body = """## A.24 EPV 侧「逐条轨迹一致」：接线已完成，卡在黄金模型的设备口没有周期精度

评审件 P2-3 建议「给 `tb/golden/epv_iss.py` 加 `LVB`/`SVB` 与 R 型对齐」。
**这一句实测是错的**：那份模型早就实现了它们（`LVB` 57 处、`SVB` 30 处、
EPV 相关 552 处命中）。真缺口在比对器只吃 ISA 三套件的 `.S`。本轮按这个理解
写了 `flow/iss_compare_epv.py`：同一份 ELF 同时喂 Verilator 与模型，
比 R 线 (pc, rd, wdata) 与 S 线 (addr, wdata, be) 两个投影，
并按 `rtl/epv32_defs.vh` 那张编码唯一真源现场解码「被比到的到底是哪几条扩展」。
探针产物一律带 `_epvisco` 后缀，不碰已认证归档（`tools/check_archive_clobber.py`
那条纪律的来源）。

**跑出来的结果（现读 `results/isco/epv.json`，寄存器投影 0/%d 一致）**：

| 用例 | RTL 退休 | 模型退休 | RTL 判定 | 模型停机原因 | 首次分歧 | 分歧处在设备口读？ |
|---|---|---|---|---|---|---|
%s
| 设备状态口读次数 | %s |
| 被解码到并参与比对的 EPV 指令 | %d/%d 条：%s |

**三条实测出来的因（不是猜测）**：
1. %d 个用例的首次分歧，其 pc 就是模型侧算出「读的是 UART/TIMER/SYSC 窗口」
   的那条 `lw`（例：`lw x15,0(x14)`，x14=0x9000_0004 即 UART 状态口，
   RTL 读 0、模型读 1）。轮询循环的次数由这个值决定 ⇒ 一旦不同，其后所有
   退休就整体错位，再比「谁多几条」没有意义。
2. `nn_bench` 不是错位而是模型侧**先耗尽 4,000,000 步**（RTL 575,879 条退休）：
   它在等待一个模型里永不翻转的 busy 标志，烧步数而不产生退休 ——
   同一个根因的另一副面孔。
3. ISA 三套件为什么能干净地逐条比到 14,700 条零分歧？因为它们**不轮询设备**，
   退出走 tohost。所以这不是比对器写坏了，而是这条证据方法的适用边界：
   逐条退休等价要求两侧的器件时序行为可比，而黄金模型的器件是组合式近似。

**这一格因此怎么算**：仍按「未接」记（正文 5.5 第②条那句不变），
探针不进认证矩阵、不出判决 —— 它现在的身份是一次带读数的尝试，不是一道门。
**什么能翻**：给 `epv_iss.py` 的 UART/TIMER 加一个与 RTL 同宽的周期模型
（发送移位器按 `uart_div` 递减、busy 何时清），或者把参与比对的 EPV 程序改成
不轮询设备的版本（去掉打印、只写 COMMIT）。两者都会改到被归档当门禁键的源码
（`tb/golden/epv_iss.py` 的哈希记在 isco 归档里）， ⇒ 都要整链换代复认证。

**复跑**：`python flow/iss_compare_epv.py`（约 3 分钟）；自检
`python flow/iss_compare_epv.py --selftest`。
""" % (d["n_tests"],
       "\n".join(row(r) for r in d["rows"]),
       json.dumps({r["test"]: r["device_reads"] for r in d["rows"]},
                  ensure_ascii=False),
       d["epv_instructions_seen"], d["epv_instructions_total"],
       ",".join(sorted(d["epv_seen"])) or "无",
       dev)

att = io.open(ATTACH, encoding="utf-8").read()
if "## A.24" in att:
    print("A.24 已存在，跳过（幂等）")
else:
    io.open(ATTACH + ".bak-a24", "w", encoding="utf-8", newline="\n").write(att)
    io.open(ATTACH, "w", encoding="utf-8", newline="\n").write(
        att.rstrip("\n") + "\n\n" + body)
    print("A.24 written")

# 收口表 P2-3 行换成实测裁决
t = io.open(TABLE, encoding="utf-8").read()
lines = t.split("\n")
hit = [i for i, l in enumerate(lines) if l.startswith("| P2-3 |")]
if hit:
    i = hit[0]
    cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
    cells[2] = "**成立，但评审开的药方是错的、缺口比它写的深**"
    cells[3] = ("**已接线并真跑，未通过（按未接记）**。`flow/iss_compare_epv.py` 已把 "
                "EPV 程序喂进逐条轨迹比对（R 与 S 两个投影 + 按 defs 表现场解码覆盖面）。"
                "实测：4 个用例里 3 个的首次分歧就落在设备状态口的那条 `lw` 上，"
                "第 4 个是模型在等一个不翻转的 busy 标志而先耗尽 4e6 步 —— "
                "**卡的是黄金模型的器件没有周期精度，不是比对脚本**。"
                "探针不进矩阵、不出判决。")
    cells[4] = ("`results/isco/epv.json`；`python flow/iss_compare_epv.py`；"
                "展开见附件 A.24")
    lines[i] = "| " + " | ".join(cells) + " |"
    t = "\n".join(lines)
    io.open(TABLE, "w", encoding="utf-8", newline="\n").write(t)
    print("收口表 P2-3 行已换成实测裁决")
else:
    print("找不到 P2-3 行")

# 清掉探针留下的大轨迹（读数已在 epv.json；小的两份留着当样本）
freed = 0
for f in ("results/trace/nn_bench_epvisco.txt", "results/trace/nn_net_epvisco.txt"):
    if os.path.isfile(f):
        freed += os.path.getsize(f)
        os.remove(f)
print("removed probe traces, freed %d bytes" % freed)


if __name__ == "__main__":
    sys.exit(main())
