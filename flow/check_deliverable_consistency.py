# -*- coding: utf-8 -*-
"""跨交付件「结论级」一致性门禁 (矩阵第 24 步)。

补 doc_gate 只管「数字对不对」、不管「话说得对不对」的缺口，双向判定：已落地的
结论不许被写成未做/下一步做（防过期），未落地的结论不许被写成已完成（防夸大）。
真值在运行时从归档与 run_all 步表现取，本文件不抄任何测量数，数字级核对仍归 doc_gate。

判决行是 stdout 最后一行且纯 ASCII：父进程按 UTF-8 解码子进程输出而本机控制台是 GBK。
"""
from __future__ import print_function

import io
import json
import os
import re
import sys

sys.dont_write_bytecode = True   # 本件会 import flow/run_all.py, 不许长出 __pycache__

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "flow"))

# 交付件语料: 四份文本出口 + PPTX (它是 zip, 必须解出 <a:t> 才算"评委看到的话")
DOC_KEYS = ("report", "readme", "demo", "ppt")

# 主语词: 这条结论在讲什么。没有主语的句子不参与判定 —— 否则「这张网络的权重
# 未训练」(讲的是合成网络 nn_net, 完全合法) 会被 CIFAR 的禁令误杀。
SUBJECTS = {
    "cifar10_acc": (u"CIFAR", u"真实数据集"),
    "cifar10_inloop": (u"CIFAR", u"真实数据集", u"在环"),
    "mem_curve": (u"访存", u"敏感性曲线", u"折损曲线", u"存储层次"),
    "epv_trace": (u"EPV 侧", u"EPV侧", u"17 条扩展", u"LVB/SVB"),
    "board": (u"上板", u"实物", u"真机"),
}
# roster 不在这里: 它没有"未做/已做"两种说法可判, 由下面那段按字段逐格核
# (首页四字段非空 + 不许留占位符 + 报告的值必须逐字出现在 PPT 里)。

# 否定词: 把结论说成"还没有"。
NEGATORS = (u"未做", u"没做", u"未落地", u"没有读数", u"无读数", u"待补", u"下一步",
            u"计划", u"还未", u"尚未", u"没读数", u"未验通", u"未训练", u"伪随机")

# 更正语境: 同一句里出现即豁免 —— 因为"撤销一条过期的未做声明"这句话本身必须
# 提到"未做"。豁免词一律取具体短语而不是通用词 (通用词等于对全语料解除武装)。
CORRECTIONS = (u"已落地", u"已补齐", u"已做完", u"本轮已补", u"现已", u"已经做完",
               u"原先列在", u"不再列", u"已复认证", u"已完成", u"本轮新增", u"改列")

# 肯定词: 把未落地的结论说成"已经有了"。
AFFIRMATORS = (u"已上板", u"上板验证", u"已实测于板", u"逐条一致", u"逐条相同",
               u"已接入", u"已接好", u"完成对齐")

# 负向命中 (说成已完成) 的豁免语境: 撤回禁令本身、把它列为目标、以及同句自带否定。
# 禁令会咬到解释禁令的那句话 —— 「此前两份交付件不出现"已上板验证"」是诚实自律不是夸大。
# 豁免窗口一律收到本句, --selftest 有"标记在别句就不许豁免"的对照, 防退化成全文解除武装。
CLAIM_WITHDRAWAL = (u"不出现", u"不许出现", u"不得出现", u"未做", u"没做", u"尚未",
                    u"还未", u"下一步", u"扩到", u"升到", u"让", u"争取", u"目标是",
                    u"待", u"计划", u"仍需", u"没有归档支撑")


def sentences(text):
    """按句切; 表格行与 PPT 项目符号各算一句 (换行即断)。

    必须按句而不是按段: 段级窗口会让"这一段某处有个已落地"豁免掉同段另一句
    真实的过期声明。也不许按行 —— 折行的引文会只匹配到半句。
    """
    text = text.replace(u"\r\n", u"\n")
    parts = re.split(u"[\n。；;！!]", text)
    return [p.strip() for p in parts if p.strip()]


