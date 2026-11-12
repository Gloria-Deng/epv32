#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/refresh_gz_receipts.py -- 给 .gz 收条补上「重跑到底能不能长出同一份字节」这一格

动因：flow/compress_artifact.py 写收条时只填了 `rerun` 一句话，而那句话是**没跑过**的：
bisect 那一格写的是 `python tools/vcd_probe.py --record`，该脚本既没有 --record，
也不产出波形，只解析波形。一个没人能照做的"可再生"声明等于没声明。

本件把两条重跑式真跑一遍，把逐字节比对的结果写进收条：
  results/demo/06_wave.vcd   -> 本轮重跑 = 归档原件逐字节相同（sha 与行数一起记）
  results/wave/bisect.vcd    -> 本轮重跑 **不**相同，收条里写清差在哪、为什么
收条里除 `rerun`/`repro` 之外的字段一律原样保留（original_sha256 是归档那一份字节的
指纹，重跑不改变它的含义）。判决行 ASCII，末行 REPROreceiptS_OK/FAILED。
"""
import gzip
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(ROOT, "flow"))
import compress_artifact as ca                      # noqa: E402  (RERUN 表唯一权威)

# 本轮实测的两份重跑产物；测完即删，>10 MB 明文产物不随包（判据 flow/check_package_hygiene.py）
MEASURED = {
    "results/demo/06_wave.vcd": {
        "regen_file": "results/wave/wave_regen.vcd",
        "command": "EPV_WAVE=1 EPV_WAVE_FILE=<绝对路径>/results/wave/wave_regen.vcd "
                   "python flow/rt.py bench sw/bench/epv_bisect.c --max-cyc 4200 "
                   "--tag wave2",
        "archive_of_this_rerun": "results/sim/epv_bisect_wave2.json",
    },
    "results/wave/bisect.vcd": {
        "regen_file": "results/wave/bisect_regen.vcd",
        "command": "EPV_WAVE=1 EPV_WAVE_FILE=<绝对路径>/results/wave/bisect_regen.vcd "
                   "python flow/rt.py bench sw/bench/epv_bisect.c --max-cyc 3600 "
                   "--tag wavegen",
        "archive_of_this_rerun": "results/sim/epv_bisect_wavegen.json",
        "why_not_identical":
            "归档那一份出自更早的一代仿真器构建：VCD 表头第 101 字节起分岔"
            "（$var 的 ident 编号整体位移），且它截的窗也没记进任何归档"
            "（归档那份最后一个时间步 6961 ps，本轮 --max-cyc 3600 跑到 7199 ps）。"
            "rtl/ 那 10 个文件的 sha256 两边逐位相同 —— 变的只有 tb 编出来的仿真器。",
        "would_flip_if": "把 EPV_WAVE_FILE 与 --max-cyc 连同 sim_sha256 一起记进波形归档，"
                         "再按同一个构建复跑；本包没有那个构建，所以这一格按不可复现记。",
    },
}

NOTE = ("original_sha256 是**归档那一份字节**的指纹。repro 这一格记录的是：按 rerun "
        "那条命令在当代构建上重跑，能不能得到同一份字节。两者不是一回事。")


def update(rel):
    gz = os.path.join(ROOT, rel + ".gz")
    side_p = gz + ".json"
    if not os.path.isfile(side_p):
        print("SKIP-NO-SIDECAR %s" % rel)
        return None
    side = json.load(io.open(side_p, encoding="utf-8"))
    m = MEASURED[rel]
    reg = os.path.join(ROOT, m["regen_file"])
    if not os.path.isfile(reg):
        # 产物不在盘（13~17 MB 明文测完即删）：不许抹掉已写 repro——否则实测结论
        # 变空白、收条却像"刚测过"；只把 rerun 行对齐 RERUN 表。
        if "repro" in side:
            side["rerun"] = ca.RERUN[rel]
            with io.open(side_p, "w", encoding="utf-8", newline="\n") as f:
                json.dump(side, f, ensure_ascii=False, indent=1)
                f.write("\n")
            print("KEEP %s repro 保留（%s 不在盘上，重测先跑 repro.rerun_command）"
                  % (rel, m["regen_file"]))
            return True
        print("MISSING-REGEN %s (先跑: %s)" % (m["regen_file"], m["command"]))
        return False
    r_sha, r_lines = ca.sha_lines(reg)
    a_sha, a_lines = side["original_sha256"], side["lines"]
    repro = {
        "byte_identical": (r_sha == a_sha) and (r_lines == a_lines),
        "rerun_command": m["command"],
        "rerun_archive": m["archive_of_this_rerun"],
        "regen_sha256": r_sha,
        "regen_lines": r_lines,
        "archived_sha256": a_sha,
        "archived_lines": a_lines,
        "note": NOTE,
    }
    if not repro["byte_identical"]:
        repro["why_not_identical"] = m["why_not_identical"]
        repro["would_flip_if"] = m["would_flip_if"]
        # 「不逐位相同」要写成读数而不是形容词：现量第一处不同的字节偏移。
        head_a = gzip.open(os.path.join(ROOT, rel + ".gz"), "rb").read(8192)
        head_b = io.open(reg, "rb").read(8192)
        n = min(len(head_a), len(head_b))
        k = 0
        while k < n and head_a[k] == head_b[k]:
            k += 1
        repro["diverges_at_byte"] = k if k < n else None
        if repro["diverges_at_byte"] is not None:
            repro["diverges_archived_bytes"] = head_a[k:k + 28].decode(
                "latin-1").replace("\r\n", "/")
            repro["diverges_regen_bytes"] = head_b[k:k + 28].decode(
                "latin-1").replace("\r\n", "/")
    side["rerun"] = ca.RERUN[rel]
    side["repro"] = repro
    with io.open(side_p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(side, f, ensure_ascii=False, indent=1)
        f.write("\n")
    # 收条写完立刻回读一次：确认它现在印的是哪个判决
    back = json.load(io.open(side_p, encoding="utf-8"))
    print("RECEIPT %s byte_identical=%s lines %d/%d"
          % (rel, back["repro"]["byte_identical"], r_lines, a_lines))
    return True


def main():
    os.chdir(ROOT)
    fails = []
    for rel in MEASURED:
        try:
            r = update(rel)
        except Exception as e:                       # noqa: BLE001
            print("RECEIPT_ERROR %s %r" % (rel, e))
            r = False
        if r is None:
            fails.append("no sidecar for " + rel)
        elif r is False:
            fails.append("not measured for " + rel)
    # 正对照：两份收条都必须真的写着 repro 这一格，且 06_wave 那条判 True
    for rel in ("results/demo/06_wave.vcd", "results/wave/bisect.vcd"):
        side = json.load(io.open(os.path.join(ROOT, rel + ".gz.json"), encoding="utf-8"))
        if "repro" not in side:
            fails.append("receipt without repro field: " + rel)
        if "--record" in side.get("rerun", ""):
            fails.append("stale fake rerun still in receipt: " + rel)
    want = {"results/demo/06_wave.vcd": True, "results/wave/bisect.vcd": False}
    for rel, v in want.items():
        side = json.load(io.open(os.path.join(ROOT, rel + ".gz.json"), encoding="utf-8"))
        got = (side.get("repro") or {}).get("byte_identical")
        if got is not v:
            fails.append("%s repro verdict %r, expected %r" % (rel, got, v))
    for f in fails:
        print("RECEIPT_FAIL " + f)
    if fails:
        return 1
    print("REPRO_RECEIPTS_OK 2/2 (一份逐字节可复现, 一份按不可复现记账)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
