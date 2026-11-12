#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""results/pnr/bram_template_arms/read_arms.py -- 从 TD 日志里取出测量数。

对臂目录只读。只印 ASCII（宿主控制台是 GBK，非 ASCII 用反斜杠转义）。
逐臂按工具行原样印出：rc/elapsed、MARK 梯子、RAM 推断行（SYN-1023/1026/1027…）、
错误/资源告警、rtl.area 与 gate.area 的 #bram/#lut 等键、stdout 尾部。
"""
import glob
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

INFER_RE = re.compile(r"SYN-\d+ : .*(DRAM|BRAM|RAM)", re.I)
ERR_RE = re.compile(r"(ERROR|Error|error|FAIL|fail|insufficient|not enough|"
                    r"no enough|exceed|overflow|cannot|Unable|unable|"
                    r"utilization.*1[0-9][0-9]\.)")
AREA_KEYS = ("#bram", "#bram9k", "#bram32k", "#fifo9k", "#lut",
             "#Total_luts", "#reg")


def asc(s):
    return s.encode("ascii", "backslashreplace").decode("ascii").rstrip()


def read_lines(path):
    if not os.path.isfile(path):
        return []
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        return [l.rstrip("\r\n") for l in f]


def area_summary(path):
    out = []
    for l in read_lines(path):
        t = l.strip()
        for k in AREA_KEYS:
            if t.startswith(k):
                out.append("  %-28s | %s" % (os.path.basename(path), asc(t)))
    return out


def main():
    arms = sorted(os.path.basename(d) for d in glob.glob(os.path.join(HERE, "*"))
                  if os.path.isdir(d) and
                  os.path.isfile(os.path.join(d, os.path.basename(d) + ".tcl")))
    print("ARMS_FOUND %d" % len(arms))
    for arm in arms:
        d = os.path.join(HERE, arm)
        out = os.path.join(d, arm + ".out")
        err = os.path.join(d, arm + ".err")
        rc = os.path.join(d, arm + ".rc")
        lines = read_lines(out)
        print("=" * 78)
        print("ARM %s" % arm)
        for l in read_lines(rc):
            print("  RC %s" % asc(l))
        marks = [l for l in lines if l.startswith("MARK-")]
        print("  MARKS (%d):" % len(marks))
        for m in marks:
            print("    %s" % asc(m))
        for l in read_lines(out):
            for l2 in ([l] if INFER_RE.search(l) else []):
                print("  INFER %s" % asc(l2))
        errhits = [l for l in lines if ERR_RE.search(l)]
        if errhits:
            print("  TOOL-COMPLAINT-LINES (%d, first 25):" % len(errhits))
            for l in errhits[:25]:
                print("    %s" % asc(l))
        else:
            print("  TOOL-COMPLAINT-LINES 0")
        for rep in ("rtl.area", "gate.area"):
            for s in area_summary(os.path.join(d, rep)):
                print("  AREA%s" % s[4:])
        for rep in ("ram_util.rpt", "gate.qor"):
            p = os.path.join(d, rep)
            if os.path.isfile(p):
                print("  FILE %s size=%d" % (rep, os.path.getsize(p)))
        ilines = [l for l in read_lines(err) if l.strip()]
        if ilines:
            print("  STDERR-lines %d (last 5):" % len(ilines))
            for l in ilines[-5:]:
                print("    %s" % asc(l))
        tail = [l for l in lines if l.strip()]
        print("  STDOUT-lines %d (last 6):" % len(tail))
        for l in tail[-6:]:
            print("    %s" % asc(l))
    return 0


if __name__ == "__main__":
    sys.exit(main())
