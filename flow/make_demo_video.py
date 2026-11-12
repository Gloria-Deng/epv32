#!/usr/bin/env python3
"""把 results/demo/ 里已归档的录屏素材编成一段演示片 (方案 Demo 的兜底件)。

赛题对方案 Demo 的硬指标是「时长 ≤10 分钟、文件 ≤100 MB、内容不涉密」。
本片**不是**人讲版：它不含真人出镜，解说音轨由 `flow/demo_narration.py` 用本机
合成语音读出（念的就是 `demo/演示脚本与讲稿.md` 的口播块，字幕与音轨同源），
画面是把 `flow/demo.sh` 那一趟跑出来的终端读数按时间轴回放。这么做只有一个理由：
让人讲版录制之前，包里有「每一个数字都能回溯到归档、且听得懂在讲哪一格」的成片，
评审不至于只看到一句"待录"。README 与设计报告 5.5 都按这个口径写，不许把它说成
演示讲解已完成；音轨有没有真装上，判据是 sidecar 的 `not` 列表与
`flow/check_demo_artifacts.py` 那条「声称有音轨就必须解得出非静音」的核对。

三条硬规矩（都落成 --selftest 里会咬的判据）：
 1) **屏幕上出现的每个数字，必须逐字存在于本次读到的归档/日志语料里**。
    数字是从 results/**.json 与 results/demo/*.txt 现取的，不抄报告正文 ——
    正文本身也是从这些归档生成的。造不出出处的数字直接拒绝出片。新加一块证据
    时的动作是**把语料扩到那份归档**（本轮的 cifar10_int8 / cifar10_net /
    mem_sensitivity 就是这么进来的），而不是放宽这条判据或往白名单里塞数；
    成片自己的那份运行日志反而是被**排除**在语料外的，因为它会回显自检里那条
    故意造的假数，让「造假数必须被抓」这条判据在第二趟空转。
 2) 时长与体积按赛题上限自检：>600 s 或 >100 MB 就不出片（而不是出了再压）。
 3) 中文字形必须真的画得出来。这台机器上 DejaVu 没有 CJK，字体选错会画成一排
    方框而脚本一路绿，所以自检里比较「中」与一个私有区码位的点阵是否相同。

用法:
    python flow/make_demo_video.py --dry        # 只排时间轴, 不编码
    python flow/make_demo_video.py              # 排 + 自检 + 编码 + 落 sidecar
    python flow/make_demo_video.py --selftest    # 五条注入 + 三条对照, 各报自己的原因
"""
import hashlib
import io
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(ROOT, "flow"))
from atomic_json import write_json_atomic                    # noqa: E402

DEMO = os.path.join("results", "demo")
OUT_MP4 = os.path.join(DEMO, "demo_video.mp4")
OUT_JSON = os.path.join(DEMO, "demo_video.json")
W, H, FPS = 1280, 720, 10
LIMIT_S, LIMIT_MB = 600.0, 100.0
# 目标时长：上一版排到 569 s，距赛题 600 s 上限只剩 31 s（5.2%），评审的 P1-9 点的
# 就是这种「合规但没有余量」的形状 —— 任何一屏内容变多都会顶破上限。压到 510 s 之后
# 余量 90 s（15%），而且解说音轨是按这个时长配的，二者由同一个 TARGET_S 导出。
TARGET_S = 510.0
LINE_H = 22
TOP_Y, BOT_Y = 78, 660                 # 正文可用区; 一屏放得下的行数:
CAP_LINES = (BOT_Y - TOP_Y) // LINE_H  # 超出就翻页, 不许静默截掉
FILL_FRAC = 0.45                       # 段内前多少时间用来"打字", 之后满屏
CAP_BAND_Y0, CAP_LINE_H = 664, 24      # 字幕条: 正文区到 660 为止, 664..720 是字幕
CAP_MAX_LINES = (H - CAP_BAND_Y0) // CAP_LINE_H   # 装不下就抛, 不许静默截掉半句
CAP_GEOM = []                            # 出片时填: [每行字数, 行数, 每页字数上限, 字宽]

# 本次运行里「印到画面上的每个承重读数」与它的出处（文件 + 归档键 + 字面打印串）。
# 它是成片与归档之间那条**内容级**同源判据的基础：哈希每趟都变（归档里有时间戳），
# 而 (文件, 键, 打印串) 三元组在同代 RTL 上必须逐字仍然成立。
KEY_READINGS = []
# 每页的字幕文本 = 该页要念的旁白（同一串字符既上屏又进音轨，不会出现两说一套）。
CAPTIONS = {}
CAP_STATS = {}

# 画面里允许出现的"结构性"数字: 它们描述的是形状/量程而不是测量结果。
# 白名单要窄 —— 往里塞 45/60/90 这类数就等于允许编造"60 倍加速"。
SHAPE_OK = {"0", "1", "2", "3", "4", "5", "7", "8", "9", "16", "31", "32",
            "64", "128", "256", "512", "1024", "4096", "65536",
            "2026"}                              # 报告封面的年份

CJK_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]
ASCII_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\consola.ttf",
    r"C:\Windows\Fonts\cour.ttf",
]


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()


def read(p):
    with io.open(p, encoding="utf-8", errors="replace") as f:
        return f.read()


def jload(p):
    return json.loads(read(p))


# --------------------------------------------------------------- 语料与取数
def corpus():
    """本次可引用的全部读数来源 (文件 -> 文本)。数字只能从这里来。"""
    # 成片**自己的**那份运行日志不进语料: 它逐行回显 --selftest 的输出, 而其中
    # 有一条故意造出来的假数 (注入用的 187.421)。让"造数必须被抓住"这条判据去
    # 读一份会回显假数的日志, 等于第二趟自检必然空转 —— 实测症状: 单独重跑
    # --selftest 时 fake-number 不报红了, 因为 187.421 此刻「确实在语料里」。
    want = [os.path.join(DEMO, f) for f in sorted(os.listdir(DEMO))
            if (f.endswith(".txt") or f.endswith(".log")) and "video" not in f]
    want += ["results/matrix.json", "results/bench/nn_bench.json",
             "results/bench/nn_net.json",
             "results/synth/summary.json",
             "results/pnr/summary2.json", "results/compliance/rv32ui.json",
             "results/compliance/rv32um.json", "results/isco/rv32ui.json",
             "results/isco/rv32um.json", "results/iss/selftest.json",
             "results/word_limits.json", "results/sim/misalign_bisect.json"]
    # 本轮新增的两块证据: 真实数据集那条链 (精度 + 在环周期) 与访存代价曲线。
    # 是把语料**扩大**到这些归档, 而不是放宽「屏上每个数字都要逐字在语料里」。
    want += ["results/bench/cifar10_int8.json", "results/bench/cifar10_net.json",
             "results/bench/mem_sensitivity.json"]
    have = {}
    for p in want:
        if os.path.isfile(p) and os.path.getsize(p):
            have[p.replace("\\", "/")] = read(p)
    return have


