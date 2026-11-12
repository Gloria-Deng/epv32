#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tools/dump_official_template.py -- 把官方模板附件的正文转写成一份可读、可 grep 的文本。

为什么要有这个文件（2026-09-26 实测出来的缺口）：
  * `赛题/赛题全文.md` 是从官方**页面**提取的，其中 4.1.3「参赛团队要求」与
    4.1.4「参赛项目要求」两栏写的是「官方页面此栏目为空，命题方未填写内容」；
  * 而这两栏的**实质要求**（注意事项 1-10、填写说明：名称需体现出RISC-V的主要特征、
    不得侵犯任何他人的专利权/著作权/商标权、评审期间须按组委会要求补充提交材料 …）
    只住在官方 4.1.8 列出的那一份附件里，也就是本文件读的那份 .docx；
  * 于是「逐条官方要求有没有落点」那道门（`tools/official_coverage.py`）如果只读
    提取件，就**看不见这两整栏** —— 不是漏几条措辞，是缺一整个载体。

判据契约（换实现也要守住）：
  1. 只允许从原件的 `word/document.xml` 里按 `<w:t>` 取段，不许手敲任何一句官方话；
  2. **往返自证**：写完/查完后，把转写件按同样的"非空行"形状读回来，与原件段落序列
     逐段比对，mismatch 必须为 0 且长度差必须为 0 —— 转写件自己就是第二份抄本，
     不往返核一次的转写等于把原件换成我打的字；
  3. 默认是**核对模式**（--check 语义：盘上转写件 == 现在从原件转出来的），
     只有显式 `--write` 才落盘。官方原件在赛期内不该变；它变了就该让人看见，
     而不是被链条悄悄刷新；
  4. 段数低于门槛（MIN_PARAS）判 SETUP-FAIL 而不是判绿 —— 空转写件会让"片段在不在
     官方原文里"这道闸对任何句子都返回"不在"，看起来像严格、其实是塌了；
  5. 判决行是 stdout 最后一行且纯 ASCII。

用法：
  python tools/dump_official_template.py            # 核对（链条用这一档）
  python tools/dump_official_template.py --write    # 重新转写落盘
  python tools/dump_official_template.py --selftest # 双向 A/B
