#!/usr/bin/env python3
"""Dump every text run of the defense deck (numbers-gate development aid)."""
import io, os, re, sys
from pptx import Presentation

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
prs = Presentation(os.path.join(ROOT, "docs", "答辩PPT.pptx"))
out = []
for i, s in enumerate(prs.slides, 1):
    out.append("===== slide %d" % i)
    for sh in s.shapes:
        if sh.has_text_frame:
            for p in sh.text_frame.paragraphs:
                t = "".join(r.text for r in p.runs)
                if t.strip():
                    out.append("  | " + t)
    if s.has_notes_slide and s.notes_slide.notes_text_frame.text.strip():
        out.append("  NOTE: " + s.notes_slide.notes_text_frame.text.strip()[:400])
txt = "\n".join(out)
with io.open(os.path.join(ROOT, "results", "ppt_dump.txt"), "w", encoding="utf-8") as f:
    f.write(txt + "\n")
nums = sorted(set(re.findall(r"\d[\d,]*\.?\d*", txt)))
print("slides=%d chars=%d distinct_number_tokens=%d" % (len(prs.slides._sldIdLst), len(txt), len(nums)))
print(" ".join(nums))
