# -*- coding: utf-8 -*-
"""把「门禁自己印的夹具条数是不是它自己清单算出来的」收成一条门。

评审 P2-13 的形状是「门禁的自我声明与实现作用域之间有落差」。落到夹具这一格就是：
末行 `X_SELFTEST_OK 7/7` 里那个 7 若是手抄字面量，那么**加一条夹具就漂一次**，而没有任何门会说。
本包已为此翻过两次车：台账第 27 条记着卫生门曾写 4/4 而代码早已 5/5；第 30 条那一轮又把它
写成手抄的 6/6（现已改成现算）。评审给的修法是「所有门禁统一加一条声明-实现一致性夹具」；
全集要先统一各脚本末行的形状，本轮没做完，所以这条门采取**已转换的强制、未转换的显式记账**
（与 `tools/third_party_licenses.py` 的 DECLARED_UNAVAILABLE 同一族形状）：

  1. 走查 flow/ 与 tools/ 下每个自带夹具判决行的脚本，按末行分母的形状分两类：
     computed（打印的是占位符，值由清单现算）/ literal（打印的是写死的 N/M）。
  2. literal 的每一支必须落在下面的 DECLARED_LITERAL 里并带理由，没登记即判红
     （于是**新写的门不能再用手工分母**）；登记了却已经改成现算的判 STALE-DECLARATION，
     不许留过时豁免。
  3. computed 的每一支做**运行时**交叉核对：读它自己那份模块级具名夹具清单的长度，跑它的
     `--selftest` 解析末行 `N/M`，断言 `M == 清单长度` 且 `N == M`。这条比形状检查强：
     它同时抓「分母与清单不符」与「有夹具声明了却根本没跑到」。
     读不到清单却自称现算的，判 NO-LIST（交叉核对做不成时不许放绿）。

用法：
  python tools/fixture_claims.py             # 判决; 明细写 results/logs/fixture_claims.txt
  python tools/fixture_claims.py --selftest  # 夹具只走内存替身, 不跑任何真实门禁
末行判决（ASCII）：
  FIXTURE_CLAIMS_OK scripts=<n> computed=<n> literal_declared=<n> undeclared=0
                    stale_declarations=0 runtime_mismatch=0 problems=0
"""
from __future__ import print_function

import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DETAIL = os.path.join("results", "logs", "fixture_claims.txt")

LIST_RE = re.compile(r"^(?P<name>[A-Z][A-Z0-9_]{2,})\s*=\s*[\[(](?P<body>.*?)[\])]",
                     re.M | re.S)
LIST_NAME_OK = re.compile(r"NAMES|CASES|FIXTURE")
STR_RE = re.compile(r'"[^"]+"')
PRINT_RE = re.compile(r'print\(\s*"([A-Z][A-Z0-9_]{4,})[^"]*?'
                     r'(\d+|%d|%s|\(?\w+\)s)\s*/\s*(\d+|%d|%s|\(?\w+\)s)')
OK_MARKER = re.compile(r"SELFTEST|SELF_TEST|FIXTURES_OK")
MIN_REASON = 12

# 未转换的豁免: 每条理由写的是「为什么还没换成现算」, 不是「不需要现算」。
DECLARED_LITERAL = {
    "flow/check_table_shape.py":
        u"它的夹具是函数体内一条条 print 的分支, 没有一份模块级具名清单可数; 换现算要先重构它自测的写法",
    "flow/compress_artifact.py":
        u"只有两条夹具且是压缩工具的内部自测, 不在矩阵判决链上; 排在本族下一批",
    "tools/official_coverage.py":
        u"清单是 selftest 里的局部变量而不是模块级具名清单; 本族下一批把它提成模块级 FIXTURE_NAMES",
}

# 本门自己的夹具清单: 末行分母由它现算（这条门先受自己管）
FIXTURE_CLAIM_CASES = ["classify_literal", "classify_computed", "list_is_countable",
                       "undeclared_literal_fires", "declared_literal_is_silent",
                       "stale_declaration_fires", "declared_reasons_are_not_empty"]