def read_doc(kind, paths):
    if kind == "ppt":
        import zipfile
        arc = paths["ppt"]
        if not os.path.isfile(arc):
            raise IOError("缺交付件: %s" % arc)
        z = zipfile.ZipFile(arc)
        names = sorted([n for n in z.namelist()
                        if re.match(r"ppt/slides/slide\d+\.xml$", n)],
                       key=lambda s: int(re.search(r"(\d+)", s).group(1)))
        chunks = []
        for n in names:
            chunks += re.findall(r"<a:t>(.*?)</a:t>",
                                 z.read(n).decode("utf-8"), re.S)
        return u"\n".join(chunks)
    p = paths[kind]
    if not os.path.isfile(p):
        raise IOError("缺交付件: %s" % p)
    return io.open(p, encoding="utf-8").read()


def jget(rel):
    p = os.path.join(ROOT, rel)
    if not os.path.isfile(p):
        return None
    return json.load(io.open(p, encoding="utf-8"))


def truth_of():
    """每条结论"到底做没做", 从归档/步表现算。缺料即判 False 并记缺的是哪份。

    返回 {key: (landed_bool, why_str, required_tokens_list)}
    """
    t = {}

    a = jget(os.path.join("results", "bench", "cifar10_int8.json"))
    if a is None or "top1_int8" not in a or "top1_float_numpy" not in a:
        t["cifar10_acc"] = (False, "cifar10_int8.json 缺件或缺 top1 字段", [])
    else:
        # 允许归档自己印出的任一形状: 0.4548 是原生印法, 45.48 是乘 100 的派生印法。
        # PPT 门禁只认原生印法, 这里若只认 45.48 会逼两个出口用两套口径 ——
        # 本判据拦的是"整块证据没进交付件", 不是小数点挪位。
        v = a["top1_int8"]
        t["cifar10_acc"] = (True, "top1_int8=%r" % v,
                            [[u"%.2f" % (v * 100.0), u"%r" % v]])

    b = jget(os.path.join("results", "bench", "cifar10_net.json"))
    if b is None or "speedup" not in b:
        t["cifar10_inloop"] = (False, "cifar10_net.json 缺件或缺 speedup", [])
    else:
        landed = (b.get("verdict") == "PASS" and b.get("gate") == "PASS"
                  and b.get("bit_exact_ref_vs_epv") is True
                  and b.get("bit_exact_ref_vs_python") is True)
        sp = b["speedup"]
        t["cifar10_inloop"] = (landed, "speedup=%r verdict=%r" % (
            sp, b.get("verdict")),
            [[u"%.2f" % sp, repr(sp), "%g" % sp]] if landed else [])

    c = jget(os.path.join("results", "bench", "mem_sensitivity.json"))
    if c is None:
        t["mem_curve"] = (False, "mem_sensitivity.json 缺件", [])
    else:
        ncase = len(c.get("cases") or [])
        t["mem_curve"] = (c.get("gate") == "PASS" and ncase > 0,
                          "gate=%r cases=%d" % (c.get("gate"), ncase), [])

    # EPV 侧轨迹: 只有当 isco 归档里真的含 EPV 用例才算接上。
    isco_files = []
    idir = os.path.join(ROOT, "results", "isco")
    if os.path.isdir(idir):
        isco_files = [f for f in os.listdir(idir) if f.endswith(".json")]
    epv_in_isco = 0
    epv_red = []
    for f in isco_files:
        d = jget(os.path.join("results", "isco", f)) or {}
        blob = json.dumps(d, ensure_ascii=False)
        if not re.search(r"nn_bench|epv_selftest|nn_cifar|nn_net", blob):
            continue
        # 光"含 EPV 用例"不够：比出分歧的归档同样含 EPV 用例而表达的恰是"没接上"。
        # 判据读归档自己的判决，不读文件在不在。
        if d.get("suite") == "epv":
            n = d.get("n_tests") or 0
            good = bool(d.get("all_agree")) and d.get("n_agree_reg") == n                 and d.get("n_agree_store") == n and n > 0
            if not good:
                epv_red.append("epv.json 在盘但比对未通过 (reg %s/%s, store %s/%s)"
                               % (d.get("n_agree_reg"), n,
                                  d.get("n_agree_store"), n))
                continue
        epv_in_isco += 1
    t["epv_trace"] = (epv_in_isco > 0,
                      "isco 归档 %d 份, 其中 EPV 比对通过的 %d 份" % (len(isco_files),
                                                            epv_in_isco),
                      epv_red)

    # 上板: 步表里没有这一格就是没做 (判据跟着步表走, 不抄正文那句)。
    import run_all as RA
    names = [n for n, _f in list(RA.STEPS) + list(RA.POST_STEPS)]
    board = [n for n in names if "board" in n.lower() or u"上板" in n]
    t["board"] = (bool(board), "步表里板上相关步=%s" % board, [])
    t["_steps"] = (len(names), "run_all 步表长度", [])
    t["_pre"] = (len(RA.STEPS), "产证据那一档的格数", [])
    return t


