#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flow/make_submission_package.py -- 生成「对外提交包」到包内的 `submission/`，并自证。

与 flow/make_review_bundle.py 的关系（两条产品、两套规则，各自自证）
----------------------------------------------------------------
`make_review_bundle.py` 产的是**给人下载的评审精简包**：它按体积降档，把
`results/trace/`、`results/backup/`、`tools/`、`demo/` 这些族留在实验机上（正文点名的
那几条路径在精简包里指不到东西，是它的取舍而不是缺陷，因为它落在包外、随附一份
「怎么再生」的清单）。

本脚本产的是**提交动作的目标目录**：判据反过来了——凡是正文或索引点名的包内路径，
必须在提交包里指得到；凡不可再生的证据，必须随包。所以它的默认档是「收」，
只排除下面 EXCLUDES 里逐条带理由的那几类。两份规则表不共用一份实现是故意的：
它们要答的是两个相反的问题，合成一张表就会有一边的判据在另一边上永远红。
两者唯一的共同点是都不留中间件（`flow/check_package_hygiene.py` 那道门管整棵树，
它对 `submission/` 整层豁免，见那个文件里 NESTED_PACKAGE 的两条实测理由）。

五条会咬的判据（--selftest 逐条注入）
------------------------------------
 1 族分母：FAMILIES 里任何一族收到 0 份 = 清单塌了，不许读成干净。
 2 正文点名可解析：把链条 doc_gate 那一条尺子（`flow/run_all.py: unresolved_paths`）
   用到提交包上——正文/README/各目录索引里反引号点名的包内路径必须逐个指得到。
 3 唯一交付结论随包：`results/matrix.json` 必须在，且 `all_green` 与步数一起印。
   「还没绿」在 `--apply`/plan 里判红，在 `--selftest` 里只印成 SETUP-NOTICE ——
   因为跑自测的 pair_gates 这一格本来就排在矩阵落盘之前，硬管会死锁。
 4 逐文件回读：拷完之后每份文件与源逐字节相同（独立重算 sha256，不信任拷贝）。
 5 排除面自证：EXCLUDES 的每一条必须真的排到东西（排到 0 条 = 那条规则在空转），
   而 `三方评审/` 这一条的代价必须写在 MANIFEST 里（否则包内跑整表会在 table_shape
   那一格莫名其妙地红，没人知道为什么）。

用法
    python flow/make_submission_package.py              # 只算清单与体积（dry，不落盘）
    python flow/make_submission_package.py --apply      # 真拷进 submission/
    python flow/make_submission_package.py --selftest   # 五条注入
