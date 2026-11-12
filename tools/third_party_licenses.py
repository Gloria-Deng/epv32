#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第三方许可证收编门（评审 P2-18）：把随包分发的每个第三方组件的许可证**全文**
从本机已安装的工件树里逐字节取回，落在 refs/THIRD_PARTY_LICENSES/ 下。

为什么要有这个工具，而不是直接在包根 LICENSE 里"点名"：
  评审 P2-18 的原话是"部分许可证（如 Verilator 的 LGPL/Artistic 双许可）要求随附许可证全文"。
  点名不等于随附。而**凭记忆写一份许可证文本进包里就是伪造合规件** —— 所以本工具
  唯一允许的文本来源是本机安装树里的那个文件本身，复制走二进制模式（文本模式复制
  会把 LF 变成 CRLF，那样落下来的就不是同一份文件了），复制完立刻把两侧的 sha256
  与字节数一起算出来并断言相等。

判据形状（默认调用即真判决，不是 --selftest 才判）：
  1. 每个组件的每个期望文件：源存在 → 逐字节复制 → 两侧 sha256 与 bytes 必须相等，
     否则判 CORRUPT；源不存在 → 判 MISSING；
  2. MISSING 的组件必须在 UNAVAILABLE_OK 里显式列名并**带一句理由**，才降级成
     DECLARED_UNAVAILABLE（这是一句可机器反驳的主张，不是一张免死牌：列名而理由为空
     即判 UNDECLARED）；没列名的 MISSING 一律判红；
  3. 分母守卫：清单里声明的条目数必须等于逐条记录的条目数，且登记簿里印的
     "共 N 个文件"必须由本次运行现算 —— 全树一个文件都没收到时不许判绿；
  4. --check 模式不复写任何文件，只核"包内那份与安装树里那份现在是否仍逐字节相同"，
     外加核包内是否存在没进登记簿的孤儿文件（登记表与磁盘两份现实必须一致）。

--selftest 用临时替身房做双向 A/B：造一个"源不存在"的组件、造一个"复制后被改一个字节"
的组件、造一个"列名但理由为空"的组件，三条都必须各自开火且点名原因。
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join("refs", "THIRD_PARTY_LICENSES")
MSYS = r"E:\msys64\ucrt64\share\licenses"
TD_ROOT = r"E:\Anlogic\TD_6.2.1_Engineer_6.2.168.116"

# 本包用到的第三方组件。files 是**必须随包**的许可证全文（一次分发时缺哪份就不合规）。
COMPONENTS = [
    {"name": "yosys", "role": "综合与等价核对", "spdx": "ISC",
     "dir": os.path.join(MSYS, "yosys"), "files": ["LICENSE"]},
    {"name": "verilator", "role": "周期精确仿真台（tb/sim_main.cpp 的宿主）",
     "spdx": "随包这份是 LGPL-3.0 全文（上游另列 Artistic-2.0 为可选许可，"
             "但本机安装树里只有 LGPL 那一份 → 未随包）",
     "dir": os.path.join(MSYS, "verilator"), "files": ["LICENSE"]},
    {"name": "binutils", "role": "交叉汇编与链接（as/ld，rv32im 目标）",
     "spdx": "GPL 系四份全文（COPYING=GPL-2.0、COPYING3=GPL-3.0 及两份 "
             "LGPL 版本），以随包文本为准",
     "dir": os.path.join(MSYS, "binutils"),
     "files": ["COPYING", "COPYING3", "COPYING.LIB", "COPYING3.LIB"]},
    {"name": "gcc", "role": "交叉 C 编译器与 libgcc",
     "spdx": "GPL-3.0 + GCC Runtime Library Exception 3.1（两份都随包）",
     "dir": os.path.join(MSYS, "gcc"),
     "files": ["COPYING3", "COPYING.RUNTIME"]},
    {"name": "mingw-crt", "role": "本机宿主侧运行时（Verilator 产物的 C 运行时）",
     "spdx": "ZPL-2.1（文件自陈：其中被显著标注为 Public Domain / BSD / LGPL "
             "的部分按各自许可）",
     "dir": os.path.join(MSYS, "crt"), "files": ["COPYING"]},
    {"name": "riscv-tests", "role": "官方合规套件（ISA 三套件用例来源）",
     "spdx": "BSD-3-Clause（UC Berkeley Regents 文本，三条条件齐全）",
     "dir": os.path.join(ROOT, "refs", "riscv-tests"), "files": ["LICENSE"]},
    {"name": "anlogic-td", "role": "国产 FPGA 全流程（综合/PnR/位流）",
     "spdx": "Proprietary-EULA（厂商最终用户许可协议）",
     # 本机安装树里 license/ 只有一个机器密钥文件 Anlogic.lic，**不是**协议全文；
     # 全文在厂商站点/装机合同里，不在本机磁盘上 → 于是这条走 DECLARED_UNAVAILABLE。
     "dir": os.path.join(TD_ROOT, "__no_eula_on_disk__"), "files": ["LICENSE"]},
    {"name": "newlib", "role": "裸机 libc（-nostdlib 下仍取它的头与启动件）",
     "spdx": "BSD-4-clause-Style（含厂商特款条款）",
     # 本机 MSYS2 的 licenses/ 下没有 newlib/libgloss 目录（交叉工具链是自建前缀），
     # 所以这条同样只能显式记"没找到"，不许凭记忆补一份。
     "dir": os.path.join(MSYS, "newlib"), "files": ["COPYING"]},
]

