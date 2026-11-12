#!/usr/bin/env python3
"""包卫生门：交付包里不许留着编译中间件、字节码目录和临时件。

判据按"能不能再生"划：.o / .pyc / tmp_* 全部可再生，删了不丢证据；例外一律
显式列举并按真实路径比对，不许按目录名猜。

目录名也在判据之内（`__pycache__` / `.pytest_cache` / `.mypy_cache`）：旧实现只按
文件后缀判，一个空的 `tb/golden/__pycache__/` 会一路绿过。修法是把判据收成与声明
一致，并配一条"只有空目录"的夹具。
"""
import io
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# refs/ 是第三方测试套件原件，里面的 .o 属于上游仓库结构，不归本包管。
EXCLUDE_DIRS = (os.path.join("refs", ""), os.path.join(".git", ""),
                os.path.join("results", "pnr") + os.sep)

# 嵌套提交包：`submission/` 是**另一个包**（`flow/make_submission_package.py` 的输出），
# 自带一份 flow/ 与这道门的同名副本，其账由在**它里面**跑这道门来付，不在父树里
# 再扫第二遍。两条理由，缺一不可：1) 本门对工具状态目录串按 realpath 自证豁免（见
# R_SELF），只认原地那份文件，而 `submission/flow/check_package_hygiene.py` 那份逐字节
# 相同的副本会被 R-b 判成硬命中，红在一条纯定义串上；2) 不收这一层，`scanned` 与
# `rb_scanned` 两个分母会从「本包文件数」膨胀成「两棵树之和」。
NESTED_PACKAGE = "submission"


def in_nested_package(rel_dir):
    """rel_dir 是嵌套提交包本身，或它的任意一层子目录。"""
    return rel_dir == NESTED_PACKAGE or rel_dir.startswith(NESTED_PACKAGE + "/")


BAD_SUFFIX = (".o", ".pyc", ".obj", ".a")
BAD_NAME_PREFIX = ("tmp_",)
# 字节码/缓存**目录**按目录名判，与里面有没有文件无关。
BAD_DIR_NAMES = ("__pycache__", ".pytest_cache", ".mypy_cache")


def census(root):
    """返回 (命中列表, 扫过的文件数)。命中 = 中间件/临时件的路径。"""
    hits, scanned = [], 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        if rel_dir == ".":
            rel_dir = ""
        if any(rel_dir.startswith(e.replace("\\", "/")) for e in EXCLUDE_DIRS) \
                or in_nested_package(rel_dir):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames
                       if not (os.path.join(rel_dir, d).replace("\\", "/")
                               .startswith(tuple(e.replace("\\", "/")
                                                 for e in EXCLUDE_DIRS)))]
        # 缓存目录整体算一条命中（路径以 / 结尾），并**剪掉**对它内部的遍历：
        # 里面的 .pyc 由目录这一条覆盖，不再重复计，也不进分母。
        keep = []
        for d in dirnames:
            rel_d = (rel_dir + "/" + d) if rel_dir else d
            if d in BAD_DIR_NAMES:
                hits.append(rel_d + "/")
                continue
            keep.append(d)
        dirnames[:] = keep
        for fn in filenames:
            rel = (rel_dir + "/" + fn) if rel_dir else fn
            scanned += 1
            if fn.endswith(BAD_SUFFIX):
                hits.append(rel)
            elif fn.startswith(BAD_NAME_PREFIX) and fn.endswith((".json", ".log")):
                hits.append(rel)
    return sorted(hits), scanned


BIG_BYTES = 10 * 1024 * 1024
# 豁免集必须写理由，而不是写文件名：
#   .gz/.zip/.xz 已经是压缩形态（评审 P2-1 要的是"别随包表明文大文件"）；
#   .mp4 是 Demo 成片本体，官方硬指标是 ≤100 MB，用它自己的门管（flow/demo.sh）。
COMPRESSED_SUFFIX = (".gz", ".zip", ".xz", ".bz2")
VIDEO_SUFFIX = (".mp4",)


def big_files(root):
    """返回 (未压缩的大文件列表, 已压缩但同样大的列表, 成片)。"""
    big, packed, movie = [], [], []
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace("\\", "/")
        if rel_dir == ".":
            rel_dir = ""
        if rel_dir.startswith("refs/") or rel_dir.startswith(".git") \
                or in_nested_package(rel_dir):
            dirnames[:] = []
            continue
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            try:
                sz = os.path.getsize(p)
            except OSError:
                continue
            if sz <= BIG_BYTES:
                continue
            rel = (rel_dir + "/" + fn) if rel_dir else fn
            if fn.endswith(VIDEO_SUFFIX):
                movie.append((rel, sz))
            elif fn.endswith(COMPRESSED_SUFFIX):
                packed.append((rel, sz))
            else:
                big.append((rel, sz))
    return (sorted(big), sorted(packed), sorted(movie))


