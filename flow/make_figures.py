#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""flow/make_figures.py —— 从**已认证归档**出交付件附图, 出图前必过出处自检。

为什么要有这个文件: 赛题 4.1.5 一.3 要「数据尽可能以图表等直观表达方式呈现,
图表标注数据来源」, 而三方评审给 ④ 档记的第一条硬伤就是「报告 0 张图片、
PPT 0 张图片, 20 页纯文字」。补图不能靠手画: 手画的图上的数会和归档漂。

三条承重设计:
  1. **不复制取数**。器件口径直接 import flow/run_all.py 的 pnr_anchors() ——
     那是文档门自己用的那张「标签 → 由归档现算的读数」表。图与门因此同源,
     归档一变, 图和正文一起变, 不会各漂一半。
  2. **每个画到图面上的数字都要有出处**, 由 --check 现算: 要么逐字出现在它
     声称的那份归档 JSON 里, 要么等于门禁锚点表现算出来的某条读数。四舍五入
     出来的值不算 (0.62405056 印成 0.624051 而归档里没有 ⇒ 判红)。
     (g)/(h) 两张还多一道 avals()/ashorts(): 取值时按「"键": 值」的形状**在归档
     文本里定位到那一个键**, 再与解析后的对象逐字对撞。因为 check_provenance 只
     回答「归档里有没有这个数」, 回答不了「这个数是不是这个键的」—— 把 (h) 两条
     臂的读数对调, 每个数都仍然逐字躺在归档里, 而图画的是假说。
  3. **图面标签全 ASCII**。DejaVu Sans 没有中文字形, 中文会画成方框而脚本
     一路绿; 所以 ASCII 是一条会咬的判据, 数据来源用归档路径标 (ASCII)。
     (g)/(h) 的中文解释一律写在登记簿/正文那侧, 不写在图面上。

图面八张: (a) 单算子 (b) 逐层 (c) 器件资源 (d) 加速比折损 (e) 合规
     (f) Yosys 单元 (g) CIFAR-10 精度与在环加速 (h) 访存代价折损曲线

用法:
  python flow/make_figures.py            # 出图 + 落登记簿
  python flow/make_figures.py --check    # 只核: 登记簿/出处/引用双向差集/无孤儿
  python flow/make_figures.py --selftest # 注入: 改一位读数/塞中文标签/裁掉标题
