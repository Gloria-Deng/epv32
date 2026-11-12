#!/usr/bin/env python3
"""把 nn_cifar.c 的 UART 读数汇成 results/bench/cifar10_net.json。

这一份是「真实数据集上的那条网络，在这颗核上跑出来的周期与逐位核对」。
与 tools/net_archive.py 的分工: 那份管合成权重网络(LFSR, 无精度), 这份管
CIFAR-10 训练权重(有 top-1)。两者的数不许互相顶替, 所以归档也各自一份。

承重设计:
  * 分母(NSAMP、每层形状)从 sw/bench/cifar_int8.h 的 #define 现解, 不抄 UART
    打印的 MACS_EXEC —— 否则「加速比的分母」与「被核对的那次比对」同源, 自检
    永远绿。两个数一起印, 不等即红。
  * 判定只读归档字段, 不读退出码(见报告 5.1)。
  * --selftest 用注入证明 problems() 会咬: 改坏逐位相同、把样本数清 0、把
    加速比抬高, 三种都必须在 problems() 里点名开火。
"""
import hashlib
import io
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "flow"))
from atomic_json import write_json_atomic          # 归档落盘的唯一实现

TC = r"E:\msys64\ucrt64\bin"
SIM_ARC = os.path.join("results", "sim", "nn_cifar.json")
HDR = os.path.join("sw", "bench", "cifar_int8.h")
OUT = os.path.join("results", "bench", "cifar10_net.json")
ACC = os.path.join("results", "bench", "cifar10_int8.json")

KEYS = ("NSAMP", "MACS_EXEC", "MACS_INFO", "REF_CYC", "EPV_CYC", "REF_INST",
        "EPV_INST", "SPEEDUP_X1000", "CYC_PER_MAC_EXEC_REF_X100",
        "CYC_PER_MAC_EXEC_EPV_X100", "TOP1_REF", "TOP1_EPV",
        "ARGMAX_REF_VS_PY", "ARGMAX_REF_VS_EPV", "LAYER_MISMATCH",
        "PY_LAYER_MISMATCH", "PY_LOGIT_MISMATCH")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()


def shape_from_header():
    """解出网络形状与量化参数: 分母的唯一真源。"""
    txt = io.open(HDR, encoding="utf-8").read()
    d = {}
    for k in ("CIF_H0", "CIF_W0", "CIF_H1", "CIF_W1", "CIF_H2", "CIF_W2",
              "CIF_CIN", "CIF_COUT", "CIF_NCLS", "CIF_NSIM", "CIF_NPAD",
              "CIF_S1", "CIF_S2", "CIF_SF"):
        m = re.search(r"(?m)^#define\s+%s\s+(\d+)$" % k, txt)
        if not m:
            raise SystemExit("SETUP-FAIL: %s 里解不出 #define %s —— 形状变了, "
                             "分母不能再由本工具自算" % (HDR, k))
        d[k] = int(m.group(1))
    # 每条权重行的长度: 9 个卷积位置 x 输入通道
    d["KPIX"] = 9 * d["CIF_CIN"]
    d["NSHIFT"] = 3
    return d


def expected_macs(sh):
    per = (sh["CIF_H1"] * sh["CIF_W1"] * sh["CIF_COUT"] * sh["KPIX"]
           + sh["CIF_H2"] * sh["CIF_W2"] * sh["CIF_COUT"] * sh["KPIX"]
           + sh["CIF_NCLS"] * sh["CIF_CIN"])
    per_info = (sh["CIF_H1"] * sh["CIF_W1"] * sh["CIF_COUT"] * 9 * sh["CIF_NPAD"]
                + sh["CIF_H2"] * sh["CIF_W2"] * sh["CIF_COUT"] * sh["KPIX"]
                + sh["CIF_NCLS"] * sh["CIF_CIN"])
    return per * sh["CIF_NSIM"], per_info * sh["CIF_NSIM"]


def ver(exe, *a):
    try:
        p = subprocess.run([exe] + list(a), capture_output=True, timeout=60)
        out = p.stdout.decode("utf-8", "replace").strip().splitlines()
        return out[0].strip() if out else "EMPTY_OUTPUT"
    except Exception as e:
        return "UNREADABLE: %r" % e


def parse_uart(txt):
    kv = {}
    for k in KEYS:
        m = re.search(r"(?m)^%s (\d+)$" % k, txt)
        if not m:
            raise SystemExit("MISSING_FIELD %s (uart 头部=%r)" % (k, txt[:300]))
        kv[k] = int(m.group(1))
    if "CIF_NET_DONE" not in txt:
        raise SystemExit("NO_DONE_MARKER: 那次运行没跑到收尾, 读数不可采信")
    return kv


