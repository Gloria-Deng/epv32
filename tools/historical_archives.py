# -*- coding: utf-8 -*-
"""给「results/ 里这份读数到底是当前树复现的，还是旧代取到之后没再取的」补一个**类别**。

为什么需要这一门（本包实测到的形状）：`rtl/` 的任何字节都是全链门禁键，带 rtl 指纹的归档
里总有几份属于「某一旧代上取到、之后矩阵不再为当前树复跑」的历史探针读数（走查到的是 10 份），
另有几份被交付正文引用的归档根本不记逐文件 rtl 全串（合规套件、ISS 自测、字数台账、PPT 回读、
数据集口径那批）。接本门之前，包里没有任何一个工件能让读者区分这两类与「当前树复现出来的读数」，
而正文引用它们时看上去一模一样。这就是收口表第五节里那条「评审没点到、由我自己查出来」的缺口。

判据（全部 fail-closed，一条实现同时给真实判决与 --selftest 用，不留第二份副本）：
  1. 走查 `results/**/*.json`（排除 `results/backup/` 镜像、`__pycache__`、登记表自己与
     本门的明细件），用 `pnr_archive.rtl_hashes()` —— 归档写入者自己那把尺子，import 而不重抄 ——
     把每份归档分成四类：
       current    它记的 rtl 指纹与当前工作树逐位对得上
       historical 它记了 rtl 指纹，但对不上（旧代读数）
       unsigned   它一个 rtl 指纹都没记
       unreadable 它是 .json 但解不出来
     rtl 指纹有两种在册形态：64 位全串（`rtl_sha256.<file>`、`sources.rtl_sha256.<file>`）与
     前缀串（`rtl_sha256_head`、`rows[i].rtl_sha_head`，本包里最短的是 12 位）。前缀必须是当前树
     某个 rtl 文件的**唯一**前缀才算相符；对不上或位数短到不足 12 位一律落 historical，
     不许「截短到没法比就算一致」。短于 16 位的前缀单独计数印出来（`weak=`），因为它是弱证据。
  2. historical 每一份必须在 `results/HISTORICAL_ARCHIVES.json` 里有一条 class=historical 的登记，
     且登记里写的 `generation_head`（那一代 `epv32_core.v` 的 16 位前缀）必须与归档自己记的对得上；
     「被交付正文引用」的 unsigned 每一份必须有一条 class=unsigned 的登记，带 `produced_by`。
  3b. 既不记指纹、又没有任何在跑的脚本会重写它的旧侧（实测 `results/pnr/summary.json`）改记
     `supersedes` 点名取代它的那份归档，本门去核**那一份自己是不是当前树读数**——
     "它已被现役归档取代"这句话于是也可反驳，不是一句免责声明。
  3. `produced_by` 要么写 `flow/run_all.py` 步表里真实存在的步骤名，要么写包内真的存在的那支
     脚本路径（`flow/make_demo_video.py` 这类不由矩阵跑的写入者）；且该归档的文件名必须能在
     flow/ 或 tools/ 下的某个脚本里找到（证明"有脚本产出它"这句话不是编的）。
     作用域写清楚：本门只核「这个步骤名是真的、且确有脚本写这个文件名」，
     **不**核「这一步每一代都重写了它」——增量写入者在内容未变时本来就不重写（实测
     `results/bench/cifar10_int8.json` 与 `results/word_style.json` 上一趟就没被重写），
     拿 mtime 当判据会把合法的幂等写入判成缺陷。哪一份最近真被刷新过，由明细件印出来给读者看。
  4. 登记表里不许出现盘上不存在的路径（GHOST），不许把 current 类登记成历史（BOGUS：
     那等于给一份新读数盖旧章），不许重复登记，理由短于 12 字符按占位符判红。

已知边界（写在这里，不许改成豁免）：走查只覆盖 `.json`；`.vcd`/`.gz`/`build/` 里的产物没有
指纹概念，本门不管它们。引用语料沿用一致性门那份（报告/README/讲稿/PPT/附件 A），
`三方评审/` 刻意排除在外——评审件把这些路径当扣分清单点名，那是提及不是依赖。

用法：
  python tools/historical_archives.py                    # 判决; 明细写 results/logs/historical_archives.txt
  python tools/historical_archives.py --write-registry   # 由走查结果生成登记表；理由写在 REASONS（逐份）或 FAMILY（探针家族，按前缀展开）
  python tools/historical_archives.py --selftest         # 夹具全走内存替身, 不碰任何真实归档与登记表
末行判决（ASCII，stdout 最后一行）：
  HIST_ARCHIVE_OK objects=.. current=.. historical=.. unsigned=.. cited_unsigned=.. weak_heads=..
                  registered=.. unregistered=0 bogus=0 problems=0
"""
from __future__ import print_function

import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REGISTRY = "results/HISTORICAL_ARCHIVES.json"
DETAIL = os.path.join("results", "logs", "historical_archives.txt")
MIN_REASON = 12
MIN_HEAD = 12          # 本包里最短的在册前缀形态是 12 位
STRONG_HEAD = 16


