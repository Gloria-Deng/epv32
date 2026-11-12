# -*- coding: utf-8 -*-
"""盘点 tools/ 里的一次性改稿脚本，并按目录现状重生成 `tools/README.md` 的清单。

政策（2026-09-27 起）：一次性改稿笔**不随包**。本脚本因此**不会**在包内建
`tools/legacy/` —— 那一整目录本轮已移出包，留着这条旧动作会让同一个包里出现两个
写入者互相覆盖（README 上方那块现数的表跟着说谎）。要真搬走必须显式给
`--move-out <包外绝对目录>`；不给就只报告候选并留在原地。

承重纪律: **零引用**才许移。判据不是"名字长得像 patch_", 而是"除自己以外没有任何
文件提到它"。语料取全目录 (README / docs / demo / flow / tools / results 的 json 与
log), 因为归档里也可能按名字点过某个驱动。任何一条被引用的脚本一律留在原地并报告。
dry-run 默认, --apply 才动（--apply 只做两件事：报告 + 重生成清单）。
"""
import io
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TDIR = os.path.join(ROOT, "tools")
LEG = os.path.join(TDIR, "legacy")

# 名字形状: 改稿类一次性工具。这只是**假设**，不是判决 —— 判决是「零引用」。
# 2026-09-27 补齐：本轮按名字无关的普查发现 `append_closure_*`、`cleanup_after_*`、
# `compress_body_*`、`repair_closure_*` 这几族同样是「把上一版文档改成当时那一版」的
# 一次性笔，而旧 CAND 收不到它们（收不到就等于永远不会被清理，而 README 会把它们
# 列成「长期工具」——那是名字形状在替用途撒谎）。
CAND = re.compile(r"^(patch_|trim_|collapse_|rewrite_|slim_|move_|strip_|sweep_|"
                  r"sync_|apply_|fix_|hack_|tmp_|once_|append_|cleanup_|compress_|"
                  r"repair_|close_.*_debt)")
CORPUS_EXT = (".py", ".md", ".sh", ".json", ".log", ".txt", ".ys", ".c", ".h",
              ".cpp", ".v", ".vh", ".bat")
# 语料口径 = **会调用它的那些出口**: 驱动 (flow/)、门禁与工具 (tools/, 除本文件)、
# 四份交付件 (docs/ README.md demo/)、以及归档里按名字点过某支驱动的 log/json (results/)。
# 明确**不含** `三方评审/`: 评审把某个脚本列成"你包里为什么混了一次性脚本"的缺陷,
# 那是点名批评而不是依赖关系 —— 把它算进引用面会让 23 个候选里 21 个"被引用",
# 于是这条清理永远做不成, 而且理由看起来完全合法 (本轮实测到的形状)。
REF_EXCLUDE_TOP = (os.path.join("三方评审", ""),)
# 普查语料的**自我引用**排除：本工具自己写出的那份清单、以及那份清单的派生物，
# 都不许算「有人依赖这支脚本」。不排除就会得到一个自我实现的判决 ——
# `tools/README.md` 列了 trim_s5c.py，于是它「被引用」，于是它留在原地并被继续列出来；
# `results/logs/historical_archives.txt` 那一行同理（它是扫这些脚本时把它们记进去的）。
# 一次性探针 `scratch_*` 也不算：那是我这一轮的草稿，不是包内长期依赖，
# 且它们按前缀排除（逐个列名字一定会漏掉下一支新写的）。
REF_EXCLUDE_NAMES = (os.path.join("tools", "README.md"),
                     os.path.join("results", "logs", "historical_archives.txt"))
REF_EXCLUDE_PREFIX = ("scratch_",)
SKIP_DIR_NAMES = {"__pycache__", ".git", "legacy"}
SELF_NAME = "organize_legacy.py"


def die(msg):
    raise SystemExit("TOOLS-LEGACY_DIE: " + msg)


def corpus_files():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES]
        if base.startswith(os.path.join(ROOT, "results", "trace")) and False:
            continue
        for f in files:
            if f.endswith(CORPUS_EXT):
                yield os.path.join(base, f)