# 本机确实拿不到全文的组件：列名 + 理由，二者缺一即判红。
# 理由必须是"为什么拿不到"，不是"我们不需要"。
UNAVAILABLE_OK = {
    "anlogic-td": "本机安装树 license/ 下只有机器密钥 Anlogic.lic，协议全文不在磁盘上；"
                  "全文随厂商装机合同分发，包内只指向其安装位置而不复制",
    "newlib": "本机交叉工具链是自建前缀，MSYS2 的 share/licenses/ 下没有 newlib 条目，"
              "磁盘上没有可逐字节取回的全文副本",
}

MANIFEST_NAME = "manifest.json"

# selftest 必须逐条跑到的一条不少 —— 名单与"跑到的集合"求差即判红。
FIXTURE_NAMES = ("positive-control", "missing", "undeclared", "stray-declaration",
                 "corrupt", "denominator", "zero-copied", "real-tree")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return h.hexdigest()


def audit(root, write=True, expected_override=None):
    """返回 (记录列表, 问题列表)。root 是包根；写落在 root/OUT_DIR。

    expected_override 只给 --selftest 的分母夹具用（生产路径传 None，由清单现算），
    它存在的唯一理由是让"记录数 != 声明数"这条守卫真的被喂过一次。
    """
    recs, problems = [], []
    out_dir = os.path.join(root, OUT_DIR)
    for comp in COMPONENTS:
        for fn in comp["files"]:
            src = os.path.join(comp["dir"], fn)
            dst = os.path.join(out_dir, comp["name"], fn)
            rel_src = src.replace("\\", "/")
            rec = {"component": comp["name"], "file": fn, "source": rel_src,
                   "delivered": os.path.join(OUT_DIR, comp["name"], fn).replace("\\", "/")}
            if not os.path.isfile(src):
                reason = UNAVAILABLE_OK.get(comp["name"])
                if reason and reason.strip():
                    rec["status"] = "DECLARED_UNAVAILABLE"
                    rec["reason"] = reason
                    # 显式声明过的缺席: 包内不许留一份空文件或旧文件冒充
                    if os.path.isfile(dst):
                        problems.append(
                            "[STALE-COPY] %s 已声明拿不到全文, 但包内仍留有 %s"
                            % (comp["name"], rec["delivered"]))
                elif comp["name"] in UNAVAILABLE_OK:
                    rec["status"] = "UNDECLARED"
                    problems.append(
                        "[UNDECLARED] %s 在 UNAVAILABLE_OK 里列了名但理由为空 —— "
                        "豁免必须带一句可反驳的理由" % comp["name"])
                else:
                    rec["status"] = "MISSING"
                    problems.append("[MISSING] 安装树里找不到 %s" % rel_src)
                recs.append(rec)
                continue
            if comp["name"] in UNAVAILABLE_OK:
                # 声明"本机拿不到"而源其实存在 ⇒ 这条声明已经过期，留着它就会永远
                # 不再复制真正的全文。判红而不是静默跳过。
                problems.append(
                    "[STRAY-DECLARATION] %s 在 UNAVAILABLE_OK 里声明拿不到，但源其实"
                    "在 %s —— 要么删掉这条声明，要么解释为什么仍不随包" %
                    (comp["name"], rel_src))
            if write:
                if not os.path.isdir(os.path.dirname(dst)):
                    os.makedirs(os.path.dirname(dst))
                shutil.copyfile(src, dst)          # 二进制语义, 不动行尾
            s_sha, d_sha = sha(src), (sha(dst) if os.path.isfile(dst) else None)
            s_sz, d_sz = os.path.getsize(src), (os.path.getsize(dst)
                                                if os.path.isfile(dst) else -1)
            rec["sha256"] = s_sha
            rec["bytes"] = s_sz
            if not os.path.isfile(dst):
                # 只核模式下"包内还没有这一份"是可交付状态，不是内容损坏：
                # 把两者混成一个 CORRUPT，下一次就有人为了变绿去改判据而不是去收编。
                rec["status"] = "NOT-DELIVERED"
                problems.append("[NOT-DELIVERED] %s 在包内缺失（跑一次不带 --check 的"
                                "收编即可）: %s" % (comp["name"], rec["delivered"]))
            elif s_sha != d_sha or s_sz != d_sz:
                rec["status"] = "CORRUPT"
                problems.append(
                    "[CORRUPT] %s 两侧不一致 src=%s/%d dst=%s/%d"
                    % (comp["name"], (d_sha or "")[:12], s_sz,
                       (d_sha or "")[:12], d_sz))
            else:
                rec["status"] = "COPIED" if write else "VERIFIED"
            recs.append(rec)
    expected = (sum(len(c["files"]) for c in COMPONENTS)
                if expected_override is None else expected_override)
    if len(recs) != expected:
        problems.append("[DENOMINATOR] 声明 %d 条但只记录 %d 条"
                        % (expected, len(recs)))
    got = [r for r in recs if r["status"] in ("COPIED", "VERIFIED")]
    if not got:
        problems.append("[ZERO-COPIED] 一个许可证全文都没收到 —— 不许判绿"
                        "（这是登记簿塌了，不是本包合规）")
    return recs, problems