"""
import io
import os
import re
import sys
import json
import glob
import shutil
import hashlib
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.chdir(ROOT)
sys.path.insert(0, os.path.join("flow"))
sys.path.insert(0, os.path.join("tools"))

import matplotlib                                      # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                        # noqa: E402
from matplotlib.transforms import Bbox                 # noqa: E402


import run_all as RA                                   # noqa: E402

FIG_DIR = os.path.join("docs", u"附图")
REGISTRY = os.path.join(FIG_DIR, "registry.json")   # 登记簿钉在交付位置, 不跟 _OUT 走
_OUT = FIG_DIR                                  # build(out_dir=) 会改它; --check 把图重画到临时目录
PNG_META = {"Software": None, "Creation Time": None, "Author": None}

# ------------------------------------------------------------------ 取数
# 期望值一律从门禁那张锚点表来, 本文件不写第二个「8193」之类的字面量。
BENCH = RA.jload(os.path.join("results", "bench", "nn_bench.json"))
NET = RA.jload(os.path.join("results", "bench", "nn_net.json"))
SYNTH = RA.jload(os.path.join("results", "synth", "summary.json"))
# (g)/(h) 两份新结果的归档。缺料即死: jload 直接抛, 不静默少画两张图。
ARC_CIF = "results/bench/cifar10_int8.json"
ARC_CIFNET = "results/bench/cifar10_net.json"
ARC_MEM = "results/bench/mem_sensitivity.json"
CIF = RA.jload(os.path.join("results", "bench", "cifar10_int8.json"))
CIFNET = RA.jload(os.path.join("results", "bench", "cifar10_net.json"))
MEMS = RA.jload(os.path.join("results", "bench", "mem_sensitivity.json"))

_ANCH = list(RA.pnr_anchors())
for ui in ("rv32ui", "rv32um", "rv32mi"):
    d = RA.jload(os.path.join("results", "compliance", ui + ".json"))
    _ANCH.append((ui + " 通过数", "%d / %d" % (d["n_pass"], d["n_tests"]), None))
    _ANCH.append((ui + " 用例数", "%d" % d["n_tests"], None))
_ANCH.append(("加速比", "%0.2f" % BENCH["speedup"], None))
_ANCH.append(("基线周期", "%s" % BENCH["ref_cycles"], None))
_ANCH.append(("EPV周期", "%s" % BENCH["epv_cycles"], None))
_ANCH.append(("MAC 数", "%s" % BENCH["macs"], None))
_ANCH.append(("核含扩展单元数", "%s" % SYNTH["arms"]["core_with_epv"]["cells"], None))
_ANCH.append(("核基线单元数", "%s" % SYNTH["arms"]["core_no_epv"]["cells"], None))
_ANCH.append(("扩展净开销", "%s" % SYNTH["epv_net_overhead_cells"], None))
_ANCH.append(("EPV单元单独", "%s" % SYNTH["arms"]["epv_unit_only"]["cells"], None))
_ANCH.append(("网络加速比", "%0.2f" % NET["speedup"], None))
ANCHOR_VALUES = set(v for _l, v, _c in _ANCH)
# 锚点表里的数按正文习惯还会带千位分隔符; 图面上不带, 所以核出处时两边都去逗号
ANCHOR_NOCOMMA = set(v.replace(",", "") for v in ANCHOR_VALUES)

ARCHIVE_TEXT = {}


def archive_text(rel):
    """归档的原始文本 (出处判据读的就是这个)。"""
    key = rel.replace(os.sep, "/")
    if key not in ARCHIVE_TEXT:
        with io.open(key, encoding="utf-8") as fh:
            ARCHIVE_TEXT[key] = fh.read().replace(",", "")
    return ARCHIVE_TEXT[key]


# 归档里「键: 值」的那个值。先验形状, 解不出即 raise —— 表式变了就停手,
# 不许退化成「抓第一个能转 float 的字段继续画」。
_VAL_TOK = r"(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"


def archive_slice(rel, after=None, before=None):
    """切出归档文本里某一个数组/对象那段 (按顶格键名定位)。找不到即 raise。"""
    txt = archive_text(rel)
    if after is not None:
        probe = '"%s"' % after
        if txt.count(probe) != 1:
            raise AssertionError("%s: 切片锚点 %r 命中 %d 次, 应为恰好 1 次 —— "
                                 "用例数变了要先改本文件的取值方式, 不许取第一条"
                                 % (rel, after, txt.count(probe)))
        txt = txt.split(probe, 1)[1]
    if before is not None:
        probe = '"%s"' % before
        if probe not in txt:
            raise AssertionError("%s: 切片右界 %r 找不到 —— 归档表式变了" % (rel, before))
        txt = txt.split(probe, 1)[0]
    return txt


def avals(rel, key, expect, after=None, before=None):
    """按「"key": 值」的形状从**归档原始文本**里解出该键的打印串, 逐个要求与解析后的
    对象 expect 逐字相等, 然后把这些打印串交给图面。

    为什么要绕这一道 (check_provenance 挡不住的那一格): 它只问「归档里有没有这个
    数」, 不问「这个数是不是这个键的」。把 (h) 的两条臂对调, 12 个读数全都还逐字
    躺在归档里, 而图讲的是一个反了的假说。
    """
    seg = archive_slice(rel, after, before)
    toks = re.findall(r'"%s":\s*%s' % (re.escape(key), _VAL_TOK), seg)
    if not toks:
        raise AssertionError("%s: 键 %r 在切片里解不出任何数值 (锚点 after=%r "
                             "before=%r) —— 表式变了先核对再画图" % (rel, key, after, before))
    if len(toks) != len(expect):
        raise AssertionError("%s: 键 %r 解出 %d 个值, 解析后的对象给 %d 个 —— "
                             "两边不是同一批读数, 不许各画各的"
                             % (rel, key, len(toks), len(expect)))
    out = []
    for tok, want in zip(toks, expect):
        if tok != str(want):
            raise AssertionError("%s: 键 %r 归档打印 %r 而解析后的对象给 %r —— "
                                 "图面要印的那个数对不上它声称的那个键"
                                 % (rel, key, tok, str(want)))
        out.append(tok)
    return out


def ashorts(rel, key, fmt, expect, after=None, before=None):
    """同 avals, 但图面印的是按 fmt 舍入后的短形。
    归档把 -0.15999999999999903 这种全精度串存着, 图面只放得下 -0.16; 容差由**短形
    自己的末位**推 (_last_place/2), 不是我挑一个 1e-4, 且原值必须逐字在归档里。
    """
    toks = avals(rel, key, expect, after, before)
    out = []
    for tok, want in zip(toks, expect):
        s = fmt % want
        tol = _last_place(s) / 2.0
        if abs(float(s) - float(tok)) > tol:
            raise AssertionError("%s: 键 %r 的 %r 不是 %r 的忠实舍入 (差 %.3g > 半个末位 %.3g)"
                                 % (rel, key, s, tok, abs(float(s) - float(tok)), tol))
        out.append(s)
    return out


# 图面上的每一条数字: (值, 它声称出自的归档)
PROV = []


def num(v, archive):
    PROV.append((str(v), archive))
    return str(v)


def _last_place(s):
    """标签末位的小数值位: "9.819"->1e-3, "15.73"->1e-2, "23005"->1。"""
    txt = str(s).strip()
    if "." in txt:
        return 10.0 ** -len(txt.split(".")[1].rstrip("%"))
    return 1.0


_NUM_TOKEN = re.compile(r"-?\d+\.\d+(?:[eE][-+]?\d+)?|-?\d+")


def check_provenance(pairs=None):
    """图面上每个数字都要满足三档之一:
      (a) 等于门禁锚点表现算出来的某条读数 —— 与文档门同一张表;
      (b) 逐字出现在它声称的那份归档里;
      (c) 是归档里某个全精度数的**忠实舍入** —— 容差由标签自己的打印位数推出
          (半个末位), 不是我挑一个 1e-4。归档存的是 9.818983289930555 这种
          全精度值, 图面只能印短形, 所以「逐字可见」对舍入标签天然不成立;
          但 (c) 必须回指到归档里那个原值, 于是它挡得住「随手改成 9.9」。
    四舍五入出来的值若在任何归档里都找不到可回指的原值, 仍然判红。
    """
    bad = []
    for v, arch in (pairs if pairs is not None else PROV):
        vv = str(v).replace(",", "").strip()
        if vv in ANCHOR_NOCOMMA or vv in archive_text(arch):
            continue
        try:
            want = float(vv)
        except ValueError:
            bad.append((v, arch, "非数字且不在归档里"))
            continue
        tol = _last_place(vv) / 2.0 + 1e-12
        hit = None
        for tok in _NUM_TOKEN.findall(archive_text(arch)):
            try:
                x = float(tok)
            except ValueError:
                continue
            if abs(want - x) <= tol:
                hit = tok
                break
        if hit is None:
            bad.append((v, arch, "找不到可回指的原值 (容差 %g)" % tol))
    return bad


# ------------------------------------------------------------------ 画图
def _all_texts(fig):
    """把图上**会出现的全部字符串**收齐: 标题、轴标签、刻度、图例、注记、图级文字。
    ASCII 判据要覆盖到这一整集, 少收一类就等于给那类开逃生门。"""
    out = []
    for o in (fig, *[ax for ax in fig.axes]):
        for attr in ("get_title", "get_xlabel", "get_ylabel"):
            if hasattr(o, attr):
                out.append(getattr(o, attr)())
        for art in list(getattr(o, "texts", [])) + list(getattr(o, "annotations", [])):
            out.append(art.get_text())
    for ax in fig.axes:
        for grp in (ax.get_xticklabels(), ax.get_yticklabels()):
            out.extend(l.get_text() for l in grp)
        leg = ax.get_legend()
        if leg is not None:
            out.extend(t.get_text() for t in leg.get_texts())
    return [s for s in out if isinstance(s, str) and s]


def _assert_drawn(fig, expect, path_hint):
    """落盘前一条: 我**打算**印到图上的每个归档读数, 必须真的在图面的文字里找得到。
    挡的是「标签被后续调用覆盖 / 取错了键所以那个串根本没进图 / annotate 挂到了
    隐藏的 artist 上」—— 这些都不会让 savefig 报错, 只会让图上少一个数。"""
    got = _all_texts(fig)
    missing = [e for e in expect if not any(e in t for t in got)]
    if missing:
        raise AssertionError("%s: 打算印上去的读数有 %d 条没出现在图面上: %s "
                             "—— 标签丢了或被覆盖, 而 savefig 一路绿"
                             % (path_hint, len(missing), missing[:6]))
    return len(expect)


def _inside_inches(tb, fig, path, tol=0.03):
    """越界量用**英寸对英寸**量: Figure.get_tightbbox() 返回英寸, 而 fig.bbox 是
    fig.dpi 下的像素, 所以 _finish 里那条同名判据实际上是拿 ~920 像素的右界去比
    ~9.2 英寸的右界 —— 它只挡得住越出 1 英寸以上的gross 裁剪 (盘上 fig_f 那条长
    suptitle 左右各越界 0.22/0.87 英寸, 它看不见)。本轮起 `_finish` 无条件走这一条
    把八张全量 (fig_f 一开就报出真实裁剪，其余七张量过之后本来就在界内)。容差 0.03 in
    给图注那行的
    下伸部留位 (实测八张图的 tb.y0 都在 -0.011..-0.001 in)。
    """
    dpi = float(fig.dpi)
    w, h = fig.bbox.width / dpi, fig.bbox.height / dpi
    over = []
    if tb.x0 < -tol:
        over.append("left %.3f in" % tb.x0)
    if tb.y0 < -tol:
        over.append("bottom %.3f in" % tb.y0)
    if tb.x1 > w + tol:
        over.append("right %.3f in" % (tb.x1 - w))
    if tb.y1 > h + tol:
        over.append("top %.3f in" % (tb.y1 - h))
    if over:
        raise AssertionError("%s: CLIPPED off canvas -- %s (content %.3f..%.3f x "
                             "%.3f..%.3f in vs canvas %.3f x %.3f in) —— 标题或图注"
                             "被画布裁掉而 savefig 一路绿"
                             % (path, ", ".join(over), tb.x0, tb.x1, tb.y0, tb.y1, w, h))


def _finish(fig, path, title, strict=False):
    """落盘前三道量: 标签全 ASCII、标题/图注没被画布裁掉、图面内容都在画布内。"""
    texts = _all_texts(fig)
    nonascii = sorted(set(ch for t in texts for ch in t if ord(ch) > 127))
    if nonascii:
        raise AssertionError("图面标签含非 ASCII 字形 %s (%s) —— DejaVu Sans "
                             "会把它画成方框而脚本一路绿"
                             % ("".join(nonascii), path))
    # 先按「留 4%% 底边给数据来源那行」收紧版面, 再量内容包围盒。
    # 不加这一步的话标题常被画布顶掉, 而 savefig 一路绿。
    fig.tight_layout(rect=(0, 0.045, 1, 0.995))
    fig.canvas.draw()
    tb = fig.get_tightbbox(fig.canvas.get_renderer())
    bb = fig.bbox
    tol = 1.0
    if tb.x0 < bb.x0 - tol or tb.y0 < bb.y0 - tol or \
       tb.x1 > bb.x1 + tol or tb.y1 > bb.y1 + tol:
        raise AssertionError("%s: 内容包围盒 (%.1f..%.1f, %.1f..%.1f) 超出画布 "
                             "(%.1f..%.1f, %.1f..%.1f) —— 标题或图注会被裁掉"
                             % (path, tb.x0, tb.x1, tb.y0, tb.y1,
                                bb.x0, bb.x1, bb.y0, bb.y1))
    # 那条像素比英寸的判据是**结构性失明**的 (见 _inside_inches 的说明): 它只挡得住
    # 越出 ~1 英寸以上的 gross 裁剪, 所以旧的六张图从来没有被真正量过。这里把严格量
    # 无条件接上 —— 「只给新增的两张开」等于把六张已交付的图继续留在无人测量状态,
    # 而 fig_f 一开就报出左右各 0.22/0.87 英寸的真实裁剪。
    _inside_inches(tb, fig, path)
    fig.savefig(path, dpi=150, metadata=PNG_META)
    plt.close(fig)
    return path


def src_note(*paths):
    return "Source: " + " + ".join(p.replace(os.sep, "/") for p in paths)


def fig_ops():
    """(a) 单算子: 周期 / 退休指令 / 每 MAC 周期 / CPI 两档对比。"""
    b = BENCH
    fig, axes = plt.subplots(1, 2, figsize=(9.2, 3.6))
    names = ["cycles", "retired instr"]
    ref = [b["ref_cycles"], b["ref_instret"]]
    epv = [b["epv_cycles"], b["epv_instret"]]
    x = range(len(names))
    axes[0].bar([i - 0.2 for i in x], ref, 0.38, label="C baseline (-O2)")
    axes[0].bar([i + 0.2 for i in x], epv, 0.38, label="EPV")
    for i, v in enumerate(ref):
        axes[0].text(i - 0.2, v * 1.03, num(v, "results/bench/nn_bench.json"),
                     ha="center", fontsize=8)
    for i, v in enumerate(epv):
        axes[0].text(i + 0.2, v * 1.03, num(v, "results/bench/nn_bench.json"),
                     ha="center", fontsize=8)
    axes[0].set_yscale("log")
    axes[0].set_xticks(list(x))
    axes[0].set_xticklabels(names)
    axes[0].set_title("3x3x16->4 INT8 conv, %s MACs" %
                      num(b["macs"], "results/bench/nn_bench.json"), pad=8)
    # Keep the legend in a dedicated band above the axes; placing it inside
    # the plot covered the tall baseline bars and their value labels.
    axes[0].legend(fontsize=8, loc="lower right", bbox_to_anchor=(1.0, 1.12),
                   borderaxespad=0.0, framealpha=0.92)
    names2 = ["cycles / MAC", "CPI"]
    r2 = [b["cyc_per_mac_ref"], b["ref_cpi"]]
    e2 = [b["cyc_per_mac_epv"], b["epv_cpi"]]
    axes[1].bar([i - 0.2 for i in range(2)], r2, 0.38, label="C baseline (-O2)")
    axes[1].bar([i + 0.2 for i in range(2)], e2, 0.38, label="EPV")
    for i, v in enumerate(r2):
        axes[1].text(i - 0.2, v * 1.03, num(("%.4g" % v),
                     "results/bench/nn_bench.json"), ha="center", fontsize=8)
    for i, v in enumerate(e2):
        axes[1].text(i + 0.2, v * 1.03, num(("%.4g" % v),
                     "results/bench/nn_bench.json"), ha="center", fontsize=8)
    axes[1].set_xticks(range(2))
    axes[1].set_xticklabels(names2)
    axes[1].set_title("per-MAC cost and CPI", pad=8)
    axes[1].legend(fontsize=8, loc="lower right", bbox_to_anchor=(1.0, 1.12),
                   borderaxespad=0.0, framealpha=0.92)
    fig.suptitle("(a) single operator: cycles, instructions, per-MAC cost")
    fig.text(0.5, 0.005, src_note("results/bench/nn_bench.json"),
             ha="center", fontsize=7)
    return _finish(fig, os.path.join(_OUT, "fig_a_operator_bars.png"), "a")


def fig_layers():
    """(b) 端到端网络逐层耗时。"""
    n = NET
    layers = ["conv_l1", "conv_l2", "pool_fc"]
    fig, ax = plt.subplots(figsize=(9.2, 3.6))
    ref = [n["per_layer_cycles"][L]["ref"] for L in layers]
    epv = [n["per_layer_cycles"][L]["epv"] for L in layers]
    for i, L in enumerate(layers):
        ax.bar(i - 0.2, ref[i], 0.38, label="C baseline" if i == 0 else "")
        ax.bar(i + 0.2, epv[i], 0.38, label="EPV" if i == 0 else "")
        ax.text(i - 0.2, ref[i] * 1.02, num(ref[i], "results/bench/nn_net.json"),
                ha="center", fontsize=8)
        ax.text(i + 0.2, epv[i] * 1.02, num(epv[i], "results/bench/nn_net.json"),
                ha="center", fontsize=8)
    ax.set_yscale("log")
    ax.set_xticks(range(len(layers)))
    ax.set_xticklabels(layers)
    ax.set_ylabel("cycles")
    ax.set_title("(b) end-to-end net, per-layer cycles (%s MACs, %sx, "
                 "bit-exact per layer)" %
                 (num(n["macs_total"], "results/bench/nn_net.json"),
                  num(n["speedup"], "results/bench/nn_net.json")))
    ax.legend(fontsize=8)
    fig.text(0.5, 0.005, src_note("results/bench/nn_net.json"),
             ha="center", fontsize=7)
    return _finish(fig, os.path.join(_OUT, "fig_b_layer_stack.png"), "b")


def fig_device():
    """(c) 国产器件两档资源: LUT / FF / DSP。"""
    global PNR
    PNR = RA.pnr_load()
    syn, par, c = RA.pnr_arms()
    a = dict((lab, val) for lab, val, _ctx in RA.pnr_anchors())
    fig, axes = plt.subplots(1, 3, figsize=(9.6, 3.4))
    for ax, key, ttl in ((axes[0], "total_luts", "#Total_luts (synthesis)"),
                         (axes[1], "ff", "FF"),
                         (axes[2], "dsp", "DSP")):
        e, b = syn["epv"][key], syn["baseline"][key]
        ax.bar([0, 1], [b, e], 0.5)
        ax.set_xticks([0, 1])
        ax.set_xticklabels(["no EPV", "EPV"])
        ax.set_title(ttl, fontsize=9)
        ax.text(0, b * 1.02, num(b, "results/pnr/summary2.json"), ha="center", fontsize=8)
        ax.text(1, e * 1.02, num(e, "results/pnr/summary2.json"), ha="center", fontsize=8)
    axes[2].set_ylim(0, PNR["device_capacity_from_report_area"]["dsp"] * 1.3)
    axes[2].axhline(PNR["device_capacity_from_report_area"]["dsp"], ls="--", lw=1)
    # 注释放在线上方偏左: 第一版写在右端, 与 EPV 那根柱子的读数「28」叠在一起,
    # 图照样出得来 —— 所以这是一条要靠看画面才发现得了的毛病。
    axes[2].text(0.0, PNR["device_capacity_from_report_area"]["dsp"] * 1.04,
                 "die limit", fontsize=7, ha="left")
    fig.suptitle("(c) Anlogic EG4S20 resources: net increase %s LUT / %s FF / "
                 "%s DSP; binding constraint is DSP" %
                 (a["TD LUT 净增"], a["TD FF 净增"],
                  "%d" % syn["delta"]["dsp_abs"]))
    for lab in ("TD LUT 净增", "TD FF 净增"):
        PROV.append((a[lab], "results/pnr/summary2.json"))
    fig.text(0.5, 0.005, src_note("results/pnr/summary2.json"),
             ha="center", fontsize=7)
    return _finish(fig, os.path.join(_OUT, "fig_c_device_resources.png"), "c")


def fig_waterfall():
    """(d) 加速比折损瀑布: 周期加速 -> 布线后频率折损 -> 净墙钟。"""
    syn, par, c = RA.pnr_arms()
    a = dict((lab, val) for lab, val, _ctx in RA.pnr_anchors())
    cyc = float(BENCH["speedup"])
    fr = float(par["epv"]["fmax_mhz"]) / float(par["baseline"]["fmax_mhz"])
    net = cyc * fr
    if abs(net - float(a["净墙钟加速"])) > 5e-3:
        raise AssertionError("瀑布图的净加速 %.4f 与门禁锚点 %s 不符" % (net, a["净墙钟加速"]))
    fig, ax = plt.subplots(figsize=(9.2, 3.8))
    ax.bar([0, 2], [cyc, net], 0.5)
    ax.plot([0.25, 1.75], [cyc, cyc], ls=":", lw=1)
    ax.annotate("Fmax cost %s%% after P&R"
                % num("%.2f" % abs(par["delta"]["fmax_pct"]),
                      "results/pnr/summary2.json"),
                xy=(1.0, (cyc + net) / 2.0), ha="center", fontsize=9)
    ax.text(0, cyc * 1.02, num("%.2f" % cyc, "results/bench/nn_bench.json"),
            ha="center", fontsize=9)
    ax.text(2, net * 1.02, num(a["净墙钟加速"], "results/pnr/summary2.json"),
            ha="center", fontsize=9)
    for v in (par["epv"]["fmax_mhz"], par["baseline"]["fmax_mhz"]):
        num("%.3f" % v, "results/pnr/summary2.json")
    ax.set_xticks([0, 2])
    ax.set_xticklabels(["%.2fx cycle speedup\n(ideal 1-cycle memory)" % cyc,
                        "%.2fx net wall-clock\n(at routed Fmax)" % net], fontsize=8)
    ax.set_ylabel("speedup (x)")
    # 标题里**不许手写** 15.73 这种数: 手抄的那份会跟归档漂, 而这条正是评审
    # 给 ④ 档记的第二号问题。数值从归档现取再印回标题。
    ax.set_title("(d) where the %sx cycle gain goes: routed-Fmax cost, "
                 "leaving %sx net wall clock" %
                 (num("%.2f" % cyc, "results/bench/nn_bench.json"),
                  num(a["净墙钟加速"], "results/pnr/summary2.json")))
    ax.set_ylim(0, cyc * 1.2)
    ax.set_xlim(-0.6, 2.6)
    fig.text(0.5, 0.005, src_note("results/bench/nn_bench.json",
                                  "results/pnr/summary2.json"),
             ha="center", fontsize=7)
    return _finish(fig, os.path.join(_OUT, "fig_d_speedup_waterfall.png"), "d")


def fig_compliance():
    """(e) 官方 riscv-tests 通过数与逐条轨迹一致数。"""
    fig, ax = plt.subplots(figsize=(9.2, 3.2))
    rows = []
    for ui in ("rv32ui", "rv32um", "rv32mi"):
        d = RA.jload(os.path.join("results", "compliance", ui + ".json"))
        rows.append((ui, d["n_pass"], d["n_tests"]))
    isco = {}
    for ui in ("rv32ui", "rv32um", "rv32mi"):
        p = os.path.join("results", "isco", ui + ".json")
        if os.path.isfile(p):
            isco[ui] = RA.jload(p)["n_agree"]
    y = range(len(rows))
    ax.barh([i - 0.18 for i in y], [r[2] for r in rows], 0.34, label="tests in suite")
    ax.barh([i + 0.18 for i in y], [r[1] for r in rows], 0.34, label="PASS on RTL")
    for i, (ui, p, n) in enumerate(rows):
        ax.text(p + 0.4, i + 0.18, num("%d / %d" % (p, n),
                    "results/compliance/%s.json" % ui), va="center", fontsize=8)
        if ui in isco:
            # Keep the long trace-agreement sentence outside the bars.  The
            # former centered placement ran through the colored bars and
            # made the numbers unreadable.
            ax.text(n + 0.8, i, "golden-ISS trace identical: %s of %s" %
                    (num(isco[ui], "results/isco/%s.json" % ui),
                     num(n, "results/isco/%s.json" % ui)),
                    fontsize=7, color="0.35", va="center", ha="left")
    ax.set_yticks(list(y))
    ax.set_yticklabels([r[0] for r in rows])
    # Reserve a right-hand text column for the trace-agreement annotations.
    ax.set_xlim(0, max(r[2] for r in rows) + 24)
    ax.set_xlabel("official riscv-tests cases")
    # Put the key in a dedicated band above the axes.  The trace-agreement
    # labels occupy the bars themselves, so every in-axes corner can overlap
    # data on one of the three rows.
    ax.set_title("(e) compliance and golden-ISS trace agreement", pad=28)
    ax.legend(fontsize=8, loc="lower right", bbox_to_anchor=(1.0, 1.02),
              borderaxespad=0.0, framealpha=0.92, ncol=2)
    fig.text(0.5, 0.005, src_note(*["results/compliance/%s.json" % r[0] for r in rows]),
             ha="center", fontsize=7)
    return _finish(fig, os.path.join(_OUT, "fig_e_compliance.png"), "e")


def fig_area():
    """(f) Yosys 通用单元口径: 核 / 关 EPV / EPV 单独 + 那条 +10 残差。"""
    s = SYNTH
    cw, cn, eo = (s["arms"]["core_with_epv"]["cells"],
                  s["arms"]["core_no_epv"]["cells"],
                  s["arms"]["epv_unit_only"]["cells"])
    ov = s["epv_net_overhead_cells"]
    resid = ov - eo
    fig, ax = plt.subplots(figsize=(9.2, 3.4))
    names = ["core (EPV on)", "core (EPV off)", "EPV unit alone"]
    vals = [cw, cn, eo]
    ax.bar(range(3), vals, 0.5)
    for i, v in enumerate(vals):
        ax.text(i, v * 1.02, num(v, "results/synth/summary.json"), ha="center", fontsize=9)
    ax.set_xticks(range(3))
    ax.set_xticklabels(names, fontsize=8)
    ax.set_ylabel("generic cells")
    ax.set_title("(f) Yosys cell counts: net EPV overhead %s cells\n"
                 "residual vs the standalone unit = %s "
                 "(what the EX-freeze shortcut bought)"
                 % (num(ov, "results/synth/summary.json"),
                    num(abs(resid), "results/synth/summary.json")),
                 fontsize=9)
    fig.text(0.5, 0.005, src_note("results/synth/summary.json"),
             ha="center", fontsize=7)
    return _finish(fig, os.path.join(_OUT, "fig_f_yosys_area.png"), "f", strict=True)


def fig_cifar():
    """(g) CIFAR-10 真数据: 部署口径 int8 top-1 vs 同网络浮点 top-1 + 在环整网加速比。

    两条纪律:
      * 精度一律用归档自己的打印格式 (0.4548 这种小数)。评审建议书写的是
        「45.48%」, 但 `45.48` 这个串在两份归档里**都不存在** —— 把它换算成
        百分数上图就是无出处读数, 所以图上留小数, 百分数说法交给正文。
      * 两份归档各算各的 top-1 (torch/numpy 侧 vs 在环导出侧), 先要求它们互等,
        不等就停手 —— 图上画的是「同一个网络的同一份成绩」, 不是两个来源的平均。
    """
    ts = CIFNET["test_set_accuracy"]
    if ts["top1_int8"] != CIF["top1_int8"]:
        raise AssertionError("两份归档的 int8 top-1 互等不成立 (cifar10_int8.top1_int8=%r "
                             "vs cifar10_net.test_set_accuracy.top1_int8=%r) —— 图面画的"
                             "是「同一个网络的同一份 int8 成绩」, 不是两个来源的平均"
                             % (CIF["top1_int8"], ts["top1_int8"]))
    if ts["top1_float"] != CIF["top1_float_numpy"]:
        raise AssertionError("两份归档的浮点 top-1 互等不成立 "
                             "(cifar10_int8.top1_float_numpy=%r vs "
                             "cifar10_net.test_set_accuracy.top1_float=%r)"
                             % (CIF["top1_float_numpy"], ts["top1_float"]))
    acc_i = avals(ARC_CIF, "top1_int8", [CIF["top1_int8"]])[0]
    acc_f = avals(ARC_CIF, "top1_float_numpy", [CIF["top1_float_numpy"]])[0]
    n_img = avals(ARC_CIF, "test_images", [CIF["test_images"]])[0]
    hit = avals(ARC_CIF, "top1_int8_hit", [CIF["top1_int8_hit"]])[0]
    loss = ashorts(ARC_CIF, "quantization_loss_pt", "%.2f",
                   [CIF["quantization_loss_pt"]])[0]
    # 同一个数在另一份归档里也必须逐字对得上 (键名不同: 那边叫 top1_float)
    if avals(ARC_CIFNET, "top1_int8", [ts["top1_int8"]])[0] != acc_i:
        raise AssertionError("%s 与 %s 的 top1_int8 打印串不一致" % (ARC_CIF, ARC_CIFNET))
    sp = avals(ARC_CIFNET, "speedup", [CIFNET["speedup"]])[0]
    rc = avals(ARC_CIFNET, "ref_cycles", [CIFNET["ref_cycles"]])[0]
    ec = avals(ARC_CIFNET, "epv_cycles", [CIFNET["epv_cycles"]])[0]
    nsamp = avals(ARC_CIFNET, "nsamp", [CIFNET["nsamp"]])[0]

    drawn = []

    def put(tok, arc):
        drawn.append(str(tok))
        return num(tok, arc)

    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.6))
    accs = [CIF["top1_int8"], CIF["top1_float_numpy"]]
    ax = axes[0]
    ax.bar([0, 1], accs, 0.5)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["int8 (deployed)", "float (same net)"], fontsize=8)
    ax.set_ylim(0, CIF["top1_float_numpy"] * 1.24)
    ax.set_ylabel("top-1 accuracy (fraction, n=%s test images)"
                  % put(n_img, ARC_CIF), fontsize=8)
    for i, (v, t) in enumerate(zip(accs, [acc_i, acc_f])):
        ax.text(i, v * 1.015, put(t, ARC_CIF), ha="center", fontsize=9)
    ax.text(0.02, 0.985, "int8 hits %s / %s\nquantization loss %s pt"
            % (put(hit, ARC_CIF), put(n_img, ARC_CIF), put(loss, ARC_CIF)),
            transform=ax.transAxes, va="top", fontsize=7.5, color="0.2")
    ax.set_title("(g1) real CIFAR-10 top-1", fontsize=9)

    ax = axes[1]
    cyc = [CIFNET["ref_cycles"], CIFNET["epv_cycles"]]
    ax.bar([0, 1], cyc, 0.5)
    ax.set_yscale("log")
    ax.set_ylim(min(cyc) * 0.25, max(cyc) * 9.0)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["scalar C baseline", "EPV in-the-loop"], fontsize=8)
    ax.set_ylabel("cycles (log)", fontsize=8)
    for i, (v, t) in enumerate(zip(cyc, [rc, ec])):
        ax.text(i, v * 1.6, put(t, ARC_CIFNET), ha="center", fontsize=8)
    ax.set_title("(g2) in-loop speedup %sx on %s images"
                 % (put(sp, ARC_CIFNET), put(nsamp, ARC_CIFNET)), fontsize=9)

    fig.suptitle("(g) CIFAR-10: int8 top-1 vs same-network float, "
                 "and the in-loop speedup")
    fig.text(0.5, 0.005, src_note(ARC_CIF, ARC_CIFNET), ha="center", fontsize=7)
    p = os.path.join(_OUT, "fig_g_cifar10_accuracy.png")
    _assert_drawn(fig, drawn, p)
    return _finish(fig, p, "g", strict=True)


def fig_memcurve():
    """(h) 访存代价折损曲线: 横轴 W (每整字总线访问多付的拍数), 两条臂都取
    **悲观界** (EPV 侧字数上界), 于是这条曲线说的是「再怎么罚也还剩多少」。

    取值走 avals(): 两条臂的 speedup_pessimistic 各有 6 个读数、全都逐字躺在同一份
    归档里, 只有按「键 → 切片」定位才能保证图上这条线真的是「只罚数据」那条。
    """
    if MEMS.get("gate") != "PASS":
        raise AssertionError("%s 的归档判定是 %r 而不是 PASS —— 不过判据的读数不许上图"
                             % (ARC_MEM, MEMS.get("gate")))
    cases = MEMS.get("cases") or []
    if len(cases) != 1 or MEMS.get("gate_cases") != 1:
        raise AssertionError("本图只画一条曲线的用例, 归档现在给 %d 条 (gate_cases=%r)。"
                             "访存曲线要补盖 nn_net/nn_cifar 时先把本函数按用例分面扩开, "
                             "不许只画第一条就当全曲线交付" % (len(cases), MEMS.get("gate_cases")))
    c = cases[0]
    wp = list(MEMS["w_points"])
    data, fetch = c["curve_data_only"], c["curve_data_plus_fetch"]
    for arm, cv in (("curve_data_only", data), ("curve_data_plus_fetch", fetch)):
        if [r["wait_states"] for r in cv] != wp:
            raise AssertionError("%s 的 wait_states 序列 %s 对不上 w_points %s"
                                 % (arm, [r["wait_states"] for r in cv], wp))
        seq = [r["speedup_pessimistic"] for r in cv]
        if any(seq[i + 1] > seq[i] + 1e-9 for i in range(len(seq) - 1)):
            raise AssertionError("%s 的悲观界不降反升 —— 与 tools/mem_sensitivity.py "
                                 "的同名判据相反, 先核对归档再画图" % arm)
    sl_d = dict(after="curve_data_only", before="curve_data_plus_fetch")
    sl_f = dict(after="curve_data_plus_fetch", before="zero_point_ok")
    ws_d = avals(ARC_MEM, "wait_states", [r["wait_states"] for r in data], **sl_d)
    pes_d = avals(ARC_MEM, "speedup_pessimistic",
                  [r["speedup_pessimistic"] for r in data], **sl_d)
    ws_f = avals(ARC_MEM, "wait_states", [r["wait_states"] for r in fetch], **sl_f)
    pes_f = avals(ARC_MEM, "speedup_pessimistic",
                  [r["speedup_pessimistic"] for r in fetch], **sl_f)
    if ws_d != ws_f:
        raise AssertionError("两条臂不在同一个 W 网格上 (%s vs %s) —— 不能画成同一条横轴"
                             % (ws_d, ws_f))
    if [str(w) for w in wp] != ws_d:
        raise AssertionError("w_points 的打印串 %s 与曲线里的 wait_states %s 不一致"
                             % ([str(w) for w in wp], ws_d))
    if pes_d[0] != pes_f[0]:
        raise AssertionError("W=0 那点两条臂本该重合 (%s vs %s), 归档里 zero_point_ok "
                             "说的就是这件事" % (pes_d[0], pes_f[0]))
    sa = avals(ARC_MEM, "speedup_archive", [c["measured"]["speedup_archive"]])[0]
    gc = avals(ARC_MEM, "gate_cases", [MEMS["gate_cases"]])[0]
    case = c["case"]

    drawn = []

    def put(tok, arc):
        drawn.append(str(tok))
        return num(tok, arc)

    y_d = [r["speedup_pessimistic"] for r in data]
    y_f = [r["speedup_pessimistic"] for r in fetch]
    fig, ax = plt.subplots(figsize=(9.2, 3.9))
    ax.plot(wp, y_d, marker="o", lw=1.8,
            label="%s: data memory penalised only" % case)
    ax.plot(wp, y_f, marker="s", lw=1.8, ls="--",
            label="%s: data + instruction fetch penalised" % case)
    ax.set_xticks(wp)
    ax.set_xticklabels([put(t, ARC_MEM) for t in ws_d], fontsize=8)
    # 下界留够余量: 最低那个点的标签是往下的, 余量不够它就掉进刻度行里
    # (实测 0.80 时 W=16 那条 5.1204 与横轴刻度 16 叠在一起, 而判据一路绿)。
    ax.set_ylim(min(y_d + y_f) * 0.62, max(y_f) * 1.16)
    ax.set_xlabel("wait states W added to every full-word bus access")
    ax.set_ylabel("cycle speedup (x), pessimistic EPV bound")
    for i, (x, y) in enumerate(zip(wp, y_d)):
        if i == 0:
            continue
        ax.annotate(put(pes_d[i], ARC_MEM), (x, y), textcoords="offset points",
                    xytext=(0, -14), ha="center", fontsize=7.5)
    for i, (x, y) in enumerate(zip(wp, y_f)):
        if i == 0:
            continue
        ax.annotate(put(pes_f[i], ARC_MEM), (x, y), textcoords="offset points",
                    xytext=(0, 8), ha="center", fontsize=7.5)
    ax.annotate("both arms at W=%s: %sx" % (put(ws_d[0], ARC_MEM),
                put(pes_d[0], ARC_MEM)), xy=(wp[0], y_d[0]),
                textcoords="offset points", xytext=(12, 4), ha="left", fontsize=7.5)
    # The curves and their point labels fill the plotting area, so keep the
    # legend above the axes instead of over the data.  Extra title padding
    # leaves a separate band for the legend after tight_layout().
    ax.legend(fontsize=7.5, loc="lower center", bbox_to_anchor=(0.5, 1.03),
              ncol=2, borderaxespad=0.0, framealpha=0.92)
    ax.set_title("(h) memory penalty: %sx at W=%s drops to %sx at W=%s on data-only "
                 "memory, while the fetch-penalised arm flattens at %sx"
                 % (put(pes_d[0], ARC_MEM), put(ws_d[0], ARC_MEM),
                    put(pes_d[-1], ARC_MEM), put(ws_d[-1], ARC_MEM),
                    put(pes_f[-1], ARC_MEM)), fontsize=8.5, pad=28)
    fig.text(0.5, 0.005, src_note(ARC_MEM) +
             " | curve case %s, gate_cases=%s; W=%s reproduces the archived speedup %s; "
             "cases marked UNAVAILABLE in that archive are not drawn"
             % (case, put(gc, ARC_MEM), put(ws_d[0], ARC_MEM), put(sa, ARC_MEM)),
             ha="center", fontsize=6.5)
    p = os.path.join(_OUT, "fig_h_mem_penalty_curve.png")
    _assert_drawn(fig, drawn, p)
    return _finish(fig, p, "h", strict=True)


FIGS = [("fig_a_operator_bars.png", fig_ops),
        ("fig_b_layer_stack.png", fig_layers),
        ("fig_c_device_resources.png", fig_device),
        ("fig_d_speedup_waterfall.png", fig_waterfall),
        ("fig_e_compliance.png", fig_compliance),
        ("fig_f_yosys_area.png", fig_area),
        ("fig_g_cifar10_accuracy.png", fig_cifar),
        ("fig_h_mem_penalty_curve.png", fig_memcurve)]



def sha(p):
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# 图面标签全 ASCII (DejaVu Sans 没有中文字形), 中文解释与数据来源就记在这里。
# 这些串不进图、也不参与任何按数字核出处的门禁 (它们只扫四份交付件正文), 所以
# 只写「这条曲线是什么 + 出自哪份归档」, 不写读数 —— 写了就会漂在归档前面。
FIG_LEGENDS = {
    "fig_a_operator_bars.png": u"单个算子 (3x3x16 到 4 的 int8 卷积) 的两臂对比: 周期数、"
                               u"退休指令数、每 MAC 周期与 CPI。出处 results/bench/nn_bench.json。",
    "fig_b_layer_stack.png": u"端到端网络逐层周期数 (未训练权重, 逐层 int8 输出逐字节相同)。"
                             u"出处 results/bench/nn_net.json。",
    "fig_c_device_resources.png": u"国产器件 (安逻辑 EG4S20) 综合与布线后资源两档对比, "
                                  u"绑定约束是 DSP。出处 results/pnr/summary2.json。",
    "fig_d_speedup_waterfall.png": u"周期加速比如何被布线后 Fmax 折损成净墙钟加速。"
                                   u"出处 results/bench/nn_bench.json 与 results/pnr/summary2.json。",
    "fig_e_compliance.png": u"官方 riscv-tests 三套件通过数与黄金 ISS 轨迹逐条一致数。"
                            u"出处 results/compliance/*.json 与 results/isco/*.json。",
    "fig_f_yosys_area.png": u"Yosys 通用单元口径的核面积: 开扩展/关扩展/扩展单元单独, "
                            u"以及净开销对单独单元的残差。出处 results/synth/summary.json。",
    "fig_g_cifar10_accuracy.png": u"真实 CIFAR-10 测试集上的 top-1: 左格是部署口径 int8 与"
                                  u"**同一个网络**的浮点读数 (纵轴用归档自己的小数格式, "
                                  u"0.4548 即 45.48%; 归档里没有 45.48 这个串, 所以图面不换算), "
                                  u"右格是同一套 int8 权重在核内周期精确仿真下的整网加速比 "
                                  u"(基线标量 C 对 EPV 两臂周期)。两份归档的 top-1 先要求互等再上图。"
                                  u"出处 results/bench/cifar10_int8.json 与 "
                                  u"results/bench/cifar10_net.json。",
    "fig_h_mem_penalty_curve.png": u"访存代价折损曲线: 横轴 W 是每个整字总线访问多付的拍数 "
                                   u"(0/1/2/4/8/16), 纵轴取 EPV 侧字数**上界**对应的加速比 "
                                   u"(悲观界, 即「再怎么罚也还剩多少」)。实线只罚数据内存, "
                                   u"虚线同时罚取指; 两条臂在 W=0 重合于归档加速比, 只罚数据那条 "
                                   u"随 W 单调塌到最坏角, 同时罚取指那条在 W>=4 之后基本走平 "
                                   u"(基线臂字数远多于 EPV 臂, 加罚反而拉大比值)。单周期理想存储 "
                                   u"只是曲线左端点, 不是一条结论。出处 results/bench/mem_sensitivity.json。",
}


def build(out_dir=None, write_registry=True, quiet=False):
    global _OUT
    _OUT = out_dir or FIG_DIR
    os.makedirs(_OUT, exist_ok=True)
    # 登记簿是「整本先删后写」, 所以图名唯一性是它唯一的去重依据。重复的名字不会
    # 报错, 只会给同一张图写下两个 sha256 —— 而「这张图现在的哈希是多少」必须是单值。
    names = [n for n, _f in FIGS]
    dup = sorted(set(n for n in names if names.count(n) > 1))
    if dup:
        raise AssertionError("FIGS 里图名重复: %s —— 登记簿按图名去重, 重复会让同一张图"
                             "有两个 sha256, 先修表再出图" % dup)
    missing_leg = sorted(set(names) ^ set(FIG_LEGENDS))
    if missing_leg:
        raise AssertionError("图名与中文说明的集合不一致 (图面只许 ASCII, 解释只能记在"
                             "登记簿里, 所以两张表必须一模一样): %s" % missing_leg)
    made = []
    for name, fn in FIGS:
        path = os.path.join(_OUT, name)
        fn()
        made.append(name)
    bad = check_provenance()
    if bad:
        raise AssertionError("图上有没出处的数字: %s" % bad[:8])
    reg = {"figures": [{"file": f,
                        "sha256": sha(os.path.join(_OUT, f))} for f in made],
           "figure_legends": FIG_LEGENDS,
           "numbers_on_figures": len(PROV),
           "provenance_rule": "each printed value is either verbatim in the named "
                              "archive or equals a recomputed doc-gate anchor; fig_g "
                              "and fig_h additionally tie every printed token to the "
                              "archive key it was sliced out of (avals/ashorts)",
           "generator": "flow/make_figures.py"}
    if write_registry:
        with io.open(REGISTRY, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(reg, ensure_ascii=False, indent=1,
                                sort_keys=True) + "\n")
    if not quiet:
        print("FIGURES-BUILT %d  numbers=%d  registry=%s"
              % (len(made), len(PROV), REGISTRY.replace(os.sep, "/")))
    return 0


def check():
    """只核不写: 登记簿 ↔ 磁盘 ↔ 重跑三方一致, 报告引用与图文件双向差集。

    三条各挡一种静默失效:
      1. 盘上那份图与登记簿对不上，或对得上但和重画的不同 —— 这里分两种成因分开报:
         重画两次就不一样 = 生成器不确定，登记簿的 sha 是假键；重画两次一样而与
         盘上不同 = 图（连同登记簿那一行 sha）属于旧归档，要重画而不是改生成器;
      2. 磁盘上有图没登记 / 登记了没在磁盘  => 有人手加或手删了一张图;
      3. 报告引了不存在的图 / 有图从没被任何正文引用(孤儿)  => 评审点进去找不到,
         或者我们以为交付了其实没交。
    """
    bad = []
    if not os.path.isfile(REGISTRY):
        raise AssertionError("没有登记簿 %s —— 先跑 python flow/make_figures.py" % REGISTRY)
    with io.open(REGISTRY, encoding="utf-8") as fh:
        reg = json.load(fh)
    tmp = tempfile.mkdtemp(prefix="figchk_")
    tmp2 = tempfile.mkdtemp(prefix="figchk2_")
    try:
        build(out_dir=tmp, write_registry=False, quiet=True)
        # 第二发不是多余的：「盘上图 != 重画的那份」有两种成因，红因指错地方会让人
        # 去改生成器（其实图过期了）或者去重画图（其实生成器不确定）。本轮实测就是
        # 前者 —— TD 换代之后 fig_c/fig_d 属于旧归档，而旧文案把这一格一律报成
        # 「图里藏了时间戳」。所以先把「重画两次是否逐字节相同」量出来再判。
        build(out_dir=tmp2, write_registry=False, quiet=True)
        for row in reg["figures"]:
            a = os.path.join(FIG_DIR, row["file"])
            b = os.path.join(tmp, row["file"])
            c = os.path.join(tmp2, row["file"])
            if not os.path.isfile(a):
                bad.append(("登记簿有、磁盘没有", row["file"]))
                continue
            det = sha(b) == sha(c)
            if sha(a) != row["sha256"]:
                bad.append(("图与登记簿的 sha 不符(图被手改或归档变了)", row["file"]))
            elif not det:
                bad.append(("重画两次就不一样(生成器不确定，登记簿的 sha 是假键)",
                            row["file"]))
            elif sha(a) != sha(b):
                bad.append(("盘上图与登记簿这一对属于旧归档(生成器可复现)",
                            row["file"]))
        on_disk = sorted(f for f in os.listdir(FIG_DIR) if f.endswith(".png"))
        listed = sorted(r["file"] for r in reg["figures"])
        if on_disk != listed:
            bad.append(("磁盘与登记簿集合不一致", sorted(set(on_disk) ^ set(listed))))
        with io.open(RA.REPORT, encoding="utf-8") as fh:
            rtxt = fh.read()
        refs = sorted(set(re.findall(r"([A-Za-z0-9_.-]+\.png)", rtxt)))
        missing = [r for r in refs if not os.path.isfile(os.path.join(FIG_DIR, r))
                   and not os.path.isfile(os.path.join(ROOT, r))]
        if missing:
            bad.append(("报告引了不存在的图", missing))
        orphans = [f for f in listed
                   if f not in rtxt and f not in io.open("README.md", encoding="utf-8").read()]
        if orphans:
            bad.append(("图从没被任何正文引用(孤儿)", orphans))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if bad:
        for item in bad:
            print("FIG-CHECK-BAD %s %s" % (item[0], item[1]))
        raise AssertionError("附图门红: %d 项" % len(bad))
    print("FIGURES_CHECK_OK figures=%d numbers=%d"
          % (len(reg["figures"]), reg["numbers_on_figures"]))
    return 0


def _hits(exc_msg, needle):
    """原因比对: 判据消息是中文而父进程按 UTF-8 解子进程输出、控制台又是 GBK, 所以
    打印一律走 ascii() 转义 —— 比对时也得能命中转义后的形态。"""
    return needle in exc_msg or ascii(needle)[1:-1] in ascii(exc_msg)


def selftest():
    """注入证明: 出处判据、键锚定取值、ASCII 判据、画布包围盒、图面标签存在性,
    每条都必须**点名开火**; 「有红」不算, 红在别处就等于这条判据是装饰。

    全部跑在临时目录里, 不碰 docs/附图/ 与登记簿; 判决行一律 ASCII。
    夹具排在真判决之后、但正对照排在所有夹具之前 (先证明干净时不开火)。
    """
    global _OUT
    keep = dict(_OUT=_OUT, CIF=CIF, CIFNET=CIFNET, MEMS=MEMS,
                src_note=src_note, _assert_drawn=_assert_drawn)
    reg_before = sha(REGISTRY) if os.path.isfile(REGISTRY) else None
    tmp = tempfile.mkdtemp(prefix="figself_")
    ct = [0, 0]                      # [ok, bad]

    def say(state, tag, reason):
        print("SELFTEST-%s %-22s %s" % (state, tag, reason))

    def ok(tag, reason):
        ct[0] += 1
        say("OK", tag, reason)

    def fail(tag, reason):
        ct[1] += 1
        say("FAIL", tag, reason)

    def must_raise(tag, needle, fn):
        try:
            fn()
        except AssertionError as exc:
            if _hits(str(exc), needle):
                ok(tag, ascii(str(exc))[:140])
            else:
                fail(tag, "raised but wrong reason, wanted substring %r, got %s"
                     % (needle, ascii(str(exc))[:140]))
            return
        except Exception as exc:               # noqa: BLE001 - 判据类型也要对
            fail(tag, "raised %s instead of AssertionError: %s"
                 % (type(exc).__name__, ascii(str(exc))[:140]))
            return
        fail(tag, "injection did NOT raise -- this judgement is decorative")

    def must_pass(tag, fn):
        try:
            fn()
        except Exception as exc:               # noqa: BLE001
            fail(tag, "positive control fired: %s" % ascii(str(exc))[:140])
            return
        ok(tag, "positive control, no fire")

    def patched(fn, pairs):
        """只在注入期间替换模块全局, 走之前一定还原 (归档只读, 改的是内存里那份)。"""
        def go():
            for gname, val in pairs:
                globals()[gname] = val
            try:
                fn()
            finally:
                for gname, _v in pairs:
                    globals()[gname] = keep[gname]
        return go

    try:
        _OUT = tmp
        # ---------------- 正对照: 两张新图干净地出得来, 读数全有出处
        PROV[:] = []
        fig_cifar()
        n_g = len(PROV)
        fig_memcurve()
        n_h = len(PROV) - n_g
        bad = check_provenance()
        # 条数下限按结构取, 不按今日读数取: (h) 至少每个 W 一个点标注 + 每个 W 一个刻度,
        # (g) 至少两条精度、两条周期、一个加速比。
        if bad:
            fail("clean_provenance", ascii(str(bad))[:160])
        elif n_g < 5 or n_h < 2 * len(MEMS["w_points"]):
            fail("clean_labels_drawn", "fig_g registered %d (want >=5), fig_h %d "
                 "(want >= %d)" % (n_g, n_h, 2 * len(MEMS["w_points"])))
        else:
            ok("clean_provenance", "fig_g=%d + fig_h=%d printed values all traceable"
               % (n_g, n_h))

        # 每张图都真的调了 _assert_drawn, 且核对的就是它注册进 PROV 的那些串
        calls = []

        def spy(fig, expect, path_hint):
            calls.append((os.path.basename(path_hint), len(expect)))
            return keep["_assert_drawn"](fig, expect, path_hint)
        globals()["_assert_drawn"] = spy
        PROV[:] = []
        fig_cifar()
        n_prov_g = len(PROV)
        fig_memcurve()
        n_prov_gh = len(PROV)
        globals()["_assert_drawn"] = keep["_assert_drawn"]
        if calls != [("fig_g_cifar10_accuracy.png", n_prov_g),
                     ("fig_h_mem_penalty_curve.png", n_prov_gh - n_prov_g)]:
            fail("labels_wired", "%s vs prov %s" % (calls, (n_prov_g, n_prov_gh)))
        else:
            ok("labels_wired", "both new figures check every registered token on canvas")

        # ---------------- 夹具: 改一位读数
        must_raise("digit_changed", "top1_int8",
                   patched(fig_cifar, [("CIF", dict(CIF, top1_int8=0.4549))]))

        def both_tampered():
            ts = dict(CIFNET["test_set_accuracy"], top1_int8=0.4549)
            return patched(fig_cifar, [
                ("CIF", dict(CIF, top1_int8=0.4549)),
                ("CIFNET", dict(CIFNET, test_set_accuracy=ts))])
        must_raise("key_anchored_tie", "top1_int8", both_tampered())

        def speedup_tampered():
            return patched(fig_cifar, [("CIFNET", dict(CIFNET, speedup=19.783))])
        must_raise("speedup_digit_changed", "speedup", speedup_tampered())

        # ---------------- 夹具: 两条臂对调 (每个数都还逐字在归档里, 只有键锚定抓得到)
        def arms_swapped():
            c0 = dict(MEMS["cases"][0])
            c0["curve_data_only"] = MEMS["cases"][0]["curve_data_plus_fetch"]
            c0["curve_data_plus_fetch"] = MEMS["cases"][0]["curve_data_only"]
            return patched(fig_memcurve, [("MEMS", dict(MEMS, cases=[c0]))])
        must_raise("arms_swapped", "speedup_pessimistic", arms_swapped())

        # ---------------- 夹具: 横轴网格与曲线不同源
        def short_grid():
            return patched(fig_memcurve, [("MEMS", dict(MEMS, w_points=MEMS["w_points"][:5]))])
        must_raise("w_grid_shortened", "wait_states", short_grid())

        def bent_arm():
            cv = [dict(r) for r in MEMS["cases"][0]["curve_data_only"]]
            cv[3]["speedup_pessimistic"] = cv[0]["speedup_pessimistic"]
            c0 = dict(MEMS["cases"][0], curve_data_only=cv)
            return patched(fig_memcurve, [("MEMS", dict(MEMS, cases=[c0]))])
        must_raise("pessimistic_rises", "curve_data_only", bent_arm())

        def gate_failed():
            return patched(fig_memcurve, [("MEMS", dict(MEMS, gate="FAIL"))])
        must_raise("archive_gate_not_pass", "mem_sensitivity", gate_failed())

        def two_cases():
            return patched(fig_memcurve, [("MEMS", dict(
                MEMS, cases=[MEMS["cases"][0], MEMS["cases"][0]], gate_cases=2))])
        must_raise("case_denominator", "gate_cases", two_cases())

        # ---------------- 夹具: 中文标签 / 标签被画布裁掉
        def with_note(mk):
            def go():
                globals()["src_note"] = mk
                try:
                    fig_memcurve()
                finally:
                    globals()["src_note"] = keep["src_note"]
            return go
        must_raise("cjk_label", "ASCII",
                   with_note(lambda *p: u"数据来源 " + u" + ".join(p)))
        must_raise("label_clipped_off_canvas", "fig_h_mem_penalty_curve.png",
                   with_note(lambda *p: "Source: " + "x" * 400))
        # 越界不到 1 英寸的那一档: 旧的松判据按像素比英寸 (920 vs 9.2), 看不见;
        # 严格判据必须看见。这里用合成包围盒做 A/B —— 真实标注的宽度依赖字形,
        # 拿它当阈值夹具会有一天悄悄不越界。
        def strict_case(x0, y0, x1, y1):
            f, _a = plt.subplots(figsize=(9.2, 3.9))
            try:
                _inside_inches(Bbox.from_bounds(x0, y0, x1 - x0, y1 - y0), f, "probe.png")
            finally:
                plt.close(f)
        must_raise("clipped_half_inch_right", "CLIPPED",
                   lambda: strict_case(0.15, 0.0, 9.70, 3.85))
        must_raise("clipped_half_inch_bottom", "CLIPPED",
                   lambda: strict_case(0.15, -0.50, 9.10, 3.85))
        must_pass("inside_canvas", lambda: strict_case(0.15, -0.011, 9.10, 3.85))

        # ---------------- 夹具: 图面少了一条读数 (标签被覆盖 / 取错了键)
        def probe_fig(text):
            f, a = plt.subplots(figsize=(4, 2))
            a.set_title("value " + text + " here")
            return f

        def dropped():
            _assert_drawn(probe_fig("0.4548"), ["0.4549"], "probe.png")
        must_raise("label_dropped", "probe.png", dropped)
        must_pass("label_present",
                  lambda: _assert_drawn(probe_fig("0.4548"), ["0.4548"], "probe.png"))

        # ---------------- 出处判据本体的 A/B (挡「随手改成 9.9」那一格)
        must_pass("policer_accepts_verbatim",
                  lambda: _require_clean([("0.4548", ARC_CIF), ("15.7343", ARC_MEM)]))
        must_pass("policer_accepts_faithful_round",
                  lambda: _require_clean([("-0.16", ARC_CIF)]))
        must_raise("policer_rejects_rounding", "0.4550",
                   lambda: _require_clean([("0.4550", ARC_CIF)]))
        must_raise("policer_rejects_foreign_value", "44937277",
                   lambda: _require_clean([("44937277", ARC_MEM)]))

        # ---------------- 副作用: 归档与登记簿一个字节都没动
        for gname, rel in (("CIF", ARC_CIF), ("CIFNET", ARC_CIFNET), ("MEMS", ARC_MEM)):
            if globals()[gname] != RA.jload(rel):
                fail("archive_untouched", "%s differs from %s after injections"
                     % (gname, rel))
            else:
                ok("archive_untouched", "%s still equals the file on disk" % rel)
        reg_after = sha(REGISTRY) if os.path.isfile(REGISTRY) else None
        if reg_after != reg_before:
            fail("registry_untouched", "selftest rewrote the registry")
        else:
            ok("registry_untouched", "registry.json byte-identical after selftest")
    finally:
        globals().update(keep)
        PROV[:] = []
        plt.close("all")
        shutil.rmtree(tmp, ignore_errors=True)

    verdict = "FIGURES_SELFTEST_%s cases=%d failures=%d" % (
        "OK" if ct[1] == 0 else "FAIL", ct[0] + ct[1], ct[1])
    print(verdict)
    return 1 if ct[1] else 0


def _require_clean(pairs):
    bad = check_provenance(pairs)
    if bad:
        raise AssertionError("provenance rejects %s" % ascii(str(bad))[:120])


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(check())
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(build())

