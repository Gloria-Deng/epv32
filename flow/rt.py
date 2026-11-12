#!/usr/bin/env python3
# =============================================================================
#  flow/rt.py -- EPV32 统一的「编译 -> 装载 -> 仿真 -> 判定」驱动。
#
#  每个数字都落到 results/ 下 JSON, **判定只看 JSON 字段, 不看退出码**
#  (踩过: 工具在中文路径下打不开文件仍退 0; verilator 的 perl 包装崩了 shell 仍见 0)。
#
# 用法:
#   python flow/rt.py riscv rv32ui             # 跑整个 rv32ui 合规套件
#   python flow/rt.py riscv rv32um --one add
#   python flow/rt.py asm  tests/asm/xxx.S
#   python flow/rt.py bench sw/bench/yyy.c --max-cyc 20000000
# =============================================================================
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TC   = r"E:\msys64\ucrt64\bin"
CC   = os.path.join(TC, "riscv64-unknown-elf-gcc.exe")
OC   = os.path.join(TC, "riscv64-unknown-elf-objcopy.exe")
NM   = os.path.join(TC, "riscv64-unknown-elf-nm.exe")
OD   = os.path.join(TC, "riscv64-unknown-elf-objdump.exe")
SIM  = os.path.join(ROOT, "build", "sim", "epv32sim.exe")
RES  = os.path.join(ROOT, "results")
REFS = os.path.join(ROOT, "refs", "riscv-tests")

MARCH = "rv32im_zicsr_zifencei"
MABI  = "ilp32"


