#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""defect_count_gate.py -- 把「测试逼出 N 条真实缺陷」这句话变成现算判据。

起因 (2026-09-26 第三方评审 R3 的 P1-1, 本轮实测确认成立)
----------------------------------------------------------
同一个计数在包里出现过三个值:

  docs/参赛项目设计报告.md  5.3 标题   「30 条」
  docs/参赛项目设计报告.md  5.3 正文   「28 条均由可反驳读数逼出」
  docs/参赛项目设计报告.md  5.6        「测试逼出 29 条真实缺陷」
  docs/附件A_缺陷与门禁台账.md A.1     表格实有 30 行
  README.md                          「30 条缺陷全表」
  flow/build_ppt.py (PPT 第 14/30 页) 「30 条」

而包内 29 步门禁没有一条锚住这个数 —— `check_deliverable_consistency.py` 只锚
步数与附图数。这正是本包自己在附件 A 第 24/28/29 条反复记过的失效模式
(「散文里的数会漂」), 而且是评委把 5.3 与 5.6 连着读就能看到的。

真源是谁
--------
附件 A 的 A.1 那张表是这条计数的**唯一真源**: 「几条缺陷」= 「表里有几行」。
所以本件不往任何地方写数字, 而是①现数表行数, ②把全部散文与生成器源码里的
「N 条 + 缺陷」句式钉到它上面。

三条会咬的判据
--------------
1) 逐数比对: 每一处「N 条…缺陷」式 claim 必须等于现算的表行数。
2) 分母守卫: 一条 claim 都没解析到 ⇒ 判红 (句子被删掉也是漂移的一种),
   表行数为 0 ⇒ 判红 (解析面塌了, 不是「没有缺陷」)。
3) 「全部由可反驳读数逼出」这句话的可核版本: A.1 每一行的「现象读数」格
   不许为空。空一格, 那句「全部」就不成立。