def die(msg):
    raise SystemExit("HIST_ARCHIVE_FAIL: " + msg)


def _here_for_sys_path():
    for p in (HERE, os.path.join(ROOT, "flow")):
        if p not in sys.path:
            sys.path.insert(0, p)


# ---------------------------------------------------------------- 当前树指纹
def current_tree():
    _here_for_sys_path()
    from pnr_archive import rtl_hashes      # 单一实现: 归档写入者自己那把尺子
    return rtl_hashes()


# ---------------------------------------------------------------- 引用语料
REF_RE = re.compile(r"results/[A-Za-z0-9_./\-\u4e00-\u9fff]+\.(?:json|log|vcd|vcd\.gz|gz|txt)")
STEP_ROW = re.compile(r'\(\s*"([a-z0-9_]+)"\s*,\s*s_[a-z0-9_]+\s*\)')


def deliverable_corpus():
    """交付正文那份语料由一致性门自己定义, 本门 import 它的 load_all()/read_doc()。"""
    _here_for_sys_path()
    import check_deliverable_consistency as cdc
    paths = cdc.load_all()
    texts = []
    for k in cdc.DOC_KEYS:
        t = cdc.read_doc(k, paths)
        if t:
            texts.append((os.path.basename(paths[k]), t))
    docs_dir = os.path.join(ROOT, "docs")
    for fn in sorted(os.listdir(docs_dir)):
        if fn.startswith(u"附件") and fn.endswith(".md"):
            texts.append((fn, io.open(os.path.join(docs_dir, fn), encoding="utf-8").read()))
    if len(texts) < 4:
        die("交付正文语料只装配出 %d 份 —— 解析面塌了, 不宣布零缺口" % len(texts))
    return texts


def cited_paths(corpus=None):
    cite = {}
    for name, t in (corpus if corpus is not None else deliverable_corpus()):
        for m in REF_RE.finditer(t):
            rel = m.group(0).rstrip(".,;:")
            parts = rel.split("/")
            if len(parts) < 2 or parts[0] != "results":
                continue
            cite.setdefault(rel, []).append(name)
    if not cite:
        die("交付正文一条 results/ 产物都没引用 —— 这条判据是空的")
    return cite


def step_names():
    src = io.open(os.path.join(ROOT, "flow", "run_all.py"), encoding="utf-8").read()
    names = [m.group(1) for m in STEP_ROW.finditer(src)]
    if len(names) < 5:
        die("run_all.py 步表解析不出步骤名 (got %d) —— 解析面塌了" % len(names))
    return names


def producer_exists(st, steps):
    """produced_by 要么写 run_all 步表里的步骤名, 要么写包内真的存在的那支脚本的路径。
    两者都不许写成散文里凭空起的名。"""
    if st in steps:
        return True
    for pre in ("", "flow/", "tools/"):
        if os.path.isfile(os.path.join(ROOT, (pre + st).replace("/", os.sep))):
            return True
    return False


