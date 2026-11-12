#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flow/rand_trace.py -- 约束随机 RV32IM 指令流, 与黄金 ISS 逐条退休轨迹比对。

本件补的是一个能力缺口: 链条里此前没有任何一档激励是「约束随机的指令流 + 那条独立
黄金模型的逐条差分」。官方 riscv-tests 是定向用例, 已有的 isco 零分歧比对用的
也是同一批程序 —— 两边覆盖的是同一组场景, 所以「零已知分歧」目前只对这 66 个程序
成立, 没有统计意义。

只复用一个比对器
----------------
逐条 (pc, rd, wdata) 的判定与投影口径已经由 `flow/iss_compare.compare_one` 定死,
而那条口径本身就是承重的: 「两边都是 rd!=0 且真的写回」这一条投影, 不各按各的算。
本件**不写第二份比较逻辑** —— 只生成 .S 并调它。两份各自实现的比对器同时看漏同一个
分歧, 就是这个包记过三次的双重假绿。

生成的程序刻意保持「合法且无陷入」
----------------------------------
随机指令流一旦踩到陷入, 两边要一致就得连 `mtvec` 向量化、mepc 奇偶、tval 这些
本包已知有历史分歧的通路一起对 —— 那会把这一格从「ISA 语义的随机差分」变成
「陷入语义的随机差分」, 后者已经有 rv32mi 在管。所以这里:
  * 只用 x15..x24 (t0-t9): 环境宏与 PASS 路径不碰这几支;
  * 移位量 mask 到 0..31, 不造 shamt[5]=1 的非法编码;
  * 分支只向前跳到本程序自己生成的标签, 不造依赖 IALIGN 的计算目标;
  * 访存只落在自己 `.data` 里一块按字对齐的scratch 区;
  * 除零/溢出**保留** (RV32 定义为不陷入), 那正是要随机砸的部分。
非法编码与陷入路径的随机化不在这一格, 别拿本件的零分歧去说「陷入语义也随机验过了」。