"""
from __future__ import print_function

import io
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OFFICIAL_DIR = os.path.join("赛题", "附件")
OUT_REL = os.path.join("赛题", "官方模板全文.md")
MIN_PARAS = 30                     # 官方模板实测 40 段非空；低于这个数就是解析面塌了
# 具名夹具清单（本包的契约：自带 --selftest 的脚本必须在**模块级**列出夹具名，
# 这样"末行的分母"由清单长度导出，而跑过的集合要和清单求差——否则某条夹具因为
# 分支没走到而从未执行，末行照样写 n/n。`tools/fixture_claims.py` 就是查这个的。）
FIXTURE_NAMES = ("writer", "positive", "dropped_paragraph", "reworded",
                 "missing_template")
HEAD = u"# 官方模板附件正文转写\n"


def find_template(root=ROOT):
    """官方 4.1.8 只列一份附件；按「设计报告」形状找，找到 0 份或 >1 份都拒绝。"""
    d = os.path.join(root, OFFICIAL_DIR)
    if not os.path.isdir(d):
        return None
    cand = [f for f in sorted(os.listdir(d))
            if f.lower().endswith(".docx") and u"设计报告" in f]
    if len(cand) != 1:
        return None
    return os.path.join(d, cand[0])


def para_texts(path):
    xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8", "replace")
    out = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
        t = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S))
        for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                     ("&quot;", '"'), ("&apos;", "'")):
            t = t.replace(a, b)
        t = t.strip()
        if t:
            out.append(t)
    return out


def render(src):
    return HEAD + u"\n" + u"\n".join(src) + u"\n"


def readback(path):
    if not os.path.isfile(path):
        return []
    return [l.strip() for l in io.open(path, encoding="utf-8").read().split("\n")
            if l.strip() and not l.startswith("#")]


def check(write=False, root=ROOT):
    tpl = find_template(root)
    if tpl is None:
        print("TRANSCRIPT_SETUP_FAIL official template not found or ambiguous")
        return 2
    src = para_texts(tpl)
    if len(src) < MIN_PARAS:
        print("TRANSCRIPT_SETUP_FAIL paragraphs=%d below_min=%d" % (len(src), MIN_PARAS))
        return 2
    out = os.path.join(root, OUT_REL)
    want = render(src)
    if write:
        tmp = out + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(want)
        os.replace(tmp, out)
    got = io.open(out, encoding="utf-8").read() if os.path.isfile(out) else None
    if got is None:
        print("TRANSCRIPT_MISSING %s (run with --write)"
              % OUT_REL.replace(os.sep, "/"))
        return 1
    if got != want:
        # 报出是"哪一段"漂了，而不只说"文件不同"——否则判红也无处下手
        rt = readback(out)
        mism = sum(1 for a, b in zip(src, rt) if a != b) + abs(len(src) - len(rt))
        print("TRANSCRIPT_DRIFT paras=%d mismatch=%d" % (len(src), mism))
        return 1
    rt = readback(out)
    mism = sum(1 for a, b in zip(src, rt) if a != b)
    len_diff = len(src) - len(rt)
    if mism or len_diff:
        print("TRANSCRIPT_MISMATCH paras=%d readback=%d mismatch=%d len_diff=%d"
              % (len(src), len(rt), mism, len_diff))
        return 1
    print("TEMPLATE_TRANSCRIPT_OK paras=%d mismatch=0 len_diff=%d" % (len(src), 0))
    return 0


def selftest():
    """双向 A/B: 正对照 + 抹掉一段 + 整份截断, 三种都要能红。"""
    import shutil
    import tempfile
    box = tempfile.mkdtemp(prefix="tmpl_ss_")
    try:
        ad = os.path.join(box, OFFICIAL_DIR)
        os.makedirs(ad)
        tpl = find_template(ROOT)
        dst = os.path.join(ad, os.path.basename(tpl))
        shutil.copyfile(tpl, dst)
        ran, fails = [], []
        if check(write=True, root=box) != 0:
            fails.append("write 分支没成功")
        ran.append("writer")
        if check(root=box) != 0:
            fails.append("正对照: 刚写完就该 OK")
        ran.append("positive")
        # 注入 1: 删掉一段（转写件少一段 -> 必须红）
        p = os.path.join(box, OUT_REL)
        lines = io.open(p, encoding="utf-8").read().split("\n")
        io.open(p, "w", encoding="utf-8", newline="\n").write(
            "\n".join(lines[:3] + lines[5:]))
        if check(root=box) == 0:
            fails.append("删掉两段后仍判 OK —— 往返比对是摆设")
        ran.append("dropped_paragraph")
        # 注入 2: 改一个字（同段数、不同内容 -> 必须红）
        check(write=True, root=box)
        t = io.open(p, encoding="utf-8").read().replace(u"注意事项", u"注意义", 1)
        io.open(p, "w", encoding="utf-8", newline="\n").write(t)
        if check(root=box) == 0:
            fails.append("改字后仍判 OK —— 逐段比对没咬内容")
        ran.append("reworded")
        # 注入 3: 原件缺失 -> SETUP-FAIL, 不许当成"没有要求"
        os.remove(dst)
        if check(root=box) != 2:
            fails.append("原件缺失没报 SETUP-FAIL")
        ran.append("missing_template")
        # 清单与"真跑过的集合"求差: 分支没走到 = 那条夹具根本没跑, 末行不许照样写 n/n
        never = [n for n in FIXTURE_NAMES if n not in ran]
        if never:
            fails.append("具名清单里有 %d 条从未被执行: %s" % (len(never), never))
        extra = [n for n in ran if n not in FIXTURE_NAMES]
        if extra:
            fails.append("跑到了 %d 个不在清单上的名字: %s" % (len(extra), extra))
        print("TEMPLATE_SELFTEST_OK %d/%d" % (len(FIXTURE_NAMES) - len(fails),
                                              len(FIXTURE_NAMES)))
        for f in fails:
            print("TEMPLATE_FIXTURE_FAIL", f)
        return 1 if fails else 0
    finally:
        shutil.rmtree(box, ignore_errors=True)


def main(argv):
    if "--selftest" in argv:
        return selftest()
    return check(write="--write" in argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