def problems(a):
    """所有判据集中在这里, --selftest 与真判决共用同一条实现。"""
    p = []
    if a.get("nsamp", 0) <= 0:
        p.append("样本数为 %r: 分母为 0 的归档不能被采信" % a.get("nsamp"))
        return p
    if a.get("macs_exec", 0) <= 0:
        p.append("MACS_EXEC 为 %r: 没有乘加就谈不上周期收益" % a.get("macs_exec"))
    # 加速比: 打印值与现算值必须一致(两侧周期直接相除)
    sp = round(a.get("speedup_x1000", 0) / 1000.0, 3)
    if abs(sp - a.get("speedup", -1)) > 0.001:
        p.append("加速比 %r 与定点读数 %r 不一致" % (a.get("speedup"), sp))
    rec = a.get("ref_cycles", 0) / float(a.get("epv_cycles", 1) or 1)
    if abs(rec - a.get("speedup", -1)) > 0.01:
        p.append("加速比 %r 与两侧周期现算 %r 差太多" % (a.get("speedup"), rec))
    # 分母由形状现算, 不采信运行自己打印的那个数
    if a.get("macs_exec_computed") is not None:
        if a["macs_exec_computed"] != a.get("macs_exec"):
            p.append("MACS_EXEC 打印 %r != 由 cif_int8.h 形状现算 %r"
                     % (a.get("macs_exec"), a["macs_exec_computed"]))
    if a.get("macs_info_computed") is not None:
        if a["macs_info_computed"] != a.get("macs_info"):
            p.append("MACS_INFO 打印 %r != 现算 %r"
                     % (a.get("macs_info"), a["macs_info_computed"]))
    if a.get("macs_info", 0) > a.get("macs_exec", 0):
        p.append("携带信息的 MAC %r 大于实际执行的 %r: 不可能, 形状解错了"
                 % (a.get("macs_info"), a.get("macs_exec")))
    # 三条实现互核: 任何一处不逐位相同即红
    for k, tag in (("layer_mismatches", "标量 C vs EPV 逐层"),
                   ("py_layer_mismatches", "标量 C vs python 侧逐层和"),
                   ("py_logit_mismatches", "标量 C vs python 侧 logits")):
        if a.get(k, -1) != 0:
            p.append("%s 不逐位相同: %r" % (tag, a.get(k)))
    if a.get("argmax_ref_vs_epv") != a.get("nsamp"):
        p.append("两份 int8 实现的类别不一致: %r/%r"
                 % (a.get("argmax_ref_vs_epv"), a.get("nsamp")))
    if a.get("argmax_ref_vs_py") != a.get("nsamp"):
        p.append("硬件与 python 侧 argmax 不一致: %r/%r"
                 % (a.get("argmax_ref_vs_py"), a.get("nsamp")))
    if a.get("verdict") != "PASS":
        p.append("运行判定不是 PASS: %r" % a.get("verdict"))
    # 此不变式抓过真 bug: 上一版 epv_svb 最后一组越界踩坏计时静态量,
    # EPV_INST=509,188,055 > 退休总数 40,645,880(物理不可能, 台账第 22 条);
    # 修越界后 EPV_INST=1,434,528, 此检查继续挡同类越界。
    if a.get("epv_instret") is not None and a.get("total_retired") is not None \
            and a["epv_instret"] > a["total_retired"]:
        p.append("EPV_INST %r 大于本次运行的退休总数 %r: 读数不可能, 先怀疑用例"
                 "自己踩了内存, 不要拿它算 CPI 或指令数比"
                 % (a["epv_instret"], a["total_retired"]))
    elif a.get("epv_instret") and a.get("ref_instret") \
            and a["epv_instret"] >= a["ref_instret"]:
        p.append("EPV 臂指令数 %r 不小于基线臂 %r: 与扩展的立意相反, 读数自洽性断了"
                 % (a["epv_instret"], a["ref_instret"]))
    # 周期一侧的自洽: 两臂之和不得超过测试台数的总周期(它含启动与 UART 输出, 只会更大)
    if a.get("total_cycles") is not None and a.get("ref_cycles") is not None:
        s = a["ref_cycles"] + a["epv_cycles"]
        if s > a["total_cycles"]:
            p.append("两臂周期之和 %r 大于本次运行的总周期 %r: 计时被重复计入"
                     % (s, a["total_cycles"]))
    if a.get("top1_ref_in_sim") is None or \
            a.get("top1_ref_in_sim") > a.get("nsamp", 0):
        p.append("top-1 命中数 %r 超出样本数 %r 或为空" % (a.get("top1_ref_in_sim"),
                                                       a.get("nsamp")))
    return p


