#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_navigation.py -- 把「按目录找东西」需要的索引做成**生成件**, 并自带 --check。

为什么是生成件而不是手写一份 INDEX
-----------------------------------
第三方评审 R3 的 P2-6 / P2-7 / P2-16 要的是「190 MB / 1382 个文件要有一份阅读顺序」。
手写清单在这类包里有一种很具体的失效形状: 目录涨了、归档换代了、日志多了一批,
而清单还停在那一天 —— 而且没有任何门会发现, 因为「清单里写了」与「盘上有」是两个
互相独立的事实。所以本件:

  体积、文件数、时刻、"哪一份日志对应 matrix.json 那一趟" —— 全部现算;
  角色一句话 (现行结论 / 证据 / 历史对照 / 可再生中间件) —— 由下面那张表**人工**
  写理由, 但类别数与条数由脚本填 (与本包 `historical_archives.py` 同一分工);
  `--check` 把「盘上那份」与「现在重生成的那份」逐字节比对, 不等即判红 ——
  这一条把它接进 pair_gates 的 spec 清单, 于是「索引过期」会变成链条上的一红。

R4 收口轮修掉的三条 (每一条都由 --selftest 里一条具名夹具钉住, 见 FIXTURE_NAMES)
-------------------------------------------------------------------------------
  D1「列出的路径必须盘上存在」: 以前把语料里搜到的路径片段**硬拼**在 `refs/` 后面
     就写进索引, 于是一份 `refs/anlogic_td_probe.md` (真实文件在 `docs/refs/`) 与一
     条纯垃圾片段 `refs/xx.md` 都在绿色判决里活着。现在片段一律先按包内路径解析,
     文件要 `os.path.isfile`、目录要 `os.path.isdir`, 解不出的**不落纸**, 并且本节
     自报 `listed/resolved/unmatched/rejected` 四个数 —— unmatched 只要 >0 就整体
     非零退出。Windows 还会把 `refs/...` 折成 `refs` 目录本身 (尾点被剥), 所以解析
     器另有一条形状守卫: 逐段校验段名, 并要求解析结果的 basename 与片段末段相等。
  D2「每个数都由落盘这一刻走树算出, 并且写完再核一遍」: 以前 `本目录共 1529 个
     文件 / 1085.0 MiB` 这类数是**上一趟**的现实 (实测今日盘上 1526 / 187.1 MiB),
     因为它挂在链条中段。现在: 所有数出自同一次 `walk_stats`; 写完立刻把文件读回
     来, 按行锚点把印出去的位数抠出来与**再一次现走树**对账, 不等即点名 `FIGURE-*`
     判红; 算不出的数不许写。`started_utc` 现读 matrix.json 并与它自己的 mtime 并列
     印出; 盘上出现比 matrix.json 更晚的证据 ⇒ 印 `STALE-VERDICT-CACHE` 并非零退出
     (这条取代了字面上的「started_utc 比最新归档还旧」—— 后者结构上必然成立, 因为
     一趟运行里的归档本来就都在 started_utc 之后落盘, 拿它当判据等于造一发永远红的
     门; 真正分辨「矩阵还没落盘」的是**有没有证据晚于 matrix.json 自己的落盘时刻**)。
     同样为了不做一发永远红的门, 这里再分一档: 链条在跑后置格**之前**先 emit 一次中间
     表 (`steps != n_total_in_table`), 那时后置归档比矩阵新是正常形状 —— 这一档只印
     `MATRIX-PARTIAL ...` 的响而不判死 (夹具 fixture_stale_verdict_detected 的臂 B 钉住
     它, 臂 A/C/D 分别钉住「终表且归档更早 ⇒ 不报」「终表而归档更新 ⇒ 必报且点名」
     「矩阵不在 ⇒ fail-closed」)。
  D3「flow/ 的说明件是生成件」: 30 步左右的认证表只要手抄一份, 加一步就过期。现在
     `flow/README.md` 的步骤表由 import 真模块 `flow/run_all.py` 读 `STEPS` /
     `POST_STEPS` / `N_TOTAL` 现生成, 步名一个都不抄; 「写不写归档」这一格由该模块
     自己的源码 + `results/HISTORICAL_ARCHIVES.json` 的 `produced_by` 记账推出来,
     推不出的印 `UNKNOWN`。块首有 provenance 行 (哪个模块、几步、那支模块当时的哈希)。

用法
----
  python tools/make_navigation.py                 # 只核 (默认即判决)
  python tools/make_navigation.py --write         # 生成并落盘
  python tools/make_navigation.py --flow-readme   # 只生成 flow/README.md 那块表
  python tools/make_navigation.py --selftest      # 本件自己的夹具