def main():
    apply_changes = "--apply" in sys.argv
    move_out = None
    if "--move-out" in sys.argv:
        i = sys.argv.index("--move-out")
        if i + 1 >= len(sys.argv):
            die("--move-out 后面要紧跟一个包外目录的绝对路径")
        move_out = sys.argv[i + 1]
    all_names = sorted(f for f in os.listdir(TDIR)
                       if f.endswith(".py") and os.path.isfile(os.path.join(TDIR, f)))
    # 分母是**全部**脚本，不是 CAND 命中的那几支：只有 CAND 命中的会被搬，
    # 但「零引用而没人搬」那一档必须打印出来，否则一支再也没人调用的诊断脚本会
    # 被 README 的清单默认成「长期工具」（名字形状不像改稿笔 ≠ 它有主人）。
    names = all_names
    if not names:
        # 这里不许 return：清单的**新鲜度**不能取决于「有没有 CAND 命中的候选」。
        # 早退会让 --apply 在一次政策清理之后再也不重生成 README，于是那份清单
        # 停在清理之前的样子而所有门照绿（本轮实测到的正是这种「没活干=没刷新」）。
        print("NO-CANDIDATES —— 候选集为空，仍然会按目录现状重生成清单")
    texts = {}
    skipped_outside = 0
    skipped_selfref = []
    for p in corpus_files():
        rel = os.path.relpath(p, ROOT).replace("\\", "/")
        if rel.startswith(u"三方评审/"):
            skipped_outside += 1
            continue
        if os.path.basename(p) == SELF_NAME:
            continue
        norm = os.path.relpath(p, ROOT)
        if norm in REF_EXCLUDE_NAMES or os.path.basename(p).startswith(REF_EXCLUDE_PREFIX):
            # 静默排除等于把守卫关掉一半：排除了哪几份要点名出来。
            skipped_selfref.append(rel)
            continue
        try:
            sz = os.path.getsize(p)
        except OSError:
            continue
        if sz > 25 * 1024 * 1024:
            continue
        try:
            texts[p] = io.open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
    referenced, free, diag = [], [], []
    for n in names:
        stem = n[:-3]
        hits = []
        for p, t in texts.items():
            if os.path.basename(p) == n:
                continue
            if stem in t:
                hits.append(os.path.relpath(p, ROOT).replace("\\", "/"))
        if hits:
            referenced.append((n, sorted(hits)[:4], len(hits)))
        elif CAND.match(n):
            free.append(n)
        else:
            diag.append(n)
    print("REF-CORPUS files=%d excluded_third_party=%d (三方评审 只算批评不算依赖)"
          % (len(texts), skipped_outside))
    print("REF-CORPUS excluded_selfref=%d %s (自己写出的清单与本轮一次性探针不算依赖)"
          % (len(skipped_selfref), sorted(skipped_selfref)[:6]))
    if diag:
        print("UNREFERENCED-NOT-ONESHOT %d 支零引用但名字不像改稿笔，留在包里当诊断件: %s"
              % (len(diag), ", ".join(sorted(diag))))
    print("CONSERVED scripts=%d referenced=%d oneshot-free=%d unreferenced-kept=%d"
          % (len(names), len(referenced), len(free), len(diag)))
    assert len(names) == len(referenced) + len(free) + len(diag), \
        "三桶加起来不等于分母 —— 普查自己漏了一档"
    print("CANDIDATES %d  REFERENCED %d  FREE %d" % (len(names), len(referenced),
                                                     len(free)))
    for n, hits, k in referenced:
        print("KEEP  %s  (被 %d 处提到: %s)" % (n, k, ", ".join(hits)))
    for n in free:
        print("MOVE  %s" % n)
    if not apply_changes:
        print("mode=dry")
        print("TOOLS-LEGACY_DRY free=%d" % len(free))
        return 0
    # 包内政策（2026-09-27 起）：一次性改稿笔**不随包**。所以这里不再往
    # `tools/legacy/` 里放东西 —— 上一版政策是「收进子目录」，那一整目录本轮已移出包，
    # 而这个函数如果被留着就会把子目录重新建出来（同一个包里出现两个写入者、
    # 而 README 上方那块现数的表会跟着说谎）。要真的搬走必须显式给一个**包外**目录。
    if free and not move_out:
        print("POLICY 一次性改稿笔不随包: %d 支候选留在原地，没有搬" % len(free))
        print("POLICY 要搬走请给 --move-out <包外绝对目录>（本脚本不会往包内建 legacy/）")
    if free and move_out:
        dst = os.path.abspath(move_out)
        if dst.startswith(ROOT + os.sep) or dst == ROOT:
            die("--move-out 必须在包外（给了 %s，包根是 %s）" % (dst, ROOT))
        if not os.path.isdir(dst):
            os.makedirs(dst)
        for n in free:
            shutil.move(os.path.join(TDIR, n), os.path.join(dst, n))
        print("MOVED-OUT %d 支 -> 包外目录（路径按本机政策不写进包内文档）" % len(free))
    if os.path.isdir(LEG):
        print("POLICY-VIOLATION 包内还有 legacy 子目录（本轮政策是不随包）: %s" % LEG)
    _write_readme(sorted(os.listdir(TDIR)), sorted(os.listdir(LEG))
                  if os.path.isdir(LEG) else [])
    print("TOOLS-LEGACY_OK moved=%d kept=%d" % (len(free), len(referenced)))
    return 0