判决行是 stdout 最后一行且全 ASCII（父进程按 UTF-8 解，控制台是 GBK）。
"""
from __future__ import print_function
import hashlib
import io
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEST_REL = "submission"
DEST = os.path.join(ROOT, DEST_REL)

# 顺序判，先命中先归类。每条 = (相对路径正则, 为什么可以不留)
EXCLUDES = [
    (r"^\.git/", "版本控制元数据：那是实验机的历史，不是提交内容（提交包要自证，"
                 "靠的是 MANIFEST 与随包归档，不是靠 git log）"),
    (r"^" + DEST_REL + r"/", "上一次生成的提交包本体（本脚本的输出目录）"),
    (r"^scratch_", "本轮诊断用的一次性探针与它的捕获件；.gitignore 里就写着「不是交付件」"),
    (r"__pycache__/", "字节码目录（flow/check_package_hygiene.py 对整棵树判红）"),
    (r"^三方评审/", "内部评审件不对外。代价：flow/check_table_shape.py 的语料有一半住在"
                    "那儿，所以在提交包内部跑整表会在那一格判红——这一条必须写进 MANIFEST"),
    (r"^build/", "ELF 与仿真器构建目录：bash flow/build_sim.sh 可再生，"
                 "矩阵的 build_sim 那一格本来就每趟重建它"),
    (r"^logs/", "包根空目录（本轮从包根删掉；规则留着防它长回来）"),
    (u"^MANIFEST_提交包.md$", "清单自己不进包：交付根就是仓库根，而打包器把它的判决写在同一个文件名上（submission/MANIFEST_提交包.md）。收进来 = 那份拷贝会被自己写出的清单覆盖，逐字节回读当场判 BYTE-DIFF（实测）。逐文件表在仓库根那一份里，口径是 git ls-files 减去这一行本身。"),
]

# 每一族都必须至少收到一份：少一族是清单塌了，不是那一族没有东西。
FAMILIES = ("docs/", "rtl/", "sw/", "tb/", "flow/", "tools/", "demo/",
            "refs/", u"赛题/", "results/")

# 判据 2 的语料：这些文件里的反引号点名路径必须能在提交包里指得到。
CITING = ["README.md", "docs/参赛项目设计报告.md", "docs/附件A_缺陷与门禁台账.md",
          "demo/README.md", "results/README.md", "flow/README.md", "tools/README.md",
          "refs/README.md", "rtl/README.md", "sw/README.md", "tb/README.md"]

MANIFEST_NAME = "MANIFEST_提交包.md"


# 目录形状的两条排除：它们排的是目录而不是文件，所以「排到 0 份文件」不是空转，
# 而是那一带本来就没有文件。判据改成「包内不许出现这个前缀」。
DIR_SHAPED = (r"__pycache__/", r"^logs/")

# 还有两族的「排到 0 份」是合法现状，不是空转：scratch_* 按 .gitignore 跑完即删，
# 而 submission/ 是本脚本自己的输出目录（第一次打包时它本来还不存在）。判红换成
# 「正则必须还咬得住自己的具名样本」——正则坏了照样红，只是不再要求盘上有货。
OPTIONAL_IN_TREE = {
    r"^scratch_": "scratch_probe.py",
    r"^" + DEST_REL + r"/": DEST_REL + "/" + MANIFEST_NAME,
}

# 正文点名的路径在**整棵树**里就指不到的，那是文档自己的既有缺陷，不是打包丢的东西；
# 这一类只记账不判红（判据只盯「树里有、包里没了」这一种失败）。


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""):
            h.update(b)
    return h.hexdigest()


def classify(rel):
    """默认收；只有 EXCLUDES 命中才不收。返回 (收不收, 理由)。"""
    for pat, why in EXCLUDES:
        if re.search(pat, rel):
            return False, why
    return True, ""


def walk_tree():
    out = []
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d != "__pycache__"]
        for n in fn:
            full = os.path.join(dp, n)
            rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
            out.append((rel, full))
    return sorted(out)


def plan():
    keep, drop = [], []
    for rel, full in walk_tree():
        take, why = classify(rel)
        (keep if take else drop).append((rel, full, why))
    return keep, drop


def audit(keep, drop, dest=None, root=None):
    """五条判据，返回 (问题清单, 统计)。root 只为夹具而存在。"""
    root = root or ROOT
    P = []
    rule_census = {}
    declared_inert = []
    stats_hash_checked = 0
    kept_bytes = sum(os.path.getsize(f) for _r, f, _w in keep)
    all_bytes = kept_bytes + sum(os.path.getsize(f) for _r, f, _w in drop)

    # 1) 族分母
    for fam in FAMILIES:
        n = sum(1 for r, _f, _w in keep if r.startswith(fam))
        if n == 0:
            P.append("EMPTY-FAMILY %s received 0 files" % fam)

    # 2) 正文点名的路径必须在包里指得到（尺子取链条那一份，不另写解析器）
    target = dest or root
    try:
        sys.path.insert(0, os.path.join(root, "flow"))
        import run_all as R
    except Exception as exc:
        P.append("RULER-UNIMPORTABLE flow/run_all.py: %r" % (exc,))
        R = None
    n_refs = 0
    preexisting = []
    if R is not None:
        for doc in CITING:
            src = os.path.join(root, *doc.split("/"))
            if not os.path.isfile(src):
                P.append("CITING-MISSING %s 不在树上" % doc)
                continue
            text = io.open(src, encoding="utf-8").read()
            refs = R.cited_paths(text)
            n_refs += len(refs)
            in_pkg = set(R.unresolved_paths(refs, target))
            in_tree = set(R.unresolved_paths(refs, root))
            for b in sorted(in_pkg - in_tree):
                P.append("LOST-IN-PACKAGE %s -> %s" % (doc, b))
            preexisting += ["%s -> %s" % (doc, b) for b in sorted(in_pkg & in_tree)]
        if n_refs < 50:
            P.append("RULER-EMPTY parsed only %d cited paths (parsing collapsed)" % n_refs)

    # 3) 唯一交付结论随包，且它自己说是全绿
    mx = os.path.join(target, "results", "matrix.json")
    if not os.path.isfile(mx):
        P.append("NO-VERDICT-ARCHIVE results/matrix.json 不在包里")
    else:
        try:
            j = json.load(io.open(mx, encoding="utf-8"))
            if not j.get("all_green"):
                P.append("VERDICT-NOT-GREEN all_green=%r failed=%r"
                         % (j.get("all_green"), j.get("failed")))
        except Exception as exc:
            P.append("VERDICT-UNREADABLE %r" % (exc,))

    # 4) 逐文件回读。这里**不设**提前中断的上限：上限一旦按「已攒了多少条问题」算，
    #    判据 2 的点名清单会先把配额撑满，于是哈希回读只跑了前 26 个文件就停了，
    #    而屏幕上仍是一片红字看着像「全查过了」。整包 3221 份哈希两遍是秒级成本，
    #    少跑一份就是拿交付件的字节当未核。打印侧的截断交给 build() 的 P[:25]。
    if dest:
        seen = 0
        for rel, full, _w in keep:
            t = os.path.join(dest, *rel.split("/"))
            if not os.path.isfile(t):
                P.append("MISSING-IN-PACKAGE %s" % rel)
                seen += 1
            elif sha(t) != sha(full):
                P.append("BYTE-DIFF %s" % rel)
                seen += 1
            elif abs(os.path.getmtime(t) - os.path.getmtime(full)) > 1.0:
                # mtime 也是交付件的一部分：包内 tools/verify_deliverables.py 那条
                # 「生成物不比源旧」读的就是时间先后，而它只在该格被单独跑时才发声。
                P.append("MTIME-LOST %s src=%d pkg=%d"
                         % (rel, os.path.getmtime(full), os.path.getmtime(t)))
                seen += 1
        stats_hash_checked = len(keep)

    # 5) 每条排除规则必须真的排到东西（空转的规则会在某一天悄悄不排了）。
    #    目录形状的那两条反过来核：它们排的是目录，包内不许出现这个前缀，
    #    而「排到 0 份文件」在那两条上是合法现状（logs/ 是空目录、字节码已被剪枝）。
    for pat, why in EXCLUDES:
        if pat in DIR_SHAPED:
            leak = [r for r, _f, _w in keep if re.search(pat, r)]
            if leak:
                P.append("DIR-LEAK %s 出现在包里: %s" % (pat, leak[:3]))
            continue
        hits = [(r, f) for r, f, _w in drop if re.search(pat, r)]
        n = len(hits)
        if n == 0:
            if pat in OPTIONAL_IN_TREE:
                sample = OPTIONAL_IN_TREE[pat]
                if not re.search(pat, sample):
                    P.append("RULE-BROKEN %s 连自己的具名样本 %s 都排不掉（正则坏了）"
                             % (pat, sample))
                else:
                    declared_inert.append((pat, sample))
                n = 0
            else:
                P.append("RULE-INERT %s 一条都没排到（规则空转，或那一带已经没了）" % pat)
        # 顺手把这一条排到几份/几字节存进统计：清单在 rmtree 之后才写，那时候
        # 上一轮 submission/ 里的文件已经不在盘上，让清单端再去 stat 一遍就是
        # FileNotFoundError（实测崩在这里）。
        rule_census[pat] = (n, sum(os.path.getsize(f) for _r, f in hits))

    return P, {"files": len(keep), "mib": round(kept_bytes / 1048576.0, 1),
               "dropped": len(drop), "dropped_mib": round((all_bytes - kept_bytes)
                                                          / 1048576.0, 1),
               "cited_paths": n_refs, "preexisting_dangling": len(preexisting),
               "preexisting_list": preexisting, "hash_roundtrip": stats_hash_checked,
               "rule_census": rule_census, "declared_inert": declared_inert}


def write_manifest(keep, drop, stats):
    L = [u"# 对外提交包 " + DEST_REL + u"/ · 清单", u"",
         u"本包由 `python flow/make_submission_package.py --apply` 生成。"
         u"它与 `flow/make_review_bundle.py` 那份「评审精简包」是**两个产品**："
         u"这一份的默认档是收，只排下面逐条带理由的那几类，"
         u"目的是让正文点名的每一条路径在包里都指得到。"
         u"每个文件与整棵树里那一份逐字节相同（落盘后逐个 sha256 回读过）。",
         u"", u"| 项 | 值 |", u"|---|---|",
         u"| 收到文件 | %d |" % stats["files"],
         u"| 体积 MiB | %s |" % stats["mib"],
         u"| 未随包文件 | %d |" % stats["dropped"],
         u"| 未随包体积 MiB | %s |" % stats["dropped_mib"],
         u"| 正文点名并被核过的路径条数 | %d |" % stats["cited_paths"],
         u"| 唯一交付结论 | `results/matrix.json`（它的 `started_utc` 见文件本身） |",
         u"", u"## 没随包的东西与理由（逐条）", u"",
         u"| 路径 | 为什么可以不留 |", u"|---|---|"]
    if stats.get("declared_inert"):
        L.append(u"")
        L.append(u"其中 %d 条按设计在冻结态盘上排不到东西（`scratch_*` 跑完即删、`%s/` 是本脚本自己的输出目录）；"
                   u"它们的正则由下面这条自证，样本咬不住即判红：%s" % (
                           len(stats["declared_inert"]), DEST_REL,
                           u"、".join(u"`%s`←`%s`" % (p, q) for p, q in stats["declared_inert"])))
    for pat, why in EXCLUDES:
        # 数一律取自 census（落盘之前算的那一份）。在这里重新 stat `drop` 会撞上
        # rmtree 已经把上一轮 submission/ 删掉的那批路径 —— 实测崩在 getsize。
        n, b = stats["rule_census"].get(pat, (0, 0))
        L.append(u"| `%s` | 排到 %d 份 / %.2f MiB：%s |" % (pat, n, b / 1048576.0, why))
    L += [u"", u"## 提交包读法", u"",
          u"这一份按整棵树的形状排布，进去之后 `python flow/run_all.py` 仍然跑得动"
          u"（ROOT 由 `__file__` 上溯，工具链位置写在 README §三）。",
          u"",
          u"**一条已知代价，不许静默**：`flow/check_table_shape.py` 的语料有一半住在 "
          u"`三方评审/`，那一族不对外，所以在提交包内部跑整表会在 `table_shape` 那一格"
          u"判红。那是「内部评审件不对外」这个取舍的后果，不是交付缺陷；要跑整表请回"
          u"实验机整棵树，或直接引用随包的这份 `results/matrix.json`。",
          u"", u"## 逐文件（路径 / 字节 / sha256 前 16 位）", u"", "```"]
    for rel, full, _w in keep:
        L.append("%-64s %10d %s" % (rel, os.path.getsize(full), sha(full)[:16]))
    L.append("```")
    if stats["preexisting_list"]:
        L += [u"", u"## 在整棵树里也指不到的点名路径（文档自身的既有缺口，不是打包丢的）", u"",
              u"这一类共 %d 条，逐条列在这里：它们住在 %s 这些索引/正文里，"
              u"链条的引用门没有覆盖到这些文件，所以打包时才第一次被读到。"
              % (stats["preexisting_dangling"], u"、".join(CITING))]
        L += [u"- `%s`" % x for x in stats["preexisting_list"]]
    with io.open(os.path.join(DEST, MANIFEST_NAME), "w", encoding="utf-8",
                 newline="\n") as f:
        f.write("\n".join(L) + "\n")
    return len(keep)


def build(apply):
    keep, drop = plan()
    # One census BEFORE anything is deleted. audit() re-stats every path in `drop`,
    # and after rmtree(DEST) the previous package's files named there no longer exist
    # -- that is what crashed the first --apply inside os.path.getsize.
    P0, pre = audit(keep, drop, None)
    if not apply:
        for x in P0[:25]:
            print("PLAN-PROBLEM " + x.encode("ascii", "backslashreplace").decode("ascii"))
        print("SUBMISSION_PLAN files=%s mib=%s dropped=%s dropped_mib=%s cited=%s "
              "preexisting_dangling=%s"
              % (pre["files"], pre["mib"], pre["dropped"], pre["dropped_mib"],
                 pre["cited_paths"], pre["preexisting_dangling"]))
        print("SUBMISSION_DRY_RUN problems=%d" % len(P0))
        return 1 if P0 else 0
    if os.path.isdir(DEST):
        shutil.rmtree(DEST)
    for rel, full, _w in keep:
        t = os.path.join(DEST, *rel.split("/"))
        d = os.path.dirname(t)
        if not os.path.isdir(d):
            os.makedirs(d)
        shutil.copy2(full, t)   # 必须带 mtime：包里 tools/verify_deliverables.py 那条
                                # 「生成物不比源旧」比的就是时间先后，用 copyfile 全打成
                                # 「现在」会按字母序把 .md 排在 .docx 之后，那一格当场判红
    n = write_manifest(keep, drop, pre)
    # Re-plan after the copy: this run wrote MANIFEST into submission/, and the stale
    # `drop` list would point at files that no longer exist.
    keep2, drop2 = plan()
    P, st = audit(keep2, drop2, DEST)
    if [x[0] for x in keep2] != [x[0] for x in keep]:
        P.append("COUNT-DRIFT re-planned=%d pre-copy=%d" % (len(keep2), len(keep)))
    for x in P[:25]:
        print("VERIFY-PROBLEM " + x.encode("ascii", "backslashreplace").decode("ascii"))
    print("SUBMISSION_PACKAGE_%s files=%s mib=%s manifest_rows=%s hash_roundtrip=%s "
          "count_pre=%s count_post=%s drift=%d verify_problems=%d"
          % ("OK" if not P else "FAIL", st["files"], st["mib"], n,
             st["hash_roundtrip"], pre["files"], st["files"],
             sum(1 for x in P if x.startswith("COUNT-DRIFT")), len(P)))
    return 0 if not P else 1


# ---- 夹具：具名清单，末行分母由它现算（别处不许抄这个数）
FIXTURE_NAMES = ["family-denominator", "dangling-citation", "no-verdict",
                 "byte-drift", "mtime-lost", "rule-inert"]


def selftest():
    fails = []
    ran = []
    keep, drop = plan()
    P0, _st0 = audit(keep, drop, None)
    # 「盘上那份判决还没绿」在这里必须放成一条 NOTICE 而不是判红：链条的 pair_gates
    # 这一格本来就排在 results/matrix.json 落盘之前，拿上一趟的判决来挡这一趟的自测
    # 是死锁。绿不绿由 --apply / plan 硬管（打包当时必须绿），自测只核其余前置。
    for x in [y for y in P0 if y.startswith("VERDICT-NOT-GREEN")]:
        print("SETUP-NOTICE " + x.encode("ascii", "backslashreplace").decode("ascii"))
    P0 = [y for y in P0 if not y.startswith("VERDICT-NOT-GREEN")]
    if P0:
        fails.append("SETUP 现状判红（先修现实再谈夹具）: 共 %d 条 -> %s"
                     % (len(P0), " | ".join(P0)))

    def red(name, mutated, want):
        ran.append(name)
        try:
            got = mutated()
        except AssertionError as e:
            got = [str(e)]
        if not any(want in x for x in got):
            fails.append("%s: 期望点名 %s，实得 %s" % (name, want, [g[:90] for g in got][:2]))

    # 1) 少一族：把 tools/ 那一族从清单里剔掉，必须报 EMPTY-FAMILY
    def no_family():
        k = [(r, f, w) for r, f, w in keep if not r.startswith("tools/")]
        return audit(k, drop, None)[0]
    red("family-denominator", no_family, "EMPTY-FAMILY tools/")

    # 2) 正文点名的一条路径在包里指不到：删掉包里那一份，必须报 DANGLING
    def dangling():
        box = os.path.join(ROOT, "results", "_selftest_stub_NOT_A_VERDICT")
        try:
            if os.path.isdir(box):
                shutil.rmtree(box)
            os.makedirs(box)
            for doc in ("README.md",):
                shutil.copyfile(os.path.join(ROOT, *doc.split("/")),
                                os.path.join(box, doc))
            return audit(keep, drop, box)[0]
        finally:
            shutil.rmtree(box, ignore_errors=True)
    red("dangling-citation", dangling, "LOST-IN-PACKAGE")

    # 3) 结论归档不在：必须报 NO-VERDICT-ARCHIVE
    def no_verdict():
        box = os.path.join(ROOT, "results", "_selftest_stub_NOT_A_VERDICT")
        try:
            os.makedirs(os.path.join(box, "docs"))
            shutil.copyfile(os.path.join(ROOT, "README.md"), os.path.join(box, "README.md"))
            return audit(keep, drop, box)[0]
        finally:
            shutil.rmtree(box, ignore_errors=True)
    red("no-verdict", no_verdict, "NO-VERDICT-ARCHIVE")

    # 4) 拷贝改了字节：造一份多一个字节的目标，必须报 BYTE-DIFF
    def drift():
        box = os.path.join(ROOT, "results", "_selftest_stub_NOT_A_VERDICT")
        try:
            rel = [r for r, _f, _w in keep if r.endswith("docs/" + u"参赛项目设计报告.md")][0]
            t = os.path.join(box, *rel.split("/"))
            os.makedirs(os.path.dirname(t))
            shutil.copyfile(os.path.join(ROOT, *rel.split("/")), t)
            with io.open(t, "a", encoding="utf-8") as f:
                f.write("\n")
            return audit(keep, drop, box)[0]
        finally:
            shutil.rmtree(box, ignore_errors=True)
    red("byte-drift", drift, "BYTE-DIFF")

    # 5) 一条排不到任何东西的规则必须被点名（空转的规则是以后所有洞的入口）
    def inert():
        saved = EXCLUDES[:]
        EXCLUDES.append((r"^no_such_dir_zzz/", "夹具：这条永远排不到东西"))
        try:
            return audit(keep, drop, None)[0]
        finally:
            del EXCLUDES[len(saved):]
    red("rule-inert", inert, "RULE-INERT")

    # 6) mtime 必须真的带过来。夹具自己就是 A/B：copy2 拷完再把时间戳打歪，
    #    判据必须点名 MTIME-LOST —— 那正是谁把 copy2 换回 copyfile 的失败形状。
    def mtime_lost():
        box = os.path.join(ROOT, "results", "_selftest_stub_NOT_A_VERDICT")
        try:
            pair = [(r, f, w) for r, f, w in keep if r == "README.md"][:1]
            rel, full, _ = pair[0]
            t = os.path.join(box, *rel.split("/"))
            os.makedirs(os.path.dirname(t))
            shutil.copy2(full, t)
            os.utime(t, (1600000000, 1600000000))
            return [x for x in audit(pair, [], box)[0] if "MTIME-LOST" in x]
        finally:
            shutil.rmtree(box, ignore_errors=True)
    red("mtime-lost", mtime_lost, "MTIME-LOST")

    n = len(FIXTURE_NAMES)
    if sorted(ran) != sorted(FIXTURE_NAMES):
        fails.append("fixtures-not-run: 实跑 %s 而清单 %s" % (sorted(ran), sorted(FIXTURE_NAMES)))
    for f in fails:
        print("SELFTEST-FAIL " + f.encode("ascii", "backslashreplace").decode("ascii"))
    print("SUBMISSION_SELFTEST %s %d/%d" % ("OK" if not fails else "FAIL",
                                            max(n - len(fails), 0), n))
    return 1 if fails else 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--selftest" in a:
        sys.exit(selftest())
    sys.exit(build("--apply" in a))
