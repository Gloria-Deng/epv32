#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""coverage_report.py -- 给「66 个程序全过」补上「且 RTL 被看到了多少」这一格。

第三方评审 R3 的 P2-3 (实测确认成立): 全包没有 line/toggle 覆盖率收集
(`flow`/`tb`/`tools` 里 `--coverage` 与 `covergroup` 零命中), 于是「通过率 100%」
背后没有覆盖率数字, 评委无法判断没测到的地方有多少。

三臂分流 (为什么必须有第三臂)
----------------------------
build/sim/epv32sim.exe 是 flow/rt.py 里 SIM 指向的**认证**仿真器; 带 --coverage
重编会把计数插进每一句 RTL, 周期数就不再是认证臂那个形状。所以覆盖率臂落在
build/sim_cov/epv32sim_cov, 断言臂落在 build/sim_assert/, 三者互不覆写。

数据格式 (本件按 verilator 5.050 实际写出的形状解, 不是照文档猜)
----------------------------------------------------------------
每行 `C '<key>' <count>`, key 内字段以控制字符分隔: f<文件> l<行> n<节点>
t<类型> page<页> o<点位> h<层次>。**一个 bucket 是否被覆盖 = 它的 count > 0**。
所以本件不需要外部 verilator_coverage 工具, 也不会因它不在 PATH 而悄悄降级。