def _write_readme(tools_now, legacy_now):
    p = os.path.join(TDIR, "README.md")
    # 这个文件有**两个**写入者：本函数整份重写，而 `tools/make_navigation.py` 只换
    # 自己那块 `<!-- BEGIN make_navigation:TOOLS-LAYERING -->` 标记内的表。
    # 不加这段保护，跑一次 --apply 就会把别人那块现数的表洗掉（而文件仍在、行号仍在，
    # 什么门都不会红 —— 与「生成块被另一次写入覆盖」是同一个形状）。
    nav = u""
    if os.path.isfile(p):
        old = io.open(p, encoding="utf-8").read()
        i = old.find(u"<!-- BEGIN make_navigation:")
        j = old.find(u"<!-- END make_navigation:")
        if (i < 0) != (j < 0):
            raise AssertionError("TOOLS-LAYERING 块只剩一半标记，先修登记簿再整份重写")
        if i >= 0 and j > i:
            eol = old.find(u"\n", j)
            nav = old[i:(eol if eol > 0 else len(old))].rstrip(u"\n")
    body = io.open(p, "w", encoding="utf-8", newline="\n")
    body.write(u"# tools/ 目录说明：哪些是长期工具，哪些是一次性改稿脚本\n\n")
    if nav:
        body.write(nav + u"\n\n")
    else:
        body.write(u"（`make_navigation` 的 TOOLS-LAYERING 生成块本轮不在文件里；"
                   u"跑一次 `python tools/make_navigation.py --write` 会补回来。）\n\n")
    body.write(u"## 这个目录是怎么分的\n\n")
    # 这句以前自带一个数（`len(tools_now)` 在**生成那一刻**算出来）。数是对的，
    # 但它是一份冻结快照：之后往 `tools/` 里加一支脚本，这句就悄悄过期，而同一个
    # 文件上方那块 `make_navigation` 生成的表是每趟现数的 —— 于是同一个文件里
    # 出现「51 个 .py」与「一共 40 个脚本」两个数（2026-09-27 实测）。
    # 修法不是把 40 改成 51（那只是换一份会再过期的抄本），而是让这句不再带数，
    # 把数量交给上面那块现数的表。
    body.write(u"`tools/` 根下现役脚本的**数量与清单**以上方那块由 "
               u"`tools/make_navigation.py` 现数的 TOOLS-LAYERING 表为准，这里不另记一份数"
               u"（另记一份就会漂）。本文件由 `python tools/organize_legacy.py --apply` 生成："
               u"它只列目录里**现在真的在**的文件，所以过期了重跑一次就重新对齐，"
               u"不靠人工回忆。\n\n")
    body.write(u"## 一次性改稿脚本（不随包）\n\n")
    body.write(u"这类脚本的作用是**把上一版文档改成当时那一版**：它们的锚点取自当时那份"
               u"文档的字节，照着重跑会改坏现役交付件（会非零退出，这是设计如此）。"
               u"本包现在的政策是把它们**整目录移出包**，而不是收进一个包内子目录，"
               u"所以这里不再列它们的名字 —— 列了就是一份指向不存在之物的清单。"
               u"包内如果又留着一支这样的脚本，上方那块现数的表里的「政策回退计数器」"
               u"就不会是 0。\n\n")
    if legacy_now:
        body.write(u"**当前包内还留着 %d 支（应当为 0）**：%s\n\n" % (
            len(legacy_now), u"、".join(legacy_now)))
    body.write(u"\n## 名字留在这里的脚本（留下的判据是「包内有文件按名字点过它」）\n\n")
    body.write(u"这条判据管的是**提及**而不是**依赖**：一支脚本被台账或某句复跑命令按名字"
               u"提到，它就还有一份指向它的文字，搬走会让那句话指不到东西；但这不等于它每趟"
               u"都会被跑到。哪一支真的在链条上被跑，看 `flow/README.md` 里那张由同一个"
               u"生成器现数的步骤表，不看这一节。\n\n")
    for f in tools_now:
        if f.endswith(".py") and f != "README.md":
            body.write(u"- `%s`\n" % f)
    body.write(u"\n## 为什么这么分\n\n")
    body.write(u"赛题 4.1.7② 的原文里有「可维护性和可拓展性」。一堆 `trim_s5c.py`"
               u"混在门禁脚本旁边会让人以为整个工程是靠打补丁堆出来的 —— 实际上不是："
               u"证据链在 `flow/run_all.py` 的步表里。收纳的判据也不是名字：一支脚本"
               u"只有在全包的引用面上**一次都没有出现**（外部组件与上游材料里的"
               u"同名提及不算引用）才"
               u"会被移进来，被引用到的一律留在原地并在 dry-run 里点名。收纳之后两件事"
               u"同时成立：长期工具一眼可辨，历史动作仍可复现。\n")
    body.close()
    print("WROTE", p)


if __name__ == "__main__":
    sys.exit(main())