def lines_of(corpus, rel, keep=None, drop=()):
    """取某份产物的行 (去掉空行与 CR), 只留内容行。"""
    txt = corpus.get(rel, "")
    out = []
    for ln in txt.replace("\r\n", "\n").split("\n"):
        ln = ln.rstrip()
        if not ln:
            continue
        if any(d in ln for d in drop):
            continue
        out.append(ln)
    return (out[:keep] if keep else out)


def _dig(cur, parts, miss, k):
    """按点分路径取值, 支持字典键与列表下标 (cases.0.curve_data_only.5.speedup)。

    取不到就记进 miss 由调用方判红 —— 静默跳过会让整段读数从画面上消失而脚本
    一路绿 (见 json_lines 的说明)。
    """
    for part in parts:
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            cur = None
        if cur is None:
            miss.append(k)
            return None
    return cur


def json_lines(corpus, rel, keys):
    """把归档里的若干字段印成画面行: 只印归档自己印得出的东西。

    取不到的键**必须**报出来：早先这里对缺键静默跳过，于是 "synthesis.epv"
    这种写错的路径会让整段器件读数从画面里消失而脚本一路绿 —— 少画一格比
    画错一格更难发现。
    """
    txt = corpus.get(rel)
    if not txt:
        raise AssertionError("画面要用 %s, 但语料里没有这份归档" % rel)
    d = json.loads(txt)
    out, miss = [], []
    for k in keys:
        cur = _dig(d, k.split("."), miss, k)
        if cur is None:
            continue
        # 打印格式必须与归档里的字面量一致：用 "%g" 会把 0.62405056... 印成
        # 0.624051，屏上那个数就不再逐字存在于任何归档里 —— 出处自检正是这样
        # 抓住它的（"造不出出处的数字" 与 "自己舍出来的数字" 是同一条判据）。
        shown = cur if isinstance(cur, str) else json.dumps(cur)
        out.append("%-46s %s" % (k, shown))
        KEY_READINGS.append({"file": rel, "key": k, "printed": shown})
    if miss:
        raise AssertionError("%s 里没有这些键: %s —— 键名写错会让整段读数静默消失"
                             % (rel, miss))
    return out


# --------------------------------------------------------------- 时间轴
def field(corpus, rel, key):
    """取一个归档字段的**字面打印串**，用来把数嵌进解释性的句子里。

    存在的理由：出处自检核的是「屏上每个数字逐字在语料里」，所以一句人话里的
    数字也必须是从归档现取的那个串，不能是格式化出来的另一个值。取不到键即抛。
    """
    txt = corpus.get(rel)
    if not txt:
        raise AssertionError("画面要用 %s, 但语料里没有这份归档" % rel)
    miss = []
    v = _dig(json.loads(txt), key.split("."), miss, key)
    if miss:
        raise AssertionError("%s 里没有键 %s —— 键名写错会让整段读数静默消失"
                             % (rel, key))
    shown = v if isinstance(v, str) else json.dumps(v)
    KEY_READINGS.append({"file": rel, "key": key, "printed": shown})
    return shown


def curve_rows(corpus, rel, key, heading):
    """把访存敏感性归档里的一组曲线印成表。

    六个 W 点全印, 一条都不挑 —— 只印好看的那几档就等于把曲线的形状替我说了。
    每个数都取归档里的字面量 (json.dumps 的打印形式与归档逐字一致)。
    """
    txt = corpus.get(rel)
    if not txt:
        raise AssertionError("画面要用 %s, 但语料里没有这份归档" % rel)
    d = json.loads(txt)
    miss, rows = [], []
    cases = _dig(d, ["cases"], miss, "cases")
    if not cases:
        raise AssertionError("%s 的 cases 是空的 —— 一条曲线都没有, 本段无从画" % rel)
    for r in _dig(cases[0], [key], miss, key) or []:
        vals = []
        for f in ("wait_states", "ref_cycles", "epv_cycles_lo", "epv_cycles_hi",
                  "speedup_at_epv_lo", "speedup_pessimistic"):
            if f not in r:
                miss.append("%s.%s" % (key, f))
                vals.append("?")
            else:
                vals.append(json.dumps(r[f]))
        rows.append("  W=%-3s 基线 %-9s EPV %-7s~%-7s 加速 %-8s 悲观 %s" % tuple(vals))
    if miss:
        raise AssertionError("%s 的曲线里没有这些字段: %s —— 键名写错会让整组读数"
                             "静默消失" % (rel, miss))
    if not rows:
        raise AssertionError("%s 的 %s 是空列表 —— 画一条空曲线比不画更糟"
                             % (rel, key))
    return [heading] + rows