def producer_scripts_naming(basename):
    """flow/ 与 tools/ 下有哪些脚本的字面量里出现这个产物文件名。"""
    hits = []
    for d in (os.path.join(ROOT, "flow"), os.path.join(ROOT, "tools")):
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".py"):
                continue
            try:
                t = io.open(os.path.join(d, fn), encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            if basename in t:
                hits.append(fn)
    return hits


# ---------------------------------------------------------------- 分类
def _name_from_key(key):
    for pre in ("rtl_sha256", "rtl_sha"):
        if key.startswith(pre):
            return key[len(pre):].lstrip("_") or None
    return None


def fingerprint_leaves(obj):
    """rtl 指纹有两种在册形状, 标记住在不同的那一层:
         嵌套形  `"rtl_sha256": {"epv32_core.v": "<64hex>"}` —— 标记在**父键**上, 文件名是叶子键;
         前缀形  `"rtl_sha256_head": "<16hex>"`、`"rows[i].rtl_sha_head": "<12hex>"` —— 标记与值同一个键。
       只看叶子键会漏掉整批嵌套形(实测夹具就是这么抓到第一版判据的)。"""
    out = []

    def emit(kp, fn, val):
        s = val.strip().lower()
        shape = "hex" if re.match(r"^[0-9a-f]+$", s) else "not-hex"
        out.append((kp, fn, s, len(s), shape))

    def collect(o, pre):
        """把 rtl_sha* 那一层底下的字符串叶子都当成 {文件名: 哈希}。"""
        if isinstance(o, dict):
            for k, v in o.items():
                kp = (pre + "." + k) if pre else k
                if isinstance(v, (dict, list)):
                    collect(v, kp)
                elif isinstance(v, str):
                    emit(kp, k, v)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                collect(v, "%s[%d]" % (pre, i))

    def rec(o, pre):
        if isinstance(o, dict):
            for k, v in o.items():
                kp = (pre + "." + k) if pre else k
                if "rtl_sha" in k.lower():
                    if isinstance(v, str):
                        emit(kp, _name_from_key(k), v)
                    else:
                        collect(v, kp)
                elif isinstance(v, (dict, list)):
                    rec(v, kp)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                rec(v, "%s[%d]" % (pre, i))

    rec(obj, "")
    return out


def classify_payload(payload, cur):
    """-> (类, 该归档记的 epv32_core.v 前缀或 None, 不符清单, 弱前缀条数)"""
    leaves = fingerprint_leaves(payload)
    if not leaves:
        return "unsigned", None, [], 0
    bad, core_heads, weak = [], set(), 0
    for kp, fn, val, n, shape in leaves:
        if shape != "hex" or n < MIN_HEAD:
            bad.append("%s(位数=%d,形态=%s)" % (kp, n, shape))
            continue
        if n < STRONG_HEAD:
            weak += 1
        name = fn if (fn and fn in cur) else None
        if name is None:
            hits = [f for f in cur if cur[f].startswith(val)]
            if len(hits) != 1:
                bad.append("%s(位数=%d,前缀命中%d个当前rtl文件)" % (kp, n, len(hits)))
                continue
            name = hits[0]
        if not cur[name].startswith(val):
            bad.append("%s:%s" % (kp, name))
        elif name == "epv32_core.v":
            core_heads.add(val[:STRONG_HEAD].ljust(STRONG_HEAD, "0")[:STRONG_HEAD])
    if bad:
        head = None
        for kp, fn, val, n, shape in leaves:
            if fn == "epv32_core.v" and shape == "hex" and n >= MIN_HEAD:
                head = (val + "0" * STRONG_HEAD)[:STRONG_HEAD]
        if head is None and core_heads:
            head = sorted(core_heads)[0]
        return "historical", head, sorted(bad), weak
    return "current", (sorted(core_heads)[0] if core_heads else None), [], weak


def load_payloads():
    out = {}
    base = os.path.join(ROOT, "results")
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "backup")]
        for fn in filenames:
            if not fn.endswith(".json"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, ROOT).replace("\\", "/")
            if rel == REGISTRY or rel == DETAIL.replace("\\", "/"):
                continue
            try:
                out[rel] = json.load(io.open(full, encoding="utf-8"))
            except Exception:
                out[rel] = None
    return out


def census(payloads, cur):
    res = {}
    for rel, payload in sorted(payloads.items()):
        if payload is None:
            res[rel] = {"class": "unreadable", "head": None, "bad": ["json 解不出来"], "weak": 0}
            continue
        cls, head, bad, weak = classify_payload(payload, cur)
        res[rel] = {"class": cls, "head": head, "bad": bad, "weak": weak}
    return res


def needs_registration(objs, cite):
    need = {}
    for rel, info in sorted(objs.items()):
        if info["class"] == "historical":
            need[rel] = ("historical", info["head"])
        elif info["class"] == "unsigned" and rel in cite:
            need[rel] = ("unsigned", None)
        elif info["class"] == "unreadable":
            need[rel] = ("unreadable", None)
    return need


# ---------------------------------------------------------------- 判决
def audit(reg, objs, cite, steps, producers, path_exists):
    """objs 必须是 census() 的输出。producers: {归档相对路径: [脚本名]}。
    分母先算再判 —— 缺登记表时读数一样不许凭空消失（否则屏幕上全是 0，读不出走查到没查到东西）。"""
    stats = {
        "objects": len(objs),
        "current": sum(1 for v in objs.values() if v["class"] == "current"),
        "historical": sum(1 for v in objs.values() if v["class"] == "historical"),
        "unsigned": sum(1 for v in objs.values() if v["class"] == "unsigned"),
        "unreadable": sum(1 for v in objs.values() if v["class"] == "unreadable"),
        "cited_unsigned": sum(1 for r, v in objs.items() if v["class"] == "unsigned" and r in cite),
        "weak_heads": sum(v["weak"] for v in objs.values()),
        "registered": len((reg or {}).get("entries", [])),
        "corpus_refs": len(cite),
        "need": len(needs_registration(objs, cite)),
    }
    if not objs:
        return ["census 里一个归档对象都没有 (解析面塌了)"], stats
    if reg is None:
        return ["缺登记表 %s —— 历史类与无指纹类归档没有类别可查" % REGISTRY], stats
    problems = []
    entries = {}
    for e in reg.get("entries", []):
        p = e.get("path")
        if not p:
            problems.append("REGISTRY 有条目不带 path")
            continue
        if p in entries:
            problems.append("REGISTRY 重复登记 %s" % p)
        entries[p] = e
    need = needs_registration(objs, cite)
    for rel, (cls, head) in sorted(need.items()):
        e = entries.get(rel)
        if e is None:
            problems.append("UNREGISTERED %s 类=%s 代=%s" % (rel, cls, head or "-"))
            continue
        if len((e.get("reason") or "").strip()) < MIN_REASON:
            problems.append("THIN-REASON %s 理由短于 %d 字符" % (rel, MIN_REASON))
        if e.get("class") != cls:
            problems.append("CLASS %s 登记成 %s 而走查判它是 %s" % (rel, e.get("class"), cls))
        elif cls == "historical":
            got = (e.get("generation_head") or "").lower()
            if got != (head or "").lower():
                problems.append("GEN %s 登记的代=%s 与归档自己记的 %s 对不上"
                                % (rel, got or "-", head or "-"))
        elif cls == "unsigned":
            st = e.get("produced_by")
            sup = e.get("supersedes")
            if not st and not sup:
                problems.append("NO-TRACE %s 无指纹又被正文引用, 既没写谁产出它, 也没写它被哪份现役归档取代" % rel)
                continue
            if st and not producer_exists(st, steps):
                problems.append("BAD-PRODUCER %s 的 produced_by=%r 既不是 run_all 步表里的步骤, "
                                "也不是包内真的存在的脚本" % (rel, st))
            if sup:
                info = objs.get(sup)
                if info is None and not path_exists(sup):
                    problems.append("SUPERSEDES-GHOST %s 说它被 %s 取代, 那份归档盘上没有" % (rel, sup))
                elif info is not None and info["class"] != "current":
                    problems.append("SUPERSEDES-STALE %s 说它被 %s 取代, 可后者自己也判为 %s 而不是当前树读数"
                                    % (rel, sup, info["class"]))
            if not producers.get(rel):
                problems.append("NO-SCRIPT %s 在 flow/ 与 tools/ 下找不到任何写这个文件名的脚本" % rel)
    for p, e in sorted(entries.items()):
        info = objs.get(p)
        if info is None:
            if not path_exists(p):
                problems.append("GHOST %s 登记了盘上不存在的归档" % p)
        elif info["class"] == "current":
            problems.append("BOGUS %s 是当前树复现的读数, 却被登记成历史/无指纹 —— "
                            "这一格每一代都会翻一次（跑了昂贵臂它就翻回当前），"
                            "修法是重跑 python tools/historical_archives.py --write-registry，"
                            "不许手改登记表" % p)
    return problems, stats