def audit(docs, truth):
    """逐条规则扫四份交付件, 返回 (规则数, 判决句数, 命中列表)。"""
    problems = []
    n_rules = 0
    n_sent = 0
    for kind in DOC_KEYS:
        sents = sentences(docs[kind])
        n_sent += len(sents)
        for key, subs in SUBJECTS.items():
            landed, why, tokens = truth[key]
            n_rules += 1
            for s in sents:
                if not any(x in s for x in subs):
                    continue
                if landed:
                    if any(x in s for x in NEGATORS) and \
                       not any(x in s for x in CORRECTIONS):
                        problems.append(
                            "STALE [%s->%s] 已落地却被写成未做 (%s): %s"
                            % (kind, key, why, esc(s[:90])))
                else:
                    if any(x in s for x in AFFIRMATORS) and \
                       not any(x in s for x in CLAIM_WITHDRAWAL):
                        problems.append(
                            "OVERCLAIM [%s->%s] 未落地却被写成完成 (%s): %s"
                            % (kind, key, why, esc(s[:90])))
            # 已落地的头条数必须在每份交付件里出现 (PPT 漏了整块证据就是上一版的事)。
            # tokens 是"备选形状组"的列表: 一组之内的任一写法命中即算数 (理由见 truth_of)。
            if landed and tokens:
                for alts in tokens:
                    if not any(alt in docs[kind] for alt in alts):
                        problems.append(
                            "MISSING-NUMBER [%s->%s] 归档现算的 %s "
                            "在该交付件里一次都没有 (%s)"
                            % (kind, key, "|".join(alts), why))
    # 评审 P2-9：字数口径的「自指句」是把派生值抄进静态文本，下次复跑即过期且无判据核对。
    # 禁的不是讨论字数，是在交付件里抄一个具体超幅。
    SELF_REF = re.compile(u"(本节|该节|本文|这一节)[^。\\n]{0,16}"
                          u"(超|出档|超过)[^。\\n]{0,12}(字|上限)")
    n_rules += 1
    for kind in DOC_KEYS:
        for s in sentences(docs[kind]):
            if SELF_REF.search(s):
                problems.append(
                    "SELFREF [%s] 交付件里不许抄字数自指句 "
                    "(要数就现跑 tools/word_style_count.py): %s"
                    % (kind, esc(s[:90])))
    # 人名与队伍: 报告是唯一权威, PPT 是派生物。首页整行与封面分栏排版不同会假红,
    # 故按"名字粒度"逐条要求出现在 PPT：角色词显式排除, 其余每段都是必须能找到的名字。
    rep = docs["report"]
    fields = roster_from_report(rep)
    ROLE_WORDS = {u"牵头人", u"队员", u"指导教师", u"队长", u"兼", u"个人"}
    name_tokens = []
    for label, val in fields.items():
        if not val:
            problems.append("EMPTY-FIELD [report->roster] 首页 %s 为空" % esc(label))
        if u"待填" in val:
            problems.append("PLACEHOLDER [report->roster] 首页 %s 仍是占位符"
                            % esc(label))
        for tok in re.split(u"[（）()　\\s/、，,。;；:：]+", val):
            tok = tok.strip()
            if len(tok) >= 2 and tok not in ROLE_WORDS:
                name_tokens.append((label, tok))
    for label, tok in name_tokens:
        if tok not in docs["ppt"]:
            problems.append("ROSTER-DRIFT [ppt] 报告首页 %s 里的「%s」没出现在 PPT 里"
                            % (esc(label), esc(tok)))
    if not name_tokens:
        problems.append("EMPTY-FIELD [report->roster] 首页一个名字都没抽出来, "
                        "上面那条 PPT 比对是空转")
    # 步数: README/讲稿的「N 步」必须等于 run_all 步表长度。解析器一律调 run_all
    # 自己那份 —— 另写一条正则就是两条实现各判各的, 会假绿。
    want = truth["_steps"][0]
    import run_all as _RA
    for kind in ("readme", "demo"):
        for n, kindw in _RA.step_claims(docs[kind]):
            if kindw == u"全绿" and n != want:
                problems.append("STEP-DRIFT [%s] 写了 %d 步而步表是 %d 格" % (kind, n, want))
            if kindw == u"前" and n != truth["_pre"][0]:
                problems.append("STEP-DRIFT [%s] 写了前 %d 步而真值是前 %d"
                                % (kind, n, truth["_pre"][0]))
            if kindw == u"序" and not (1 <= n <= want):
                problems.append("STEP-DRIFT [%s] 引用了第 %d 步而步表只有 %d 格" % (kind, n, want))
    # 附图: 登记簿里每张图都要被正文引用, 正文引的每张图都要在盘上
    reg = jget(os.path.join("docs", chr(0x9644) + chr(0x56fe), "registry.json"))
    if reg is None:
        problems.append("NO-REGISTRY 附图登记簿缺失, 图面证据无人核")
    else:
        names = [e["file"] for e in reg.get("figures", [])]
        for f in names:
            if f not in docs["report"]:
                problems.append("ORPHAN-FIG 登记簿有 %s 而正文没引用" % f)
        for m in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", docs["report"]):
            base = os.path.basename(m.group(1))
            if base not in names:
                problems.append("UNREG-FIG 正文引了 %s 而登记簿没有它" % base)
        if len(names) < 8:
            problems.append("FIG-COUNT 登记簿只有 %d 张图 (本轮要求 CIFAR 与访存"
                            "两块新证据各一张)" % len(names))
    return n_rules, n_sent, problems