def build_scenes(corpus):
    """返回 [(标题, 类型, 行列表, 计划秒数)]。每段的行全部现取自语料。"""
    m = json_lines(corpus, "results/matrix.json",
                   ["steps", "n_pass", "failed", "all_green"])
    b = json_lines(corpus, "results/bench/nn_bench.json",
                   ["macs", "ref_cycles", "epv_cycles", "speedup",
                    "bit_exact", "cyc_per_mac_ref", "cyc_per_mac_epv"])
    n = json_lines(corpus, "results/bench/nn_net.json",
                   ["macs_total", "ref_cycles", "epv_cycles", "speedup",
                    "bit_exact_per_layer", "samples"])
    y = json_lines(corpus, "results/synth/summary.json",
                   ["arms.core_with_epv.cells", "arms.core_no_epv.cells",
                    "epv_net_overhead_cells"])
    C = "comparison_same_platform"
    Y = C + ".post_synthesis_report_area"
    R = C + ".post_place_and_route_report_area"
    p = json_lines(corpus, "results/pnr/summary2.json",
                   [Y + ".epv.total_luts", Y + ".baseline.total_luts",
                    Y + ".delta.total_luts_pct", Y + ".epv.dsp",
                    Y + ".epv.dsp_of", Y + ".epv.fmax_mhz",
                    Y + ".baseline.fmax_mhz",
                    R + ".epv.fmax_mhz", R + ".baseline.fmax_mhz",
                    R + ".delta.fmax_pct", R + ".epv.lut_pct"])
    u = json_lines(corpus, "results/compliance/rv32ui.json", ["n_pass", "n_tests"])
    um = json_lines(corpus, "results/compliance/rv32um.json", ["n_pass", "n_tests"])
    is_ = json_lines(corpus, "results/isco/rv32ui.json",
                     ["n_agree", "n_tests", "all_agree"])
    # 本轮新增的两块证据: 真实数据集精度 + 在环周期数, 以及访存代价曲线。
    # 取数字段一律现读归档, 打印形式沿用归档里的字面量 (见 json_lines 的说明)。
    ci = json_lines(corpus, "results/bench/cifar10_int8.json",
                    ["dataset", "test_images", "top1_int8", "top1_int8_hit",
                     "top1_float_numpy", "quantization_loss_pt", "trained_on",
                     "network", "preprocessing", "input_pad_efficiency",
                     "dataset_tarball_sha256", "torch_version"])
    cc = json_lines(corpus, "results/bench/cifar10_net.json",
                    ["nsamp", "macs_exec", "macs_info", "ref_cycles", "epv_cycles",
                     "ref_instret", "epv_instret", "speedup", "lane_efficiency_pct",
                     "layer_mismatches", "py_layer_mismatches", "py_logit_mismatches",
                     "bit_exact_ref_vs_epv", "bit_exact_ref_vs_python",
                     "top1_ref_in_sim", "top1_epv_in_sim", "argmax_ref_vs_epv",
                     "verdict", "gate", "run_archive", "source"])
    mm = json_lines(corpus, "results/bench/mem_sensitivity.json",
                    ["gate", "gate_cases", "words_per_vec_move",
                     "cases.0.case", "cases.0.archive", "cases.0.zero_point_ok",
                     "cases.0.words_ref_data", "cases.0.words_epv_data",
                     "cases.0.words_ref_all", "cases.0.words_epv_all",
                     "cases.0.invisible_instr_ref", "cases.0.invisible_instr_epv",
                     "cases.0.measured.ref_instret",
                     "cases.0.measured.epv_instret"])
    nb_ratio = json_lines(corpus, "results/bench/nn_bench.json",
                          ["inst_ratio_x1000"])
    five = [u"五个头条数，每个数后面跟着的就是它的归档：",
            u"单算子 INT8 卷积  speedup %s  ← results/bench/nn_bench.json"
            % field(corpus, "results/bench/nn_bench.json", "speedup"),
            u"真实 CIFAR-10 精度  top1_int8 %s  ← results/bench/cifar10_int8.json"
            % field(corpus, "results/bench/cifar10_int8.json", "top1_int8"),
            u"CIFAR 网络在环  speedup %s  ← results/bench/cifar10_net.json"
            % field(corpus, "results/bench/cifar10_net.json", "speedup"),
            u"扩展面积净开销  epv_net_overhead_cells %s  ← results/synth/summary.json"
            % field(corpus, "results/synth/summary.json",
                    "epv_net_overhead_cells"),
            u"布线后 Fmax  %s MHz  ← results/pnr/summary2.json"
            % field(corpus, "results/pnr/summary2.json",
                    "comparison_same_platform.post_place_and_route_report_area"
                    ".epv.fmax_mhz")]


    S = []
    S.append(("EPV32 · 带自定义指令扩展的 RISC-V 端侧 AI 加速核", "card", [
        "赛题四十一 · RISC-V 高水平创新方向 · 高校赛道",
        "以下是 flow/demo.sh 那一趟跑出来的归档读数回放（段号与讲稿一一对应）",
        "每一条读数的出处文件名都印在画面上，可以逐条回查", ""] + five + [
        "", "下面按段回放这些数是怎么跑出来的，段号与讲稿一一对应。"], 46))
    S.append(("官方合规：riscv-tests 不是我自写的测试", "term",
              ["$ python flow/rt.py riscv rv32ui", "$ python flow/rt.py riscv rv32um"]
              + lines_of(corpus, "results/demo/01_compliance_rv32ui.txt", 6)
              + lines_of(corpus, "results/demo/02_compliance_rv32um.txt", 6)
              + [""] + u + um + [""] + is_, 50))
    S.append(("自定义指令扩展的 17 条编码：三方一致 + 注入自检", "term",
              ["$ grep -n EPV_F7 rtl/epv32_defs.vh",
               "$ python tools/check_encoding_consistency.py",
               "$ python tools/check_encoding_consistency.py --selftest"]
              + lines_of(corpus, "results/demo/08_encoding_gate.txt", 22), 52))
    S.append(("EPV 逐条功能自测：参考值由同一份 C 现算", "term",
              ["$ python flow/rt.py bench sw/bench/epv_selftest.c"]
              + lines_of(corpus, "results/demo/03_epv_selftest.txt", 24), 52))
    S.append(("波形：一条 LVB 拆成 4 次整字总线访问", "term",
              ["$ python tools/vcd_probe.py   (读 results/wave/bisect.vcd.gz)"]
              + lines_of(corpus, "results/demo/06_wave_probe.txt", 20) + [
                  "", "说明：这一页来自一次 4200 拍的截窗运行，不是认证运行；",
                  "      它的归档单独带 tag，认证归档没被它写过。"], 55))
    S.append(("核心一页：同一件事少花多少周期，而且逐位一样", "term",
              ["$ python flow/rt.py bench sw/bench/nn_bench.c",
               "$ python tools/bench_archive.py"]
              + lines_of(corpus, "results/demo/05_nn_bench.txt", 20) + [""] + b, 62))
    S.append(("网络级：整张合成权重的 INT8 网络跑一遍", "term",
              ["$ python flow/rt.py bench sw/bench/nn_net.c",
               "$ python tools/net_archive.py"] + n + [
                  "", "注意：这张网络的权重是 LFSR 定种子生成的、没有训练过，所以",
                  "      这一屏证的是「算术逐位一致」与「周期收益的规模」，",
                  "      它不给精度；精度在下一屏，那份权重训练过。"], 48))
    S.append(("真实数据集：CIFAR-10 上量化网络的 top-1", "term",
              ["$ python tools/train_cifar_int8.py",
               "$ 归档 results/bench/cifar10_int8.json"] + ci + [
                  "", "读法：top1_int8 与 top1_float_numpy 的差就是量化损失；",
                  "      test_images 是精度那个数的分母，trained_on 写明训练用了",
                  "      多少张真实图；权重与图像都按 nn_cifar.c 那套 int8 定点导出。"], 50))
    S.append(("在环：同一张 CIFAR-10 网络跑在周期精确模型上", "term",
              ["$ python flow/rt.py bench sw/bench/nn_cifar.c",
               "$ python tools/cifar_archive.py"] + cc + [
                  "读法：ref_cycles 是同算式的标量 C，两条 bit_exact 是与核、与",
                  "      python 导出侧的逐位核对；lane_efficiency_pct 是 3/16 lane",
                  "      携带像素的空转，它没折进加速比。"], 54))
    S.append(("访存代价：把「理想单周期存储」这个假设量成一条曲线", "term",
              ["$ python tools/mem_sensitivity.py"] + mm + [""] +
              curve_rows(corpus, "results/bench/mem_sensitivity.json",
                         "curve_data_only",
                         "只罚数据访问 curve_data_only：") + [""] +
              curve_rows(corpus, "results/bench/mem_sensitivity.json",
                         "curve_data_plus_fetch",
                         "数据 + 取指一起罚 curve_data_plus_fetch：") + nb_ratio + [
                  "", "读法：悲观那一列把 EPV 臂看不见的指令全当成 4 字向量装载；",
                  "      第二组曲线明显比第一组平，因为基线臂每拍都要取指、它的",
                  "      指令字数是 EPV 臂的几十倍（比值见上一行，单位千分之一）。"], 58))
    S.append(("面积代价：通用单元计数 与 真器件综合/布局布线", "term",
              ["$ python tools/synth_archive.py", "$ python tools/pnr_archive.py"]
              + y + ["" ] + p + [
                  "", "上面三行是 Yosys 通用单元数（不是 LUT）；",
                  "下面那些 comparison_same_platform.* 才是安路 TD 的器件读数。"], 58))
    S.append(("一条命令跑完整套门禁：判绿只读归档字段", "term",
              ["$ python flow/run_all.py"] + m
              + lines_of(corpus, "results/demo/09_matrix.txt", 14), 48))
    S.append(("边界：做到哪儿、没做到哪儿（不粉饰）", "card", [
        "已做：官方 rv32ui 与 rv32um 全通过；黄金 ISS 与核逐条退休轨迹一致；",
        "      17 条编码三方一致；内核与网络级 INT8 推理逐位相等；",
        "      真实 CIFAR-10 上训练出的量化网络的 top-1（前面那一屏）；",
        "      同一张网络在周期精确模型上的在环周期与三条实现互核；",
        "      理想存储假设已量成访存代价曲线；",
        "      Yosys 单元数 + 安路 TD 综合与布局布线（含 Fmax）。",
        "还没做到的：实物上板，所以没有功耗读数（预赛按纯仿真交付，",
        "      进决赛购置国产板卡补上电验证）；",
        "      本片的解说音轨与出镜（现在是归档回放，人讲版另录）；",
        "      ISS 侧 EPV 四个程序的 R/S 双投影已逐条一致；17 条扩展中逐条黄金模型覆盖 3/17，",
        "      其余 14 条尚未完成指令级覆盖；",
        "      精度那一屏的网络很小：两层卷积、输入降到 8x8，",
        "      所以 top-1 的绝对值不高，它量的是「整条 int8 通路走得通且算得对」。",
        "",
        "报告五节汉字数（出处 results/word_limits.json）："] + iw_all(corpus), 44))
    return S


