#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/bram_arms_build.py -- 从八臂原始 log 重建 `results/pnr/bram_template_arms.json`。

它是那份归档的**生产者**（`HISTORICAL_ARCHIVES.json` 的 produced_by 就登记它），
所以「盘上那份 == 现在重建出来的那份」必须可核：默认调用即只核（不等就非零退出并点名
要重跑哪条命令），重建是显式 `--write`。

为什么这条链要单独有一份：那八臂是一次约 40 分钟的安路 TD 批次（两臂在 900 s 上限内
没返回），太贵而不能进认证矩阵；链条里跑的是便宜的 `tools/bram_arms_check.py`，
它核「臂源码 sha 没变 + 归档里每行 verdict 还在原始 log 里 + 三条结论仍成立」。
"""
from __future__ import print_function
import hashlib
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DIR = os.path.join("results", "pnr", "bram_template_arms")
ARCH = os.path.join("results", "pnr", "bram_template_arms.json")

NOTE = (u"rc/elapsed_s/timed_out 是从每臂那份 .rc 文件解出来的字段（第一版把整份文件"
        u"文本塞进 rc 一格，那没法断言）")
WHAT = (u"把「把 rtl/epv32_ram.v 改成 BRAM 可推断模板就能让交付顶层过后端」这条修法拆成"
        u"两条互不相干的约束各测一臂：模板形状（异步读 vs 注册读）与容量"
        u"（256 Kbit 装得下 vs 2048 Kbit 装不下）。")
WHY_STANDALONE = (u"八臂都是独立的最小 RAM 模块，不吃 rtl/ 那棵树，所以它们不改变交付归档的 "
                  u"rtl_sha256 代，也不进认证矩阵重跑；链条里那发便宜的核对只核"
                  u"「盘上这份 .v == 归档记的 sha」与「归档记的 verdict 行还在原始 log 里」。")


def sha(p):
    return hashlib.sha256(io.open(p, "rb").read()).hexdigest()


def read(p):
    return io.open(p, encoding="utf-8", errors="replace").read()


def first(lines, pat):
    for l in lines:
        if re.search(pat, l):
            return l.strip()
    return None


def build():
    arms = sorted(d for d in os.listdir(os.path.join(ROOT, DIR))
                  if os.path.isdir(os.path.join(ROOT, DIR, d)))
    rows = []
    for a in arms:
        d = os.path.join(ROOT, DIR, a)
        v = os.path.join(d, a + ".v")
        out = os.path.join(d, a + ".out")
        area = os.path.join(d, "gate.area")
        qor = os.path.join(d, "gate.qor")
        rcp = os.path.join(d, a + ".rc")
        if not (os.path.isfile(v) and os.path.isfile(out)):
            continue
        src = read(v)
        m = re.search(r"WORDS\s*=\s*(\d+)", src)
        f = {}
        if os.path.isfile(rcp):
            for ln in read(rcp).splitlines():
                k, _s, val = ln.partition("=")
                f[k.strip()] = val.strip()
        alines = (read(area).splitlines()
                  if os.path.isfile(area) and os.path.getsize(area) else [])
        rows.append({
            "arm": a,
            "source_sha256": sha(v),
            "source_bytes": os.path.getsize(v),
            "declared_words": int(m.group(1)) if m else None,
            "capacity_kbit": (int(m.group(1)) * 32 // 1024) if m else None,
            "rc": int(f["rc"]) if f.get("rc", "").lstrip("-").isdigit() else None,
            "elapsed_s": float(f["elapsed_s"]) if f.get("elapsed_s") else None,
            "timeout_limit_s": float(f["timeout_limit_s"]) if f.get("timeout_limit_s") else None,
            "timed_out": f.get("timed_out") == "yes",
            "start_utc": f.get("start_utc"), "end_utc": f.get("end_utc"),
            "infer_summary": first(read(out).splitlines(), r"SYN-1023"),
            "bram9k_line": first(alines, r"#bram9k"),
            "bram_total_line": first(alines, r"^#bram\s"),
            "lut_line": first(alines, r"^#lut\s"),
            "total_luts_line": first(alines, r"#Total_luts"),
            "qor_ram_line": first(read(qor).splitlines(), r"#RAMs") if os.path.isfile(qor) else None,
            "area_report_present": bool(alines),
        })
    ov = next((r for r in rows if r["arm"] == "ram_bram_256k"), None)
    mm = re.search(r"#bram\s+(\d+)\s+out of\s+(\d+)", (ov or {}).get("bram_total_line") or "")
    return {
        "what": WHAT, "why_standalone_rams": WHY_STANDALONE,
        "tool": u"Anlogic TD 6.2.1 (td.exe -b), device EG4S20BG256, 只跑到 gate.area"
               u"（不 place/route/bitgen）",
        "measured_utc_first_run": "2026-09-27T02:00:18Z",
        "elapsed_note": u"两条 900 s 上限内没跑完的臂按 rc=124 记为 timed_out，不当成结论",
        "rc_field_note": NOTE,
        "arms": rows,
        "readings": ({"overflow_used": int(mm.group(1)), "overflow_capacity": int(mm.group(2)),
                      "overflow_percent_line": mm.group(0)} if mm else {}),
        "reproduce": u"python tools/bram_arms_make.py && bash flow/bram_arms_run_all.sh 900"
                     u" && python tools/bram_arms_build.py --write",
        "produced_by": "tools/bram_arms_build.py",
    }


def main(argv):
    os.chdir(ROOT)
    if not os.path.isdir(DIR):
        print("BRAM_ARMS_SETUP_FAIL missing %s" % DIR)
        return 2
    got = build()
    txt = json.dumps(got, ensure_ascii=False, indent=1) + "\n"
    if "--write" in argv:
        io.open(ARCH, "w", encoding="utf-8", newline="\n").write(txt)
        print("BRAM_ARMS_BUILT arms=%d bytes=%d" % (len(got["arms"]), len(txt.encode("utf-8"))))
        return 0
    if not os.path.isfile(ARCH):
        print("BRAM_ARMS_SETUP_FAIL missing %s" % ARCH)
        return 2
    old = io.open(ARCH, encoding="utf-8", newline="\n").read()
    same = sha_of(old) == sha_of(txt)
    if not same:
        # 只报是哪一臂哪个字段漂了，不然「过期」两个字帮不了下一步
        try:
            o = json.loads(old)
            diffs = [(a["arm"], k) for a in got["arms"]
                     for b in o.get("arms", []) if b["arm"] == a["arm"]
                     for k in a if a[k] != b.get(k)]
        except Exception:
            diffs = [("?", "?")]
        print("BRAM_ARMS_STALE fields=%d first=%s 先跑 python tools/bram_arms_build.py --write"
              % (len(diffs), str(diffs[:3]).encode("ascii", "backslashreplace").decode("ascii")))
        return 1
    print("BRAM_ARMS_ARCHIVE_OK arms=%d byte_identical=1" % len(got["arms"]))
    return 0


def sha_of(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