def orphan_scan(root):
    """包内已存在但不在本次清单里的 .txt/许可证文件 —— 登记表与磁盘两份现实要一致。"""
    out_dir = os.path.join(root, OUT_DIR)
    listed = set()
    # 两侧必须在同一个路径空间里比：orphan 那一侧是相对包根的路径，
    # 这里如果用绝对路径拼，就永远匹配不上 —— 于是每一份合法交付件都被报成孤儿
    # （首跑就是这样：10 份全文全被判 LIC-ORPHAN）。
    for comp in COMPONENTS:
        for fn in comp["files"]:
            listed.add(os.path.join(OUT_DIR, comp["name"], fn).replace("\\", "/"))
    orphans = []
    for dirpath, _dirs, files in os.walk(out_dir):
        for fn in files:
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace("\\", "/")
            if rel not in listed and fn != MANIFEST_NAME and not fn.endswith(".md"):
                orphans.append(rel)
    return sorted(orphans)


def write_outputs(root, recs):
    counts = {}
    for r in recs:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    man = {"counting_basis": "本次运行逐条现算; 期望条数由 COMPONENTS 的 files 求和",
           "components_declared": len(COMPONENTS),
           "files_expected": sum(len(c["files"]) for c in COMPONENTS),
           "files_recorded": len(recs),
           "status_counts": counts,
           "entries": recs}
    out_dir = os.path.join(root, OUT_DIR)
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    io.open(os.path.join(out_dir, MANIFEST_NAME), "w", encoding="utf-8",
            newline="\n").write(json.dumps(man, ensure_ascii=False, indent=1))

    # README 由清单现算生成 —— 不手抄数字（评审 P2-7 点的那类"由长度决定的数"）
    L = [u"# 第三方组件许可证随包情况", "",
         u"本目录由 `python tools/third_party_licenses.py` 生成，登记表是 "
         + "`%s`。这里的每一份文本都是**从本机安装树逐字节复制**的，" % MANIFEST_NAME,
         u"复制后立刻比对两侧 sha256 与字节数；不一致即判 `CORRUPT`，",
         u"本机确实没有全文的组件走 `DECLARED_UNAVAILABLE` 并**必须带一句理由** —— "
         u"没有任何一份文本是凭记忆写的。", "",
         u"| 组件 | 用途 | 许可证标识 | 状态 | 随包文件 | 字节 | sha256 前 12 位 |",
         u"|---|---|---|---|---|---|---|"]
    by_name = {}
    for r in recs:
        by_name.setdefault(r["component"], []).append(r)
    for comp in COMPONENTS:
        rs = by_name.get(comp["name"], [])
        st = rs[0]["status"] if rs else "NOT-RECORDED"
        for k, r in enumerate(rs):
            L.append(u"| %s | %s | %s | %s | `%s` | %s | %s |" % (
                comp["name"] if k == 0 else u"〃",
                comp["role"] if k == 0 else u"〃",
                (comp["spdx"] if k == 0 else u"〃").replace(u"|", u"\\|"),
                r["status"], r["delivered"],
                r.get("bytes", u"—"), (r.get("sha256") or u"—")[:12]))
        if st == "DECLARED_UNAVAILABLE":
            L.append(u"|  |  |  |  |  |  | 理由：%s |" % rs[0]["reason"])
    L += [u"", u"复跑与核验：", "",
          u"```",
          u"python tools/third_party_licenses.py            # 收编并自证",
          u"python tools/third_party_licenses.py --check     # 只核, 不复写",
          # 条数由夹具清单本身现算。以前这行写死「三条注入」, 而本工具末行印的是
          # LICENSE_SELFTEST_OK 8/8 —— 生成的自述句成了第二份抄本, 抄本已经漂了。
          u"python tools/third_party_licenses.py --selftest  # 跑 %d 条夹具"
          u"（含双向注入）, 末行 LICENSE_SELFTEST_OK N/N" % len(FIXTURE_NAMES),
          u"```", ""]
    io.open(os.path.join(out_dir, "README.md"), "w", encoding="utf-8",
            newline="\n").write(u"\n".join(L))
    return counts


