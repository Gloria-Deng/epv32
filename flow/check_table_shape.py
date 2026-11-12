#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flow/check_table_shape.py -- Markdown 表格**形状**核对（一格一列，不许拼行/裂行）

为什么要有这一支：内容核对器（"复现入口指指得到东西"）对形状是瞎的。本轮两次实测：
一次 Edit 的 old_string 取了某行「行首 + 第一格」，替换文本却是另一条完整新行，
于是两行被拼成一行（8~9 格）而内容核对器照判 OK —— 读者看到的是整列错位。

判据（三条各自有夹具）：
  1. 每张表按**未转义**的 `|` 切格，表体每行格数必须等于该表表头列数；
  2. 围栏代码块里的 `|` 行不算表格（终端回放与目录树都爱用竖线）；
  3. 分母守卫：一份文件里一张表都没扫到，而它明明是多表交付件（报告/附件 A/README），
     判红而不是判绿 —— "0 个缺陷"必须能区分"没有缺陷"与"根本没看"。

用法：
  python flow/check_table_shape.py                 # 查四份交付件 + 收口表
  python flow/check_table_shape.py --selftest      # 五条夹具（含"转义竖线不许假红"）
"""
import argparse
import io
import os
import re
import shutil
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPLIT = re.compile(r"(?<!\\)\|")
FENCE = re.compile(r"^\s*(```|~~~)")
# 这些文件"应当有表"：扫不到任何表体行 = 解析面塌了，判红
MUST_HAVE_TABLES = (
    os.path.join("docs", u"参赛项目设计报告.md"),
    os.path.join("docs", u"附件A_缺陷与门禁台账.md"),
    "README.md",
    os.path.join("三方评审", u"收口表_本轮逐条裁决与复现入口_20260926.md"),
    # v1 那一份是当轮交付的收口表、被同目录其他评审件当复现入口引；
    # v2 是**现役**的逐条裁决与复现入口表。两条都得核形状 —— 原先只列 v1，
    # 于是「正在被行列核对的是旧副本、现役这份没人核」，本轮补上（补前现量：
    # v2 单独 audit 为 tables=3 body=30 problems=0，不是一次「加了就知道红不红」的赌）。
    os.path.join("三方评审", u"收口表_逐条裁决与复现入口_20260926_v2.md"),
    # R3 这一份是**本轮现役**的逐条裁决与复现入口表（评审 R3 的 34 条编号都在里面）。
    # 上面那条注释早就写过同一次教训：「正在被行列核对的是旧副本、现役这份没人核」。
    # 补之前现量：单独 audit = tables=9 body_rows=71 problems=0，所以这一步不是
    # 「加了就知道红不红」的赌，而是把一个没人管的活动文档纳进尺子。
    os.path.join("三方评审", u"收口表_R3逐条裁决与复现入口_20260926.md"),
    # R4 这一份是**本轮现役**的逐条裁决表。补之前现量：单独 audit = tables=6
    # body_rows=50 problems=0（它有 4 行格数不齐，是补进语料之前先被同一把尺子
    # 抓出来并修好的——那 4 行就是「不补就没人核」的实测代价）。
    os.path.join("三方评审", u"收口表_R4逐条裁决与复现入口_20260927.md"),
    # 两份带表的自解释件。这里刻意**不**列 tb/refs/build/demo 那四份 README：
    # 现量它们各自 0 根行首竖线（真没有表），列进来只会让「一张表都没扫到＝解析面
    # 塌了」那条守卫变成一条假红，而那正是本文件另一处记过的错法。
    os.path.join("docs", "refs", "SOURCES.md"),
    "flow/README.md",
)


def audit(path):
    """返回 (表数, 表体行数, [问题串])。"""
    bad = []
    tables = 0
    body_rows = 0
    header = None
    in_fence = False
    lineno_of_header = 0
    for n, raw in enumerate(io.open(path, encoding="utf-8").read().splitlines(), 1):
        if FENCE.match(raw):
            in_fence = not in_fence
            header = None
            continue
        if in_fence:
            continue
        s = raw.strip()
        if not s.startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in SPLIT.split(s.strip("|"))]
        if not "".join(cells).strip():
            header = None
            continue
        if set("".join(cells).replace(" ", "")) <= set("-:"):
            continue                      # 分隔行：表头已经在上一行记过了
        if header is None:
            header = len(cells)
            tables += 1
            lineno_of_header = n
            continue
        body_rows += 1
        if len(cells) != header:
            bad.append("%s:%d 这行 %d 格，而本表表头（第 %d 行）是 %d 格；首格=%r"
                       % (os.path.basename(path), n, len(cells),
                          lineno_of_header, header, cells[0][:24]))
    return tables, body_rows, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("files", nargs="*", help="默认查四份交付件与收口表")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    targets = a.files or [os.path.join(ROOT, p) for p in MUST_HAVE_TABLES]
    problems = []
    ntab = nrow = 0
    for p in targets:
        if not os.path.isfile(p):
            problems.append("要查的文件不在盘上: %s" % p)
            continue
        t, r, bad = audit(p)
        ntab += t
        nrow += r
        problems += bad
        if t == 0 and os.path.relpath(p, ROOT).replace("\\", "/") in \
                [m.replace("\\", "/") for m in MUST_HAVE_TABLES]:
            problems.append("%s 一张表都没扫到（解析面塌了，不许读成「没有缺陷」）"
                            % os.path.basename(p))
    for msg in problems[:12]:
        print("TABLE_SHAPE_PROBLEM " + msg)
    if len(problems) > 12:
        print("   …另外 %d 条" % (len(problems) - 12))
    print("TABLE_SHAPE tables=%d body_rows=%d files=%d problems=%d"
          % (ntab, nrow, len(targets), len(problems)))
    if nrow == 0:
        print("TABLE_SHAPE_FAILED 分母为 0：一行表体都没读到，判红而不是判绿")
        return 1
    return 1 if problems else 0


def selftest():
    """五条夹具：好表 / 多一格 / 少一格 / 转义竖线不许假红 / 围栏里的竖线不算表。"""
    box = tempfile.mkdtemp(prefix="tableshape_")
    fails = []
    try:
        good = (u"# t\n\n| A | B | C |\n| --- | --- | --- |\n"
                u"| 1 | 2 | 3 |\n| 4 | 5 | 6 |\n")
        wide = good.replace(u"| 4 | 5 | 6 |", u"| 4 | 5 | 6 | 7 |")
        narrow = good.replace(u"| 4 | 5 | 6 |", u"| 4 | 5 |")
        escaped = (u"# t\n\n| A | B |\n| --- | --- |\n"
                   u"| shell 的 `a \\|\\| b` | 值 |\n| 另一行 | 值 |\n")
        fenced = (u"# t\n\n```\n| 这不是 | 表格 |\n| --- | --- |\n| a | b |\n```\n"
                  + good)

        def run(name, text, want_problems, want_rows):
            p = os.path.join(box, name + ".md")
            io.open(p, "w", encoding="utf-8").write(text)
            _t, r, bad = audit(p)
            if len(bad) != want_problems or r != want_rows:
                fails.append("%s: 读到 rows=%d problems=%d，应为 rows=%d problems=%d "
                             "(%s)" % (name, r, len(bad), want_rows, want_problems,
                                       bad[:1]))
            else:
                print("[selftest] %-10s rows=%d problems=%d" % (name, r, len(bad)))

        run("good", good, 0, 2)
        run("wide", wide, 1, 2)
        run("narrow", narrow, 1, 2)
        run("escaped", escaped, 0, 2)         # 反假红：转义的竖线不算分列
        run("fenced", fenced, 0, 2)           # 围栏里那三行不算表体
        # 分母守卫：整个语料一张表都没有时，main() 必须非零退出而不是报 0 problems
        empty = os.path.join(box, "empty.md")
        io.open(empty, "w", encoding="utf-8").write(u"# 没有表格\n\n正文一行。\n")
        _t, r, _bad = audit(empty)
        if r != 0:
            fails.append("guard: 无表文件的 rows=%d，应为 0" % r)
        else:
            print("[selftest] guard      rows=0（分母守卫看的是这个数）")
    finally:
        shutil.rmtree(box, ignore_errors=True)
    for f in fails:
        print("TABLE_SHAPE_SELFTEST_FAIL " + f)
    if fails:
        return 1
    print("TABLE_SHAPE_SELFTEST_OK 5/5 + 分母守卫（含「转义竖线不许假红」这一条）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