def iw_all(corpus):
    """报告五节的汉字数一行, 全部现取归档, 不抄正文。"""
    txt = corpus.get("results/word_limits.json")
    if not txt:
        return ["[缺归档 results/word_limits.json]"]
    rows = json.loads(txt).get("sections", [])
    out = []
    for r in rows:
        name = str(r.get("section", "?"))[:14]
        out.append("  %-16s %s 字（上限 %s）"
                   % (name, r.get("han_chars"), r.get("limit")))
    return out or ["[word_limits 里没有 sections]"]


def paginate(S, fonts):
    """把每段按'一屏放得下'翻页。

    早先版本把超出一屏的行直接丢掉（`if y > H-60: break`），于是画面少了一页
    而脚本一路绿 —— 少画一格比画错一格更难发现。这里先按字体真实折行，再按
    容量切页，段时长按行数分摊。
    """
    cjk, ascii_f = fonts
    out = []
    for title, kind, rows, sec in S:
        wrapped = []
        for ln in rows:
            f = ascii_f if _is_ascii(ln) else cjk
            wrapped.extend(_wrap(ln, f, W - 36))
        pages = [wrapped[i:i + CAP_LINES] for i in range(0, max(1, len(wrapped)),
                                                          CAP_LINES)]
        if not wrapped:
            pages = [[]]
        for j, pg in enumerate(pages):
            t = title if len(pages) == 1 else u"%s（%d/%d）" % (title, j + 1, len(pages))
            share = sec * (len(pg) / float(max(1, len(wrapped))))
            out.append((t, kind, pg, max(10, int(round(share)))))
    return out


def recorded_cps():
    """上一条音轨实测出来的语速（字/秒）。第一次出片时还没有它，返回 None。"""
    try:
        if os.path.isfile(OUT_JSON):
            c = jload(OUT_JSON).get("audio", {}).get("speech_rate_cps_measured")
            if c:
                return float(c)
    except Exception:
        pass
    return None


def build_captions(S, cps=None):
    """按页把讲稿的口播分下来，填 CAPTIONS。取不到讲稿即抛，不静默出无声片。"""
    import demo_narration as NR
    segs = NR.parse_script()
    use = cps or recorded_cps() or NR.DEFAULT_CPS
    # 每行能放几个汉字由**真正要画字幕的那个字体**量出来（不是凭眼力写 65）。
    from PIL import ImageFont
    path = [q for q in CJK_FONT_CANDIDATES if os.path.isfile(q)][0]
    fnt = ImageFont.truetype(path, 19)
    sample = u"端侧量化网络指令扩展周期精确仿真逐通道重量化"
    per_char = fnt.getlength(sample) / float(len(sample))
    per_line = int((W - 40) / per_char)
    max_chars = per_line * CAP_MAX_LINES
    caps, stats = NR.allocate([x[0] for x in S], [x[3] for x in S], segs, cps=use,
                              max_chars=max_chars)
    CAP_GEOM[:] = [per_line, CAP_MAX_LINES, max_chars, round(per_char, 2)]
    return caps, stats


def plan(corpus_map, cps=None, captions=True):
    """排时间轴的唯一路径：建段 -> 缩放 -> 翻页 -> 配旁白。
    --dry / 自检 / 编码都走它，所以字幕与音轨不可能各排一套时间轴。"""
    fonts = pick_fonts()
    KEY_READINGS[:] = []            # 承重读数的账从空重记（排在 build_scenes 之前）
    S = paginate(fit_timings(build_scenes(corpus_map)), fonts)
    if captions:
        CAPTIONS.clear()
        CAP_STATS.clear()
        caps, stats = build_captions(S, cps)
        CAPTIONS.update(caps)
        CAP_STATS.update(stats)
    return S


def total_seconds(S):
    return sum(s[3] for s in S)


def fit_timings(S, target=TARGET_S):
    """把各段时长按比例缩放到目标附近, 并保证整数秒、每段 ≥12 s。"""
    t = total_seconds(S)
    if t <= 0:
        raise AssertionError("时间轴总长为 0, 没有素材可放")
    k = target / t
    out = []
    for title, kind, rows, sec in S:
        # 行数多的段不许被压到读不完: 每行至少 0.55 s
        need = max(12.0, 0.55 * max(1, len(rows)))
        out.append((title, kind, rows, max(need, round(sec * k))))
    return out


# --------------------------------------------------------------- 数字出处
NUM = re.compile(r"\d[\d,]*\.?\d*")


def strip_commas(s):
    return s.replace(",", "")


def unsourced_numbers(S, corpus_text):
    """返回 [(段, 行, 数字)] —— 画面上印了但语料里没有的数。

    字幕也在扫描范围内：旁白是从讲稿来的，而讲稿里的数是人手写的转述，
    正是这条判据最该盯的东西（它不许「15.734」被讲成「15.73 倍」之外的任何值）。
    """
    hay = strip_commas(corpus_text)
    bad = []
    for title, _kind, rows, _sec in S:
        lines = list(rows) + [CAPTIONS.get(title, u"")]
        for ln in lines:
            if not ln:
                continue
            for tok in NUM.findall(ln):
                v = strip_commas(tok)
                if v in SHAPE_OK or v in hay:
                    continue
                # 允许 "8 分 30 秒" 这类时间轴自述与纯排版序号
                if re.search(u"分|秒|页|段|行|字|MB|GHz|MHz|ns|拍", ln):
                    continue
                bad.append((title, ln[:70], tok))
    return bad


