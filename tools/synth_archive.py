#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 flow/synth_*.ys 的 Yosys 统计汇成 results/synth/summary.json。

三条硬规矩, 都是本机踩过之后写进来的:
  1. 只登记工具**自己印出来**的数。解析不到、或计数为 0 就报错退出, 不写
     「看起来成功」的归档 —— Yosys 曾经把整个核当死代码删掉, 印出 2 wires /
     0 cells, 而脚本退出码仍是 0 (见 rtl/synth_wrappers.v 的头注释)。
  2. 工具版本从综合日志正文里取, 不用 subprocess 现调: 这条路上
     `yosys --version` 在 PATH 缺 MSYS2 运行时时会「退 0 且零输出」,
     空字段比缺字段更危险。
  3. 口径自己写在字段里, 不等别人问: 这里是 Yosys 通用单元计数, 未映射到
     任何器件工艺库, 因此**不是 LUT 数**, 也不许被引成 LUT 数。
"""
import io
import json
import os
import re
import hashlib
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "flow"))
from atomic_json import write_json_atomic      # 归档落盘的唯一实现

LOGS = (("core_with_epv", "results/synth/core_epv.txt",   "epv32_core_epv"),
        ("core_no_epv",   "results/synth/core_noepv.txt", "epv32_core_noepv"),
        ("epv_unit_only", "results/synth/epv_only.txt",   "epv32_epv"))

DFF_RE = re.compile(r"^(?:\$_?[AS]DFF[\w]*|\$(?:adff|dff|dffsr|sdff|aldff)\w*)$")


def tool_version(path):
    """从日志里抓 'Yosys 0.69 (git sha1 ...)' 那一行。抓不到就明写抓不到。"""
    t = io.open(path, encoding="utf-8", errors="replace").read()
    m = re.search(r"Yosys [0-9][^\r\n]*", t)
    return m.group(0).strip() if m else "UNREADABLE_FROM_LOG"


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return h.hexdigest()


def parse_stat(path, mod):
    t = io.open(path, encoding="utf-8", errors="replace").read()
    parts = t.split("=== %s ===" % mod, 1)
    if len(parts) < 2:
        raise SystemExit("NO_STAT_SECTION module=%s file=%s" % (mod, path))
    d = {}
    # stat 的名字可以带空格 ("wire bits", "public wire bits"),
    # 所以名字段必须吃到行尾, 不能按单个 token 取。
    for m in re.finditer(r"^\s+(\d+)\s+(\S.*\S)\s*$", parts[1], re.M):
        d[m.group(2)] = int(m.group(1))
    for k in ("cells", "wires", "wire bits"):
        if k not in d:
            raise SystemExit("STAT_KEY_ABSENT key=%s file=%s keys=%s"
                             % (k, path, sorted(d)[:8]))
        if d[k] == 0:
            raise SystemExit("STAT_EMPTY key=%s file=%s -- 统计为空不等于成功, "
                             "极可能是整块逻辑被当死代码删了" % (k, path))
    return d


def main():
    for _, f, _ in LOGS:
        if not os.path.isfile(f):
            raise SystemExit("MISSING_LOG %s -- 先跑 flow/synth_*.ys" % f)

    arms = {}
    for tag, f, mod in LOGS:
        d = parse_stat(f, mod)
        dff = sum(v for k, v in d.items() if DFF_RE.match(k))
        hist = {k: v for k, v in
                sorted(((a, b) for a, b in d.items() if a.startswith("$")),
                       key=lambda x: -x[1])[:15]}
        arms[tag] = {
            "cells": d["cells"],
            "wires": d["wires"],
            "wire_bits": d["wire bits"],
            "dff_cells": dff,
            "cell_histogram_top15": hist,
            "source_log": f.replace("\\", "/"),
        }
    if arms["core_with_epv"]["dff_cells"] == 0:
        raise SystemExit("DFF_COUNT_ZERO -- 计数模式没抓住触发器, 宁可退出也不写 0")

    c = arms["core_with_epv"]["cells"]
    n = arms["core_no_epv"]["cells"]
    u = arms["epv_unit_only"]["cells"]

    out = {
        "tool": tool_version("results/synth/core_epv.txt"),
        "scripts": ["flow/synth_core_epv.ys", "flow/synth_core_noepv.ys",
                    "flow/synth_epv.ys",
                    "tools/make_wrappers.py -> rtl/synth_wrappers.v",
                    "tools/synth_archive.py (本文件)"],
        "arms": arms,
        "epv_unit_cells": u,
        "epv_net_overhead_cells": c - n,
        "epv_net_overhead_pct": round(100.0 * (c - n) / n, 1),
        "cross_check": {
            "unit_cells_lte_net_overhead": u <= (c - n),
            "residual_cells": (c - n) - u,
            "meaning": ("核的净增量应 >= 单独综合 EPV 单元的单元数, 差值就是核内"
                        "为它加的分派/冻结逻辑; 差值为负说明两次统计不是同一份 RTL"),
        },
        "scope_caveat": (
            "Yosys 通用单元计数, 未映射到任何器件工艺库: 这不是 LUT 数, 也不能"
            "被引成 LUT 数。真器件资源与时序见 results/pnr/ (安路 TD)。"),
        "not_included": (
            "主存 epv32_ram.v 是 65536 字异步读阵列, 任何 FPGA 都无法用 BRAM "
            "实现, 故不计入核面积; 256 KiB 按容量单独折算。"),
        "rtl_sha256": {f: sha(os.path.join("rtl", f))
                       for f in sorted(os.listdir("rtl"))
                       if f.endswith((".v", ".vh"))},
    }
    os.makedirs("results/synth", exist_ok=True)
    write_json_atomic("results/synth/summary.json", out)
    print("WROTE results/synth/summary.json")
    print("  tool       : %s" % out["tool"])
    print("  core+EPV   : %d cells (%d DFF)" % (c, arms["core_with_epv"]["dff_cells"]))
    print("  core no-EPV: %d cells" % n)
    print("  EPV unit   : %d cells" % u)
    print("  overhead   : +%d cells (+%.1f%%), residual %d"
          % (c - n, out["epv_net_overhead_pct"], out["cross_check"]["residual_cells"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