# ---------------------------------------------------------------- 登记表生成
# 理由是我逐份读过这些归档与它的写入者之后写的, 不是从归档里推出来的; 类别与代前缀由脚本现填。
#
# 为什么这里**没有** results/_probe_subset_NOT_A_VERDICT.json 这一条（2026-09-26 实测后撤掉）：
# 登记表断言的是「这份东西属于旧代、矩阵不会再为当前树复跑它」，而子集产物恰恰相反——
# 任何人跑一次 `python flow/run_all.py <某步>` 就会把它刷成**当前树**的指纹。于是这条
# 登记在一半的时间里是假陈述，实测签名就是类别门报
# `BOGUS ... 是当前树读数的对象, 却按登记算历史`。它的「不是结论」由文件自己那条
# `_warning` 键与 results/README.md 承担，不需要一个会随复跑翻脸的类别断言。
# 反过来说：只有「写入者已不在现役链里」的对象才配进这张表。
REASONS = {
    # 这些读数本轮被矩阵刷新成当前树读了，历史登记随之撤掉：旧代 misalign/EPV 二分与两份成片相关归档。本轮矩阵把它们重跑成
    # 当前树读数了，所以历史章撤掉（留着就会被 BOGUS 判红）。要恢复登记得先证明它们又不再随代刷新。
    "results/sim/epv_bisect_epvisco.json": u"EPV 二分定位过程的历史读数;定位完成后这一臂不再进矩阵,是过程证据不是结论",
    "results/sim/epv_bisect_wave2.json": u"EPV 二分定位的第二发波形;定位完成后不再复跑,留档只为那条分叉可回看",
    "results/sim/epv_bisect_wavegen.json": u"EPV 二分定位的波形生成件,一次性过程产物,矩阵不复跑",
    "results/sim/epv_selftest_epvisco.json": u"自测程序在 isco 上的旧代读数;现役自测走另一臂,这份只作历史对照",
    "results/sim/nn_bench_epvisco.json": u"bench 在 isco 平台上的旧代读数;同一网络的现役结论由 results/bench/nn_bench.json 承担",
    "results/sim/nn_net_epvisco.json": u"合成网络在 isco 平台上的旧代读数;现役结论由 results/bench/nn_net.json 承担",
    "results/isco_probe/epv.json": u"isco 兼容性探针的旧代读数;探针按一次性运行设计,矩阵不为当前树重跑它",
    "results/pnr/summary.json": u"上一代器件口径归档;现役 pnr_gate 只认 summary2.json,这份留着做配对对照的旧侧",
    "results/pnr/ab_m6_vs_m7.json": u"一次性 A/B 对照归档,不在矩阵复跑范围内;引用它时按历史读数读",
    "results/pnr/ab_sdc_sensitivity.json": u"一次性 SDC 敏感性对照归档,不在矩阵复跑范围内",
    "results/compliance/rv32ui.json": u"合规套件读数:本身不记逐文件 rtl 全串,由 rv32ui 步骤产出并随矩阵刷新",
    "results/compliance/rv32um.json": u"合规套件读数:本身不记逐文件 rtl 全串,由 rv32um 步骤产出并随矩阵刷新",
    "results/compliance/rv32mi.json": u"合规套件读数:本身不记逐文件 rtl 全串,由 rv32mi 步骤产出并随矩阵刷新",
    "results/bench/cifar10_int8.json": u"真实数据集口径读数:新鲜度由它自己记的数据集/浮点权重哈希与 torch/numpy 版本保证,增量写入者内容未变时不重写",
    "results/bench/mem_sensitivity.json": u"访存敏感性读数:新鲜度由它记的被扫镜像与所引归档哈希保证,由 mem_sensitivity 步骤产出",
    "results/iss/selftest.json": u"ISS 自测读数:记 ISS 源文件哈希头,由 iss_selftest 步骤产出并随矩阵刷新",
    "results/word_limits.json": u"字数上限台账:由 check_word_limits 从赛题原文现读,由 word_gate 步骤产出",
    "results/word_style.json": u"字数口径读数:由字数门现算,增量写入时内容未变就不重写,故不按每代刷新读它",
    "results/ppt/verify.json": u"PPT 回读校验产物:由 ppt 步骤产出",
    "results/pnr/bram_template_arms.json": u"八臂 TD 实测读数:臂源码是独立最小 RAM 模块、不吃 rtl/ 那棵树，故本身不记 rtl 指纹；一次约 40 分钟（两臂在 900 s 上限内未返回），矩阵不为当前树重跑它；链条里跑的是 tools/bram_arms_check.py 那发便宜核对",
    "results/asserts/mutation_proof.json": u"断言门的注入证明判决件：由 flow/assert_selfcheck.py 在改断言臂时产出，矩阵这一代没有再跑它（mtime 停在 09-26 16:55），所以它记的是本轮改 muldiv.v 之前那一代；要拿它当现役证据得复跑那一步",
    "results/cov/summary.json": u"覆盖率臂的汇总读数：那是第三条构建目录（build/sim_cov），矩阵只在开关变化时重跑；这份停在 09-26 20:04，与当前树唯一的差是本轮改掉的 rtl/epv32_muldiv.v，故属旧代",
    "results/rand_trace/prefix_divzero_summary.json": u"rand_trace 除零那一格的 A/B 判决件：反例臂把 muldiv.v 退回旧行为，所以它记的指纹属改前那一代；现役随机流结论由 results/rand_trace/summary.json 承担",
}