# --------------------------------------------------------------- 渲染
def pick_fonts():
    from PIL import ImageFont
    cjk = ascii_f = None
    for p in CJK_FONT_CANDIDATES:
        if os.path.isfile(p):
            cjk = ImageFont.truetype(p, 21)
            break
    for p in ASCII_FONT_CANDIDATES:
        if os.path.isfile(p):
            ascii_f = ImageFont.truetype(p, 20)
            break
    if cjk is None or ascii_f is None:
        raise AssertionError("字体没找到: cjk=%s ascii=%s" % (cjk, ascii_f))
    return cjk, ascii_f


def assert_cjk_glyphs():
    """证明中文字形真的画得出来（选错字体只会画一排方框而脚本照样绿）。"""
    from PIL import ImageFont, ImageDraw, Image
    f = None
    for p in CJK_FONT_CANDIDATES:
        if os.path.isfile(p):
            f = ImageFont.truetype(p, 40)
            break
    if f is None:
        raise AssertionError("没有可用的中文字体, 画面会变成方框")
    def mask(ch):
        im = Image.new("L", (80, 60), 0)
        d = ImageDraw.Draw(im)
        d.text((2, 2), ch, font=f, fill=255)
        return list(im.tobytes())        # 不用 getdata: Pillow 14 会移除它
    a, b = mask(u"中"), mask(u"\ue009")     # 私有区码位: 缺字形时画的就是方框
    ink = sum(1 for v in a if v)
    if ink < 60:
        raise AssertionError("中文字形墨点只有 %d, 等于没画出来" % ink)
    if a == b:
        raise AssertionError("「中」与私有区码位点阵相同 —— 该字体没有 CJK 字形")
    return {"font": [p for p in CJK_FONT_CANDIDATES if os.path.isfile(p)][0],
            "ink_pixels_probe_CJK": ink}


def assert_scene_glyphs(S):
    """把本片**实际要画的**每个非 ASCII 字符逐个试画, 与私有区码位比点阵。

    assert_cjk_glyphs 只拿「中」一个字证明字体选对了, 但一句新加的旁白里只要
    有一个字符不在字体里 (箭头、全角符号都算), 画面上就是一个方框而脚本一路绿
    —— 评委看到的坏字形没人知道。所以这条按本次字符集来核。
    """
    from PIL import ImageFont, ImageDraw, Image
    path = [p for p in CJK_FONT_CANDIDATES if os.path.isfile(p)][0]
    f = ImageFont.truetype(path, 40)

    def mask(ch):
        im = Image.new("L", (80, 60), 0)
        ImageDraw.Draw(im).text((2, 2), ch, font=f, fill=255)
        return list(im.tobytes())

    box = mask(u"\ue009")
    chars = set()
    for title, _kind, rows, _sec in S:
        for txt in [title] + list(rows) + [CAPTIONS.get(title, u"")]:
            chars.update(ch for ch in txt if ord(ch) > 127)
    missing = sorted(ch for ch in chars if mask(ch) == box)
    if missing:
        raise AssertionError("字体 %s 画不出这些字符 (会变成方框): %s"
                             % (os.path.basename(path),
                                " ".join("\\u%04x" % ord(c) for c in missing)))
    return {"font": os.path.basename(path),
            "distinct_non_ascii_chars_policed": len(chars),
            "missing_glyphs": 0}


def _wrap(text, font, px):
    """按像素宽度折行。直接截断会把一行读到一个没写完的半句 —— 画面里宁可
    折两行，也不要出现被切掉的尾巴。"""
    out, cur = [], ""
    for ch in text:
        if font.getlength(cur + ch) <= px:
            cur += ch
            continue
        out.append(cur)
        cur = ch
    out.append(cur)
    return out or [""]


def frame_image(title, rows, cursor, fonts, hold=False):
    from PIL import Image, ImageDraw
    cjk, ascii_f = fonts
    im = Image.new("RGB", (W, H), (12, 14, 18))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 46], fill=(28, 34, 46))
    d.text((18, 11), title, font=cjk, fill=(226, 232, 240))
    d.text((18, 58), "$ " + "=" * 60, font=ascii_f, fill=(70, 80, 96))
    y = 78
    shown = 0
    for ln in rows:
        if shown >= cursor:
            break
        f = ascii_f if _is_ascii(ln) else cjk
        for seg in _wrap(ln, f, W - 36):
            if y > H - 60:
                raise AssertionError(
                    "一段画不下了: %r 第 %d 行溢出 (CAP_LINES=%d) —— "
                    "翻页的容量算法与实际渲染不一致" % (title, shown, CAP_LINES))
            col = (226, 232, 240)
            if ln.startswith("$"):
                col = (122, 200, 255)
            elif (ln.startswith(u"注意") or u"未做" in ln or u"还没" in ln
                  or ln.startswith(u"      实物")):
                col = (250, 180, 90)
            d.text((18, y), seg, font=f, fill=col)
            y += 22
        shown += 1
    if hold:
        d.rectangle([W - 250, 8, W - 18, 40], fill=(46, 54, 70))
        d.text((W - 240, 14), "本段读数已停在满屏", font=cjk, fill=(150, 162, 180))
    d.text((18, H - 26), u"EPV32 · results/demo/ 归档读数回放 · 出处逐条印在画面上",
           font=cjk, fill=(90, 100, 116))
    return im


def _is_ascii(s):
    try:
        s.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


_CAP_FONT = [None]


def cap_font():
    from PIL import ImageFont
    if _CAP_FONT[0] is None:
        cands = [p for p in CJK_FONT_CANDIDATES if os.path.isfile(p)]
        if not cands:
            raise AssertionError("找不到中文字体，字幕画不出来")
        _CAP_FONT[0] = ImageFont.truetype(cands[0], 19)
    return _CAP_FONT[0]


def draw_caption(bgr, text):
    """把这一页要念的旁白原样烧进字幕条 —— 音轨念的是同一串字符。

    装不下即抛，不许截半句：一句说不完的字幕比没有字幕更糟，而归档里那句是完整的。
    字幕与音轨同源这条设计的目的：改旁白只改讲稿一处，不会出现「说的是一套、
    屏上是另一套」那种本包刚被抓住过的代际漂移。
    """
    if not text:
        return bgr
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw
    segs = _wrap(text, cap_font(), W - 40)
    if len(segs) > CAP_MAX_LINES:
        raise AssertionError("字幕装不下: %d 行 > %d 行 (%r) —— 这一页旁白太长"
                             % (len(segs), CAP_MAX_LINES, text[:30]))
    im = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(im)
    d.rectangle([0, CAP_BAND_Y0 - 6, W, H], fill=(6, 8, 12))
    y = CAP_BAND_Y0
    for s in segs:
        d.text((20, y), s, font=cap_font(), fill=(255, 214, 132))
        y += CAP_LINE_H
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)


