#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""官方要求正向覆盖度审计（评审没点到的要求也可能有缺口）。

承重前提（先于任何判决）：
  1. **每条要求的片段必须逐字存在于 `赛题/赛题全文.md`** —— 片段是我从官方原文里挑的
     辨识度片段，不是转述。任何一条在官方文件里找不到，就是我把要求编造了或抄错了，
     这时整份审计作废（FRAGMENT-NOT-IN-OFFICIAL），因为覆盖率的分母已经不可信。
  2. **语料必须是显式列出的交付件清单**，且跑前先证明它非空、且每条都能读到 ——
     glob 落空给一个空语料，会把"没人引用"报成"全部覆盖"。
  3. 缺口按**三档**处理，不许只报红：COVERED（在交付件里找到落点）/
     NOT_DONE（显式声明没做，必须带理由）/ NOT_APPLICABLE（这句对本交付形态不适用，
     必须带判断依据）。既没覆盖又没声明 = UNDECLARED，判红。

整行 grep 是坏度量（一句官方原话常被交付件换个说法落地），所以每条要求带一组
**同义探针**，命中任一即算覆盖；命中处连文件名与所在小节一起打印，供人复核。
"""
import io
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 官方语料取两种载体并集：4.1.3/4.1.4 的实质要求只住在 4.1.8 那份 .docx 附件的转写件
# `赛题/官方模板全文.md` 里，提取件中这两栏被标为"此栏目为空"。
OFFICIALS = [os.path.join("赛题", "赛题全文.md"),
             os.path.join("赛题", "官方模板全文.md")]
# 交付件语料：显式列举，不按目录 glob（同 hygiene 门的教训）。
CORPUS = ["docs/参赛项目设计报告.md", "docs/附件A_缺陷与门禁台账.md",
          "README.md", "demo/演示脚本与讲稿.md"]

# 每条: (官方小节, 逐字片段(必须能在赛题全文.md里原样找到), [同义探针], 声明档位, 落点小节)
# 落点小节 = (交付件相对路径, 该文件里那一节的标题正则)。给定了它，探针就**只在那一节里**
# 找 —— 否则"文档里出现过'模块'两个字"也算覆盖，那道门永远不会红，等于没有门。
# 声明档位 = {} 表示"应当被覆盖"; 否则给 NOT_DONE/NOT_APPLICABLE + 理由。
R = u"docs/参赛项目设计报告.md"
REQ = [
    ("4.1.2 一", "项目摘要（200字左右）", [u"项目摘要", u"摘要"], {}, None),
    ("4.1.2 二", "战略意义、基础作用", [u"战略意义", u"基础作用"], {}, (R, r"^## 二")),
    ("4.1.2 二", "在RISC-V全产业链所处的环节和地位", [u"产业链", u"环节", u"地位"], {},
     (R, r"^## 二")),
    ("4.1.2 二", "市场前景、应用价值", [u"市场前景", u"应用价值"], {}, (R, r"^## 二")),
    ("4.1.2 三", "架构设计方案", [u"架构", u"流水线", u"五级"], {}, (R, r"^### 3\.1")),
    ("4.1.2 三", "功能需求分析", [u"功能需求", u"需求"], {}, (R, r"^### 3\.7")),
    ("4.1.2 三", "接口方案", [u"接口", u"总线", u"CSR", u"UART"], {}, (R, r"^### 3\.8")),
    ("4.1.2 三", "模块设计", [u"模块", u"epv32_"], {}, (R, r"^### 3\.9")),
    ("4.1.2 三", "软件设计", [u"裸机", u"crt0", u"intrinsic", u"软件"], {},
     (R, r"^### 3\.10")),
    ("4.1.2 三", "关键技术和算法", [u"关键技术", u"算法", u"定点"], {}, (R, r"^### 3\.11")),
    ("4.1.2 三", "实现方法", [u"实现方法", u"实现", u"分解"], {}, (R, r"^### 3\.9")),
    ("4.1.2 四", "技术原理、实现方法等方面的创新", [u"原理", u"创新"], {}, (R, r"^## 四")),
    ("4.1.2 四", "性能提升、应用场景等方面的创新", [u"性能", u"场景", u"倍"], {},
     (R, r"^## 四")),
    ("4.1.2 五", "验证和测试方法", [u"验证", u"判定", u"黄金模型", u"轨迹"], {},
     (R, r"^## 五")),
    ("4.1.2 五", "测试环境", [u"测试环境", u"Verilator", u"TD 6.2.1"], {},
     (R, r"^### 5\.1")),
    ("4.1.2 五", "测试用例", [u"用例", u"rv32ui", u"rv32um"], {}, (R, r"^### 5\.2")),
    ("4.1.2 五", "实验结果、性能指标测试结果", [u"实测", u"加速比", u"top-1", u"LUT"], {},
     (R, r"^### 5\.4")),
    ("4.1.2 五", "风险识别与评估分析", [u"风险", u"边界", u"未关闭"], {}, (R, r"^### 5\.5")),
    ("4.1.2 五", "项目总结和未来展望", [u"总结", u"展望", u"下一步"], {},
     (R, r"^### 5\.6")),
    ("4.1.5 一.3", "数据尽可能以图表等直观表达方式呈现", [u"![图"], {}, (R, None)),
    ("4.1.5 一.3", "图表标注数据来源", [u"附图", u"registry.json", u"来源"], {}, None),
    ("4.1.5 一.4", "若相关材料提交到开源项目，请在文档中附上相关的PR/MR",
     [u"PR/MR", u"未对外开源"], {}, None),
    ("4.1.5 一.5", "设计报告、软硬件设计源代码和方案Demo", [u"方案 Demo", u"Demo"], {}, None),
    ("4.1.5 一.6", "PPT讲解答辩和实物展示，时长20分钟以内",
     [u"答辩PPT", u"20 分钟", u"20分钟"], {}, None),
    ("4.1.5 二", "黑体三号", [u"黑体"], {}, None),
    ("4.1.5 二", "仿宋体三号", [u"仿宋"], {}, None),
    ("4.1.5 二", "固定值28磅", [u"28 磅", u"28磅", u"line=560"], {}, None),
    ("4.1.5 三", "标题层次不宜超过三级", [u"层级", u"max_level"], {}, None),
    ("4.1.5 三", "未能详述的内容，可作为辅助材料以附件形式加以补充",
     [u"附件A", u"以附件"], {}, None),
    ("4.1.5 四", "时长不超过10分钟，文件不超过100MB", [u"10 分钟", u"100 MB",
                                                    u"100MB"], {}, None),
    ("4.1.7", "鼓励使用国产硬件平台和国产开源架构", [u"安路", u"国产"], {}, None),
    # 4.1.6 建议领域的两格: 本包确实没做, 显式声明而不是让探针报 UNDECLARED。
    ("4.1.6", "编译与代码优化", [],
     {"status": "NOT_DONE",
      "reason": u"本包的编译侧只到「用 GCC 把 intrinsics 编进裸机」这一层，没有自研 pass "
                u"或代码生成器；报告与 README 都按 intrinsics 手写口径写，未声称做过编译优化。"
                u"补它需要另一条工具链，属外部资源档。"}, None),
    ("4.1.6", "操作系统与软件生态", [],
     {"status": "NOT_DONE",
      "reason": u"交付形态是裸机基准（crt0 + 链接脚本 + UART 退出），没有 RTOS/驱动生态；"
                u"这一点在报告 5.5 的边界里，README 也不声称有软件生态。"}, None),
    # 官方 4.1.3/4.1.4 两栏页面为空, 实质要求只在 4.1.8 附件 .docx 的转写件里；
    # 以下片段逐字来自 `赛题/官方模板全文.md`。
    (u"附件 注意事项 9", u"能体现出RISC-V的主要特征",
     [u"主要特征", u"公序良俗"], {}, (u"README.md", r"^## 六")),
    (u"附件 注意事项 7", u"不得侵犯任何他人的专利权、著作权、商标权",
     [u"知识产权", u"许可", u"专利"], {}, (u"README.md", r"^## 六")),
    (u"附件 注意事项 10", u"须按照大赛组委会的要求补充提交参赛项目有关材料", [],
     {"status": "NOT_DONE",
      "reason": u"这是赛期内的响应义务（人在收信与补交），不是本包的一个工件；"
                u"包内能做的部分是把官方要求与提交面逐条对表并留可复跑读数，"
                u"已在 README §六与 `tools/official_coverage.py` 自己这条清单里落地。"
                u"归属：只能本人动作那一档。"}, None),
]


def section_slice(txt, head_re):
    """取某个标题往下直到下一个同级或更高级标题的那一段。"""
    if not head_re:
        return txt
    lines = txt.split(u"\n")
    start = None
    lvl = 0
    for i, l in enumerate(lines):
        m = re.match(r"(?m)^(#+)\s", l)
        if m and re.match(head_re, l):
            start = i
            lvl = len(m.group(1))
            break
    if start is None:
        return None            # 那一节根本不存在 -> 由调用方判缺口
    for j in range(start + 1, len(lines)):
        m = re.match(r"^(#+)\s", lines[j])
        if m and len(m.group(1)) <= lvl:
            return u"\n".join(lines[start:j])
    return u"\n".join(lines[start:])


def read(p):
    return io.open(os.path.join(ROOT, p), encoding="utf-8").read()


def audit(corpus=None, official=None):
    """返回 (rows, problems)。corpus/official 只为 --selftest 的替身房留口子。"""
    problems = []
    if official is not None:
        text_off = official
    else:
        # 少一份官方载体就拒绝出数：转写件缺失时, 那两条"只住在附件里"的要求会变成
        # FRAGMENT-NOT-IN-OFFICIAL(看着像我把片段编错了), 而不是被人看见"语料缺一份"。
        missing = [r for r in OFFICIALS
                   if not os.path.isfile(os.path.join(ROOT, r))]
        if missing:
            problems.append("[OFFICIAL-SOURCE-MISSING] 官方侧缺 %d 份载体: %s"
                            % (len(missing), missing))
            return [], problems
        text_off = u"\n".join(read(r) for r in OFFICIALS)
    files = corpus if corpus is not None else list(CORPUS)
    texts = {}
    for rel in files:
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p):
            problems.append("[CORPUS-MISSING] 语料文件不在: %s" % rel)
            continue
        texts[rel] = io.open(p, encoding="utf-8").read()
    if not texts:
        problems.append("[CORPUS-EMPTY] 一份交付件都没读到 —— 拒绝判绿")
        return [], problems

    rows = []
    for sec, frag, probes, decl, scope in REQ:
        # 闸 1: 片段必须逐字在官方原文里
        if frag not in text_off:
            problems.append("[FRAGMENT-NOT-IN-OFFICIAL] %s 的片段在官方文件里找不到: %r"
                            % (sec, frag[:34]))
            continue
        if not probes and not decl:
            problems.append("[NO-PROBE] %s 既没探针也没声明: %r" % (sec, frag[:34]))
            continue
        hit = None
        for rel, txt in texts.items():
            body = txt
            where_scope = u"全语料"
            if scope:
                if scope[0] != rel:
                    continue
                body = section_slice(txt, scope[1])
                if body is None:
                    problems.append("[SECTION-MISSING] %s 要求落在 %s 的那一节，但那份"
                                    "文件里没有这一节: %r" % (sec, rel, scope[1]))
                    body = u""
                where_scope = scope[1]
            for pr in probes:
                i = body.find(pr)
                if i >= 0:
                    hit = (rel, pr, where_scope)
                    break
            if hit:
                break
        if hit:
            rows.append({"sec": sec, "frag": frag, "status": "COVERED",
                         "where": "%s 小节 %s 内命中 %r"
                                  % (hit[0], hit[2], hit[1])})
        elif decl.get("status") == "NOT_DONE":
            if not decl.get("reason", "").strip():
                problems.append("[UNDECLARED] %s 声明没做但没写理由: %r" % (sec, frag[:34]))
            rows.append({"sec": sec, "frag": frag, "status": "NOT_DONE",
                         "where": decl["reason"][:60]})
        elif decl.get("status") == "NOT_APPLICABLE":
            if not decl.get("reason", "").strip():
                problems.append("[UNDECLARED] %s 判不适用但没写依据" % sec)
            rows.append({"sec": sec, "frag": frag, "status": "NOT_APPLICABLE",
                         "where": decl["reason"][:60]})
        else:
            problems.append("[UNCOVERED] %s 的这句要求在交付件里找不到落点: %r"
                            % (sec, frag[:34]))
            rows.append({"sec": sec, "frag": frag, "status": "UNCOVERED", "where": "-"})
    if len(rows) + len([p for p in problems if p.startswith("[FRAGMENT")
                        or p.startswith("[NO-PROBE")]) != len(REQ):
        problems.append("[DENOMINATOR] 要求共 %d 条, 只记录到 %d 条"
                        % (len(REQ), len(rows)))
    return rows, problems


def selftest():
    """四条注入必须各自点名原因, 且都跑在临时替身房里, 不碰真交付件。"""
    import shutil
    import tempfile
    fails = []
    box = tempfile.mkdtemp(prefix="offcov_")
    try:
        good_off = u"甲 项目摘要（200字左右）\n乙 接口方案\n丙 战略意义、基础作用\n"
        io.open(os.path.join(box, "d.md"), "w", encoding="utf-8").write(
            u"本项目摘要如下；接口见总线协议一节。\n")
        corpus = ["d.md"]
        root_saved = globals()["ROOT"]
        globals()["ROOT"] = box
        try:
            # 1) 正对照: 全部片段都在官方文里
            globals()["REQ"] = [("一", u"项目摘要（200字左右）", [u"摘要"], {}, None),
                                ("二", u"接口方案", [u"总线协议"], {}, None)]
            rows, probs = audit(corpus, good_off)
            if [r["status"] for r in rows] != ["COVERED", "COVERED"] or probs:
                fails.append("正对照没全绿: %r %r" % (rows, probs))
            else:
                print("[fixture] positive   2/2 COVERED")
            # 1b) 节内才算覆盖: 同一探针在**别的小节**里有, 也不能算覆盖
            globals()["REQ"] = [("三", "战略意义、基础作用", [u"摘要"], {},
                                 (u"d.md", r"^## 不存在的小节"))]
            _rb, pb = audit(corpus, u"甲 战略意义、基础作用\n")
            if not any(x.startswith("[SECTION-MISSING]") for x in pb):
                fails.append("指定的落点小节不存在却没判红: %r" % (pb[:2],))
            else:
                print("[fixture] scoped     落点小节缺失被点名")
            # 2) 片段被编造/抄错 -> 必须判 FRAGMENT-NOT-IN-OFFICIAL
            globals()["REQ"] = [("一", u"项目摘要（300字左右）", [u"摘要"], {}, None)]
            _r2, p2 = audit(corpus, good_off)
            if not any(x.startswith("[FRAGMENT-NOT-IN-OFFICIAL]") for x in p2):
                fails.append("编造的官方片段没被拒: %r" % (p2[:2],))
            else:
                print("[fixture] fabricated 官方里找不到的片段被拒")
            # 3) 未覆盖且未声明 -> UNCOVERED
            globals()["REQ"] = [("三", u"战略意义、基础作用", [u"根本没有这个词"],
                                 {}, None)]
            _r3, p3 = audit(corpus, good_off)
            if not any(x.startswith("[UNCOVERED]") for x in p3):
                fails.append("缺口没报 UNCOVERED")
            else:
                print("[fixture] uncovered  缺口开火")
            # 3b) 词在别的小节, 但要求落在指定小节 -> 仍须判缺口
            globals()["REQ"] = [("三", u"战略意义、基础作用", [u"摘要"], {},
                                 (u"d.md", r"^## 乙"))]
            _r3b, p3b = audit(corpus, u"甲 战略意义、基础作用\n")
            if not any(x.startswith("[UNCOVERED]") for x in p3b):
                fails.append("词在别的小节却被当成覆盖了")
            else:
                print("[fixture] cross-sec  别的小节的命中不算覆盖")
            # 4) 声明没做但不写理由 -> UNDECLARED
            globals()["REQ"] = [("三", u"战略意义、基础作用", [],
                                 {"status": "NOT_DONE", "reason": "   "}, None)]
            _r4, p4 = audit(corpus, good_off)
            if not any(x.startswith("[UNDECLARED]") for x in p4):
                fails.append("空理由的声明没开火")
            else:
                print("[fixture] bare-decl  空理由声明被拒")
            # 5) 空语料 -> CORPUS 类判红, 不许判绿
            globals()["REQ"] = [("一", u"项目摘要（200字左右）", [u"摘要"], {}, None)]
            _r5, p5 = audit(["nope.md"], good_off)
            if not any(x.startswith("[CORPUS") for x in p5):
                fails.append("语料读不到却没判红: %r" % (p5[:2],))
            else:
                print("[fixture] corpus     缺语料判红")
        finally:
            globals()["ROOT"] = root_saved
    finally:
        shutil.rmtree(box, ignore_errors=True)
    for f in fails:
        print("OFFCOV-SELFTEST-FAIL %s" % f)
    if fails:
        return 1
    print("OFFCOV_SELFTEST_OK 7/7 (positive/scoped-missing/fabricated/uncovered/"
          "cross-section/bare-decl/empty-corpus)")
    return 0


def main():
    os.chdir(ROOT)
    if "--selftest" in sys.argv:
        return selftest()
    rows, problems = audit()
    for r in rows:
        print("REQ %-16s %-11s %s" % (r["sec"][:16], r["status"], r["frag"][:28]))
        print("      -> %s" % r["where"][:120])
    for p in problems:
        print("OFFCOV-PROBLEM %s" % p)
    n_cov = sum(1 for r in rows if r["status"] == "COVERED")
    n_gap = sum(1 for r in rows if r["status"] == "UNCOVERED")
    n_dec = sum(1 for r in rows if r["status"] in ("NOT_DONE", "NOT_APPLICABLE"))
    if problems or n_gap:
        print("OFFICIAL_COVERAGE_FAILED total=%d covered=%d declared=%d gaps=%d "
              "problems=%d" % (len(REQ), n_cov, n_dec, n_gap, len(problems)))
        return 1
    print("OFFICIAL_COVERAGE_OK total=%d covered=%d declared=%d gaps=0 problems=0"
          % (len(REQ), n_cov, n_dec))
    return 0


if __name__ == "__main__":
    sys.exit(main())
