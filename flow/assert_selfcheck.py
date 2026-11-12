#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""assert_selfcheck.py -- 把 rtl/epv32_assert_*.vh 从「写了」变成「证明会咬」。

为什么单独要这一支
------------------
一条从来不开火的断言, 和一条不存在的断言, 在报告里长得一模一样。所以本件做两件事:
  (1) 正向臂: 用带 `-DEFV_ASSERTIONS` 的那一臂仿真器跑一批真程序, 要求
      a) 两条 ARMED heartbeat 都在 (证明这段代码真进了方程, 而不是被 ifdef 挡在
         外面而构建照样成功);
      b) `EPVASSERT FAIL` 行数为 0 (合法设计上恒不开火)。
  (2) 注入臂: 逐条把源码改坏一处, 重新编译到 build/sim_mut, 跑一发程序,
      要求被点名的那个 tag 真的打印出来; 每次注入后按 sha256 还原并回读证明。

分流: 注入臂只写 build/sim_mut 与临时目录, **绝不写 results/ 下的认证归档**
      (run_sim 会写 results/sim/<name>.json, 所以这里不调它, 只复用它的 env 配方)。

用法
----
  python flow/assert_selfcheck.py                 # 正向臂 + 注入臂
  python flow/assert_selfcheck.py --no-mutate     # 只跑正向臂 (快)
  python flow/assert_selfcheck.py --selftest      # 本件自己的夹具 (见下)

自己的夹具证明本件不空转: 把一个 tag 从源码里抹掉 ⇒ 它必须从「已证明」集合里消失;
把某条注入的期望 tag 换成一个不存在的名字 ⇒ 该次注入必须判红。