def encode(S, fonts, path):
    import cv2
    import numpy as np
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vw = None
    written = 0
    for title, kind, rows, sec in S:
        n = int(round(sec * FPS))
        if vw is None:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            vw = cv2.VideoWriter(path, fourcc, FPS, (W, H))
            if not vw.isOpened():
                raise AssertionError("VideoWriter 打不开编码器 (fourcc=mp4v)")
        # 打字速度由 FILL_FRAC 决定, 与 reveal_check 共用同一个算式：
        # 第一版按整段线性铺开, 抽帧一看大半画面是空的。
        steps = max(1, min(len(rows), n))
        fill = max(1, int(round(n * FILL_FRAC)))
        cache = {}
        for i in range(n):
            if kind == "card":
                cursor = len(rows)
            else:
                cursor = min(len(rows), 1 + int((i + 1) * steps / float(fill)))
            hold = i >= n - FPS * 2
            key = (cursor, hold)
            if key not in cache:            # 同一屏内容只渲染一次 PIL
                im = frame_image(title, rows, cursor, fonts, hold)
                cache[key] = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
                cache[key] = draw_caption(cache[key], CAPTIONS.get(title, u""))
            vw.write(cache[key])
            written += 1
    if vw is not None:
        vw.release()
    return written


def measure(path):
    """从容器回读真实时长 —— 判据只认量出来的那个数。"""
    import cv2
    v = cv2.VideoCapture(path)
    if not v.isOpened():
        raise AssertionError("成片写完了却回读不了: %s" % path)
    fps = v.get(cv2.CAP_PROP_FPS)
    frames = int(v.get(cv2.CAP_PROP_FRAME_COUNT))
    ok, fr = v.read()
    shape = None if not ok else tuple(fr.shape[:2])
    v.release()
    if frames <= 0 or fps <= 0:
        raise AssertionError("容器里的帧数/帧率不合法: frames=%d fps=%s" % (frames, fps))
    return {"frames_measured": frames, "fps_measured": fps,
            "duration_measured_s": round(frames / fps, 1),
            "first_frame_readable": bool(ok), "frame_shape": list(shape or [])}


# --------------------------------------------------------------- 自检
def reveal_check(S):
    """到每段的 70% 时画面必须已经满屏 —— 否则观众看到的是大半段空屏。

    这条判据与 encode 用同一个 FILL_FRAC/steps 算式（不另写一遍），
    它抓的是"排版计划"层面的错，不需要真的把片子编出来再看。
    """
    bad = []
    for title, kind, rows, sec in S:
        if kind == "card" or not rows:
            continue
        n = int(round(sec * FPS))
        steps = max(1, min(len(rows), n))
        fill = max(1, int(round(n * FILL_FRAC)))
        i = int(0.7 * n)
        cursor = min(len(rows), 1 + int((i + 1) * steps / float(fill)))
        if cursor < len(rows):
            bad.append("SLOW-REVEAL: %r 到 %.0f%% 处只露出 %d/%d 行"
                       % (title[:24], 70.0, cursor, len(rows)))
    return bad


def selfcheck(S, corpus_map):
    """出片前必须全过；任何一条不过就不落 mp4。"""
    ct = "\n".join(corpus_map.values())
    problems = []
    uns = unsourced_numbers(S, ct)
    if uns:
        problems.append("DRAWN-NOT-IN-ARCHIVE: %d 处, 首例 %s / %r"
                        % (len(uns), uns[0][2], uns[0][1]))
    problems += reveal_check(S)
    # 字幕超行也排在编码之前：渲染端那条「装不下就抛」的断言是对的，但它抛在
    # 已经写完 5000 帧之后 —— 产物被删、这一趟白跑。这里提前判，同一把尺子
    # （同一个字体、同一个 CAP_MAX_LINES），不另写第二份容量算式。
    if CAPTIONS:
        over = []
        for t, txt in sorted(CAPTIONS.items()):
            if txt and len(_wrap(txt, cap_font(), W - 40)) > CAP_MAX_LINES:
                over.append((t[:18], len(_wrap(txt, cap_font(), W - 40))))
        if over:
            problems.append("CAPTION-OVERFLOW: %d 页字幕超过 %d 行, 首例 %r "
                            "(每页上限 %s 字)"
                            % (len(over), CAP_MAX_LINES, over[0],
                               CAP_GEOM[2] if CAP_GEOM else "?"))
    tot = total_seconds(S)
    if tot > LIMIT_S:
        problems.append("TOO-LONG: 计划 %.0f s 超过赛题上限 %.0f s" % (tot, LIMIT_S))
    if tot < 0.6 * TARGET_S:
        problems.append("TOO-SHORT: 计划 %.0f s 短于目标 %.0f s 的六成"
                        % (tot, TARGET_S))
    empty = [s[0] for s in S if not s[2]]
    if empty:
        problems.append("EMPTY-SCENE: %s" % empty)
    return problems, tot, uns


def frame_account(written, meas):
    """编码器数出来的帧数 vs 容器里量出来的帧数 —— 不等就是有一边在说谎。

    这条判据的来历：第一版 encode 在补齐每段尾巴时只加计数器不写帧，sidecar
    于是报 507 s 而容器实测 498 s。时长/体积是赛题的硬指标，必须由**回读**
    决定；把两个数放在一起比，才不至于又变成"自己数给自己看"。
    """
    got = meas.get("frames_measured")
    if written != got:
        return ("FRAMES-MISMATCH: 编码器写了 %d 帧, 容器里 %d 帧 (差 %+d) —— "
                "sidecar 的时长不可信" % (written, got, written - (got or 0)))
    if not meas.get("first_frame_readable"):
        return "UNREADABLE: 成片回读不到第一帧"
    return None