# 家族级理由：一个探针家族一条规则。登记表仍然按逐路径落条目（判据一字不改），只是
# 理由文本由这里展开 —— 同一条事实抄 57 遍是第二份抄本而不是证据；而下一条新探针不会
# 自带手写理由，只会让 --write-registry 死在「没有理由」上。每条都对着 census() 的 bad
# 列表写的（现量见 scratch_histdiag2.log）：这些归档与当前树的差只落在
# rtl/epv32_muldiv.v 上，也就是本轮改掉的那一格除零行为。
FAMILY = (
    ("results/sim/shrink__",
     u"09-26 那轮缩例探针（把复现程序逐条删到最小）的逐发读数，由 flow/rt.py bench --tag 产出；"
     u"与当前树唯一的差是本轮改掉的 rtl/epv32_muldiv.v，故属旧代。矩阵不为当前树重跑这些"
     u"探针，现役结论由同名的无 tag 归档承担"),
    ("results/sim/signab__",
     u"09-26 有符号除法 A/B 两臂的逐发读数（把商与余数的符号处理退回旧行为那一臂），由"
     u" flow/rt.py bench --tag 产出；与当前树唯一的差是本轮改掉的 rtl/epv32_muldiv.v，故属旧代"),
    ("results/sim/rem2__",
     u"09-26 rem 除零那一格的 A/B 逐发读数（反例臂把 REM 退回返回 |被除数|），由 flow/rt.py"
     u" bench --tag 产出；与当前树唯一的差是本轮改掉的 rtl/epv32_muldiv.v，故属旧代"),
    ("results/sim/chainab__",
     u"09-26 链式 A/B（一次改动多臂对照）留下的逐发读数，由 flow/rt.py bench --tag 产出；"
     u"与当前树唯一的差是本轮改掉的 rtl/epv32_muldiv.v，故属旧代"),
    ("results/sim/minimal__",
     u"09-26 最小复现程序的一发读数（定位过程证据，不是结论），由 flow/rt.py bench --tag 产出；"
     u"与当前树唯一的差是本轮改掉的 rtl/epv32_muldiv.v，故属旧代"),
)


def reason_for(rel):
    # REASONS 优先，其次家族前缀；都没有返回 None，调用方按缺理由判红。
    if rel in REASONS:
        return REASONS[rel]
    for pre, why in FAMILY:
        if rel.startswith(pre):
            return why
    return None

