#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flow/build_docx.py -- 由 docs/参赛项目设计报告.md 生成官方排版 docx

赛题对排版的原话（见 `赛题/赛题全文.md` 与附件模板）:
    材料标题字体使用黑体三号，内容字体使用仿宋体三号，不加粗；行距：固定值28磅。
    项目材料中标题层次不宜超过三级。

所以 .md 是真源, .docx 是**生成物**: 改内容只改 .md, 重跑本脚本。
排版参数集中在下面的常量里, 不在正文里散落。校验见 flow/verify_docx.py。
"""
import io
import os
import re
import sys

from docx import Document
from docx.enum.text import WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import Pt, Inches

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.chdir(ROOT)

# 默认仍是设计报告那一对; 环境变量 DOCX_SRC/DOCX_DST 只是把同一套排版通道开放给
# 别的 markdown 交付件用 (本轮: 附件 A 也要一份 docx)。不另写第二份 docx 生成器
# —— 两份各自演进迟早会给出不一致的排版, 而那正是这包用「一个模块一处真源」挡掉的形状。
SRC = os.environ.get("DOCX_SRC") or os.path.join("docs", "参赛项目设计报告.md")
DST = os.environ.get("DOCX_DST") or os.path.join("docs", "参赛项目设计报告.docx")

# 三号 = 16pt（GB/T 14691 字号对照）
SIZE_SANHAO = Pt(16)
FONT_HEAD = "黑体"          # SimHei
FONT_BODY = "仿宋"          # FangSong
LINE_PT = 28                # 固定值 28 磅
NOBRK = " "             # 代码块里保住缩进: 普通空格会被折行吃掉
IMG_RE = re.compile(r"^!\[[^\]]*\]\(([^)]+)\)$")
# 官方 4.1.5 二只给两档字体（标题黑体 / 内容仿宋体三号）。上一版给代码块开了
# 「Consolas + 10.5pt(五号)」这个例外，独立拆包回读数到 230 个这样的 run ——
# 严格审查员可以据此判「内容字体不符」。例外已取消：等宽只保留缩进用的
# 不可断空格(NOBRK)，字体与字号一律随正文。verify_docx 现在会反过来
# 断言「全文不许出现第二个字体族」，所以这个例外不会偷偷长回来。
CODE_PT = Pt(10.5)          # 仅供 verify_docx 反向断言「已经没有这个档」


def set_font(run, name, size=SIZE_SANHAO, bold=False, mono=False):
    f = run.font
    f.name = name          # mono 只影响缩进用的空格, 不再换字体族
    f.size = size
    f.bold = bold
    rpr = run._element.get_or_add_rPr()
    rf = rpr.find(qn("w:rFonts"))
    if rf is None:
        rf = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rf)
    # 中文字形走 eastAsia, 否则 Word 会用默认字体, 看起来"设了但没生效"
    rf.set(qn("w:eastAsia"), name)
    rf.set(qn("w:ascii"), f.name)
    rf.set(qn("w:hAnsi"), f.name)


def fmt_para(p, space_before=0, space_after=0):
    pf = p.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    pf.line_spacing = Pt(LINE_PT)
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    return p


INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+`)")


def add_rich(p, text, name, size=SIZE_SANHAO, bold=False):
    """处理 **粗体** 与 `行内代码`; 其余按正文样式。"""
    for tok in INLINE.split(text):
        if not tok:
            continue
        if tok.startswith("**") and tok.endswith("**") and len(tok) > 4:
            # 官方要求「内容...不加粗」, 所以 ** 只作强调标记, 落成普通字重。
            r = p.add_run(tok[2:-2])
            set_font(r, name, size, bold=False)
        elif tok.startswith("`") and tok.endswith("`") and len(tok) > 2:
            r = p.add_run(tok[1:-1])
            set_font(r, name, size, bold=bold, mono=True)
        else:
            r = p.add_run(tok)
            set_font(r, name, size, bold=bold)


def split_table_row(line):
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def is_sep_row(line):
    return bool(re.match(r"^\s*\|?[\s:\-\|]+\|[\s:\-\|]*$", line)) and "-" in line


OFFICIAL_TEMPLATE = os.path.join("赛题", "附件", "【参赛项目设计报告】高校赛道.docx")


def official_cover(title, path=OFFICIAL_TEMPLATE):
    """官方模板封面顶部那几行（标题之前的非空段），从**原件**里读，不抄字面量。

    为什么不让这里出现那两句中文：评审 P2-10/P2-18 要求的「封面缺方向标识」是一句
    可机器核的主张，而把官方原话打进来当常量，等于让门禁拿自己的转述当真值——
    模板改一个字我们就照旧绿。现在 build 与 verify 两侧各自去读同一份原件。

    两处官方口径**互相矛盾**，本函数只管文本，样式在调用侧：
      * 赛题原文 4.1.5 二：「材料标题字体使用黑体三号，内容字体使用仿宋体三号，
        不加粗；行距：固定值28磅」——没有给封面开例外；
      * 模板封面那三行的实际字号是 sz=48（24pt）。
    本包按**写出来的那条规则**收（黑体三号、不加粗、28 磅），只沿用原件的
    **逐字文本**与**居中**；这一格取舍记在收口表 P2-18 行。

    返回 None = 原件读不到（调用侧必须失败，不许静默少两行）；
    返回 []  = 这份 markdown 不是官方报告那种封面（例如附件 A 的 H1 不在模板前几段里）。
    """
    if not os.path.isfile(path):
        return None
    try:
        paras = [p.text.strip() for p in Document(path).paragraphs if p.text.strip()]
    except Exception as exc:                      # 模板坏了也要 fail-closed
        print("DOCX_TEMPLATE_UNREADABLE %r" % (exc,))
        return None
    if not title or title not in paras[:8]:
        return []
    return paras[:paras.index(title)]


def build():
    lines = io.open(SRC, encoding="utf-8").read().split("\n")
    doc = Document()
    st = doc.styles["Normal"]
    st.font.name = FONT_BODY
    st.font.size = SIZE_SANHAO
    st.element.rPr.rFonts.set(qn("w:eastAsia"), FONT_BODY)

    # ---- 封面顶部：官方模板的那几行（文本取自原件），排在正文第一个标题之前。
    #      居中照模板；字体字号照 4.1.5 二那句成文规则（黑体三号不加粗）。
    first_h1 = next((l.strip()[2:].strip().replace("**", "")
                     for l in lines if l.strip().startswith("# ")), "")
    cover = official_cover(first_h1)
    if cover is None:
        print("DOCX_NO_OFFICIAL_TEMPLATE %s"
              % OFFICIAL_TEMPLATE.replace(os.sep, "/"))
        return 1
    center_title = bool(cover)
    for t in cover:
        cp = fmt_para(doc.add_paragraph())
        cp.alignment = 1                          # 居中，与模板一致
        add_rich(cp, t, FONT_HEAD)                # 黑体三号不加粗
    print("DOCX_COVER_LINES n=%d centered=%s" % (len(cover), center_title))

    n_head = n_body = n_tbl = n_code = n_pic = 0
    i = 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()

        # ---- 图片: ![说明](相对 docs/ 的路径)
        #      图内已自带 "Source: results/..." 那一行, 所以这里**不再加图注段落**:
        #      图注会用非三号字, 撞 verify_docx 的「正文全仿宋三号」回读判据。
        m = IMG_RE.match(s)
        if m:
            rel = m.group(1).strip()
            ip = os.path.normpath(os.path.join(os.path.dirname(SRC), rel))
            if not os.path.isfile(ip):
                print("DOCX_MISSING_IMAGE: 报告引了不存在的图 %s" % ip)
                return 1
            pic = fmt_para(doc.add_paragraph())
            pic.alignment = 1                      # 居中
            pic.add_run().add_picture(ip, width=Inches(6.1))
            n_pic += 1
            i += 1
            continue

        # ---- 代码块: 整体作为一个"保持原样"的段落组
        if s.startswith("```"):
            i += 1
            buf = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            p = fmt_para(doc.add_paragraph())
            for j, cl in enumerate(buf):
                if j:
                    p.add_run().add_break()
                r = p.add_run(cl.replace(" ", "\u00a0"))
                set_font(r, FONT_BODY, SIZE_SANHAO, mono=True)
            n_code += 1
            continue

        # ---- 表格
        if s.startswith("|") and i + 1 < len(lines) and is_sep_row(lines[i + 1]):
            head = split_table_row(s)
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(split_table_row(lines[i]))
                i += 1
            t = doc.add_table(rows=1 + len(rows), cols=len(head))
            t.style = "Table Grid"
            for c, txt in enumerate(head):
                p = fmt_para(t.rows[0].cells[c].paragraphs[0])
                # 表头也用正文字体: 官方只给了「标题黑体 / 内容仿宋」两档,
                # 表格属于内容, 没有第三种字体档可用; 表头靠 Table Grid 区分。
                add_rich(p, txt, FONT_BODY)
            for r_i, row in enumerate(rows, start=1):
                for c in range(len(head)):
                    p = fmt_para(t.rows[r_i].cells[c].paragraphs[0])
                    add_rich(p, row[c] if c < len(row) else "", FONT_BODY)
            n_tbl += 1
            continue

        # ---- 标题（层级 <=3）
        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            lvl = len(m.group(1))
            txt = m.group(2).replace("**", "").strip()
            p = fmt_para(doc.add_paragraph(), space_before=6)
            r = p.add_run(txt)
            set_font(r, FONT_HEAD, SIZE_SANHAO)
            p.style = doc.styles["Heading %d" % min(lvl, 3)]
            # 样式表会带自己的字号/字体, 再压一遍确保生效
            for rr in p.runs:
                set_font(rr, FONT_HEAD, SIZE_SANHAO)
            if center_title:                      # 只有封面那一个标题居中
                p.alignment = 1
                center_title = False
            n_head += 1
            i += 1
            continue

        # ---- 引用块 / 提示行
        if s.startswith(">"):
            p = fmt_para(doc.add_paragraph())
            add_rich(p, s.lstrip("> ").strip(), FONT_BODY)
            n_body += 1
            i += 1
            continue

        # ---- 列表
        m = re.match(r"^[-*]\s+(.*)$", s)
        if m:
            p = fmt_para(doc.add_paragraph(style="List Bullet"))
            add_rich(p, m.group(1), FONT_BODY)
            n_body += 1
            i += 1
            continue

        # ---- 分隔线 / 空行
        if s in ("---", "***", ""):
            i += 1
            continue

        # ---- 正文段
        p = fmt_para(doc.add_paragraph())
        add_rich(p, s, FONT_BODY)
        n_body += 1
        i += 1

    doc.save(DST)
    print("WROTE %s  标题=%d 正文=%d 表格=%d 代码块=%d 图=%d"
          % (os.path.relpath(DST, ROOT), n_head, n_body, n_tbl, n_code, n_pic))
    # 自检: 表格数与 .md 里的表格块数必须一致, 否则说明有表格被静默丢弃
    md_tbl = sum(1 for k in range(len(lines) - 1)
                 if lines[k].strip().startswith("|") and is_sep_row(lines[k + 1]))
    if md_tbl != n_tbl:
        print("DOCX_TABLE_LOSS: .md 有 %d 张表, docx 只落了 %d 张" % (md_tbl, n_tbl))
        return 1
    # 图的自检: .md 里有几张, docx 里就必须有几张; 一条都不认的生成器会一路绿
    md_pic = sum(1 for ln in lines if IMG_RE.match(ln.strip()))
    if md_pic != n_pic:
        print("DOCX_IMAGE_LOSS: .md 有 %d 张图, docx 只落了 %d 张" % (md_pic, n_pic))
        return 1
    if md_pic and n_pic == 0:
        print("DOCX_IMAGE_LOSS: 有图却一张都没落")
        return 1
    if n_head == 0 or n_body == 0:
        print("DOCX_EMPTY_SECTION: 标题或正文一段都没生成, 解析器坏了")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(build())
