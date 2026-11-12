#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flow/build_ppt.py 的产物 -> docs/答辩PPT.md（逐页文本，Markdown 交付形态）。

为什么要有这一支而不是手写一份 Markdown：手写就是「同一个内容的第二份抄本」，
而本包的全部纪律就是不许有第二份抄本。这一支只读 .pptx 的文本框与讲稿，
不做任何改写，所以 .md 里出现的每个数都还是 build_ppt.py 从归档现读出来的那个。

四条判据（顺序不可交换）：
  1) 缺 .pptx 或缺 python-pptx => SETUP-FAIL 退 2（仪器层，不冒充内容判决）；
  2) 页数必须等于 results/ppt/verify.json 里那一格现读的 slides（参照臂来自归档，
     不由本次导出自己数出来 —— 同源分母的自检永远绿）；
  3) .pptx 里的每一个数字 token 必须逐个出现在导出的 .md 里（转形态不许丢数）；
  4) 判决行只印 ASCII，且是 stdout 的最后一行。
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECK = os.path.join(ROOT, "docs", u"答辩PPT.pptx")
MD = os.path.join(ROOT, "docs", u"答辩PPT.md")
VERIFY = os.path.join(ROOT, "results", "ppt", "verify.json")

NUM = re.compile(r"\d[\d,]*\.?\d*")


def setup_fail(reason, extra=""):
    sys.stdout.write("PPT_MD_SETUP-FAIL %s %s\n" % (reason, extra))
    sys.exit(2)


if not os.path.isfile(DECK):
    setup_fail("deck-missing", "(先跑 python flow/build_ppt.py 再导 Markdown)")
try:
    from pptx import Presentation
except Exception as exc:
    setup_fail("python-pptx-unavailable", repr(exc))

if not os.path.isfile(VERIFY):
    setup_fail("verify-json-missing", VERIFY)
arc = json.load(io.open(VERIFY, encoding="utf-8"))
for k in ("slides", "tokens"):
    if k not in arc:
        setup_fail("verify-json-no-key", k)

prs = Presentation(DECK)


def figure_pointer(title):
    """附图页的标题只有「字母 + 主题词」，画面本身是一张 PNG。

    这一支要是只写「本页无文本」，读者就永远指不回那张图 —— 而图作为文件本来就随包
    （docs/附图/*.png），所以这里按登记簿把页名换成可解析的路径与图注。
    按字母 glob，恰好一支才算数；零支或多支都停下来问，不许挑第一个。
    """
    key = (title or "").strip()[:1].lower()
    if not ("a" <= key <= "z"):
        return None
    d = os.path.join(ROOT, "docs", u"附图")
    hits = sorted(x for x in os.listdir(d)
                  if x.startswith("fig_%s_" % key) and x.lower().endswith(".png"))
    if len(hits) != 1:
        sys.stdout.write("PPT_MD_FIGURE-AMBIGUOUS key=%s hits=%r\n" % (key, hits))
        sys.exit(1)
    fname = hits[0]
    legend = ""
    try:
        reg = json.load(io.open(os.path.join(d, "registry.json"), encoding="utf-8"))
        legend = reg.get("figure_legends", {}).get(fname, "")
    except Exception:
        legend = ""
    return (fname, legend)


pages = []
for i, s in enumerate(prs.slides, 1):
    blocks = []
    title = None
    for sh in s.shapes:
        if not sh.has_text_frame:
            continue
        for p in sh.text_frame.paragraphs:
            t = "".join(r.text for r in p.runs).strip()
            if not t:
                continue
            if title is None:
                title = t
            else:
                blocks.append((p.level, t))
    note = ""
    if s.has_notes_slide:
        note = s.notes_slide.notes_text_frame.text.strip()
    pages.append((i, title or "(本页无文本)", blocks, note))

deck_nums = set()
for i, s in enumerate(prs.slides, 1):
    for sh in s.shapes:
        if sh.has_text_frame:
            deck_nums.update(NUM.findall(sh.text_frame.text))
    if s.has_notes_slide:
        deck_nums.update(NUM.findall(s.notes_slide.notes_text_frame.text))

L = [u"# 答辩 PPT · 逐页文本（Markdown 交付形态）", u"",
     u"本件由 `python tools/dump_ppt_md.py` 从 `flow/build_ppt.py` 的产物逐页导出，"
     u"不改写任何一句话；屏幕上的每个数仍由生成器从归档现读。"
     u"页数与数字 token 档位以 `results/ppt/verify.json` 为准，本文件末尾不抄那些数。",
     u""]
for (i, title, blocks, note) in pages:
    L.append(u"## 第 %d 页 · %s" % (i, title))
    for (lvl, t) in blocks:
        L.append(u"%s- %s" % (u"  " * max(0, lvl), t))
    if not blocks:
        fp = figure_pointer(title)
        if fp:
            fname, legend = fp
            L.append(u"本页是一张数据附图，画面即随包文件 `docs/附图/%s`。" % fname)
            if legend:
                L.append(u"")
                L.append(u"图注（取自 `docs/附图/registry.json` 原文）：%s" % legend)
        else:
            L.append(u"（本页无文本）")
    if note:
        L.append(u"")
        L.append(u"> 讲稿：%s" % note)
    L.append(u"")
text = "\n".join(L).rstrip() + "\n"

md_nums = set(NUM.findall(text))
lost = sorted(deck_nums - md_nums)
n_pages = len(re.findall(r"(?m)^## 第 \d+ 页 ", text))
empty = sum(1 for (_i, _t, b, _n) in pages if not b)

io.open(MD, "w", encoding="utf-8", newline="\n").write(text)
back = io.open(MD, encoding="utf-8").read()
if back != text:
    setup_fail("readback-differs", "bytes %d/%d" % (len(back), len(text)))

print("deck=%s md_pages=%d archive_slides=%s md_tokens=%d deck_tokens=%d empty_pages=%d"
      % (os.path.basename(DECK), n_pages, arc["slides"], len(md_nums), len(deck_nums), empty))
problems = []
if n_pages != int(arc["slides"]):
    problems.append("PAGE-COUNT md=%d archive=%s" % (n_pages, arc["slides"]))
if lost:
    problems.append("TOKENS-LOST-IN-CONVERSION %d 个: %s" % (len(lost), lost[:8]))
if len(back) < 200:
    problems.append("MD-TOO-SHORT %d" % len(back))
for p in problems:
    sys.stdout.write("PPT_MD_PROBLEM %s\n" % p)
sys.stdout.write("PPT_MD_%s bytes=%d pages=%d tokens=%d\n"
                 % ("OK" if not problems else "FAIL", len(back), n_pages, len(md_nums)))
sys.exit(0 if not problems else 1)