# ================================================================ 三条 R 判据
# 为什么这三条住在这里而不是"交付前记得检查一遍"：本文件上面那两条（中间件与尺寸）
# 就是因为只写在 README 里没进判据，才出现"文档说清了而盘上没清"。这一轮补的三条同形：
#   R-a 证据归档里躺着交付正文的第二份草稿副本；
#   R-b 包内文件点名内部评审目录 / 工具状态目录 / 主机绝对路径；
#   R-c 生成的索引指向盘上不存在的路径。
# 三条都**不**并进 census()：census 的返回值会被 clean() 拿去 os.remove/rmtree，
# 而这三条抓到的东西一律不可再生（草稿副本是历史证据、索引是生成件），
# 让清理端认识它们等于下一次 --clean 直接抹掉证据。
R_DOC_SUFFIX = (".md", ".docx", ".pptx")
R_BACKUP_REL = "results/backup"

# R-b 的语料：只扫文本形状，二进制（.elf/.bit/.vcd/.mp4/.gz）交给尺寸与别的门管。
R_TEXT_SUFFIX = (".md", ".py", ".sh", ".json", ".txt", ".log")
R_MAX_BYTES = 8 << 20
# 评审工作区与一次性探针住在**交付包之外**：前者整个目录的名字就是被找的那个字符串，
# 后者在 .gitignore 里被明写成「不是交付件，也不参与任何结论」。把它们算进包内等于
# 让判据在定义上不可能通过 —— 那是判据写错作用域，不是包不干净。
# NESTED_PACKAGE 同一把尺子的第三种形状：那份树不是本包的账，见它定义处的两条实测理由。
R_OUTSIDE_PACKAGE = (".git", "refs", u"三方评审", NESTED_PACKAGE)
R_SCRATCH_PREFIX = "scratch_"
# 本文件自己必须点名这些串才找得到它们 —— 规格里写明的唯一自我豁免。
R_SELF = os.path.realpath(os.path.abspath(__file__))

# 「工具状态目录」这一枚在全包（除包外两区）都不该出现，所以它走包级硬判据；
# 实测当前包内除评审工作区与 scratch 外为零，故可以按最严的一档定。
LEAK_TOKEN_TOOLDIR = u".qoder-cn"
LEAK_TOKENS_EVIDENCE = (u"三方评审", u"收口表")
# 主机绝对路径：C:\Users\<name>，name 取到下一个反斜杠为止（只用于**检出**，
# 报告里不抄匹配文本，所以贪婪与否不影响判据）。反斜杠一律由 chr(92) 现拼：
# 写成正则字面量时「Python 字符串转义」与「正则转义」两层会互相盖掉，
# 实测同一行里 \\\\ 与 \\ 都能编过，但只有一个真匹配单根反斜杠。
_B = chr(92)
LEAK_HOST_RE = re.compile("C:" + _B * 2 + "Users" + _B * 2 +
                          "[^" + _B * 2 + "]+", re.IGNORECASE)


def _rel(root, path):
    return os.path.relpath(path, root).replace("\\", "/")


def doc_copies_in_backup(root):
    """R-a：results/backup/ 下不许住任何 .md/.docx/.pptx。

    归档目录是**读侧输入**（配对对照的旧代基线），不是稿件柜：躺在证据归档里的交付
    正文副本，读者无法判断它是「当前结论」还是「上一版草稿」，还带着已过期的数字。
    """
    hits = []
    bdir = os.path.join(root, *R_BACKUP_REL.split("/"))
    if not os.path.isdir(bdir):
        return hits
    for dirpath, _dn, filenames in os.walk(bdir):
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in R_DOC_SUFFIX:
                hits.append(_rel(root, os.path.join(dirpath, fn)))
    return sorted(hits)


def _is_evidence_path(rel):
    """证据层 = 归档目录整体 + results/ 下的机器产物文件（手写自解释件不算）。"""
    if rel.startswith(R_BACKUP_REL + "/") or rel.startswith("results/logs/"):
        return True
    if not rel.startswith("results/"):
        return False
    return os.path.splitext(rel)[1].lower() in (".json", ".log", ".txt")


