#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flow/iss_compare.py -- 黄金 ISS 与 RTL 的逐条退休轨迹比对

把验证从「用例最后写了 tohost=1」升级成「每一条退休指令的 (pc, rd, wdata)
两边逐位相同」。前者只证明程序跑完了, 后者才证明**每一步状态转移**一致:
一个把分支算反、又把正确值写进 rd 的核, 也能过前者。

做法:
  1. 同一个源文件编出的同一份 flat image, 同时喂给 Verilator 与 ISS;
  2. RTL 侧写 results/trace/<name>_isco.txt (R 行 = 寄存器写回退休),
     ISS 侧用 step() 投影出同样的 (pc, rd, wdata) 序列 —— 两边都是
     「rd!=0 且真的写回」这一条投影, 不各按各的口径;
  3. 逐条比, 第一处分歧就把两边各自的前后几行一起印出来。

RTL 运行一律带 --tag/_isco 后缀: 认证归档 results/sim/<name>.json 不许被
比对用的复跑写过 (这条纪律是 tools/check_archive_clobber.py 抓出来的)。

--selftest 用替身轨迹证明分歧会咬, 并拿一份真实归档做正对照。
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "flow"))
sys.path.insert(0, os.path.join(ROOT, "tb", "golden"))
import rt                                                        # noqa: E402
import epv_iss                                                   # noqa: E402
from atomic_json import write_json_atomic                         # noqa: E402

BASE = 0x80000000
RLINE = re.compile(r"^R\s+([0-9a-fA-F]{8})\s+(\d+)\s+([0-9a-fA-F]{8})\s*$")


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def rtl_trace(path):
    out = []
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for ln in f:
            m = RLINE.match(ln.strip())
            if m:
                out.append((int(m.group(1), 16), int(m.group(2)),
                            int(m.group(3), 16)))
    return out


def iss_trace(binf, tohost, max_steps=2_000_000):
    s = io.open(binf, "rb").read()
    iss = epv_iss.ISS(enable_epv=True)
    iss.reset(BASE)
    iss.load_image(BASE, s)
    tr = []
    steps = 0
    reason = "max_steps"
    while steps < max_steps:
        steps += 1
        try:
            sr = iss.step()
        except epv_iss.SimHalt as h:
            reason = "halt:%d" % (h.value if h.value is not None else -1)
            break
        if sr.retired and sr.rd is not None:
            tr.append((sr.pc & 0xFFFFFFFF, sr.rd, sr.wdata & 0xFFFFFFFF))
        if tohost and (iss._ram_peek(tohost) or 0) != 0:
            reason = "tohost"
            break
    return tr, {"steps": steps, "reason": reason, "image_bytes": len(s)}


def first_divergence(a, b):
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    if len(a) != len(b):
        return n
    return None


def fmt(tr, lo, hi):
    return "\n".join("      %6d  pc=%08x x%d=%08x"
                     % (i, tr[i][0], tr[i][1], tr[i][2]) for i in range(lo, hi))


def compare_one(src, name, max_steps=400_000):
    """返回这一条用例的比对结论(dict)。name 形如 rv32ui__add。"""
    elf, binf, _ = rt.build_elf(name, [src], link_env_ld=True)
    syms = rt.symbols(elf)
    r = rt.run_sim(name, binf, syms, max_cyc=300_000, aname=name + "_isco")
    tp = os.path.join(ROOT, "results", "trace", name + "_isco.txt")
    a = rtl_trace(tp)
    b, info = iss_trace(binf, syms.get("tohost", 0), max_steps=max_steps)
    d = first_divergence(a, b)
    row = {"test": name, "rtl_commits": len(a), "iss_commits": len(b),
           "rtl_verdict": r.get("verdict"), "iss_stop": info["reason"],
           "agree": d is None}
    if d is not None:
        lo = max(0, d - 2)
        row["first_divergence_index"] = d
        row["context"] = ("RTL:\n%s\nISS:\n%s"
                          % (fmt(a, lo, min(len(a), d + 3)),
                             fmt(b, lo, min(len(b), d + 3))))
    return row