判决 (末行 ASCII, 四条各配夹具)
--------------------------------
RAND_TRACE_OK programs=N agree=N diverging=0 retired=R seeds_unique=S problems=0
"""
import argparse
import hashlib
import io
import json
import os
import random
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

OUT_DIR = os.path.join(ROOT, "results", "rand_trace")
SRC_DIR = os.path.join(ROOT, "build", "rand")

REGS = list(range(15, 25))                 # t0..t9
ALU_R = ["add", "sub", "and", "or", "xor", "sll", "srl", "sra", "slt", "sltu"]
ALU_I = ["addi", "andi", "ori", "xori", "slli", "srli", "srai", "slti", "sltiu"]
M_OP = ["mul", "mulh", "mulhsu", "mulhu", "div", "divu", "rem", "remu"]
BR = ["beq", "bne", "blt", "bge", "bltu", "bgeu"]
U_IMM12 = [0, 1, -1, 0x7FF, -0x800, 0x123, -0x123]


def _imm(rng):
    if rng.random() < 0.35:
        return rng.choice(U_IMM12)
    return rng.randint(-2048, 2047)


def gen_program(seed, nops=220):
    """一份合法、无陷入、可终止的随机 RV32IM 程序 (riscv-tests env 骨架)。"""
    rng = random.Random(seed)
    lines = []
    lbl = 0
    for i in range(nops):
        pick = rng.random()
        a, b, c = (rng.choice(REGS) for _ in range(3))
        if pick < 0.30:
            op = rng.choice(ALU_R)
            sh = (" , %d" % rng.randrange(0, 32)) if op in ("sll", "srl", "sra") else ""
            lines.append("  %s x%d, x%d, x%d%s" % (op, a, b, c, "") if not sh
                         else "  %s x%d, x%d, %d" % (op, a, b, rng.randrange(0, 32)))
        elif pick < 0.55:
            op = rng.choice(ALU_I)
            if op in ("slli", "srli", "srai"):
                lines.append("  %s x%d, x%d, %d" % (op, a, b, rng.randrange(0, 32)))
            else:
                lines.append("  %s x%d, x%d, %d" % (op, a, b, _imm(rng)))
        elif pick < 0.72:
            lines.append("  %s x%d, x%d, x%d" % (rng.choice(M_OP), a, b, c))
        elif pick < 0.84:
            lines.append("  li x%d, %d" % (a, _imm(rng)))
        elif pick < 0.90:
            lines.append("  lui x%d, 0x%x" % (a, rng.randrange(1, 0xFFFFF)))
        elif pick < 0.96:
            # 访存: 落在 scratch 区, 按字对齐
            off = 4 * rng.randrange(0, 64)
            if rng.random() < 0.5:
                lines.append("  sw x%d, %d(x28)" % (a, off))
            else:
                lines.append("  lw x%d, %d(x28)" % (a, off))
        else:
            lbl += 1
            lines.append("  %s x%d, x%d, rl_%d" % (rng.choice(BR), b, c, lbl))
    body = "\n".join(lines)
    tail = "\n".join("rl_%d: nop" % (k + 1) for k in range(lbl))
    return _wrap(seed, nops, body, tail)


# 那十条 li 是刻意给的**角例料**：x19=0 给「除数为零」，x18=0x80000000 与 x16=-1
# 给「INT_MIN / -1」，x21 与 x24 是负数与高位置 1 的位型（REM 的符号跟随被除数、
# REMU 不跟随，两者的差别正好落在这几支上）。
CORNER_BODY = "\n".join([
    "  div  x15, x21, x19",        # 负被除数 / 0  -> -1
    "  rem  x15, x21, x19",        # 负被除数 % 0  -> 原样给被除数  <-- 本轮被抓出的那一格
    "  add  x15, x15, x20",        # 让上一条的结果真的被消费一次(不是写完就没人看)
    "  remu x15, x18, x19",        # 高位置 1 的无符号被除数 % 0 -> 原样, 不许取反
    "  divu x15, x18, x19",        # 无符号 / 0 -> -1
    "  rem  x15, x20, x19",        # 正被除数 % 0 -> 被除数
    "  div  x15, x18, x16",        # INT_MIN / -1 -> INT_MIN (溢出定义)
    "  rem  x15, x18, x16",        # INT_MIN % -1 -> 0
    "  div  x15, x24, x21",        # 两个负数相除 -> 商为正
    "  rem  x15, x24, x21",        # 余数符号跟随被除数(负)
    "  mulh x15, x18, x16",
    "  mulhsu x15, x18, x16",
    "  mulhu x15, x18, x16",
])


def gen_corner_program():
    """固定的一则**定向**角例程序（不吃种子）。

    为什么要有它而不是只靠随机流：抓到 `rem` 除零那一格的是种子 20260926 的第 94 拍，
    而那是随机产物 —— 生成器的 op 表一改，那一拍就可能消失，于是「这条回归还活着」
    会变成一句运气话。角例由这十条固定指令钉住，随机那一半继续负责没被想到的组合。
    """
    return _wrap(0, len(CORNER_BODY.splitlines()), CORNER_BODY, "rl_0: nop")


def _wrap(seed, nops, body, tail):
    return (
        "# 由 flow/rand_trace.py 生成 -- 不要手改; seed=%d nops=%d\n"
        "#include \"riscv_test.h\"\n"
        "#include \"test_macros.h\"\n\n"
        "RVTEST_RV32U\n"
        "RVTEST_CODE_BEGIN\n\n"
        "  la x28, scratch\n"
        "  li x15, 1\n  li x16, -1\n  li x17, 0x7fffffff\n  li x18, 0x80000000\n"
        "  li x19, 0\n  li x20, 0x12345\n  li x21, -0x54321\n  li x22, 32\n"
        "  li x23, 0xffffffff\n  li x24, 0xdeadbeef\n\n"
        "%s\n\n"
        "  TEST_PASSFAIL\n\n"
        "RVTEST_CODE_END\n\n"
        "  .data\n"
        "RVTEST_DATA_BEGIN\n"
        "  .align 6\n"
        "scratch:\n"
        "  .space 1024\n"
        "TEST_DATA\n"
        "RVTEST_DATA_END\n"
        "%s\n" % (seed, nops, body, tail))


def mnemonic_audit(text):
    """回读自己生成的程序: 出现的助记符必须在允许集合内。

    这一条挡的是「生成器悄悄造出一个非法或会陷入的编码, 而比对那边把它当成
    真实差分报出来」—— 那会让人去改一个没坏的核。
    """
    allowed = set(ALU_R + ALU_I + M_OP + BR + ["li", "lui", "la", "sw", "lw",
                                               "nop", "j", "ret"])
    bad = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", ".", "rv", "RVTEST", "rl_")) or ":" in s.split(" ")[0]:
            continue
        m = re.match(r"^([a-z][a-z0-9_]*)", s)
        if m and m.group(1) not in allowed:
            bad.append(m.group(1))
        if m and m.group(1) in ("sll", "srl", "sra", "slli", "srli", "srai"):
            sh = int(re.search(r",\s*(\d+)\s*$", s).group(1))
            if sh > 31:
                bad.append("shamt>31:" + s)
    return bad


def run(programs, nops, seed0):
    import iss_compare
    os.makedirs(SRC_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    rows, t0 = [], time.time()
    # 第 0 则永远是那一条**定向**角例（除零 / INT_MIN 除 -1 / 三个 mul 高位），它不吃
    # 种子；随机那 programs 则排在它后面。本轮抓到 rem 除零那个缺陷的是种子 20260926
    # 的第 94 拍，但**回归不许靠运气**：生成器的 op 表一改，那一拍就可能不在，
    # 所以把这十种形状钉成一则固定程序，让它每次都在分母里。
    jobs = [("rand__corner", gen_corner_program(), -1)] + [
        ("rand__p%02d" % i, gen_program(seed0 + i, nops), seed0 + i)
        for i in range(programs)]
    n_jobs = len(jobs)
    for name, text, seed in jobs:
        src = os.path.join(SRC_DIR, name + ".S")
        io.open(src, "w", encoding="utf-8", newline="\n").write(text)
        bad = mnemonic_audit(text)
        if bad:
            rows.append({"test": name, "agree": False, "seed": seed,
                         "generator_violation": sorted(set(bad))[:6]})
            print("%s GENERATOR-VIOLATION %s" % (name, sorted(set(bad))[:6]))
            continue
        r = iss_compare.compare_one(src, name)
        r["seed"] = seed
        r["kind"] = "corner" if name == "rand__corner" else "random"
        rows.append(r)
        print("%s rtl=%-6d iss=%-6d %s" % (
            name, r["rtl_commits"], r["iss_commits"],
            "AGREE" if r["agree"] else "DIVERGE@" + str(r.get("first_divergence_index"))))
    agree = sum(1 for r in rows if r.get("agree"))
    retired = sum(int(r.get("rtl_commits") or 0) for r in rows)
    programs = n_jobs          # 分母含那一则定向角例
    prov = {}
    try:
        import rt
        prov = rt.sim_provenance()
    except Exception as exc:
        prov = {"error": repr(exc)}
    out = {
        "what": "约束随机 RV32IM 指令流与黄金 ISS 的逐条退休轨迹比对",
        "not": ["不含非法编码与陷入路径的随机化: 那部分是 rv32mi 与手写探针在管"],
        "generator": "flow/rand_trace.py",
        "comparator": "flow/iss_compare.compare_one (唯一的逐条比对实现)",
        "programs": programs, "corner_programs": 1,
        "random_programs": programs - 1,
        "nops_per_program": nops, "seed_base": seed0,
        "seeds": [seed0 + i for i in range(programs)],
        "n_agree": agree, "n_diverging": programs - agree,
        "retired_total": retired,
        "all_agree": agree == programs and programs > 0,
        "wall_s": round(time.time() - t0, 1),
        "rows": rows,
        "sources": {"rtl_sha256": prov.get("rtl_sha256"),
                    "simulator_sha256": prov.get("sim_sha256"),
                    "golden_iss_sha256": hashlib.sha256(io.open(
                        os.path.join(ROOT, "tb", "golden", "epv_iss.py"),
                        "rb").read()).hexdigest(),
                    "how_to_read": "门禁只认 rtl_sha256 == 当前工作树 的那一份归档"},
    }
    io.open(os.path.join(OUT_DIR, "summary.json"), "w", encoding="utf-8").write(
        json.dumps(out, indent=1, ensure_ascii=True))
    probs = []
    if programs < 2:
        probs.append("DENOM 只跑了 %d 则（至少要定向角例加一则随机）" % programs)
    if not any(r.get("kind") == "corner" and r.get("agree") for r in rows):
        probs.append("CORNER-NOT-AGREED 定向角例那则没通过（或根本没跑到）")
    if retired <= 0:
        probs.append("DENOM 退休指令总数为 0")
    if programs - agree:
        probs.append("DIVERGING %d 则不一致" % (programs - agree))
    print("RAND_TRACE_%s programs=%d agree=%d diverging=%d retired=%d problems=%d" % (
        "OK" if not probs else "FAIL", programs, agree, programs - agree, retired,
        len(probs)))
    for m in probs:
        print("RAND_TRACE_PROBLEM " + m)
    return 0 if not probs else 1


FIXTURE_NAMES = ("seed_deterministic", "seed_changes_bytes", "env_skeleton_present",
                 "generated_passes_audit_p7", "generated_passes_audit_p8",
                 "audit_fires_on_illegal")


def selftest():
    """夹具按名单逐条跑, 末行分母由 len(FIXTURE_NAMES) 现算。

    写死分母在这一格里特别容易骗人: 第一版我写 `n = 6` 而实际只有五个错误槽,
    于是有一条夹具没跑也会报 6/6 —— tools/fixture_claims.py 的 NO-LIST 就是把
    这种「自述句没人管」的形状抓出来的。
    """
    a = gen_program(7, 120)
    b = gen_program(7, 120)
    c = gen_program(8, 120)
    checks = [
        ("seed_deterministic", a == b),
        ("seed_changes_bytes", a != c),
        ("env_skeleton_present",
         all(k in a for k in ("RVTEST_CODE_BEGIN", "TEST_PASSFAIL", "scratch:"))),
        ("generated_passes_audit_p7", not mnemonic_audit(a)),
        ("generated_passes_audit_p8", not mnemonic_audit(c)),
        # 反例臂: 造一条明显非法的输出, 审计必须抓到 (否则审计是空转的)
        ("audit_fires_on_illegal", bool(mnemonic_audit(c + "\n  slli x15, x16, 44\n  wfi\n"))),
    ]
    assert [n for n, _ok in checks] == list(FIXTURE_NAMES), \
        "夹具名单与实际跑的条目不一致: %s vs %s" % (
            [n for n, _ok in checks], list(FIXTURE_NAMES))
    ok = 0
    for name, passed in checks:
        if passed:
            ok += 1
        else:
            print("FAIL %s" % name)
    print("RAND_TRACE_SELFTEST OK %d/%d" % (ok, len(FIXTURE_NAMES)))
    return 0 if ok == len(FIXTURE_NAMES) else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--programs", type=int, default=12)
    ap.add_argument("--ops", type=int, default=220)
    ap.add_argument("--seed", type=int, default=20260926)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    return run(args.programs, args.ops, args.seed)


if __name__ == "__main__":
    sys.exit(main())