def text_corpus(root):
    """R-b 的语料枚举：返回 (文本文件相对路径列表, 跳过的超大件数)。"""
    files, skipped = [], 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = _rel(root, dirpath)
        if rel_dir == ".":
            rel_dir = ""
        top = rel_dir.split("/")[0]
        if top in R_OUTSIDE_PACKAGE:
            dirnames[:] = []
            continue
        keep = []
        for d in dirnames:
            if d in BAD_DIR_NAMES:
                continue
            keep.append(d)
        dirnames[:] = keep
        for fn in filenames:
            if not fn.endswith(R_TEXT_SUFFIX):
                continue
            rel = (rel_dir + "/" + fn) if rel_dir else fn
            if rel_dir == "" and fn.startswith(R_SCRATCH_PREFIX):
                continue
            p = os.path.join(dirpath, fn)
            if os.path.realpath(p) == R_SELF:
                continue
            try:
                if os.path.getsize(p) > R_MAX_BYTES:
                    skipped += 1
                    continue
            except OSError:
                continue
            files.append(rel)
    return sorted(files), skipped


def leak_tokens(root):
    """R-b：返回 (硬判据命中, 提示级命中, 扫过件数, 跳过超大件数)。

    分两档是因为评审目录名/「收口表」/主机绝对路径在**工具源与 README**里是承重结构
    （如 flow/check_table_shape.py 的语料就是那几份收口表）。一律判红等于让这道门在
    定义上不可能通过。所以：机器产出的证据层判红，人手写的源与文档层只报不判红。
    """
    files, skipped = text_corpus(root)
    hard, note = [], []
    for rel in files:
        p = os.path.join(root, *rel.split("/"))
        with io.open(p, "rb") as fh:
            text = fh.read().decode("utf-8", errors="replace")
        # 工具状态目录：包内任何地方都不该出现 —— 它不是「源层可以合法点名的东西」,
        # 所以它**不按位置分档**, 一律硬判据。
        if LEAK_TOKEN_TOOLDIR in text:
            hard.append("%s tool-dir" % rel)
        found = []
        for tok in LEAK_TOKENS_EVIDENCE:
            if tok in text:
                found.append("review-token")
        if LEAK_HOST_RE.search(text):
            found.append("host-path")
        if not found:
            continue
        item = "%s %s" % (rel, ",".join(sorted(set(found))))
        (hard if _is_evidence_path(rel) else note).append(item)
    return sorted(hard), sorted(note), len(files), skipped


REFS_INDEX_REL = "refs/README.md"
REFS_PATH_RE = re.compile(r"refs/[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]*)?")
REFS_TRAIL = "）)、。，;:,./*"


def refs_index_dangling(root):
    """R-c：refs/README.md 点名的每一条 refs/... 路径必须在盘上存在。

    分母守卫排在判决之前：一条 `refs/` 都没解出来时判的是「解析面塌了」而不是
    「索引很干净」—— 空语料与零缺口在这份输出里必须长得不一样。
    """
    p = os.path.join(root, *REFS_INDEX_REL.split("/"))
    if not os.path.isfile(p):
        return [u"%s 找不到，索引无法核对" % REFS_INDEX_REL], 0
    with io.open(p, "rb") as fh:
        text = fh.read().decode("utf-8", errors="replace")
    cands = set()
    for m in REFS_PATH_RE.finditer(text):
        c = m.group(0).rstrip(REFS_TRAIL)
        if c == "refs":
            continue
        cands.add(c)
    if not cands:
        return [u"%s 里一条 refs/ 路径都没解析出来（解析面塌了，不是干净）"
                % REFS_INDEX_REL], 0
    missing = []
    for c in sorted(cands):
        if not os.path.exists(os.path.join(root, *c.split("/"))):
            missing.append("%s -> %s 盘上不存在" % (REFS_INDEX_REL, c))
    return missing, len(cands)


def audit_rules(root):
    """跑齐三条，返回 (红, 提示, 计数 dict)。每条红都自带 [R-x] 标签。"""
    problems = []
    for rel in doc_copies_in_backup(root):
        problems.append("[R-a] %s 交付稿/演示文稿的副本住在证据归档里（只许 .md/.docx/.pptx"
                        " 之外的事物待在这里）" % rel)
    hard, note, nscan, nskip = leak_tokens(root)
    for h in hard:
        problems.append("[R-b] 证据层文件泄露内部路径: %s" % h)
    missing, nrefs = refs_index_dangling(root)
    for m in missing:
        problems.append("[R-c] %s" % m)
    counts = {"rb_scanned": nscan, "rb_skipped": nskip,
              "rb_note": len(note), "rc_refs_paths": nrefs}
    return problems, ["[R-b/note] %s" % n for n in note], counts


# 夹具具名清单: 判决行的分母由它现算, 加了夹具忘了改散文也会由这一行自己印出来。
FIXTURE_NAMES = ["positive_control", "denominator_guard", "clean_counterexample",
                 "third_party_exempt", "size_exempt", "empty_cache_dir",
                 "all_fails_are_printed", "declared_but_not_run",
                 "r_a_backup_doc_copy", "r_b_evidence_leak",
                 "r_b_tooldir_package_wide", "r_c_refs_dangling",
                 "live_state_clean"]


