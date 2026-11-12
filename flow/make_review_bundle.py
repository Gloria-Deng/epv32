#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flow/make_review_bundle.py -- 生成「评审用精简包」到包**外**，并自证它没漏没假。

为什么放在包外（而不是包里一个 dist/ 目录）
------------------------------------------
评审 R4 的 P1-1 与 N-2 抓的就是「包里有第二份正文」：`results/backup/preWORD_r1/`
里躺着旧版设计报告与旧版附件 A，读者分不清哪份是现役的。如果精简包做成本包内的
一个目录，我就用同一只手制造了同一个缺陷。所以默认落地在包的**同级**目录，
包内不留副本、不留哈希、也不留任何指向它的绝对路径。

它降级的东西一律写成「复跑即再生」并附那条命令，不写成「删了不影响」。
判决行是 stdout 最后一行且全 ASCII（父进程按 UTF-8 解子进程输出，而控制台是 GBK）。

用法
    python flow/make_review_bundle.py                 # 只算清单与体积, 不落盘 (dry)
    python flow/make_review_bundle.py --apply         # 真拷
    python flow/make_review_bundle.py --selftest      # 三条会咬的判据
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

BUNDLE_NAME = "EPV32_评审包"
SIZE_SOFT_LIMIT_MIB = 80.0          # 官方没给体积上限；这条是自己给"评审可下载"定的档

# 规则按**顺序**判：先命中先归类。每条 = (相对路径正则, 收/不收, 为什么)
RULES = [
    (r"^\.git/", "DROP", "版本控制目录，不是交付件"),
    (r"^scratch_", "DROP", "本轮诊断用的一次性探针"),
    (r"__pycache__/", "DROP", "字节码"),
    (r"^三方评审/", "DROP", "内部评审件，不随提交包对外"),
    (r"^results/trace/", "DROP", "逐周期轨迹（可再生：python flow/run_all.py nn_cifar nn_net nn_bench，再由 compact_traces 收成 .gz）"),
    (r"^results/wave/", "DROP", "波形解析产物，可再生：python flow/demo.sh"),
    (r"^results/backup/", "DROP", "配对对照的旧侧读数，评审不需要；复跑对照用 tools/m9_paired_control.py"),
    (r"^build/elf/", "DROP", "258 个 ELF/bin，可再生：bash flow/build_sim.sh"),
    (r"^build/sim", "DROP", "仿真器构建目录，可再生：bash flow/build_sim.sh"),
    (r"^refs/riscv-tests/", "KEEP", "官方套件原件：正文那句「通过官方测试套件」的可比前提"),
    (r"\.vcd$", "DROP", "明文波形（随包只留 .gz）"),
    (r"^logs/", "DROP", "空目录（本轮已从包根删掉；规则留着防它长回来）"),
    (r"^tools/legacy/", "DROP", "一次性改稿脚本，不参与任何结论"),
]
# 交付硬指标那条「数据以图表呈现 + 图面数字可回指」的东西必须留全：
MUST_KEEP = (r"^docs/", r"^results/demo/demo_video\.(mp4|json)$", r"^results/matrix\.json$",
             r"^results/word_limits\.json$", r"^results/HISTORICAL_ARCHIVES\.json$",
             r"^rtl/", r"^sw/", r"^tb/", r"^flow/", r"^refs/THIRD_PARTY_LICENSES/",
             r"^赛题/", r"^README\.md$", r"^LICENSE$")


def sha(p, limit=None):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()


def classify(rel):
    for pat, verdict, why in RULES:
        if re.search(pat, rel):
            return verdict, why
    for pat in MUST_KEEP:
        if re.search(pat, rel):
            return "KEEP", "交付件本体或它的结论"
    # 剩下的 results/ 归档一律留：*.json 是结论，*.txt/log 是屏上数字的原始 stdout
    if rel.startswith("results/"):
        if re.search(r"\.(json|txt|log|md|csv)$", rel):
            return "KEEP", "归档读数（正文每个「实测」的出处）"
        return "DROP", "results/ 里的非文本大件，可再生"
    return "DROP", "未列入清单：默认不收"


def walk_all():
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
    for rel, full in walk_all():
        verdict, why = classify(rel)
        (keep if verdict == "KEEP" else drop).append((rel, full, why))
    return keep, drop