"""
import argparse
import datetime
import inspect
import io
import json
import os
import re
import shutil
import sys
import tempfile

sys.dont_write_bytecode = True   # 本件会 import flow/run_all.py, 不许长出 __pycache__

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


# ------------------------------------------------------------------ 基础件
def rd(p):
    return io.open(p, encoding="utf-8", errors="replace").read()


def w(p, text):
    p = p.replace("/", os.sep)
    d = os.path.dirname(p)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    io.open(p, "w", encoding="utf-8", newline="\n").write(text)


def walk_stats(base):
    """find + du 的等价物, 一趟走树同时给出: 文件数、字节数、最新 mtime 及是谁。

    索引里**每一个**体积/条数读数都必须经这只函数, 而且写完之后要用它再走一次对账
    (见 verify_figures)。返回 (n_files, n_bytes, newest_mtime, newest_relpath)。
    """
    n, size, newest, newest_rel = 0, 0, 0.0, ""
    if not os.path.isdir(base):
        st = None
        try:
            st = os.stat(base)
        except OSError:
            return 0, 0, 0.0, ""
        return 1, st.st_size, st.st_mtime, os.path.basename(base)
    for dp, _dn, fns in os.walk(base):
        for fn in fns:
            p = os.path.join(dp, fn)
            try:
                st = os.stat(p)
            except OSError:
                continue
            n += 1
            size += st.st_size
            if st.st_mtime > newest:
                newest, newest_rel = st.st_mtime, os.path.relpath(p, base).replace(os.sep, "/")
    return n, size, newest, newest_rel


def dir_stats(rel, root=None):
    """向后兼容的旧接口: 只回条数与字节数。"""
    base = os.path.join(root or ROOT, rel.replace("/", os.sep))
    n, size, _t, _r = walk_stats(base)
    return n, size


def mib(size_bytes):
    return size_bytes / 1048576.0


def utc(t):
    return datetime.datetime.utcfromtimestamp(t).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha16(path):
    import hashlib
    try:
        return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]
    except OSError:
        return "UNREADABLE"


# ---------------------------------------------------------------- 人工写的角色句
# 只有「为什么这一格是这种角色」归人; 条数/体积/时刻归脚本。
RESULT_ROLES = [
    ("matrix.json", "现行结论", "唯一交付结论: 整表跑完那一趟的判决。格数与 pass/fail "
     "由链条自己写进这份 json 的 n_total_in_table / n_pass / failed 三个键, 本索引不抄数; "
     "只由 `flow/run_all.py` 不带步名地整表跑完时写"),
    ("_probe_subset_NOT_A_VERDICT.json", "子集探针产物", "按**步名**过滤的那一趟写这里而不"
     "是 matrix.json (名字里就写着「这不是判决」; 它以前叫 matrix_subset.json, 与认证归档"
     "只差一个词, 误读风险实测存在)。用法是 `python flow/run_all.py <步名...>`; 本脚本没有"
     " --only 这个开关, 未知开关会被直接拒绝); 里面的 all_green 为 false 只代表那趟只跑了"
     "几格, 不是矩阵崩了"),
    ("HISTORICAL_ARCHIVES.json", "现行登记", "results/ 读数按 RTL 哈希分三类 "
     "(当前树复现 / 旧代历史 / 不记指纹), 由 tools/historical_archives.py 现填"),
    ("README.md", "现行说明", "本目录的读法与「真源 vs 副本 vs 历史对照」的分工"),
    ("sim/", "现行证据", "逐用例判决归档（随机流那一档的读数在 `rand_trace/`，EPV 单元级台架没有）"),
    ("isco/", "现行证据", "黄金 ISS 与核的逐条退休轨迹比对, 连两侧源码哈希一起记"),
    ("compliance/", "现行证据", "官方 riscv-tests 三套件逐用例结果"),
    ("bench/", "现行证据", "周期数与加速比归档"),
    ("synth/", "现行证据", "Yosys 面积口径归档"),
    ("pnr/", "现行证据 + 历史臂", "安路 TD 两档综合与布线读数; 其中的 rtl/ 是综合器"
     "实际吃进去的快照, 不许「顺手同步」成现役树"),
    ("lint/", "现行证据", "Verilator 两档 (EPV_ENABLE 开/关) 0 error 0 warning"),
    ("logs/", "过程日志", "矩阵与探针的原始 stdout; 未认证的运行也留, 按 UTC 命名"),
    ("trace/", "可再生中间件", "全量逐周期退休轨迹 (.txt.gz); 证据的压缩形态, "
     "复跑即再生"),
    ("cov/", "现行证据", "覆盖率臂的 line/toggle 汇总 (本轮新增)"),
    ("rand_trace/", "现行证据", "约束随机指令流与黄金 ISS 的逐条比对 (本轮新增)"),
    # 没有 epv_unit/ 这一条：那个模块级台架本轮**没做**（台账 A.1 第 28 行、
    # tb/README.md 也明写它不在包里）。替一条不存在的归档预先登记「现行证据」，
    # 等于哪天有人建出同名目录时无条件拿到「这是证据」的章——那是替没做的事占位。
    ("asserts/", "现行证据", "断言注入臂 (发数由 tools/defect_count_gate.py 与 "
     "flow/assert_selfcheck.py 的判决行现算, 本索引不抄) 的判决存档, 带它当时的逐文件 "
     "rtl 指纹; 正向臂那一步会核这份存档属不属于当前这一代"),
    ("demo/", "现行证据", "成片与逐段产物"),
    ("wave/", "现行证据", "VCD 与压缩件 + 收条"),
    ("backup/", "历史对照", "各轮换代前的整份镜像; 配对对照的旧侧数据源, 不是垃圾"),
    # ---- 下面这些是 R4 轮由「自动发现」那一节真数出来、再逐条回源码认领的条目。
    # ---- 认领依据是 flow/ 与 tools/ 里点名写它的生产者; 生产者写不出来的那一条
    # ---- 明写「未认领」, 不猜角色。
    ("iss/", "现行证据", "黄金 ISS 自测的聚合判决 (assertions/failed/rc + 那份 .py 的"
     "哈希头), 由 `flow/run_all.py` 的 iss_selftest 步现写"),
    ("isco_probe/", "现行证据", "EPV 指令级的协同仿真探针产物, 由 "
     "`flow/iss_compare_epv.py` 写"),
    ("ppt/", "现行证据", "PPT 逐页核对的机器读数, 由 `tools/verify_ppt.py` 写"),
    ("traces/", "可再生中间件", "轨迹收成器自己的收条 (收成后的清单与省下字节数), 由 "
     "`flow/compact_traces.py` 写"),
    ("elf_manifest.json", "现行登记", "build/ 里每个 ELF 指到哪个用例与哪份判决归档, "
     "由 `tools/elf_manifest.py` 现填"),
    ("elf_manifest.md", "现行登记", "上一那条 json 的人读视图, 同一个生产者"),
    ("word_limits.json", "现行证据", "交付正文逐节字数读数, 由 `tools/check_word_limits.py` 写"),
    ("word_style.json", "现行证据", "字数与文体的第二口径读数, 由 `tools/word_style_count.py` 写"),
    ("soc_td_run.log", "未认领", "**盘上有、但本包没有任何生产者点名写过它** —— 一条"
     "手工/一次性运行留下的 stdout, 既不在链条步骤表里也没有 writer; 本轮只登记这个"
     "事实, 不替它编角色"),
]
# 上面这些「认领」条目的依据是 flow/ 与 tools/ 里点名写它的生产者; 生产者写不出来的
# 那一条明写「未认领」, 不猜角色。

LOGS_KEEP = ("matrix_final", "matrix_run", "assert", "rand", "cov", "unit")

# 本工具自己写出去的那几份。列目录时必须按**真实路径**把它们剔掉: 不剔的话
# 第一趟生成的 INDEX 会在第二趟被数成「未登记的新条目」与「一份新日志」,
# 于是 --check 永远报 DRIFT —— 生成件不许把自己的产物算进分母。
GENERATED = (os.path.join("results", "INDEX.md"),
             os.path.join("results", "logs", "INDEX.md"),
             os.path.join("refs", "README.md"),
             os.path.join("tools", "README.md"),
             os.path.join("flow", "README.md"))
GENERATED_REL = set(g.replace(os.sep, "/") for g in GENERATED)
GENERATED_ABS = set(os.path.realpath(os.path.join(ROOT, g)) for g in GENERATED)

# 判决文件本身不是「证据归档」: 比较「有没有证据晚于 matrix.json」时必须把它们剔掉,
# 否则子集探针产物与 matrix.json 自己就会把自己判成过期。旧名一并留着 —— 换代前的树里
# 它可能还在盘上。
VERDICT_FILES = ("matrix.json", "_probe_subset_NOT_A_VERDICT.json", "matrix_subset.json")
# README.md 是**人写的说明件**, 不属于「某一趟运行落下的证据」: 它被编辑过不该让矩阵
# 判过期 (实测: 一份 results/README.md 的手工编辑让判据点名了一个根本不是证据的对象)。
DOC_BASENAMES = ("README.md",)

# Demo 驱动自己的尾部产物：`bash flow/demo.sh` 在第 9 步整表跑链条并把末行判决重定向进
# `results/demo/09_matrix.txt`，之后再重出成片；而「按最终矩阵重出一遍成片」正是让 sidecar
# 的矩阵时刻指向现役矩阵的唯一次序。这三份不是「晚于判决的新证据」，是同一次出件的尾部：
# 它们与现役归档是否同源由 flow/check_demo_artifacts.py 按**内容**判（J3 逐键读数、
# J4 rtl 头、J5 逐格判决），不靠这里的 mtime 启发式。剔掉时把条数印进 VERDICT_CACHE，
# 让「剔了谁」可见；被剔名单只许这三个具名相对路径。
DEMO_TAIL_REL = ("results/demo/run.log",
                 "results/demo/demo_video.mp4",
                 "results/demo/demo_video.json")

# 夹具名单, 末行分母由它现算。
FIXTURE_NAMES = ("idempotent_build", "self_exclusion", "denom_nonzero",
                 "splice_single_block", "refs_paths_resolve",
                 "refs_forced_line_refused", "figures_recomputed",
                 "stale_verdict_detected", "flow_block_idempotent")


# ---------------------------------------------------------------- 现算量登记簿
# 生成时把「我印了哪个数、它是走哪棵树算出来的、它印成长什么样」记在一起;
# 落盘之后逐条回核。
def rec_fig(figs, label, target, anchor, files, size, token=u"| %d | %.1f MiB |"):
    figs.append({"label": label, "target": target, "anchor": anchor,
                 "files": files, "mib": round(mib(size), 1), "token": token})


def row_find(text, anchor):
    """按行锚点把印出去的那一行抠回来 (跨行/被折行都不算找到)。"""
    hits = [ln for ln in text.splitlines() if anchor in ln]
    return hits[0] if len(hits) >= 1 else None


def verify_figures(text, figs):
    """D2 的自证: 对每个登记过的数, 再走一次树, 并要求**回读到的那份文件里那一行**
    仍带着同一个数。三种失败形状分开报: 树动了 / 行找不回 / 位数不是那一行。
    返回 (problems, n_checked)。
    """
    problems = []
    for f in figs:
        n, size, _t, _r = walk_stats(f["target"])
        now_mib = round(mib(size), 1)
        if n != f["files"] or now_mib != f["mib"]:
            problems.append("FIGURE-STALE %s walked_files=%d printed_files=%d "
                            "walked_mib=%.1f printed_mib=%.1f" % (
                                _a(f["label"]), n, f["files"], now_mib, f["mib"]))
            continue
        line = row_find(text, f["anchor"])
        if line is None:
            problems.append("FIGURE-ROW-MISSING %s anchor=%s" % (
                _a(f["label"]), _a(f["anchor"])))
            continue
        if (f["token"] % (f["files"], f["mib"])) not in line:
            problems.append("FIGURE-PRINT-MISMATCH %s line=%s" % (
                _a(f["label"]), _a(line.strip()[:120])))
    return problems, len(figs)


# ---------------------------------------------------------------- matrix.json 来历
def verdict_cache_state(root=ROOT):
    """现读 matrix.json 的 started_utc、它自己的落盘时刻, 以及树里最新的证据。

    分辨「本件跑在矩阵落盘之前」的判据是 **有没有证据归档比 matrix.json 还新**:
    一趟运行里归档必然都在该趟 started_utc 之后落盘, 所以拿 started_utc 与归档 mtime
    直接比会永远红; 而矩阵落盘之后不该再有证据被写。
    """
    mp = os.path.join(root, "results", "matrix.json")
    st = {"matrix": os.path.relpath(mp, root).replace(os.sep, "/"),
          "started_utc": None, "matrix_mtime": None,
          "newest_mtime": 0.0, "newest_path": "", "stale": True,
          "tail_excluded": 0,
          "partial": True, "steps_in_matrix": None, "n_total_in_table": None,
          "newer_than_verdict": False, "reason": "MATRIX-MISSING"}
    if not os.path.isfile(mp):
        return st
    st["matrix_mtime"] = os.path.getmtime(mp)
    try:
        mm = json.loads(rd(mp))
        st["started_utc"] = mm.get("started_utc")
    except Exception as e:
        st["reason"] = "MATRIX-UNPARSABLE %s" % type(e).__name__
        return st
    base = os.path.join(root, "results")
    for dp, _dn, fns in os.walk(base):
        for fn in fns:
            p = os.path.join(dp, fn)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            if rel in GENERATED_REL or os.path.basename(fn) in VERDICT_FILES:
                continue
            if os.path.basename(fn) in DOC_BASENAMES:
                continue
            if rel in DEMO_TAIL_REL:
                st["tail_excluded"] += 1
                continue
            try:
                t = os.stat(p).st_mtime
            except OSError:
                continue
            if t > st["newest_mtime"]:
                st["newest_mtime"], st["newest_path"] = t, rel
    if not st["newest_path"]:
        st["reason"] = "NO-ARCHIVE-MEASURED"
        return st
    # 关键分档: 链条在 POST_STEPS 之前先 emit 一次(只有前置步的 rows), 跑完才 emit
    # 终表。所以「矩阵是中间落盘」(steps != n_total_in_table) 时, 后置步写的归档**必然**
    # 比 matrix.json 新 —— 那正是链条的正常形状, 不能判过期, 否则又造出一发永远红的门。
    # 只有当矩阵已经是终表(steps == n_total_in_table)而仍有归档比它新, 才说明盘上这份
    # 判决属于更早的一趟: 那才是本判据要拦的东西。
    st["partial"] = (mm.get("steps") != mm.get("n_total_in_table")) or bool(
        mm.get("ran_subset"))
    st["steps_in_matrix"] = mm.get("steps")
    st["n_total_in_table"] = mm.get("n_total_in_table")
    st["newer_than_verdict"] = st["newest_mtime"] > st["matrix_mtime"]
    st["stale"] = st["newer_than_verdict"] and not st["partial"]
    if st["stale"]:
        st["reason"] = "EVIDENCE-NEWER-THAN-FINAL-VERDICT"
    elif st["partial"] and st["newer_than_verdict"]:
        st["reason"] = "MATRIX-PARTIAL-RUN-IN-PROGRESS"
    else:
        st["reason"] = "OK"
    return st


# ---------------------------------------------------------------- results/INDEX.md
def gen_results_index(root=ROOT, figs=None):
    """阅读顺序表。每个数都出自生成这一刻的一次走树, 并登记进 figs 供落盘后回核。"""
    figs = [] if figs is None else figs
    res_dir = os.path.join(root, "results")
    L = [u"# results/ 阅读顺序（生成件，勿手改）",
         u"",
         u"> 由 `python tools/make_navigation.py --write` 生成；`--check` 会核对盘上这份",
         u"> 与现在重生成的那份是否逐字节相同。体积、文件数、时刻全部由落盘这一刻走一遍",
         u"> 树算出，写完还把本文件读回来把每个数再核一次（对不上就点名 FIGURE-* 判红），",
         u"> 只有「这一格是什么角色」那句话归人写。",
         u""]
    st = verdict_cache_state(root)
    mpath = os.path.join(root, "results", "matrix.json")
    if os.path.isfile(mpath):
        m = json.loads(rd(mpath))
        L += [u"- 本目录的**唯一交付结论**是 `matrix.json`：`started_utc=%s`，"
              u"`n_pass=%s`，`failed=%s`，`skipped=%s`，`steps=%s`，"
              u"`all_green=%s`，`ran_subset=%s`。" % (
                  m.get("started_utc"), m.get("n_pass"), m.get("failed"),
                  m.get("skipped"), m.get("steps"), m.get("all_green"),
                  m.get("ran_subset")),
              u"- 上面这行属于**哪一趟**由这份文件自己的落盘时刻决定："
              u"`matrix.json` mtime = `%s`；它之后盘上若还落下别的证据，"
              % (utc(st["matrix_mtime"]) if st["matrix_mtime"] else u"?"),
              u"  就说明本索引是在矩阵落盘**之前**写的，读到的结论属于上一趟。",
              u"- 这条判据另有具名一档：Demo 驱动的尾部产物 %d 份（`results/demo/run.log`、"
              u"`results/demo/demo_video.mp4`、`results/demo/demo_video.json`）不算「晚于判决的新证据」——"
              u"按最终矩阵重出成片是 sidecar 指向现役矩阵的唯一次序，而这三份与现役归档是否同源由 "
              u"`flow/check_demo_artifacts.py` 按内容判（J3 逐键读数 / J4 rtl 头 / J5 逐格判决），不靠 mtime。"
              % st.get("tail_excluded", 0),
              u"- 本目录树里最新的证据（剔掉本工具的索引、人写的 README.md 与判决 json）："
              u"`%s` @ `%s`。" % (st["newest_path"] or u"（无）",
                                  utc(st["newest_mtime"]) if st["newest_mtime"] else u"?"),
              u"- 引用任何一个数之前先核这一行的 `started_utc` 是不是你关心的那一趟；",
              u"  链条只在末尾落盘，跑动期间盘上这份属于**上一趟**。",
              u""]
    L += [u"| 先看/再看 | 名称 | 角色 | 文件数 | 体积 | 为什么 |",
          u"|---|---|---|---:|---:|---|"]
    order = []
    absent_roles = []
    for name, role, why in RESULT_ROLES:
        p = os.path.join(root, "results", name.replace("/", os.sep))
        if not os.path.exists(p):
            absent_roles.append(name)
            continue
        order.append((name, role, why, p))
    for rank, (name, role, why, p) in enumerate(order):
        n, sz, _t, _r = walk_stats(p)
        rec_fig(figs, "row:" + name, p, u"| `%s`" % name, n, sz)
        L.append(u"| %s | `%s` | %s | %d | %.1f MiB | %s |" % (
            "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"[rank] if rank < 20 else str(rank + 1),
            name, role, n, mib(sz), why))
    # 自动发现: 盘上有、表里没登记的条目。以前这几行缩进在 continue 之后, 于是
    # `extra` 恒为空、"未登记 0 项" 是一句按构造成立的假话。现在它是真判据。
    listed = set(n.rstrip("/").strip("/") for n, _r, _w2, _p in order)
    extra = []
    for entry in sorted(os.listdir(res_dir)):
        if entry in listed:
            continue
        full = os.path.join(res_dir, entry)
        if os.path.realpath(full) in GENERATED_ABS:
            continue
        p = full
        n, sz, _t, _r = walk_stats(p)
        rec_fig(figs, "extra:" + entry, p, u"`%s/`" % entry, n, sz,
                token=u"文件数 %d 体积 %.1f MiB")
        extra.append((entry, n, sz))
    L += [u"", u"## 没被上面点到的条目（自动发现，条数由本表现算）", u""]
    if not extra:
        L.append(u"（无 —— 本目录每一项都在上面有归属；这一句本身是判据："
                 u"出现新目录而本表没更新时，本节会先列出它，然后下面那条守恒式的"
                 u"未登记计数就非零）")
    for entry, n, sz in extra:
        L.append(u"- `%s/` 文件数 %d 体积 %.1f MiB —— **未登记**" % (entry, n, mib(sz)))
    tot_n, tot_sz, tot_t, tot_r = walk_stats(res_dir)
    parts_n, parts_sz = 0, 0
    for entry in sorted(os.listdir(res_dir)):
        p = os.path.join(res_dir, entry)
        if os.path.realpath(p) in GENERATED_ABS:
            parts_n += 1
            parts_sz += os.path.getsize(p)
            continue
        en, esz, _t, _r = walk_stats(p)
        parts_n += en
        parts_sz += esz
    L += [u"", u"守恒式：本目录共 %d 个文件 / %.1f MiB；上面登记了 %d 项，"
          u"未登记 %d 项；逐条目相加 %d 个文件（与前者不等即判红）。"
          u"登记表里指向盘上不存在对象的行有 %d 条：%s。" % (
              tot_n, mib(tot_sz), len(order), len(extra), parts_n,
              len(absent_roles),
              u", ".join(u"`%s`" % a for a in absent_roles) or u"（无）"),
          u""]
    counts = {"entries": len(order), "extra": len(extra), "total_files": tot_n,
              "total_bytes": tot_sz, "parts_files": parts_n, "parts_bytes": parts_sz,
              "absent_roles": len(absent_roles), "absent_names": absent_roles}
    return u"\n".join(L), counts


# ---------------------------------------------------------------- logs/INDEX.md
def gen_logs_index(root=ROOT, figs=None):
    figs = [] if figs is None else figs
    d = os.path.join(root, "results", "logs")
    files = []
    for fn in sorted(os.listdir(d)):
        p = os.path.join(d, fn)
        if (os.path.isfile(p)
                and os.path.relpath(p, root).replace(os.sep, "/") not in GENERATED_REL):
            files.append((fn, os.path.getmtime(p), os.path.getsize(p)))
    st = verdict_cache_state(root)
    started = st["started_utc"]
    try:
        sdt = datetime.datetime.strptime(started, "%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        sdt = None
    stamp = datetime.datetime.utcfromtimestamp
    L = [u"# results/logs 索引（生成件，勿手改）", u"",
         u"> 由 `python tools/make_navigation.py --write` 生成。本目录是过程日志：",
         u"> 未认证的运行也留（这是有意的，见 `results/README.md`），所以「哪一份",
         u"> 对应盘上那份 matrix.json」必须被算出来，而不是靠人记得。", u"",
         u"- `results/matrix.json` 的 `started_utc = %s`；这份矩阵自己的落盘时刻 = `%s`"
         u"（两个数都是本文件写出**当时**现读的，不是上一趟的）" % (
             started, utc(st["matrix_mtime"]) if st["matrix_mtime"] else u"?"),
         u"- 树里最新的证据（剔掉本工具的索引、README.md 与判决 json）是 `%s` @ `%s`；"
         u"它比上面那个矩阵落盘时刻还新 ⇒ 本索引写早了，判据名 "
         u"`STALE-VERDICT-CACHE`。" % (st["newest_path"] or u"（无）",
                                       utc(st["newest_mtime"]) if st["newest_mtime"]
                                       else u"?"),
         u"- 下面按 mtime 与它配对：**mtime >= 本趟起始时刻**的才可能是这一趟写的。", u""]
    hit = 0
    L += [u"| 文件 | mtime(UTC) | 大小(B) | 与 matrix.json 那一趟的关系 |",
          u"|---|---|---:|---|"]
    for fn, mt, sz in files:
        rel = u"更早，属于上一趟"
        if sdt is not None and stamp(mt) >= sdt:
            rel = u"**不早于本趟起始** → 可能是这一趟写的"
            hit += 1
        L.append(u"| `%s` | %s | %d | %s |" % (fn, stamp(mt).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                              sz, rel))
    L += [u"", u"共 %d 份，其中 %d 份不早于 matrix.json 的 `started_utc`。" % (len(files), hit),
          u"要看结论只看 `../matrix.json` 与它自己的 `steps` 键（那个数由链条按步表现算，",
          u"本索引不抄），不要从本目录挑一份看着像最终的。", u""]
    counts = {"logs": len(files), "hits": hit, "bytes": sum(s for _f, _m, s in files)}
    return u"\n".join(L), counts


def verify_logs_counts(text, root=ROOT):
    """把 logs/INDEX.md 里那两个数抠回来，再走一遍目录对账。"""
    problems = []
    d = os.path.join(root, "results", "logs")
    n = 0
    for fn in sorted(os.listdir(d)):
        p = os.path.join(d, fn)
        if (os.path.isfile(p)
                and os.path.relpath(p, root).replace(os.sep, "/") not in GENERATED_REL):
            n += 1
    m = re.search(u"共 (\\d+) 份，其中 (\\d+) 份", text)
    if not m:
        problems.append("LOGS-COUNT-UNPARSEABLE")
    elif int(m.group(1)) != n:
        problems.append("LOGS-COUNT-STALE printed=%d walked=%d" % (int(m.group(1)), n))
    return problems, n


# ---------------------------------------------------------------- refs/README.md
# D1: 片段必须先解析到真实对象才许落纸。语料是 flow/ 与 tools/ 的 .py/.sh。
REFS_FRAG = re.compile(r"(?<![A-Za-z0-9_./\\-])refs(?:[/\\]+[A-Za-z0-9_.\-]+)+")
SEG_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")


def resolve_refs_fragment(root, frag):
    """把一个 `refs/...` 片段解析到包内真实对象。返回 (kind, rel) 或 (None, rel)。

    kind: "file" / "dir" / None。规则:
      * 逐段校验段名字符集, 不许空段、`.`、`..`、全点段 —— Windows 会把 `refs/...`
        的尾点剥掉而解析成 `refs` 目录本身, 那是一条**不存在的路径被判存在**的形状;
      * 解析结果必须仍在 root/refs 之下 (realpath 前缀判据), 防 `..` 逃逸;
      * basename 必须等于片段的末段, 否则说明发生了点号折叠之类的路径魔法;
      * 目录要 isdir, 文件要 isfile —— 二选一, 不许 isexists 蒙过。
    """
    comps = [c for c in re.split(r"[/\\]+", frag) if c != ""]
    rel = "/".join(comps)
    if not comps or comps[0] != "refs":
        return None, rel
    for c in comps[1:]:
        if c in (".", "..") or set(c) == {"."} or not SEG_OK.match(c):
            return None, rel
    target = os.path.join(root, *comps)
    base = os.path.realpath(os.path.join(root, "refs"))
    real = os.path.realpath(target)
    if os.path.normcase(real) != os.path.normcase(target.replace("/", os.sep)):
        return None, rel
    if os.path.commonpath([base, real]) != base:
        return None, rel
    if comps[-1] != os.path.basename(real):
        return None, rel
    if os.path.isdir(real):
        return "dir", rel
    if os.path.isfile(real):
        return "file", rel
    return None, rel


def refs_corpus(root=ROOT):
    """语料 = flow/ 与 tools/ 根下的 .py/.sh。

    本件自己不进气味里: 它的夹具字符串与正则字面量里全是 `refs/xx.md` 这种
    故意不存在的路径, 让它们进气味就等于把「本件证明了它拒绝垃圾」这件事变成
    「本件自己在生产垃圾」。与上面 GENERATED 的自我排除同一分工。
    """
    out = []
    me = os.path.basename(__file__)
    for base in ("flow", "tools"):
        d = os.path.join(root, base)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            p = os.path.join(d, fn)
            if os.path.isfile(p) and fn.endswith((".py", ".sh")) and fn != me:
                out.append((base + "/" + fn, rd(p)))
    return out


def refs_section(root=ROOT, corpus=None):
    """返回 (行列表, 统计, 已发射行的解析目标)。发射的每一行都自带解析凭据。"""
    corpus = corpus if corpus is not None else refs_corpus(root)
    used = {}
    for src, text in corpus:
        for m in REFS_FRAG.finditer(text):
            used.setdefault(m.group(0).replace("\\", "/"), set()).add(src)
    lines, emitted, rejected = [], [], []
    for frag in sorted(used):
        kind, rel = resolve_refs_fragment(root, frag)
        if kind is None:
            rejected.append(rel)
            continue
        p = os.path.join(root, *rel.split("/"))
        lines.append(u"- `%s` — 已核为盘上%s，← %s" % (
            rel, u"目录" if kind == "dir" else u"文件", ", ".join(sorted(used[frag]))))
        emitted.append({"label": "refs:" + rel, "target": p,
                        "anchor": u"`%s`" % rel, "kind": kind,
                        "files": None, "mib": None})
    listed = len(lines)
    resolved = sum(1 for e in emitted
                   if resolve_refs_fragment(root, e["label"][5:])[0] is not None)
    stats = {"listed": listed, "resolved": resolved,
             "unmatched": listed - resolved, "rejected": len(rejected),
             "found": len(used)}
    return lines, stats, emitted


def verify_refs_lines(root, emitted):
    """发射之后回核: 印出去的每一个路径**现在**仍解析得到。不等即非零退出。"""
    problems = []
    for e in emitted:
        kind, _rel = resolve_refs_fragment(root, e["label"][5:])
        if kind is None:
            problems.append("REFS-UNRESOLVED-LINE %s" % e["label"][5:])
        elif kind != e["kind"]:
            problems.append("REFS-KIND-CHANGED %s printed=%s now=%s" % (
                e["label"][5:], e["kind"], kind))
    return problems


def gen_refs_readme(root=ROOT, figs=None):
    figs = [] if figs is None else figs
    top = sorted(os.listdir(os.path.join(root, "refs")))
    lines, stats, emitted = refs_section(root)
    L = [u"# refs/ 说明（生成件，勿手改）", u"",
         u"> 由 `python tools/make_navigation.py --write` 生成。`refs/riscv-tests/` 是上游",
         u"> 仓库的完整拷贝（文件数见下面那一行，由本件现走树数出来），本包真正读到的只有",
         u"> 其中一小部分；哪些被点名不靠人记，靠下面在 `flow/` 与 `tools/` 源码里搜出来的",
         u"> 路径片段 —— 但**只有解析到盘上真实对象的那几条才会被写进来**。", u""]
    L += [u"## 本目录顶层", u""]
    for e in top:
        p = os.path.join(root, "refs", e)
        n, sz, _t, _r = walk_stats(p)
        rec_fig(figs, "refs-top:" + e, p, u"- `%s`" % e, n, sz,
                token=u"文件数 %d，体积 %.1f MiB")
        L.append(u"- `%s` — 文件数 %d，体积 %.1f MiB" % (e, n, mib(sz)))
    L += [u"", u"`rt.tar.gz` 与 `riscv-tests/` 的关系：前者是来源包，后者是它解出的结果。",
          u"两份都在是因为交付时要能证明解出来的树与归档对得上（`verify-unpacked-tree`"
          u"那一类判据），不是因为内容重复。", u"",
          u"## 被 flow/ 与 tools/ 源码点名的 refs/ 子路径", u"",
          u"这一节只列**解析得到**的片段：以前是把搜到的片段直接拼在 `refs/` 后面写出来，",
          u"于是盘上没有的东西（含一条纯垃圾片段）也进了索引而无人发现。现在片段按包内路径",
          u"解析，目录要 `isdir`、文件要 `isfile`，并且逐段验名（`refs/...` 这种占位片段在",
          u"Windows 上会被折叠成 `refs` 目录本身，所以形状守卫先把它挡掉）。", u""]
    if lines:
        L += lines
    else:
        L.append(u"（一条都没搜到 —— 这本身要判红：说明引用方式变了或本函数失效）")
    L += [u"",
          u"listed=%d resolved=%d unmatched=%d rejected=%d found=%d" % (
              stats["listed"], stats["resolved"], stats["unmatched"],
              stats["rejected"], stats["found"]),
          u"",
          u"`unmatched` 是「印出来了但解析不到」的条数，它非零本件就非零退出；`rejected` 是",
          u"语料里点到但盘上没有、因而**没有**被写进本节的片段数（散文里的举例、占位串、",
          u"以及挂在 `docs/refs/` 下却被写成 `refs/` 的片段都落在这一档）。", u""]
    lic = os.path.join(root, "refs", "THIRD_PARTY_LICENSES")
    if os.path.isdir(lic):
        subs = [e for e in sorted(os.listdir(lic))
                if os.path.isdir(os.path.join(lic, e))]
        L += [u"", u"## 第三方许可全文", u"",
              u"`refs/THIRD_PARTY_LICENSES/` 下 %d 个子目录：%s。" % (
                  len(subs), ", ".join(subs)),
              u"收编与「拿不到的必须显式声明」的判据在 "
              u"`tools/third_party_licenses.py`（`LICENSE_GATE_OK`）。", u""]
    return u"\n".join(L), stats, emitted


# ---------------------------------------------------------------- flow/README.md
FLOW_BEGIN = u"<!-- FLOW_STEPS:auto -->"
FLOW_END = u"<!-- /FLOW_STEPS:auto -->"
FLOW_PROSE = u"""# flow/ 说明（部分生成件）