def run_suite(suite, only=None, max_steps=400_000):
    d = os.path.join(rt.REFS, "isa", suite)
    if not os.path.isdir(d):
        raise SystemExit("NO_SUCH_SUITE_DIR " + d)
    tests = sorted(x[:-2] for x in os.listdir(d) if x.endswith(".S"))
    if only:
        tests = [t for t in tests if t in only]
    rows = []
    t0 = time.time()
    for t in tests:
        row = compare_one(os.path.join(d, t + ".S"), "%s__%s" % (suite, t),
                          max_steps=max_steps)
        rows.append(row)
        print("%-6s %-22s rtl=%-5d iss=%-5d %s"
              % (suite, t, row["rtl_commits"], row["iss_commits"],
                 "AGREE" if row["agree"] else "DIVERGE@"
                 + str(row.get("first_divergence_index"))))
    ok = sum(1 for r in rows if r["agree"])
    prov = rt.sim_provenance()
    out = {"suite": suite, "n_tests": len(rows), "n_agree": ok,
           "all_agree": ok == len(rows) and len(rows) > 0,
           "wall_s": round(time.time() - t0, 1), "rows": rows,
           "projection": "R-line == retired && rd!=0 && reg-write, both sides",
           # 这个判据的两侧各由一份源码决定: 核(经 Verilator 仿真器)与黄金模型。
           # 只记一边的话, "42/42 一致" 可以在模型被改坏的情况下仍然全绿。
           "sources": {
               "rtl_sha256": prov["rtl_sha256"],
               "simulator_sha256": prov["sim_sha256"],
               "golden_iss_sha256": _sha(os.path.join(ROOT, "tb", "golden",
                                                      "epv_iss.py")),
               "comparator_sha256": _sha(os.path.abspath(__file__)),
               "how_to_read": "门禁只认 rtl_sha256 == 当前工作树 的那一份归档"}}
    os.makedirs(os.path.join(ROOT, "results", "isco"), exist_ok=True)
    write_json_atomic(os.path.join(ROOT, "results", "isco", suite + ".json"), out)
    print("ISCO %s: %d/%d 轨迹逐条一致  all_agree=%s  -> results/isco/%s.json"
          % (suite, ok, len(rows), out["all_agree"], suite))
    return 0 if out["all_agree"] else 1


def selftest():
    """分歧必须咬; 正对照用一份真实归档。"""
    real = os.path.join(ROOT, "results", "trace", "rv32ui__add_isco.txt")
    if not os.path.isfile(real):
        print("先跑一次 python flow/iss_compare.py rv32ui --one add 生成基准轨迹")
        return 1
    a = rtl_trace(real)
    if len(a) < 20:
        print("SELFTEST_FAILED: 基准轨迹太短(%d 条)" % len(a))
        return 1
    b = list(a)
    b[7] = (b[7][0], b[7][1], (b[7][2] ^ 0x1) & 0xFFFFFFFF)
    d = first_divergence(a, b)
    if d != 7:
        print("SELFTEST_FAILED: 改一位 wdata 却在 %s 处才分歧" % d)
        return 1
    print("[selftest] wdata 扰动 -> 分歧 index=%d" % d)
    d2 = first_divergence(a, a[:-1])
    if d2 != len(a) - 1:
        print("SELFTEST_FAILED: 少一条退休却在 %s 处才分歧" % d2)
        return 1
    print("[selftest] 少一条退休 -> 分歧 index=%d (=%d)" % (d2, len(a) - 1))
    if first_divergence(a, list(a)) is not None:
        print("SELFTEST_FAILED: 完全相同的两条轨迹报了分歧")
        return 1
    print("[selftest] 正对照: 同一份轨迹两边一致")
    print("ISCO_SELFTEST_OK: 3 条扰动各自定位到该定位的下标")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("suite", nargs="?", help="rv32ui | rv32um")
    ap.add_argument("--one", action="append", default=None)
    ap.add_argument("--max-steps", type=int, default=400_000)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.suite:
        ap.print_help()
        return 1
    return run_suite(a.suite, a.one, a.max_steps)


if __name__ == "__main__":
    sys.exit(main())