def _touch(box, rel, text="x"):
    p = os.path.join(box, *rel.split("/"))
    if not os.path.isdir(os.path.dirname(p)):
        os.makedirs(os.path.dirname(p), exist_ok=True)
    io.open(p, "w", encoding="utf-8").write(text)
    return p


def _host_path():
    """现拼一条 C:\\Users\\<name> 形状的主机绝对路径给夹具用。

    用 chr(92) 拼而不是在源文件里写下字面反斜杠串，为的是让夹具审的是**正则的形状**
    而不是这一份源文件里的某个具体写法（名字也故意不叫 lenovo，挡住「夹具与本机同名
    才被抓」这种假判据）。
    """
    b = chr(92)
    return "C:" + b + "Users" + b + "someone" + b + "work"



def report(fails, ran, declared_names):
    """把「跑过的夹具」与「声明的夹具」求差, 并把**全部**失败一次印完。

    旧实现是 `for f in fails: print(...); return 1` —— return 落在循环体里, 于是
    两条以上失败只讲第一条(台账第 26 条)。这里把它收成纯函数, 好让夹具能直接喂它。
    """
    lines = list(fails)
    missing = [n for n in declared_names if n not in set(ran)]
    for n in missing:
        lines.append("这条夹具声明了但根本没跑到: %s" % n)
    out = ["HYGIENE-SELFTEST-FAIL %s" % l for l in lines]
    return out, (1 if lines else 0), len(lines)


