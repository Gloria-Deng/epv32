#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tools/elf_manifest.py -- `build/elf/` 的「ELF ↔ 用例 ↔ 判决归档 ↔ 复跑入口」清单。

评审两处要这张表（§五 P2-12 的后半、逐目录段 P2-15）：一百多个 ELF 没有一份清单说明
「哪个文件对应哪条命令 / 哪个用例」。文件名看着能猜，但**猜出来的映射不是清单**，
所以三列全部由盘上事实导出：

  用例列   `rv32ui__add` 这类按 `<suite>__<test>` 解，并要求
           `results/compliance/<suite>.json` 的 `rows[]` 里**真有** `test == <test>` 这一行。
  归档列   `results/sim/<stem>.json` 与 `results/sim/<stem>_isco.json` 两个形态都试，
           只列实际存在的那些。66 个是双份（逐例判决 + 退休轨迹比对），那是两个不同的
           判据面，不是重复。
  入口列   只写**现查得到的生产者**：compliance 家族固定 `flow/rt.py`；其余拿 stem 的主干
           去 `flow/` 与 `tools/` 源码里查，查不到就落 `NO_ACTIVE_PRODUCER` + 单独计数，
           绝不编一条看着合理的命令行（本包刚被 `run_all.py --only` 那种「文档里有、运行时
           没有」的假命令咬过一次）。

判据（fail-closed，都由本文件自己数）：
  DEN1 每个已声明家族至少有一个条目（分派逻辑瞎了会让整族静默消失）
  DEN2 unmapped == 0
  DEN3 elfs == mapped + unmapped（守恒式）
  ARCHIVE-MISSING / PRODUCER-MISSING 表里写的每个路径都必须真在盘上
  SETUP-FAIL 目录缺失或一个 ELF 都没有 —— 分母塌了不许读成「零缺陷」