本目录是「一条命令跑齐全套证据」的那条链：`run_all.py` 是矩阵本体（每步既跑命令也
断言归档里的字段，命令退 0 而没产出预期字段就判红），其余是它逐步调用的门禁与归档
写入者 —— 面积/布线/波形/文档/字数/成片各一支，跑完把读数落进 `results/`。步序不
是摆设：好几步读的是**前一步刚落盘**的那份归档（例如 `isco_trace` 读
`results/compliance/*.json` 当分母、`consistency` 读本趟的 docx 与 pptx），所以
「能不能打乱顺序」这件事的答案，写在下面那张表的「写归档」一列里。

下面那一格表是生成件：步名一个都不手抄，由 `tools/make_navigation.py` import 本目录
的 `run_all.py` 现读 `STEPS` / `POST_STEPS` 生成。「写归档 / 不写归档」这一格按该模块
自己的源码与 `results/HISTORICAL_ARCHIVES.json` 的 `produced_by` 登记推，推不出来
的印 `UNKNOWN` 而不是猜一个 —— 要改本目录的说明文字请改标记**之外**的段落，标记
**之内**改了会在下一次生成时被抹掉。

""" + FLOW_BEGIN + u"\n\n" + FLOW_END + u"\n"

WRITE_PRIMS = re.compile(r"write_json_atomic\s*\(|io\.open\([^)\n]*,\s*[\"']w[\"']"
                         r"|open\([^)\n]*,\s*[\"']w[\"']|os\.makedirs\s*\(|"
                         r"shutil\.(?:copy|copyfile|move)\s*\(")
# shell 那支的写形态完全不同: 重定向与 tee 才是落盘动作。
SHELL_WRITE = re.compile(r">>?\s*\"?\$|\btee\b|mkdir -p")
RESULTS_REF = re.compile(r"\"results\"|results/|results\\\\")
QUOTED_SCRIPT = re.compile(r"\"([A-Za-z0-9_./\\-]+\.(?:py|sh))\"")
READISH = re.compile(r"jload\s*\(|\brd\s*\(|json\.load|\.read\(|open\([^)\n]*encoding")
SANDBOX = re.compile(r"\bbox\b|tempfile|tmp|_box|mkdtemp|rmtree")
# 子脚本按包内真实目录找: 步骤源码里写的常是 os.path.join("tools", "x.py") 这种两截
# 字面量, 也可能整支写 "flow/lint_rtl.sh" —— 两种都认。找不到的一律记成「不可判」。
CHILD_DIRS = ("flow", "tools", os.path.join("tb", "golden"), "sw", "rtl")


def _run_all_helpers(ra):
    """本模块自己定义的函数名集合 —— 闭包只在 run_all 的命名空间里走, 不许越界。"""
    return set(n for n, v in vars(ra).items()
               if callable(v) and getattr(v, "__module__", "") == ra.__name__
               and getattr(v, "__name__", "") == n)


def _results_bound_names(text):
    """哪些变量名在这份源码里被绑到 results/ 路径上。

    `d = os.path.join("results", "iss")` 之后 `write_json_atomic(os.path.join(d, ...))`
    才是写归档 —— 写原语与 results/ 常隔着一行以上, 只看同一行会漏判。for 解包
    (`for log, cmd, mod in jobs:`) 也算一跳绑定传递。
    """
    names = set()
    for m in re.finditer(r"^\s*([A-Za-z_]\w*)\s*=\s*(.+)$", text, re.M):
        rhs = m.group(2)
        if RESULTS_REF.search(rhs) and not READISH.search(rhs) and not SANDBOX.search(rhs):
            names.add(m.group(1))
    for _ in range(3):
        grew = False
        # for 解包: `for log, cmd, mod in jobs:` 把 jobs 的绑定传给 log
        for m in re.finditer(r"^\s*for\s+([A-Za-z_,\s]+?)\s+in\s+([A-Za-z_]\w*)\s*[:(]", text, re.M):
            if m.group(2) in names:
                for t in m.group(1).split(","):
                    if t.strip() and t.strip() not in names:
                        names.add(t.strip())
                        grew = True
        # 变量再派生: `summ = os.path.join(td, fn + ".summary.txt")` 里 td 已绑到
        # results/ —— 不传这一跳, 住在变量里的写目标就全被判成「解不出来」。
        for m in re.finditer(r"^\s*([A-Za-z_]\w*)\s*=\s*(.+)$", text, re.M):
            rhs = m.group(2)
            if m.group(1) in names or SANDBOX.search(rhs) or READISH.search(rhs):
                continue
            if any(re.search(r"%s" % re.escape(b), rhs) for b in names):
                names.add(m.group(1))
                grew = True
        if not grew:
            break
    return names


def writes_into_results(text, shell=False):
    """三态判决: True 写 / False 不写 / None 判不出来 (调用方须印 UNKNOWN)。

    成立条件: 这份源码里有写动作, **且**该写动作的目标是 results/ 下的对象 ——
    要么同一行就带着 results/, 要么它写的变量名在这份源码里被绑到 results/ 路径。
    替身房(box / tempfile / rmtree) 里的那份不算, 只读语境 (jload / rd / open 读) 不算。
    """
    prims = SHELL_WRITE if shell else WRITE_PRIMS
    if not RESULTS_REF.search(text):
        return False          # 整份源码一次都不提 results/ —— 它写不出那里的对象
    wl = [ln for ln in text.splitlines() if prims.search(ln)]
    if not wl:
        return False
    bound = _results_bound_names(text)
    for ln in wl:
        if SANDBOX.search(ln):
            continue
        if RESULTS_REF.search(ln):
            return True
        if any(re.search(r"\b%s\b" % re.escape(b), ln) for b in bound):
            return True
    return None


def step_closure_source(ra, fn):
    """把一步**在本模块内**能到达的源码并起来。

    步骤普遍只写一行 `return _bench(...)` 或 `return _check_suite(ctx, "rv32ui")`，
    真正的写入动作住在 helper 里 —— 只看函数自己会得出「它不写归档」的假结论。
    闭包只在 run_all 自己的命名空间里走, 不越界去读别的模块的源码。
    """
    names = _run_all_helpers(ra)
    work, seen, srcs = [fn.__name__], set(), []
    while work:
        nm = work.pop()
        if nm in seen:
            continue
        seen.add(nm)
        f = getattr(ra, nm, None)
        if f is None or not callable(f) or getattr(f, "__module__", "") != ra.__name__:
            continue
        try:
            s = inspect.getsource(f)
        except (OSError, TypeError):
            continue
        srcs.append(s)
        for other in names:
            if other not in seen and re.search(r"\b%s\s*\(" % re.escape(other), s):
                work.append(other)
    return "\n".join(srcs)


def child_scripts_called(root, src):
    """返回 (能定位到盘上文件的子脚本相对路径, 定位不到的名字)。"""
    found, missing = [], []
    for name in sorted(set(QUOTED_SCRIPT.findall(src))):
        bare = name.replace("\\", os.sep).replace("/", os.sep).split(os.sep)[-1]
        if bare == "run_all.py":
            continue   # 矩阵在自己源码里点名自己(沙盒/注释), 那不是「调用的子脚本」
        hit = None
        cand = os.path.join(root, name.replace("/", os.sep).replace("\\", os.sep))
        if os.path.isfile(cand):
            hit = os.path.relpath(cand, root).replace(os.sep, "/")
        else:
            for d in CHILD_DIRS:
                p = os.path.join(root, d, bare)
                if os.path.isfile(p):
                    hit = os.path.relpath(p, root).replace(os.sep, "/")
                    break
        if hit:
            found.append(hit)
        else:
            missing.append(name)
    return found, missing


def child_writes_results(root, rel):
    """子脚本自己是否往 results/ 写东西。True/False 是判决, None 是没读到文件。"""
    p = os.path.join(root, rel.replace("/", os.sep))
    if not os.path.isfile(p):
        return None
    return writes_into_results(rd(p), shell=rel.endswith(".sh"))


def flow_steps_table(root=ROOT):
    """import 真模块读步骤表 —— 步名一律不抄。返回 (行列表, 统计)。"""
    stats = {"steps": 0, "pre": 0, "post": 0, "writes": 0, "checks": 0,
             "unknown": 0, "module": "flow/run_all.py", "sha16": "UNIMPORTABLE",
             "mtime": "", "error": ""}
    old_cwd = os.getcwd()
    try:
        sys.path.insert(0, os.path.join(root, "flow"))
        import run_all as ra
    except Exception as e:
        return [u"（import `flow/run_all.py` 失败：%s：%s —— 本表拒绝凭记忆填空）"
                % (type(e).__name__, str(e)[:120])], stats
    finally:
        try:
            os.chdir(old_cwd)
            if sys.path and sys.path[0] == os.path.join(root, "flow"):
                sys.path.pop(0)
        except Exception:
            pass
    mod_path = os.path.join(root, "flow", "run_all.py")
    stats["sha16"] = sha16(mod_path)
    stats["mtime"] = utc(os.path.getmtime(mod_path)) if os.path.isfile(mod_path) else ""
    stats["pre"] = len(ra.STEPS)
    stats["post"] = len(ra.POST_STEPS)
    stats["steps"] = ra.N_TOTAL
    credited = {}
    hp = os.path.join(root, "results", "HISTORICAL_ARCHIVES.json")
    try:
        for e in json.loads(rd(hp)).get("entries", []):
            credited.setdefault(str(e.get("produced_by")), []).append(e.get("path"))
    except Exception:
        credited = {}
    rows = [u"| # | 步骤 | 它产出什么 | 写归档 / 不写归档 | 它的证据文件 |",
            u"|---:|---|---|---|---|"]
    for idx, (name, fn) in enumerate(list(ra.STEPS) + list(ra.POST_STEPS), 1):
        src = step_closure_source(ra, fn)
        own = writes_into_results(src)
        kids, unknown_kids = child_scripts_called(root, src)
        kid_states = [(k, child_writes_results(root, k)) for k in kids]
        kid_write = any(v is True for _k, v in kid_states)
        inconclusive = (bool(unknown_kids) or any(v is None for _k, v in kid_states)
                        or own is None or not src)
        cred = sorted(set(credited.get(name, [])))
        paths = sorted(set(re.findall(r"\"results\"\s*,\s*\"([^\"]+)\"", src))
                       | set(re.findall(r"\"(results/[A-Za-z0-9_.\\-]+)\"", src)))
        if unknown_kids:
            why_own = (u"子脚本名解不到盘上文件: " + u", ".join(unknown_kids[:3])
                       + u"（若它是本步自带的反例夹具名, 那是有意为之, 本格仍不猜）")
        elif any(SANDBOX.search(ln) for ln in src.splitlines() if WRITE_PRIMS.search(ln)):
            why_own = u"这一步的写动作只出现在替身房(box/tempfile)里, 归档目标解不出"
        elif own is None:
            why_own = u"写动作的目标解不出（路径住在参数/返回值里）"
        else:
            why_own = u"点名的子脚本自己写不写 results/ 解不出"
        if cred or own is True or kid_write:
            verdict = u"写归档"
            why_v = (u"`HISTORICAL_ARCHIVES.json` 记它为 produced_by" if cred else
                     (u"本模块内可达源码里有指向 results/ 的写动作" if own is True
                      else u"它点名的子脚本自己写 results/"))
            stats["writes"] += 1
        elif inconclusive:
            verdict = u"UNKNOWN"
            why_v = why_own
            stats["unknown"] += 1
        else:
            verdict = u"不写归档"
            why_v = u"可达源码里没有任何指向 results/ 的写动作"
            stats["checks"] += 1
        ev = u", ".join(u"`%s`" % c for c in cred) if cred else (
            u", ".join(u"`results/%s`" % p for p in paths[:3]) if paths else u"UNKNOWN")
        if cred and len(cred) > 3:
            ev += u" 等 %d 条" % len(cred)
        if not cred and paths and own is True:
            ev += u"（写目标按源码里的路径字面量，未进归档登记）"
        elif own is not True and not cred and kid_write:
            ev = u"由它调用的子脚本写，登记里没记这一步"
        what = []
        if kids:
            what.append(u"调 " + u", ".join(u"`%s`" % k.split("/")[-1] for k in kids[:3]))
        elif unknown_kids:
            what.append(u"调 UNKNOWN 名字: %s" % ", ".join(unknown_kids[:3]))
        elif src:
            what.append(u"在本进程内直接跑模型/断言")
        else:
            what.append(u"UNKNOWN")
        if own is True or cred or kid_write:
            what.append(u"落归档")
        tag = (u"（后置格：读本趟刚落盘的矩阵）"
               if any(name == n for n, _f in ra.POST_STEPS) else u"")
        rows.append(u"| %d | `%s`%s | %s | %s（%s） | %s |" % (
            idx, name, tag, u"；".join(what), verdict, why_v, ev))
    rows.append(u"")
    rows.append(u"> 本块由 `tools/make_navigation.py` 生成：import `%s` 读到 "
                u"`STEPS`=%d + `POST_STEPS`=%d ⇒ `N_TOTAL`=%d；该模块此刻 "
                u"sha256 前 16 位 = `%s`、mtime = `%s`。统计：写归档 %d 步、"
                u"不写归档 %d 步、UNKNOWN %d 步（三个数相加 = N_TOTAL）。" % (
                    stats["module"], stats["pre"], stats["post"], stats["steps"],
                    stats["sha16"], stats["mtime"], stats["writes"],
                    stats["checks"], stats["unknown"]))
    rows.append(u"> 「写归档 / 不写归档 / UNKNOWN」这一格的推法（三条按强弱排, 都不成立才 "
                u"UNKNOWN）：① 这一步在 `results/HISTORICAL_ARCHIVES.json` 里被记为某份归档"
                u"的 `produced_by`；② 它**在本模块内可达的源码**（含它调的 helper，如 "
                u"`_bench` / `_check_suite`）里有写动作且目标是 `results/` 下的对象 —— "
                u"写原语与路径常隔一行，所以顺带认变量绑定，但替身房（`box` / `tempfile` / "
                u"`rmtree`）里那份与纯只读语境（`jload` / `rd`）都不算；③ 它点名的子脚本"
                u"（`.py` / `.sh`，盘上能定位到的）自己写 `results/`。子脚本名解不到盘上文件"
                u"、或写动作的目标解不出来 ⇒ 印 `UNKNOWN`，绝不猜。")
    return rows, stats


def gen_flow_readme(root=ROOT, existing=None):
    """带标记的生成块 + 手写散文; 只替换标记之内, 标记之外归人。"""
    rows, stats = flow_steps_table(root)
    block = u"\n".join([FLOW_BEGIN] + rows + [FLOW_END])
    path = os.path.join(root, "flow", "README.md")
    text = existing if existing is not None else (
        rd(path) if os.path.isfile(path) else None)
    if text is None:
        text = FLOW_PROSE
    body = splice_block(text, FLOW_BEGIN, FLOW_END, block)
    return body, stats


# ---------------------------------------------------------------- tools/README.md
def gen_tools_readme_head():
    """在 tools/README.md 顶部替换/插入一段「现役 vs 历史」的分层表。"""
    root_py = [f for f in sorted(os.listdir(os.path.join(ROOT, "tools")))
               if f.endswith(".py")]
    leg = os.path.join(ROOT, "tools", "legacy")
    leg_py = [f for f in sorted(os.listdir(leg)) if f.endswith(".py")] if os.path.isdir(leg) else []
    block = [u"<!-- BEGIN make_navigation:TOOLS-LAYERING (生成件, 勿手改本块) -->",
             u"",
             u"| 层 | 数量 | 是什么 | 要不要跑 |",
             u"|---|---:|---|---|"]
    block += [u"| `tools/` 根 | %d 个 `.py` | 现役工具（门禁、归档汇综、生成器） | "
              u"要，`flow/run_all.py` 会跑到其中的门禁 |" % len(root_py),
              u"| 一次性改稿笔 | %d 个还留在包里 | 历史上一次性用过的搬表/改措辞/打补丁脚本 | "
              u"**不要**，照着重跑会改坏现役交付件；本轮起这类脚本不随包 |" % len(leg_py),
              u"",
              u"两个数都由本块生成时现数目录得到。第二行是**政策回退计数器**：包内一次性"
              u"改稿脚本的数量应当为 0，不为 0 就说明有历史脚本被放回了包里，而不是"
              u"「目录内容变了」这么简单。",
              u"",
              u"### 内部里程碑代号对照",
              u"",
              u"| 代号 | 这一轮在做什么 | 名字出现在 |",
              u"|---|---|---|"]
    block += [u"| M6 | 硬件非对齐访存（跨字 load/store 的两相位状态机） | "
              u"`tools/ab_m6_m7.py`、`results/pnr/run2s_*` 一类臂名 |",
              u"| M7 | 与 M6 的配对对照（改前/改后同一用例的逐位比对） | 同上 |",
              u"| M8 | 可写计数器与 PMP 的 WARL 语义（`rv32mi` 那批用例） | "
              u"`tools/m9_paired_control.py` 的注释与 `docs/附件A` |",
              u"| M9 | 计数器写语义逐项探针与门禁收口轮 | "
              u"`sw/bench/micro_bisect.c`、`docs/附件A` 的 M9 改动台账 |",
              u"",
              u"代号是本包内部按里程碑顺序起的名字，对包外读者不可解 —— 所以这里给出"
              u"对照，而不是把文件名改成中文（文件名被归档与门禁按名字点名）。",
              u"<!-- END make_navigation:TOOLS-LAYERING -->"]
    return u"\n".join(block)


BEGIN = u"<!-- BEGIN make_navigation:TOOLS-LAYERING"
END = u"<!-- END make_navigation:TOOLS-LAYERING -->"


def splice_block(text, begin, end, block):
    """标记在 → 原地替换 (幂等); 标记不在 → 贴着第一个标题之后插一次。"""
    if begin in text and end in text:
        a = text.index(begin)
        b = text.index(end) + len(end)
        return text[:a] + block + text[b:]
    lines = text.splitlines(True)
    ins = 0
    for i, ln in enumerate(lines):
        if ln.startswith("#"):
            ins = i + 1
            break
    return u"".join(lines[:ins]) + u"\n" + block + u"\n" + u"".join(lines[ins:])


def splice(text, block):
    return splice_block(text, BEGIN, END, block)


# ---------------------------------------------------------------- 装配
def build_all(root=ROOT):
    """返回 {相对路径: 文本} + 计数 + 现算量登记簿 + refs 那节的发射记录。"""
    figs = []
    ri, rc = gen_results_index(root, figs)
    li, lc = gen_logs_index(root, figs)
    rr, rs, refs_emitted = gen_refs_readme(root, figs)
    tb = gen_tools_readme_head()
    tr_path = os.path.join(root, "tools", "README.md")
    tr = splice(rd(tr_path) if os.path.isfile(tr_path) else u"", tb)
    fr, fs = gen_flow_readme(root)
    out = {os.path.join("results", "INDEX.md"): ri,
           os.path.join("results", "logs", "INDEX.md"): li,
           os.path.join("refs", "README.md"): rr,
           os.path.join("tools", "README.md"): tr,
           os.path.join("flow", "README.md"): fr}
    fig_key = {os.path.join("results", "INDEX.md"): [f for f in figs
                                                     if f["label"].startswith("row:")
                                                     or f["label"].startswith("extra:")],
               os.path.join("refs", "README.md"): [f for f in figs
                                                   if f["label"].startswith("refs-top:")]}
    return out, (rc, lc, rs, fs), fig_key, refs_emitted


# ---------------------------------------------------------------- 夹具
def _fixture_refs_tree(tmp):
    """搭一间替身房: 两个真对象 + 三条解不到的片段（其中一条是 Windows 折叠陷阱）。"""
    for d in ("refs/real_dir", "flow", "docs/refs"):
        os.makedirs(os.path.join(tmp, d))
    io.open(os.path.join(tmp, "refs", "real_file.md"), "w",
            encoding="utf-8", newline="\n").write(u"x\n")
    io.open(os.path.join(tmp, "docs", "refs", "anlogic_td_probe.md"), "w",
            encoding="utf-8", newline="\n").write(u"x\n")
    io.open(os.path.join(tmp, "flow", "x.py"), "w",
            encoding="utf-8", newline="\n").write(
        u"# refs/real_file.md  refs/real_dir  refs/does_not_exist_zz.md\n"
        u"# 缩进树里的 refs/xx.md 不算; 占位 refs/... 也不许落纸\n"
        u"# 挂在 docs/ 那一节下面: refs/anlogic_td_probe.md\n"
        u"P = \"refs/../outside.md\"\n")
    io.open(os.path.join(tmp, "outside.md"), "w", encoding="utf-8", newline="\n").write(u"x\n")
    return [(u"flow/x.py", rd(os.path.join(tmp, "flow", "x.py")))]


def fixture_refs_paths_resolve():
    """D1 的正臂: 只发射解析得到的片段, 且不硬拼前缀。"""
    errs = []
    tmp = tempfile.mkdtemp(prefix="nav_refs_")
    try:
        corpus = _fixture_refs_tree(tmp)
        lines, stats, emitted = refs_section(tmp, corpus)
        printed = "\n".join(lines)
        if "refs/does_not_exist_zz.md" in printed or "xx.md" in printed:
            errs.append("nonexistent fragment got emitted")
        if "refs/..." in printed or "refs/anlogic_td_probe.md`" in printed:
            errs.append("placeholder or mis-rooted fragment got emitted")
        if "refs/../outside.md" in printed:
            errs.append("path escape got emitted")
        got = sorted(e["label"][5:] for e in emitted)
        if got != ["refs/real_dir", "refs/real_file.md"]:
            errs.append("emitted set wrong: %s" % got)
        if stats["listed"] != stats["resolved"] or stats["unmatched"] != 0:
            errs.append("listed=%d resolved=%d unmatched=%d" % (
                stats["listed"], stats["resolved"], stats["unmatched"]))
        if stats["rejected"] < 3:
            errs.append("rejected=%d, 三条坏片段没被数进来" % stats["rejected"])
        if verify_refs_lines(tmp, emitted):
            errs.append("resolver disagreed with verifier on good lines")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return errs


def fixture_refs_forced_line_refused():
    """D1 的反臂: 硬把一条盘上没有的路径发射出去, 回核必须点名拒收。"""
    errs = []
    tmp = tempfile.mkdtemp(prefix="nav_refsb_")
    try:
        _fixture_refs_tree(tmp)
        _lines, _stats, emitted = refs_section(tmp)
        forged = dict(emitted[0])
        forged["label"] = u"refs:__never_on_disk__.md"
        forged["anchor"] = u"`refs/__never_on_disk__.md`"
        if not verify_refs_lines(tmp, emitted + [forged]):
            errs.append("forced line NOT refused")
        # 折叠陷阱: `refs/...` 在 Windows 上 os.path.exists 为真 (解析成 refs 目录),
        # 但形状守卫必须判它解不到 —— 否则就是「不存在的路径被判存在」。
        if os.path.exists(os.path.join(tmp, "refs", "...")):
            if resolve_refs_fragment(tmp, "refs/...")[0] is not None:
                errs.append("dot-folded placeholder resolved as real")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return errs


def fixture_figures_recomputed():
    """D2 的正臂 + 反臂: 真数对得上; 把某一行的数改一位, 回核必须抓到。"""
    errs = []
    figs = []
    _text, counts = gen_results_index(ROOT, figs)
    problems, n = verify_figures(_text, figs)
    if problems:
        errs.append("clean figures reported %s (checked=%d)" % (problems[:2], n))
    if not figs:
        errs.append("no figures registered")
    tampered = _text.replace(u"| `sim/` |", u"| `sim_X` |", 1)
    if not verify_figures(tampered, figs)[0]:
        errs.append("missing row anchor passed")
    # 改一位体积: 现算值与印出去的值不再相等, 必须红
    idx = next(i for i, f in enumerate(figs) if f["label"] == "row:sim/")
    bad = dict(figs[idx], files=figs[idx]["files"] + 1)
    if not verify_figures(_text, figs[:idx] + [bad] + figs[idx + 1:])[0]:
        errs.append("off-by-one file count passed")
    # 守恒式必须真在文本里, 且两侧现算相等
    m = re.search(u"本目录共 (\\d+) 个文件", _text)
    if not m or int(m.group(1)) != counts["total_files"]:
        errs.append("conservation line unparseable")
    if counts["total_files"] != counts["parts_files"]:
        errs.append("conservation broken: total=%d parts=%d" % (
            counts["total_files"], counts["parts_files"]))
    return errs


def fixture_stale_verdict_detected():
    """D2 的反臂: 造一棵「证据比矩阵还新」的替身树, 判据必须报 stale;
    把那条证据挪到矩阵之前, 判据必须转不报。"""
    errs = []
    tmp = tempfile.mkdtemp(prefix="nav_stale_")
    try:
        os.makedirs(os.path.join(tmp, "results", "sim"))
        mp = os.path.join(tmp, "results", "matrix.json")
        ev = os.path.join(tmp, "results", "sim", "a.json")
        io.open(ev, "w", encoding="utf-8", newline="\n").write("{}\n")
        base = 1600000000
        os.utime(ev, (base, base))

        def put_matrix(steps, n_total, when):
            io.open(mp, "w", encoding="utf-8", newline="\n").write(
                json.dumps({"started_utc": "2020-01-01T00:00:00Z", "steps": steps,
                            "n_total_in_table": n_total, "ran_subset": False}))
            os.utime(mp, (when, when))

        # 臂 A: 终表已落盘、归档都在它之前 ⇒ 不过期
        put_matrix(3, 3, base + 100)
        st = verdict_cache_state(tmp)
        if st["stale"] or st["partial"]:
            errs.append("fresh tree judged stale/partial: %s" % st["reason"])
        # 臂 B: 链条先 emit 中间表再跑后置格 ⇒ 后置归档必然比矩阵新, 这一档不许判过期
        #        (判了就是一发永远红的门, 而链条恰恰把本件放在后置格里跑)
        put_matrix(1, 3, base + 100)
        os.utime(ev, (base + 500, base + 500))
        st_mid = verdict_cache_state(tmp)
        if st_mid["stale"] or not st_mid["partial"]:
            errs.append("mid-run matrix judged stale (永远红的门): %s" % st_mid["reason"])
        # 臂 C: 终表已落盘而仍有归档比它新 ⇒ 必须判过期, 且要点名是哪一份
        put_matrix(3, 3, base + 100)
        st = verdict_cache_state(tmp)
        if not st["stale"]:
            errs.append("stale tree judged fresh")
        if "sim/a.json" not in st["newest_path"].replace(os.sep, "/"):
            errs.append("stale verdict named the wrong file: %s" % st["newest_path"])
        # 臂 D: 矩阵不在 ⇒ fail-closed, 不许读成「不过期」
        os.remove(mp)
        if not verdict_cache_state(tmp)["stale"]:
            errs.append("missing matrix judged fresh")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return errs


def fixture_flow_block_idempotent():
    """D3 的幂等臂: 同一棵树连生两次逐字节相同, 且步数与真模块一致;
    标记块贴进已有散文只有一份。"""
    errs = []
    a, sa = gen_flow_readme(ROOT)
    b, _sb = gen_flow_readme(ROOT)
    if a != b:
        errs.append("flow README not byte-idempotent")
    if a.count(FLOW_BEGIN) != 1 or a.count(FLOW_END) != 1:
        errs.append("FLOW_STEPS block count %d/%d" % (a.count(FLOW_BEGIN), a.count(FLOW_END)))
    try:
        sys.path.insert(0, os.path.join(ROOT, "flow"))
        import run_all as ra
        n = ra.N_TOTAL
    except Exception as e:
        n = None
        errs.append("cannot import run_all: %s" % type(e).__name__)
    finally:
        try:
            os.chdir(ROOT)
        except Exception:
            pass
    if n is not None:
        if sa["steps"] != n:
            errs.append("block steps=%d but module N_TOTAL=%d" % (sa["steps"], n))
        if len(re.findall(r"^\| \d+ \|", a, re.M)) != n:
            errs.append("table rows=%d != N_TOTAL=%d" % (
                len(re.findall(r"^\| \d+ \|", a, re.M)), n))
        if ("`N_TOTAL`=%d" % n) not in a:
            errs.append("provenance line missing N_TOTAL=%d" % n)
    if sa["writes"] + sa["checks"] + sa["unknown"] != sa["steps"]:
        errs.append("cell accounting broken: %d+%d+%d != %d" % (
            sa["writes"], sa["checks"], sa["unknown"], sa["steps"]))
    seeded = splice_block(u"# H\n\ntext\n\n" + FLOW_BEGIN + u"\nOLD\n" + FLOW_END + u"\n\ntail\n",
                          FLOW_BEGIN, FLOW_END, FLOW_BEGIN + u"\nNEW\n" + FLOW_END)
    if seeded.count(FLOW_BEGIN) != 1 or u"OLD" in seeded or u"tail" not in seeded:
        errs.append("block splice not in place")
    return errs


def selftest():
    """本件自己的夹具: 证明它不空转, 也证明「自我排除」那一格真的在起作用。"""
    errs = []
    out, counts, fig_key, refs_emitted = build_all()
    out2, _c2, _f2, _r2 = build_all()
    # 夹具 1: 幂等。同一个函数连续调两次必须给出逐字节相同的文本 ——
    #   不幂等就没有任何「过期」判据可写 (--check 会永远红, 或永远绿)。
    if out != out2:
        # 第三次生成: 若它与第二次一致, 差异来自**树在动**(有链在跑), 而不是本件不确定。
        # 仍然判红, 但红因必须写成真的那一条 —— 否则下一位会去找一个不存在的 bug。
        out3, _c3, _f3, _r3 = build_all()
        diff = [k for k in out if out[k] != out2.get(k)]
        if out3 == out2:
            errs.append("TREE-CHURN (生成器可重复, 是盘上的树在动: A!=B 而 C==B) %s"
                        % diff)
        else:
            errs.append("NOT-IDEMPOTENT %s" % diff)
    # 夹具 2: 自己的产物不许进自己的分母。
    for k, text in out.items():
        if os.path.basename(k) == "INDEX.md" and "results/INDEX.md" in text.replace("\\", "/"):
            if u"未登记" in text and u"INDEX.md" in text.split(u"未登记")[0][-400:]:
                errs.append("SELF-INCLUSION 生成件把自己算成了未登记条目")
    # 夹具 3: 分母塌了要红, 不许报一个干净的 0。
    rc, lc, rs, fs = counts
    if rc["entries"] == 0:
        errs.append("DENOM results/ 一项都没登记")
    if rs["resolved"] == 0:
        errs.append("DENOM 没搜到任何能解析的 refs/ 子路径")
    # 夹具 4: splice 幂等 —— 同一块贴两次会堆出两份 TOOLS-LAYERING。
    t = out[os.path.join("tools", "README.md")]
    if t.count("BEGIN make_navigation:TOOLS-LAYERING") != 1:
        errs.append("SPLICE-DUP TOOLS-LAYERING 块出现 %d 次"
                    % t.count("BEGIN make_navigation:TOOLS-LAYERING"))
    # 夹具 5..9: 三条缺陷各自的正/反臂。
    for fx in (fixture_refs_paths_resolve, fixture_refs_forced_line_refused,
               fixture_figures_recomputed, fixture_stale_verdict_detected,
               fixture_flow_block_idempotent):
        errs += ["%s: %s" % (fx.__name__, e) for e in fx()]
    if rc["extra"]:
        errs.append("UNREGISTERED %d 项没进 RESULT_ROLES" % rc["extra"])
    n = len(FIXTURE_NAMES)
    reported = 0
    for i, e in enumerate(errs, 1):
        sys.stdout.write("FAIL fixture%d %s\n" % (i, _a(e)))
        reported += 1
    if reported != len(errs):
        sys.stdout.write("FAIL selftest-report-hides-failures\n")
    ok = n - len(errs)
    sys.stdout.write("NAVIGATION_SELFTEST OK %d/%d\n" % (max(ok, 0), n))
    return 0 if ok == n else 1


def _a(s):
    """判决行一律 ASCII: 控制台是 GBK, 中文会被替换成乱码而看不出红因。"""
    return s.encode("ascii", "backslashreplace").decode("ascii")


# ---------------------------------------------------------------- 主流程
def run_write(root=ROOT):
    out, counts, fig_key, refs_emitted = build_all(root)
    rc, lc, rs, fs = counts
    problems = []
    changed = 0
    for rel, text in sorted(out.items()):
        p = os.path.join(root, rel)
        if (rd(p) if os.path.isfile(p) else None) != text:
            w(p, text)
            changed += 1
    # 落盘之后立刻把**盘上那份**读回来对账 (D2 的自证)。
    fig_problems, n_fig = [], 0
    for rel, figs in sorted(fig_key.items()):
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            fig_problems.append("OUTPUT-MISSING %s" % rel.replace(os.sep, "/"))
            continue
        prob, k = verify_figures(rd(p), figs)
        n_fig += k
        fig_problems += ["%s %s" % (rel.replace(os.sep, "/"), x) for x in prob]
    lp = os.path.join(root, "results", "logs", "INDEX.md")
    if not os.path.isfile(lp):
        fig_problems.append("logs/INDEX.md OUTPUT-MISSING")
        n_logs = 0
    else:
        prob, n_logs = verify_logs_counts(rd(lp), root)
        fig_problems += ["logs/INDEX.md " + x for x in prob]
    # 写完再重生成一遍必须得到同一份文本, 否则说明索引对自己写出去的字节有反馈依赖。
    out2, _c2, _f2, _r2 = build_all(root)
    feedback = [rel.replace(os.sep, "/") for rel in sorted(out) if out[rel] != out2[rel]]
    refs_problems = verify_refs_lines(root, refs_emitted)
    st = verdict_cache_state(root)
    return out, counts, dict(changed=changed, n_fig=n_fig, n_logs=n_logs,
                             fig_problems=fig_problems, feedback=feedback,
                             refs_problems=refs_problems, state=st,
                             refs_emitted=refs_emitted)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="生成并落盘。**默认是只核对**: 默认调用即真判决, "
                         "把写盘做成显式动作, 这样总跑链条读到的是判决而不是副作用。")
    ap.add_argument("--check", action="store_true",
                    help="与默认行为相同, 保留给手敲的人; 两者都只核不写")
    ap.add_argument("--flow-readme", action="store_true",
                    help="只重生成 flow/README.md 的那个标记块 (链条外的手工入口)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    if args.flow_readme:
        text, fs = gen_flow_readme(ROOT)
        p = os.path.join(ROOT, "flow", "README.md")
        before = rd(p) if os.path.isfile(p) else None
        w(p, text)
        again, _ = gen_flow_readme(ROOT)
        idem = "IDEMPOTENT=yes" if again == rd(p) else "IDEMPOTENT=NO"
        print("FLOW_README wrote=%d steps=%d pre=%d post=%d writes=%d checks=%d "
              "unknown=%d module=%s sha16=%s %s" % (
                  0 if before == text else 1, fs["steps"], fs["pre"], fs["post"],
                  fs["writes"], fs["checks"], fs["unknown"], fs["module"],
                  fs["sha16"], idem))
        return 0 if idem == "IDEMPOTENT=yes" else 1

    problems = []
    if args.write:
        out, counts, checked = run_write(ROOT)
        rc, lc, rs, fs = counts
        n_reg, n_extra = rc["entries"], rc["extra"]
        n_logs, n_hits = lc["logs"], lc["hits"]
        n_used = rs["resolved"]
        n_fig, fig_problems, feedback = checked["n_fig"], checked["fig_problems"], checked["feedback"]
        problems += ["FIGURE %s" % x for x in fig_problems]
        problems += ["FEEDBACK-LOOP %s" % x for x in feedback]
        problems += ["REFS %s" % x for x in checked["refs_problems"]]
        # 链条里跑的是**写入者**而不是核对者, 这是有意的: 索引的内容按构造每趟都变
        # (results/logs 每趟多一份日志, 总体积也随之变), 所以「盘上那份 == 现在生成的」
        # 从链条内部永远不成立。承重的判据是那组分母守卫 + 现算量回核。
        print("NAVIGATION_WROTE files=%d changed=%d results_entries=%d unregistered=%d "
              "logs=%d used_refs=%d figures=%d figure_mismatch=%d absent_roles=%d "
              "refs_listed=%d refs_resolved=%d refs_unmatched=%d refs_rejected=%d "
              "flow_steps=%d flow_unknown=%d feedback=%d" % (
                  len(out), checked["changed"], n_reg, n_extra, n_logs, n_used,
                  n_fig, len(fig_problems), rc["absent_roles"],
                  rs["listed"], rs["resolved"], rs["unmatched"], rs["rejected"],
                  fs["steps"], fs["unknown"], len(feedback)))
    else:
        out, counts, fig_key, refs_emitted = build_all(ROOT)
        rc, lc, rs, fs = counts
        n_reg, n_extra = rc["entries"], rc["extra"]
        n_logs, n_hits = lc["logs"], lc["hits"]
        n_used = rs["resolved"]
        drift = [rel for rel, text in sorted(out.items())
                 if (rd(os.path.join(ROOT, rel))
                     if os.path.isfile(os.path.join(ROOT, rel)) else None) != text]
        for d in drift:
            sys.stdout.write("  - DRIFT %s\n" % _a(rel_ify(d)))
        fig_problems, n_fig = [], 0
        for rel, figs in sorted(fig_key.items()):
            prob, k = verify_figures(out[rel], figs)
            n_fig += k
            fig_problems += ["%s %s" % (rel_ify(rel), x) for x in prob]
        problems += ["FIGURE %s" % x for x in fig_problems]
        problems += ["REFS %s" % x for x in verify_refs_lines(ROOT, refs_emitted)]
        print("NAVIGATION_CHECKED files=%d drift=%d results_entries=%d "
              "unregistered=%d logs=%d used_refs=%d figures=%d figure_mismatch=%d "
              "absent_roles=%d refs_listed=%d refs_resolved=%d refs_unmatched=%d "
              "refs_rejected=%d flow_steps=%d" % (
                  len(out), len(drift), n_reg, n_extra, n_logs, n_used, n_fig,
                  len(fig_problems), rc["absent_roles"], rs["listed"], rs["resolved"],
                  rs["unmatched"], rs["rejected"], fs["steps"]))

    if n_used == 0:
        problems.append("REFS-NO-USED-PATH refs/ 子路径一条都没解析到")
    if rs["unmatched"]:
        problems.append("REFS-UNRESOLVED-EMITTED %d 行印出来却解析不到" % rs["unmatched"])
    if n_reg == 0:
        problems.append("DENOM results/ 一项都没登记 (解析面塌了)")
    if n_extra:
        problems.append("UNREGISTERED %d 项没进 RESULT_ROLES" % n_extra)
    if rc["total_files"] != rc["parts_files"]:
        problems.append("CONSERVATION total=%d parts=%d" % (
            rc["total_files"], rc["parts_files"]))
    if fs["steps"] == 0 or fs["writes"] + fs["checks"] + fs["unknown"] != fs["steps"]:
        problems.append("FLOW-TABLE flow 步表塌了: %s" % _a(json.dumps(
            {k: fs[k] for k in ("steps", "writes", "checks", "unknown", "error")},
            ensure_ascii=False)))
    st = verdict_cache_state(ROOT)
    print(_a("VERDICT_CACHE started_utc=%s matrix_mtime=%s newest_archive_mtime=%s "
             "newest=%s stale=%s reason=%s matrix_stage=%s steps=%s tail_excluded=%s") % (
              st["started_utc"], utc(st["matrix_mtime"]) if st["matrix_mtime"] else "?",
              utc(st["newest_mtime"]) if st["newest_mtime"] else "?",
              st["newest_path"] or "?", st["stale"], st["reason"],
              ("PARTIAL" if st.get("partial") else "FINAL"),
              "%s/%s" % (st.get("steps_in_matrix"), st.get("n_total_in_table")),
              st.get("tail_excluded", 0)))
    if st["partial"] and st["newer_than_verdict"] and not st["stale"]:
        sys.stdout.write(
            "MATRIX-PARTIAL matrix_steps=%s/%s newest_archive=%s @ %s "
            "later_than_matrix_mtime=yes run_in_progress=likely "
            "note=chain-emits-interim-matrix-before-post-steps-so-this-is-not-stale\n" % (
                st.get("steps_in_matrix"), st.get("n_total_in_table"),
                st["newest_path"], utc(st["newest_mtime"])))
    if st["stale"]:
        problems.append("STALE-VERDICT-CACHE %s @ %s 晚于**终表** matrix.json 的落盘"
                        "时刻 %s => 盘上这份判决属于更早的一趟, 印出的 started_utc "
                        "不是这一趟的" % (
                            st["newest_path"], utc(st["newest_mtime"]),
                            utc(st["matrix_mtime"]) if st["matrix_mtime"] else "?"))
        sys.stdout.write("STALE-VERDICT-CACHE matrix=%s started_utc=%s matrix_mtime=%s "
                         "newest_archive=%s newest_mtime=%s\n" % (
                             st["matrix"], st["started_utc"],
                             utc(st["matrix_mtime"]) if st["matrix_mtime"] else "?",
                             st["newest_path"], utc(st["newest_mtime"])))

    for p in problems:
        sys.stdout.write("  - %s\n" % _a(p))
    if problems:
        print("NAVIGATION_STALE problems=%d" % len(problems))
        return 1
    return 0


def rel_ify(p):
    return p.replace(os.sep, "/")


if __name__ == "__main__":
    sys.exit(main())