def selftest():
    """注入证明：造一个 .pyc、一个 .o、一个 tmp_*.json，三门必须各自认领。"""
    import shutil
    import tempfile
    box = tempfile.mkdtemp(prefix="hygiene_selftest_")
    fails = []
    ran = []
    try:
        os.makedirs(os.path.join(box, "flow", "__pycache__"))
        for rel in ("flow/__pycache__/x.pyc", "build/y.o",
                    "results/tmp_wl.json", "docs/报告.md"):
            p = os.path.join(box, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            io.open(p, "w", encoding="utf-8").write("x")
        hits, scanned = census(box)
        ran.append("positive_control")
        # 目录算一条命中，里面的 .pyc 不再重复计；分母只数真正扫到的文件（3 个）。
        want = {"flow/__pycache__/", "build/y.o", "results/tmp_wl.json"}
        got = set(hits)
        if got != want:
            fails.append("正对照应命中 %r 实际 %r" % (sorted(want), sorted(got)))
        if scanned != 3:
            fails.append("分母应为 3 个文件（缓存目录内的已剪枝）, 实际 %d" % scanned)
        ran.append("denominator_guard")
        # 反对照：干净树上必须 0 命中（命中数为 0 排在被审树之后，
        # 否则「扫不到东西」与「扫了且干净」在这条输出里长得一样）。
        干净 = os.path.join(box, "clean")
        os.makedirs(os.path.join(干净, "docs"))
        io.open(os.path.join(干净, "docs", "a.md"), "w",
                encoding="utf-8").write("x")
        h2, s2 = census(干净)
        ran.append("clean_counterexample")
        if h2 or s2 != 1:
            fails.append("反对照应 0 命中/1 文件, 实际 %r/%d" % (h2, s2))
        # 4) refs/ 里第三方的同名文件不算本包账上。
        os.makedirs(os.path.join(box, "refs", "riscv-tests"))
        io.open(os.path.join(box, "refs", "riscv-tests", "upstream.o"), "w",
                encoding="utf-8").write("x")
        h3, _ = census(box)
        ran.append("third_party_exempt")
        if any(r.startswith("refs/") for r in h3):
            fails.append("refs/ 里的第三方 .o 被误计: %r" % h3)
        # 5) 尺寸判据：同是 11 MB，明文产物必须被抓、已压缩的必须豁免
        #    （评审 P2-1 要的是"别随表明文大文件"，不是"包必须小"）。
        for nm in ("big_raw.vcd", "big_packed.gz"):
            with io.open(os.path.join(box, nm), "wb") as fh:
                fh.write(b"\0" * (11 << 20))
        b1, p1, m1 = big_files(box)
        ran.append("size_exempt")
        if [x[0] for x in b1] != ["big_raw.vcd"]:
            fails.append("尺寸正对照应只抓 big_raw.vcd, 实际 %r" % (b1,))
        if [x[0] for x in p1] != ["big_packed.gz"]:
            fails.append("已压缩件没被豁免: %r" % (p1,))
        if m1:
            fails.append("沙箱里没有 mp4, 却有 video 豁免项: %r" % (m1,))
        if len(b1) == 1 and len(p1) == 1:
            print("[fixture] size   11MB 明文被抓 / 11MB .gz 豁免")
        # 6) 只留一个**空**的字节码目录、里面一个文件都没有 —— 必须判红。
        #    这条专咬旧实现（只按文件后缀判）的形状：删掉目录名判据，本条即 FAIL。
        cache_only = os.path.join(box, "cache_only")
        os.makedirs(os.path.join(cache_only, "tb", "golden", "__pycache__"))
        io.open(os.path.join(cache_only, "tb", "golden", "epv_iss.py"), "w",
                encoding="utf-8").write("x")
        h6, s6 = census(cache_only)
        ran.append("empty_cache_dir")
        if h6 != ["tb/golden/__pycache__/"]:
            fails.append("空 __pycache__ 目录应被抓为唯一命中, 实际 %r" % (h6,))
        if s6 != 1:
            fails.append("缓存目录内部不该进分母, 应为 1 实际 %d" % s6)
        if not h6 and s6 == 1:
            fails.append("缓存目录判据没开火: 命中 0 而分母 1")
        else:
            print("[fixture] dirname 空 __pycache__ 被抓 / 其内部不计分母")
        # 7) 失败报告路径本身: 喂两条合成 FAIL, 两条都必须原样印出来。
        #    这条专咬旧实现那个「return 在 for 循环里」的形状 —— 抹掉它, 本条即 FAIL。
        _lines, _rc, _n = report(["甲", "乙"], [], FIXTURE_NAMES[:0])
        if _n != 2 or _rc != 1 or not all("HYGIENE-SELFTEST-FAIL" in x for x in _lines):
            fails.append("失败报告只讲了 %d 条(应有 2 条): %r" % (_n, _lines))
        if [x for x in _lines if "乙" in x] == []:
            fails.append("第二条 FAIL 被吞掉了: %r" % (_lines,))
        ran.append("all_fails_are_printed")
        # 8) 声明了却没跑到的夹具必须开火(否则「6/6」这种分母自己就会说谎)。
        _l2, _rc2, _ = report([], ["positive_control"], ["positive_control", "never_ran"])
        if _rc2 != 1 or not any("根本没跑到" in x and "never_ran" in x for x in _l2):
            fails.append("没跑到的夹具没被抓: %r" % (_l2,))
        ran.append("declared_but_not_run")

        # ---- 9) R-a: 证据归档里不许住稿件副本。正对照 = 归档里只有 .json/.v 这类
        #     读侧输入; 反对照 = 同一层塞进 .md/.docx/.pptx 各一份, 三份必须全被抓,
        #     且红因必须自带 [R-a] 标签(只看计数说不出是哪条规则开的火)。
        ra = os.path.join(box, "r_a")
        _touch(ra, "results/backup/preX/nn_bench.json")
        _touch(ra, "results/backup/epv32_core.v.pre_misaligned")
        h0 = doc_copies_in_backup(ra)
        ran.append("r_a_backup_doc_copy")
        if h0:
            fails.append("[R-a] 正对照(归档只有读侧输入)应 0 命中, 实际 %r" % (h0,))
        for nm in (u"报告.md", u"报告.docx", u"答辩.pptx"):
            _touch(ra, "results/backup/preX/" + nm)
        h1 = doc_copies_in_backup(ra)
        if len(h1) != 3 or not all(x.startswith("results/backup/") for x in h1):
            fails.append("[R-a] 三种稿件后缀应全被抓: %r" % (h1,))
        pa, _na, _ca = audit_rules(ra)
        if not any(x.startswith("[R-a]") for x in pa):
            fails.append("[R-a] 红因没点名是哪条规则开的火: %r" % (pa,))
        else:
            print("[fixture] r_a    md/docx/pptx 各咬一次 / 只读输入正对照 0 命中")

        # ---- 10) R-b 证据层泄露。注入串一律**复用判据自己的常量**, 不在夹具里另抄
        #     一份字面量 —— 否则改了常量夹具照样绿, 那条夹具就在审一个不存在的串。
        rb = os.path.join(box, "r_b")
        _touch(rb, "results/logs/step_a.log", "MATRIX 3/5 all_green=True\n")
        _touch(rb, "results/matrix.json", '{"all_green": true}\n')
        _touch(rb, "flow/driver.py", "print('ok')\n")
        hard0, note0, ns0, sk0 = leak_tokens(rb)
        ran.append("r_b_evidence_leak")
        if hard0 or note0 or ns0 != 3:
            fails.append("[R-b] 正对照应 0 红/0 提示/3 件, 实际 %r/%r/%d"
                         % (hard0, note0, ns0))
        _touch(rb, "results/logs/step_b.log", u"见 " + LEAK_TOKENS_EVIDENCE[0] + u"\n")
        _touch(rb, "results/sim/x.json", '{"p": "' + _host_path() + u'"}\n')
        _touch(rb, "flow/tool.py", u"# " + LEAK_TOKENS_EVIDENCE[1] + u"\n")
        hard1, note1, _n1, _s1 = leak_tokens(rb)
        problems_b, _n2, _c2 = audit_rules(rb)
        if not any("review-token" in x and x.startswith("results/logs/") for x in hard1):
            fails.append("[R-b] 证据层点名内部评审目录没被抓: %r" % (hard1,))
        if not any("host-path" in x and x.startswith("results/sim/") for x in hard1):
            fails.append("[R-b] 证据层主机绝对路径没被抓: %r" % (hard1,))
        if not any(x.startswith("[R-b]") for x in problems_b):
            fails.append("[R-b] 红因没点名规则: %r" % (problems_b,))
        # 分档守卫: 同一个串住在源层只许进提示档, 判红等于让门永远过不去。
        if any("flow/tool.py" in x for x in hard1):
            fails.append("[R-b] 源层命中被判红了(分档守卫失效): %r" % (hard1,))
        if not any("flow/tool.py" in x for x in note1):
            fails.append("[R-b] 源层命中没进提示档: %r" % (note1,))
        else:
            print("[fixture] r_b    证据层 review-token/host-path 判红 / 源层只记提示")

        # ---- 11) 工具状态目录走包级硬判据(它没有任何合法落点), 且包外两区不许开火。
        rc2 = os.path.join(box, "r_b2")
        _touch(rc2, "flow/tool.py", "print(1)\n")
        _touch(rc2, "results/logs/a.log", "ok\n")
        h3, n3, _a3, _b3 = leak_tokens(rc2)
        ran.append("r_b_tooldir_package_wide")
        if h3 or n3:
            fails.append("[R-b] 工具状态目录正对照应 0 命中: %r/%r" % (h3, n3))
        _touch(rc2, "docs/spec.md", u"见 " + LEAK_TOKEN_TOOLDIR + u"\n")
        _touch(rc2, u"三方评审" + u"/note.md", LEAK_TOKENS_EVIDENCE[0] + u"\n")
        _touch(rc2, "scratch_probe.py", LEAK_TOKEN_TOOLDIR + u"\n")
        h4, n4, _a4, _b4 = leak_tokens(rc2)
        if not any("docs/spec.md" in x and "tool-dir" in x for x in h4):
            fails.append("[R-b] 源层出现工具状态目录必须判红: %r" % (h4,))
        if any(x.split(" ")[0].startswith(u"三方评审/") for x in h4 + n4):
            fails.append("[R-b] 包外的评审工作区被算进包内账: %r" % (h4 + n4,))
        if any(x.split(" ")[0].startswith("scratch_") for x in h4 + n4):
            fails.append("[R-b] 包外 scratch 被算进包内账: %r" % (h4 + n4,))
        else:
            print("[fixture] r_b2   tool-dir 包级判红 / 包外两区不开火")

        # ---- 12) R-c: 生成的 refs/ 索引指向盘上没有的东西。三条对照: 全指得到(0 缺口)、
        #     指错一条(恰好抓那一条)、一条都解析不出来(判「解析面塌了」而不是判干净)。
        rd = os.path.join(box, "r_c")
        _touch(rd, "refs/riscv-tests/env/encoding.b")
        _touch(rd, "refs/rt.tar.gz")
        _touch(rd, "refs/README.md",
               u"# refs index\n- `refs/riscv-tests/` upstream\n- `refs/rt.tar.gz` src\n")
        miss0, n0 = refs_index_dangling(rd)
        ran.append("r_c_refs_dangling")
        if n0 != 2:
            fails.append("[R-c] 应解析到 2 条 refs/ 路径, 实际 %d (%r)" % (n0, miss0))
        if miss0:
            fails.append("[R-c] 正对照(每条都指得到)应 0 缺口: %r" % (miss0,))
        _touch(rd, "refs/README.md",
               u"# refs index\n- `refs/riscv-tests/`\n- `refs/rt.tar.gz`\n"
               u"- `refs/anlogic_td_probe.md` <- run_all.py\n")
        miss1, n1 = refs_index_dangling(rd)
        if not (n1 == 3 and len(miss1) == 1 and "anlogic_td_probe" in miss1[0]):
            fails.append("[R-c] 指向盘上不存在的那一条没被抓: %r (解析到 %d 条)"
                         % (miss1, n1))
        _touch(rd, "refs/README.md", u"# index\nnothing here\n")
        miss2, n2 = refs_index_dangling(rd)
        if n2 != 0 or not miss2:
            fails.append("[R-c] 空解析面被当成干净(n=%d miss=%r)" % (n2, miss2))
        pc, _n3, _c3 = audit_rules(rd)
        if not any(x.startswith("[R-c]") for x in pc):
            fails.append("[R-c] 红因没点名规则: %r" % (pc,))
        else:
            print("[fixture] r_c    缺口被抓 / 空解析面单独判红(不当干净)")

        # ---- 13) 现役包的落点: 本轮刚清完的 R-a 与 R-b 硬档必须归零, 且语料面不能塌。
        #     R-c 不在此断言 —— 现役 refs/README.md 确实还指着不存在的条目, 那是
        #     生成器(tools/make_navigation.py, 本轮不许动)的活, 由实时判决报出来。
        lp, ln, lc = audit_rules(ROOT)
        ran.append("live_state_clean")
        ra_now = [x for x in lp if x.startswith("[R-a]")]
        rb_now = [x for x in lp if x.startswith("[R-b]")]
        if ra_now or rb_now:
            fails.append("现役包 R-a/R-b 硬判据不为零(清理没做干净): R-a=%r R-b=%r"
                         % (ra_now[:3], rb_now[:3]))
        if lc["rb_scanned"] < 50:
            fails.append("R-b 语料只扫到 %d 个文本件, 明显不全, 拒绝判绿"
                         % lc["rb_scanned"])
        # ---- 13b) 嵌套提交包那一层：排除必须**只**对那一个目录名生效。
        #       两条臂互不遮蔽：submission/ 里同形状的中间件与那条自证串一律不算本包的账
        #       （否则父门红在自己那份判据源的一份逐字节副本上，见 NESTED_PACKAGE 定义处），
        #       而同样三样东西住在别的目录名下必须照样被抓（否则这行排除就是把门解除武装）。
        nest = os.path.join(box, "nest")
        _touch(nest, NESTED_PACKAGE + "/build/o.a")
        _touch(nest, NESTED_PACKAGE + "/x.py", u"y = %r\n" % LEAK_TOKEN_TOOLDIR)
        os.makedirs(os.path.join(nest, NESTED_PACKAGE, "__pycache__"))
        hn, sn = census(nest)
        fn_, _sk = text_corpus(nest)
        bn, _pn, _mn = big_files(nest)
        hardn, _noten, _an, _bn = leak_tokens(nest)
        _touch(nest, "pkg2/build/o.a")
        _touch(nest, "pkg2/x.py", u"y = %r\n" % LEAK_TOKEN_TOOLDIR)
        os.makedirs(os.path.join(nest, "pkg2", "__pycache__"))
        h2, s2b = census(nest)
        f2, _sk2 = text_corpus(nest)
        hard2, _no2, _a2, _b2 = leak_tokens(nest)
        want2 = set(["pkg2/build/o.a", "pkg2/__pycache__/"])
        if hn or fn_ or bn or hardn or sn != 0:
            fails.append("[nested] 嵌套包不该进本包账: census=%r text=%d big=%d hard=%r scanned=%d"
                         % (hn, len(fn_), len(bn), hardn, sn))
        if set(h2) != want2 or "pkg2/x.py" not in f2 or not hard2:
            fails.append("[nested] 同形状东西换了目录名必须照样被抓: hits=%r text=%r hard=%r"
                         % (sorted(set(h2) ^ want2), f2, hard2))
        if any(in_nested_package(x.split(" ")[0]) for x in h2 + hard2):
            fails.append("[nested] 排除之外的命中里出现了嵌套包路径: %r" % (h2 + hard2,))
        print("[fixture] nested 嵌套包 0 账 / 换目录名同形状 3 样全被抓")
        print("[fixture] live   hard R-a=0 R-b=0 note=%d rb_scanned=%d skipped_big=%d"
              " rc_parsed=%d rc_dangling=%d"
              % (len(ln), lc["rb_scanned"], lc["rb_skipped"], lc["rc_refs_paths"],
                 len([x for x in lp if x.startswith("[R-c]")])))
    finally:
        shutil.rmtree(box, ignore_errors=True)
    lines, rc, nfail = report(fails, ran, FIXTURE_NAMES)
    for line in lines:
        print(line)
    if rc:
        # 明细之后补一条 ASCII 判决行, 且它必须是 stdout 最后一行: 父进程按 UTF-8 解
        # 子进程输出而这台机器的控制台是 GBK —— 中文落在最后一屏会糊成乱码, 于是
        # 「哪条规则开的火」恰恰在最该看见它的那一行上丢失。上面那些明细行自带
        # [R-a]/[R-b]/[R-c] 标签, 这条只负责把「红了多少、跑了几条、声明了几条」说死。
        print("HYGIENE_SELFTEST_FAILED ran=%d declared=%d fails=%d"
              % (len(ran), len(FIXTURE_NAMES), nfail))
        return 1
    print("HYGIENE_SELFTEST_OK %d/%d (denominator computed from FIXTURE_NAMES;"
          " do not copy that count into prose)" % (len(ran), len(FIXTURE_NAMES)))
    return 0


def clean(dry):
    """删掉本次普查到的可再生产物，返回 (删了几个, 名单)。

    清理住在门里而非 README 的"交付前记得清"：矩阵会重新长出 .o/.a（Verilator 与
    综合的副产品），只在末端判、红了靠手工删，正是"文档说清了而盘上没清"的形状。
    只删被归类为可再生的三类；认不出的东西一律留在原地并判红。
    """
    hits, _ = census(ROOT)
    removed = []
    for rel in hits:
        # census 只会给出被归类为可再生的四类（.o/.a/.pyc、tmp_*.json|log，以及
        # 以 / 结尾的字节码目录），所以这里不再二次筛：多一层条件就多一层
        # "看起来安全其实永远不删"的空间。目录走 rmtree —— 旧实现把 os.remove
        # 用在目录路径上会抛，而它当初是靠下面那个**硬编码元组**删目录的，
        # 于是元组没列到的（tb/golden/__pycache__）永远删不掉。
        removed.append(rel)
        if not dry:
            p = os.path.join(ROOT, rel)
            if rel.endswith("/"):
                import shutil
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
    return removed


def main():
    os.chdir(ROOT)
    if "--selftest" in sys.argv:
        return selftest()
    if "--clean" in sys.argv:
        removed = clean(dry="--dry" in sys.argv)
        hits2, scanned2 = census(ROOT)
        for r in removed:
            print("HYGIENE-REMOVED %s" % r)
        if hits2:
            print("HYGIENE_CLEAN_INCOMPLETE removed=%d remaining=%d %s"
                  % (len(removed), len(hits2), hits2[:5]))
            return 1
        print("HYGIENE_CLEAN_OK removed=%d remaining=0 scanned=%d"
              % (len(removed), scanned2))
        return 0
    hits, scanned = census(ROOT)
    big, packed, movie = big_files(ROOT)
    if scanned < 50:
        print("HYGIENE_FAIL corpus=%d reason=SCANNED-TOO-FEW (corpus looks truncated,"
              " refusing to call it green)" % scanned)
        return 2
    problems, notes, counts = audit_rules(ROOT)
    for h in hits:
        print("HYGIENE-HIT %s" % h)
    for rel, sz in big:
        print("HYGIENE-BIG %s bytes=%d  (>10MB 明文产物；用 "
              "python flow/compress_artifact.py 压，回读验过才删原件)" % (rel, sz))
    for rel, sz in packed:
        print("HYGIENE-PACKED-EXEMPT %s bytes=%d (已是压缩形态)" % (rel, sz))
    for rel, sz in movie:
        print("HYGIENE-VIDEO-EXEMPT %s bytes=%d (Demo 成片本体，受 100MB 上限门管)"
              % (rel, sz))
    for n in notes:
        # 源与文档层点名内部路径 = 记下来给人看，不判红（理由见 leak_tokens 的 docstring）。
        print("HYGIENE-RULE-NOTE %s" % n)
    for m in problems:
        # 每条红都自带 [R-a]/[R-b]/[R-c] 标签: 只看计数无法知道是哪条规则开的火。
        print("HYGIENE-RULE-FAIL %s" % m)
    if hits or big or problems:
        # 末行必须自带**首例点名**：链条那道 `out[:600]` 切片会把中间那几十行
        # HYGIENE-*-FAIL 切掉，只留计数就等于「红是红了，但没人看得见为什么」。
        # 这里印的是原因的形状而不是证据的形状（不带完整哈希/路径串以外的东西）。
        first = (hits + [b[0] + " big-bytes" for b in big] + list(problems) + ["(无)"])[0]
        print("HYGIENE_FAIL hits=%d big=%d rules=%d scanned=%d note=%d 首例=%s"
              " (remove/compress the middleware, or list non-regenerable ones"
              " in the whitelist with a reason)"
              % (len(hits), len(big), len(problems), scanned, len(notes),
                 first[:150]))
        return 1
    print("HYGIENE_OK scanned=%d hits=0 big=0 packed=%d video=%d rules=0 note=%d"
          " rb_scanned=%d rb_skipped=%d rc_paths=%d"
          % (scanned, len(packed), len(movie), len(notes),
             counts["rb_scanned"], counts["rb_skipped"], counts["rc_refs_paths"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