def sh(cmd, env=None, cwd=None):
    """跑一条命令, 返回 (rc, stdout, stderr)。stdout/stderr 一律按字节捕获再
    用 utf-8 容错解码 -- Windows 控制台是 GBK, 中文一律落文件不回显。

    PATH 里强制带上工具链目录: MSYS2 的 gcc 找不到同目录的 as/ld 时会
    「退 1 且一个字都不输出」(实测), 这种失败长得像脚本 bug。驱动不依赖
    调用方给的环境。"""
    e = dict(os.environ if env is None else env)
    e["PATH"] = TC + os.pathsep + e.get("PATH", "")
    e.setdefault("TMP", r"C:\Users\lenovo\AppData\Local\Temp")
    e.setdefault("TEMP", e["TMP"])
    p = subprocess.run(cmd, env=e, cwd=cwd,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def ensure_dirs():
    for d in ("build/elf", "results/sim", "results/compliance", "results/trace"):
        os.makedirs(os.path.join(ROOT, d), exist_ok=True)


# ------------------------------------------------------------------ 符号表
def symbols(elf):
    rc, out = sh([NM, "-n", elf])
    if rc != 0 or not out.strip():
        raise RuntimeError("NM_FAIL rc=%d out=%r" % (rc, out[:200]))
    syms = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3 and re.fullmatch(r"[0-9a-f]+", parts[0]):
            syms[parts[2]] = int(parts[0], 16)
    if not syms:
        raise RuntimeError("NM_PARSED_ZERO_SYMBOLS")
    return syms


# ------------------------------------------------------------------ 编译
BUILD_INPUTS = {}    # 每次 build_elf 刷新：本次真正编进去的文件 -> sha256 前 16 位


def sha16(path):
    """文件指纹的唯一定义（16 位十六进制）。门禁侧要重算也调这个，别另写一份。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return h.hexdigest()[:16]


def build_elf(name, srcs, ldscript=None, extra_defs=None, link_env_ld=False):
    ensure_dirs()
    elf = os.path.join(ROOT, "build", "elf", name + ".elf")
    binf = os.path.join(ROOT, "build", "elf", name + ".bin")
    cmd = [CC, "-march=" + MARCH, "-mabi=" + MABI, "-nostdlib", "-nostartfiles",
           "-static", "-g", "-O2", "-fno-lto", "-Wall",
           "-Wl,--no-warn-rwx-segments", "-I", os.path.join(ROOT, "sw", "inc")]
    for d in (extra_defs or {}):
        cmd.append("-D%s=%s" % (d, extra_defs[d]))
    if link_env_ld:
        cmd.append("-T" + os.path.join(REFS, "env", "p", "link.ld"))
        cmd.append("-I" + os.path.join(REFS, "env"))
        cmd.append("-I" + os.path.join(REFS, "env", "p"))
        cmd.append("-I" + os.path.join(REFS, "isa", "macros", "scalar"))
    else:
        cmd.append("-T" + (ldscript or os.path.join(ROOT, "sw", "mem", "epv32.ld")))
        cmd.append(os.path.join(ROOT, "sw", "mem", "crt0.S"))
        cmd.append(os.path.join(ROOT, "sw", "lib", "platform.c"))
    cmd += list(srcs)
    # RV32 上 64 位乘/除需要 libgcc 的 __udivdi3/__umoddi3, 而 -nostdlib 不带这些;
    # 不加 -lgcc 会 undefined reference (实测 nn_bench 打 64 位 cycle ratio)。libgcc
    # 属编译器自身, 不会引入 newlib 运行时。
    cmd += ["-Wl,--start-group", "-lgcc", "-Wl,--end-group"]
    cmd += ["-o", elf]
    # 把"这次到底编进去了哪些文件"记下来。归档新鲜度以前只核 RTL 哈希，
    # C 侧改坏编不过时，盘上还有上一趟的归档可以顶着 PASS —— 见 2026-09-24
    # 那次：epv_selftest.c 有四处 epv_lvb 少写一个参数，编译报错，而认证矩阵
    # 里那一格仍然绿（它读的是旧 ELF 留下的 results/sim/epv_selftest.json）。
    ins = {}
    for f in (list(srcs)
              + [os.path.join(ROOT, "sw", "mem", "crt0.S"),
                 os.path.join(ROOT, "sw", "lib", "platform.c"),
                 ldscript or os.path.join(ROOT, "sw", "mem", "epv32.ld")]):
        if os.path.isfile(f):
            ins[os.path.abspath(f).replace(ROOT + os.sep, "").replace("\\", "/")] = sha16(f)
    for d in (os.path.join(ROOT, "sw", "inc"),):
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if fn.endswith((".h", ".inc")):
                    ins[("sw/inc/" + fn)] = sha16(os.path.join(d, fn))
    BUILD_INPUTS.clear()
    BUILD_INPUTS.update(ins)
    rc, out = sh(cmd)
    if rc != 0 or not os.path.isfile(elf):
        # 报错要**从头**截: gcc 的真错在第一行, 尾巴全是「defined but not used」
        # 那类警告 —— 只印 out[-4000:] 会把答案整段切掉 (今天就是这么找了三轮)。
        head = out if len(out) <= 4000 else out[:2500] + "\n...[切了 %d 字]...\n" % (
            len(out) - 4000) + out[-1500:]
        raise RuntimeError("COMPILE_FAIL rc=%d\n%s" % (rc, head))
    rc, out = sh([OC, "-O", "binary", "--gap-fill", "0", elf, binf])
    if rc != 0 or not os.path.isfile(binf) or os.path.getsize(binf) == 0:
        raise RuntimeError("OBJCOPY_FAIL rc=%d %s" % (rc, out[-500:]))
    return elf, binf, out


# ------------------------------------------------------------------ 仿真
from atomic_json import write_json_atomic   # 唯一实现, 见 flow/atomic_json.py


def sim_provenance():
    """每条仿真归档都要说清「是哪一版 RTL、哪个仿真器跑出来的」。

    「这结果是这版源码跑出来的」也是一条归属断言, 不记就等于没有 (M7 改 IALIGN 前
    的探针归档事后无法对应到改前的核; 综合/基准归档都为此单独存哈希)。
    """
    h = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
    rtl = {}
    for fn in sorted(os.listdir(os.path.join(ROOT, "rtl"))):
        if fn.endswith((".v", ".vh")):
            rtl[fn] = h(os.path.join(ROOT, "rtl", fn))
    assert rtl, "rtl/ 里没有源文件"
    return {"rtl_sha256": rtl,
            "sim_sha256": h(SIM) if os.path.isfile(SIM) else None,
            "sim_mtime_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                           time.gmtime(os.path.getmtime(SIM)))
                             if os.path.isfile(SIM) else None}


def run_sim(name, binf, syms, max_cyc=2_000_000, trace=True, epv=1, aname=None):
    ensure_dirs()
    aname = aname or name
    rf  = os.path.join(RES, "sim", aname + ".json")
    tf  = os.path.join(RES, "trace", aname + ".txt")
    env = dict(os.environ)
    env.update({
        "EPV_IMAGE":  binf,
        "EPV_BASE":   "0x80000000",
        "EPV_MAXCYC": str(max_cyc),
        "EPV_RESULT": rf,
        "EPV_TRACE":  tf if trace else "",
        "EPV_TOHOST": str(syms.get("tohost", 0)),
    })
    t0 = time.time()
    rc, out = sh([SIM], env=env)
    wall = time.time() - t0
    if not os.path.isfile(rf):
        raise RuntimeError("NO_RESULT_JSON rc=%d sim_stdout=%r" % (rc, out[:400]))
    with open(rf, encoding="utf-8") as f:
        r = json.load(f)
    r["sim_rc"] = rc
    r["wall_s"] = round(wall, 3)
    r["size_bytes"] = os.path.getsize(binf)
    r["image"] = binf.replace(ROOT + os.sep, "")
    r["name"] = name
    r["archive"] = aname
    r["tag"] = (aname[len(name) + 1:] if aname != name else None)
    r["max_cyc"] = max_cyc
    r["tohost_addr"] = hex(syms.get("tohost", 0))
    r["trace_file"] = tf.replace(ROOT + os.sep, "") if trace else None
    r.update(sim_provenance())
    r["build_inputs"] = dict(BUILD_INPUTS)
    if trace:
        r["trace_lines"] = sum(1 for _ in open(tf, encoding="utf-8", errors="replace"))
    write_json_atomic(rf, r)
    return r


# ------------------------------------------------------------------ 判定
def verdict(r):
    """只看归档字段判成败。tohost 协议: 1 = PASS, 否则 (testnum<<1)|1 = FAIL。"""
    if r["stop_reason"] == "TOHOST":
        last = r.get("commit_val")
        return ("PASS" if last == 1 else "FAIL"), last
    if r["stop_reason"] == "COMMIT":
        return ("PASS" if r["commit_val"] == 0 else "FAIL"), r["commit_val"]
    return "TIMEOUT", None


def parse_tohost_store(trace_lines_before_break):
    return None


# ------------------------------------------------------------------  riscv-tests
def run_compliance(suite, only=None, max_cyc=300_000):
    d = os.path.join(REFS, "isa", suite)
    if not os.path.isdir(d):
        raise RuntimeError("NO_SUCH_SUITE_DIR " + d)
    tests = sorted(x[:-2] for x in os.listdir(d) if x.endswith(".S"))
    if only:
        tests = [t for t in tests if t in only]
    rows, npass = [], 0
    for t in tests:
        src = os.path.join(d, t + ".S")
        nm_ = "%s__%s" % (suite, t)
        row = {"test": t, "suite": suite}
        try:
            elf, binf, wlog = build_elf(nm_, [src], link_env_ld=True)
            syms = symbols(elf)
            r = run_sim(nm_, binf, syms, max_cyc=max_cyc)
            v, code = verdict(r)
            # tohost 的终值不在 commit_val 里 (TB 在写 tohost 那一拍就停),
            # 所以回读轨迹里最后一次落到 tohost 地址的写值。
            th = syms.get("tohost", 0)
            if th:
                last = None
                tp = os.path.join(RES, "trace", nm_ + ".txt")
                if os.path.isfile(tp):
                    for ln in open(tp, encoding="utf-8", errors="replace"):
                        m = re.match(r"S ([0-9a-f]+) ([0-9a-f]+) ", ln)
                        if m and int(m.group(1), 16) == (th & ~3):
                            last = int(m.group(2), 16)
                if last is not None:
                    v = "PASS" if last == 1 else "FAIL"
                    code = last
            r["verdict"] = v
            r["tohost_val"] = code
            row.update(verdict=v, tohost=code, cycles=r["cycles"],
                       retired=r["retired"], stop=r["stop_reason"])
            if v == "PASS":
                npass += 1
        except Exception as e:
            row.update(verdict="BUILD_FAIL", err=str(e)[:200])
        rows.append(row)
    out = {"suite": suite, "n_tests": len(rows), "n_pass": npass,
           "all_pass": (npass == len(rows) and len(rows) > 0),
           "march": MARCH, "rows": rows}
    p = os.path.join(RES, "compliance", "%s.json" % suite)
    write_json_atomic(p, out)
    return out, p


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["riscv", "asm", "bench"])
    ap.add_argument("target")
    ap.add_argument("--one", action="append", default=None)
    ap.add_argument("--max-cyc", type=int, default=2_000_000)
    ap.add_argument("--no-trace", action="store_true")
    ap.add_argument("--tag", default=None,
                    help="把归档写到 <name>_<tag>.json，不碰已认证归档。"
                         "任何截断窗口/额外参数的复跑都必须带它")
    a = ap.parse_args()

    if a.kind == "riscv":
        out, p = run_compliance(a.target, a.one, a.max_cyc)
        print("COMPLIANCE %s: %d/%d PASS  all_pass=%s  -> %s"
              % (out["suite"], out["n_pass"], out["n_tests"],
                 out["all_pass"], os.path.relpath(p, ROOT)))
        bad = [r for r in out["rows"] if r["verdict"] != "PASS"]
        for r in bad[:40]:
            print("   NOTPASS %-14s verdict=%-10s tohost=%s stop=%s cyc=%s %s"
                  % (r["test"], r["verdict"], r.get("tohost"), r.get("stop"),
                     r.get("cycles"), r.get("err", "")[:60]))
        return 0 if out["all_pass"] else 1

    # asm / bench: 单个目标
    src = a.target if os.path.isabs(a.target) else os.path.join(ROOT, a.target)
    name = os.path.splitext(os.path.basename(src))[0]
    elf, binf, _ = build_elf(name, [src])
    syms = symbols(elf)
    aname = name + ("_" + a.tag if a.tag else "")
    r = run_sim(name, binf, syms, max_cyc=a.max_cyc, trace=not a.no_trace,
                aname=aname)
    v, code = verdict(r)
    r["verdict"] = v
    r["src_sha"] = sha16(src)      # 这份归档是哪份 C 源码产出的（门禁核新鲜度用）
    write_json_atomic(os.path.join(RES, "sim", aname + ".json"), r)
    print("VERDICT=%s code=%s cycles=%s retired=%s uart=%r"
          % (v, code, r["cycles"], r["retired"], r["uart"][:200]))
    print("RESULT=" + os.path.join("results", "sim", aname + ".json"))
    return 0 if v == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