会咬的判据
----------
1) 分母守卫: bucket 总数为 0 ⇒ SETUP-FAIL (那是没收到数, 不是 0% 覆盖)。
2) 逐文件在场性: 参与建模的每个 rtl/*.v 都必须在 .dat 里出现 —— 一个模块被
   综合/仿真侧悄悄摘掉时, 「覆盖率」会在它身上给出 0 条而不报错。
3) 合并增益: 这一批程序合起来的 covered 数必须 **严格大于** 其中最好那一发;
   相等就说明多出来的程序没带任何新覆盖, 那份程序清单是装饰。
4) 每条程序必须真的产出 .dat: 缺一份即 SETUP-FAIL, 不许按 0 计入合并。
5) 归档自带来历: rtl 逐文件 sha256 + 覆盖率臂二进制的 sha256 + 程序清单 +
   bucket 总数, 让读者能判断这份读数属于哪一代。
"""
import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "flow"))

import rt                                              # noqa: E402

COV_DIR = os.path.join(ROOT, "build", "sim_cov")
COV_EXE = os.path.join(COV_DIR, "epv32sim_cov.exe")
OUT = os.path.join(ROOT, "results", "cov", "summary.json")
RTL_BUILT = ["epv32_soc.v", "epv32_core.v", "epv32_alu.v", "epv32_rf.v",
             "epv32_muldiv.v", "epv32_epv.v", "epv32_csr.v", "epv32_ram.v"]
# 与 flow/build_sim.sh 的文件列表必须一致; 不一致由 JUDGE 2 现量出来, 不靠人记。

REC = re.compile(r"^C\s+'(.*)'\s+(\d+)\s*$")

# 白名单由 .dat 里实际出现过的 t= 标定, 不许照文档抄
# (第一版漏列 branch, 每程序 372 条被静默丢, 由 PARSE-GAP 报出)。
TYPES = ("line", "branch", "toggle", "user")

# 夹具名单: 末行的分母由它现算, 不写死字面量。
FIXTURE_NAMES = ("four_known_types_parse", "unknown_type_counted_unmatched",
                 "type_set_is_complete", "same_bucket_takes_max",
                 "merge_takes_per_key_max")


def sha16(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def parse_dat(path):
    """-> {bucket: hits}. bucket = (文件, 类型, 行:节点 或 点位).

    实测形状 (verilator 5.050, 从它自己写出的 .dat 里读出来的, 不是照文档猜):
        "C '\\x01f\\x02rtl\\\\epv32_alu.v\\x01l\\x0210\\x01n\\x0222\\x01t\\x02toggle"
        "\\x01page\\x02v_toggle/epv32_alu\\x01o\\x02a[0]:0->1\\x01h\\x02TOP...\\x02' 681"
    即 **字段名以 \\x01 起、以 \\x02 结尾, 值跟在后面**。两版错路各栽过一次:
      - 按控制字符切完再「取 token 去掉首字母」⇒ 名字与值是两个 token, 于是
        首字母永远配不上值, 几万条全塌成一两个键, 报出「分母 1 / 覆盖率 100%」;
      - 按 `t(line|toggle)` 这种相邻正则解 ⇒ \\x01/\\x02 插在中间, 一条都解不出,
        报「0 buckets」。
    所以这里把 key 按 [\\x01\\x02] 切成名/值交替的序列再成对取, 并统计解不出的
    行数: 未匹配 != 0 就是本件的解析面漏了, 不许把解出来的那部分当全量。
    """
    out = {}
    unmatched = 0
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("#") or not line.strip():
                continue
            m = REC.match(line)
            if not m:
                unmatched += 1
                continue
            key, hits = m.group(1), int(m.group(2))
            toks = [t for t in re.split(r"[\x01\x02]", key) if t != ""]
            fields = {}
            for i in range(0, len(toks) - 1, 2):
                fields.setdefault(toks[i], toks[i + 1])
            typ = fields.get("t", "")
            if typ not in TYPES:
                unmatched += 1
                continue
            fn = fields.get("f", "?")
            if typ == "toggle":
                b = (fn, typ, fields.get("o", "?"))
            else:
                b = (fn, typ, "%s:%s" % (fields.get("l", "?"), fields.get("n", "?")))
            out[b] = max(out.get(b, 0), hits)
    parse_dat.unmatched = getattr(parse_dat, "unmatched", 0) + unmatched
    return out


def program_list():
    d = os.path.join(ROOT, "build", "elf")
    stems = {}
    for dp, _dn, fns in os.walk(d):
        for fn in fns:
            if fn.endswith((".bin", ".elf")):
                stems.setdefault(os.path.basename(fn)[:-4], {})[fn[-4:]] = \
                    os.path.join(dp, fn)
    pairs = sorted((s, v[".elf"], v[".bin"]) for s, v in stems.items()
                   if ".elf" in v and ".bin" in v)
    want = [p for p in pairs if p[0] in (
        "epv_selftest", "epv_bisect", "micro_bisect", "misalign_bisect",
        "nn_bench", "nn_net", "nn_cifar")]
    suites = [p for p in pairs if p[0].startswith(("rv32ui__", "rv32um__", "rv32mi__"))]
    return (want + suites) or pairs[:8]


def run_cov(binf, elf, dat, scratch):
    syms = rt.symbols(elf)
    env = dict(os.environ)
    env.update({"EPV_IMAGE": binf, "EPV_BASE": "0x80000000",
                "EPV_MAXCYC": "3000000",
                "EPV_RESULT": os.path.join(scratch, "r_%s.json" %
                                           os.path.basename(binf)),
                "EPV_TRACE": "", "EPV_TOHOST": str(syms.get("tohost", 0)),
                "EPV_COV": dat})
    p = subprocess.run([COV_EXE], env=env, capture_output=True, timeout=1800)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def merge(per_prog):
    tot = {}
    items = per_prog.values() if hasattr(per_prog, "values") else per_prog
    for d in items:
        for b, h in d.items():
            tot[b] = max(tot.get(b, 0), h)
    return tot


def stats(buckets):
    """按 (类型) 与 (文件, 类型) 两档现数 covered/total。"""
    by_type = {}
    by_file = {}
    for (fn, typ, _loc), h in buckets.items():
        base = os.path.basename(fn.replace("\\", "/"))
        for d, k in ((by_type, (typ,)), (by_file, (base, typ))):
            slot = d.setdefault(k, [0, 0])
            slot[1] += 1
            if h > 0:
                slot[0] += 1
    return by_type, by_file


def selftest():
    """夹具: 证明解析面认得全部四种记录, 且见到第五种会报而不是静默丢。"""
    import tempfile
    def rec(fields, hits):
        body = "".join("\x01%s\x02%s" % (k, v) for k, v in fields.items())
        return "C '%s' %d\n" % (body, hits)

    errs = []
    d = tempfile.mkdtemp(prefix="epvcovst_")
    p1 = os.path.join(d, "a.dat")
    with io.open(p1, "w", encoding="utf-8", newline="\n") as f:
        f.write("# SystemC::Coverage-3\n")
        for typ, extra in (("line", {"l": "10", "n": "3"}),
                           ("branch", {"l": "11", "n": "4"}),
                           ("toggle", {"l": "12", "n": "5", "o": "a[0]:0->1"}),
                           ("user", {"l": "13", "n": "6"})):
            fl = {"f": "rtl\\epv32_core.v", "t": typ, "page": "v_%s/x" % typ}
            fl.update(extra)
            f.write(rec(fl, 7))
        f.write(rec({"f": "rtl\\epv32_core.v", "t": "mystery_type_zz"}, 1))
    parse_dat.unmatched = 0
    b = parse_dat(p1)
    if len(b) != 4:
        errs.append("F1四种已知类型应解出 4 个 bucket, 实得 %d" % len(b))
    if getattr(parse_dat, "unmatched", 0) != 1:
        errs.append("F2未知类型应计 1 条 unmatched, 实得 %s"
                    % getattr(parse_dat, "unmatched", 0))
    kinds = set(k[1] for k in b)
    if kinds != {"line", "branch", "toggle", "user"}:
        errs.append("F3类型集合不全: %s" % sorted(kinds))
    # 命中数取 max 而不是 last: 同一条 bucket 出现两次, 大的那个才算数
    p2 = os.path.join(d, "b.dat")
    with io.open(p2, "w", encoding="utf-8", newline="\n") as f:
        f.write(rec({"f": "x.v", "t": "line", "l": "1", "n": "1"}, 0))
        f.write(rec({"f": "x.v", "t": "line", "l": "1", "n": "1"}, 5))
    parse_dat.unmatched = 0
    b2 = parse_dat(p2)
    if b2.get(("x.v", "line", "1:1")) != 5:
        errs.append("F4同 bucket 两次记录应取 max, 实得 %s" % b2)
    tot = merge([{"a": 1}, {"a": 0, "b": 3}])
    if tot != {"a": 1, "b": 3}:
        errs.append("F5 merge 应取逐键 max, 实得 %s" % tot)
    for i, e in enumerate(errs, 1):
        sys.stdout.write("FAIL fixture%d %s\n" % (i, e))
    n = len(FIXTURE_NAMES)
    ok = n - len(errs)
    sys.stdout.write("COVERAGE_SELFTEST OK %d/%d\n" % (max(ok, 0), n))
    return 0 if ok == n else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="先重编覆盖率臂")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()

    if args.build or not os.path.isfile(COV_EXE):
        env = dict(os.environ)
        env["COV"] = "1"
        rc = subprocess.call(["bash", os.path.join(ROOT, "flow", "build_sim.sh")],
                             cwd=ROOT, env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        if rc != 0 or not os.path.isfile(COV_EXE):
            print("SETUP-FAIL cov build rc=%d exe=%s" % (rc, os.path.isfile(COV_EXE)))
            return 2

    scratch = tempfile.mkdtemp(prefix="epvcov_")
    progs = program_list()
    per_prog, missing = {}, []
    t0 = time.time()
    for stem, elf, b in progs:
        dat = os.path.join(scratch, "cov_%s.dat" % stem)
        try:
            run_cov(b, elf, dat, scratch)
        except subprocess.TimeoutExpired:
            missing.append((stem, "TIMEOUT"))
            continue
        if not os.path.isfile(dat):
            missing.append((stem, "NO_DAT"))
            continue
        per_prog[stem] = parse_dat(dat)
    if missing:
        print("SETUP-FAIL %d programs produced no coverage data: %s" % (
            len(missing), missing[:6]))
        return 2
    if not per_prog:
        print("SETUP-FAIL no program ran -- 分母塌了")
        return 2

    tot = merge(per_prog)
    if not tot:
        print("SETUP-FAIL 0 coverage buckets parsed -- 解析面塌了, 不是 0%% 覆盖")
        return 2
    by_type, by_file = stats(tot)
    single_best = max(sum(1 for h in d.values() if h > 0) for d in per_prog.values())
    merged_cov = sum(1 for h in tot.values() if h > 0)
    present = set(os.path.basename(f.replace("\\", "/")) for f, _t, _l in tot)
    absent = [m for m in RTL_BUILT if m not in present]

    per_type = {}
    for typ in TYPES:
        c, t = by_type.get((typ,), [0, 0])
        per_type[typ] = {"total": t, "covered": c,
                         "pct": round(100.0 * c / t, 2) if t else None}
    covered_line = by_type.get(("line",), [0, 0])
    rec = {
        "what": "覆盖率臂 (build/sim_cov) 对本批程序的 line/toggle 合并读数",
        "how_to_read": "bucket = (文件, 类型, 行或点位); 覆盖 = 该 bucket 的 hit 计数 > 0; "
                       "百分比由本件现算, 不由任何文档拄",
        "coverage_arm_binary_sha256_head": sha16(COV_EXE),
        "rtl_sha256": dict((k, v[:16]) for k, v in rt.sim_provenance()["rtl_sha256"].items()),
        "programs": sorted(per_prog),
        "buckets_total": len(tot),
        "buckets_covered": merged_cov,
        "by_type": per_type,
        "line": per_type["line"],
        "toggle": per_type["toggle"],
        "per_file": dict(("%s/%s" % k, {"buckets": v[1], "covered": v[0],
                                        "pct": round(100.0 * v[0] / v[1], 2) if v[1] else None})
                         for k, v in sorted(by_file.items())),
        "parser_unmatched_records": getattr(parse_dat, "unmatched", 0),
        "single_program_best_covered": single_best,
        "merge_gain": merged_cov - single_best,
        "absent_modules": absent,
        "wall_s": round(time.time() - t0, 1),
        "not": ["不是认证臂的读数: 覆盖率臂另建一份二进制, 它的周期数与面积都不进交付结论",
                "没有 functional coverage: 本包没有 covergroup, 这一格仍是空的"],
    }
    problems = []
    if absent:
        problems.append("ABSENT-MODULE %s 在 .dat 里一条 bucket 都没有" % absent)
    if rec["merge_gain"] <= 0:
        problems.append("NO-MERGE-GAIN 合并后 covered 不比最好那一发多 (gain=%d) "
                        "=> 这批程序里有装饰" % rec["merge_gain"])
    if not covered_line[1]:
        problems.append("NO-LINE-BUCKETS line 类型的 bucket 为 0 => --coverage-line 没生效")
    if rec["parser_unmatched_records"]:
        # 解析面漏了就不许把「解出来的那部分」当全量覆盖率报出去。
        problems.append("PARSE-GAP %d 条记录没被解析" % rec["parser_unmatched_records"])

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    sys.path.insert(0, os.path.join(ROOT, "flow"))
    from atomic_json import write_json_atomic
    write_json_atomic(OUT, rec)

    for typ in TYPES:
        v = per_type[typ]
        print("%-7s %s/%s = %s%%" % (typ, v["covered"], v["total"], v["pct"]))
    print("merge_gain=%d  programs=%d  buckets=%d" % (
        rec["merge_gain"], len(per_prog), len(tot)))
    if problems:
        for p in problems:
            print("  - %s" % p)
        print("COVERAGE_FAILED problems=%d" % len(problems))
        return 1
    print("COVERAGE_OK programs=%d buckets=%d covered=%d line_pct=%s branch_pct=%s "
          "toggle_pct=%s absent_modules=0 unmatched=0" % (
              len(per_prog), len(tot), merged_cov, per_type["line"]["pct"],
              per_type["branch"]["pct"], per_type["toggle"]["pct"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