def selftest():
    """双向 A/B。每条夹具必须自己点名"为什么红"，且最后核一遍有没有夹具根本没跑 ——
    一条从没被喂过的判据在输出里和一条不存在的判据长得一样。"""
    global COMPONENTS, UNAVAILABLE_OK
    fails, ran = [], set()
    box = tempfile.mkdtemp(prefix="lic_selftest_")
    saved = (COMPONENTS, UNAVAILABLE_OK)
    try:
        good_dir = os.path.join(box, "src", "good")
        stray_dir = os.path.join(box, "src", "stray")
        os.makedirs(good_dir)
        os.makedirs(stray_dir)
        io.open(os.path.join(good_dir, "LICENSE"), "w",
                encoding="utf-8").write("ISC license text\n")
        io.open(os.path.join(stray_dir, "LICENSE"), "w",
                encoding="utf-8").write("actually on disk\n")
        COMPONENTS = [
            {"name": "good", "role": "正对照", "spdx": "ISC",
             "dir": good_dir, "files": ["LICENSE"]},
            {"name": "absent", "role": "源不存在且没声明", "spdx": "X",
             "dir": os.path.join(box, "src", "absent"), "files": ["LICENSE"]},
            {"name": "declared", "role": "列名但理由为空", "spdx": "Y",
             "dir": os.path.join(box, "src", "declared"), "files": ["LICENSE"]},
            {"name": "stray", "role": "声明拿不到但源在", "spdx": "Z",
             "dir": stray_dir, "files": ["LICENSE"]},
        ]
        UNAVAILABLE_OK = {"declared": "   ", "stray": "本机拿不到（其实拿得到）"}
        recs, probs = audit(box, write=True)
        st = {r["component"]: r["status"] for r in recs}
        if st.get("good") not in ("COPIED", "VERIFIED"):
            fails.append("正对照没收到全文: %r" % st)
        else:
            ran.add("positive-control")
        if not any(p.startswith("[MISSING]") for p in probs):
            fails.append("源不存在却没判 MISSING: %r" % probs[:3])
        else:
            ran.add("missing")
        if not any(p.startswith("[UNDECLARED]") for p in probs):
            fails.append("列名而理由为空却没开火: %r" % probs[:3])
        else:
            ran.add("undeclared")
        if not any(p.startswith("[STRAY-DECLARATION]") for p in probs):
            fails.append("声明过期（源其实在）却没开火: %r" % probs[:3])
        else:
            ran.add("stray-declaration")
        # 篡改包内那份 → 只核模式必须判 CORRUPT
        gp = os.path.join(box, OUT_DIR, "good", "LICENSE")
        io.open(gp, "w", encoding="utf-8").write("ISC license texk\n")
        _r2, p2 = audit(box, write=False)
        if not any(p.startswith("[CORRUPT]") for p in p2):
            fails.append("包内副本被改一字节而只核模式没判 CORRUPT")
        else:
            ran.add("corrupt")
        # 分母守卫: 声明 4 条只记录到 3 条必须红
        _r3, p3 = audit(box, write=False, expected_override=99)
        if not any(p.startswith("[DENOMINATOR]") for p in p3):
            fails.append("分母守卫没开火（记录数 != 声明数）")
        else:
            ran.add("denominator")
        # 空清单: 一个都没收到时必须判红, 不许判绿
        COMPONENTS = []
        _r4, p4 = audit(box, write=False)
        if not any(p.startswith("[ZERO-COPIED]") for p in p4):
            fails.append("一条全文都没收到却判成了绿")
        else:
            ran.add("zero-copied")
    finally:
        COMPONENTS, UNAVAILABLE_OK = saved
        shutil.rmtree(box, ignore_errors=True)
    # 真实安装树正对照
    recs3, probs3 = audit(ROOT, write=False)
    n_real = sum(1 for r in recs3 if r["status"] == "VERIFIED")
    if n_real == 0:
        fails.append("真实树一条 VERIFIED 都没有: %r" % [r["status"] for r in recs3])
    elif any(p.startswith("[ZERO-COPIED]") for p in probs3):
        fails.append("真实树被判 ZERO-COPIED —— 安装树没装全")
    else:
        ran.add("real-tree")
    never = sorted(set(FIXTURE_NAMES) - ran)
    if never:
        fails.append("这些夹具根本没跑到（等于这条判据是潜伏版）: %s" % never)
    print("FIXTURES ran=%d expected=%d real_verified=%d" % (
        len(ran), len(FIXTURE_NAMES), n_real))
    for f in fails:
        print("LICENSE-SELFTEST-FAIL %s" % f)
    if fails:
        return 1
    print("LICENSE_SELFTEST_OK %d/%d (%s)"
          % (len(ran), len(FIXTURE_NAMES), ", ".join(FIXTURE_NAMES)))
    return 0