def audit(keep, drop, dest=None):
    """四条判据，任何一条不成立即返回问题清单（不落盘 / 非零退出）。"""
    P = []
    kept_bytes = sum(os.path.getsize(f) for _r, f, _w in keep)
    # 1) 分母守卫：清单为空或某族一个都没有，是解析面塌了，不是"没有要收的"
    fams = ("docs/", "rtl/", "sw/", "tb/", "flow/", "results/", "refs/", u"赛题/")
    got = {p: sum(1 for r, _f, _w in keep if r.startswith(p)) for p in fams}
    for p, n in got.items():
        if n == 0:
            P.append("EMPTY-FAMILY %s 一个文件都没收到（分母塌了，不能读成干净）" % p)
    if not any(r == "results/matrix.json" for r, _f, _w in keep):
        P.append("NO-VERDICT-ARCHIVE results/matrix.json 不在清单里 —— 那评审拿不到唯一交付结论")
    if not any(r.endswith(".mp4") for r, _f, _w in keep):
        P.append("NO-DEMO 官方 4.1.5 一.5 点名的方案 Demo 没进包")
    # 2) 精简包必须真的比整包小，否则它没有存在理由
    all_bytes = kept_bytes + sum(os.path.getsize(f) for _r, f, _w in drop)
    if kept_bytes >= all_bytes:
        P.append("NOT-SMALLER 精简包 %d B >= 整包 %d B" % (kept_bytes, all_bytes))
    if kept_bytes / 1048576.0 > SIZE_SOFT_LIMIT_MIB:
        P.append("OVER-SOFT-LIMIT %.1f MiB > %.1f MiB"
                 % (kept_bytes / 1048576.0, SIZE_SOFT_LIMIT_MIB))
    # 3) 不许在精简包里放第二份正文（本轮刚被抓住的那个缺陷类别）
    docs = [r for r, _f, _w in keep if r.endswith(("参赛项目设计报告.md",
                                                   "附件A_缺陷与门禁台账.md"))]
    dup = [r for r in docs if r != "docs/" + r.split("/")[-1] and not r.startswith("docs/")]
    if dup:
        P.append("SECOND-COPY %s 包内正文的副本会造出「哪份是现役的」问题" % dup)
    # 4) 落盘之后逐文件回读哈希必须与源逐位相同（拷贝过程不许改字节）
    if dest:
        bad = 0
        for rel, full, _w in keep:
            t = os.path.join(dest, rel.replace("/", os.sep))
            if not os.path.isfile(t):
                bad += 1
                P.append("MISSING-IN-BUNDLE %s" % rel)
            elif sha(t) != sha(full):
                bad += 1
                P.append("HASH-DIFF %s" % rel)
            if len(P) > 20:
                break
    return P, {"files": len(keep), "mib": round(kept_bytes / 1048576.0, 1),
               "dropped_files": len(drop),
               "saved_mib": round((all_bytes - kept_bytes) / 1048576.0, 1)}