def roster_from_report(text):
    """从报告首页那三行抽四个字段 —— 报告是权威, 别处只许抄它。"""
    out = {}
    pat = re.compile(u"\\*\\*([^*]+)\\*\\*[:：](.*)")
    for m in pat.finditer(text[:2000]):
        label, val = m.group(1).strip(), m.group(2).strip()
        out[label] = val
    want = [u"参赛队伍名称", u"牵头人 / 队员 / 指导教师", u"参赛申报单位"]
    return dict((w, out.get(w, u"")) for w in want)


def esc(s):
    return s.encode("unicode_escape").decode("ascii") if isinstance(s, unicode_type) \
        else str(s)


try:
    unicode_type = unicode          # py2
except NameError:
    unicode_type = str


def load_all():
    return {
        "report": os.path.join(ROOT, "docs", u"参赛项目设计报告.md"),
        "readme": os.path.join(ROOT, "README.md"),
        "demo": os.path.join(ROOT, "demo", u"演示脚本与讲稿.md"),
        "ppt": os.path.join(ROOT, "docs", u"答辩PPT.pptx"),
    }


def run(docs_dir=None, ppt_path=None, corpus_override=None):
    """默认: 读真实交付件并判。docs_dir/ppt_path 只给 --selftest 的替身房用。"""
    paths = load_all()
    if corpus_override:
        paths = dict(paths)
        paths.update(corpus_override)
    truth = truth_of()
    docs = dict((k, read_doc(k, paths)) for k in DOC_KEYS)
    return audit(docs, truth) + (truth,)


# --------------------------------------------------------------------------
# 注入自检: 双向 A/B, 全部走替身文本, 不碰任何真实交付件与归档。
# --------------------------------------------------------------------------

REAL_CIFAR = u"真实数据集精度已落地"          # 合法: 带主语 + 更正词
SENTENCE_TPL = u"本包 %s 。"