PRODUCED_BY = {
    "results/compliance/rv32ui.json": "rv32ui",
    "results/compliance/rv32um.json": "rv32um",
    "results/compliance/rv32mi.json": "rv32mi",
    "results/bench/cifar10_int8.json": "cifar10_net",
    "results/bench/mem_sensitivity.json": "mem_sensitivity",
    "results/iss/selftest.json": "iss_selftest",
    "results/word_limits.json": "word_gate",
    "results/word_style.json": "word_gate",
    "results/ppt/verify.json": "ppt",
    "results/demo/demo_video.json": "flow/make_demo_video.py",
    "results/pnr/ab_m6_vs_m7.json": "tools/ab_m6_m7.py",
    "results/pnr/ab_sdc_sensitivity.json": "tools/ab_m6_m7.py",
    "results/pnr/bram_template_arms.json": "tools/bram_arms_build.py",
}

# 有些归档既不记 rtl 指纹、也没有任何在跑的脚本会重写它 —— 它们是「被某份现役归档取代」的旧侧
# (实测: results/pnr/summary.json, tools/pnr_archive.py 里就写着它被 summary2.json 取代)。
# 这一档不许只写一句理由: 必须点名取代它的那份, 由本门去核那份自己是不是当前树读数。
FROZEN = {
    "results/pnr/summary.json": "results/pnr/summary2.json",
}