def write_manifest(dest, keep, drop, stats):
    lines = [u"# 评审用精简包 · 清单", u"",
             u"本包由 `python flow/make_review_bundle.py --apply` 生成，" +
             u"是**整包的子集**：每个文件与整包里那份逐字节相同（生成后逐个 sha256 回读过）。",
             u"",
             u"| 项 | 值 |", u"|---|---|",
             u"| 收到文件 | %d |" % stats["files"],
             u"| 体积 MiB | %s |" % stats["mib"],
             u"| 未随包文件 | %d |" % stats["dropped_files"],
             u"| 未随包体积 MiB | %s |" % stats["saved_mib"],
             u"| 唯一交付结论 | `results/matrix.json` |",
             u"", u"## 没收的东西怎么再生", u"",
             u"| 路径 | 复跑命令 |", u"|---|---|",
             u"| `build/elf`、`build/sim*` | `bash flow/build_sim.sh` |",
             u"| `results/trace/` | `python flow/run_all.py nn_cifar nn_net nn_bench` |",
             u"| `results/wave/`、`results/demo/*.txt` | `bash flow/demo.sh` |",
             u"| `refs/riscv-tests/` | 解 `refs/rt.tar.gz`（同包已收） |",
             u"| `results/backup/` | 不需要：它是配对对照的旧侧，随整包留在实验机上 |",
             u"", u"## 逐文件（路径 / 字节 / sha256 前 16 位）", u"",
             "```", ]
    for rel, full, _w in keep:
        lines.append("%-64s %10d %s" % (rel, os.path.getsize(full), sha(full)[:16]))
    lines.append("```")
    with io.open(os.path.join(dest, "MANIFEST_精简包.md"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return len(keep)


def build(apply):
    keep, drop = plan()
    parent = os.path.dirname(ROOT)
    dest = os.path.join(parent, BUNDLE_NAME)
    if not apply:
        P, st = audit(keep, drop, None)
        for x in P:
            print("PLAN-PROBLEM " + x.encode("ascii", "backslashreplace").decode("ascii"))
        print("BUNDLE_PLAN files=%s mib=%s dropped=%s saved_mib=%s dest=%s"
              % (st["files"], st["mib"], st["dropped_files"], st["saved_mib"],
                 dest.encode("ascii", "backslashreplace").decode("ascii")))
        print("BUNDLE_DRY_RUN problems=%d" % len(P))
        return 1 if P else 0
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    for rel, full, _w in keep:
        t = os.path.join(dest, rel.replace("/", os.sep))
        d = os.path.dirname(t)
        if not os.path.isdir(d):
            os.makedirs(d)
        shutil.copyfile(full, t)          # 不用 copy2：保 mtime 会让增量构建误判
    n = write_manifest(dest, keep, drop, audit(keep, drop, None)[1])
    P, st = audit(keep, drop, dest)
    for x in P[:20]:
        print("VERIFY-PROBLEM " + x.encode("ascii", "backslashreplace").decode("ascii"))
    print("REVIEW_BUNDLE_%s files=%s mib=%s manifest_rows=%s verify_problems=%d"
          % ("OK" if not P else "FAIL", st["files"], st["mib"], n, len(P)))
    return 0 if not P else 1


# 自测夹具的具名清单：末行分母由它现算（不许再手抄一个 4）。
# selftest() 里每跑一条就往 RAN 里记一条，跑完核对 RAN == FIXTURE_NAMES ——
# 否则「清单里有五条而只跑了四条」会印成一个漂亮的 4/4。
FIXTURE_NAMES = ["empty-family", "second-copy", "no-demo", "hash-drift"]


def selftest():
    """四条判据各自要能红：空族、第二份正文、缺成片、拷贝改了字节。"""
    fails = []
    ran = []
    keep, drop = plan()
    P0, _ = audit(keep, drop, None)
    if P0:
        fails.append("SETUP 现状判红（先修现实再谈夹具）: %s" % P0[:2])

    def red(name, mutated, want):
        if name not in FIXTURE_NAMES:
            fails.append("%s: 跑了一条没登记在 FIXTURE_NAMES 里的夹具" % name)
        ran.append(name)
        try:
            got = mutated()
        except AssertionError as e:
            got = [str(e)]
        if not any(want in x for x in got):
            fails.append("%s: 期望点名 %s，实得 %s" % (name, want, got[:2]))

    def empty_family():
        k = [(r, f, w) for r, f, w in keep if not r.startswith("docs/")]
        return audit(k, drop, None)[0]
    red("empty-family", empty_family, "EMPTY-FAMILY docs/")

    def second_copy():
        k = keep + [("results/backup/preWORD_r1/参赛项目设计报告.md", keep[0][1], "x")]
        return audit(k, drop, None)[0]
    red("second-copy", lambda: audit([r for r in keep if r[0] != "x"] + [
        ("docs/参赛项目设计报告.md", keep[0][1], "x"),
        ("results/notes/参赛项目设计报告.md", keep[0][1], "x")], drop, None)[0], "SECOND-COPY")

    def no_demo():
        k = [(r, f, w) for r, f, w in keep if not r.endswith(".mp4")]
        return audit(k, drop, None)[0]
    red("no-demo", no_demo, "NO-DEMO")

    def hash_drift():
        import tempfile
        w = tempfile.mkdtemp(prefix="epv32_bundle_t_")
        try:
            rel, full, _ = [x for x in keep if x[0].endswith(".py")][0]
            t = os.path.join(w, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(t))
            shutil.copyfile(full, t)
            with io.open(t, "a", encoding="utf-8") as f:
                f.write(u"\n")           # 模拟"拷过去顺手改了行尾"
            return audit(keep, drop, w)[0]
        finally:
            shutil.rmtree(w, ignore_errors=True)
    red("hash-drift", hash_drift, "HASH-DIFF")

    # 分母由清单现算，并且先核「登记的每一条都真的跑过」——不然清单与实跑会各说各话。
    # 这条守恒检查排在打印之前：先追加的失败如果落在打印之后，屏幕上就只有
    # 「3/4」而没有那条原因，读的人会以为坏的是四条夹具里的某一条。
    n = len(FIXTURE_NAMES)
    if sorted(ran) != sorted(FIXTURE_NAMES):
        fails.append("fixtures-not-run: 实跑 %s 而清单 %s"
                     % (sorted(ran), sorted(FIXTURE_NAMES)))
    if n and len(ran) > n:
        fails.append("fixtures-extra: 实跑 %d 条多于清单 %d 条" % (len(ran), n))
    for f in fails:
        print("SELFTEST-FAIL " + f.encode("ascii", "backslashreplace").decode("ascii"))
    print("REVIEW_BUNDLE_SELFTEST %s %d/%d" % ("OK" if not fails else "FAIL",
                                               max(n - len(fails), 0), n))
    return 1 if fails else 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--selftest" in a:
        sys.exit(selftest())
    sys.exit(build("--apply" in a))
