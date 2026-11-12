#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flow/verify_docx.py -- 打开生成的 docx, 逐 run 核排版

不信任 build_docx.py 的自我评价: 这里只读 .docx 二进制里最终落下的属性。
核的是赛题原话那四条:
    标题黑体三号 / 内容仿宋体三号 / 不加粗 / 行距固定值 28 磅 / 层级 <= 3

同时核"正文里的关键数字仍在 docx 中可见"—— .md 与 .docx 不一致是最容易发生的
漂移 (改了 md 忘了重跑, 或 Word 里手改过)。

--selftest 会把行距改掉再断言本门必须报红, 证明这道门真的在看属性而不是摆设。
"""
import io
import json
import os
import re
import sys

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import Pt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 封面那几行的**唯一**真源是官方模板原件；这里 import 生成端同一个读法，
# 不另写一份「什么叫封面合规」（补丁端与门禁端各写一遍同一个判据＝双重假绿）。
from build_docx import official_cover                      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.chdir(ROOT)
# 与 build_docx 同一对环境变量: 附件那份 docx 也要能被同一条排版尺子核,
# 而不是让本门永远只读设计报告、却把报告的数字当成附件的读数报出去。
DOCX = os.environ.get("DOCX_DST") or os.path.join("docs", "参赛项目设计报告.docx")
SRC = os.environ.get("DOCX_SRC") or os.path.join("docs", "参赛项目设计报告.md")

HEAD_FONT, BODY_FONT = "黑体", "仿宋"
SANHAO = Pt(16)
CODE_PT = Pt(10.5)
LINE = Pt(28)


def east_asia(run):
    rpr = run._element.find(qn("w:rPr"))
    if rpr is None:
        return None
    rf = rpr.find(qn("w:rFonts"))
    return None if rf is None else rf.get(qn("w:eastAsia"))


def walk(doc):
    """产出 (kind, paragraph); kind 区分正文/表格单元, 两者都算内容。"""
    for p in doc.paragraphs:
        yield "body", p
    for t in doc.tables:
        for row in t.rows:
            for c in row.cells:
                for p in c.paragraphs:
                    yield "cell", p


def first_heading1(doc):
    """我们这份 docx 的文档标题（= 官方模板封面的第三行该长成的样子）。"""
    for p in doc.paragraphs:
        style = (p.style.name or "") if p.style is not None else ""
        if style.startswith("Heading 1") and p.text.strip():
            return p.text.strip()
    return ""


def cover_audit(doc, cover, errs):
    """封面顶部的方向行：文本逐字取自官方模板原件，排版按 4.1.5 二的成文规则。

    官方 4.1.5 二只说「材料标题黑体三号、内容仿宋三号、不加粗、固定 28 磅」，没给封面
    开例外；而模板封面三行的实际字号是 24pt。两处官方口径互相矛盾，本包按成文规则收
    （黑体三号不加粗），只沿用原件的逐字文本与居中 —— 取舍记在收口表 P2-18 行。
    """
    if not cover:
        return 0
    want = list(cover)
    head = [p for p in doc.paragraphs if p.text.strip()][:len(want)]
    got = [p.text.strip() for p in head]
    if got != want:
        errs.append("封面顶部行与官方模板不符: 应=%r 实=%r" % (want, got))
        return len(want)
    for p in head:
        if p.alignment != WD_ALIGN_PARAGRAPH.CENTER:
            errs.append("封面行未居中: %r" % p.text[:24])
        for r in p.runs:
            if not r.text.strip():
                continue
            bad = (r.bold or east_asia(r) != HEAD_FONT
                   or (r.font.size is not None and r.font.size != SANHAO))
            if bad:
                errs.append("封面行应黑体三号不加粗: %r ea=%r sz=%r bold=%r"
                            % (p.text[:20], east_asia(r), r.font.size, r.bold))
    return len(want)


def check(path):
    doc = Document(path)
    errs = []
    n_run = n_head_run = n_code_run = n_bold = 0
    heads_seen = set()
    cover = official_cover(first_heading1(doc))
    if cover is None:
        errs.append("官方模板读不到，无法核封面（DOCX_NO_OFFICIAL_TEMPLATE）")
        cover = []
    cover_set = set(cover)
    n_cover = cover_audit(doc, cover, errs)
    for kind, p in walk(doc):
        style = (p.style.name or "") if p.style is not None else ""
        # 封面的方向行按 4.1.5 二属于「材料标题」，用黑体；它不带 Heading 样式，
        # 所以这里按**从原件读到的那几行文本**认，而不是按字体认（按字体认＝循环论证）。
        is_head = style.startswith("Heading") or p.text.strip() in cover_set
        if is_head:
            m = re.match(r"Heading (\d)", style)
            if m:
                heads_seen.add(int(m.group(1)))
        pf = p.paragraph_format
        has_text = bool(p.text.strip())
        if has_text:
            rule = pf.line_spacing_rule
            ls = pf.line_spacing
            if rule != WD_LINE_SPACING.EXACTLY or (ls is not None and ls != LINE):
                errs.append("行距不合规: %r rule=%r spacing=%r  <%s>"
                            % (p.text[:28], rule, ls, style))
        for r in p.runs:
            if not r.text.strip():
                continue
            n_run += 1
            if r.bold:
                n_bold += 1
                errs.append("出现加粗 run(要求不加粗): %r" % r.text[:28])
            if r.font.size != SANHAO:
                errs.append("字号不是三号(16pt): %r size=%r" % (r.text[:24], r.font.size))
            ea = east_asia(r)
            want = HEAD_FONT if is_head else BODY_FONT
            if ea != want:
                errs.append("中文字体不合: %r eastAsia=%r 应为 %r (head=%s)"
                            % (r.text[:24], ea, want, is_head))
            if r.font.name is not None and r.font.name != want:
                # 内容字体只许仿宋、标题只许黑体：曾给代码块开 Consolas 例外
                # （拆包数到 230 个 run，评审 P1-4）。例外已取消，这一条挡住它长回来。
                errs.append("西文字体族不合: %r ascii=%r 应为 %r"
                            % (r.text[:24], r.font.name, want))
            if is_head:
                n_head_run += 1
            if r.font.size == CODE_PT:
                n_code_run += 1     # 期望恒为 0：字号例外已取消，见 build_docx 注释
    if max(heads_seen or [0]) > 3:
        errs.append("标题层级超过三级: %s" % sorted(heads_seen))
    if n_head_run == 0:
        errs.append("一个标题 run 都没核到 —— 本门可能什么都没检查")
    if n_run < 200:
        errs.append("核到的 run 只有 %d 个, 太少, 疑似遍历不到内容" % n_run)

    # .md 与 .docx 必须同源: 抽 .md 里的数字锚, 逐个查 docx 全文
    txt_md = io.open(SRC, encoding="utf-8").read()
    txt_dx = "\n".join(p.text for _, p in walk(doc))
    anchors = re.findall(r"\b\d{1,3}(?:,\d{3})+\b|\b15\.73\b|\b\d+ / \d+\b", txt_md)
    anchors = sorted(set(anchors))
    miss = [a for a in anchors if a.replace(",", "") not in txt_dx.replace(",", "")]
    if len(anchors) < 5:
        errs.append("锚点只抽到 %d 个, 分母可疑, 这项核对等于没做" % len(anchors))
    if miss:
        errs.append("docx 缺 .md 里的数字锚: %s" % miss[:6])
    return errs, dict(runs=n_run, head_runs=n_head_run, code_runs=n_code_run,
                      bold_runs=n_bold, heading_levels=sorted(heads_seen),
                      anchors=len(anchors), cover=n_cover)


FIXTURES = ("leading", "cover_missing", "cover_uncentered")
# 每条注入各自断言「红在哪一句」：只看「有没有红」分辨不出门是在看行距还是在看封面，
# 而这三条恰好会同时改掉文档开头的同一个段落 —— 不点名原因就无法归因。
REASONS = {"leading": "行距不合规", "cover_missing": "封面顶部行",
           "cover_uncentered": "居中"}


def selftest():
    errs0, stats0 = check(DOCX)
    if errs0:
        print("DOCX_SELFTEST_SETUP_FAIL 正对照不过, 现产 docx 本身有 %d 处红" % len(errs0))
        return 1
    cover = official_cover(first_heading1(Document(DOCX))) or []
    names = list(FIXTURES) if cover else ["leading"]
    fails = []
    for name in names:
        d = Document(DOCX)
        if name == "leading":
            d.paragraphs[0].paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
        elif name == "cover_missing":
            p0 = d.paragraphs[0]
            p0._element.getparent().remove(p0._element)     # 整行删掉
        else:
            d.paragraphs[0].alignment = None                # 只改居中, 字体字号不动
        tmp = DOCX + ".inj"
        d.save(tmp)
        try:
            e2, _ = check(tmp)
        finally:
            os.remove(tmp)
        if not any(REASONS[name] in m for m in e2):
            fails.append("%s 没有让门红在「%s」上, 实际: %s"
                         % (name, REASONS[name], (e2[:1] or ["一条都没红"])))
    if not cover:
        print("DOCX_SELFTEST_NOTE 这份 docx 的标题不在官方模板前 8 段里, 封面夹具跳过")
    print("DOCX_SELFTEST names=%d fired=%d cover_lines=%d"
          % (len(names), len(names) - len(fails), len(cover)))
    for f in fails:
        print("DOCX_FIXTURE_FAIL", f)
    return 1 if fails else 0


def main():
    mutate = "--selftest" in sys.argv
    if not os.path.isfile(DOCX):
        print("DOCX_MISSING %s" % DOCX)
        print("DOCX_MISSING 按交付形态就是丢件：2026-10-16 起正式载体随包，"
              "docs/参赛项目设计报告.docx 必须在盘上。它由 python flow/build_docx.py "
              "从 docs/参赛项目设计报告.md 生成（官方模板随包在 赛题/附件/），"
              "本门核的是 4.1.5 二 的排版（黑体三号/仿宋三号/不加粗/固定 28 磅）。")
        return 1
    errs, stats = check(DOCX)
    print("排版核到 run=%d 标题run=%d 代码run=%d 加粗=%d 层级=%s 数字锚=%d 封面行=%d"
          % (stats["runs"], stats["head_runs"], stats["code_runs"],
             stats["bold_runs"], stats["heading_levels"], stats["anchors"],
             stats["cover"]))
    if mutate:
        return selftest()
    for e in errs[:20]:
        print("  DOCX_FAIL", e)
    if errs:
        print("DOCX_VERIFY_FAIL: 共 %d 处" % len(errs))
        return 1
    print("DOCX_VERIFY_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
