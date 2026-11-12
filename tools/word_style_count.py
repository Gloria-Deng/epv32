"""按 Word「字数」口径重算各节长度, 并把它落成一份归档。

为什么单独算一遍: 本项目原来的字数门只数**汉字**, 那是最宽松的口径; 赛题 4.1.2
给的是「2000 字以内」这类要求, 而格式审查(11-09~11-13)很可能按 Word 的读数判。
Word 的「字数」= 每个中日韩字符算 1 + 每个连续的西文/数字词算 1。

这份脚本**只测不判**: 它不替换现有判据, 而是把两个口径一起印出来, 让「差多少」
变成一个读数而不是一个猜测。判与不判由人决定, 决定完再进门禁。
"""
import hashlib
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.join("flow"))
sys.path.insert(0, "tools")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import run_all as RA          # 复用同一份分节逻辑, 不开第二个分节器
import check_word_limits as W

CJK = re.compile(u"[\u4e00-\u9fff\u3400-\u4dbf]")
WORD = re.compile(u"[A-Za-z0-9_]+")


def word_style(s):
    """Word「字数」近似: 汉字逐字 + 其余按连续西文/数字串算一词。"""
    n = len(CJK.findall(s))
    rest = CJK.sub(" ", s)
    return n + len(WORD.findall(rest))


def band_of(entry):
    """与 check_word_limits.py:12 那条规则同形：以内 => None..X；左右 => 0.8X..1.3X。"""
    lim, kind = entry
    if kind == u"左右":
        return [int(round(lim * 0.8)), int(round(lim * 1.3))]
    return [None, lim]


def main():
    src = io.open(W.REPORT, encoding="utf-8").read()
    # 这份归档里每个 han_chars 都是本函数从同一个 src 算的，所以"归档与工作树不同源"
    # 唯一可能的成因就是这份文件没随报告重跑。记下报告哈希，让矩阵能判它是不是过期。
    report_sha256 = hashlib.sha256(src.encode("utf-8")).hexdigest()
    # 复用 check_word_limits 那份分节器与那份「从赛题原文解析提纲」的函数 ——
    # 另写一个分节器就是第二份真源, 两处的节边界迟早各漂各的。
    secs, unknown = W.split_sections(src)
    outline = dict((n, (lim, kind)) for n, lim, kind in W.parse_outline(W.OFFICIAL))
    if unknown:
        raise SystemExit("报告里有 %d 个二级标题不匹配提纲形状, 分节不可信: %s"
                         % (len(unknown), unknown[:3]))
    out = []
    for name, body in secs:
        out.append({"section": name,
                    "han_chars": W.cjk_count(body),
                    "nonspace_chars": W.nonspace_count(body),
                    "word_style": word_style(body),
                    "limit": outline[name][0],
                    # 上限必须与汉字口径用**同一条 band 规则**（见
                    # check_word_limits.py:12：「X 字左右」=> 0.8X..1.3X）。
                    # 赛题 4.1.2 对摘要写的就是「200 字左右」，若这里仍拿 200
                    # 当硬上限，同一个官方句子会在两个口径下得出两种判决。
                    "kind": outline[name][1],
                    "band": band_of(outline[name])})
    # 交叉核对：两个口径的 band 必须逐节相同，否则"两个口径同时达标"是假话。
    wl = dict((r["section"], r["band"]) for r in
              json.loads(io.open(os.path.join("results", "word_limits.json"),
                                 encoding="utf-8").read())["sections"])
    for r in out:
        if r["section"] in wl and wl[r["section"]] != r["band"]:
            raise SystemExit("BAND_DRIFT: %s 汉字口径 band=%s 而 Word 口径 band=%s"
                             % (r["section"], wl[r["section"]], r["band"]))
    for r in out:
        r["ceiling"] = r["band"][1] if r["band"][1] is not None else r["limit"]
    missing = [r["section"] for r in out if r["limit"] is None]
    if missing:
        raise SystemExit("这些节在赛题原文里没有上限, 分母是假的: %s" % missing)
    if not out:
        raise SystemExit("一节都没数出来 —— 报告的分节标题形状变了，"
                         "不能把空集读成「在档内」")
    tight = min(out, key=lambda r: r["ceiling"] - r["word_style"])
    with io.open(os.path.join("results", "word_style.json"), "w",
                 encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"counting_basis": "han + latin/number runs "
                             "(approximates Word '字数'); NOT a pass/fail gate",
                             "official_source": W.OFFICIAL.replace(os.sep, "/"),
                             "report": W.REPORT.replace(os.sep, "/"),
                             "report_sha256": report_sha256,
                             "over_word_limit": sum(
                                 1 for r in out if r["word_style"] > r["ceiling"]),
                             # 最紧那一节离档线还剩几个单位。正文与台账只许指这两个
                             # 字段，不许再各自抄一遍数 —— 一次加词的编辑就过期。
                             "tightest_section": tight["section"],
                             "tightest_margin_units": tight["ceiling"]
                             - tight["word_style"],
                             "sections": out},
                            ensure_ascii=False, indent=1) + "\n")
    for r in out:
        hi = r["band"][1] if r["band"][1] is not None else r["limit"]
        lo = r["band"][0]
        bad = r["word_style"] > hi or (lo is not None and r["word_style"] < lo)
        print("%-16s han=%-6s word=%-6s band=%s..%s %s"
              % (r["section"], r["han_chars"], r["word_style"], lo, hi,
                 "OUT" if bad else "in"))
        r["ceiling"] = hi
    over = [r for r in out if r["word_style"] > r["ceiling"]]
    print("WORDSTYLE_MARGIN tightest=%s units=%d basis=word_style"
          % (tight["section"], tight["ceiling"] - tight["word_style"]))
    print("WORDSTYLE_CENSUS sections=%d over_word_limit=%d report_sha256=%s"
          % (len(out), len(over), report_sha256[:16]))
    print("WROTE results/word_style.json")


if __name__ == "__main__":
    main()
