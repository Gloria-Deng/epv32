#!/usr/bin/env python3
"""把台账第 22 条从"修了一半"改成"三个用例全清"，并按实测结果写清哪几个数变了。

配对对照的实测结论（改前归档镜像在 results/backup/preSVB_r1/）：
  被正文引用的每一格都逐位不变 —— nn_bench 361,967 / 23,005 / 15.734、
  nn_net 2,172,053 / 109,569 / 19.823、cifar10_net 的 test_set_top1=0.4548、
  lane_efficiency=43.83、三条互核 bit_exact_three_ways=true 全部原样；
  mismatch / layer_mismatch / logit_mismatch / commit_val 仍为 0。
  **变了的只有两份归档里的 testbench 级聚合** total_cycles 与 total_retired
  （+48/+32 与 +141/+95）：留白把 .bss 里静态量的布局挪了，而这两个键是整趟
  所有程序之和，不是被引用的那一格。
所以这次收尾不许写成"除预期项外逐位相同"，要指名是哪两个键、变多少。
"""
import io
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join("docs", "参赛项目设计报告.md")
ATTACH = os.path.join("docs", "附件A_缺陷与门禁台账.md")
LEDGER = ATTACH

REP_EDITS = [
    ("⑤ `SVB` 尾组越界 **`nn_cifar` 已修并复认证**，`nn_net`/`nn_bench` 仍在；",
     "⑤ `SVB` 尾组越界 **三个用例已全部留白并复认证**；"),
    ("(4) 16 字节留白补进 `nn_net.c`/`nn_bench.c` 并整链复认证。",
     "(4) `nn_net.c`/`nn_bench.c` 的 16 字节留白本轮已补并复认证（见 5.5 第⑤条）。"),
]

# 台账第 22 条：先探它的形状，命中不到就报出来而不是静默跳过
ATT_PATTERNS = [
    (r"\*\*同一写法仍在\*\* `nn_net\.c`/`nn_bench\.c`：改它们要让归档换代并整链复认证，未做",
     "同一写法已在 `nn_net.c`/`nn_bench.c` 一并留白（本轮）：改后重跑 nn_bench/nn_net/"
     "cifar10_net 三格全 PASS，被引用的读数逐位不变；唯一变化的是两份归档里 "
     "testbench 级聚合 `total_cycles`/`total_retired`（+48/+32 与 +141/+95），"
     "因为留白挪动了 .bss 里静态量的布局，而这两个键是整趟所有程序之和。"
     "改前归档镜像在 `results/backup/preSVB_r1/`。"),
]


def main():
    os.chdir(ROOT)
    rep = io.open(REPORT, encoding="utf-8").read()
    for o, n in REP_EDITS:
        if rep.count(o) != 1:
            raise SystemExit("SVB-DOC-FAIL: 正文命中 %d 次: %s" % (rep.count(o), o[:40]))
        rep = rep.replace(o, n)
    io.open(REPORT, "w", encoding="utf-8", newline="\n").write(rep)
    print("REPORT edits=%d" % len(REP_EDITS))

    led = io.open(LEDGER, encoding="utf-8").read()
    hits = 0
    for pat, repl in ATT_PATTERNS:
        n = len(re.findall(pat, led))
        if n == 1:
            led = re.sub(pat, repl.replace("\\", "\\\\"), led)
            hits += 1
        else:
            print("LEDGER pattern hits=%d (需人工看一眼): %s" % (n, pat[:50]))
    # A.22 的 ⑤ 行
    old = "**同一写法仍在** `nn_net.c`/`nn_bench.c`：改它们要让归档换代并整链复认证，未做"
    if led.count(old) == 1:
        led = led.replace(old,
            "三个用例已全部留白并复认证；被引用读数逐位不变，仅 "
            "`total_cycles`/`total_retired` 随 .bss 布局变化（见 A.1 第 22 条）")
        hits += 1
    io.open(LEDGER, "w", encoding="utf-8", newline="\n").write(led)
    print("LEDGER rewrites=%d" % hits)

    # 复跑判据：台账/正文的说法必须与归档一致
    for cmd in (["python", "tools/check_word_limits.py"],
                ["python", "tools/word_style_count.py"],
                ["python", "flow/check_deliverable_consistency.py"]):
        rc = os.spawnv(os.P_WAIT, sys.executable, cmd)
        print("SVB-RERUN %s rc=%d" % (" ".join(cmd), rc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
