#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/verify_ppt.py -- 反向门: 答辩 PPT 里出现的每个数字都必须能回查出处。

PPT 是最容易被手动改一个数的交付件(它不在任何生成链上, 且决赛现场才改)。
本门把 .pptx **重新打开**读文本, 逐 token 定档:

  A 测量值   token 出现在 results/**/*.json 的数值/字符串里(含 abs/%g/%.1f~3/
             千分位/十六进制几种印法) -> 记下它来自哪个文件
  B 派生值   DERIVED 表里给了操作数路径, 现场重算再按 PPT 的打印位比对
  C 历史读数 修复前调试时留下的读数(退休轨迹逐位相同那一段), 原始归档已被
             后续运行覆盖 -> 必须同时出现在设计报告的 5.3 节里(报告↔PPT 一致)
  D 结构量   架构参数/赛题规定/讲稿计时, 逐条给理由

任何一档都落不进的 token = 报红, 并说清它出现在第几页、原文是哪句。
--selftest 用替身 pptx 证明 A/C/D 三档真的会咬(改一个测量数、删一处报告里
没有的历史数、加一个没理由的新数字), 并跑真实 deck 作正对照。
"""
import io
import json
import os
import re
import sys
import tempfile
import shutil
import hashlib

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.dont_write_bytecode = True   # 本件会 import flow/run_all.py, 不许长出 __pycache__

from pptx import Presentation

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "flow"))
from atomic_json import write_json_atomic      # 归档落盘的唯一实现
DECK = os.path.join("docs", u"答辩PPT.pptx")
REPORT = os.path.join("docs", u"参赛项目设计报告.md")
RESULTS = os.path.join("results")
TOKEN = re.compile(r"\d[\d,]*\.?\d*")

# B 档: 现场重算的派生量。每个条目 = PPT 里的 token -> (说明, 取数函数)
def _bench():
    return json.load(io.open(os.path.join(ROOT, "results", "bench", "nn_bench.json"),
                             encoding="utf-8"))


def _pnr():
    """器件口径归档：走 run_all.pnr_load 的同一把尺子（只认对应当前 RTL 的那份）。

    这里曾经直接写死 summary.json —— 于是门禁的三条派生值（净墙钟加速、指令数比、
    Fmax 降幅）是拿**过期归档**算出来的，deck 里的 11.36 / 27.78 被"验算通过"，
    而它们对应的 8230 / 28.445 早已被 M7 之后的复跑取代。验收端与生成端读同一份
    过期数据时，两边一起错是查不出来的。
    """
    return RUN.pnr_load()


def isco_total_programs():
    """两套件用例总数：deck 上那句「这 50 个程序」的分母。"""
    return "%d" % (_compliance("rv32ui")["n_tests"] + _compliance("rv32um")["n_tests"])


def _compliance(suite):
    return json.load(io.open(os.path.join(ROOT, "results", "compliance",
                                          suite + ".json"), encoding="utf-8"))


def net_wallclock_x():
    """PPT/报告里的「净墙钟加速」= 周期加速 x 布线后频率比。"""
    b, p = _bench(), _pnr()
    par = p["comparison_same_platform"]["post_place_and_route_report_area"]
    return "%.2f" % (b["speedup"] * par["epv"]["fmax_mhz"] / par["baseline"]["fmax_mhz"])


def inst_ratio_x():
    """指令数比: 归档存的是 x1000 的整数, PPT 印成一位小数。"""
    b = _bench()
    return "%.1f" % (b["inst_ratio_x1000"] / 1000.0)


def fmax_drop_pct():
    p = _pnr()
    par = p["comparison_same_platform"]["post_place_and_route_report_area"]
    return "%.2f" % abs(par["delta"]["fmax_pct"])


def epv_trace_seen():
    """整程序黄金 ISS 双投影逐条比对真跑到的 EPV 指令条数。
    真源 = flow/iss_compare_epv.py 写进 results/isco_probe/epv.json 的
    epv_instructions_seen / _total / epv_never_compared 三个字段。"""    
    d = json.load(io.open(os.path.join(ROOT, "results", "isco_probe", "epv.json"),
                            encoding="utf-8"))
    seen, total = d["epv_instructions_seen"], d["epv_instructions_total"]
    never = len(d["epv_never_compared"])
    if seen + never != total:
        raise AssertionError("epv.json 自相矛盾: seen=%s never=%s total=%s" % (
            seen, never, total))
    return str(seen)


def epv_trace_missing():
    d = json.load(io.open(os.path.join(ROOT, "results", "isco_probe", "epv.json"),
                            encoding="utf-8"))
    return str(len(d["epv_never_compared"]))


DERIVED = [
    (u"EPV 整程序轨迹已比对条数", epv_trace_seen),
    (u"EPV 未进整程序轨迹条数", epv_trace_missing),
    (u"净墙钟加速", net_wallclock_x),
    (u"指令数比", inst_ratio_x),
    (u"布线后 Fmax 降幅", fmax_drop_pct),
    (u"两套件用例总数", isco_total_programs),
]

# D 档: 结构量 / 赛题规定 / 讲稿计时 —— 每条必须有理由, 空理由不算理由。
STRUCTURAL = {
    "0": "讲稿分页与编号里出现的零",
    "1": "编号 1)", "2": "编号 2)", "3": "3x3 卷积核 / 编号 3)",
    "4": "rv32ui 到 rv32um 之外的编号 4)", "5": "五级流水(架构参数)",
    "6": "编号 6)", "7": "编号 7)", "8": "8 个向量寄存器 / 16 lane 的 8",
    "9": "编号 9)", "10": "Demo 视频 <=10 分钟(赛题规定)",
    "11": "5.3 节第 11 条缺陷编号", "12": "5.3 节第 12 条缺陷编号",
    "13": "5.3 节第 13 条缺陷编号", "14": "5.3 节 14 条缺陷 / 编号",
    "15": "编号", "16": "16 输入通道 / 16 lane", "17": "EPV 指令条数",
    "18": "页码", "19": "页数", "20": "答辩 20 分钟(赛题规定)",
    "30": "评分项 30 分(技术难度与实现完整度)", "31": "S 域 5 bit 上限 31",
    "32": "rv32 / int32 lane 位宽", "40": "讲稿计时: 开场 40 秒",
    "64": "int64 中间量 / 器件 BRAM 计数", "90": "评分项 90 分制残段",
    "128": "128bit 向量寄存器", "256": "主存 256 KiB",
    "42": "rv32ui 用例总数分母(与归档一致)",
}

# C 档: 修复前调试时的历史读数 —— 原始归档已被修复后的复跑覆盖, 无法再生成。
# 这是一个**封闭清单**(不是"凡是报告里有的数都算有据"): 每条都要写下来由谁记,
# 并且额外要求它同时出现在设计报告正文里 —— 两份交付件不许各说各话。
HISTORICAL = {
    "80000028": "缺陷#2 当时的 mepc",
    "2405": "缺陷#11 排查时三条退休轨迹的指令数",
    "3481": "缺陷#11 排查时三条退休轨迹的周期数",
}


# A 档语料 = **显式列出的测量归档**。不许按目录 glob:
# 同目录里躺着本门自己写的 results/ppt/verify.json、报告的字数计数
# results/word_limits.json、矩阵 results/matrix.json 与给人看的 results/demo/ 拷贝
# —— 把它们的数收进"允许值集合", 就等于让 PPT 里一个从未测过的数也能蒙绿
# (循环允许集)。排除必须是显式的, 并且下面用一条断言证明排除真的在排除。
UNIVERSE_DIRS = ("results/bench", "results/synth", "results/compliance",
                 "results/iss", "results/pnr", "results/sim")
UNIVERSE_SKIP = ("results/sim/epv_bisect_wave.json",)   # 截断运行, 不是认证读数
# 逐测试的运行归档（rv32ui__bltu.json 与它们的 _isco 兄弟）也不算可引用语料：
# 它们有 100+ 份，每份里都躺着一些偶然的小整数。实测：某次复跑之后
# rv32ui__bltu.json 里出现了一个独立的 "40"，于是 deck 上那个作为结构量豁免的
# "40" 被 A 档认领导了 —— 「删掉豁免理由门必须报红」那条注入当场变绿。
# 允许值集合按目录 glob，就等于让任何一个从未测过的数都有机会撞进语料。
PER_TEST_RUN = re.compile(r"^results/sim/rv32(ui|um)__[a-z0-9_]+(_isco)?\.json$")
# 这两类归档必须归因到一版 RTL；缺 rtl_sha256 = 无法归因 = 不进语料。
UNATTRIBUTABLE_DIRS = ("results/pnr/", "results/sim/")

# 语料准入的第三条规则：**带 rtl_sha256 的归档必须与当前 RTL 同源**。
# 起因是实测到的东西：deck 里印着 8230 / 28.445 / 1237 / 39.387 —— 全是 M7 之前
# 那一版 RTL 的器件读数，出处是 results/pnr/summary.json（已被 summary2.json 取代）。
# 门禁当时全绿，因为它的语料是按目录 glob 的：过期归档自己带着那些数，等于
# 「过期数字由过期归档作证」。现在过期归档不进语料，引它的数就判无出处。
import run_all as RUN          # rtl_hashes() 是 RTL 指纹的唯一实现（不另写一份）


def clean_tokens(xs):
    """只留「干净的数」：分词碎片（'0.'、'10,'）与结构量豁免表都剔掉。
    注入夹具要挑能原样往返的 token，否则检的是分词而不是判据。"""
    return [x for x in sorted(xs) if re.match(r"^\d+(\.\d+)?$", x)
            and x not in STRUCTURAL]


def stale_by_rtl(rel):
    """归档带 rtl_sha256 且不等于当前工作树 -> True（不许当出处）。

    器件口径与运行归档这两类**必须**能归因到一版 RTL：没有 rtl_sha256 就等于
    无法归因，同样不许进语料。实测：results/pnr/summary.json（M7 之前那版）
    根本没有这个键，第一版规则因此放过了它，deck 里的 8230/28.445 照样"有档可归"。
    """
    p = os.path.join(ROOT, *rel.split("/"))
    try:
        d = json.loads(io.open(p, encoding="utf-8").read())
    except Exception:
        return False                       # 读不出交给别的判据，这里不越权
    got = d.get("rtl_sha256")
    if not isinstance(got, dict) or not got:
        return rel.startswith(UNATTRIBUTABLE_DIRS)
    return got != RUN.rtl_hashes()


def excluded_stale():
    return [rel for rel in per_test_paths() + [
            ("results/pnr/" + f) for f in sorted(os.listdir(os.path.join(ROOT, "results", "pnr")))
            if f.endswith(".json")] if stale_by_rtl(rel)]


def per_test_paths():
    return [rel for rel in (
        "results/sim/" + f for f in sorted(
            os.listdir(os.path.join(ROOT, "results", "sim")))
        if f.endswith(".json")) if PER_TEST_RUN.match(rel)]


def universe_paths():
    out = []
    for d in UNIVERSE_DIRS:
        full = os.path.join(ROOT, *d.split("/"))
        if not os.path.isdir(full):
            continue
        for fn in sorted(os.listdir(full)):
            if not fn.endswith(".json"):
                continue
            rel = (d + "/" + fn).replace(os.sep, "/")
            if rel in UNIVERSE_SKIP or PER_TEST_RUN.match(rel):
                continue
            if stale_by_rtl(rel):
                continue
            out.append(rel)
    return out


def excluded_numbers():
    """返回只存在于**被排除**归档里的数(用来证明排除不是装饰)。"""
    uni = set(arch_tokens())
    uniq = set()
    for rel in ("results/matrix.json", "results/word_limits.json",
                "results/ppt/verify.json"):
        p = os.path.join(ROOT, *rel.split("/"))
        if not os.path.isfile(p):
            continue
        blob = io.open(p, encoding="utf-8").read()
        for m in TOKEN.findall(blob):
            if m not in uni:
                uniq.add(m)
    return uniq


def arch_tokens():
    """A 档语料: universe_paths() 里那些**测量归档**的数, 连同它们可能被印成的样子。"""
    uni = {}
    def add(tok, where):
        uni.setdefault(tok.replace(",", ""), where)
        uni.setdefault(tok, where)

    def walk(o, where):
        if isinstance(o, dict):
            for v in o.values():
                walk(v, where)
        elif isinstance(o, list):
            for v in o:
                walk(v, where)
        elif isinstance(o, bool):
            return
        elif isinstance(o, (int, float)):
            for s in (str(o), "%g" % o, "%.1f" % o, "%.2f" % o, "%.3f" % o,
                      "%d" % abs(o) if float(o).is_integer() else "%.2f" % abs(o),
                      format(int(o), ",") if float(o).is_integer() else str(o)):
                add(s, where)
        elif isinstance(o, str):
            for m in TOKEN.findall(o):
                add(m, where)
                add(m.replace(",", ""), where)
            for m in re.findall(r"0x([0-9a-fA-F]+)", o):
                add(str(int(m, 16)), where)
                add(m.lstrip("0") or "0", where)

    for rel in universe_paths():
        p = os.path.join(ROOT, *rel.split("/"))
        if not os.path.isfile(p):
            continue                      # 缺哪份归档由矩阵那一步负责报, 本门不越界
        try:
            with io.open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        walk(data, rel)
    return uni


def deck_tokens(path):
    """打开 .pptx, 返回 [(slide, token, 原文行)] —— 只读文本, 不改文件。"""
    prs = Presentation(path)
    out = []
    for i, s in enumerate(prs.slides, 1):
        texts = []
        for sh in s.shapes:
            if sh.has_text_frame:
                for p in sh.text_frame.paragraphs:
                    t = "".join(r.text for r in p.runs)
                    if t.strip():
                        texts.append(t)
        if s.has_notes_slide:
            n = s.notes_slide.notes_text_frame.text
            if n.strip():
                texts.append(n)
        for t in texts:
            for m in TOKEN.finditer(t):
                out.append((i, m.group(0), t.strip()[:110]))
    return out


def classify(tokens, uni, report_text):
    """每条 (slide, token, line) -> (档位, 说明)。落不进任何档 -> ('?', ...)"""
    derived = {}
    for label, fn in DERIVED:
        derived[fn()] = label
    rows = []
    for slide, tok, line in tokens:
        key = tok.replace(",", "")
        if key in uni:
            rows.append((slide, tok, line, "A", uni[key]))
        elif key in derived:
            rows.append((slide, tok, line, "B", derived[key]))
        elif tok in STRUCTURAL:
            rows.append((slide, tok, line, "D", STRUCTURAL[tok]))
        elif key in HISTORICAL and key in report_text.replace(",", ""):
            rows.append((slide, tok, line, "C", HISTORICAL[key]))
        elif key in HISTORICAL:
            rows.append((slide, tok, line, "?",
                         "列在历史读数清单里, 但正文与附件 A 都没有它 —— 交付件之间不一致"))
        else:
            rows.append((slide, tok, line, "?", "无出处"))
    return rows


def corpus_text(report=None):
    """C 档「历史读数」允许落在的**交付文案**集合。

    M9 之后 5.3 的缺陷全表按赛题 4.1.5 三搬到了附件 A, 所以历史读数的落点不再
    只有正文一份。但判据的强度一点没降: token 必须出现在**某一门交付给人读的
    文档**里, 而且显式传 `report=` 的注入用例只看那一个文件 —— 否则「把报告里
    那条历史读数删掉」这条注入会因为附件里还在而变成空跑。
    """
    if report:
        return io.open(report, encoding="utf-8").read()
    parts = [io.open(os.path.join(ROOT, REPORT), encoding="utf-8").read()]
    dd = os.path.join(ROOT, "docs")
    for fn in sorted(os.listdir(dd)):
        if fn.endswith(".md") and fn.startswith(u"附件"):
            parts.append(io.open(os.path.join(dd, fn), encoding="utf-8").read())
    return "\n".join(parts)


def picture_count(deck):
    """数 deck 里真有多少张嵌入图 —— 「PPT 0 张图片」是评审记过的第 6 条,
    所以这条必须是个会咬的计数, 不是「我记得加了」。"""
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    prs = Presentation(deck)
    n = 0
    for s in prs.slides:
        for sh in s.shapes:
            if sh.shape_type == MSO_SHAPE_TYPE.PICTURE:
                n += 1
    return n


def registered_figures():
    p = os.path.join(ROOT, "docs", u"附图", "registry.json")
    if not os.path.isfile(p):
        return None
    with io.open(p, encoding="utf-8") as fh:
        return len(json.load(fh)["figures"])


def _layout_of(flags):
    """由"每页是不是图"这一列标志算排布形状（拆出来才能被合成反例喂到）。"""
    run = mx = 0
    for f in flags:
        run = run + 1 if f else 0
        mx = max(mx, run)
    return {"figure_pages": sum(flags), "longest_run": mx,
            "tail_is_figure": bool(flags[-1:]) and bool(flags[-1])}


def figure_layout(deck):
    """图在 deck 里的排布：页数、最长连排、最后一页是不是图。

    为什么独立再数一遍：写入端 build_ppt 搬完图会自查，但「八页图堆成尾部附录」
    这条是评审记过的形状，它得在**读端**也可反驳 —— 有人手工动过 pptx 时，
    只有这里的数字会掉下来。
    """
    prs = Presentation(deck)
    flags = []
    for s in prs.slides:
        t = u""
        for sh in s.shapes:
            if sh.has_text_frame and sh.text_frame.text.strip():
                t = sh.text_frame.text.strip()
                break
        flags.append(bool(re.match(r"^[A-H]\s*·", t)))
    return _layout_of(flags)


def check(deck=None, report=None):
    deck = deck or os.path.join(ROOT, DECK)
    report_text = corpus_text(report)
    uni = arch_tokens()
    rows = classify(deck_tokens(deck), uni, report_text)
    bad = [r for r in rows if r[3] == "?"]
    npic, nreg = picture_count(deck), registered_figures()
    if nreg is not None:
        if npic != nreg:
            bad.append(("PICTURE_COUNT", "deck 嵌了 %d 张图, 附图登记簿有 %d 张"
                        % (npic, nreg)))
        if npic == 0:
            bad.append(("PICTURE_COUNT", "deck 一张图都没有"))
    lay = figure_layout(deck)
    if lay["figure_pages"] != npic:
        bad.append(("FIGURE_LAYOUT", "按形状数到 %d 页图, 按 shape 数到 %d 页"
                    % (lay["figure_pages"], npic)))
    if lay["longest_run"] > 2:
        bad.append(("FIGURE_LAYOUT", "%d 页图连排成一段附录（要图随文走，"
                    "同锚点的一对最多 2 页）" % lay["longest_run"]))
    if lay["tail_is_figure"]:
        bad.append(("FIGURE_LAYOUT", "最后一页还是图：收尾页没被留在最后"))
    return rows, bad


def report_out(rows, bad):
    tally = {}
    for r in rows:
        tally[r[3]] = tally.get(r[3], 0) + 1
    print("  tokens=%d 档位分布 %s" % (len(rows), dict(sorted(tally.items()))))
    for slide, tok, line, tier, why in rows:
        if tier == "?":
            print("  PPT_UNTRACED slide %d token %r in %r -> %s" % (slide, tok, line, why))
    if bad:
        print("PPT_VERIFY_FAIL: %d 个数字没有出处" % len(bad))
        return 1
    print("PPT_VERIFY_OK: 每个数字都能回查到归档 / 现算派生 / 报告一致性 / 结构量理由")
    return 0


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return h.hexdigest()


def selftest():
    real = [os.path.join(ROOT, DECK), os.path.join(ROOT, REPORT)]
    if not all(os.path.isfile(p) for p in real):
        raise SystemExit("PPT_SELFTEST_NO_BASELINE: 缺 deck 或报告, 先跑 flow/build_ppt.py")
    before = dict((p, sha(p)) for p in real)
    rows, bad = check()
    if bad:
        for r in bad:
            print("  真实 deck 已报红: %r (%s)" % (r[1], r[2]))
        print("PPT_SELFTEST_BLOCKED: 正对照不过, 注入无法判读")
        return 1
    print("[selftest] 正对照: 真实 deck %d 个数字全部有档可归" % len(rows))

    fails = []
    tmp = tempfile.mkdtemp(prefix="pptverify_")
    try:
        # 1) 改一个 A 档测量数 -> 必红, 且报出它所在那一页
        work = os.path.join(tmp, "tampered.pptx")
        shutil.copyfile(real[0], work)
        prs = Presentation(work)
        measured = [(s, t) for s, t, l, k, w in rows if k == "A" and len(t) >= 4]
        slide, tok = measured[0]
        hit = 0
        for s in prs.slides:
            for sh in s.shapes:
                if not sh.has_text_frame:
                    continue
                for p in sh.text_frame.paragraphs:
                    for r in p.runs:
                        if tok in r.text:
                            r.text = r.text.replace(tok, tok[:-1] + ("7" if tok[-1] != "7" else "3"), 1)
                            hit += 1
        prs.save(work)
        if hit == 0:
            fails.append("tamper-A: 没替换到任何 run, 注入是空跑")
        else:
            _, b = check(work)
            if not b:
                fails.append("tamper-A: 改了测量数门不红")
            else:
                print("[selftest] tamper-A            红 -> slide %d %r" % (b[0][0], b[0][1]))

        # 2) 造一个报告里也不存在的长数字冒充历史读数 -> 必红
        work2 = os.path.join(tmp, "ghost.pptx")
        shutil.copyfile(real[0], work2)
        prs = Presentation(work2)
        s = prs.slides[0]
        tf = [sh for sh in s.shapes if sh.has_text_frame][-1].text_frame
        p = tf.add_paragraph()
        p.text = u"编一个不存在的读数 987654321"
        prs.save(work2)
        _, b = check(work2)
        if not any(x[1] == "987654321" for x in b):
            fails.append("ghost-number: 新造的无出处数字没被判红 (%s)" % ([x[1] for x in b],))
        else:
            print("[selftest] ghost-number        红 -> 987654321 无出处")

        # 3) 结构量白名单不许"白放"：整张 D 档清空之后，仍然被放过的
        #    structural token 必须各有 A/B/C 出处；判红的那些就是 D 真正在管的。
        #    原来这里写死一个 token（"40"）当受害者，注释还专门解释过为什么不能
        #    挑 128（它同时被 A 档命中）。但写死任何值都会随语料长大而静默失效：
        #    本轮把证据表搬进附件之后，C 档（正文+附件）把 30 条结构量理由全遮蔽了，
        #    那条注入当场判红。现在改成不依赖具体值，并把**当前处在哪一档**印出来：
        #      - D 在管若干条 => 白名单承重，逐条可反驳；
        #      - D 一条都不管 => 白名单冗余（所有 structural token 另有出处），
        #        这是合法状态，但必须说出来，不许假装它还在承重。
        keep = dict(STRUCTURAL)
        try:
            _, base_bad = check()
            base_set = set(x[1] for x in base_bad)
            STRUCTURAL.clear()
            _, noD_bad = check()
        finally:
            STRUCTURAL.clear()
            STRUCTURAL.update(keep)
        carried = sorted(set(x[1] for x in noD_bad) - base_set)
        strays = [t for t in carried if t not in keep]
        if strays:
            fails.append("allowlist-audit: D 档清掉后有 %s 判红却不在白名单里"
                         " —— 白名单与它声称管的对象不一致" % strays[:5])
        elif carried:
            print("[selftest] allowlist-removed   红 -> D 档在管 %d 个 token "
                  "(%s…)；删白名单即失据" % (len(carried), ",".join(carried[:4])))
        else:
            print("[selftest] allowlist-redundant D 档当前不单独支撑任何 token "
                  "(%d 条理由全部另有 A/B/C 出处) —— 冗余但不是白放" % len(keep))
        # 4) 派生量: 破坏归档之外的路 —— 把 DERIVED 的一个函数结果改掉, 门必须
        #    不再放过那个 token(证明 B 档是真的在重算而不是白名单)
        keep_d = list(DERIVED)
        DERIVED[:] = [(u"净墙钟加速", lambda: "0.00"),
                      (u"指令数比", inst_ratio_x), (u"布线后 Fmax 降幅", fmax_drop_pct)]
        _, b = check()
        if not any(x[1] == net_wallclock_x() for x in b):
            fails.append("derived-stale: 派生函数给出别的值时门没红 —— B 档其实是白名单")
        else:
            print("[selftest] derived-stale       红 -> %s 现算对不上即判无出处"
                  % net_wallclock_x())
        DERIVED[:] = keep_d

        # 5) 把报告里那条历史读数删掉 -> deck 里对应的 C 档 token 必须变红
        #    (证明 C 档不是"随便给个历史名头就放行", 它真的在核两份交付件一致)
        rep2 = os.path.join(tmp, "report_no_hist.md")
        rt = corpus_text()          # C 档读的是「正文 + 附件」整个集合
        stripped = rt.replace("3481", "四位数")
        if stripped == rt:
            fails.append("hist-desync: 正文与附件里都找不到 3481, 注入是空跑")
        else:
            io.open(rep2, "w", encoding="utf-8").write(stripped)
            _, b = check(report=rep2)
            if not any(x[1] == "3481" for x in b):
                fails.append("hist-desync: 报告删掉该读数后, deck 里的 3481 仍被放过")
            else:
                print("[selftest] hist-desync         红 -> 报告一删, deck 里的 3481 即失据")
        # 6) 排除必须真的在排除: 被挡在语料之外的归档(matrix / word_limits / 本门
        #    自己写的 verify.json)里, 必须存在「A 档认不出来」的数。若哪天有人把
        #    这些目录塞回语料, 允许集就会自食其数(PPT 抄一个从没测过的数也绿)。
        excl = excluded_numbers()
        if not excl:
            fails.append("exclusion-vacuous: 被挡在语料外的归档里没有一个数是 A 档不认的"
                         " —— 排除形同虚设, 或者语料其实把它们收进来了")
        else:
            # 取样要挑「干净的数」：excluded_numbers() 里混着 "0,"、"10." 这类被
            # 分词器带上下标/标点的碎片，塞进 deck 之后又被重新切成别的 token，
            # 于是"判无出处"这条断言检的不是排除而是分词。早先 sorted()[0] 恰好
            # 取到 "0.3" 蒙对了，收紧语料之后它取到 "0," 就变成一条假红。
            clean = clean_tokens(excl)
            if not clean:
                fails.append("exclusion-vacuous: 被排除归档里没有一个是干净可注入的数"
                             " —— 无法证明排除有效 (%d 个候选全被分词碎片或豁免表吃掉)"
                             % len(excl))
                sample = None
            else:
                sample = clean[0]
        if sample:
            work = os.path.join(tmp, "excl-probe.pptx")
            shutil.copyfile(real[0], work)
            prs2 = Presentation(work)
            tfs = [sh for sh in prs2.slides[0].shapes if sh.has_text_frame]
            tfs[-1].text_frame.add_paragraph().text = \
                u"抄一个只出现在被排除归档里的数 %s" % sample
            prs2.save(work)
            _, b = check(work)
            if not any(x[1] == sample for x in b):
                fails.append("exclusion-vacuous: 数 %s 来自被排除归档却被放过" % sample)
            else:
                print("[selftest] %-20s 被排除归档里的 %s 判无出处 -> 排除有效"
                      % ("exclusion-real", sample))

        # 3c) 逐测试运行归档必须留在可引用语料之外。这条注入演的是本轮真实事故：
        #     一次复跑之后 rv32ui__bltu.json 里出现独立的 "40"，deck 上那个作为
        #     结构量豁免的 "40" 被 A 档认领，「删掉豁免理由门必须报红」当场变绿。
        pt = per_test_paths()
        if not pt:
            fails.append("per-test-glob: PER_TEST_RUN 一份归档都没匹配上 —— "
                         "要么文件改名了, 要么这条排除已经成死码")
        else:
            uni2 = set(arch_tokens())
            only = set()
            for rel in pt:
                fp = os.path.join(ROOT, *rel.split("/"))
                blob = io.open(fp, encoding="utf-8", errors="replace").read()
                for m in TOKEN.findall(blob.replace(",", "")):
                    if m not in uni2:
                        only.add(m)
            if not only:
                fails.append("per-test-vacuous: %d 份逐测试归档里没有一个数是 A 档与"
                             "豁免表都不认的 —— 无法证明这条排除有效" % len(pt))
            else:
                s2 = clean_tokens(only)[0]
                work2 = os.path.join(tmp, "pertest-probe.pptx")
                shutil.copyfile(real[0], work2)
                prs3 = Presentation(work2)
                tfs3 = [sh for sh in prs3.slides[0].shapes if sh.has_text_frame]
                tfs3[-1].text_frame.add_paragraph().text = \
                    u"抄一个只在逐测试运行归档里出现的数 %s" % s2
                prs3.save(work2)
                _, b3 = check(work2)
                if not any(x[1] == s2 for x in b3):
                    fails.append("per-test-leak: %s 来自被排除的逐测试归档却被放过" % s2)
                else:
                    print("[selftest] %-20s %d 份逐测试归档被排除, 其中的 %s 判无出处"
                          % ("per-test-real", len(pt), s2))

        # 3d) 过期归档（rtl_sha256 不等于当前工作树）不许当出处。
        #     本轮真实事故：deck 印着上一版 RTL 的 8230/28.445，而门禁因为
        #     按目录 glob 收着那份过期归档，于是"过期数字有过期归档作证"全绿。
        st = [rel for rel in ("results/pnr/summary.json",
                              "results/sim/misalign_bisect_pre_m7.json")
              if os.path.isfile(os.path.join(ROOT, *rel.split("/"))) and stale_by_rtl(rel)]
        if not st:
            fails.append("stale-rule-dead: 没有任何一份归档被判过期 —— RTL 哈希比对"
                         "没在起作用, 或者过期归档被删了(那这条判据就没人喂过)")
        else:
            uni3 = set(arch_tokens())
            only3 = set()
            for rel in st:
                blob = io.open(os.path.join(ROOT, *rel.split("/")),
                               encoding="utf-8", errors="replace").read()
                for m in TOKEN.findall(blob.replace(",", "")):
                    if m not in uni3:
                        only3.add(m)
            if not only3:
                fails.append("stale-vacuous: %s 被排除, 但里面没有一个数是语料不认的"
                             % st)
            else:
                s3 = sorted(clean_tokens(only3), key=lambda x: (-len(x), x))[0]
                work3 = os.path.join(tmp, "stale-probe.pptx")
                shutil.copyfile(real[0], work3)
                prs4 = Presentation(work3)
                tfs4 = [sh for sh in prs4.slides[0].shapes if sh.has_text_frame]
                tfs4[-1].text_frame.add_paragraph().text = \
                    u"抄一个只在过期归档里出现的数 %s" % s3
                prs4.save(work3)
                _, b4 = check(work3)
                if not any(x[1] == s3 for x in b4):
                    fails.append("stale-leak: %s 来自过期归档 %s 却被放过" % (s3, st))
                else:
                    print("[selftest] %-20s %d 份过期归档被排除, 其中的 %s 判无出处"
                          % ("stale-real", len(st), s3))
        # 9) 「图随文走」这把尺子的两向对照：真实 deck 必须量出来不像附录，
        #    而把形状改回"末尾连排八页"必须被同一条规则判出来（否则这条判据是装饰）。
        lay = figure_layout(os.path.join(ROOT, DECK))
        if lay["figure_pages"] != picture_count(os.path.join(ROOT, DECK)):
            fails.append("layout-shape: 两把数图的尺子不等 %r" % lay)
        if lay["longest_run"] > 2 or lay["tail_is_figure"]:
            fails.append("layout-positive: 真实 deck 仍像尾部附录 %r" % lay)
        back = _layout_of([False] * 21 + [True] * 8 + [False])
        if back["longest_run"] <= 2 or back["figure_pages"] != 8:
            fails.append("layout-negative: 搬回附录的形状没被判出来 %r" % back)
        else:
            print("[selftest] %-20s 真实 deck 最长连排 %d 页 / 附录反例 %d 页"
                  % ("figure-layout", lay["longest_run"], back["longest_run"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    after = dict((p, sha(p)) for p in real)
    if after != before:
        fails.append("自检改动了真实 deck/报告")
    else:
        print("[selftest] 真实文件哈希前后一致, 注入只落在临时副本")
    if fails:
        print("PPT_SELFTEST_FAILED: %d 条" % len(fails))
        for f in fails:
            print("   " + f)
        return 1
    print("PPT_SELFTEST_OK: 上面每一条注入都各自报出了原因（条数不在此复述，"
          "避免又一个由运行长度决定的数被抄进消息里）")
    return 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    rows, bad = check()
    rc = report_out(rows, bad)
    if "--json" in sys.argv:
        # slides 这一格以前没有：评审的 P2-8 验收句写的是「verify.json 的
        # slides <= 24」，而这个归档从来没记过页数 ⇒ 那条判据无法被核对。
        # 现在把它记下来：页数由 deck 自己数，不由生成器自报。
        out = {"deck": DECK.replace(os.sep, "/"), "tokens": len(rows),
               "slides": len(Presentation(os.path.join(ROOT, DECK)).slides),
               # 图随文走这件事要能在读端被反驳：记排布形状，而不是记"我搬过了"。
               "figure_layout": figure_layout(os.path.join(ROOT, DECK)),
               "tiers": dict((k, sum(1 for r in rows if r[3] == k))
                             for k in set(r[3] for r in rows)),
               "untraced": [(list(r) + [None, None, None])[:3] for r in bad]}
        d = os.path.join(ROOT, "results", "ppt")
        if not os.path.isdir(d):
            os.makedirs(d)
        write_json_atomic(os.path.join(d, "verify.json"), out)
        print("WROTE results/ppt/verify.json")
    return rc


if __name__ == "__main__":
    sys.exit(main())
