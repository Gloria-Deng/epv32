#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_word_limits.py -- 报告各节字数上限由**赛题原文**给出, 本门现读原文。

为什么要有它: 提纲里写着「五、方案测试及总结（2000字以内）」, 而这条要求在
2026-09-24 之前**从来没有一道门核过** —— 我把器件口径的实测表加进第五节,
汉字数涨到 2082, 一路把 docx 都建出来了, 没人报红。要求写在原文里而没人反查,
就等于没写。

判据形状:
  1. 上限取自查 `赛题/赛题全文.md` 的提纲行, 不写死在本文件里(原文改了本门跟着改);
  2. 「X字以内」=> 该节汉字数 <= X; 「X字左右」=> 0.8X <= 汉字数 <= 1.3X;
  3. 提纲列的每一节都必须在报告里存在; 报告里出现提纲没列的 `## ` 一节也报红
     (防止有人加一节"附录"把内容挪出去绕开上限);
  4. 计数口径 = 汉字数(CJK 表意文字); 同时把「非空白字符数」一并印出并归档,
     两个口径都摆出来才说得清"我们是按哪个过的"。

--selftest 在临时副本上做 6 条注入(超限 / 只灌宽松节 / 改节名 / 多出提纲外小节 /
原文上限收紧 / 摘要偏短), 每条都必须报出**自己那一节**; 跑完重算真实文件哈希,
证明注入零副作用。每个用例都从真实文件重抄, 不累积改动 —— 否则后一条报红的
原因可能是前一条注入造成的, 那就等于没证明这一条会咬。
"""
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "flow"))
from atomic_json import write_json_atomic      # 归档落盘的唯一实现
OFFICIAL = os.path.join("赛题", "赛题全文.md")
REPORT = os.path.join("docs", "参赛项目设计报告.md")

CN = re.compile(u"[\u4e00-\u9fff]")
# 提纲行形状: **一、项目摘要（200字左右）** / **五、方案测试及总结（2000字以内）**
OUTLINE = re.compile(
    u"^\\*\\*[一二三四五六七八九十]、(.+?)（(\\d+)字(以内|左右)）\\*\\*\\s*$", re.M)
HEAD = re.compile(u"^##\\s*[一二三四五六七八九十]、(.+?)\\s*$")


def die(msg):
    raise SystemExit("WORDLIMIT_FAIL: " + msg)


def cjk_count(text):
    return len(CN.findall(text))


def nonspace_count(text):
    return len(re.sub(r"\s", "", text))


def parse_outline(path):
    """[(节名, 上限, 类型)] 按原文顺序。解析不到 3 条以上就停 —— 是原文改了
    格式, 还是本门的正则过期了, 都不许悄悄退回硬编码表。"""
    t = io.open(path, encoding="utf-8", errors="replace").read()
    rows = [(m.group(1), int(m.group(2)), m.group(3)) for m in OUTLINE.finditer(t)]
    if len(rows) < 3:
        die("在 %s 里只解析到 %d 条提纲字数要求 —— 原文改格式了还是本门正则过期了"
            % (path, len(rows)))
    return rows


def split_sections(md_text):
    """返回 ([(节名, 正文)], [不匹配提纲形状的二级标题行])。"""
    lines = md_text.split("\n")
    heads = [(i, HEAD.match(l).group(1)) for i, l in enumerate(lines) if HEAD.match(l)]
    unknown = [l.strip() for l in lines
               if re.match(u"^##\\s", l) and not HEAD.match(l)]
    secs = []
    for k, (i, name) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        secs.append((name, "\n".join(lines[i:end])))
    return secs, unknown


def check(report_path, official_path):
    """Return (violations, rows) —— rows 是要归档的那份读数。"""
    outline = parse_outline(official_path)
    md = io.open(report_path, encoding="utf-8").read()
    secs, unknown = split_sections(md)
    by_name = {}
    for name, body in secs:
        by_name.setdefault(name, body)
    bad, rows = [], []
    names = [o[0] for o in outline]
    for name, limit, kind in outline:
        if name not in by_name:
            bad.append("提纲要求的这一节在报告里不存在: %s" % name)
            continue
        body = by_name[name]
        c, ns = cjk_count(body), nonspace_count(body)
        # 注意: 这里不能写 `lo, hi, ok = None, limit, c <= hi` —— 元组赋值右边
        # 先整体求值, `c <= hi` 读到的是**上一节**留下的 hi(实测: 515<=1000 却判
        # 出档, 因为上一节的 hi 是 260)。三步分开写。
        if kind == u"以内":
            lo = None
            hi = limit
            ok = c <= hi
        else:
            lo = int(round(0.8 * limit))
            hi = int(round(1.3 * limit))
            ok = lo <= c <= hi
        rows.append({"section": name, "limit": limit, "kind": kind,
                     "han_chars": c, "nonspace_chars": ns,
                     "band": [lo, hi], "within": bool(ok)})
        if not ok:
            bad.append("「%s」汉字数=%d 出档(原文: %d 字%s, 判据区间 %s..%s)"
                       % (name, c, limit, kind, lo, hi))
    for extra in unknown:
        bad.append("报告里出现提纲没有的二级标题: %r" % extra)
    for name in by_name:
        if name not in names:
            bad.append("报告这一节 %r 不在提纲里(不许用改节名绕开上限)" % name)
    return bad, rows


def report_out(bad, rows):
    for r in rows:
        print("  %-14s han=%-5d nonspace=%-6d limit=%-5d %-4s band=%s..%s  %s"
              % (r["section"], r["han_chars"], r["nonspace_chars"], r["limit"],
                 r["kind"], r["band"][0], r["band"][1],
                 "OK" if r["within"] else "OVER"))
    if bad:
        for b in bad:
            print("  " + b)
        print("WORDLIMIT_FAIL: %d 条" % len(bad))
        return 1
    print("WORDLIMIT_OK: %d 节全部在赛题原文给出的字数档内(汉字口径)" % len(rows))
    return 0


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return h.hexdigest()


def selftest():
    real = [os.path.join(ROOT, REPORT), os.path.join(ROOT, OFFICIAL)]
    missing = [p for p in real if not os.path.isfile(p)]
    if missing:
        for p in missing:
            print("  缺文件: " + p)
        die("SELFTEST_HAS_NO_BASELINE")
    before = dict((p, sha(p)) for p in real)
    base_bad, base_rows = check(real[0], real[1])
    if base_bad:
        print("[selftest] 正对照失败: 真实语料本来就不在档内:")
        for b in base_bad:
            print("   " + b)
        return 1
    print("[selftest] 正对照: 真实语料 %d 节全部在档内" % len(base_rows))

    tmp = tempfile.mkdtemp(prefix="wordlimit_")
    fails = []
    try:
        def fresh(tag):
            sub = os.path.join(tmp, tag)
            os.makedirs(sub)
            r, o = os.path.join(sub, "report.md"), os.path.join(sub, "official.md")
            shutil.copyfile(real[0], r)
            shutil.copyfile(real[1], o)
            return r, o

        def expect(label, r, o, want):
            bad, _ = check(r, o)
            hit = [b for b in bad if want in b]
            if not bad:
                fails.append("%s: 注入后没有报红" % label)
            elif not hit:
                fails.append("%s: 报红了但不是因为 %r (%s)"
                             % (label, want, bad[0][:90]))
            else:
                print("[selftest] %-20s 如期报红 -> %s" % (label, hit[0][:92]))

        last = base_rows[-1]["section"]

        # 0) 两端点: 合成一份"只有两节"的文档, 恰好等于上限必须 OK, 多一个字必须红。
        #    (真实文档上做不到定点 —— 节里有表格和代码; 而元组赋值这类 bug 只有在
        #     端点上才会露出来: 上一版 `lo, hi, ok = None, limit, c <= hi` 让
        #     515/1000 判出档, 在真实文档上看着却像"第五节确实超了"。)
        for delta, want_ok in ((0, True), (1, False), (-1, True)):
            r, o = fresh("endpoint%+d" % delta)
            LIM = 20
            # 标题行本身也算进节正文: "## 一、甲节" 有 3 个汉字(一/甲/节),
            # 所以正文只写 LIM-HDR 个字才刚好落到端点上。这个算术必须先断言,
            # 否则"端点"根本就不是端点。
            HDR = 3
            io.open(o, "w", encoding="utf-8").write(
                u"**一、甲节（%d字以内）**\n\n**二、乙节（%d字以内）**\n"
                u"**三、丙节（%d字以内）**\n" % (LIM, LIM, LIM))
            io.open(r, "w", encoding="utf-8").write(
                u"# 合成\n\n## 一、甲节\n\n" + u"字" * (LIM - HDR + delta) +
                u"\n\n## 二、乙节\n\n" + u"字" * 3 + u"\n\n"
                u"## 三、丙节\n\n" + u"字" * 3 + u"\n")
            bad, rows = check(r, o)
            st = [x for x in rows if x["section"] == u"甲节"][0]
            if st["han_chars"] != LIM + delta:
                fails.append("endpoint%+d: 夹具算术错 —— 期望该节 %d 字, 实测 %d"
                             % (delta, LIM + delta, st["han_chars"]))
            elif st["within"] is not want_ok or bool(bad) is want_ok:
                fails.append("endpoint%+d: han=%d limit=%d 判 %s, 期望 %s (%s)"
                             % (delta, st["han_chars"], st["limit"], st["within"],
                                want_ok, bad[:1]))
            else:
                print("[selftest] %-20s han=%d limit=%d -> within=%s"
                      % ("endpoint%+d" % delta, st["han_chars"], st["limit"],
                         st["within"]))

        # 1) 往最后一节尾部灌 600 个汉字 -> 该节必出档
        r, o = fresh("over")
        text = io.open(r, encoding="utf-8").read()
        io.open(r, "w", encoding="utf-8").write(text + u"\n" + u"填" * 600 + u"\n")
        expect("over-limit", r, o, last)

        # 1b) 同样一批字灌进**余量最大**的那节: 门不许误伤别的小节。
        # 灌多少不能写死成 600 —— 那等于假设「总有一节还留着 600 字余量」, 而正文
        # 按官方上限把预算填满之后这个前提就不成立了(夹具过期, 不是内容违规)。
        # 取 min(600, 最大余量), 并把实际灌入量一起报出来; 一节余量都没有时判红而不是跳过。
        room = max(base_rows, key=lambda x: x["limit"] - x["han_chars"])["section"]
        head = max(x["limit"] - x["han_chars"] for x in base_rows)
        if head < 1:
            fails.append("room: 没有一节还有余量可灌, 这条用例证明不了『涨字要落在正确的"
                         "节上』; 也不许靠调松限值来凑")
            room_delta = 0
        else:
            room_delta = min(600, head)
        r, o = fresh("room")
        lines = io.open(r, encoding="utf-8").read().split("\n")
        heads = [i for i, l in enumerate(lines) if HEAD.match(l)]
        names = [HEAD.match(lines[i]).group(1) for i in heads]
        k = names.index(room)
        stop = heads[k + 1] if k + 1 < len(heads) else len(lines)
        io.open(r, "w", encoding="utf-8").write(
            "\n".join(lines[:stop]) + u"\n" + u"填" * room_delta + u"\n"
            + "\n".join(lines[stop:]))
        bad, rows = check(r, o)
        st = dict((x["section"], x) for x in rows)
        was = [x for x in base_rows if x["section"] == room][0]["han_chars"]
        if st[room]["han_chars"] != was + room_delta:
            fails.append("room: %s 应从 %d 涨到 %d, 实测 %d(%d 字没落进这一节)"
                         % (room, was, was + room_delta, st[room]["han_chars"],
                            room_delta))
        elif bad:
            fails.append("room: 只灌宽松节却报红 %s" % (bad[:1],))
        else:
            print("[selftest] %-20s 只有 %s 从 %d 涨到 %d 字, 其余节读数不动 -> OK"
                  % ("room-in-right-section", room, was, st[room]["han_chars"]))

        # 2) 把末节的标题改掉 -> 提纲要求的节不见了
        r, o = fresh("missing")
        text = io.open(r, encoding="utf-8").read()
        if (u"五、%s" % last) not in text:
            fails.append("section-missing: 找不到那一节的标题, 注入是空跑")
        else:
            io.open(r, "w", encoding="utf-8").write(
                text.replace(u"## 五、%s" % last, u"## 五、%s 改名之后" % last, 1))
            expect("section-missing", r, o, last)

        # 3) 加一个提纲里没有的二级标题
        r, o = fresh("extra")
        text = io.open(r, encoding="utf-8").read()
        io.open(r, "w", encoding="utf-8").write(
            text + u"\n## 九、附录：把内容挪到这里就没人数字数了\n\n内容\n")
        expect("unknown-section", r, o, u"不在提纲里")

        # 4) 原文把上限收到"该节当前字数 - 1" -> 本门必须跟着收紧并判出档。
        #    (原来写的是 limit-1: 该节当时正好 2000 字所以恰好也红, 那是巧合 ——
        #     正文一修剪到 1998, 同一注入就变成"收紧到 1999 而 1998 字仍判 OK"。)
        r, o = fresh("tighter")
        han = base_rows[-1]["han_chars"]
        lim = base_rows[-1]["limit"]
        old = u"（%d字以内）" % lim
        text = io.open(o, encoding="utf-8").read()
        if text.count(old) != 1:
            fails.append("limit-tighter: 原文里 %r 出现 %d 次, 注入不唯一"
                         % (old, text.count(old)))
        else:
            io.open(o, "w", encoding="utf-8").write(
                text.replace(old, u"（%d字以内）" % (han - 1), 1))
            bad, rows = check(r, o)
            got = [x for x in rows if x["section"] == last][0]
            if got["limit"] != han - 1:
                fails.append("limit-tighter: 读到的上限没跟着原文变 (%d)" % got["limit"])
            elif got["within"]:
                fails.append("limit-tighter: 收紧到 %d 而该节 %d 字仍判 OK"
                             % (han - 1, got["han_chars"]))
            else:
                print("[selftest] %-20s 上限随原文 %d->%d, 该节 %d 字如期出档"
                      % ("limit-tighter", lim, han - 1, got["han_chars"]))

        # 5) 摘要削到 40 个汉字 -> 低于「左右」的下界
        r, o = fresh("short")
        lines = io.open(r, encoding="utf-8").read().split("\n")
        heads = [i for i, l in enumerate(lines) if l.startswith(u"## ")]
        body = "\n".join(lines[heads[0] + 1:heads[1]])
        keep, cnt = [], 0
        for ch in body:
            keep.append(ch)
            if CN.match(ch):
                cnt += 1
            if cnt > 40:
                break
        io.open(r, "w", encoding="utf-8").write(
            "\n".join(lines[:heads[0] + 1] + ["\n".join(keep)] + lines[heads[1]:]))
        bad, _ = check(r, o)
        hits = [b for b in bad if u"项目摘要" in b]
        if not hits:
            fails.append("abstract-too-short: 摘要削到 40 字仍未报出档 (%s)" % (bad[:1],))
        else:
            print("[selftest] %-20s 如期报红 -> %s" % ("abstract-too-short", hits[0][:92]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    after = dict((p, sha(p)) for p in real)
    if after != before:
        fails.append("自检改动了真实文件!")
    else:
        print("[selftest] 真实文件哈希前后一致(%d 个), 注入只落在临时副本" % len(after))
    if fails:
        print("WORDLIMIT_SELFTEST_FAILED: %d 条" % len(fails))
        for f in fails:
            print("   " + f)
        return 1
    print("WORDLIMIT_SELFTEST_OK: 3 个端点 + 6 条注入各自报红")
    return 0


def main():
    args = sys.argv[1:]
    if "--json" in args:
        bad, rows = check(os.path.join(ROOT, REPORT), os.path.join(ROOT, OFFICIAL))
        out = {"official_source": OFFICIAL, "report": REPORT,
               "counting_basis": "han_chars = count of CJK ideographs per section",
               "sections": rows, "violations": bad, "all_within": not bad}
        dest = os.path.join(ROOT, "results", "word_limits.json")
        write_json_atomic(dest, out)
        print("WROTE results/word_limits.json")
        return 0 if not bad else 1
    if "--selftest" in args:
        return selftest()
    bad, rows = check(os.path.join(ROOT, REPORT), os.path.join(ROOT, OFFICIAL))
    return report_out(bad, rows)


if __name__ == "__main__":
    sys.exit(main())