def main(argv):
    corpus_map = corpus()
    if not corpus_map:
        raise SystemExit("NO_CORPUS: results/demo/ 里什么都没有, 先跑 bash flow/demo.sh")
    S = plan(corpus_map)
    tot = total_seconds(S)
    if "--dry" in argv:
        for i, (t, k, r, s) in enumerate(S):
            print("scene %2d  %-5s %4ds %3d lines  %s" % (i, k, s, len(r), t[:46]))
        print("DRY_TOTAL_S %.0f (上限 %.0f, 目标 %.0f)  语料 %d 份  一屏 %d 行"
              % (tot, LIMIT_S, TARGET_S, len(corpus_map), CAP_LINES))
        return 0

    problems, tot, uns = selfcheck(S, corpus_map)
    if problems:
        for x in problems:
            print("SELF-CHECK-FAIL: " + x)
        print("NOT_REACHED: 未编码, 不落 %s" % OUT_MP4)
        return 1
    if "--check-only" in argv:
        print("DEMO_VIDEO_SELFCHECK_OK %d 段 / %.0f s / 无越界数字" % (len(S), tot))
        return 0

    fonts = pick_fonts()
    glyph = assert_cjk_glyphs()
    glyph["per_scene_char_audit"] = assert_scene_glyphs(S)
    t0 = time.time()
    written = encode(S, fonts, OUT_MP4)
    meas = measure(OUT_MP4)
    size = os.path.getsize(OUT_MP4)
    dur = meas["duration_measured_s"]
    # 时长以容器回读为准；自己数出来的帧数与量出来的不一致就是 bug，不许静默
    acct = frame_account(written, meas)
    if acct:
        os.remove(OUT_MP4)
        raise SystemExit("SELF-CHECK-FAIL: " + acct + "，已删除产物")
    if dur > LIMIT_S:
        os.remove(OUT_MP4)
        raise SystemExit("SELF-CHECK-FAIL: 编码后实测 %.0f s 超上限, 已删除产物" % dur)
    if size > LIMIT_MB * 1024 * 1024:
        os.remove(OUT_MP4)
        raise SystemExit("SELF-CHECK-FAIL: 编码后 %.1f MB 超 100 MB, 已删除产物"
                         % (size / 1048576.0))
    # ---- 解说音轨：合成 -> 装配 -> 混流 -> 从容器回读自证 ------------------
    # 音轨失败时**不许**留一段无声片而 sidecar 说它有声音：audio_block 保持 None,
    # 下面的 not 列表就会重新写上「没有解说音轨」，并把失败原因留在 audio_error 里，
    # 整步以非零退出。`not` 由本函数算出来的状态导出，不是抄一份写死的话。
    audio_block, audio_err = None, None
    if "--no-audio" not in argv:
        import tempfile
        import demo_narration as NR
        wk = tempfile.mkdtemp(prefix="epv32_narr_")
        try:
            titles = [x[0] for x in S]
            secs = [x[3] for x in S]
            audio_block = NR.emit_audio(titles, secs, CAPTIONS, OUT_MP4, wk)
            size = os.path.getsize(OUT_MP4)
            meas = measure(OUT_MP4)
            dur = meas["duration_measured_s"]
            acct = frame_account(written, meas)
            if acct:
                raise AssertionError(acct)
            if size > LIMIT_MB * 1024 * 1024 or dur > LIMIT_S:
                raise AssertionError("混流后 %.0f s / %.1f MB 超上限"
                                     % (dur, size / 1048576.0))
        except Exception as e:
            # 混流已经把 mp4 换掉了。这时候如果只报错不留痕迹，盘上就是一份
            # 「成片是新的、sidecar 是上一份」的错位交付件 —— 所以连产物一起删，
            # 让下一步（demo.sh 或人）必须重出，而不是拿一份与 sidecar 不符的片子交上去。
            audio_err = repr(e)[:300]
            audio_block = None
            if os.path.isfile(OUT_MP4):
                os.remove(OUT_MP4)
            raise SystemExit("SELF-CHECK-FAIL(音轨): %s；已删除 %s，重跑本命令可再出"
                             % (audio_err, OUT_MP4))
        finally:
            import shutil
            shutil.rmtree(wk, ignore_errors=True)
    # 波形页那一屏的截窗拍数: 从它自己那份带 tag 的归档现读, 读不到就明写读不到。
    # (上一版这里从 corpus_map 取, 而语料只收 .txt/.log, 于是 .get("max_cyc")
    #  拿到 None, sidecar 的 caveat 印成"一次 None 拍" —— 一个静默的假读数。)
    wave_win = "[缺 results/demo/06b_wave_archive.json]"
    wave_arc = os.path.join(DEMO, "06b_wave_archive.json")
    try:
        if os.path.isfile(wave_arc):
            wd = json.loads(read(wave_arc))
            if wd.get("max_cyc") is not None:
                wave_win = str(wd["max_cyc"])
    except Exception:
        pass
    has_audio = bool(audio_block)
    mid_pages = sorted(k for k, v in CAP_STATS.items() if v.get("mid_sentence_cut"))
    out = {
        "what": (u"方案 Demo 的归档回放片: 画面是 flow/demo.sh 那一趟的终端读数, "
                 u"配一条中文解说音轨与同源字幕"
                 if has_audio else
                 u"方案 Demo 的无声归档回放片: 画面是 flow/demo.sh 那一趟的终端读数"),
        "not": ([] if has_audio else [u"没有解说音轨"])
               + [u"没有出镜", u"没有实物上板画面"],
        "audio": audio_block or {"error": audio_err or "本次以 --no-audio 出片"},
        "audio_error": audio_err,
        "file": OUT_MP4.replace("\\", "/"),
        "sha256": sha(OUT_MP4),
        "duration_s": dur, "limit_duration_s": LIMIT_S,
        "size_bytes": size, "limit_size_bytes": int(LIMIT_MB * 1048576),
        "fps": FPS, "resolution": "%dx%d" % (W, H),
        "frames_written": written, "lines_per_screen": CAP_LINES,
        "measured_from_container": meas,
        "how_to_read": ("duration_s 是 cv2 回读容器得到的 frames/fps, "
                        "不是编码器自己数的帧数; 两者不等时本工具删掉产物并退出非零"),
        "codec": ("libx264+aac" if has_audio else "mp4v"),
        "encode_wall_s": round(time.time() - t0, 1),
        "generation": {
            "why": u"代际由 RTL 哈希定，而不由时间戳定：链条每复跑一趟矩阵的 "
                   u"started_utc 都变，逐份哈希相等这条判据按构造永远不可能成立，"
                   u"而「成片属于哪一代」是真问题",
            "matrix_started_utc": jload("results/matrix.json").get("started_utc"),
            "rtl_sha256_head": jload("results/matrix.json").get("rtl_sha256_head"),
            "matrix_steps": jload("results/matrix.json").get("steps"),
            "matrix_n_pass": jload("results/matrix.json").get("n_pass"),
            # 同一代证据链的末端指纹：矩阵先落盘、09_matrix.txt 再捕获，
            # 最后才出片；门禁据此拒绝“同 RTL 但快照/矩阵来自不同收口轮”的组合。
            "matrix_sha256": sha("results/matrix.json"),
            "snapshot_sha256": sha(os.path.join(DEMO, "09_matrix.txt")),
        },
        "scenes": [{"i": i, "title": t, "kind": k, "seconds": s, "lines": len(r),
                    "caption_chars": len(CAPTIONS.get(t, u""))}
                   for i, (t, k, r, s) in enumerate(S)],
        "number_provenance": {
            "rule": "屏上每个数字必须逐字出现在下列语料里; 白名单只放算式记号; "
                    "成片自己的运行日志不在语料内(它会回显自检造的假数); "
                    "字幕与音轨同源，所以旁白里的数也走同一条判据",
            "corpus_files": sorted(corpus_map),
            "corpus_sha256": {f: sha(f) for f in sorted(corpus_map)},
            "corpus_sha256_is_a_verdict": False,
            "why_not_a_verdict": u"这些归档里带时间戳，每复跑一趟哈希就变；拿它当判据 "
                                 u"会造出一发永远红的门。真正的判据是 key_readings：",
            "key_readings": list(KEY_READINGS),
            "key_readings_count": len(KEY_READINGS),
            "unsourced_numbers_on_screen": len(uns),
        },
        "caption_geometry": {
            "chars_per_line": CAP_GEOM[0] if CAP_GEOM else None,
            "max_lines": CAP_MAX_LINES, "per_page_char_cap": CAP_GEOM[2] if CAP_GEOM else None,
            "measured_with": "PIL ImageFont.getlength(样本串) / 字数, 与实际渲染同字体同字号",
        },
        "narration_allocation": {
            "chars_spoken_total": sum(v.get("chars_used", 0) for v in CAP_STATS.values()),
            "chars_available_total": sum(v.get("chars_total", 0) for v in CAP_STATS.values()),
            "sentences_dropped_for_process_language":
                sum(v.get("sentences_dropped_for_process_language", 0)
                    for v in CAP_STATS.values()),
            "sentences_dropped_by_length":
                sum(v.get("sentences_dropped_by_length", 0) for v in CAP_STATS.values()),
            "pages_cut_mid_sentence": sorted(k for k, v in CAP_STATS.items()
                                             if v.get("mid_sentence_cut")),
            "per_page": CAP_STATS,
        },
        "cjk_font": glyph,
        "caveats": [
            (u"本片的中文解说由本机合成语音读出（非真人），念的字幕与讲的同源；"
             u"真人版按 demo/演示脚本与讲稿.md 录即可替换，替换命令见 flow/demo_narration.py"
             if has_audio else
             u"本片没有解说音轨：音轨合成这一步本轮未成功或未启用，原因见 audio 块"),
            u"解说文本逐字取自 demo/演示脚本与讲稿.md 的口播块，画面时长装不下的整句"
            u"不念（记在 narration_allocation 里），所以音轨讲的是那份稿子的一部分",
            u"波形页来自一次 %s 拍的截窗运行, 不是认证运行" % wave_win,
            u"合成网络(nn_net)那一屏的权重是 LFSR 定种子生成的, 只证算术一致与周期收益",
            u"精度那一屏来自网络在真实 CIFAR-10 上的训练与量化, 与 nn_net 不同源不同数",
            u"访存曲线只有 cases[0] 这一条用例出得来, 另两条记在归档的 unavailable 里",
            u"没有实物板, 所以全片没有一个功耗读数",
        ] + ([u"有 %d 页的旁白是在句中停顿处收的（一整句超过该页时长）: %s"
              % (len(mid_pages), ", ".join(t[:14] for t in mid_pages))]
             if mid_pages else []),
    }
    write_json_atomic(OUT_JSON, out)
    print("DEMO_VIDEO_OK %s  %.0f s  %.1f MB  %d 段  -> %s"
          % (out["resolution"], dur, size / 1048576.0, len(S), OUT_JSON))
    return 0