def die(msg):
    raise SystemExit("FIXTURE_CLAIMS_FAIL: " + msg)


def scan(text):
    """-> None 或 {kind, hits, lists}"""
    lists = {}
    for m in LIST_RE.finditer(text):
        if LIST_NAME_OK.search(m.group("name")):
            lists[m.group("name")] = len(STR_RE.findall(m.group("body")))
    hits = []
    for m in PRINT_RE.finditer(text):
        marker = m.group(1)
        if not OK_MARKER.search(marker):
            continue
        a, b = m.group(2), m.group(3)
        hits.append((marker, a, b, bool(a.isdigit() and b.isdigit())))
    if not hits:
        return None
    kinds = set("literal" if h[3] else "computed" for h in hits)
    return {"kind": "literal" if kinds == {"literal"} else "computed",
            "hits": hits, "lists": lists}


def candidates():
    out = []
    for d in ("flow", "tools"):
        for fn in sorted(os.listdir(os.path.join(ROOT, d))):
            if not fn.endswith(".py"):
                continue
            rel = d + "/" + fn
            text = io.open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace").read()
            info = scan(text)
            if info:
                out.append((rel, info, text))
    return out


def run_denominator(rel):
    """跑它自己的 --selftest, 解析末行夹具判决 N/M。"""
    p = subprocess.run([sys.executable, os.path.join(ROOT, rel.replace("/", os.sep)), "--selftest"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=ROOT)
    got = None
    for line in p.stdout.decode("utf-8", "replace").splitlines():
        m = re.search(r"([A-Z][A-Z0-9_]{3,}?(?:_OK| OK| FAIL))\s*\s*(\d+)\s*/\s*(\d+)", line)
        if m and OK_MARKER.search(m.group(1)):
            got = (int(m.group(2)), int(m.group(3)), line.strip())
    return got


def verdict_for(rel, kind):
    """一条夹具判决的谓词: 字面量必须有登记, 已现算的不许还挂着登记。"""
    if kind == "literal":
        return [] if rel in DECLARED_LITERAL else ["UNDECLARED-LITERAL %s 末行分母是写死的字面量, 且没登记理由" % rel]
    return ["STALE-DECLARATION %s 分母已改为现算, 豁免该撤" % rel] if rel in DECLARED_LITERAL else []


def audit(cands):
    problems, detail = [], []
    computed, declared, undeclared, stale = [], [], [], []
    for rel, info, text in cands:
        ps = verdict_for(rel, info["kind"])
        problems.extend(ps)
        if any(p.startswith("UNDECLARED") for p in ps):
            undeclared.append(rel)
        if any(p.startswith("STALE") for p in ps):
            stale.append(rel)
        if info["kind"] == "literal":
            if rel in DECLARED_LITERAL:
                declared.append(rel)
            detail.append("%-32s %-8s 登记理由 %d 字符" % (
                rel, "literal", len((DECLARED_LITERAL.get(rel) or u"").strip())))
        else:
            computed.append(rel)
    runtime_bad = 0
    for rel, info, text in cands:
        if rel not in computed:
            continue
        want = max(info["lists"].values()) if info["lists"] else None
        got = run_denominator(rel)
        if got is None:
            problems.append("NO-VERDICT %s --selftest 没印出可解析的夹具判决行" % rel)
            runtime_bad += 1
            continue
        n, m, line = got
        if want is None:
            problems.append("NO-LIST %s 判它现算, 但读不到模块级具名夹具清单, 交叉核对做不成" % rel)
            runtime_bad += 1
        elif m != want or n != m:
            problems.append("MISMATCH %s 末行 %d/%d 而它自己清单是 %d 条 (%s)" % (rel, n, m, want, line))
            runtime_bad += 1
        detail.append("%-32s %-8s 末行=%d/%d 清单=%-4s %s" % (
            rel, "computed", n, m, want,
            "|".join("%s:%d" % kv for kv in sorted(info["lists"].items()))))
    stats = {"scripts": len(cands), "computed": len(computed), "literal_declared": len(declared),
             "undeclared": len(undeclared), "stale": len(stale), "runtime_bad": runtime_bad}
    return problems, stats, detail


def selftest():
    lit = u'    print("FOO_SELFTEST_OK 3/3")\n'
    cmp_ = (u'FOO_FIXTURE_NAMES = ["a", "b"]\n'
            u'    print("FOO_SELFTEST_OK %d/%d" % (len(FOO_FIXTURE_NAMES), len(FOO_FIXTURE_NAMES)))\n')
    s_lit, s_cmp = scan(lit), scan(cmp_)
    known = sorted(DECLARED_LITERAL)[0]
    probes = [
        ("classify_literal", bool(s_lit) and s_lit["kind"] == "literal"),
        ("classify_computed", bool(s_cmp) and s_cmp["kind"] == "computed"),
        ("list_is_countable", bool(s_cmp["lists"]) and max(s_cmp["lists"].values()) == 2),
        ("undeclared_literal_fires",
         any(x.startswith("UNDECLARED-LITERAL") for x in verdict_for("flow/zzz_unlisted.py", "literal"))),
        ("declared_literal_is_silent", verdict_for(known, "literal") == []),
        ("stale_declaration_fires", any(x.startswith("STALE-DECLARATION") for x in verdict_for(known, "computed"))),
        ("declared_reasons_are_not_empty",
         all(len(v.strip()) >= MIN_REASON for v in DECLARED_LITERAL.values())),
    ]
    names = [n for n, _ in probes]
    missing = [c for c in FIXTURE_CLAIM_CASES if c not in set(names)]
    fails = [n for n, hit in probes if not hit] + ["declared_not_run:" + m for m in missing]
    for n, hit in probes:
        print("[fixture] %-32s %s" % (n, "bites as expected" if hit else "FAIL"))
    for m in missing:
        print("[fixture] %-32s FAIL 声明了但根本没跑到" % m)
    total = len(FIXTURE_CLAIM_CASES)
    if fails:
        print("FIXTURE_CLAIMS_SELFTEST FAIL fails=%d/%d %s" % (len(fails), total, ",".join(fails)))
        return 1
    print("FIXTURE_CLAIMS_SELFTEST OK %d/%d" % (total, total))
    return 0


def main(argv):
    if "--selftest" in argv:
        return selftest()
    cands = candidates()
    if not cands:
        die("一支带夹具判决行的脚本都没扫到 —— 解析面塌了, 不宣布零缺口")
    problems, stats, detail = audit(cands)
    d = os.path.join(ROOT, DETAIL)
    if not os.path.isdir(os.path.dirname(d)):
        os.makedirs(os.path.dirname(d))
    verdict = ("FIXTURE_CLAIMS_%s scripts=%d computed=%d literal_declared=%d "
               "undeclared=%d stale_declarations=%d runtime_mismatch=%d problems=%d" % (
        "OK" if not problems else "FAIL", stats["scripts"], stats["computed"],
        stats["literal_declared"], stats["undeclared"], stats["stale"],
        stats["runtime_bad"], len(problems)))
    with io.open(d, "w", encoding="utf-8") as f:
        f.write(u"scripts=%d computed=%d literal_declared=%d\n\n" % (
            stats["scripts"], stats["computed"], stats["literal_declared"]))
        for line in detail:
            f.write(line + "\n")
        if problems:
            f.write(u"\n== 问题 ==\n")
            for p in problems:
                f.write("PROBLEM " + p + "\n")
        # 判决行无条件落盘：保证任何按文件名 grep 末行的入口都能命中判决标记。
        f.write(u"\n== 判决 ==\n" + verdict + "\n")
    sys.stderr.write(("\n".join(problems[:10]) + "\n") if problems else "")
    print(verdict)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