"""
from __future__ import print_function

import io
import json
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ELF_DIR = os.path.join("build", "elf")
SIM_DIR = os.path.join("results", "sim")
COMPL_DIR = os.path.join("results", "compliance")
OUT_JSON = os.path.join("results", "elf_manifest.json")
OUT_MD = os.path.join("results", "elf_manifest.md")

SUITE_RE = re.compile(r"^(rv32(?:ui|um|mi))__(\w+)$")
FAMILY_NAMES = ("compliance", "bench", "probe")
FIXTURE_NAMES = ("positive", "stray_elf", "archive_removed",
                 "denominator_guard", "unclassified_family")


def _p(*rel):
    return os.path.join(ROOT, *rel)


def read_json(rel):
    return json.load(io.open(_p(rel), encoding="utf-8"))


def compliance_tests():
    out = {}
    d = _p(COMPL_DIR)
    if not os.path.isdir(d):
        return out
    for n in sorted(os.listdir(d)):
        if not n.endswith(".json"):
            continue
        rows = read_json(os.path.join(COMPL_DIR, n)).get("rows", [])
        out[n[:-5]] = set(r.get("test") for r in rows if r.get("test"))
    return out


def sim_stems():
    d = _p(SIM_DIR)
    if not os.path.isdir(d):
        return set()
    return set(f[:-5] for f in os.listdir(d) if f.endswith(".json"))


def producers_text():
    chunks = []
    for sub in ("flow", "tools"):
        d = _p(sub)
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if f.endswith((".py", ".sh")):
                try:
                    chunks.append(io.open(_p(sub, f), encoding="utf-8",
                                          errors="replace").read())
                except Exception:
                    pass
    return "\n".join(chunks)


def build(family_names=FAMILY_NAMES, elf_dir=ELF_DIR):
    if not os.path.isdir(_p(elf_dir)):
        return None, ["SETUP-FAIL no elf dir"]
    elfs = sorted(f for f in os.listdir(_p(elf_dir)) if f.endswith(".elf"))
    if not elfs:
        return None, ["SETUP-FAIL zero elfs, denominator collapsed"]
    if not os.path.isdir(_p(SIM_DIR)):
        return None, ["SETUP-FAIL no results/sim dir"]

    cmpl, sim, prod = compliance_tests(), sim_stems(), producers_text()
    rows, unmapped, no_prod, problems = [], [], [], []
    for elf in elfs:
        stem = elf[:-4]
        m = SUITE_RE.match(stem)
        if m and m.group(1) in cmpl:
            suite, test = m.group(1), m.group(2)
            if test not in cmpl[suite]:
                unmapped.append([elf, "suite json has no such test"])
                continue
            if stem not in sim:
                # 逐例判决只在 results/sim/<stem>.json：缺了就报错，不许用 _isco
                # 的退休轨迹比对顶替逐例 PASS/FAIL。
                problems.append("VERDICT-ARCHIVE-MISSING %s 缺 %s"
                                % (elf, os.path.join(SIM_DIR, stem + ".json")))
                unmapped.append([elf, "compliance verdict json missing"])
                continue
            family, case = "compliance", "%s / %s" % (suite, test)
            archives = [os.path.join(COMPL_DIR, suite + ".json"),
                        os.path.join(SIM_DIR, stem + ".json")]
            producer = os.path.join("flow", "rt.py")
        else:
            have = [s for s in (stem, stem + "_isco") if s in sim]
            if not have:
                unmapped.append([elf, "neither <stem>.json nor <stem>_isco.json"])
                continue
            family = "probe" if all(h.endswith("_isco") for h in have) else "bench"
            case = stem
            archives = [os.path.join(SIM_DIR, h + ".json") for h in have]
            key = stem.split("__")[0]
            producer = os.path.join("flow", "run_all.py") if key and key in prod else None
        if producer is None:
            no_prod.append(elf)
        rows.append({"elf": elf_dir + "/" + elf, "case": case, "family": family,
                     "verdict_archives": archives,
                     "producer": producer or "NO_ACTIVE_PRODUCER"})

    fam_counts = {}
    for r in rows:
        fam_counts[r["family"]] = fam_counts.get(r["family"], 0) + 1
    for fam in family_names:
        if fam not in fam_counts:
            problems.append("DEN1-A declared family %s got zero rows (dispatch blind?)" % fam)
    for fam in sorted(fam_counts):
        if fam not in family_names:
            problems.append("DEN1-B rows classified into undeclared family %s (%d 条)"
                            % (fam, fam_counts[fam]))
    if len(rows) + len(unmapped) != len(elfs):
        problems.append("DEN3 rows=%d + unmapped=%d != elfs=%d"
                        % (len(rows), len(unmapped), len(elfs)))
    for r in rows:
        for a in r["verdict_archives"]:
            if not os.path.isfile(_p(a)):
                problems.append("ARCHIVE-MISSING %s -> %s" % (r["elf"], a))
        if r["producer"] != "NO_ACTIVE_PRODUCER" and not os.path.isfile(_p(r["producer"])):
            problems.append("PRODUCER-MISSING %s -> %s" % (r["elf"], r["producer"]))
    if unmapped:
        problems.append("DEN2 unmapped=%d e.g. %s" % (len(unmapped), unmapped[:3]))
    return {"elfs": len(elfs), "mapped": len(rows), "unmapped": unmapped,
            "families": fam_counts, "no_active_producer": no_prod,
            "rows": rows}, problems


def emit(data):
    with io.open(_p(OUT_JSON), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    lines = [u"# `build/elf/` 清单：ELF ↔ 用例 ↔ 判决归档 ↔ 复跑入口", "",
             u"本表由 `tools/elf_manifest.py` 从盘上现算，不手写；每一列的存在性都由那一步",
             u"自己核对（归档不在盘上、生产者脚本不在盘上，都会判红而不是留空）。", "",
             u"| ELF | 用例 | 家族 | 判决归档 | 复跑入口 |",
             u"|---|---|---|---|---|"]
    for r in data["rows"]:
        lines.append(u"| `%s` | `%s` | %s | %s | `%s` |" % (
            r["elf"], r["case"], r["family"],
            u" 与 ".join(u"`%s`" % a for a in r["verdict_archives"]), r["producer"]))
    lines.append(u"")
    lines.append(u"合计：ELF `%d`，映射 `%d`，未映射 `%d`，无现役生产者 `%d`"
                 u"（逐条名单在 `%s` 的 `no_active_producer` 键里）。"
                 % (data["elfs"], data["mapped"], len(data["unmapped"]),
                    len(data["no_active_producer"]), OUT_JSON.replace(os.sep, "/")))
    with io.open(_p(OUT_MD), "w", encoding="utf-8", newline="\n") as f:
        f.write(u"\n".join(lines) + u"\n")


def selftest():
    data, probs = build()
    if data is None or probs:
        print("ELF_SELFTEST_SETUP_FAIL real tree not clean: %s" % (probs[:2],))
        return 1
    fails, fired = [], 0

    # positive: 显式断言现树映射形状
    if data["mapped"] <= 0 or data["unmapped"]:
        fails.append("positive: 现树映射不成立")
    else:
        fired += 1

    # stray_elf: 往 build/elf 塞一个没有归档的 elf -> 必须 unmapped>0
    probe = _p(ELF_DIR, "__elfmanifest_probe__.elf")
    open(probe, "wb").write(b"\x7fELF")
    try:
        d2, p2 = build()
        if d2 is None or not any(x.startswith("DEN2") for x in p2):
            fails.append("stray_elf: 没报 DEN2, 实际 %s" % (p2[:1],))
        else:
            fired += 1
    finally:
        os.remove(probe)

    # archive_removed: 拿走一条真实归档 -> 必须红且点名
    victim = _p(SIM_DIR, "rv32ui__add.json")
    bak = victim + ".selftest_bak"
    before = io.open(victim, "rb").read()
    shutil.move(victim, bak)
    try:
        d3, p3 = build()
        reasons = " ".join(p3)
        if d3 is None or not (("VERDICT-ARCHIVE-MISSING" in reasons)
                              or ("DEN2" in reasons)):
            fails.append("archive_removed: 抽走逐例判决归档仍不红, 实际 %s" % (p3[:2],))
        else:
            fired += 1
    finally:
        shutil.move(bak, victim)
    after = io.open(victim, "rb").read()
    if after != before:
        fails.append("还原逐字节不等 (len %d -> %d), 这台机器被本夹具改坏了"
                     % (len(before), len(after)))
    # 还原后必须复绿（证明刚才的红是那次注入造成的, 不是文件坏了）
    d4, p4 = build()
    if d4 is None or p4:
        fails.append("还原后没复绿: %s" % (p4[:2],))

    # denominator_guard: 目录不存在 -> SETUP-FAIL, 不许当成「零缺陷」
    d5, p5 = build(elf_dir=os.path.join("build", "__no_such_dir__"))
    if d5 is not None or not (p5 and p5[0].startswith("SETUP-FAIL")):
        fails.append("denominator_guard: 空目录没判 SETUP-FAIL")
    else:
        fired += 1

    # unclassified_family: 声明里去掉 probe -> DEN1 必须点名 probe
    d6, p6 = build(family_names=("compliance", "bench"))
    if not any("probe" in x for x in p6):
        fails.append("unclassified_family: 去掉声明的家族没报 DEN1")
    else:
        fired += 1

    print("ELF_MANIFEST_SELFTEST OK %d/%d" % (fired, len(FIXTURE_NAMES)))
    for f in fails:
        print("ELF_FIXTURE_FAIL", f[:150])
    return 1 if fails else 0


def main(argv):
    if "--selftest" in argv:
        return selftest()
    data, problems = build()
    if data is None:
        print("ELF_MANIFEST_SETUP_FAIL %s" % "; ".join(problems)[:180])
        return 2
    if problems:
        for p in problems[:8]:
            print("ELF-MANIFEST-PROBLEM " + p[:170])
        print("ELF_MANIFEST_FAIL elfs=%d unmapped=%d problems=%d"
              % (data["elfs"], len(data["unmapped"]), len(problems)))
        return 1
    if "--write" in argv:
        emit(data)
    print("ELF_MANIFEST_OK elfs=%d mapped=%d unmapped=0 families=%s no_producer=%d"
          % (data["elfs"], data["mapped"],
             ",".join("%s:%d" % kv for kv in sorted(data["families"].items())),
             len(data["no_active_producer"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