# --------------------------------------------------------------- 注入自检
def selftest():
    """每条注入必须落在它自己的判据上, 并打印原因里的关键词。"""
    fails = []
    corpus_map = corpus()
    if not corpus_map:
        print("SELFTEST_HAS_NO_BASELINE: results/demo/ 为空, 先跑 flow/demo.sh")
        return 1
    # 先给真实产物留指纹：自检只读不写，最后再比一次。
    before = {p: sha(p) for p in (OUT_MP4, OUT_JSON) if os.path.isfile(p)}

    def run(S):
        probs, tot, uns = selfcheck(S, corpus_map)
        return probs, tot, uns

    base = plan(corpus_map)
    probs, tot, uns = run(base)
    if probs:
        print("[selftest] 健康时间轴已红, 无法给注入定级: %s" % probs[:3])
        return 1
    print("[selftest] healthy: %d 段 / %.0f s / 越界数字 %d" % (len(base), tot, len(uns)))

    # 1) 造一个归档里没有的数字 -> 必须被 unsourced 抓住并点名
    S = [list(x) for x in base]
    S[0][2] = S[0][2] + ["加速比 187.421 倍"]
    probs, _t, uns = run([tuple(x) for x in S])
    if not any("DRAWN-NOT-IN-ARCHIVE" in p and "187.421" in p for p in probs):
        fails.append("fake-number: 没抓住造出来的 187.421: %s" % probs[:2])
    else:
        print("[selftest] fake-number   红作为 %s" % probs[0][:80])

    # 2) 超时长 -> 必须报 TOO-LONG 而不是照编
    S = [(t, k, r, 400) for (t, k, r, _s) in base]
    probs, tot2, _ = run(S)
    if not any("TOO-LONG" in p for p in probs):
        fails.append("too-long: %.0f s 竟然没报" % tot2)
    else:
        print("[selftest] too-long      红作为 %s" % [p for p in probs if "TOO-LONG" in p][0][:70])

    # 3) 空段 -> 必须报 EMPTY-SCENE
    S = [list(x) for x in base]
    S[3][2] = []
    probs, _t, _u = run([tuple(x) for x in S])
    if not any("EMPTY-SCENE" in p for p in probs):
        fails.append("empty-scene: 没抓住空段")
    else:
        print("[selftest] empty-scene   红作为 %s" % [p for p in probs if "EMPTY" in p][0][:70])

    # 4) 语料清空 -> 必须拒绝出片 (NO_CORPUS 路径在 main, 这里核判据本身)
    probs, _t, _u = selfcheck(base, {})
    if not any("DRAWN-NOT-IN-ARCHIVE" in p for p in probs):
        fails.append("no-corpus: 语料为空还放绿")
    else:
        print("[selftest] no-corpus     红作为 %s" % probs[0][:80])

    # 5) 打字太慢 -> 必须报 SLOW-REVEAL。把 FILL_FRAC 退回第一版的"整段线性"
    #    形状 (1.0) 就是当时的真实实现, 这条注入演的正是那次事故。
    globals()["FILL_FRAC"] = 1.0
    try:
        probs, _t, _u = run(base)
    finally:
        globals()["FILL_FRAC"] = 0.45
    if not any("SLOW-REVEAL" in p for p in probs):
        fails.append("slow-reveal: 整段线性打字竟然没被抓到")
    else:
        print("[selftest] slow-reveal   红作为 %s"
              % [p for p in probs if "SLOW" in p][0][:80])

    # 6) 帧数账: 编码器数的与容器量出来的不一致, 必须拒绝出片。
    #    两条对照一起放: 一致时不许报红, 差 88 帧时必须报出差多少。
    if frame_account(4982, {"frames_measured": 4982, "first_frame_readable": True}):
        fails.append("frames-equal: 两本账一致却被判红")
    d1 = frame_account(5070, {"frames_measured": 4982, "first_frame_readable": True})
    if not d1 or "FRAMES-MISMATCH" not in d1 or "88" not in d1:
        fails.append("frames-diff: 88 帧的差竟然放绿或没说清 (%r)" % d1)
    else:
        print("[selftest] frames-account  红作为 %s" % d1[:80])
    d2 = frame_account(100, {"frames_measured": 100, "first_frame_readable": False})
    if not d2 or "UNREADABLE" not in d2:
        fails.append("unreadable-frame: 回读不到第一帧却放绿 (%r)" % d2)

    # 7) 副作用: 注入不许碰真实产物
    after = {p: sha(p) for p in (OUT_MP4, OUT_JSON) if os.path.isfile(p)}
    if after != before:
        fails.append("side-effect: 自检改写了 %s"
                     % [p for p in after if before.get(p) != after.get(p)])
    else:
        print("[selftest] side-effect     成片与 sidecar 逐位未变 (%d 份)" % len(after))
    if fails:
        print("[selftest] SELFTEST_FAILED %d case(s)" % len(fails))
        for f in fails:
            print("   " + f)
        return 1
    print("DEMO_VIDEO_SELFTEST_OK: 5 条注入 + 3 条对照各自报出原因")
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main(sys.argv[1:]))