末行判决全 ASCII。
"""
import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import rt  # 唯一的编译/仿真驱动, 不另写第二份      # noqa: E402

ASSERT_FILES = ["rtl/epv32_assert_core.vh", "rtl/epv32_assert_epv.vh"]
# 两种写法都要能现读出 tag: EPV 那份是内联 $display("EPVASSERT FAIL E1_... t=%0t"),
# 核那份走 `EPV_ASSERT_FAIL("A1_...") 宏。只认前一种会让核侧那一批从清单里凭空
# 消失 —— 于是「tag 全集」这个分母自己就是错的, 而它管着守恒式。
TAG_RE = re.compile(r'"EPVASSERT FAIL\s+([A-Z0-9_]+)\s+t=%0t"')
TAG_MACRO_RE = re.compile(r'`EPV_ASSERT_FAIL\("([A-Z0-9_]+)"\)')

MUT = os.path.join(ROOT, "build", "sim_mut")

# ------------------------------------------------------------------ 注入表
# 每条 = (被点名的 tag, 要改的源文件, 原样片段, 改成的片段)
# 原样片段必须在文件里唯一命中一次, 否则本件判 SETUP-FAIL 而不是「跳过」。
MUTATIONS = [
    # 注入方向由判据那条「不等式」现推, 不许凭直觉。两条纠正过的方法:
    #   - 造 A1 用 `trap_fire = ... || branch_take` 是组合环 (branch_take 带 !trap_fire),
    #     Verilator 振铃后一条都不打印, 症状像「这条断言是死的」。
    #   - 造 A6 用 `flush_idex = hazard` 方向相反: 那只会让 flush 更少为真,
    #     而 A6 判的是 flush 那一拍后 idex_valid 必须为 0。
    ("A1_TRAP_SRC_NOT_UNIQUE", "rtl/epv32_core.v",
     "assign jump_take   = idex_valid && idex_is_jmp && !trap_fire && !ex_stall;",
     "assign jump_take   = idex_valid && idex_is_jmp && !ex_stall;"),
    ("A2_TRAP_DURING_EXSTALL", "rtl/epv32_core.v",
     "assign trap_fire= ex_exc || irq_now;",
     "assign trap_fire= ex_exc || irq_now || (idex_is_md && md_busy);"),
    ("A3_REDIRECT_DURING_EXSTALL", "rtl/epv32_core.v",
     "assign branch_take = idex_valid && idex_is_br  && cond_take && !trap_fire && !ex_stall;",
     "assign branch_take = idex_valid && idex_is_br  && cond_take && !trap_fire;"),
    ("A4_PC_MOVED_DURING_EXSTALL", "rtl/epv32_core.v",
     "assign pc_stall = hazard || ex_stall;",
     "assign pc_stall = hazard;"),
    ("A5_IFID_CHANGED_DURING_HOLD", "rtl/epv32_core.v",
     "    end else if (!hold_ifid) begin",
     "    end else begin"),
    ("A6_IDEX_NOT_FLUSHED", "rtl/epv32_core.v",
     "idex_valid    <= 1'b0;",
     "idex_valid    <= 1'b1;"),
    ("A7_DMEM_UNALIGNED_PORT", "rtl/epv32_core.v",
     "assign o_dmem_addr  = memop_in_mem ? (mem_x_phase ? m_word1 : m_word0) : epv_maddr;",
     "assign o_dmem_addr  = (memop_in_mem ? (mem_x_phase ? m_word1 : m_word0) : epv_maddr) | 32'h2;"),
    ("A8_IFETCH_OFF_MAP", "rtl/epv32_core.v",
     "assign o_imem_addr = pc;",
     "assign o_imem_addr = pc + 32'h4000_0000;"),
    ("A10_TRAP_CNT_WENT_BACK", "rtl/epv32_core.v",
     "o_dbg_trap_cnt  <= o_dbg_trap_cnt + 32'd1;",
     "o_dbg_trap_cnt  <= o_dbg_trap_cnt - 32'd1;"),
    ("E1_VRF_WR_NOT_ON_DONE", "rtl/epv32_epv.v",
     "assign o_vrf_wr = o_done && vec_wr;",
     "assign o_vrf_wr = vec_wr;"),
    ("E2_ILLEGAL_STILL_WRITES", "rtl/epv32_epv.v",
     "assign o_vrf_wr = o_done && vec_wr;",
     "assign o_vrf_wr = (o_done || o_illegal) && vec_wr;"),
    ("A8_DMEM_OFF_MAP", "rtl/epv32_core.v",
     "assign o_dmem_addr  = memop_in_mem ? (mem_x_phase ? m_word1 : m_word0) : epv_maddr;",
     "assign o_dmem_addr  = (memop_in_mem ? (mem_x_phase ? m_word1 : m_word0) : epv_maddr)"
     " | 32'h4000_0000;"),
    # 与 A7 同一个锚点但换一个扰动位: A7 打的是「未对齐」(低位), A8_DMEM 打的是
    # 「不在地址图上」(高位)。同一个 `| 32'h2` 只会撞 A7, 于是 A8_DMEM 这一条
    # 永远没人注入过 —— 形状相近的两条判据各要各的扰动。
    ("E4_EPV_BUS_UNALIGNED", "rtl/epv32_epv.v",
     "assign o_mem_addr  = base + {28'b0, wc, 2'b00};",
     "assign o_mem_addr  = (base + {28'b0, wc, 2'b00}) | 32'h2;"),
    ("E5_GRANT_WITHOUT_REQ", "rtl/epv32_core.v",
     "assign epv_grant    = epv_mreq && !memop_in_mem;",
     "assign epv_grant    = !memop_in_mem;"),
]

# 没有廉价源码注入的 tag —— 明写出来, 不靠「反正门是绿的」蒙过去。
# 每条带上「为什么注不出来」与「什么实验能翻这一格」: 未证明与不可能证明是两句话。
NO_CHEAP_MUTATION = [
    # 需要一个「EX 里是分支」且「MEM 里正做跨字访问 (mem_hold)」同拍成立的定向
    # 汇编探针; 现有 14 个程序 (含 misalign_bisect 与三套件) 全跑完 14/14 都没给出
    # 这一拍。翻案办法: 写一条 lb 跨字后紧跟一条必然成立的 beq 的 .S 探针。
    "A3_REDIRECT_DURING_EXSTALL",
    # 构造性需要「同一条编码既被判 o_illegal 又让 vec_wr 为真」; 现有译码里两支
    # 互斥, 所以源码注入注不出来。翻案办法: tb/epv_unit.cpp 直接驱动端口, 送一个
    # field_bad 与 c1_mac4 同时成立的指令字。
    "E6_EPV_INST_CHANGED_MIDFLIGHT",
    "E2_ILLEGAL_STILL_WRITES",
    # o_xwr 与 o_done 由同一根 assign 导出, 单独注 o_xwr 会先撞 E1。
    "E3_XWR_NOT_ON_DONE",
    # 需要「乘/除 busy 与 EPV busy 同拍」—— 译码上 idex_is_md 与 idex_is_epv 互斥,
    # 所以这条在当前设计里是结构性永真; 它留在门里是为了将来引入双单元时不静默。
    "A11_TWO_BUSY_UNITS",
    # 需要「同一条指令既非法又是 ecall/ebreak」; 当前 sys_bad 与 do_ecall 互斥。
    "A12_ILLEGAL_AND_SYSCALL",
]


def sha16(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def declared_tags():
    """tag 清单由两份 .vh 现读, 不手抄 (手抄的那份会漂)。"""
    tags = []
    for rel in ASSERT_FILES:
        p = os.path.join(ROOT, rel.replace("/", os.sep))
        txt = io.open(p, encoding="utf-8").read()
        tags += TAG_RE.findall(txt) + TAG_MACRO_RE.findall(txt)
    return tags


SRC_MDIR = os.path.join(ROOT, "build", "sim_assert")

# run_selftest 里夹具的名字。末行的 n 由这个清单现算, 不写死。
SELFTEST_NAMES = ("tag_visibility_ab", "mutation_table_staleness")


def build_sim(expect_fail=False):
    # 产物路径写死在**构建端**的输出目录上。之前这里用调用者传进来的 mdir
    # (那是注入臂要把产物挪去的地方), 于是构建其实成功了而 ok 恒 False,
    # 13 发注入整批 BUILD-FAIL —— 仪器把自己的判决读反了。
    env = dict(os.environ)
    env["ASSERT"] = "1"
    log = os.path.join(tempfile.gettempdir(), "assert_build.log")
    with io.open(log, "w", encoding="utf-8", errors="replace") as f:
        rc = subprocess.call(["bash", os.path.join(ROOT, "flow", "build_sim.sh").replace(
            os.sep, "/")], cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, env=env)
    txt = io.open(log, encoding="utf-8", errors="replace").read()
    exe = os.path.join(SRC_MDIR, "epv32sim_assert.exe")
    ok = os.path.isfile(exe) and "BUILD_SIM_OK" in txt
    if not ok and not expect_fail:
        print("SETUP-FAIL build rc=%d exe_exists=%s log_head=%r" % (
            rc, os.path.isfile(exe), txt[:600]))
    return rc, ok, txt


def relocate(mdir):
    """Verilator 的 --Mdir 由 build_sim.sh 定死, 注入臂把它挪到另一个目录。"""
    src = SRC_MDIR
    if os.path.isdir(mdir):
        shutil.rmtree(mdir, ignore_errors=True)
    if os.path.isdir(src):
        shutil.move(src, mdir)
    else:
        raise RuntimeError("RELOCATE-FAIL: %s 不存在 (编译那一步没产出目录)" % src)


ARM_RE = re.compile(r"EPVASSERT (CORE|EPV)_ARMED n=(\d+)")


def run_one(exe, binf, max_cyc, scratch, elf=None):
    # .bin 是镜像 (EPV_IMAGE 吃它), .elf 才有符号表 (nm 吃它) —— 分开传,
    # 否则会报 "file format not recognized" 而长得像「用例不存在」。
    syms = rt.symbols(elf or binf)
    rf = os.path.join(scratch, "res.json")
    if os.path.isfile(rf):
        os.remove(rf)
    env = dict(os.environ)
    env.update({
        "EPV_IMAGE": binf, "EPV_BASE": "0x80000000",
        "EPV_MAXCYC": str(max_cyc), "EPV_RESULT": rf,
        "EPV_TRACE": "", "EPV_TOHOST": str(syms.get("tohost", 0)),
    })
    t0 = time.time()
    try:
        p = subprocess.run([exe], env=env, capture_output=True, timeout=600)
        out = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
        rc = p.returncode
    except subprocess.TimeoutExpired:
        out, rc = "TIMEOUT", -1
    r = {}
    if os.path.isfile(rf):
        r = json.load(io.open(rf, encoding="utf-8"))
    return rc, out, r, round(time.time() - t0, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-mutate", action="store_true",
                    help="(兼容旧写法) 只跑正向臂; 这本来就是默认行为")
    ap.add_argument("--mutate", action="store_true",
                    help="跑 13 发源码注入臂 (~16 分钟) 并把判决连同 rtl 指纹落到 "
                         "results/asserts/mutation_proof.json")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--only", default="",
                    help="逗号分隔的 tag 名, 只跑注入表里这些条 (调试用)")
    ap.add_argument("--max-cyc", type=int, default=400_000)
    args = ap.parse_args()

    problems = []
    scratch = tempfile.mkdtemp(prefix="epvassert_")

    # ---- 现读 tag 清单与 .vh 里自称的条数是否一致 (.vh 里那句 n=12 是给人看的)
    tags = sorted(set(declared_tags()))
    print("TAGS declared=%d %s" % (len(tags), ",".join(tags)))
    per_file = {}
    for rel in ASSERT_FILES:
        txt = io.open(os.path.join(ROOT, rel.replace("/", os.sep)), encoding="utf-8").read()
        m = re.search(r"_ARMED n=(\d+)", txt)
        per_file[rel] = int(m.group(1)) if m else -1
    print("TAGS armed_claims=%s" % per_file)

    # ---- 正向臂: 认证臂与断言臂都必须是「同一份 RTL」编出来的
    exe = os.path.join(ROOT, "build", "sim_assert", "epv32sim_assert.exe")
    if not os.path.isfile(exe):
        build_sim()
    if not os.path.isfile(exe):
        print("SETUP-FAIL no assert-arm simulator at %s" % exe)
        return 2

    bins = sorted(glob_bins())   # [(stem, elf, bin), ...]
    if len(bins) < 3:
        print("SETUP-FAIL only %d image pairs found -- 正向臂的分母塌了" % len(bins))
        return 2
    print("PROGRAMS found=%d run=%s" % (len(bins), ",".join(b[0] for b in bins)))

    armed_seen = set()
    total_fails = 0
    prog_rows = []
    for stem, elf, b in bins:
        rc, out, r, wall = run_one(exe, b, args.max_cyc, scratch, elf=elf)
        fails = [l for l in out.splitlines() if "EPVASSERT FAIL" in l]
        for m in ARM_RE.finditer(out):
            armed_seen.add(m.group(1))
        total_fails += len(fails)
        prog_rows.append((stem, rc, len(fails), r.get("verdict"), wall))
    print("POSITIVE programs=%d armed=%s fail_lines=%d" % (
        len(prog_rows), sorted(armed_seen), total_fails))
    for row in prog_rows:
        print("   %-26s rc=%-3s fails=%-3d verdict=%-8s wall=%ss" % row)
    for which in ("CORE", "EPV"):
        if which not in armed_seen:
            problems.append("NO-HEARTBEAT %s_ARMED 没打印 => 那一份 .vh 根本没编进方程" % which)
    if total_fails:
        problems.append("LEGAL-DESIGN-FAILS %d 条断言在合法程序上开火了" % total_fails)

    # ---- 注入臂
    proven = set()
    mut_rows = []
    if args.mutate:
        only = [o.strip() for o in args.only.split(",") if o.strip()]
        for tag, rel, old, new in MUTATIONS:
            if only and not any(o in tag for o in only):
                continue
            hit = apply_patch(rel, old, new)
            if hit != 1:
                mut_rows.append((tag, "ANCHOR-hits=%d" % hit))
                problems.append("SETUP-FAIL anchor %s hits=%d" % (tag, hit))
                continue
            try:
                rc, ok, btxt = build_sim()
                relocate(MUT)
                exe_m = os.path.join(MUT, "epv32sim_assert.exe")
                if not ok or not os.path.isfile(exe_m):
                    mut_rows.append((tag, "BUILD-FAIL"))
                    problems.append("SETUP-FAIL mutation build %s" % tag)
                    continue
                fired = []
                seen = set()
                ran = 0
                # 不许在「第一个开出任何火的程序」处 break: 一次注入常常会先撞上一条
                # 更早/更外围的断言 (实测 A6 的注入先在 epv_bisect 上打出
                # A8_IFETCH_OFF_MAP), 那就永远轮不到被点名的那条 —— 症状是断言被判
                # 「死」, 而真相是搜索被遮蔽了。只有拿到被点名的 tag 才停。
                for stem, elf, b in bins:
                    _rc, out, _r, _w = run_one(exe_m, b, args.max_cyc, scratch, elf=elf)
                    if _r:
                        ran += 1
                    got = set(re.findall(r"EPVASSERT FAIL ([A-Z0-9_]+)", out))
                    if got:
                        fired.append((stem, sorted(got)))
                        seen |= got
                    if tag in seen:
                        break
                if any(tag in f[1] for f in fired):
                    proven.add(tag)
                    mut_rows.append((tag, "FIRED-by=%s" % fired[0][0]))
                else:
                    # 注入表里保留这些条目是**记录试过什么**; 但它们的 tag 已经被
                    # NO_CHEAP_MUTATION 逐条写明「为什么源码注入注不出来 + 什么实验
                    # 能翻案」, 所以 no-fire 是预期结果而不是缺陷。把它判成
                    # DEAD-ASSERTION 会诱导下一个人去松那条断言, 而那正是要挡的。
                    if tag in NO_CHEAP_MUTATION:
                        mut_rows.append((tag, "DECLARED-NOFIRE (见 no_cheap 理由)"))
                    else:
                        mut_rows.append((tag, "NO-FIRE tried=%d ran=%d %s" % (
                            len(bins), ran, fired[:1])))
                        problems.append("DEAD-ASSERTION %s 被注入后仍不开火" % tag)
            finally:
                restore(rel)
                if os.path.isdir(MUT):
                    shutil.rmtree(MUT, ignore_errors=True)
        print("MUTATIONS run=%d proven=%d" % (len(MUTATIONS), len(proven)))
        for row in mut_rows:
            print("   %-34s %s" % row)

    # ---- 注入臂判决的落盘与「本代有没有被证明过」这一格
    # 默认那一臂 (正向) 只有 ~1 秒, 而注入臂要 16 分钟, 所以链条里跑默认那一臂。
    # 但「这些断言被证明会咬」是一句关于**当前这一代 RTL** 的话: 注入臂的判决
    # 必须连同它当时的逐文件 rtl 指纹一起存档, 默认臂再核它是不是本代的 ——
    # 否则改了 RTL 之后链条照绿, 而负向证明属于上一代。
    from atomic_json import write_json_atomic
    proof_path = os.path.join(ROOT, "results", "asserts", "mutation_proof.json")
    try:
        rtl_now = rt.sim_provenance()["rtl_sha256"]
    except Exception as exc:
        rtl_now = None
        problems.append("SETUP-FAIL 读不到 rtl 指纹: %r" % (exc,))
    if args.mutate:
        write_json_atomic(proof_path, {
            "what": "注入臂 (%d 发源码注入) 的判决, 连同它当时的逐文件 rtl sha256"
                    % len(MUTATIONS),
            "how_to_read": "proven 是「注入后确实开火」的 tag; not_proven 是既没有"
                           "注入臂也没被列进 no_cheap_mutation 的缺口 (应为空)",
            "rtl_sha256": rtl_now, "tags": tags,
            "proven": sorted(proven), "mutations": len(MUTATIONS),
            "not_proven": [t for t in tags
                           if t not in proven and t not in NO_CHEAP_MUTATION],
            "no_cheap_mutation": sorted(NO_CHEAP_MUTATION),
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        print("PROOF_WRITTEN %s proven=%d/%d" % (
            proof_path.replace(os.sep, "/")[len(ROOT) + 1:], len(proven), len(tags)))
    elif rtl_now is not None:
        if not os.path.isfile(proof_path):
            problems.append("ARMOR-PROOF-MISSING 没有任何一份注入臂判决存档 "
                            "=> 「这些断言会咬」这句话没有证据")
        else:
            pr = json.load(io.open(proof_path, encoding="utf-8"))
            if pr.get("rtl_sha256") != rtl_now:
                problems.append("ARMOR-PROOF-STALE 注入臂判决属于另一代 RTL "
                                "(缺 %d 个 / 多 %d 个 / 变 %d 个) => 重跑 "
                                "python flow/assert_selfcheck.py --mutate" % (
                                    len(set(rtl_now) - set(pr.get("rtl_sha256") or {})),
                                    len(set(pr.get("rtl_sha256") or {}) - set(rtl_now)),
                                    sum(1 for k in set(rtl_now) & set(
                                        pr.get("rtl_sha256") or {})
                                        if rtl_now[k] != pr["rtl_sha256"][k])))
            else:
                proven = set(pr.get("proven") or [])

    unproven = [t for t in tags if t not in proven and t not in NO_CHEAP_MUTATION]
    print("COVERAGE tags=%d mutations=%d proven=%d declared_no_cheap=%d unaccounted=%d" % (
        len(tags), len(MUTATIONS), len(proven), len(NO_CHEAP_MUTATION), len(unproven)))
    if unproven:
        print("   UNACCOUNTED %s" % ",".join(unproven))
        if args.only:
            # --only 是调试滤器, 它让分母变小而不是让缺口消失 —— 不许据此判绿。
            print("   NOTE filtered_by=--only, 守恒式在滤器下不适用, 未判绿")
        else:
            problems.append("UNPROVEN %d 条 tag 既没有注入证明也没被显式列为无廉价注入" % len(unproven))
    # 守恒式: tag 全集 = 已证明 + 已声明无注入; 两者不许重叠成漏项
    overlap = sorted(set(NO_CHEAP_MUTATION) & proven)
    ghost = [t for t in NO_CHEAP_MUTATION if t not in tags]
    if overlap:
        print("   NOTE 声明为无注入但其实已被别的原因开火: %s" % overlap)
    if ghost:
        problems.append("STALE-NO-MUTATION-LIST %s 已不在 tag 全集里 (清单过期)" % ghost)
        print("   STALE %s" % ghost)

    if args.selftest:
        s_errs = run_selftest(tags)
        problems += s_errs
        # 分母内联 len(名单): 中间变量会让「这个数由清单现算」读不出来,
        # 而 tools/fixture_claims.py 专抓末行分母没人管的形状。
        sys.stdout.write("ASSERT_SELFTEST_OK %d/%d\n" % (
            max(len(SELFTEST_NAMES) - len(s_errs), 0), len(SELFTEST_NAMES)))

    shutil.rmtree(scratch, ignore_errors=True)
    print("")
    if problems:
        print("ASSERT_SELFCHECK FAILED problems=%d" % len(problems))
        for p in problems:
            print("  - %s" % p)
        return 1
    # 判决行里不许出现第二个 `N/M` 形状: tools/fixture_claims.py 按「末行有没有
    # 一个 X_OK N/M」认领夹具判决, 而 `armed=2/2` 会被它读成那条判决 —— 一个数
    # 两种身份就是两个消费者各读一份。heartbeat 数因此印成不带斜杠的 armed_units。
    print("ASSERT_SELFCHECK OK mode=%s programs=%d armed_units=%d fail_lines=0 "
          "tags=%d proven=%d no_cheap=%d" % (
              "mutate" if args.mutate else "positive+proof",
              len(prog_rows), len(armed_seen), len(tags),
              len(proven), len(NO_CHEAP_MUTATION)))
    return 0


_PATCH_STATE = {}


def apply_patch(rel, old, new):
    p = os.path.join(ROOT, rel.replace("/", os.sep))
    txt = io.open(p, encoding="utf-8", newline="").read()
    hits = txt.count(old)
    if hits != 1:
        return hits
    _PATCH_STATE[rel] = (p, txt, sha16(p))
    io.open(p, "w", encoding="utf-8", newline="").write(txt.replace(old, new))
    return hits


def restore(rel):
    p, txt, before = _PATCH_STATE.pop(rel)
    io.open(p, "w", encoding="utf-8", newline="").write(txt)
    after = sha16(p)
    print("   restored %s sha16 %s -> %s (diff %s)" % (
        rel, before, after, "0" if before == after else "NONZERO"))
    if before != after:
        raise RuntimeError("RESTORE-MISMATCH %s" % rel)


def glob_bins():
    d = os.path.join(ROOT, "build", "elf")
    out = []
    for dirpath, _dirnames, filenames in os.walk(d):
        for fn in filenames:
            if fn.endswith((".bin", ".elf")):
                out.append(os.path.join(dirpath, fn))
    stems = {}
    for fp in out:
        base = os.path.basename(fp)
        stems.setdefault(base[:-4], {})[base[-4:]] = fp
    pairs = [(s, v[".elf"], v[".bin"]) for s, v in sorted(stems.items())
             if ".elf" in v and ".bin" in v]
    # 命名用双下划线 (rv32um__div), 单下划线一条都匹配不到 —— 分母会悄悄缩水。
    want = ("epv_selftest", "epv_bisect", "micro_bisect", "misalign_bisect",
            "nn_bench", "rv32um__div", "rv32um__mul", "rv32um__rem",
            "rv32mi__illegal", "rv32mi__breakpoint", "rv32mi__csr",
            "rv32ui__add", "rv32ui__beq", "rv32ui__lb")
    picked = [p for p in pairs if p[0] in want]
    return picked or pairs[:6]


def run_selftest(tags):
    """本件自己的两条夹具: 抹掉一个 tag 必须从清单里消失; 期望值不许来自抄本。"""
    errs = []
    checks = 0
    probe = tags[0]
    rel = ASSERT_FILES[0] if probe in declared_tags_of(ASSERT_FILES[0]) else ASSERT_FILES[1]
    p = os.path.join(ROOT, rel.replace("/", os.sep))
    txt = io.open(p, encoding="utf-8", newline="").read()
    if probe not in txt:
        errs.append("SELFTEST-SETUP 现读的 tag %s 不在 %s" % (probe, rel))
    else:
        io.open(p, "w", encoding="utf-8", newline="").write(txt.replace(probe, "TAG_REMOVED"))
        gone = probe not in declared_tags()
        io.open(p, "w", encoding="utf-8", newline="").write(txt)
        back = sha16(p)
        if not gone:
            errs.append("SELFTEST-FAIL 抹掉 tag 后它仍在清单里")
        if "TAG_REMOVED" in io.open(p, encoding="utf-8").read():
            errs.append("SELFTEST-FAIL 还原没做干净")
        print("SELFTEST 1/%d tag-visibility probe=%s detected=%s restore_sha16=%s" % (
            len(SELFTEST_NAMES), probe, gone, back))
    # 夹具 2: tag 全集不许为空, 且注入表点名的 tag 必须都在全集里
    bad = [t for t, _f, _o, _n in MUTATIONS if t not in tags]
    if bad:
        errs.append("STALE-MUTATION-TABLE %s" % bad)
    print("SELFTEST 2/%d mutation_table_entries=%d stale=%d" % (
        len(SELFTEST_NAMES), len(MUTATIONS), len(bad)))
    return errs


def declared_tags_of(rel):
    txt = io.open(os.path.join(ROOT, rel.replace("/", os.sep)), encoding="utf-8").read()
    return TAG_RE.findall(txt) + TAG_MACRO_RE.findall(txt)


if __name__ == "__main__":
    sys.exit(main())