def write_registry():
    cur = current_tree()
    objs = census(load_payloads(), cur)
    cite = cited_paths()
    steps = step_names()
    need = needs_registration(objs, cite)
    no_reason = [r for r in need if reason_for(r) is None]
    if no_reason:
        die("有 %d 份需要登记的归档在 REASONS 里没有我写的理由: %s"
            % (len(no_reason), ", ".join(no_reason[:8])))
    entries = []
    for rel, (cls, head) in sorted(need.items()):
        e = {"path": rel, "class": cls, "reason": reason_for(rel),
             "cited_by": sorted(set(cite.get(rel, [])))[:5]}
        if cls == "historical":
            e["generation_head"] = head
        elif cls == "unsigned":
            if rel in FROZEN:
                e["supersedes"] = FROZEN[rel]
                entries.append(e)
                continue
            st = PRODUCED_BY.get(rel, "")
            if not producer_exists(st, steps):
                die("无指纹归档 %s 的 PRODUCED_BY 给 produced_by=%r, 但它既不在 run_all 步表里, "
                    u"也不是包内真的存在的脚本 —— 这句话要么是我编的, 要么步骤/脚本改过名" % (rel, st))
            e["produced_by"] = st
        entries.append(e)
    doc = {"schema": "epv32.historical_archives.v1",
           "note": (u"results/ 归档的读数类别登记表。historical=旧代取到、矩阵不为当前树重取；"
                    u"unsigned=不记逐文件 rtl 全串、新鲜度由别的键或别的步骤保证。"
                    u"类别/代前缀/produced_by 一律由 tools/historical_archives.py 现填并复核，不要手改本文件。"),
           "current_tree_head": cur["epv32_core.v"][:STRONG_HEAD],
           "entries": entries}
    with io.open(os.path.join(ROOT, REGISTRY), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    print("HIST_REGISTRY_WROTE entries=%d historical=%d unsigned=%d unreadable=%d current_tree_head=%s -> %s"
          % (len(entries), sum(1 for e in entries if e["class"] == "historical"),
             sum(1 for e in entries if e["class"] == "unsigned"),
             sum(1 for e in entries if e["class"] == "unreadable"),
             doc["current_tree_head"], REGISTRY))
    return 0


# ---------------------------------------------------------------- 夹具
CASES = ["positive_control", "unregistered_historical", "class_swapped",
         "entry_for_current_is_bogus", "thin_reason", "bad_produced_step",
         "no_producer_field", "unsigned_without_producer_script",
         "cited_unsigned_unregistered", "registry_absent", "empty_census_denominator",
         "ghost_entry", "duplicate_entry", "wrong_generation_head",
         "head_form_matches_current", "head_form_mismatch_is_historical",
         "prefix_too_short_is_historical", "unreadable_must_be_registered",
         "frozen_supersedes_current_ok", "frozen_supersedes_not_current", "frozen_supersedes_ghost"]


def selftest():
    cur = {"epv32_core.v": "ab" * 32, "epv32_alu.v": "cd" * 32}
    OLD = "dd" * 32
    p_cur = {"rtl_sha256": {"epv32_core.v": cur["epv32_core.v"], "epv32_alu.v": cur["epv32_alu.v"]}}
    p_old = {"rtl_sha256": {"epv32_core.v": OLD, "epv32_alu.v": cur["epv32_alu.v"]}}
    p_plain = {"suite": "rv32ui", "all_pass": True, "n_tests": 42}
    p_head_ok = {"rtl_sha256_head": cur["epv32_core.v"][:16]}
    p_head_bad = {"rtl_sha256_head": "ee" * 16}
    p_short = {"rtl_sha256_head": "ababab"}            # 6 位: 短到不足 12 位, 不许当相符
    payloads = {"results/cur.json": p_cur, "results/hist.json": p_old, "results/plain.json": p_plain}
    objs = census(payloads, cur)
    cite = {"results/cur.json": ["README.md"], "results/hist.json": [u"附件A.md"],
            "results/plain.json": ["README.md"]}
    steps = ["rv32ui", "doc_gate"]
    producers = {"results/plain.json": ["rt.py"], "results/hist.json": [], "results/cur.json": []}

    def ent(path, cls, reason, head=None, by=None):
        e = {"path": path, "class": cls, "reason": reason}
        if head:
            e["generation_head"] = head
        if by:
            e["produced_by"] = by
        return e

    good = {"entries": [
        ent("results/hist.json", "historical", u"旧代二分定位读数,矩阵不为当前树重取它", head="dd" * 8),
        ent("results/plain.json", "unsigned", u"由 rv32ui 步骤产出并随矩阵刷新", by="rv32ui")]}
    exists = lambda p: p not in ("results/nope.json", "results/nowhere.json")

    def run(pl, registry, cs=None, pr=producers, ob=None):
        o = ob if ob is not None else census(pl, cur)
        return audit(registry, o, cs if cs is not None else cite, steps, pr, exists)

    probes = []

    def add(name, probs, cond, dbg):
        probes.append((name, bool(cond) and not any(x.startswith("内部错误") for x in probs), dbg))

    p, s = run(payloads, good)
    add("positive_control", p, p == [], p)
    p, s = run(payloads, {"entries": [good["entries"][1]]})
    add("unregistered_historical", p, any(x.startswith("UNREGISTERED results/hist.json") for x in p), p)
    swapped = {"entries": [ent("results/hist.json", "unsigned", u"旧代二分定位读数,矩阵不为当前树重取它", by="rv32ui"),
                           good["entries"][1]]}
    p, s = run(payloads, swapped)
    add("class_swapped", p, any(x.startswith("CLASS results/hist.json") for x in p), p)
    p, s = run(payloads, {"entries": good["entries"] + [ent("results/cur.json", "historical", u"拿当前树的读数盖一个历史章, 这句够长", head="dd" * 8)]})
    add("entry_for_current_is_bogus", p, any(x.startswith("BOGUS results/cur.json") for x in p), p)
    p, s = run(payloads, {"entries": [ent("results/hist.json", "historical", u"TODO", head="dd" * 8), good["entries"][1]]})
    add("thin_reason", p, any(x.startswith("THIN-REASON") for x in p), p)
    p, s = run(payloads, {"entries": [good["entries"][0], ent("results/plain.json", "unsigned", u"由某步骤产出并随矩阵刷新", by="no_such_step")]})
    add("bad_produced_step", p, any("BAD-PRODUCER" in x and "no_such_step" in x for x in p), p)
    p, s = run(payloads, {"entries": [good["entries"][0], ent("results/plain.json", "unsigned", u"由某步骤产出并随矩阵刷新")]})
    add("no_producer_field", p, any(x.startswith("NO-TRACE") for x in p), p)
    frozen_ok = {"entries": [good["entries"][0],
                             dict(ent("results/plain.json", "unsigned", u"这一份没人重写, 由现役归档取代"),
                                  supersedes="results/cur.json")]}
    p, s = run(payloads, frozen_ok)
    add("frozen_supersedes_current_ok", p, p == [], p)
    frozen_bad = {"entries": [good["entries"][0],
                              dict(ent("results/plain.json", "unsigned", u"这一份没人重写, 由现役归档取代"),
                                   supersedes="results/hist.json")]}
    p, s = run(payloads, frozen_bad)
    add("frozen_supersedes_not_current", p, any("SUPERSEDES-STALE" in x for x in p), p)
    frozen_ghost = {"entries": [good["entries"][0],
                                dict(ent("results/plain.json", "unsigned", u"这一份没人重写, 由现役归档取代"),
                                     supersedes="results/nowhere.json")]}
    p, s = run(payloads, frozen_ghost)
    add("frozen_supersedes_ghost", p, any("SUPERSEDES-GHOST" in x for x in p), p)
    p, s = run(payloads, good, pr={})
    add("unsigned_without_producer_script", p, any(x.startswith("NO-SCRIPT") for x in p), p)
    p, s = run(payloads, {"entries": [good["entries"][0]]})
    add("cited_unsigned_unregistered", p, any(x.startswith("UNREGISTERED results/plain.json") for x in p), p)
    p, s = run(payloads, None)
    add("registry_absent", p, any("缺登记表" in x for x in p), p)
    p, s = run({}, {"entries": []}, cs={})
    add("empty_census_denominator", p, any("解析面塌了" in x for x in p), p)
    p, s = run(payloads, {"entries": good["entries"] + [ent("results/nope.json", "historical", u"盘上根本没有的一份", head="dd" * 8)]})
    add("ghost_entry", p, any(x.startswith("GHOST results/nope.json") for x in p), p)
    dup = good["entries"] + [dict(good["entries"][0])]
    p, s = run(payloads, {"entries": dup})
    add("duplicate_entry", p, any("重复登记" in x for x in p), p)
    p, s = run(payloads, {"entries": [ent("results/hist.json", "historical", u"旧代二分定位读数,矩阵不为当前树重取它", head="ee" * 8), good["entries"][1]]})
    add("wrong_generation_head", p, any(x.startswith("GEN results/hist.json") for x in p), p)
    p, s = run({"results/h.json": p_head_ok}, {"entries": []}, cs={})
    add("head_form_matches_current", p, s.get("current") == 1 and p == [], (p, s))
    p, s = run({"results/h.json": p_head_bad}, {"entries": []}, cs={})
    add("head_form_mismatch_is_historical", p, s.get("historical") == 1, (p, s))
    p, s = run({"results/h.json": p_short}, {"entries": []}, cs={})
    add("prefix_too_short_is_historical", p, s.get("historical") == 1, (p, s))
    pl2 = dict(payloads)
    pl2["results/broken.json"] = None
    p, s = run(pl2, good)
    add("unreadable_must_be_registered", p, any(x.startswith("UNREGISTERED results/broken.json") for x in p), p)

    names = set(n for n, _, _ in probes)
    missing = [c for c in CASES if c not in names]
    fails = [n for n, hit, _ in probes if not hit] + ["declared_not_run:" + c for c in missing]
    for n, hit, dbg in probes:
        print("[fixture] %-34s %s" % (n, "bites as expected" if hit else "FAIL %s" % (dbg,)))
    for c in missing:
        print("[fixture] %-34s FAIL 这一条声明了但根本没跑到" % c)
    total = len(probes)
    if fails:
        print("HIST_ARCHIVE_SELFTEST FAIL fails=%d/%d %s" % (len(fails), total, ",".join(fails)))
        return 1
    print("HIST_ARCHIVE_SELFTEST OK %d/%d" % (total, total))
    return 0


# ---------------------------------------------------------------- main
def main(argv):
    if "--selftest" in argv:
        return selftest()
    if "--write-registry" in argv:
        return write_registry()
    cur = current_tree()
    payloads = load_payloads()
    objs = census(payloads, cur)
    reg = None
    rp = os.path.join(ROOT, REGISTRY)
    if os.path.isfile(rp):
        reg = json.load(io.open(rp, encoding="utf-8"))
    cite = cited_paths()
    steps = step_names()
    producers = {rel: producer_scripts_naming(os.path.basename(rel))
                 for rel, info in objs.items() if info["class"] == "unsigned"}
    problems, stats = audit(reg, objs, cite, steps, producers,
                            lambda p: os.path.isfile(os.path.join(ROOT, p)))
    detail = io.open(os.path.join(ROOT, DETAIL), "w", encoding="utf-8")
    detail.write(u"current_tree_head=%s\n" % cur["epv32_core.v"][:STRONG_HEAD])
    detail.write(u"stats=%s\n\n== 登记在案的每一条 ==\n" % json.dumps(stats, sort_keys=True))
    for e in (reg or {}).get("entries", []):
        detail.write(u"%-42s %-11s gen=%-16s produced_by=%-16s cited_by=%s\n  理由: %s\n" % (
            e.get("path"), e.get("class"), e.get("generation_head", "-"),
            e.get("produced_by", "-"), ",".join(e.get("cited_by", []) or ["-"]), e.get("reason")))
    detail.write(u"\n== 走查判 historical 的每一份与它记的代 ==\n")
    for rel, info in sorted(objs.items()):
        if info["class"] == "historical":
            detail.write(u"%-42s gen=%-16s %s\n" % (rel, info["head"], "; ".join(info["bad"])))
    detail.write(u"\n== 无指纹归档各自被哪些脚本命名 ==\n")
    for rel, hits in sorted(producers.items()):
        detail.write(u"%-42s %s\n" % (rel, ",".join(hits) if hits else "(没有脚本写这个名字)"))
    verdict = ("HIST_ARCHIVE_%s objects=%d current=%d historical=%d unsigned=%d "
               "cited_unsigned=%d weak_heads=%d registered=%d unregistered=%d bogus=%d problems=%d" % (
        "OK" if not problems else "FAIL",
        stats.get("objects", 0), stats.get("current", 0), stats.get("historical", 0),
        stats.get("unsigned", 0), stats.get("cited_unsigned", 0), stats.get("weak_heads", 0),
        stats.get("registered", 0),
        sum(1 for p in problems if p.startswith("UNREGISTERED")),
        sum(1 for p in problems if p.startswith(("BOGUS", "GHOST", "CLASS", "GEN", "THIN",
                                                 "NO-TRACE", "NO-SCRIPT", "BAD-PRODUCER",
                                                 "SUPERSEDES-GHOST", "SUPERSEDES-STALE", "REGISTRY"))),
        len(problems)))
    if problems:
        detail.write(u"\n== 问题 ==\n")
        for p in problems:
            detail.write(u"PROBLEM " + p + u"\n")
    # 判决行无条件落盘（与 tools/fixture_claims.py 同一条纪律）：干净时那份被收口表
    # 点名的读数里也要 grep 得到末行，否则「产物在」与「判决行在」分家。
    detail.write(u"\n== 判决 ==\n" + verdict + u"\n")
    detail.close()
    for p in problems[:15]:
        sys.stderr.write(p + "\n")
    print(verdict)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