`--selftest` 走替身文本, 不碰任何真实交付件与归档。末行判决全 ASCII。
"""
import argparse
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

LEDGER = os.path.join("docs", u"附件A_缺陷与门禁台账.md")

# 计数句式。**不止一种** —— 一种句式一条锚点是本包记过的老账 (同一计数换个词序
# 或折行就检不出)。顺序无关, 全部并成同一个 claim 集合。
CLAIM_PATTERNS = [
    (u"title", re.compile(u"缺陷台账（(\\d+)\\s*条")),
    (u"paren", re.compile(u"缺陷（(\\d+)\\s*条）")),
    (u"pre",   re.compile(u"(\\d+)\\s*条(?:真实)?缺陷")),
    (u"verb",  re.compile(u"逼出\\s*(\\d+)\\s*条")),
    (u"tbl",   re.compile(u"(\\d+)\\s*条缺陷全表")),
]

# 语料: 正文、README、讲稿, 以及**生成器自己的源码** (PPT 的「30 条」住在
# flow/build_ppt.py 的字面量里; 只扫交付件会留下一份没人管的抄本)。
CORPUS = [os.path.join("docs", u"参赛项目设计报告.md"),
          "README.md",
          os.path.join("demo", u"演示脚本与讲稿.md"),
          os.path.join("flow", "build_ppt.py"),
          LEDGER]

ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|")


def read(rel):
    p = os.path.join(ROOT, rel.replace("/", os.sep))
    if not os.path.isfile(p):
        return None
    return io.open(p, encoding="utf-8", errors="replace").read()


def split_cells(line):
    """按未转义竖线切格; 首尾那两根竖线会各产生一个空格子, 去掉。"""
    parts = re.split(r"(?<!\\)\|", line)
    if parts and parts[0].strip() == "":
        parts = parts[1:]
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return [p.strip() for p in parts]


def ledger_rows(text):
    """返回 (行号列表, 现象读数为空的行号列表)。只取 A.1 那一节。"""
    m = re.search(r"^##+\s*A\.1\b", text, re.M)
    if not m:
        return [], []
    body = text[m.end():]
    nxt = re.search(r"^##+\s*A\.", body, re.M)
    if nxt:
        body = body[:nxt.start()]
    # 「现象读数」是第几列由表头自己说, 不写死第 3 列 (改表顺序即失效)
    hdr_col = None
    rows, empty = [], []
    for line in body.splitlines():
        if ROW_RE.match(line):
            rows.append(int(ROW_RE.match(line).group(1)))
            cells = split_cells(line)
            if hdr_col is not None and hdr_col < len(cells):
                if not cells[hdr_col]:
                    empty.append(int(ROW_RE.match(line).group(1)))
        elif u"现象读数" in line and line.strip().startswith("|"):
            hdr_col = [i for i, c in enumerate(split_cells(line))
                       if u"现象读数" in c][0]
    return rows, empty


def claims_in(text):
    """一句里多个句式同时命中只算一条 claim。

    「测试逼出 29 条真实缺陷」同时落在 `verb` 与 `pre` 两条正则上 —— 那是**同一个**
    派生数被写了两种形状, 报两条会让「几处漂移」这个数虚高一倍 (本包在别的门上
    记过同一形状: 每条错配报两次会让人以为缺口翻倍)。所以按 (行号, 数值) 去重,
    但保留它是哪一类句式的第一条, 供人核对句式覆盖。
    """
    seen = {}
    for i, line in enumerate(text.splitlines(), 1):
        for kind, rx in CLAIM_PATTERNS:
            for m in rx.finditer(line):
                seen.setdefault((i, int(m.group(1))), (kind, int(m.group(1)), i))
    return sorted(seen.values(), key=lambda t: t[2])


def audit(corpus=None):
    """corpus: {rel: text} 替身房; None 走真实交付件。"""
    problems = []
    if corpus is None:
        corpus = dict((rel, read(rel)) for rel in CORPUS)
    led = corpus.get(LEDGER) or u""
    rows, empty = ledger_rows(led)
    total = len(rows)
    if total == 0:
        problems.append("DENOM 附件 A.1 一行都没解析到 (解析面塌了, 不是没有缺陷)")
    dup = len(rows) - len(set(rows))
    if dup:
        problems.append("DUP-ROW A.1 有 %d 个重复行号" % dup)
    if empty:
        problems.append("EMPTY-READING %d 行没有「现象读数」格: %s ⇒ "
                        u"「全部由可反驳读数逼出」不成立" % (len(empty), empty))

    n_claims = 0
    for rel in CORPUS:
        text = corpus.get(rel)
        if text is None:
            problems.append("MISSING-CORPUS %s 读不到" % rel)
            continue
        for kind, n, ln in claims_in(text):
            n_claims += 1
            if total and n != total:
                problems.append("DEFECT-DRIFT [%s:%d] 写了 %d 条而 A.1 表有 %d 行"
                                % (rel, ln, n, total))
    if n_claims == 0:
        problems.append("NO-CLAIM 一处「N 条…缺陷」都没解析到 ⇒ 这句话被删了或在"
                        "一个本件看不到的地方; 两种都不许判绿")
    return total, n_claims, len(empty), problems


def good_fixture():
    """一条合法形状: 表 3 行、三处句式各写 3、现象读数格齐。"""
    led = (u"## A.1 缺陷台账（3 条，逐条带可反驳读数）\n\n"
           u"| # | 缺陷 | 现象读数 | 出处 |\n|---|---|---|---|\n"
           u"| 1 | a | `x=1` | r |\n| 2 | b | `y=2` | r |\n"
           u"| 3 | c | `z=3` | r |\n\n## A.2 别的\n")
    return {LEDGER: led,
            os.path.join("docs", u"参赛项目设计报告.md"):
                u"### 5.3 测试逼出来的真实缺陷（3 条）\n3 条缺陷全表在附件 A。\n"
                u"已完成且站得住的：测试逼出 3 条真实缺陷。\n",
            "README.md": u"附件A 3 条缺陷全表\n",
            os.path.join("demo", u"演示脚本与讲稿.md"): u"这 3 条缺陷\n",
            os.path.join("flow", "build_ppt.py"): u'SLIDES.append("3 条缺陷")\n'}


def selftest():
    """夹具的条数由本清单现算, 不写死 (写死的那份会漂 —— 附件 A 记过三次)。"""
    fixtures = []

    def add(name, fn):
        fixtures.append((name, fn))

    def case_good():
        t, c, e, p = audit(good_fixture())
        return (t == 3 and c >= 3 and not p), "good total=%s claims=%s problems=%s" % (t, c, p)

    def case_wrong_number():
        f = good_fixture()
        f["README.md"] = u"附件A 29 条缺陷全表\n"
        t, c, e, p = audit(f)
        hit = [x for x in p if x.startswith("DEFECT-DRIFT") and "README" in x]
        return len(hit) == 1, "expect exactly 1 README drift, got %s" % p

    def case_title_pattern():
        # 换一种句式也必须被抓住 (只锚一种 = 其余句式无人管)
        f = good_fixture()
        f[os.path.join("docs", u"参赛项目设计报告.md")] = \
            u"### 5.3 测试逼出来的真实缺陷（40 条）\n"
        t, c, e, p = audit(f)
        return any(x.startswith("DEFECT-DRIFT") for x in p), p

    def case_empty_reading():
        f = good_fixture()
        f[LEDGER] = f[LEDGER].replace(u"| 2 | b | `y=2` | r |", u"| 2 | b |  | r |")
        t, c, e, p = audit(f)
        return any(x.startswith("EMPTY-READING") for x in p), p

    def case_no_claim():
        f = good_fixture()
        for k in list(f):
            if k != LEDGER:
                f[k] = u"这里没有那句话\n"
        # A.1 标题自己那句「（3 条」也是一条 claim —— 要判 NO-CLAIM 就得把它也去掉,
        # 只清另外四份会留下一条命中, 于是这条夹具测的其实不是它想测的那一格。
        f[LEDGER] = f[LEDGER].replace(u"缺陷台账（3 条，逐条带可反驳读数）",
                                       u"缺陷台账（逐条带可反驳读数）")
        t, c, e, p = audit(f)
        return any(x.startswith("NO-CLAIM") for x in p), p

    def case_denom_zero():
        f = good_fixture()
        f[LEDGER] = u"## A.1 缺陷台账\n\n没有表格\n"
        t, c, e, p = audit(f)
        return any(x.startswith("DENOM") for x in p), p

    def case_missing_corpus():
        f = good_fixture()
        del f["README.md"]
        t, c, e, p = audit(f)
        return any(x.startswith("MISSING-CORPUS") for x in p), p

    def case_parser_shape():
        # 转义竖线不许被当成列边界 (表格单元里写 shell 或-真的形状)
        line = u"| 4 | e | `a\\|b=1` | r |"
        return len(split_cells(line)) == 4, split_cells(line)

    def case_section_scope():
        # A.2 里再出现一张带数字的表, 不许进分母
        f = good_fixture()
        f[LEDGER] = (f[LEDGER] + u"| 1 | 不是缺陷 | x | y |\n"
                     u"| 2 | 不是缺陷 | x | y |\n")
        t, _c, _e, _p = audit(f)
        return t == 3, "total=%d (A.2 之后的表不该进分母)" % t

    add("good_shape", case_good)
    add("wrong_number_readme", case_wrong_number)
    add("wrong_number_title_paren", case_title_pattern)
    add("empty_reading_cell", case_empty_reading)
    add("no_claim_at_all", case_no_claim)
    add("denom_zero", case_denom_zero)
    add("missing_corpus_file", case_missing_corpus)
    add("escaped_pipe_not_a_column", case_parser_shape)
    add("a1_section_scope", case_section_scope)

    ok = 0
    for name, fn in fixtures:
        try:
            good, why = fn()
        except Exception as exc:
            good, why = False, "EXC %r" % (exc,)
        if good:
            ok += 1
        else:
            sys.stdout.write("FAIL %s :: %s\n" % (name, why))
    return ok, len(fixtures)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        ok, tot = selftest()
        sys.stdout.write("DEFECT_COUNT_SELFTEST OK %d/%d\n" % (ok, tot))
        return 0 if ok == tot else 1

    total, n_claims, n_empty, problems = audit()
    for p in problems:
        sys.stdout.write("  - %s\n" % p.encode("unicode_escape").decode("ascii"))
    if problems:
        sys.stdout.write("DEFECT_COUNT_FAILED claims=%d total=%d problems=%d\n"
                         % (n_claims, total, len(problems)))
        return 1
    sys.stdout.write("DEFECT_COUNT_OK total=%d claims=%d empty_readings=0 "
                     "problems=0\n" % (total, n_claims))
    return 0


if __name__ == "__main__":
    sys.exit(main())