def main():
    os.chdir(ROOT)
    if "--selftest" in sys.argv:
        return selftest()
    check = "--check" in sys.argv
    recs, probs = audit(ROOT, write=not check)
    counts = {} if check else write_outputs(ROOT, recs)
    orphans = orphan_scan(ROOT)
    for r in recs:
        print("LIC %-22s %-8s bytes=%-8s %s" % (
            r["component"] + "/" + r["file"], r["status"],
            r.get("bytes", "-"), (r.get("sha256") or "-")[:12]))
    for p in probs:
        print("LIC-PROBLEM %s" % p)
    for o in orphans:
        print("LIC-ORPHAN %s (在包内但不在登记表)" % o)
    n_ok = sum(1 for r in recs if r["status"] in ("COPIED", "VERIFIED"))
    n_dec = sum(1 for r in recs if r["status"] == "DECLARED_UNAVAILABLE")
    tail = ("checked" if check else "copied")
    if probs or orphans:
        print("LICENSE_GATE_FAILED %s=%d declared_unavailable=%d problems=%d "
              "orphans=%d" % (tail, n_ok, n_dec, len(probs), len(orphans)))
        return 1
    print("LICENSE_GATE_OK %s=%d declared_unavailable=%d problems=0 orphans=0 "
          "expected=%d" % (tail, n_ok, n_dec,
                           sum(len(c["files"]) for c in COMPONENTS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
