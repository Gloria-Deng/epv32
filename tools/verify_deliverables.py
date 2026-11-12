"""交付件级验收: 交付正文里真有图、器件数只有一份来源、废弃数字不复活。

为什么单独要这一支: 矩阵全绿说的是「归档自洽」, 而评委拿到的是 docx 与 pptx。
评审记的第 1 号问题正是 docx 生成于 21:34 而 .md 改到 23:10 —— 那是一道
「谁都没核过这份生成物是不是这一趟写的」的缺口。

载体自适应（2026-09-27 参赛者决定对外只交 Markdown，三份 Office 渲染件不随包）：
第 2/3 节问的是"交付正文"，渲染件在盘上就读渲染件，不在就读它的 .md 真源
（.docx 本就是 build_docx.py 从同一份 .md 渲出来的，两半文字同源），每次换载体都
印出用的哪一份，且计入末行的 carrier 计数——判据一条没减，只是不许把它读成"查过渲染件"。
第 1 节的"生成物不比源旧"只在渲染件存在时才有对象：渲染件缺席时印 DECLARED-ABSENT
并计数，既不判绿也不静默跳过。排版（黑体三号/仿宋三号/固定 28 磅）只有渲染件才有
对象，那一半归 flow/verify_docx.py，它在渲染件缺席时判红并点名要重跑哪条命令。
"""
import io
import json
import os
import sys
import zipfile
import hashlib
import re

if hasattr(sys.stdout, "reconfigure"):        # 本文件一路印中文，GBK 控制台上不守卫就炸
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "flow")
sys.path.insert(0, "tools")
import run_all as RA          # noqa: E402

SRC = "docs/" + chr(0x53c2) + chr(0x8d5b) + chr(0x9879) + chr(0x76ee) \
    + chr(0x8bbe) + chr(0x8ba1) + chr(0x62a5) + chr(0x544a) + ".md"
DOCS = "docs/参赛项目设计报告.docx"
DECK = "docs/" + chr(0x7b54) + chr(0x8fa9) + "PPT.pptx"
FIGDIR = os.path.join("docs", chr(0x9644) + chr(0x56fe))
# 渲染件 -> 它的 Markdown 真源。写死这一张表而不是到处 if 文件名：
# 换载体的决定只许有一处，读出的是哪一版文字则每次都必须印出来。
MD_TWIN = {DOCS: SRC, DECK: "docs/" + chr(0x7b54) + chr(0x8fa9) + "PPT.md"}
DECLARED = []          # 第 1 节：这一趟没对象可核的那些
CARRIERS = []          # 第 2/3 节：这次读的是哪一份文字

ok = True


def declare(msg):
    """不改变 ok —— 判红会把人推回去重新生成渲染件，判绿是撒谎。"""
    print("  DECLARED-ABSENT " + msg)
    DECLARED.append(msg)


def carrier(rendered):
    """("rendered"|"markdown"|"absent", 实际要读的路径)。"""
    if os.path.isfile(rendered):
        return "rendered", rendered
    twin = MD_TWIN.get(rendered)
    if twin and os.path.isfile(twin):
        return "markdown", twin
    return "absent", rendered


def registered_figures():
    reg = json.load(io.open(os.path.join(FIGDIR, "registry.json"), encoding="utf-8"))
    return [x["file"] for x in reg["figures"]]


def md_figure_gap(path):
    """Markdown 载体：登记簿里每张图都要被点到，且那个文件真在包内。"""
    t = io.open(path, encoding="utf-8", errors="replace").read()
    refs = set(re.findall(r"fig_[a-z]_[a-z0-9_]*\.png", t))
    want = set(registered_figures())
    missing_file = sorted(x for x in want
                           if not os.path.isfile(os.path.join(FIGDIR, x)))
    return sorted(want - refs), missing_file


def plain_text(kind, path):
    if kind == "rendered":
        from docx import Document
        d = Document(path)
        t = "\n".join(p.text for p in d.paragraphs)
        for tbl in d.tables:
            for row in tbl.rows:
                t += "\n" + " ".join(c.text for c in row.cells)
        return t
    return io.open(path, encoding="utf-8", errors="replace").read()


def chk(cond, msg):
    global ok
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        ok = False


def media(pfx, arc):
    z = zipfile.ZipFile(arc)
    return [n for n in z.namelist() if n.startswith(pfx)]


msrc = os.path.getmtime(SRC)
print("== 1) 生成物不比源旧（渲染件在盘上才有对象） ==")
for f in (DOCS, DECK):
    kind, obj = carrier(f)
    if kind == "rendered":
        chk(os.path.getmtime(obj) >= msrc, "%s mtime >= %s" % (f, SRC))
    elif kind == "markdown":
        declare("%s 未随包（交付形态=Markdown，正文 %s 在盘上）；这条判据要重新有对象，跑 python flow/build_docx.py 或 flow/build_ppt.py" % (f, obj))
    else:
        chk(False, "%s 与它的 Markdown 真源 %s 都不在盘上" % (f, MD_TWIN.get(f)))