def selftest():
    fails = []
    truth = truth_of()
    ok = truth["cifar10_acc"][0] and truth["cifar10_inloop"][0]
    if not ok:
        fails.append("SETUP: CIFAR 两条结论的现算真值为假 —— 注入前提不成立, %s / %s"
                     % (truth["cifar10_acc"][1], truth["cifar10_inloop"][1]))
    if not truth["mem_curve"][0]:
        fails.append("SETUP: 访存曲线真值为假: %s" % truth["mem_curve"][1])
    if truth["epv_trace"][0]:
        fails.append("SETUP: EPV 侧轨迹被判已落地, 本件的负向臂失效")
    if truth["board"][0]:
        fails.append("SETUP: 上板被判已落地, 本件的负向臂失效")

    base = dict((k, u"x") for k in DOC_KEYS)
    n_rules, n_sent, _p = audit(base, truth)
    if n_rules < len(SUBJECTS) * len(DOC_KEYS):
        fails.append("SHAPE: 规则数 %d 少于 主语集 x 交付件数" % n_rules)
    if n_sent == 0:
        fails.append("SHAPE: 判决句数为 0, 说明句子切分器没跑")

    # 正向夹具: 报告里放一条真数, 别让 MISSING-NUMBER 一直开火挡住别的判据
    nums = u" ".join(alt for grp in (truth["cifar10_acc"][2] + truth["cifar10_inloop"][2])
                     for alt in grp)
    roster_ok = (u"**参赛队伍名称**：等一个棋迹\n"
                 u"**牵头人 / 队员 / 指导教师**：陈思远　刘文娟\n"
                 u"**参赛申报单位**：武汉大学\n")

    def doc_with(body):
        d = dict((k, nums + u"\n" + roster_ok + body) for k in DOC_KEYS)
        return d

    # 1) 已落地却被写成未做 -> 必须开火, 且原因是 STALE
    _r, _s, p1 = audit(doc_with(u"CIFAR-10 的真实数据集精度还没有读数。"), truth)
    if not any("STALE" in x and "cifar10" in x for x in p1):
        fails.append("BLIND: 已落地的 CIFAR 被写成没做, 本门没开火")
    # 2) 同句带更正语境 -> 必须豁免 (否则"撤销过期声明"这句话自己会被打死)
    _r, _s, p2 = audit(doc_with(u"原先列在未做的真实数据集精度已落地。"), truth)
    if any("STALE" in x and "cifar10" in x for x in p2):
        fails.append("OVERFIRE: 带更正语境的合法句子被误杀: %s" % esc(str(p2[:1])))
    # 3) 更正词在**另一句** -> 不豁免 (证明语境窗口收到本句, 不是全文)
    _r, _s, p3 = audit(doc_with(u"这一项已落地。真实数据集精度没读数。"), truth)
    if not any("STALE" in x and "cifar10" in x for x in p3):
        fails.append("BLIND: 更正词在别处就豁免了本句, 窗口收到句子的判据没生效")
    # 4) 无主语的合法否定句 (讲合成网络) -> 不许开火
    _r, _s, p4 = audit(doc_with(u"这张网络的权重未训练，所以它不给精度。"), truth)
    # 断言要钉**规则 id 的括号形状**: 只查 "cifar10" 子串会把 ORPHAN-FIG 里那个
    # fig_g_cifar10_accuracy.png 也数进来 (本轮夹具真的这样报了一次红)。
    if any("->cifar10" in x for x in p4):
        fails.append("OVERFIRE: 讲合成网络 nn_net 的句子被 CIFAR 禁令误杀: %s"
                     % esc(str([x for x in p4 if "->cifar10" in x][:1])))
    # 5) 未落地被写成完成 -> 必须开火, 且原因是 OVERCLAIM
    _r, _s, p5 = audit(doc_with(u"EPV 侧轨迹与核已逐条一致。"), truth)
    if not any("OVERCLAIM" in x and "epv_trace" in x for x in p5):
        fails.append("BLIND: 未接线的 EPV 轨迹被写成已逐条一致, 本门没开火")
    _r, _s, p5b = audit(doc_with(u"本包已上板验证。"), truth)
    if not any("OVERCLAIM" in x and "board" in x for x in p5b):
        fails.append("BLIND: 无上板却写成已上板, 本门没开火")
    # 6) 占位符必须红, 填上必须绿
    d = dict((k, nums) for k in DOC_KEYS)
    _r, _s, p6 = audit(d, truth)
    if not any("EMPTY-FIELD" in x for x in p6):
        fails.append("BLIND: 首页字段缺失没被判红")
    _r, _s, p6b = audit(doc_with(u""), truth)
    if any("EMPTY-FIELD" in x or "PLACEHOLDER" in x for x in p6b):
        fails.append("OVERFIRE: 填好的首页仍被判空/占位")
    # 7) 步数漂移: 抄来的 N 步 != 步表长度必须红; 正确值必须不红
    want = truth["_steps"][0]
    _r, _s, p7 = audit(doc_with(u"本表 %d 步认证矩阵全绿。" % (want + 1)), truth)
    if not any("STEP-DRIFT" in x for x in p7):
        fails.append("BLIND: 抄错的步数 (真值+1) 没被判红")
    _r, _s, p7b = audit(doc_with(u"本表 %d 步认证矩阵全绿。" % want), truth)
    if any("STEP-DRIFT" in x for x in p7b):
        fails.append("OVERFIRE: 正确步数被判漂移")
    _r, _s, p7c = audit(doc_with(u"前 %d 步产证据。" % truth["_pre"][0]), truth)
    if any("STEP-DRIFT" in x for x in p7c):
        fails.append("OVERFIRE: 正确的前置步数被判漂移")
    _r, _s, p7d = audit(doc_with(u"前 %d 步产证据。" % (truth["_pre"][0] + 1)), truth)
    if not any("STEP-DRIFT" in x for x in p7d):
        fails.append("BLIND: 前置步数抄错 (真值+1) 没被判红")
    # 8) 孤儿图与未登记图
    _r, _s, p8 = audit(doc_with(u""), truth)
    if not any("ORPHAN-FIG" in x or "NO-REGISTRY" in x for x in p8):
        fails.append("BLIND: 登记簿里的图没被正文引用, 本门没开火")
    # 9) 头条数缺席 (PPT 整块证据漏掉就是这一条)
    d = doc_with(u"")
    stripped = d["ppt"]
    for alt in truth["cifar10_acc"][2][0]:      # 抹掉**全部**备选形状, 只抹一种会假绿
        stripped = stripped.replace(alt, u"")
    d["ppt"] = stripped
    _r, _s, p9 = audit(d, truth)
    if not any("MISSING-NUMBER" in x and "ppt" in x for x in p9):
        fails.append("BLIND: PPT 漏了归档现算的头条数, 本门没开火")
    # P2-9 双向：自指句必须被拦；只是"讨论字数口径达标"的合法句子不许被误杀。
    _r, _s, pself = audit(doc_with(u"本节按 Word 口径超上限 756 字。"), truth)
    if not any("SELFREF" in x for x in pself):
        fails.append("BLIND: 字数自指句没被拦住 —— P2-9 只是被识别，没被挡住")
    else:
        print("[fixture] selfref-fire     拦下 1 条 (示例: %s)"
              % esc(pself[0][:70]))
    _r, _s, pok = audit(doc_with(u"字数口径在两个口径下同时达标，读数见附件 A.6。"),
                        truth)
    if any("SELFREF" in x for x in pok):
        fails.append("OVERFIRE: 合规的口径陈述被自指禁令误杀: %s"
                     % esc(str(pok[:1])[:90]))
    else:
        print("[fixture] selfref-exempt   合法口径句放行")
    return fails, n_rules, n_sent, truth