def build():
    for f in (SIM_ARC, HDR):
        if not os.path.isfile(f):
            raise SystemExit("SETUP-FAIL 缺件: %s" % f)
    r = json.load(io.open(SIM_ARC, encoding="utf-8"))
    txt = r["uart"].replace("\r\n", "\n")
    kv = parse_uart(txt)
    sh = shape_from_header()
    me, mi = expected_macs(sh)
    nsamp = kv["NSAMP"]
    if nsamp != sh["CIF_NSIM"]:
        raise SystemExit("SETUP-FAIL: 运行报了 %d 张图, 而 cif_int8.h 里是 %d 张"
                         % (nsamp, sh["CIF_NSIM"]))
    acc = None
    if os.path.isfile(ACC):
        acc = json.load(io.open(ACC, encoding="utf-8"))

    a = {
        "benchmark": "cifar10_net: CIFAR-10 训练权重的 INT8 网络, 在 EPV32-C5 上"
                     "周期精确仿真; 基线=同算式的标量 C, 第三方=导出脚本的 python 侧",
        "run_archive": SIM_ARC.replace("\\", "/"),
        "source": "sw/bench/nn_cifar.c",
        "source_sha256": sha256("sw/bench/nn_cifar.c"),
        "weights_header": HDR.replace("\\", "/"),
        "weights_header_sha256": sha256(HDR),
        "shapes": sh,
        "nsamp": nsamp,
        "macs_exec": kv["MACS_EXEC"],
        "macs_info": kv["MACS_INFO"],
        "macs_exec_computed": me,
        "macs_info_computed": mi,
        "lane_efficiency_pct": round(100.0 * kv["MACS_INFO"] / kv["MACS_EXEC"], 2),
        "ref_cycles": kv["REF_CYC"], "epv_cycles": kv["EPV_CYC"],
        "ref_instret": kv["REF_INST"], "epv_instret": kv["EPV_INST"],
        "speedup_x1000": kv["SPEEDUP_X1000"],
        "speedup": round(kv["SPEEDUP_X1000"] / 1000.0, 3),
        # CPI 与指令数比曾被误诊撤下(真因是越界踩坏静态量, 非 minstret 不可信,
        # 台账第 22 条); 现放回但要求自洽: epv_instret > total_retired 或 >= ref_instret 即红。
        "inst_ratio_x1000": round(kv["REF_INST"] * 1000.0 / kv["EPV_INST"]),
        "epv_cpi": round(kv["EPV_CYC"] / float(kv["EPV_INST"]), 3),
        "ref_cpi": round(kv["REF_CYC"] / float(kv["REF_INST"]), 3),
        "cyc_per_mac_exec_ref": kv["CYC_PER_MAC_EXEC_REF_X100"] / 100.0,
        "cyc_per_mac_exec_epv": kv["CYC_PER_MAC_EXEC_EPV_X100"] / 100.0,
        "layer_mismatches": kv["LAYER_MISMATCH"],
        "py_layer_mismatches": kv["PY_LAYER_MISMATCH"],
        "py_logit_mismatches": kv["PY_LOGIT_MISMATCH"],
        "bit_exact_ref_vs_epv": kv["LAYER_MISMATCH"] == 0,
        "bit_exact_ref_vs_python": (kv["PY_LAYER_MISMATCH"] == 0
                                    and kv["PY_LOGIT_MISMATCH"] == 0),
        "top1_ref_in_sim": kv["TOP1_REF"], "top1_epv_in_sim": kv["TOP1_EPV"],
        "argmax_ref_vs_py": kv["ARGMAX_REF_VS_PY"],
        "argmax_ref_vs_epv": kv["ARGMAX_REF_VS_EPV"],
        "shifts": [sh["CIF_S1"], sh["CIF_S2"], sh["CIF_SF"]],
        "verdict": r.get("verdict"), "epv_exit_code": r.get("commit_val"),
        "total_cycles": r.get("cycles"), "total_retired": r.get("retired"),
        "wall_s": r.get("wall_s"),
        "toolchain": {
            "simulator": ver(os.path.join(TC, "verilator_bin.exe"), "--version"),
            "cc": ver(os.path.join(TC, "riscv64-unknown-elf-gcc.exe"), "--version"),
            "march": "rv32im_zicsr_zifencei", "mabi": "ilp32",
            "opt": "-O2 -nostdlib -lgcc",
        },
        "rtl_sha256": r.get("rtl_sha256"),
        "sim_sha256": r.get("sim_sha256"),
        "build_inputs": r.get("build_inputs"),
        "test_set_accuracy": None,
        "caveats": [
            "存储为单周期理想模型(无 cache): 这里的每 MAC 周期是计算受限下界",
            "top-1 只在这份归档的 %d 张真实测试图上算; 全测试集精度见 %s" % (
                nsamp, ACC.replace("\\", "/")),
            "第一层 3/16 lane 携带像素(CIFAR 只有 3 通道), 浪费按 lane_efficiency_pct 报, 不折进加速比",
            "无实物板; 未做 FPGA 上电验证",
        ],
    }
    if acc:
        a["test_set_accuracy"] = {
            "top1_int8": acc.get("top1_int8"),
            "top1_float": acc.get("top1_float_torch"),
            "quantization_loss_pt": acc.get("quantization_loss_pt"),
            "test_images": acc.get("test_images"),
            "dataset_tarball_sha256": acc.get("dataset_tarball_sha256"),
        }
    pr = problems(a)
    a["gate_problems"] = pr
    a["gate"] = "PASS" if not pr else "FAIL"
    write_json_atomic(OUT, a)
    print("CIFAR_ARCHIVE_OK gate=%s speedup=%s bit_exact=%s top1=%s/%s "
          "mac_exec=%s lane_eff=%s%% problems=%d"
          % (a["gate"], a["speedup"], a["bit_exact_ref_vs_epv"],
             a["top1_ref_in_sim"], a["nsamp"], a["macs_exec"],
             a["lane_efficiency_pct"], len(pr)))
    for x in pr:
        print("  PROBLEM:", x)
    print("WROTE", OUT.replace("\\", "/"))
    return 0 if a["gate"] == "PASS" else 1