print("== 2) 交付正文里真有图（登记簿逐张点名） ==")
want = registered_figures()
nreg = len(want)
chk(nreg > 0, "登记簿分母 %d（为 0 就是解析面塌了）" % nreg)
for f in (DOCS, DECK):
    kind, obj = carrier(f)
    CARRIERS.append("%s=%s" % (f, kind))
    if kind == "absent":
        chk(False, "%s 与其 Markdown 真源都不在盘上，图无法核对" % f)
        continue
    if kind == "rendered":
        pfx = "word/media/" if f == DOCS else "ppt/media/"
        got = [x for x in media(pfx, obj) if not x.endswith("/")]
        chk(len(got) >= nreg, "%s 内嵌 %d 张, 登记簿 %d 张" % (f, len(got), nreg))
    else:
        miss, offdisk = md_figure_gap(obj)
        chk(not miss, "%s 缺 %d/%d 张图的引用 %r" % (f, len(miss), nreg, miss))
        chk(not offdisk, "%s 点到的图有 %d 张不在盘上 %r" % (f, len(offdisk), offdisk))

print("== 3) 器件数只有一个来源, 且交付件里出现的就是它 ==")
syn, par, _c = RA.pnr_arms()
key = {"epv_luts": str(syn["epv"]["total_luts"]),
       "base_luts": str(syn["baseline"]["total_luts"]),
       "net": str(syn["delta"]["total_luts_abs"]),
       "routed_epv": "%.3f" % par["epv"]["fmax_mhz"],
       "routed_base": "%.3f" % par["baseline"]["fmax_mhz"]}
kind_doc, obj_doc = carrier(DOCS)
dt = plain_text(kind_doc, obj_doc)
for k, v in key.items():
    chk(v in dt.replace(",", ""), "%s 里有 %s=%s" % (obj_doc, k, v))
# 反向: 评审列的废弃数字不许在 docx 里复活
BANNED = ["8388", "3123", "5205", "29.284", "37.418", "12.31", "4951",
          "18.78", "28.434", "34.080", "13.13", "5265", "168.59"]
dtnc = dt.replace(",", "").replace(" ", "")
bad = [b for b in BANNED if b in dtnc]
chk(not bad, "%s 里已无废弃数字 (残留=%s)" % (obj_doc, bad))

print("== 4) 矩阵结论 ==")
# 矩阵只在全链跑完后才落 matrix.json，链条内此刻读到的是上一趟的结果；
# 所以链条内跳过本节，链条外独立复跑（不带 flag）才检查。
if "--inside-matrix" in sys.argv:
    print("  SKIP matrix.json: 本步由认证矩阵调用，此刻盘上那份属于上一趟；"
          "链条外跑 `python tools/verify_deliverables.py` 才查这一节")
else:
    m = RA.jload("results/matrix.json")
    chk(m["all_green"] and m["steps"] == m["n_total_in_table"] and not m["ran_subset"],
        "matrix.json all_green=%s steps=%s/%s ran_subset=%s"
        % (m["all_green"], m["steps"], m["n_total_in_table"], m["ran_subset"]))
    chk(m["rtl_sha256_head"] == hashlib.sha256(
        open(os.path.join("rtl", "epv32_core.v"), "rb").read()).hexdigest()[:16],
        "矩阵记的 rtl 指纹 == 工作树")

print("== 5) 人字段: 已填且不留占位 (报告首页是唯一权威) ==")
# 旧判据要求盘上留有【待填】以防工具编名，但报告本须体现队员与指导教师信息，
# 会把它判成"不许满足"——方向跟着现实翻。现在：占位符归零，字段值一律从报告现读（不抄第二份名字）。
rt = io.open(SRC, encoding="utf-8").read()
sys.path.insert(0, os.path.join(".", "flow"))
import check_deliverable_consistency as CD   # noqa: E402
chk(rt.count(u"【待填】") == 0,
    "报告首页【待填】残留 %d 处 (须为 0)" % rt.count(u"【待填】"))
_fields = CD.roster_from_report(rt)
for _lab, _val in sorted(_fields.items()):
    chk(bool(_val.strip()), "首页字段非空: %s" % _lab.encode("unicode_escape"))
chk(sum(1 for v in _fields.values() if v.strip()) == len(_fields),
    "首页 %d 个字段全部有值" % len(_fields))
_d = io.open("README.md", encoding="utf-8").read()
chk(u"【待填】" not in _d, "README 不再声称字段待填")

print("\nRESULT %s carrier=%s declared_absent=%d%s" % (
    ("ALL-DELIVERABLE-CHECKS-PASS" if ok else "SOME-CHECKS-FAILED"),
    ",".join(CARRIERS) or "NA", len(DECLARED),
    "" if not DECLARED else " (渲染件未随包的那几条见上方 DECLARED-ABSENT)"))
sys.exit(0 if ok else 1)
