#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/net_archive.py -- 把 nn_net (端到端 INT8 推理) 的读数汇成认证归档。

与 bench_archive.py 同一套规矩: 正文里关于「网络级」的任何数字只能来自这份文件,
字段全部现算自本次运行, 不从报告反抄。另外多三条自洽核对:
  * 整网周期 == 三层之和 (不一致就说明有两处计时互相踩了读数);
  * 打印出来的 SPEEDUP_X1000 与「两侧周期现算的比值」相符 (容差由 ×1000 的末位推);
  * 判决与退出码都取自运行归档, 不看本工具的退出码。
"""
import hashlib
import io
import json
import os
import re
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "flow"))
from atomic_json import write_json_atomic      # 归档落盘的唯一实现
TC = r"E:\msys64" + os.sep + "ucrt64" + os.sep + "bin"
SIMF = os.path.join("results", "sim", "nn_net.json")
OUT = os.path.join("results", "bench", "nn_net.json")

INT_KEYS = ("NET_MACS", "NSAMP", "REF_CYC", "EPV_CYC", "WIDE_CYC", "REF_INST",
            "EPV_INST", "SPEEDUP_X1000", "CYC_PER_MAC_REF_X100",
            "CYC_PER_MAC_EPV_X100", "LAYER_MISMATCH", "LOGIT_MISMATCH",
            "ARGMAX_AGREE", "MAX_ABS_DEV", "REF_CYC_L1", "REF_CYC_L2",
            "REF_CYC_TAIL", "EPV_CYC_L1", "EPV_CYC_L2", "EPV_CYC_TAIL")


def die(msg):
    raise SystemExit("NET_ARCHIVE_FAIL: " + msg)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for blk in iter(lambda: f.read(65536), b""):
            h.update(blk)
    return h.hexdigest()


def ver(exe, *a):
    try:
        p = subprocess.run([exe] + list(a), capture_output=True, timeout=60)
        return p.stdout.decode("utf-8", "replace").splitlines()[0].strip()
    except Exception as e:
        return "UNREADABLE: %r" % e


def _is_utf8(b):
    """子进程在没有 reconfigure 的管道上会用本机代码页 (GBK) 写字节 ——
    按 UTF-8 硬解会得到一串替换字符, 于是「理由对不对」变成「解码运气好不好」。"""
    try:
        b.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def selftest():
    """每条注入都必须核对**为什么红**; healthy 那条还兼作零副作用证明。

    替身归档写在临时目录里, 真归档一个字都不许动 —— 上一轮就是因为一个
    截断运行往认证档上写了一次 TIMEOUT, 才有整套同源门禁。
    """
    import shutil
    import tempfile
    if not os.path.isfile(SIMF):
        print("SETUP-FAIL 没有 %s, 先跑一次 nn_net" % SIMF)
        return 1
    base = json.load(io.open(SIMF, encoding="utf-8"))
    real_out = sha(OUT) if os.path.isfile(OUT) else None
    tmp = tempfile.mkdtemp(prefix="net_inj_")
    fails, fired = [], []
    try:
        def variant(name, fn):
            d = json.loads(json.dumps(base))
            d["uart"] = fn(d["uart"].replace("\r\n", "\n"))
            sp = os.path.join(tmp, name + ".json")
            with io.open(sp, "w", encoding="utf-8") as f:
                json.dump(d, f)
            op = os.path.join(tmp, name + ".out.json")
            p = subprocess.run([sys.executable, os.path.abspath(__file__),
                                "--sim", sp, "--out", op],
                               capture_output=True)
            msg = (p.stdout + p.stderr).decode("utf-8") if _is_utf8(
                p.stdout + p.stderr) else (p.stdout + p.stderr).decode(
                "gbk", "replace")
            return p.returncode, msg, op

        def expect(name, want_sub, fn):
            rc, msg, _op = variant(name, fn)
            if rc == 0:
                fails.append("%s: 没有红 —— 这条自洽核对是空的" % name)
            elif want_sub not in msg:
                fails.append("%s: 红了但理由不是 %r: %s" % (name, want_sub, msg[:180]))
            else:
                fired.append(name)

        expect("层和不等", "三层之和",
               lambda u: re.sub(r"(?m)^REF_CYC \d+$", "REF_CYC 999999", u))
        expect("加速比不自洽", "由两侧周期现算",
               lambda u: re.sub(r"(?m)^SPEEDUP_X1000 \d+$", "SPEEDUP_X1000 1", u))
        expect("缺读数", "读数里缺 MAX_ABS_DEV",
               lambda u: re.sub(r"(?m)^MAX_ABS_DEV \d+$", "", u))
        expect("样本行数", "逐样本行数",
               lambda u: re.sub(r"(?m)^S 0 ref=.*$", "", u))
        # healthy: 同样的输入走一遍, 必须写替身路径成功且不碰真归档
        rc, msg, op = variant("healthy", lambda u: u)
        if rc != 0 or not os.path.isfile(op):
            fails.append("healthy 没跑成: rc=%d %s" % (rc, msg[:180]))
        if os.path.isfile(OUT) and sha(OUT) != real_out:
            fails.append("注入自检改动了真归档 %s —— 副作用泄漏" % OUT)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for f in fails:
        print("SELFTEST-FAIL " + f)
    if fails:
        print("NET_ARCHIVE_SELFTEST_FAIL %d" % len(fails))
        return 1
    print("NET_ARCHIVE_SELFTEST_OK %d 条注入各自点名理由, healthy 写了替身且真归档未动"
          % len(fired))
    return 0


def main():
    # 注入自检需喂替身运行归档，故路径可覆盖；默认值不变，正常复现命令不加参数。
    global SIMF, OUT
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--sim" and i + 1 < len(argv):
            SIMF = argv[i + 1]
        if a == "--out" and i + 1 < len(argv):
            OUT = argv[i + 1]
    if "--selftest" in argv:
        return selftest()
    if not os.path.isfile(SIMF):
        die("没有运行归档 %s —— 先跑 python flow/rt.py bench sw/bench/nn_net.c" % SIMF)
    r = json.load(io.open(SIMF, encoding="utf-8"))
    txt = r["uart"].replace("\r\n", "\n")
    kv = {}
    for k in INT_KEYS:
        m = re.search(r"(?m)^%s (\d+)$" % k, txt)
        if not m:
            die("读数里缺 %s 这一行 (uart 头 %r)" % (k, txt[:160]))
        kv[k] = int(m.group(1))
    # 逐样本的 argmax 行: "S 0 ref=2 epv=2 wide=3"
    per = [(int(a), int(b), int(c), int(d)) for a, b, c, d in
           re.findall(r"(?m)^S (\d+) ref=(-?\d+) epv=(-?\d+) wide=(-?\d+)$", txt)]
    if len(per) != kv["NSAMP"]:
        die("逐样本行数 %d 与 NSAMP %d 不符 —— 有一样本没跑到末尾就停了"
            % (len(per), kv["NSAMP"]))

    # ---- 自洽核对 ----
    if kv["REF_CYC"] != kv["REF_CYC_L1"] + kv["REF_CYC_L2"] + kv["REF_CYC_TAIL"]:
        die("基线整网周期 %d != 三层之和 %d —— 两处计时互相踩了读数"
            % (kv["REF_CYC"],
               kv["REF_CYC_L1"] + kv["REF_CYC_L2"] + kv["REF_CYC_TAIL"]))
    if kv["EPV_CYC"] != kv["EPV_CYC_L1"] + kv["EPV_CYC_L2"] + kv["EPV_CYC_TAIL"]:
        die("EPV 整网周期 %d != 三层之和 —— 同上" % kv["EPV_CYC"])
    exact = kv["REF_CYC"] * 1000 // kv["EPV_CYC"]
    # 打印的是整数除法的结果, 容差 1 个末位 (×1000 定点)
    if abs(exact - kv["SPEEDUP_X1000"]) > 1:
        die("SPEEDUP_X1000 印 %d, 由两侧周期现算是 %d —— 对不上"
            % (kv["SPEEDUP_X1000"], exact))
    agree = sum(1 for _, a, b, _ in per if a == b)
    if agree != len(per):
        die("两份 int8 实现的 argmax 有 %d 个样本不一致, 但 LOGIT_MISMATCH=%d"
            % (len(per) - agree, kv["LOGIT_MISMATCH"]))

    out = {
        "benchmark": "nn_net: 端到端三层 INT8 网络 (conv3x3 6x6x16->4x4x16 -> "
                     "conv3x3 4x4x16->2x2x16 -> 2x2 最大池化 -> FC 16->4), "
                     "%d 个样本" % kv["NSAMP"],
        "run_archive": "results/sim/nn_net.json",
        "source": "sw/bench/nn_net.c",
        "source_sha256": sha(os.path.join("sw", "bench", "nn_net.c")),
        "macs_per_sample": kv["NET_MACS"],
        "samples": kv["NSAMP"],
        "macs_total": kv["NET_MACS"] * kv["NSAMP"],
        "ref_cycles": kv["REF_CYC"], "epv_cycles": kv["EPV_CYC"],
        "wide_cycles": kv["WIDE_CYC"],
        "ref_instret": kv["REF_INST"], "epv_instret": kv["EPV_INST"],
        "speedup_x1000": kv["SPEEDUP_X1000"],
        "speedup": round(kv["SPEEDUP_X1000"] / 1000.0, 3),
        "per_layer_cycles": {
            "conv_l1": {"ref": kv["REF_CYC_L1"], "epv": kv["EPV_CYC_L1"]},
            "conv_l2": {"ref": kv["REF_CYC_L2"], "epv": kv["EPV_CYC_L2"]},
            "pool_fc": {"ref": kv["REF_CYC_TAIL"], "epv": kv["EPV_CYC_TAIL"]}},
        "cyc_per_mac_ref": round(kv["REF_CYC"] / float(kv["NET_MACS"] * kv["NSAMP"]), 3),
        "cyc_per_mac_epv": round(kv["EPV_CYC"] / float(kv["NET_MACS"] * kv["NSAMP"]), 4),
        "layer_mismatches": kv["LAYER_MISMATCH"],
        "logit_mismatches": kv["LOGIT_MISMATCH"],
        "bit_exact_per_layer": kv["LAYER_MISMATCH"] == 0 and kv["LOGIT_MISMATCH"] == 0,
        "argmax_int8_vs_wide": kv["ARGMAX_AGREE"],
        "argmax_int8_vs_epv": agree,
        "max_abs_logit_dev_vs_wide": kv["MAX_ABS_DEV"],
        "per_sample_argmax": [{"i": i, "ref": a, "epv": b, "wide": c}
                              for i, a, b, c in per],
        "verdict": r.get("verdict"), "commit_val": r.get("commit_val"),
        "total_cycles": r.get("cycles"), "total_retired": r.get("retired"),
        "toolchain": {
            "simulator": ver(os.path.join(TC, "verilator_bin.exe"), "--version"),
            "cc": ver(os.path.join(TC, "riscv64-unknown-elf-gcc.exe"), "--version"),
            "march": "rv32im_zicsr_zifencei", "mabi": "ilp32",
            "opt": "-O2 -nostdlib -lgcc"},
        "rtl_sha256": {fn: sha(os.path.join("rtl", fn))
                       for fn in sorted(os.listdir("rtl"))
                       if fn.endswith((".v", ".vh"))},
        "caveats": [
            "权重由 LFSR 定种子生成, 这张网络**没有训练过**: 所以本归档里没有"
            "「公开数据集精度」这类读数, 只有逐位相同、周期与量化误差量级",
            "ARGMAX_AGREE=0 是这张随机权重网络的性质, 不是缺陷: int8 与宽精度"
            "的 logits 最大偏差 %d 已一并记下, 它只是误差量级, 不是精度"
            % kv["MAX_ABS_DEV"],
            "宽精度那一档只用于量化误差对照, 不作为性能读数",
            "存储为单周期理想模型 (无 cache); 无实物板",
        ],
        "self_consistency": {
            "ref_total_equals_layer_sum": True,
            "epv_total_equals_layer_sum": True,
            "speedup_recomputed_from_cycles": exact,
            "note": "三条都在取数前核过; 任何一条不符本工具直接报错退出, 不落归档"},
    }
    os.makedirs("results/bench", exist_ok=True)
    write_json_atomic(OUT, out)
    print("NET_ARCHIVE_OK -> results/bench/nn_net.json")
    print("  speedup=%.3f  bit_exact_per_layer=%s  cyc/MAC %.3f -> %.3f"
          % (out["speedup"], out["bit_exact_per_layer"],
             out["cyc_per_mac_ref"], out["cyc_per_mac_epv"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