def selftest():
    """注入证明 problems() 会咬, 每条都要说出它是哪一条开的火。"""
    base = {
        "nsamp": 24, "macs_exec": 2879232, "macs_info": 1261824,
        "macs_exec_computed": 2879232, "macs_info_computed": 1261824,
        "ref_cycles": 28000000, "epv_cycles": 2400000,
        "speedup_x1000": 11666, "speedup": 11.666,
        "layer_mismatches": 0, "py_layer_mismatches": 0,
        "py_logit_mismatches": 0, "argmax_ref_vs_epv": 24,
        "argmax_ref_vs_py": 24, "verdict": "PASS", "top1_ref_in_sim": 7,
        "ref_instret": 38926383, "epv_instret": 380000,
        "total_retired": 40645880, "total_cycles": 47587127,
    }
    cases = [
        ("clean", {}, []),
        ("逐位相同坏了", {"layer_mismatches": 3}, ["逐层"]),
        ("python 侧对不上", {"py_logit_mismatches": 5}, ["logits"]),
        ("分母为 0", {"nsamp": 0}, ["样本数为"]),
        ("打印的 MAC 与形状现算不符", {"macs_exec": 1}, ["MACS_EXEC 打印"]),
        ("加速比被抬高", {"speedup": 99.0}, ["与定点读数"]),
        ("类别不一致", {"argmax_ref_vs_py": 23}, ["argmax 不一致"]),
        ("判定不是 PASS", {"verdict": "TIMEOUT"}, ["不是 PASS"]),
        # 读数自洽的两条: 超过退休总数(越界会触发), 或不大于基线臂却反超立意。
        ("EPV 指令数超过退休总数",
         {"epv_instret": 509188055, "total_retired": 40645880},
         ["大于本次运行的退休总数"]),
        ("EPV 指令数反超基线臂",
         {"epv_instret": 39926383, "ref_instret": 38926383,
          "total_retired": 40645880},
         ["不小于基线臂"]),
        ("两臂周期之和超过总周期",
         {"ref_cycles": 40000000, "epv_cycles": 24000000,
          "speedup_x1000": 1666, "speedup": 1.666},
         ["计时被重复计入"]),
        ("top-1 超出样本数", {"top1_ref_in_sim": 99}, ["超出样本数"]),
    ]
    bad = 0
    for tag, patch, want in cases:
        d = dict(base)
        d.update(patch)
        pr = problems(d)
        if tag == "clean":
            if pr:
                bad += 1
                print("SELFTEST-FAIL clean 却在开火:", pr)
            else:
                print("SELFTEST-OK clean 无问题(且它下面每条都能单独开火)")
            continue
        hit = [x for x in pr if any(w in x for w in want)]
        if not hit:
            bad += 1
            print("SELFTEST-FAIL %-28s 期望 %s 开火, 实际 %s" % (tag, want, pr))
        else:
            print("SELFTEST-OK %-28s -> %s" % (tag, hit[0][:70]))
    # 条数由夹具自己数：文档里那句「N 条注入」以前是手抄的，加一条夹具没人回头改文档。
    print("CIFAR_ARCHIVE_SELFTEST_%s (cases=%d injections=%d bad=%d)"
          % ("OK" if not bad else "FAIL", len(cases),
             sum(1 for c in cases if c[0] != "clean"), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(build())