def main():
    ascii_out = sys.stdout
    if "--selftest" in sys.argv:
        fails, n_rules, n_sent, truth = selftest()
        for f in fails:
            print("FIXTURE-FAIL " + esc(f))
        print("FIXTURES rules=%d sentences=%d failed=%d"
              % (n_rules, n_sent, len(fails)))
        if fails:
            print("CONSISTENCY_SELFTEST_FAILED")
            return 1
        print("CONSISTENCY_SELFTEST_PASS")
        return 0

    try:
        n_rules, n_sent, problems, truth = run()
    except Exception as exc:                      # 缺料即死, 不许当跳过
        print("SETUP-ERROR " + esc("%r" % (exc,)))
        print("CONSISTENCY_FAILED rules=0 docs=0 reason=missing-material")
        print(esc("CONSISTENCY-HINT 这条判据的对照对象是渲染件里的文字；渲染件按"
                  "交付形态决定不随包。要跑这一门：先 python flow/build_docx.py 与 "
                  "python flow/build_ppt.py（本轮实测：重跑能长出同尺寸的渲染件，"
                  "字节因容器内嵌时刻而不同）"))
        return 1
    for p in problems:
        print("PROBLEM " + esc(p))
    n_doc = len(DOC_KEYS)
    if n_rules == 0 or n_doc == 0 or n_sent == 0:
        print("CONSISTENCY_FAILED rules=%d docs=%d sentences=%d reason=empty-denominator"
              % (n_rules, n_doc, n_sent))
        return 1
    if problems:
        print("CONSISTENCY_FAILED rules=%d docs=%d sentences=%d problems=%d"
              % (n_rules, n_doc, n_sent, len(problems)))
        return 1
    print("CONSISTENCY_PASS rules=%d docs=%d sentences=%d problems=0"
          % (n_rules, n_doc, n_sent))
    return 0


if __name__ == "__main__":
    sys.exit(main())
