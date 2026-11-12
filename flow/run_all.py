#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flow/run_all.py -- EPV32 的一键认证矩阵

设计约束（每一条都对应本机真实踩过的坑）:
  1. 每一步既跑命令, 也**断言归档里的字段**。命令退 0 而不产出预期字段, 判红。
  2. 结果只在全部跑完后落 results/matrix.json; 跑动中读到的是上一趟的结论,
     所以矩阵自带 started_utc 与 steps 列表, 判绿必须核对它们。
  3. 文档门: 正文里出现的每个 `results/...` 路径必须真的存在; 正文引用的关键
     数字必须由归档**现算**出来再比对, 期望值不取自正文里那串字面量。
  4. 步骤清单是数据不是硬编码计数: 报告里若写「N 步」, 由本脚本自己数并回填。
"""
import io
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import hashlib
from atomic_json import write_json_atomic   # 归档落盘的唯一实现
import rt as RT                             # sha16: 源码指纹的唯一定义
import sim_fingerprint                      # 仿真器二进制的输入指纹（build_sim 判据）
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"   # 同上，但管到本表 spawn 的每个子进程  # 交付包内不留 __pycache__（flow/check_package_hygiene.py 判红）

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
PY = sys.executable
TC = r"E:\msys64\ucrt64\bin"
# Windows 端的 MSYS2 Bash 不在默认 PATH 中；使用绝对路径避免
# subprocess 把 `bash` 解析失败为 [WinError 2]。在类 Unix 环境保留
# `bash` 回退，脚本仍可由现有 CI/评审机直接运行。
if os.name == "nt":
    _BASH_CANDIDATES = [
        os.path.join(os.path.dirname(TC), "usr", "bin", "bash.exe"),
        os.path.join(os.environ.get("MSYS2_ROOT", r"E:\msys64"), "usr", "bin", "bash.exe"),
    ]
    BASH = next((p for p in _BASH_CANDIDATES if os.path.isfile(p)), "bash")
else:
    BASH = "bash"
REPORT = os.path.join("docs", "参赛项目设计报告.md")


def run(cmd, timeout=1800, extra_env=None):
    env = dict(os.environ)
    env["PATH"] = TC + os.pathsep + env.get("PATH", "")
    if extra_env:
        env.update(extra_env)
    p = subprocess.run(cmd, env=env, capture_output=True, timeout=timeout)
    out = p.stdout.decode("utf-8", "replace")
    # rc==0 时一个字都不加（各步的正则与末行解析因此逐字不变）；rc!=0 时把 stderr
    # 尾巴附回，否则失败子跑的真因只活在父进程丢掉的那条通道里（实测: rand_trace
    # 一趟 rc=1，stdout 停在 13 行 AGREE 之后，既没有末行也没有 traceback，
    # 因为 capture_output 收下的 stderr 被 return 直接扔了）。
    if p.returncode != 0:
        err = p.stderr.decode("utf-8", "replace").strip()
        if err:
            out = out + "\n[RUN-STDERR] " + err[-2000:]
    return p.returncode, out


def jload(p):
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------------ 步骤
def s_lint(ctx):
    rc, out = run([BASH, "flow/lint_rtl.sh", "--quiet"])
    m = dict(re.findall(r"(LINT_CORE_RC|LINT_NOEPV_RC)=(\d+)", out))
    assert m.get("LINT_CORE_RC") == "0", "lint core rc=%r out=%s" % (m.get("LINT_CORE_RC"), out[-400:])
    assert m.get("LINT_NOEPV_RC") == "0", "lint noepv rc=%r" % m.get("LINT_NOEPV_RC")
    return m


def s_build(ctx):
    """判据是「这个二进制由现在这几份源编出来」，用输入指纹，不用 mtime。

    为什么把 mtime 判据换掉（两条都是实测，不是假想）：
      * 假红：flow/rand_trace.py 的 A/B 与 flow/assert_selfcheck.py 都会把源文件
        **按原内容**重写一遍再重编，restore() 之后内容逐字节相同而 mtime 更新
        （A/B 末行自己印 restored_equal=True）。原来的 `exe mtime >= 最新 RTL mtime`
        于是必然判红，而且只要那两个自检跑过，下一趟还是红 —— 一道按构造过不去的
        门。本轮 12:18 那一趟红在这里，而盘上那份 exe 恰恰就是新逻辑。
      * 假绿：Verilator `--build` 底下的 make 对「.v 比 exe 新 10 秒」没有重链
        （同一趟里 exe 时间戳原地不动）。mtime 排序挡不住「没重编」，只挡得住
        「时间戳没排对」。
    换成内容指纹后要重编就得**整目录**重编（FORCE=1），理由同上那条假绿。
    而**每一趟都重编**，因为链接本身不可复现：本轮实测同一份源、清空目录连编两次
    得到 5854885c2aab 与 3ba2296a5269 两个不同的 exe 哈希（成本实测 9 秒），所以
    「sha 相等」既不能证明新也不能证明旧，sim_sha256 只做来历注记（全仓库没有一道
    门拿它当判据）。于是唯一站得住的说法是「这颗二进制是本趟从这几份源全量编出来
    的」，编完立刻记侧车、记完再核一遍。
    """
    exe = os.path.join("build", "sim", "epv32sim.exe")
    rc, out = run([BASH, "flow/build_sim.sh"], timeout=1800,
                  extra_env={"FORCE": "1"})
    assert os.path.isfile(exe), "仿真器产物不存在 rc=%d" % rc
    # BUILD_SIM_OK 那行字只证明脚本走到底；产物是不是**这一趟**写的由侧车说。
    assert "BUILD_SIM_OK" in out, "构建没有打印 BUILD_SIM_OK 末行 rc=%d out=%s" % (
        rc, out[-500:])
    rec = sim_fingerprint.write_sidecar("cert")     # 记完再核，不核不判绿
    pl = sim_fingerprint.plan("cert")
    assert not pl["stale"], "记完仍不合: %s recorded=%s now=%s" % (
        pl["reasons"], str(pl["recorded"])[:12], pl["fingerprint"][:12])
    assert rec["exe_sha256"] == pl["exe_sha256"], "侧车与盘上 exe 不是同一颗"
    assert pl["n_sources"] >= 5 and rec["n_sources"] == pl["n_sources"], \
        "输入源集合只有 %s 份，本步会无条件通过" % pl["n_sources"]
    return {"n_sources": pl["n_sources"],
            "fp12": pl["fingerprint"][:12],
            "clean_rebuild": True,
            "sim_sha16": pl["exe_sha256"][:16],
            "exe_mtime": int(os.path.getmtime(exe))}


def _bench(ctx, src, need_fields=()):
    name = os.path.splitext(os.path.basename(src))[0]
    p = os.path.join("results", "sim", name + ".json")
    before = os.path.getmtime(p) if os.path.isfile(p) else 0.0
    rc, out = run([PY, "flow/rt.py", "bench", src, "--max-cyc", "4000000"])
    # 三层新鲜度，缺一层就会把旧结论认证成新的（今天真实栽在这一条上：
    # epv_selftest.c 有四处 epv_lvb 少写参数、编译报错，而这一格连着三轮绿 ——
    # 因为本步原来只核「归档在不在」，盘上那份属于 15:25 的旧 ELF）：
    #   1) 这一步的退出码；2) 归档确实被这一趟写过；3) 归档里记的源码指纹
    #      == 工作树现在的源码。指纹算法只在 rt.sha16 一处定义。
    assert rc == 0, "%s 运行失败 rc=%d out=%s" % (name, rc, out[-700:])
    assert os.path.isfile(p), "%s 没有产出归档 (rc=%d)" % (name, rc)
    assert os.path.getmtime(p) > before, \
        "%s 的归档没被这一趟写过（mtime %s 未前进）—— 读它等于读上一趟" % (name, before)
    r = jload(p)
    assert r["retired"] > 100, "%s 退休指令数=%r, 程序没真跑" % (name, r.get("retired"))
    assert r["verdict"] == "PASS", "%s verdict=%r code=%r uart=%r" % (
        name, r["verdict"], r.get("commit_val"), r.get("uart", "")[:160])
    rel = src.replace(os.sep, "/")
    rec = (r.get("build_inputs") or {}).get(rel)
    cur = RT.sha16(src)
    assert rec == cur, ("%s 归档里的源码指纹与当前工作树不符: 归档 %r 当前 %r —— "
                        "这份证据不是现在这份代码产出的" % (name, rec, cur))
    for k in need_fields:
        assert r.get(k) not in (None, ""), "%s 缺字段 %s" % (name, k)
    return {"cycles": r["cycles"], "retired": r["retired"], "verdict": r["verdict"],
            "src_sha": cur}


def s_iss(ctx):
    """黄金 ISS 自己的断言数 —— 正文与 PPT 里那句"ISS 通过 N 条自身断言"的出处。
    判绿看退出码; N 只从那一行读数里取, 取不到就红(不许回落到正文里那串字面量)。"""
    iss = os.path.join("tb", "golden", "epv_iss.py")
    rc, out = run([PY, iss, "--selftest"], timeout=600)
    assert rc == 0, "ISS 自检 rc=%d out=%s" % (rc, out[-600:])
    m = re.search(r"self test: (\d+) assertions passed, (\d+) failed", out)
    assert m, "ISS 自检没打印预期的读数行, 本门无从判断: %s" % out[-300:]
    n, failed = int(m.group(1)), int(m.group(2))
    assert failed == 0 and n > 0, "ISS 自检 failed=%d n=%d" % (failed, n)
    d = os.path.join("results", "iss")
    if not os.path.isdir(d):
        os.makedirs(d)
    write_json_atomic(os.path.join(d, "selftest.json"),
        {"assertions": n, "failed": failed, "rc": rc,
         "source": iss.replace(os.sep, "/"),
         "source_sha256_head": hashlib.sha256(
             open(iss, "rb").read()).hexdigest()[:16]})
    return {"assertions": n, "failed": failed}


def isco_fresh(d):
    """这份轨迹比对归档是不是拿**当前** RTL 跑的。挑出来写成函数, 是为了能给
    它配一条 A/B: 哈希改一位必须被拒, 否则这个新鲜度键是装饰。"""
    src = d.get("sources") or {}
    cur = rtl_hashes()
    assert src.get("rtl_sha256") == cur, \
        ("轨迹比对归档的核侧哈希与当前工作树不符 (归档记 epv32_core.v=%s, 现在=%s) "
         "—— 重跑 python flow/iss_compare.py <suite>"
         % (str((src.get("rtl_sha256") or {}).get("epv32_core.v", ""))[:12],
            cur["epv32_core.v"][:12]))
    iss = os.path.join("tb", "golden", "epv_iss.py")
    now = hashlib.sha256(open(iss, "rb").read()).hexdigest()
    assert src.get("golden_iss_sha256") == now, \
        "轨迹比对归档里的黄金模型哈希过期了"
    return True


def s_isco(ctx):
    """黄金模型 ↔ 核 的**逐条退休轨迹**比对。两套件每个程序的提交流都要一字节不差,
    并且同格先跑一遍比对器自己的注入 —— 看不见分歧的比对器等于没有。
    这一格不是「测试过了」的另一种说法: 合规测试判的是 tohost, 本格判的是
    每条退休指令的 (pc, rd, wdata), 而且两侧源码都被哈希钉住。"""
    rc0, out0 = run([PY, os.path.join("flow", "iss_compare.py"), "--selftest"],
                    timeout=900)
    assert rc0 == 0 and "ISCO_SELFTEST_OK" in out0, \
        "轨迹比对器的注入自检没报红/没报成: 它对分歧是盲的. %s" % out0[-500:]
    read, tot, names = {}, 0, []
    for suite in ("rv32um", "rv32ui", "rv32mi"):
        run([PY, os.path.join("flow", "iss_compare.py"), suite], timeout=3600)
        d = jload(os.path.join("results", "isco", suite + ".json"))
        comp = jload(os.path.join("results", "compliance", suite + ".json"))
        assert d["n_tests"] == comp["n_tests"] and d["n_tests"] > 0, \
            "%s: 轨迹比对跑了 %d 个程序, 合规跑的是 %d —— 分母不一样" % (
                suite, d["n_tests"], comp["n_tests"])
        isco_fresh(d)
        bad = {r["test"] for r in d["rows"] if not r["agree"]}
        # 分歧清单与失败清单一样双向核：多一个分歧 = 模型与核又对不上了；
        # 少一个 = 那条已经修好而清单没销账。
        exp = ISCO_KNOWN_DIVERGE.get(suite, set())
        assert bad == exp, "%s: 分歧集合与清单不符 新增=%s 已修好未销账=%s" % (
            suite, sorted(bad - exp), sorted(exp - bad))
        assert d["n_agree"] == d["n_tests"] - len(exp), \
            "%s: 一致数 %d 与 分母-清单 %d 不符" % (
                suite, d["n_agree"], d["n_tests"] - len(exp))
        # A/B: 哈希改一位, 同一道核对必须拒绝采信这份归档。
        stale = json.loads(json.dumps(d))
        h = stale["sources"]["rtl_sha256"]["epv32_core.v"]
        stale["sources"]["rtl_sha256"]["epv32_core.v"] = \
            h[:-1] + ("1" if h[-1] != "1" else "2")
        try:
            isco_fresh(stale)
        except AssertionError:
            pass
        else:
            raise AssertionError("%s: 扰动过的归档仍然通过新鲜度核对 —— 键不咬" % suite)
        tot += sum(r["rtl_commits"] for r in d["rows"])
        read[suite] = "%d/%d" % (d["n_agree"], d["n_tests"])
        names += [r["test"] for r in d["rows"]]
    assert len(set(names)) == sum(int(v.split("/")[1]) for v in read.values()), \
        "各套件的程序名有重叠, 分母是假的"
    return {"suites": read, "compared_commits": tot,
            "diverging": sorted({t for s in ISCO_KNOWN_DIVERGE
                                 for t in ISCO_KNOWN_DIVERGE[s]}),
            "inject": "3 条 (wdata 扰动 / 少一条退休 / 正对照) + 1 条哈希扰动 A/B"}


def net_problems(n):
    """网络级归档的判据集合 —— 写成函数是为了能给它的每一条配 A/B 注入:
    「判据只在归档真坏时报红」这件事, 不看代码信不过。"""
    p = []
    if n.get("verdict") != "PASS":
        p.append("运行判决不是 PASS: %r" % n.get("verdict"))
    if n.get("commit_val") != 0:
        p.append("用例返回码非 0: %r" % n.get("commit_val"))
    if n.get("layer_mismatches") != 0 or n.get("logit_mismatches") != 0:
        p.append("两份 int8 实现没有逐层逐位相同 (层 %r / logits %r)"
                 % (n.get("layer_mismatches"), n.get("logit_mismatches")))
    if not n.get("bit_exact_per_layer"):
        p.append("归档自己说没做到逐位相同")
    if n.get("samples", 0) <= 0:
        p.append("样本数为 %r —— 分母不能为零" % n.get("samples"))
    if n.get("argmax_int8_vs_epv") != n.get("samples"):
        p.append("两份 int8 的 argmax 有样本不一致 (%r/%r)"
                 % (n.get("argmax_int8_vs_epv"), n.get("samples")))
    pl = n.get("per_layer_cycles", {})
    rs = sum(v.get("ref", 0) for v in pl.values())
    es = sum(v.get("epv", 0) for v in pl.values())
    if rs != n.get("ref_cycles"):
        p.append("基线整网周期 %r != 三层之和 %r" % (n.get("ref_cycles"), rs))
    if es != n.get("epv_cycles"):
        p.append("EPV 整网周期 %r != 三层之和 %r" % (n.get("epv_cycles"), es))
    if n.get("macs_total") != n.get("macs_per_sample", 0) * n.get("samples", 0):
        p.append("MAC 总数与「每样本 × 样本数」不符")
    sp = round(n.get("speedup_x1000", 0) / 1000.0, 3)
    if abs(sp - n.get("speedup", -1)) > 0.001:
        p.append("加速比 %r 与定点读数 %r 不一致" % (n.get("speedup"), sp))
    rec = n.get("ref_cycles", 0) / float(n.get("epv_cycles", 1) or 1)
    if abs(rec - n.get("speedup", -1)) > 0.01:
        p.append("加速比 %r 与两侧周期现算 %r 差太多" % (n.get("speedup"), rec))
    if n.get("self_consistency", {}).get("ref_total_equals_layer_sum") is not True:
        p.append("归档自己的自洽核对没通过")
    return p


def s_nnnet(ctx):
    """端到端网络级 INT8 推理: 跑 -> 汇综 -> 判据只读归档字段。
    与 nn_bench 的分工: 那份量一个卷积核, 这份量整张网络 (两层卷积 + 池化 + FC),
    而且比对是**逐层**做的 —— 只比末端会把「中间层算歪又被下层掩盖」读成通过。"""
    rc, out = run([PY, os.path.join("flow", "rt.py"), "bench",
                   os.path.join("sw", "bench", "nn_net.c"),
                   "--max-cyc", "12000000"], timeout=1800)
    assert rc == 0, "网络级用例没跑成: %s" % out[-400:]
    rc2, out2 = run([PY, os.path.join("tools", "net_archive.py")], timeout=300)
    assert rc2 == 0 and "NET_ARCHIVE_OK" in out2, \
        "网络读数汇综失败: %s" % out2[-400:]
    n = jload(os.path.join("results", "bench", "nn_net.json"))
    problems = net_problems(n)
    assert not problems, "网络级归档不过: %s" % problems
    assert n["rtl_sha256"] == rtl_hashes(), \
        "网络归档不是当前 RTL 跑的 (记 epv32_core.v=%s, 现在=%s)" % (
            n["rtl_sha256"]["epv32_core.v"][:12], rtl_hashes()["epv32_core.v"][:12])
    rc3, out3 = run([PY, os.path.join("tools", "net_archive.py"), "--selftest"],
                    timeout=600)
    assert rc3 == 0 and "NET_ARCHIVE_SELFTEST_OK" in out3, \
        "汇综工具的自洽核对不会被注入咬红 —— 那三条等式是装饰: %s" % out3[-300:]
    # A/B: 把 layer_mismatches 从 0 改成 3, 同一道判据必须报出它是哪一条
    bad = json.loads(json.dumps(n))
    bad["layer_mismatches"] = 3
    assert any("逐层" in x for x in net_problems(bad)), \
        "逐位相同这条判据不咬: 改坏了归档却没人报红"
    bad2 = json.loads(json.dumps(n))
    bad2["samples"] = 0
    assert any("样本数" in x for x in net_problems(bad2)), \
        "分母为 0 的归档能被采信"
    return {"speedup": n["speedup"], "bit_exact_per_layer": n["bit_exact_per_layer"],
            "samples": n["samples"], "macs": n["macs_total"],
            "cyc_per_mac": [n["cyc_per_mac_ref"], n["cyc_per_mac_epv"]]}


def s_cifar(ctx):
    """真实数据集那条链: 训练产出的权重在环跑, 三条实现互核, 判据只读归档字段。
    与 nn_net 的分工: nn_net 的合成权重网络不给精度, 这一格给; 两者的数不许互相顶替,
    所以归档也各一份 (results/bench/cifar10_net.json)。"""
    rc, out = run([PY, os.path.join("flow", "rt.py"), "bench",
                   os.path.join("sw", "bench", "nn_cifar.c"),
                   "--max-cyc", "80000000"], timeout=2400)
    assert rc == 0, "CIFAR-10 在环用例没跑成: %s" % out[-400:]
    rc2, out2 = run([PY, os.path.join("tools", "cifar_archive.py")], timeout=600)
    c = jload(os.path.join("results", "bench", "cifar10_net.json"))
    assert "CIFAR_ARCHIVE_OK" in out2, "CIFAR 读数汇综失败: %s" % out2[-400:]
    assert c["gate"] == "PASS", "CIFAR 归档不过: %s" % c["gate_problems"]
    assert c["rtl_sha256"] == rtl_hashes(), \
        "CIFAR 归档不是当前 RTL 跑的 (记 %s, 现在 %s)" % (
            c["rtl_sha256"]["epv32_core.v"][:12], rtl_hashes()["epv32_core.v"][:12])
    # 退休读数的自洽性由归档工具自己管(它抓出过一次越界); 这里只复核它确实跑过。
    assert c.get("epv_instret", 0) <= c.get("total_retired", -1), \
        "EPV 指令数大于退休总数: 越界类缺陷回来了"
    rc3, out3 = run([PY, os.path.join("tools", "cifar_archive.py"), "--selftest"],
                    timeout=600)
    assert rc3 == 0 and "CIFAR_ARCHIVE_SELFTEST_OK" in out3, \
        "CIFAR 汇综的注入自检不咬: %s" % out3[-300:]
    return {"speedup": c["speedup"], "nsamp": c["nsamp"],
            "top1_in_sim": [c["top1_ref_in_sim"], c["top1_epv_in_sim"]],
            "lane_efficiency_pct": c["lane_efficiency_pct"],
            "bit_exact_three_ways": bool(c["bit_exact_ref_vs_epv"]
                                          and c["bit_exact_ref_vs_python"]),
            "test_set_top1": (c.get("test_set_accuracy") or {}).get("top1_int8")}


def s_memsens(ctx):
    """访存代价敏感性: 正文 3.4 那条「恒 1 周期就绪」的上界假设量成曲线。
    分工: 计时步给的是「这台机器上核跑多快」, 这一格给的是「这个数字里有多少
    来自理想存储」。它不重新计时, 只把已归档的两臂周期/退休数与退休轨迹里
    窗口内的实测字数喂进模型, 所以排在三个 bench 之后。
    判决取归档字段 gate, 不取 stdout 字样; 曲线只有一条用例出得来 (另两条的
    计时窗口被逐层/逐图读数切碎), 所以还核一条「至少一条曲线」而不是「三条」。"""
    rc, out = run([PY, os.path.join("tools", "mem_sensitivity.py")], timeout=1800)
    m = jload(os.path.join("results", "bench", "mem_sensitivity.json"))
    assert "MEMSENS gate=" in out, "敏感性工具没出判决行: %s" % out[-400:]
    assert rc == 0 and m["gate"] == "PASS", "敏感性曲线不过: %s" % m["gate_problems"]
    assert m["gate_cases"] >= 1, "一条曲线都没有: 分母为零的 PASS 不算数"
    assert m["unavailable"], "两条切碎窗口的用例本该报不可用却没报: %s" % m["unavailable"]
    c = m["cases"][0]
    assert c["zero_point_ok"], "W=0 那点没对上归档加速比: 模型一上来就先错"
    rc2, out2 = run([PY, os.path.join("tools", "mem_sensitivity.py"), "--selftest"],
                    timeout=600)
    assert rc2 == 0 and "MEMSENS_SELFTEST_OK" in out2, \
        "敏感性判据的注入自检不咬: %s" % out2[-300:]
    pes = dict((r["wait_states"], r["speedup_pessimistic"])
               for r in c["curve_data_only"])
    return {"case": c["case"], "words_ref_data": c["words_ref_data"],
            "words_epv_data_range": c["words_epv_data"],
            "speedup_w0": c["curve_data_only"][0]["speedup_at_epv_lo"],
            "pessimistic_w4_w16": [pes[4], pes[16]],
            "curves": len(m["cases"]), "unavailable": len(m["unavailable"])}


def step_claims(doc):
    """把正文/README 里所有「N 步」这类派生计数抽成 (数, 句式)。
    句式只有两类各有各的真值: 「N 步认证矩阵/全绿/自动跑/产证据」指整表格数,
    「前 N 步」指后置格之前, 「第 N 步」是序数指代(只要求在范围内)。"""
    out = []
    # 「前 22 步产证据」同时含「前 N 步」与「N 步产证据」两个形状, 两类句式会在同一句
    # 话上重叠(实测: 光用否定环视会被「22」的第二位绕过去, 把 2 读成总步数, 于是本表
    # 23 步却被判成"文档写了 2 步")。所以先把前/序两类整段抹掉, 再在剩下的文本上找
    # 总步数那条。
    spans = []
    for pat, kind in ((u"前\\s*(\\d+)\\s*步", u"前"), (u"第\\s*(\\d+)\\s*步", u"序")):
        for m in re.finditer(pat, doc):
            out.append((int(m.group(1)), kind))
            spans.append(m.span())
    masked = list(doc)
    for a, b in spans:
        for i in range(a, b):
            masked[i] = u"#"
    md = u"".join(masked)
    for m in re.finditer(u"(\\d+)\\s*步(?:认证矩阵|全绿|自动跑|产证据)", md):
        out.append((int(m.group(1)), u"全绿"))
    for m in re.finditer(u"(?:跑完|整表|共)\\s*(\\d+)\\s*步", doc):
        out.append((int(m.group(1)), u"全绿"))
    return out


def s_wordgate(ctx):
    """各节字数上限取自查赛题原文; 先核真实文档, 再跑 9 条端点+注入证明它会咬。

    这一格同时是 Word「字数」口径那个第二生产者的主人：`tools/word_style_count.py`
    以前没有任何一步跑它，于是盘上那份 `results/word_style.json` 比报告源旧了 6.5 小时，
    而 `HISTORICAL_ARCHIVES.json` 还把它登记成 `word_gate` 产的（登记也是错的）。
    现在由本步跑它，并当场核「它不比源旧」——这句话的成立与否由时间戳判，不靠记性。
    """
    rc, out = run([PY, os.path.join("tools", "check_word_limits.py"), "--json"])
    assert rc == 0, "有节出档: %s" % out[-600:]
    rc2, out2 = run([PY, os.path.join("tools", "check_word_limits.py"), "--selftest"])
    assert rc2 == 0 and "WORDLIMIT_SELFTEST_OK" in out2, \
        "字数门自检不报红/报错, 这道门是装饰: rc=%d %s" % (rc2, out2[-500:])
    w = jload(os.path.join("results", "word_limits.json"))
    assert w["all_within"] and len(w["sections"]) == 5, "归档字段不对: %s" % w.keys()
    tight = max(w["sections"], key=lambda s: s["han_chars"] / float(s["limit"]))
    rc3, out3 = run([PY, os.path.join("tools", "word_style_count.py")])
    assert rc3 == 0, "Word 口径那一把没算成 rc=%d %s" % (rc3, out3[-400:])
    srcp = os.path.join("docs", u"参赛项目设计报告.md")
    wsp = os.path.join("results", "word_style.json")
    src_mt, ws_mt = os.path.getmtime(srcp), os.path.getmtime(wsp)
    assert ws_mt >= src_mt - 2, "word_style.json 比报告源旧 (%.0f < %.0f) —— 它没跟着源刷新" % (
        ws_mt, src_mt)
    ws = jload(wsp)
    assert ws.get("over_word_limit") == 0, "Word 口径有节出档: %s" % ws.get("sections")
    return {"sections": len(w["sections"]),
            "tightest": tight["section"],
            "tightest_pct": round(100.0 * tight["han_chars"] / tight["limit"], 1),
            "word_style_over": ws.get("over_word_limit"),
            "word_style_tightest_units": ws.get("tightest_margin_units"),
            "word_style_age_vs_source_sec": round(ws_mt - src_mt, 1)}


def s_ppt(ctx):
    """生成答辩 PPT(每个数字都从归档现读) -> 反向门逐个 token 定档 -> 再跑那条门的
    5 条注入。判绿看退出码; slides/tiers 只是给人看的补充。"""
    rc, out = run([PY, "flow/build_ppt.py"])
    assert rc == 0, "PPT 生成失败 rc=%d: %s" % (rc, out[-500:])
    m = re.search(r"slides=(\d+)", out)
    rc2, out2 = run([PY, "tools/verify_ppt.py", "--json"])
    assert rc2 == 0, "PPT 里有没出处的数字: %s" % out2[-900:]
    rc3, out3 = run([PY, "tools/verify_ppt.py", "--selftest"])
    assert rc3 == 0, "PPT 门的注入不咬: %s" % out3[-500:]
    v = jload(os.path.join("results", "ppt", "verify.json"))
    return {"slides": int(m.group(1)) if m else None, "tokens": v["tokens"],
            "tiers": v["tiers"], "selftest_rc": rc3}


def s_figures(ctx):
    """附图门: 先由本步**重画**（写入者），再核出处、登记簿、报告引用双向差集。

    为什么写入者要在链条里：本轮实测 TD 换代之后归档是新的、盘上那两张图与登记簿
    是旧的，而这一格原来只跑 --check —— 于是链条红在一个「重画不一样」的诊断上，
    红因还指错了地方（说成图里藏了时间戳，其实是图过期）。归档每代都重跑而图不会
    自己长出来，所以「盘上图 == 这一趟画出来的那份」必须由本步构造出来，而不是
    指望有人记得手动重画。只核对的那一发留在链条外：`python flow/make_figures.py
    --check`，它是「登记簿↔磁盘↔重画」三方一致的那道门。
    """
    rcw, outw = run([PY, os.path.join("flow", "make_figures.py")])
    assert rcw == 0 and "FIGURES-BUILT" in outw, \
        "重画附图失败 rc=%d out=%s" % (rcw, outw[-500:])
    rc, out = run([PY, os.path.join("flow", "make_figures.py"), "--check"])
    assert rc == 0 and "FIGURES_CHECK_OK" in out, \
        "附图门红 (出处/登记簿/引用其中一条不成立): %s" % out[-700:]
    # 夹具排在建图之后: make_figures 的 --selftest 走沙盒并回头断言三份归档与
    # registry.json 逐字节未变, 所以它可以在实扫之后再跑 (形状同 s_ppt)。
    rc2, out2 = run([PY, os.path.join("flow", "make_figures.py"), "--selftest"])
    assert rc2 == 0 and "FIGURES_SELFTEST_OK" in out2, \
        "附图门自带夹具判红 rc=%d out=%s" % (rc2, out2[-700:])
    reg = jload(os.path.join("docs", u"附图", "registry.json"))
    assert reg["figures"], "登记簿是空的 —— 这张图门分母为 0"
    return {"figures": len(reg["figures"]),
            "numbers_policed": reg["numbers_on_figures"]}


def s_docx(ctx):
    """报告 .md → 模板排版 .docx, 再打开生成的文件回读排版, 最后跑那条排版门的注入。
    判绿一律看退出码 + 归档式回读, 不看它印了什么。"""
    rc, out = run([PY, "flow/build_docx.py"])
    assert rc == 0, "docx 生成失败 rc=%d: %s" % (rc, out[-400:])
    docx = os.path.join("docs", "参赛项目设计报告.docx")
    assert os.path.isfile(docx) and os.path.getsize(docx) > 20000, \
        "docx 不存在或过小(%s 字节)" % (os.path.getsize(docx) if os.path.isfile(docx) else "无")
    rc2, out2 = run([PY, "flow/verify_docx.py"])
    assert rc2 == 0, "排版回读校验红: %s" % out2[-600:]
    rc3, out3 = run([PY, "flow/verify_docx.py", "--selftest"])
    assert rc3 == 0, "排版门自己不咬: %s" % out3[-600:]
    m = re.search(r"run=(\d+).*?加粗=(\d+).*?层级=\[([0-9, ]+)\].*?封面行=(\d+)", out2, re.S)
    assert m, "校验输出里没有排版读数行, 本步无从记账: %s" % out2[-300:]
    assert int(m.group(2)) == 0, "正文出现加粗(规范要求不加粗): %s" % m.group(2)
    lv = sorted(int(x) for x in m.group(3).split(","))
    assert lv and max(lv) <= 3, "标题层级超过 3 级: %s" % lv
    # 封面的方向标识是一句「官方模板顶部有、我们也得有」的主张, 所以它必须由这一步
    # 现核条数, 不许只在收口表里写成一句话: 期望值来自官方模板原件(verify_docx 里
    # import 的那个读法), 不是来自这里手抄的 2。
    assert int(m.group(4)) > 0, "报告 docx 的封面行数为 0: 官方模板顶部的方向标识没落上"
    mf = re.search(r"DOCX_SELFTEST names=(\d+) fired=(\d+) cover_lines=(\d+)", out3)
    assert mf, "夹具末行读不到, 不许只凭退出码判绿: %s" % out3[-200:]
    assert int(mf.group(1)) == int(mf.group(2)) > 0, \
        "排版夹具没全开火: %s/%s" % (mf.group(2), mf.group(1))
    assert int(mf.group(3)) == int(m.group(4)), \
        "夹具读到的封面行数与校验读到的不等: %s vs %s" % (mf.group(3), m.group(4))

    # 附件 A 那份 docx 也必须由链条产出并核过: 它此前只由人手一句话生成,
    # 「包里有这份 docx」与「链条能长出这份 docx」是两件事(删掉它链条照样绿就是假证据)。
    att_md = os.path.join("docs", "附件A_缺陷与门禁台账.md")
    att_dx = att_md[:-3] + ".docx"
    saved = {k: os.environ.get(k) for k in ("DOCX_SRC", "DOCX_DST")}
    if os.path.isfile(att_dx):
        os.remove(att_dx)                     # 后置条件断言的前提: 必须先没有它
    os.environ["DOCX_SRC"], os.environ["DOCX_DST"] = att_md, att_dx
    try:
        rc4, out4 = run([PY, "flow/build_docx.py"])
        assert rc4 == 0, "附件 docx 生成失败 rc=%d: %s" % (rc4, out4[-400:])
        assert os.path.isfile(att_dx), \
            "子进程没按 DOCX_DST 落附件(链条此前只会重复生成报告那一份)"
        rc5, out5 = run([PY, "flow/verify_docx.py"])
        assert rc5 == 0, "附件排版回读校验红: %s" % out5[-600:]
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    ma = re.search(r"run=(\d+).*?数字锚=(\d+).*?封面行=(\d+)", out5, re.S)
    assert ma and int(ma.group(1)) > 200, "附件排版读数解不到: %s" % out5[-200:]
    assert int(ma.group(3)) == 0, "附件不该有报告封面, 却核到 %s 行" % ma.group(3)
    return {"title_runs": int(m.group(1)), "bold_runs": int(m.group(2)),
            "max_level": max(lv), "selftest_rc": rc3,
            "report_cover_lines": int(m.group(4)), "report_fixtures": mf.group(2),
            "attachment_runs": int(ma.group(1)), "attachment_anchors": int(ma.group(2))}


def s_bisect(ctx):
    return _bench(ctx, os.path.join("sw", "bench", "epv_bisect.c"))


def s_selftest(ctx):
    return _bench(ctx, os.path.join("sw", "bench", "epv_selftest.c"),
                  need_fields=("uart",))


def s_micro(ctx):
    """M9: 计数器写语义 + PMP 强制的逐项探针。

    rv32mi 那两条从 14/16 到 16/16 是靠它**拆开定位**的 —— 官方用例只给一个
    TESTNUM, 而「高位没写进去」「进位没穿过去」「抑制没生效」三种毛病修法互不
    相同。所以这一步必须在表里, 不能只在 README 的复现清单里。
    """
    return _bench(ctx, os.path.join("sw", "bench", "micro_bisect.c"),
                  need_fields=("uart",))


def s_nnbench(ctx):
    ev = _bench(ctx, os.path.join("sw", "bench", "nn_bench.c"))
    rc, out = run([PY, "tools/bench_archive.py"])
    assert "WROTE" in out, "bench_archive 没写文件: %s" % out[-300:]
    b = jload(os.path.join("results", "bench", "nn_bench.json"))
    assert b["bit_exact"] is True, "逐位比对失败 MISMATCH=%r" % b.get("bit_exact_mismatches")
    assert b["macs"] > 0 and b["epv_cycles"] > 0, "基准计数为空"
    assert b["speedup"] > 1.0, "加速比不 >1: %r" % b["speedup"]
    ctx["bench"] = b
    # src_sha 要一起记账：同源门禁核的就是「矩阵记的每个数都有归档可对」，
    # 本步自己组装证据表时把它丢了，那道门就会报「这个数没人作证」。
    return {"speedup": b["speedup"], "mismatch": b["bit_exact_mismatches"],
            "ref_cyc": b["ref_cycles"], "epv_cyc": b["epv_cycles"],
            "src_sha": ev["src_sha"]}


def _suite(ctx, suite):
    rc, out = run([PY, "flow/rt.py", "riscv", suite])
    p = os.path.join("results", "compliance", suite + ".json")
    assert os.path.isfile(p), "%s 无归档 rc=%d" % (suite, rc)
    d = jload(p)
    assert d["n_tests"] > 0, "%s 分母为 0" % suite
    ctx.setdefault("compliance", {})[suite] = d
    return {"suite": suite, "pass": d["n_pass"], "of": d["n_tests"]}


# 已知失败清单要**逐名核对**: 多出名字、少了名字都算红。
# 少了名字 = 又坏了一个用例被当成"本来就这样"; 多了名字 = ma_data 修好了却没
# 更新清单, 于是正文里那句"已知未过"变成假话。两个方向都会响。
# 已知失败清单按名字双向核: 名单里有却过了 = 红 (提醒收账), 名单外却挂了 = 红。
# 2026-09-24: rv32ui/ma_data 由「核按规范陷入」改成「MEM 拆两拍做硬件非对齐访问」
# (rtl/epv32_core.v 的 M6 / SUPPORT_UNALIGNED), 42/42 后本表清空。
# 留空表而不是删掉机制: 下一次挂用例时它照样会咬。
KNOWN_FAILING = {"rv32ui": set(), "rv32um": set(),
                 # M8 及以前: rv32mi 16 项里挂 2 项, 两条都有名字 ——
                 #   pmpaddr          PMP 不在能力清单里 (写被忽略、读回 0、不陷入)
                 #   instret_overflow mcycle/minstret 是只读观察量
                 # M9 把这两条做进了硬件, 于是三套件 66 个程序全过。这张清单同样
                 # 双向核: 还留着这两条名字的话, _check_suite 会报「已修好但清单
                 # 未更新」并判红 —— 销账是强制的, 不是自觉的。
                 "rv32mi": set()}
# 模型 ↔ 核 的分歧清单，与失败清单同样双向核。rv32mi 那一条不是「谁算错了」而是
# 「写入计数器那一拍算不算一条退休指令」——两侧各按自己的读法数，差一拍；
# 这条用例本来就不通过，所以它既在 KNOWN_FAILING 也在这里，两个方向都记着。
# M9 之后清空: 黄金模型补上了「计数器可写 + 写入那一拍抑制自增」与 PMP 寄存器的
# WARL/锁定语义, 于是 rv32mi 的 16 个程序连退休轨迹也与核逐条相同 —— 三套件
# 66 个程序、零已知分歧。这张清单是双向核的: 留着一条早已修好的名字, 这一步
# 就红 (今天就是这样逼着把模型跟上设计, 而不是把分歧合法化)。
ISCO_KNOWN_DIVERGE = {}


def _check_suite(ctx, suite):
    r = _suite(ctx, suite)
    d = ctx["compliance"][suite]
    got = {x["test"] for x in d["rows"] if x["verdict"] != "PASS"}
    exp = KNOWN_FAILING[suite]
    assert got == exp, "%s 失败集合与清单不符: 新增失败=%s 已修好但清单未更新=%s" % (
        suite, sorted(got - exp), sorted(exp - got))
    assert r["pass"] == r["of"] - len(exp), "%s 通过数 %d 与 分母-清单 %d 不符" % (
        suite, r["pass"], r["of"] - len(exp))
    r["known_failing"] = sorted(exp)
    return r


def s_rv32ui(ctx):
    return _check_suite(ctx, "rv32ui")


def s_rv32um(ctx):
    return _check_suite(ctx, "rv32um")


def s_rv32mi(ctx):
    """官方机器模式套件：CSR / 非法指令 / 非对齐 / 取指对齐 / PMP / 计数器。
    这一套件对本案特别值钱 —— M6（硬件非对齐）与 M7（IALIGN=32）的判据
    正是它里面的 lw/sh/sw/lh-misaligned 与 ma_fetch 在直接考。"""
    return _check_suite(ctx, "rv32mi")


def s_randtrace(ctx):
    """约束随机指令流 + 一则定向角例，与黄金 ISS 逐条退休轨迹差分。

    这一格本轮才接进链条：在此之前 `flow/rand_trace.py` 只在盘上留下一份自己写的
    归档，**没有任何一步跑它**（名字与产物都在，主人是 0 —— 那条「README 列了名字
    ≠ 有主人」的老形状）。接上之后它第一轮就砸出一个 RTL 缺陷：`rem` 在被除数为负、
    除数为 0 时给的是 |被除数| 而不是被除数，而官方 rv32um/p-rem 的八个用例不含除零
    角例，所以「rv32um 8/8」覆盖不到它（缺陷、排除面与修法见台账 A.1 第 31 行与
    `rtl/epv32_muldiv.v` 的头注释）。
    第 0 则是**定向**角例而不是随机产物：抓到那一格的是种子 20260926 的第 94 拍，
    但回归不许靠运气，所以那十种形状被钉成一则固定程序（A/B 见台账同一条：把
    那一行改回去，这格必须报 DIVERGE）。
    两通道：rc==0 且末行 `RAND_TRACE_OK`，且逐键取到正数、diverging 必须为 0；
    夹具那一路单独跑一次并要求 done==tot。
    """
    gate = os.path.join("flow", "rand_trace.py")
    rc, out = run([PY, gate])
    m = re.search(r"RAND_TRACE_(OK|FAIL) programs=(\d+) agree=(\d+) diverging=(\d+) "
                  r"retired=(\d+) problems=(\d+)", out)
    assert rc == 0 and m and m.group(1) == "OK", \
        "随机/角例差分与黄金模型不一致 rc=%d out=%s" % (rc, out[-900:])
    vals = {"programs": int(m.group(2)), "agree": int(m.group(3)),
            "diverging": int(m.group(4)), "retired": int(m.group(5)),
            "problems": int(m.group(6))}
    assert vals["programs"] >= 2 and vals["retired"] > 0, \
        "分母塌了（%s 则 / 退休 %d 条），不能读成『零分歧』" % (
            vals["programs"], vals["retired"])
    assert vals["agree"] == vals["programs"] and vals["diverging"] == 0, \
        "判决行自称 OK 而逐键不等: %s" % vals
    rc2, out2 = run([PY, gate, "--selftest"])
    fx = re.search(r"RAND_TRACE_SELFTEST (?:OK|FAIL) (\d+)/(\d+)", out2)
    assert rc2 == 0 and fx and fx.group(1) == fx.group(2), \
        "随机差分门自己的夹具没全过 rc=%d out=%s" % (rc2, out2[-600:])
    return dict(vals, programs_total=vals["programs"],
                fixtures="%s/%s" % (fx.group(1), fx.group(2)), gate_rc=rc)


def s_synthesis(ctx):
    YS = r"D:\yosys_build\bucrt\yosys.exe"
    jobs = [("results/synth/core_epv.txt",
             [YS, "-s", "flow/synth_core_epv.ys"], "epv32_core_epv"),
            ("results/synth/core_noepv.txt",
             [YS, "-s", "flow/synth_core_noepv.ys"], "epv32_core_noepv"),
            ("results/synth/epv_only.txt",
             [YS, "-s", "flow/synth_epv.ys"], "epv32_epv")]
    rtl_newest = max(os.path.getmtime(os.path.join("rtl", f))
                     for f in os.listdir("rtl") if f.endswith((".v", ".vh")))
    for log, cmd, mod in jobs:
        if not os.path.isfile(cmd[-1]):
            raise AssertionError("综合脚本不存在: %s" % cmd[-1])
        rc, out = run(cmd)
        assert rc == 0, "%s 综合失败 rc=%d" % (mod, rc)
        # 关键一步: .ys 脚本自己不落日志 (demo.sh 是靠 shell 重定向写的),
        # 所以这里必须把**本拍 stdout** 写进日志再读它。曾经这里读的是上一趟
        # 留下的旧日志 —— RTL 改了、Yosys 也重跑了, 归档里却还是旧单元数,
        # 而 synth_archive 会给它配上**新**的 RTL 哈希 = 一条假归属。
        with io.open(log, "w", encoding="utf-8") as f:
            f.write(out)
        assert os.path.getmtime(log) >= rtl_newest, \
            "%s 比 RTL 还老, 读它等于读上一趟的结论" % log
        t = io.open(log, encoding="utf-8", errors="replace").read()
        assert ("=== %s ===" % mod) in t, "%s 没打印统计表" % mod
        cells = re.search(r"=== %s ===[\s\S]*?\n\s+(\d+) cells" % mod, t)
        assert cells and int(cells.group(1)) > 50, \
            "%s 单元数可疑(%s) —— 空统计往往是整块逻辑被当死代码删了" % (
                mod, cells.group(1) if cells else "None")
    rc, out = run([PY, "tools/synth_archive.py"])
    assert "WROTE" in out, "synth_archive 未写文件: %s" % out[-300:]
    s = jload(os.path.join("results", "synth", "summary.json"))
    ctx["synth"] = s
    assert s["arms"]["core_with_epv"]["cells"] > s["arms"]["core_no_epv"]["cells"], \
        "含扩展的核不比基线大, 说明扩展根本没进综合"
    assert s["cross_check"]["unit_cells_lte_net_overhead"], "EPV 单独面积 > 净开销, 两档不是同一份 RTL"
    return {"core_epv": s["arms"]["core_with_epv"]["cells"],
            "core_noepv": s["arms"]["core_no_epv"]["cells"],
            "overhead": s["epv_net_overhead_cells"]}


def s_encgate(ctx):
    rc, out = run([PY, "tools/check_encoding_consistency.py"])
    # 判绿只看退出码, 再看那行字: 打印了 OK 而 rc!=0 说明它在报错之前先印了结论
    assert rc == 0, "编码门 rc=%d: %s" % (rc, out[-500:])
    assert "ENCODING_GATE_OK" in out, "编码门未过: %s" % out[-500:]
    rc2, out2 = run([PY, "tools/check_encoding_consistency.py", "--selftest"])
    assert rc2 != 0 and "ENCODING_GATE_FAIL" in out2, \
        "注入自检没有报红 —— 这道门是装饰: %s" % out2[-300:]
    return {"injected_rc": rc2}


# ------------------------------------------------------------------ 器件口径
# 候选归档按"新→旧"排; 选档的唯一依据是它记的 rtl 哈希等于当前工作树。
PNR_CANDIDATES = ("results/pnr/summary2.json", "results/pnr/summary.json")


def rtl_hashes():
    out = {}
    for fn in sorted(os.listdir("rtl")):
        if fn.endswith((".v", ".vh")):
            out[fn] = hashlib.sha256(
                open(os.path.join("rtl", fn), "rb").read()).hexdigest()
    assert out, "rtl/ 里没有源文件"
    return out


def pnr_load(cands=None):
    """挑一份**对应当前 RTL** 的器件口径归档; 挑不出来就红, 不许沿用旧数。"""
    cands = tuple(cands or PNR_CANDIDATES)
    cur = rtl_hashes()
    have = [(rel, jload(rel)) for rel in cands if os.path.isfile(rel)]
    assert have, ("没有一份器件口径归档 (%s) —— 正文里那一档 LUT/DSP/Fmax 没有出处"
                  % ", ".join(cands))
    for rel, d in have:
        if d.get("rtl_sha256") == cur:
            return d
    detail = "; ".join(
        ("%s 无 rtl_sha256 键" % rel) if not d.get("rtl_sha256") else
        ("%s 记的 epv32_core.v=%s" % (rel, str(d["rtl_sha256"].get("epv32_core.v", ""))[:12]))
        for rel, d in have)
    raise AssertionError(
        "器件口径归档没有一份对应当前 RTL (当前 epv32_core.v=%s); %s "
        "—— RTL 改过之后必须复跑: bash flow/run_td_refresh.sh && python tools/pnr_archive.py"
        % (cur["epv32_core.v"][:12], detail))


def pnr_arms():
    c = pnr_load()["comparison_same_platform"]
    return (c["post_synthesis_report_area"], c["post_place_and_route_report_area"], c)


def pnr_anchors():
    """正文必须原样出现的器件读数, 全部由归档现算。第三个字段是行级语境词。"""
    b = jload(os.path.join("results", "bench", "nn_bench.json"))
    syn, par, c = pnr_arms()
    cap_dsp = pnr_load()["device_capacity_from_report_area"]["dsp"]
    a = [
        ("目标器件", c["part"].split()[0], "器件"),
        ("TD EPV LUT", "%d" % syn["epv"]["total_luts"], "交付顶层"),
        ("TD 基线 LUT", "%d" % syn["baseline"]["total_luts"], "交付顶层"),
        ("TD LUT 净增", "%d" % syn["delta"]["total_luts_abs"], "净增"),
        ("TD LUT 净增幅", "%.2f" % syn["delta"]["total_luts_pct"], "净增"),
        ("TD FF 净增", "%d" % syn["delta"]["ff_abs"], "净增"),
        ("TD EPV DSP", "%d out of %d" % (syn["epv"]["dsp"], cap_dsp), "DSP"),
        ("TD DSP 占用率", "%.2f" % (100.0 * syn["epv"]["dsp"] / cap_dsp), "DSP"),
        ("TD EPV LUT 占位", "%d" % syn["epv"]["lut_sites"], "交付顶层"),
        ("TD 基线 LUT 占位", "%d" % syn["baseline"]["lut_sites"], "交付顶层"),
        ("TD EPV 综合 Fmax", "%.3f" % syn["epv"]["fmax_mhz"], "MHz"),
        ("TD 基线 综合 Fmax", "%.3f" % syn["baseline"]["fmax_mhz"], "MHz"),
        ("TD EPV 布线后 Fmax", "%.3f" % par["epv"]["fmax_mhz"], "MHz"),
        ("TD 基线 布线后 Fmax", "%.3f" % par["baseline"]["fmax_mhz"], "MHz"),
        ("TD Fmax 降幅", "%.2f" % abs(par["delta"]["fmax_pct"]), "布线后频率"),
    ]
    # 净墙钟加速: 周期加速 × 频率比, 三个操作数都来自归档, 正文只许印这个结果
    net = b["speedup"] * par["epv"]["fmax_mhz"] / par["baseline"]["fmax_mhz"]
    a.append(("净墙钟加速", "%.2f" % net, "净剩"))
    return a


def s_pnr_gate(ctx):
    """核归档的自洽性、可归因性与**新鲜度**; 不重跑 TD —— 一次两档 P&R 十几分钟
    且占工具许可, 它属于「按需复现」那一档, 复现命令印在 README 与失败消息里。"""
    d = pnr_load()
    v = d.get("verified_against_source")
    assert isinstance(v, dict), \
        ("verified_against_source 不是一个算出来的比对结果而是 %r —— 旧版这里是个写死"
         "的 True, 这道口从来核不到任何东西" % (v,))
    assert v.get("all_match") is True and v.get("files_checked", 0) >= 8, \
        "臂目录里的输入副本没有全部核过当前源码: %r" % (v.get("files_checked"),)
    assert v.get("td_run_id"), \
        "归档没记 TD log 里的 run_id, 无法排除「沿用上一版 RTL 的报告」"
    syn, par, c = pnr_arms()
    proof = c["epv_module_presence_proof"]["epv32_epv_in_elaborated_modules"]
    assert proof["epv_delivered_top"] is True, \
        "EPV 档展开模块列表里没有 epv32_epv —— 两档差异无法归因于扩展"
    assert proof["baseline_delivered_top"] is False, \
        "基线档竟然展开了 epv32_epv —— EPV_ENABLE=0 没生效, 所有增量都是假的"
    cap = d["device_capacity_from_report_area"]
    cm = c["common_to_both_arms"]
    assert cm["clock_constraint_ns"] == d["clock_constraint_ns"] == 10.0, \
        "两档时钟约束不是 10 ns: %r / %r" % (cm["clock_constraint_ns"], d["clock_constraint_ns"])
    for k in ("td_version", "device_db", "package"):
        assert cm[k] == d[k], "两档公共条件字段 %s 不一致: %r vs %r" % (k, cm[k], d[k])
    for blk, name in ((syn, "综合"), (par, "布线后")):
        for k in ("dsp", "ff"):
            assert blk["epv"][k] - blk["baseline"][k] == blk["delta"][k + "_abs"], \
                "%s档 %s 增量与两档读数不等" % (name, k)
        if name == "综合":
            assert blk["epv"]["total_luts"] - blk["baseline"]["total_luts"] \
                == blk["delta"]["total_luts_abs"], "综合档 LUT 增量与两档读数不等"
        else:
            # 布线后报告里就是没有 #Total_luts 这一行(TD 只在 gate 级印)。
            # 写成显式断言而不是 `if k in blk` —— 条件式回落会让"报告少了
            # 一整列"和"本来就没有这一列"长得一样。
            assert "total_luts" not in blk["epv"], \
                "布线后档出现了 total_luts, 本门的判据集合要跟着改"
            assert "total_luts" not in blk["delta"]
        assert blk["epv"]["dsp"] < cap["dsp"], \
            "%s档 DSP 数 %d 不小于器件容量 %d, 归档不可能对" % (name, blk["epv"]["dsp"], cap["dsp"])
    # 布线后 Fmax 降幅必须与两档读数自洽(容差由 %.2f 的半个末位推出)
    want = 100.0 * (par["epv"]["fmax_mhz"] / par["baseline"]["fmax_mhz"] - 1.0)
    assert abs(want - par["delta"]["fmax_pct"]) <= 0.005 + 1e-9, \
        "布线后 Fmax 降幅 %r 与两档重算 %+.3f 不符" % (par["delta"]["fmax_pct"], want)
    # A/B: 把归档记的 RTL 哈希改一位再选档 —— 必须选不出来。
    # 不跑这一条, "按哈希选档" 与 "随便读第一份" 是同一个读数。
    ab = os.path.join("results", "pnr", "_ab_stale.json")
    try:
        stale = json.loads(json.dumps(d))
        h = stale["rtl_sha256"]["epv32_core.v"]
        stale["rtl_sha256"]["epv32_core.v"] = h[:-1] + ("1" if h[-1] != "1" else "2")
        assert stale["rtl_sha256"] != d["rtl_sha256"], "扰动没造出差异, A/B 是空跑"
        with io.open(ab, "w", encoding="utf-8") as f:
            json.dump(stale, f)
        fired = False
        try:
            pnr_load([ab])
        except AssertionError as e:
            fired = "对应当前 RTL" in str(e)
        assert fired, "哈希改一位还是能被选上 —— 新鲜度键不咬"
    finally:
        if os.path.isfile(ab):
            os.remove(ab)
    return {"part": d["device"], "rtl_sha_head": d["rtl_sha256"]["epv32_core.v"][:12],
            "epv_luts": syn["epv"]["total_luts"],
            "base_luts": syn["baseline"]["total_luts"],
            "epv_dsp": "%d/%d" % (syn["epv"]["dsp"], cap["dsp"]),
            "routed_fmax_epv": par["epv"]["fmax_mhz"],
            "routed_fmax_base": par["baseline"]["fmax_mhz"]}


def doc_check(txt, anchors):
    """锚点表是 (标签, 期望值, 语境词|None) 三元组。
    ctx=None 走全局: 只要正文某处出现这个数即可。
    ctx 给了则必须**同一行**同时出现数字与语境词 —— 文档级判据太松:
    一个数被留在表格某一行、结论句里换成别的数, 全局层看不见。
    报红时说清是哪一层开的火。"""
    norm = txt.replace(",", "")
    lines = [ln.replace(",", "") for ln in txt.split("\n")]
    bad = []
    for label, val, ctx in anchors:
        v = val.replace(",", "")
        if ctx is None:
            if v not in norm:
                bad.append((label, val, "全局"))
        elif not any(v in ln and ctx in ln for ln in lines):
            bad.append((label, val, "行级(语境词 %r 与数字不同行)" % ctx))
    return bad


def bump(val):
    """把期望值末位数字 +1(模 10), 得到一个形状相同但必不相等的串。"""
    ds = [i for i, ch in enumerate(val) if ch.isdigit()]
    if not ds:
        return None
    i = ds[-1]
    return val[:i] + str((int(val[i]) + 1) % 10) + val[i + 1:]


HEADLINE = ("加速比", "基线周期", "EPV周期", "MAC 数",
            "核含扩展单元数", "核基线单元数", "扩展净开销",
            "网络加速比", "网络基线周期", "网络EPV周期", "网络MAC数",
            "TD EPV LUT", "TD 基线 LUT", "TD LUT 净增", "TD FF 净增",
            "TD EPV 布线后 Fmax", "TD 基线 布线后 Fmax", "净墙钟加速",
            "rv32ui 通过数", "rv32um 通过数", "ISS 自身断言数")


def readme_drift(anchors, rd):
    """报告与 README 两份交付件共用同一批头条数, 不许各说各话。"""
    rdn = rd.replace(",", "")
    return [(lab, val) for lab, val, _ in anchors
            if lab in HEADLINE and val.replace(",", "") not in rdn]


# 正文引用面普查：把交付件里写下的 `results/...` 一律收进来，再逐个问「盘上有吗」。
# 判据按**形状**而不是扩展名白名单。原来这里是
# `results/[\w/\-\.]+\.(?:json|txt|vcd|md)`，两个方向都错过：
#   ①多段扩展名被截断 —— 波形收成 `06_wave.vcd.gz` 之后，那条正则只认到 `.vcd`
#     为止，于是对着一个真实存在的产物报「引用了不存在的路径」；
#   ②不在白名单里的形态根本不进判据 —— `demo_video.mp4`、目录名 `results/xxx/`
#     一律免检，于是一份**从没存在过**的 `results/encoding/` 一路绿到本轮。
DOC_PATH_RE = re.compile(u"results/[A-Za-z0-9_./\\-*?]+")
GLOB_CHARS = ("*", "?", "[")
# 正文点名一个包内工件时一律用反引号（`tools/pnr_archive.py`），所以「这句话能不能
# 指到东西」可以只按反引号里的整段判：目录树展示件里写的是缩进的相对名
# （`refs/anlogic_td_probe.md` 挂在 docs/ 那一节下面），把它们按整仓路径查必然假红。
DOC_TOPS = ("flow", "tools", "rtl", "sw", "tb", "docs", "demo", "refs",
            "legacy", "figures", "results")
CITED_PATH_RE = re.compile(u"`((?:%s)/[^`\\s]*)`" % "|".join(DOC_TOPS))


def doc_paths(text):
    """正文里的 results/ 引用（去重、排序）。

    尾巴上的标点是句子的一部分不是路径的一部分（`... summary.json.` 这种），
    末尾的 `/` 也不改变指向（`results/demo/` 与 `results/demo` 是同一个目录），
    而 glob 里的 `*` 必须留着 —— 截掉它就等于把「这一批日志」这种引用
    当成一个具体的文件名去查，那必然查不到。
    """
    return sorted({q.rstrip(".,;:/") for q in DOC_PATH_RE.findall(text)})


def cited_paths(text):
    """正文反引号点名的包内路径（含 results/ 归档，也含脚本、源文件、目录）。"""
    return sorted(set(CITED_PATH_RE.findall(text)))


def unresolved_paths(refs, root="."):
    """返回「指不到东西」的那几条。三种合法形态都算指得到：
    文件、目录、以及至少命中一个的 glob（写成 `matrix_run*.log` 那种
    「这一批日志」的引用，指的是集合，集合空了就是引用过期）。
    明文大产物收成 `.gz` 之后，正文里那种「重跑命令的输出路径」仍写明文名 ——
    只要同目录有 `<名>.gz` 与它的收条 `<名>.gz.json`，就算指得到（收条把
    原件指纹与压缩件指纹绑在一起，见 flow/compress_artifact.py）。"""
    bad = []
    for q in refs:
        if any(c in q for c in GLOB_CHARS):
            if not glob.glob(os.path.join(root, q)):
                bad.append(q + u" (glob 命中 0 个)")
            continue
        p = os.path.join(root, q)
        if os.path.isfile(p) or os.path.isdir(p):
            continue
        if os.path.isfile(p + ".gz") and os.path.isfile(p + ".gz.json"):
            continue
        bad.append(q)
    return bad


def doc_path_resolver_selfcheck():
    """判据自己的夹具：盖一间一次性沙盒，四种形态各判一次。

    期望值全部由沙盒的构造决定，不取包内现状 —— 否则「今天包里有 results/compliance/」
    会变成夹具的前提，哪天产物改名就把这条判据一起带走。
    """
    box = tempfile.mkdtemp(prefix="docpath_selfcheck_")
    fails = []
    try:
        os.makedirs(os.path.join(box, "results", "dir_ok"))
        for nm in ("a.json", "logs_run1.log", "logs_run2.log",
                   "big.vcd.gz", "big.vcd.gz.json", "orphan.vcd.gz"):
            with io.open(os.path.join(box, "results", nm), "w",
                         encoding="utf-8") as f:
                f.write("x\n")
        cases = [
            (u"目录算可解析", ["results/dir_ok"], []),
            (u"文件算可解析", ["results/a.json"], []),
            (u"glob 命中 2 个算可解析", ["results/logs_run*.log"], []),
            (u"glob 命中 0 个必须判红", ["results/nothing_run*.log"],
             ["results/nothing_run*.log (glob 命中 0 个)"]),
            (u"凭空造的路径必须判红", ["results/fake_archive.json"],
             ["results/fake_archive.json"]),
            (u"明文已收成压缩件（带收条）算可解析", ["results/big.vcd"], []),
            (u"压缩件在而收条不在，不许当证据", ["results/orphan.vcd"],
             ["results/orphan.vcd"]),
        ]
        for why, refs, want in cases:
            got = unresolved_paths(refs, root=box)
            if got != want:
                fails.append("%s: 判出 %r，应为 %r" % (why, got, want))
        # 形状判据的回归正对照：多段扩展名与视频名都必须**整条**被收到，
        # 而不是收到 `.vcd` 就停（那正是本轮第一次红在讲稿上的原因）；
        # glob 也必须整条留着，把 `matrix_run*.log` 截成 `matrix_run` 会拿一个
        # 「这一批日志」的引用去查一个不存在的文件（第一版就是这么红的）。
        shape = doc_paths(u"看 `results/demo/06_wave.vcd.gz` 与 "
                          u"`results/demo/demo_video.mp4`，目录 `results/demo/`，"
                          u"数法见 `results/logs/matrix_run*.log`。")
        for want in (u"results/demo/06_wave.vcd.gz",
                     u"results/demo/demo_video.mp4", u"results/demo",
                     u"results/logs/matrix_run*.log"):
            if want not in shape:
                fails.append("形状判据没收全: 缺 %s（实到 %r）" % (want, shape))
        if u"results/demo/06_wave.vcd" in shape:
            fails.append("形状判据又把 .vcd.gz 截断成 .vcd 了")
        if u"results/logs/matrix_run" in shape:
            fails.append("形状判据把 glob 的 * 截掉了")
        # 反引号点名那一路：整段路径要收进来（不带反引号），且不存在的必须判红。
        cited = cited_paths(u"见 `flow/zz_nope.py`、`results/` 与本文件 "
                            u"`flow/run_all.py`；缩进树里的 refs/xx.md 不算。")
        if u"flow/zz_nope.py" not in cited or u"flow/run_all.py" not in cited:
            fails.append("反引号点名普查收漏: %r" % (cited,))
        if any(c.startswith("`") or c.endswith("`") for c in cited):
            fails.append("反引号被当成路径的一部分收进来了: %r" % (cited,))
        if unresolved_paths([u"flow/zz_nope.py"], root=box) != [u"flow/zz_nope.py"]:
            fails.append("点名的脚本不存在却没判红")
    finally:
        shutil.rmtree(box, ignore_errors=True)
    return fails


def s_docgate(ctx):
    """正文引用的 results/ 路径必须存在; 正文的关键数字必须由归档现算再比对。"""
    if not os.path.isfile(REPORT):
        raise AssertionError("报告不存在: %s" % REPORT)
    txt = io.open(REPORT, encoding="utf-8").read()
    refs = doc_paths(txt)
    missing = unresolved_paths(refs)
    assert not missing, "正文引用了不存在的归档: %s" % missing
    assert refs, "正文一个归档都没引用, 说明数字没有出处"
    # 引用面判据自己也要有双向夹具：一条凭空造的路径必须判红，而目录、glob、
    # 压缩件这三种合法形态不许判红（把合法形态判红，下一个动的人就是删判据的人）。
    selfbad = doc_path_resolver_selfcheck()
    assert not selfbad, "results/ 引用面判据的夹具不自洽: %s" % selfbad[:4]

    b = ctx.get("bench") or jload(os.path.join("results", "bench", "nn_bench.json"))
    s = ctx.get("synth") or jload(os.path.join("results", "synth", "summary.json"))
    comp = ctx.get("compliance") or {}

    def num(x):
        return "{:,}".format(int(x))

    anchors = [
        ("加速比", "%0.2f" % b["speedup"], None),
        ("基线周期", num(b["ref_cycles"]), None),
        ("EPV周期", num(b["epv_cycles"]), None),
        ("MAC 数", num(b["macs"]), None),
        ("核含扩展单元数", num(s["arms"]["core_with_epv"]["cells"]), None),
        ("核基线单元数", num(s["arms"]["core_no_epv"]["cells"]), None),
        ("扩展净开销", num(s["epv_net_overhead_cells"]), None),
    ]
    # 网络级读数 (整张网络跑一遍): 同一趟加进来的证据, 也得同样被锚住
    netf = os.path.join("results", "bench", "nn_net.json")
    assert os.path.isfile(netf), "缺 %s —— 报告里「端到端网络」那一段没有出处" % netf
    net = jload(netf)
    anchors += [
        ("网络MAC数", num(net["macs_total"]), None),
        ("网络基线周期", num(net["ref_cycles"]), None),
        ("网络EPV周期", num(net["epv_cycles"]), None),
        ("网络加速比", "%0.2f" % net["speedup"], None),
    ]
    # 器件口径: 期望值一律从 results/pnr/summary.json 现算, 不抄正文里那串字面量
    anchors += pnr_anchors()
    # 黄金 ISS 的断言数: 正文与 PPT 都引了这个数, 出处是 iss_selftest 那一步
    issf = os.path.join("results", "iss", "selftest.json")
    assert os.path.isfile(issf), "缺 %s —— 正文那句 ISS 断言数没有出处" % issf
    iss = jload(issf)
    anchors.append(("ISS 自身断言数", "%d" % iss["assertions"], "ISS"))
    for ui, suite in (("rv32ui", "rv32ui"), ("rv32um", "rv32um"),
                      ("rv32mi", "rv32mi")):
        d = comp.get(suite) or jload(os.path.join("results", "compliance", suite + ".json"))
        anchors.append((ui + " 通过数", "%d / %d" % (d["n_pass"], d["n_tests"]), None))
    # 比对前先归一化掉千位分隔符: 本门要核的是「这个数在不在正文里」,
    # 不是排版。曾因为归档侧印 1,378 而正文写 1378 而假红。
    bad = doc_check(txt, anchors)
    assert not bad, "正文与归档对不上(期望由归档现算): %s" % bad

    # 注入自检: 逐条把期望值末位改一位再喂回去, 本门必须只咬这一条并说清层次。
    # 不写这一步, "11 个锚点全过" 与 "这 11 个锚点其实一个都不咬" 是同一个读数。
    inj = []
    for label, val, ctx in anchors:
        new = bump(val)
        if new is None or new == val:
            inj.append((label, "造不出扰动值"))
            continue
        # 正文允许写 1378 而归档印 1,378(比对时两边都去逗号), 所以扰动也要
        # 按正文里真实出现的那一种写法去做, 否则这条注入是空跑。
        mutated = txt
        for form, repl in ((val, new), (val.replace(",", ""), new.replace(",", ""))):
            if form in txt:
                mutated = txt.replace(form, repl)
                break
        if mutated == txt:
            inj.append((label, "期望值在正文里替换不动"))
            continue
        names = set(x[0] for x in doc_check(mutated, anchors))
        if label not in names:
            inj.append((label, "改了它门不红"))
        elif names - {label}:
            inj.append((label, "还连带报红了 %s" % sorted(names - {label})))
    assert not inj, "文档门不咬: %s" % inj[:8]

    # README 与报告共用头条数: 归档路径要存在, 数字要两份一致
    rd = io.open("README.md", encoding="utf-8").read()
    rrefs = doc_paths(rd)
    rmiss = unresolved_paths(rrefs)
    assert not rmiss, "README 引用了不存在的归档: %s" % rmiss
    drift = readme_drift(anchors, rd)
    assert not drift, "README 与归档/报告对不上: %s" % drift
    # 步数是一句"派生计数": 它由本表的长度决定, 谁加一步忘了改 README/报告就会漂。
    # 两类句式各有各的真值, 不许混在一个集合里放(上一版就是这么写的: 「N 步全绿」
    # 与「前 N-1 步产证据」共用 ±1 余量 => 加一步之后旧文案仍被判绿, 这条门当场失效)。
    true_steps = len(STEPS) + len(POST_STEPS)
    # 「前 N 步产证据」里的 N 是**产证据那一档**的格数, 不是总格数减一: 后置格里
    # 已经有两格 (核同源 / 收轨迹体积), 用 total-1 会把第二格后置格也算成产证据。
    pre_steps = len(STEPS)
    bad_n = []
    demo_p = os.path.join("demo", u"演示脚本与讲稿.md")
    corpus = [txt, rd]
    if os.path.isfile(demo_p):
        corpus.append(io.open(demo_p, encoding="utf-8").read())
    for doc in corpus:
        for n, kind in step_claims(doc):
            ok = (n == pre_steps) if kind == u"前" else \
                 (1 <= n <= true_steps) if kind == u"序" else (n == true_steps)
            if not ok:
                bad_n.append("%s 步(%s 句式) 而本表是 %d 步(产证据那档 %d 格)"
                             % (n, kind, true_steps, pre_steps))
    assert not bad_n, ("文档步数文案与本表不一致: %s" % sorted(set(bad_n))[:6])
    # 这条判据也要证明它会咬: 内存里各造一条 off-by-one 的文案, 两类句式各一条。
    for kind, base in (("全绿", true_steps), ("前", pre_steps)):
        probe_doc = u"本表 %d 步认证矩阵" % base if kind == "全绿" \
            else u"前 %d 步产证据" % base
        got = [(n, k) for n, k in step_claims(probe_doc)]
        assert got == ([(base, "全绿")] if kind == "全绿" else [(base, "前")]), \
            "步数解析器连正常句式都解不出: %s -> %s" % (probe_doc, got)
        for off in (base - 1, base + 1):
            bad = (u"本表 %d 步认证矩阵" % off if kind == "全绿"
                   else u"前 %d 步产证据" % off)
            hits = [1 for n, k in step_claims(bad)
                    if n != (pre_steps if k == u"前" else true_steps)]
            assert hits, ("步数门对 %s 句式偏移不报红 —— 这条门是装饰" % kind)
    # 「第 N 步」是序数指代, 只管在不在范围内: 越界必须红, 界内必须不红。
    for s, want_red in ((true_steps, False), (true_steps + 1, True), (0, True)):
        got = [1 for n, k in step_claims(u"第 %d 步做的事" % s)
               if not (1 <= n <= true_steps)]
        assert bool(got) == want_red, "序数句式判错: 第 %d 步 -> %s" % (s, got)
    # 这条判据也要证明它会咬: 在内存里把 README 的一个头条数改一位。
    # 目标值从锚点表现取(不写死字面量) —— 复跑一次综合, 8230 就变成了 7959,
    # 写死的探针会退化成"注入是空跑"然后被 assert 抓住(今天就是这样)。
    tgt = [(l, v) for l, v, _c in anchors if l == "TD EPV LUT"]
    assert tgt, "锚点表里没有 'TD EPV LUT' 这一条, README 注入没有目标"
    tgt_label, tgt_val = tgt[0]
    forms = [f for f in (tgt_val, tgt_val.replace(",", "")) if f in rd]
    assert forms, ("头条数 %s(%s) 在 README 里找不到, 注入是空跑" % (tgt_label, tgt_val))
    probe = rd.replace(tgt_val, bump(tgt_val) or tgt_val + "9")
    assert probe != rd, "扰动没改变 README 一个字节, 注入是空跑"
    d2 = readme_drift(anchors, probe)
    assert [x[0] for x in d2] == [tgt_label], \
        "改 README 的头条数却报出别的/没报 (实际=%s)" % ([x[0] for x in d2],)

    # 第三份文档：录屏讲稿。它印的是同一批头条数 —— 一漂就等于对着镜头念错数。
    # 这一层只管「归档现在印的值, 讲稿里有没有」(全局层)：讲稿不是数据表,
    # 行级语境那套留给报告; 但它自己写下的 results/ 路径必须可解析。
    demo_md = os.path.join("demo", u"演示脚本与讲稿.md")
    assert os.path.isfile(demo_md), "缺 %s —— README 指向了一份不存在的讲稿" % demo_md
    dtxt = io.open(demo_md, encoding="utf-8").read()
    drefs = doc_paths(dtxt)
    assert drefs, "讲稿一个 results/ 产物都没引用, 那这条判据是空的"
    # 就地注入：往讲稿的引用清单里塞一条凭空造的产物名，本步必须原样报出它，
    # 且只报它 —— 报出别的说明归属错了，一条都不报说明这条判据没接线。
    inj = unresolved_paths(drefs + ["results/zz_injected_missing.json"])
    assert inj == ["results/zz_injected_missing.json"], \
        "讲稿引用面判据不咬或误报: %s" % inj
    dmiss = unresolved_paths(drefs)
    assert not dmiss, "讲稿引用了不存在的路径: %s" % dmiss
    # 讲稿那条「各段相加 … = 569 s / 余 31 s / 本版 51.3 MB」是成片 sidecar 的手工抄本，
    # 本轮之前没有任何门核它（P1-5 那一行自己就写着这件事没做）。
    # 判据的期望值一律取自 demo_video.json，不取自讲稿；解析不出来即判红 ——
    # 一条从不发火的抄本核对，等于没有抄本核对。
    vd = jload(os.path.join("results", "demo", "demo_video.json"))
    scenes = [int(s["seconds"]) for s in vd["scenes"]]
    sec_tbl = dtxt.split(u"## 时间轴总览", 1)
    assert len(sec_tbl) == 2, u"讲稿里没有「时间轴总览」那一节，逐段秒数没有可比对象"
    body = sec_tbl[1].split(u"\n## ", 1)[0]
    rows = re.findall(u"^\\| (\\d+) \\|.*\\| ([0-9]+(?: \\+ [0-9]+)*) \\|$",
                      body, re.M)
    assert rows, u"时间轴表一行都没解析出来（表格形状变了？）"
    per_row = [[int(x) for x in r[1].split(u"+")] for r in rows]
    flat = [x for grp in per_row for x in grp]
    assert flat == scenes, u"讲稿逐段秒数与成片不一致: 讲稿 %r vs 成片 %r" % (flat, scenes)
    m = re.search(u"各段相加 ([0-9+ ]+) = \\*\\*([0-9]+) s\\*\\*", dtxt)
    assert m, u"讲稿那条时间轴算式解析不出来"
    terms = [int(x) for x in m.group(1).split(u"+")]
    total = int(m.group(2))
    assert terms == [sum(g) for g in per_row], \
        u"算式的每一项与表格各行对不上: %r vs %r" % (terms, [sum(g) for g in per_row])
    dur = int(round(float(vd["duration_s"])))
    assert sum(terms) == total == dur, \
        u"算式自相矛盾或抄了旧成片: 加和 %d, 写着 %d, 成片 %d" % (sum(terms), total, dur)
    lim = int(round(float(vd["limit_duration_s"])))
    m2 = re.search(u"上限 ([0-9]+) s[^。\\n]*?余 \\*\\*([0-9]+) s\\*\\*", dtxt)
    assert m2 and int(m2.group(1)) == lim and int(m2.group(2)) == lim - total, \
        u"余量句与成片对不上: %r（上限 %d, 成片 %d）" % (
            m2.groups() if m2 else None, lim, total)
    m3 = re.search(u"本版 \\*\\*([0-9.]+) MB\\*\\*", dtxt)
    assert m3, u"讲稿那条体积句解析不出来"
    mb = float(vd["size_bytes"]) / (1 << 20)
    assert abs(float(m3.group(1)) - mb) < 0.05, \
        u"讲稿写 %s MB 而成片是 %.2f MB（半兆之内才算同一个数）" % (m3.group(1), mb)
    m4 = re.search(u"上限 ([0-9.]+) MB", dtxt)
    assert m4 and abs(float(m4.group(1))
                      - float(vd["limit_size_bytes"]) / (1 << 20)) < 0.05, \
        u"讲稿写的体积上限与成片判据里的上限不是一回事: %r" % (
            m4.groups() if m4 else None,)
    # 这条抄本核对也要证明它咬：在内存里各改一位（不落盘），两次都必须被抓出来。
    # 注入的受害者取自讲稿自己解析出来的值，不写死字面量 —— 写死的话，成片一改尺寸
    # 或时间轴一重排，注入就没有落点，而"没有红"会被读成"判据是好的"。
    p1 = dtxt.replace(u"各段相加 %d+" % terms[0],
                      u"各段相加 %d+" % (terms[0] + 1), 1)
    assert p1 != dtxt, "时间轴算式没有注入落点（句式变了，判据要跟着改）"
    t2 = [int(x) for x in re.search(
        u"各段相加 ([0-9+ ]+) = \\*\\*([0-9]+) s\\*\\*", p1).group(1).split(u"+")]
    assert t2 != [sum(g) for g in per_row], "改一位逐段秒数却没被发现 —— 装饰"
    p3 = dtxt.replace(u"本版 **%s MB" % m3.group(1),
                      u"本版 **%.1f MB" % (float(m3.group(1)) + 1.0), 1)
    assert p3 != dtxt, "体积句没有注入落点"
    mb2 = float(re.search(u"本版 \\*\\*([0-9.]+) MB\\*\\*", p3).group(1))
    assert abs(mb2 - mb) >= 0.05, "体积抄本改了一位却没被判出去 —— 装饰"
    # 附件 A 是台账，通篇在点名归档与脚本，它是第四份该被同一条判据看到的交付件。
    # 已知摩擦（本轮实测到）：台账里"解释某条悬空引用"的那句话会把那条引用原样写出来，
    # 于是禁令咬到解释禁令的句子。处置是**换写法**（「results/ 下一个叫 encoding 的目录」）
    # 而不是给台账开口子 —— 一开口子，真悬空的引用就也有地方待了。
    attp = os.path.join("docs", u"附件A_缺陷与门禁台账.md")
    assert os.path.isfile(attp), "缺附件 A: %s（报告里的「台账」指代全部失效）" % attp
    arefs = doc_paths(io.open(attp, encoding="utf-8").read())
    amiss = unresolved_paths(arefs)
    assert arefs, "附件 A 一个 results/ 产物都没点名, 这条判据是空的"
    assert not amiss, "附件 A 点名了不存在的路径: %s" % amiss
    # 第四份交付件之外，再把「反引号点名的包内工件」这一整层纳入：脚本、源文件、
    # 目录都算。本轮实测的真缺陷就长这样 —— 台账把包卫生门写成了
    # `tools/check_package_hygiene.py`，而它在 `flow/`：按 .json 判的老口径抓不到。
    cited_bad = []
    cited_n = 0
    for d in (REPORT, "README.md", demo_md, attp):
        cp = cited_paths(io.open(d, encoding="utf-8").read())
        cited_n += len(cp)
        cited_bad += ["%s: %s" % (os.path.basename(d), x)
                      for x in unresolved_paths(cp)]
    assert cited_n > 100, \
        "反引号点名普查只收到 %d 条 —— 分母塌了说明解析面坏了，先修判据再谈结论" % cited_n
    assert not cited_bad, "正文点名的包内工件指不到: %s" % cited_bad[:6]
    DEMO_LABELS = (u"加速比", u"基线周期", u"EPV周期", u"MAC 数",
                   u"网络MAC数", u"网络加速比", u"核含扩展单元数",
                   u"核基线单元数", u"扩展净开销", u"rv32ui 通过数",
                   u"rv32um 通过数", u"ISS 自身断言数", u"TD EPV LUT",
                   u"TD 基线 LUT", u"TD LUT 净增", u"TD LUT 净增幅",
                   u"TD FF 净增", u"TD EPV DSP", u"TD DSP 占用率",
                   u"TD EPV 综合 Fmax", u"TD 基线 综合 Fmax",
                   u"TD EPV 布线后 Fmax", u"TD 基线 布线后 Fmax",
                   u"TD Fmax 降幅", u"净墙钟加速")
    have = {l for l, _v, _c in anchors}
    absent = sorted(set(DEMO_LABELS) - have)
    assert not absent, "锚点表里没有讲稿要核的这几条: %s" % absent
    da = [(l, v, None) for l, v, _c in anchors if l in DEMO_LABELS]
    badd = doc_check(dtxt, da)
    assert not badd, "讲稿与归档对不上(期望由归档现算): %s" % badd
    # 这条判据也要证明它咬：把讲稿里一个头条数末位改一位再扫一遍。
    tl = [(l, v) for l, v, _c in da if l == u"网络加速比"]
    assert tl, "讲稿注入没有目标(锚点表里找不到 '网络加速比')"
    tl_label, tl_val = tl[0]
    assert tl_val in dtxt, "讲稿里找不到 %s(%s), 注入是空跑" % (tl_label, tl_val)
    dprobe = dtxt.replace(tl_val, bump(tl_val) or tl_val + "9", 1)
    assert dprobe != dtxt, "扰动没改讲稿一个字节"
    dbad = doc_check(dprobe, da)
    assert [x[0] for x in dbad] == [tl_label], \
        "改讲稿的网络加速比却报出别的/没报 (实际=%s)" % ([x[0] for x in dbad],)
    return {"checked_anchors": len(anchors), "archive_refs": len(refs),
            "readme_refs": len(rrefs), "demo_anchors": len(da),
            "demo_refs": len(drefs), "cited_paths_policed": cited_n,
            "injected_anchors": len(anchors) - len(inj)}


def s_clobber(ctx):
    """归档 ↔ 矩阵 同源核对。判据是子进程退出码, 不是它有没有打印
    ARCHIVE_CLOBBER_OK 这行字; violations 数只是给人看的补充。"""
    rc, out = run([PY, os.path.join("tools", "check_archive_clobber.py")])
    m = re.search(r"ARCHIVE_CLOBBER_FAIL: (\d+) violation", out)
    assert rc == 0, "归档与矩阵不同源 rc=%d out=%s" % (rc, out[-1200:])
    return {"violations": int(m.group(1)) if m else 0, "gate_rc": rc,
            "checked": "epv_bisect,epv_selftest,nn_bench"}


def s_consistency(ctx):
    """跨交付件「结论级」一致性: doc_gate 管数字对不对, 这一格管话说得对不对。

    起因是 2026-09-25 那份评审里最有价值的结构性发现: CIFAR-10 与访存曲线两块
    新证据落地时只改了报告, PPT 仍写着「精度…都没有读数」、README 仍把它列在
    「未做」里, 而 23 步矩阵全绿 —— 因为没有一个出口在核「四份交付件说的是不是
    同一件事」。两通道判决: 实扫 rc 与夹具 rc 都必须为 0, 且两边的计数都要读出来
    (只看结论行会读到上一趟留下的文件)。
    """
    gate = os.path.join("flow", "check_deliverable_consistency.py")
    rc_live, out_live = run([PY, gate])
    rc_fix, out_fix = run([PY, gate, "--selftest"])
    m = re.search(r"CONSISTENCY_(PASS|FAILED)", out_live)
    assert rc_live == 0 and m, "一致性门禁判红 rc=%d out=%s" % (rc_live, out_live[-2500:])
    lm = re.search(r"rules=(\d+) docs=(\d+) sentences=(\d+)", out_live)
    assert rc_fix == 0, "一致性门禁自带夹具判红 rc=%d out=%s" % (rc_fix, out_fix[-2500:])
    fx = re.search(r"FIXTURES rules=(\d+) sentences=(\d+) failed=(\d+)", out_fix)
    assert fx and fx.group(3) == "0", "夹具计数不可得或有失败: %s" % (
        fx.groups() if fx else None,)
    return {"rules": int(lm.group(1)), "docs_policed": int(lm.group(2)),
            "sentences_policed": int(lm.group(3)), "problems": 0,
            "fixture_rules": int(fx.group(1)), "fixture_failed": int(fx.group(3)),
            "gate_rc_live": rc_live, "gate_rc_selftest": rc_fix}


def s_pair_gates(ctx):
    """官方覆盖度门、第三方许可全文门、归档读数类别门、夹具分母一致性门，加上本轮
    R3 收口轮新接的四把（缺陷条数锚点、导航索引新鲜度、覆盖率读数、断言注入证明），
    一起在这一步跑齐。

    这里刻意不写「共 N 把」：那个数没有任何判据盯着它，写下来就是一份会漂的手抄
    （本包在附件 A 里为同一件事记过三次账）。把数由下面 `specs` 清单的长度承担，
    而清单条数就是这一步实际跑了多少把 —— 要数就数那个表。

    起因是本轮自己的普查: 前两道门盘上早就有、README 也列了名字, 但**没有任何命令
    跑它们** —— 名字不等于主人。把它们写成「已接」是假陈述, 所以这里真接。
    第三把是随后落地的: results/ 里的读数分三类(当前树复现 / 旧代历史读数 / 不记 rtl
    指纹的每代重写件), 以前没有任何工件能让读者分清 —— 这条缺口由收口表第五节记着,
    本轮补成 `tools/historical_archives.py` 走查 + `results/HISTORICAL_ARCHIVES.json` 登记。
    为什么不直接给步骤加 --selftest: 三个工具的夹具都排在实扫判决**之后**
    (tools/official_coverage.py:273 是 flag 分支), 传那个 flag 会让这一步只报夹具
    而丢掉实扫判决行。所以每条门以纯模式跑一次拿判决, 再单独跑一次夹具, 两通道
    合取: 退出码 + 判决行 + 计数, 三者都要读到。
    缺料即死: 解析不到判决行/计数一律 raise, 不许把「读不到」记成 0 当通过。
    """
    out = {}
    specs = [
        # ELF 清单门: build/ 自称「全部是可再生产物」，那每个 ELF 都必须指得到用例与判决
        # 归档；指不到生产者的那条不许编命令，落成 NO_ACTIVE_PRODUCER 并单独计数。
        # 排在 compact_traces 之前、任何 results/ 归档都已落盘的位置（它读 sim 与 compliance）。
        ("elf_manifest", os.path.join("tools", "elf_manifest.py"),
         "ELF_MANIFEST_OK",
         r"ELF_MANIFEST_OK elfs=(\d+) mapped=(\d+) unmapped=0 "
         r"families=[\w:,]+ no_producer=0",
         # no_producer 是**缺陷计数**，不是分母：把它放进 keys 会让
         # 「一条都没有」这个正确答案撞上通用守卫 all(v>0)。它在判决行里
         # 按字面 0 钉住，非零即整条判决行解不出来而判红。
         ("elfs", "mapped"),
         r"ELF_MANIFEST_SELFTEST OK (\d+)/(\d+)"),
        # 官方模板正文的转写件: 排在 official_coverage **之前**——那道门的官方语料
        # 包含这份转写, 它缺一段就该先在这里红, 而不是到覆盖率门上变成
        # "片段不在官方原文里"(那会被读成我把片段编错了)。默认即核对模式:
        # 官方原件在赛期内不该变, 变了要让人看见, 不许链条悄悄刷新。
        ("dump_official_template", os.path.join("tools", "dump_official_template.py"),
         "TEMPLATE_TRANSCRIPT_OK",
         r"TEMPLATE_TRANSCRIPT_OK paras=(\d+) mismatch=0 len_diff=0",
         ("paras",),
         r"TEMPLATE_SELFTEST_OK (\d+)/(\d+)"),
        ("official_coverage", os.path.join("tools", "official_coverage.py"),
         "OFFICIAL_COVERAGE_OK",
         r"OFFICIAL_COVERAGE_OK total=(\d+) covered=(\d+) declared=(\d+) "
         r"gaps=0 problems=0",
         ("total", "covered", "declared"),
         r"OFFCOV_SELFTEST_OK (\d+)/(\d+)"),
        ("third_party_licenses", os.path.join("tools", "third_party_licenses.py"),
         "LICENSE_GATE_OK",
         r"LICENSE_GATE_OK copied=(\d+) declared_unavailable=(\d+) problems=0 "
         r"orphans=0 expected=(\d+)",
         ("copied", "declared_unavailable", "expected"),
         r"LICENSE_SELFTEST_OK (\d+)/(\d+)"),
        ("fixture_claims", os.path.join("tools", "fixture_claims.py"),
         "FIXTURE_CLAIMS_OK",
         r"FIXTURE_CLAIMS_OK scripts=(\d+) computed=(\d+) literal_declared=(\d+) "
         r"undeclared=0 stale_declarations=0 runtime_mismatch=0 problems=0",
         ("scripts", "computed", "literal_declared"),
         r"FIXTURE_CLAIMS_SELFTEST OK (\d+)/(\d+)"),
        ("historical_archives", os.path.join("tools", "historical_archives.py"),
         "HIST_ARCHIVE_OK",
         r"HIST_ARCHIVE_OK objects=(\d+) current=(\d+) historical=(\d+) unsigned=(\d+) "
         r"cited_unsigned=\d+ weak_heads=\d+ registered=(\d+) unregistered=0 bogus=0 "
         r"problems=0",
         ("objects", "current", "historical", "unsigned", "registered"),
         r"HIST_ARCHIVE_SELFTEST OK (\d+)/(\d+)"),
        # ---- 2026-09-26 R3 评审收口轮新增的四把 (都不占步骤表的一格) ----
        ("defect_count_gate", os.path.join("tools", "defect_count_gate.py"),
         "DEFECT_COUNT_OK",
         r"DEFECT_COUNT_OK total=(\d+) claims=(\d+) empty_readings=0 problems=0",
         ("total", "claims"),
         r"DEFECT_COUNT_SELFTEST OK (\d+)/(\d+)"),
        # 导航索引不在这一格里跑了：它要在 matrix.json 落盘**之后**、且
        # compact_traces 收完轨迹**之后**跑，否则它索引的是上一趟的结论与收成前的
        # 体积（实测：它写「1529 文件 / 1085.0 MiB」而盘上是 1526 / 187.1 MiB，
        # 并引用早一趟的 started_utc）。挪到后置格 `navigation`，见 s_navigation。
        ("coverage_report", os.path.join("tools", "coverage_report.py"),
         "COVERAGE_OK",
         r"COVERAGE_OK programs=(\d+) buckets=(\d+) covered=(\d+) line_pct=[0-9.]+ "
         r"branch_pct=[0-9.]+ toggle_pct=[0-9.]+ absent_modules=0 unmatched=0",
         ("programs", "buckets", "covered"),
         r"COVERAGE_SELFTEST OK (\d+)/(\d+)"),
        ("assert_selfcheck", os.path.join("flow", "assert_selfcheck.py"),
         "ASSERT_SELFCHECK OK",
         r"ASSERT_SELFCHECK OK mode=\S+ programs=(\d+) armed_units=2 fail_lines=0 "
         r"tags=(\d+) proven=(\d+) no_cheap=(\d+)",
         ("programs", "tags", "proven", "no_cheap"),
         r"ASSERT_SELFTEST_OK (\d+)/(\d+)"),
        # BRAM 模板/容量那八臂是一次约 40 分钟的 TD 批次，不能进步骤表；
        # 链条里跑的是它那份便宜的归档核对（源码 sha + 归档行还在原始 log 里 + 三条结论）。
        ("bram_arms", os.path.join("tools", "bram_arms_check.py"),
         "BRAM_ARMS_OK",
         r"BRAM_ARMS_OK arms=(\d+) async_zero_bram=(\d+)/(\d+) fit_256kbit=(\d+) "
         r"overflow=(\d+)/(\d+)",
         ("arms", "async_zero", "async_arms", "fit_used", "overflow_used", "overflow_cap"),
         r"BRAM_ARMS_SELFTEST (?:OK|FAIL) (\d+)/(\d+)"),
        # 仿真器二进制的输入指纹：链条里跑的是那发便宜的只核（盘上侧车 == 现在重算的
        # 指纹，且逐文件那本账一份不差），重编本身在 build_sim 那一步已经付掉了。
        # 不占步骤格：它读的侧车就是 build_sim 写的，排在它后面任何一格都成立。
        ("sim_fingerprint", os.path.join("flow", "sim_fingerprint.py"),
         "SIM_FINGERPRINT_VERIFY OK",
         r"SIM_FINGERPRINT_VERIFY OK arm=cert n_sources=(\d+) mismatched_files=0",
         ("n_sources",),
         r"SIM_FINGERPRINT_SELFTEST (?:OK|FAIL) (\d+)/(\d+)",
         ["--verify", "cert"]),
    ]
    for spec in specs:
        name, gate, marker, pat, keys, fixpat = spec[:6]
        live_args = list(spec[6]) if len(spec) > 6 else []
        rc_live, out_live = run([PY, gate] + live_args)
        rc_fix, out_fix = run([PY, gate, "--selftest"])
        m = re.search(pat, out_live)
        assert rc_live == 0 and m, (
            "%s 判红或未印判决行 rc=%d out=%s" % (name, rc_live, out_live[-2500:]))
        assert marker in out_live, "%s 判决行 %s 缺失" % (name, marker)
        fx = re.search(fixpat, out_fix)
        assert rc_fix == 0 and fx, (
            "%s 夹具未跑或未通过 rc=%d out=%s" % (name, rc_fix, out_fix[-2500:]))
        vals = [int(g) for g in m.groups()]
        assert len(vals) == len(keys), "%s 计数形状变了: %r" % (name, m.groups())
        assert all(v > 0 for v in vals), (
            "%s 计数里有 0, 分母不像装齐了: %r" % (name, dict(zip(keys, vals))))
        if name == "official_coverage":
            assert vals[0] == vals[1] + vals[2], (
                "覆盖度守恒式不成立: total=%d != covered=%d + declared=%d"
                % tuple(vals))
        if name == "bram_arms":
            # 三条关系而不是一个 PASS：异步读各臂全都不出 BRAM、注册读模板在 256 Kbit
            # 那臂装得进器件、同一模板在 2048 Kbit 那臂**溢出**（used>capacity）。
            # 第三条是这一档的全部要点：TD 在 gate 阶段不拦容量溢出，所以
            # 「报告里出现了 BRAM 数」不等于「装得下」。
            assert vals[1] == vals[2], (
                "异步读臂未全部推不出 BRAM: %d/%d" % (vals[1], vals[2]))
            assert 0 < vals[3] <= vals[5], (
                "注册读模板在 256 Kbit 那臂应当装进器件: %d <= %d 不成立"
                % (vals[3], vals[5]))
            assert vals[4] > vals[5], (
                "2048 Kbit 那臂应当溢出: used=%d 不大于 capacity=%d —— 交付主存"
                "容量这条约束变了，A.23 那句要重写" % (vals[4], vals[5]))
        if name == "elf_manifest":
            # 判决行的正则已经把 `unmapped=0` 钉死了, 这一条补的是「数得齐」:
            # ELF 总数必须等于映射条数(家族划分由工具自己的 DEN1/DEN3 守)。
            assert vals[0] == vals[1], (
                "ELF 清单不守恒: elfs=%d 而 mapped=%d" % (vals[0], vals[1]))
        if name == "historical_archives":
            # 四类分流必须刚好盖住总体: 走查里没有任何一份归档被漏掉
            assert vals[0] == vals[1] + vals[2] + vals[3], (
                "归档类别划分不守恒: objects=%d 而 current+historical+unsigned=%d"
                % (vals[0], vals[1] + vals[2] + vals[3]))
            assert vals[4] >= vals[2], (
                "登记条目比 historical 归档还少(%d < %d): 有旧代读数没进登记表"
                % (vals[4], vals[2]))
        if name == "defect_count_gate":
            # 「几条缺陷」的真源是附件 A.1 的行数; 正文/README/PPT 生成器里的每一处
            # 「N 条…缺陷」都必须等于它。claims 比 total 大是小不了, 但 claims=0 说明
            # 那句话没了 —— 那也是一种漂移, 上面 all(v>0) 已经挡住。
            assert vals[0] >= 1, "缺陷台账行数为 0, 分母塌了"
        if name == "coverage_report":
            assert vals[1] >= vals[2], (
                "覆盖率 bucket 数比 covered 还小: buckets=%d covered=%d"
                % (vals[1], vals[2]))
        if name == "assert_selfcheck":
            # 守恒式: 已证明会咬的 + 已声明「注不出廉价反例」的 == 断言全集。
            # 缺一条就是有一条断言从没被任何负向测试跑到过 (那等于没有)。
            n_tags, n_proven, n_nocheap = vals[1], vals[2], vals[3]
            assert n_proven + n_nocheap == n_tags, (
                "断言全集没收口: tags=%d 而 proven=%d + no_cheap=%d = %d"
                % (n_tags, n_proven, n_nocheap, n_proven + n_nocheap))
        done, tot = int(fx.group(1)), int(fx.group(2))
        assert done == tot and tot > 0, "%s 夹具没跑满: %d/%d" % (name, done, tot)
        for k, v in zip(keys, vals):
            out["%s_%s" % (name, k)] = v
        out["%s_fixture" % name] = "%d/%d" % (done, tot)
        out["%s_rc" % name] = "%d/%d" % (rc_live, rc_fix)
    return out


STEPS = [("lint", s_lint), ("build_sim", s_build), ("iss_selftest", s_iss),
         ("epv_bisect", s_bisect), ("epv_selftest", s_selftest),
         ("nn_bench", s_nnbench), ("nn_net", s_nnnet), ("cifar10_net", s_cifar),
         ("mem_sensitivity", s_memsens),
         ("rv32ui", s_rv32ui), ("rv32um", s_rv32um), ("rv32mi", s_rv32mi),
         ("micro_bisect", s_micro),
         # 这一格读 results/compliance/*.json 当分母, 所以必须排在各套件之后。
         ("isco_trace", s_isco),
         ("rand_trace", s_randtrace),
         ("synthesis", s_synthesis), ("encoding_gate", s_encgate),
         ("pnr_gate", s_pnr_gate), ("doc_gate", s_docgate),
         ("word_gate", s_wordgate), ("figures", s_figures),
         ("docx", s_docx), ("ppt", s_ppt),
         # 排在 docx/ppt 之后: 它要读**本趟刚落盘**的那两份生成物, 早一步就是拿
         # 上一趟的封面核对这一趟的正文。
         ("consistency", s_consistency),
         # 这两道门先前只在 README 里列了名字, 没有任何命令跑它们(名字不等于主人)。
         # 排在 consistency 之后: 它俩核的是文档与随包件, 与前面产证据的格互不依赖。
         ("pair_gates", s_pair_gates)]

# 这一格读的是**本趟刚落盘的 matrix.json**, 所以它不能排在写矩阵之前。
# 步数不在注释里写死（写了就会漂）: N_TOTAL 由下面两个列表的长度算出来，
# 而正文里那句「N 步」由 doc_gate 拿本表长度现算再比对。
def s_compact(ctx):
    """把 900 MB 级逐周期轨迹收成 .gz + 摘要 (体积证据, 不产新读数值)。

    预赛是线上评审: 评委要下载并解压整个包, 而判定早在 flow/rt.py 读完轨迹写进
    results/sim/*.json 时就已经落进归档了。留着全量文本的唯一作用是"万一有人想看",
    那件事可以用 前 N 行 + 后 N 行 + 全量 sha256 + 复跑命令 替掉 —— 证据没少,
    只是换了一种可核对的形状。判决两通道: 收成 rc 与夹具 rc 都要为 0, 且两边的
    计数都要从判决行现读 (只看结论行会读到上一趟留下的文件)。
    """
    gate = os.path.join("flow", "compact_traces.py")
    rc, out = run([PY, gate, "--apply"])
    m = re.search(r"TRACE-COMPACT_OK files=(\d+) bytes_saved=(\d+)", out)
    assert rc == 0 and m, "轨迹收成失败 rc=%d out=%s" % (rc, out[-2000:])
    rc2, out2 = run([PY, gate, "--selftest"])
    fx = re.search(r"FIXTURES cases=(\d+) failed=(\d+)", out2)
    assert rc2 == 0 and fx and fx.group(2) == "0", \
        "收成器自带夹具判红 rc=%d out=%s" % (rc2, out2[-2000:])
    return {"compacted_files": int(m.group(1)), "bytes_saved": int(m.group(2)),
            "gate_rc": rc, "fixture_cases": int(fx.group(1)),
            "threshold_bytes": 4 * 1024 * 1024}


def s_hygiene(ctx):
    """交付包卫生：编译中间件 / 字节码 / 临时件一律不许随包交付。

    为什么它必须进矩阵而不是"交付前手工清一次"：上一版 README 写了"本轮已清"而盘上
    没清（评审 P1-1），那是"说了没做"，落在 4.1.5 一.2「杜绝虚构」上比不声明更伤。
    三通道合取：先清（HYGIENE_CLEAN_OK 且 remaining=0），再判（HYGIENE_OK hits=0），
    再跑夹具证明它会咬（HYGIENE_SELFTEST_OK），三个 rc 都要 0。
    清理必须住在这一步里：矩阵自己的构建步骤每次都会重新长出 .o/.a，
    一个只在末端"判"的门在这里永远红，而红了就靠人记得删 —— 那正是 P1-1 的原形状。
    """
    gate = os.path.join("flow", "check_package_hygiene.py")
    rc0, out0 = run([PY, gate, "--clean"])
    cm = re.search(r"HYGIENE_CLEAN_OK removed=(\d+) remaining=(\d+)", out0)
    assert rc0 == 0 and cm and cm.group(2) == "0",         "清不干净 rc=%d out=%s" % (rc0, out0[-1200:])
    rc, out = run([PY, gate])
    m = re.search(r"HYGIENE_OK scanned=(\d+) hits=(\d+)", out)
    assert rc == 0 and m and m.group(2) == "0",         "包内有中间件/临时件 rc=%d out=%s" % (rc, out[-1500:])
    rc2, out2 = run([PY, gate, "--selftest"])
    fx = re.search(r"HYGIENE_SELFTEST_OK (\d+)/(\d+)", out2)
    assert rc2 == 0 and fx and fx.group(1) == fx.group(2),         "卫生门的注入夹具没全过 rc=%d out=%s" % (rc2, out2[-1500:])
    return {"scanned": int(m.group(1)), "hits": int(m.group(2)),
            "cleaned": int(cm.group(1)), "still_there": int(cm.group(2)),
            "gate_rc": rc, "fixtures": "%s/%s" % (fx.group(1), fx.group(2))}


def s_deliverables(ctx):
    """交付件级验收：评委拿到的是 docx 与 pptx，不是 .md。

    这一支早就在包里，但**全包没有一条命令跑过它**（两份 README 的清单列过它，
    仅此而已）—— 那是"有判据、无执行"的典型形状。手动跑它一次，当场抓到两格真实红：
    docx / pptx 的 mtime 比报告 .md 旧。现在它进链条，排在 docx 与 ppt 两步之后。
    两通道合取：rc==0 **且**末行印出 ALL-DELIVERABLE-CHECKS-PASS（缺一不算过）。
    它在链条里带 --inside-matrix：那一节要读 matrix.json，而跑动中那份是上一趟的结论。
    """
    rc, out = run([PY, os.path.join("tools", "verify_deliverables.py"),
                   "--inside-matrix"])
    m = re.search(r"RESULT (\S+)", out)
    assert rc == 0 and m and m.group(1) == "ALL-DELIVERABLE-CHECKS-PASS", \
        "交付件级验收没过 rc=%d FAIL行数=%d out=%s" % (
            rc, out.count("  FAIL "), out[-1500:])
    return {"verdict": m.group(1), "fail_lines": out.count("  FAIL "),
            "checks": out.count("  OK   "), "gate_rc": rc}


def s_tableshape(ctx):
    """Markdown 表格形状：一格一列，不许拼行。

    为什么内容核对器不够：它查的是"每行的复现入口指指得到东西"，而一次 Edit 把两行
    拼成一行（本轮真实发生两次）时它照判 OK —— 读者看到的是整列错位。
    三通道：真实语料 0 问题、分母守卫（表体行数低于门槛即判"根本没扫到"而不是判绿）、
    夹具 5/5（其中一条专门防"把已转义的竖线数成多一格"的假红）。
    """
    gate = os.path.join("flow", "check_table_shape.py")
    rc, out = run([PY, gate])
    m = re.search(r"TABLE_SHAPE tables=(\d+) body_rows=(\d+) files=(\d+) problems=(\d+)",
                  out)
    assert rc == 0 and m and m.group(4) == "0", \
        "有表格形状不对的文件 rc=%d out=%s" % (rc, out[-1200:])
    assert m and int(m.group(2)) >= 100, \
        "表体只读到 %s 行：解析面塌了，不能读成「没有缺陷」" % (m.group(2) if m else "?")
    rc2, out2 = run([PY, gate, "--selftest"])
    fx = re.search(r"TABLE_SHAPE_SELFTEST_OK (\d+)/(\d+)", out2)
    assert rc2 == 0 and fx and fx.group(1) == fx.group(2), \
        "形状门的夹具没全过 rc=%d out=%s" % (rc2, out2[-1200:])
    return {"tables": int(m.group(1)), "body_rows": int(m.group(2)),
            "problems": int(m.group(4)),
            "fixtures": "%s/%s" % (fx.group(1), fx.group(2)), "gate_rc": rc}


def s_navigation(ctx):
    """把 results/ 与 results/logs/ 两份索引重新生成，排在 compact_traces 与
    matrix.json 落盘之后 —— 它以前挂在链条中段，于是它写的文件数、体积与
    `started_utc` 系统性落后一趟（实测：它写 1085.0 MiB，盘上 187.1 MiB）。

    它在链条里的身份是**写入者**（带 --write），不是核对者：索引的内容按构造每趟都变
    （每趟多一份日志），拿「盘上那份 == 现在重生成那份」当判据结构上永远不成立。
    承重的是它自带的那组分母守卫与「列出的路径必须盘上存在」那条断言。
    两通道：rc==0 且末行印出 NAVIGATION_WROTE 且逐键取到正数。
    """
    gate = os.path.join("tools", "make_navigation.py")
    rc, out = run([PY, gate, "--write"])
    m = re.search(r"NAVIGATION_WROTE files=(\d+) changed=\d+ results_entries=(\d+) "
                  r"unregistered=0 logs=(\d+) used_refs=(\d+)", out)
    assert rc == 0 and m, "导航索引没写成或没印出自己的判据 rc=%d out=%s" % (rc, out[-900:])
    vals = {k: int(g) for k, g in zip(
        ("files", "results_entries", "logs", "used_refs"), m.groups())}
    assert all(v > 0 for v in vals.values()), \
        "索引某一族分母塌成 0（那是解析面塌了，不是没有条目）: %s" % vals
    rc2, out2 = run([PY, gate, "--selftest"])
    fx = re.search(r"NAVIGATION_SELFTEST OK (\d+)/(\d+)", out2)
    assert rc2 == 0 and fx and fx.group(1) == fx.group(2), \
        "导航门的夹具没全过 rc=%d out=%s" % (rc2, out2[-900:])
    return dict(vals, fixtures="%s/%s" % (fx.group(1), fx.group(2)), gate_rc=rc)


def s_demo_artifacts(ctx):
    """成片（官方 4.1.5 一.5 点名的交付物）与现役归档之间的同源判据。

    为什么这一格必须存在：矩阵曾经 29 步全绿而**没有任何一步看过成片**，
    于是第 12 屏印的还是上一代矩阵的读数（快照 `iss_selftest.assertions=356`
    而现役归档是 365、快照头 21/21 而现役 29/29）。那句「全绿 = 全套证据成立」
    当时是有洞的。判据取内容级而不是哈希级，理由写在
    `flow/check_demo_artifacts.py` 的文件头：语料归档自带时间戳，
    逐份哈希相等这条判据每次合法复跑之后必然红，那是造一发永远红的门。
    """
    gate = os.path.join("flow", "check_demo_artifacts.py")
    rc, out = run([PY, gate])
    # 判决行按**键名**解，不按位置正则解。原来那条位置正则
    # `... agg_excluded=(\d+) snapshot_lines=(\d+) ...` 是那道门输出格式的第二份抄本：
    # 这一格今天加了 partial_skipped / footprint_excluded 两个排除计数，位置正则
    # 直接解不出来，于是链条把一条 rc=0 且写着 DEMO_ARTIFACTS_OK 的判决判成红
    # —— 症状长得像「成片不同源」，红因却是本步自己抄了别人的行格式。
    # 键名解 + 下面这条「必备键齐不齐」的守卫，既不复制顺序也不放过硬缺失。
    line = next((l for l in out.splitlines()
                 if l.startswith("DEMO_ARTIFACTS_OK") or l.startswith("DEMO_ARTIFACTS_FAIL")), "")
    toks = line.split()
    st = {}
    for tk in toks[1:]:
        if "=" in tk:
            k, v = tk.split("=", 1)
            st[k] = v
    must = ("readings", "drift", "volatile_excluded", "agg_excluded",
            "partial_skipped", "footprint_excluded", "snapshot_lines",
            "duration_s", "size_mib", "problems")
    gone = [k for k in must if k not in st]
    assert not gone, "同源门的判决行缺键 %s（它改了输出格式而本步的必备键清单没跟着改）: %s" % (
        gone, line[:200])
    assert rc == 0 and toks and toks[0] == "DEMO_ARTIFACTS_OK", \
        "成片与现役归档不同源 rc=%d out=%s" % (rc, out[-1600:])
    assert int(st["readings"]) > 0, "屏上承重读数的账是空的（分母塌了），不能读成同源成立"
    assert int(st["snapshot_lines"]) > 0, \
        "快照判据比了 0 行（捕获件里没解析到 PASS 行）—— 这一格在空转，不能读成同源成立"
    assert int(st["problems"]) == 0, "同源门自己数到 %s 条问题却末行判 OK" % st["problems"]
    rc2, out2 = run([PY, gate, "--selftest"])
    fx = re.search(r"DEMO_ARTIFACTS_SELFTEST (?:OK|FAIL) (\d+)/(\d+)", out2)
    assert rc2 == 0 and fx and fx.group(1) == fx.group(2), \
        "同源门的夹具没全过 rc=%d out=%s" % (rc2, out2[-1200:])
    return {"readings": int(st["readings"]), "drift": int(st["drift"]),
            "volatile_excluded": int(st["volatile_excluded"]),
            "agg_excluded": int(st["agg_excluded"]),
            "partial_skipped": int(st["partial_skipped"]),
            "footprint_excluded": int(st["footprint_excluded"]),
            "snapshot_lines": int(st["snapshot_lines"]),
            "duration_s": float(st["duration_s"]), "size_mib": float(st["size_mib"]),
            "audio": st.get("audio", ""), "problems": int(st["problems"]),
            "fixtures": "%s/%s" % (fx.group(1), fx.group(2)), "gate_rc": rc}


POST_STEPS = [("archive_clobber", s_clobber), ("compact_traces", s_compact),
              ("deliverables", s_deliverables), ("table_shape", s_tableshape),
              ("navigation", s_navigation), ("demo_artifacts", s_demo_artifacts),
              ("package_hygiene", s_hygiene)]
N_TOTAL = len(STEPS) + len(POST_STEPS)


def emit(started, rows, only):
    """落 matrix.json; all_green 只认「跑满 N_TOTAL 格且无 FAIL 无 SKIP」。

    跑子集时**不碰 matrix.json** —— 它改写到
    results/_probe_subset_NOT_A_VERDICT.json（名字里就写着「这不是判决」；
    以前它叫 matrix_subset.json，与认证归档只差一个词，误读风险实测存在）。
    起因是实测: 本文件以前没有 --list 这个开关, 而参数是被当成步名过滤器用的,
    于是 `python flow/run_all.py --list` 会「一个步都没匹配上」然后把认证矩阵
    覆成 steps=0/all_green=false —— 一次手滑就洗掉了全套证据的结论, 而它自己
    一个字都不报错。子集跑本来就不该有资格写整表结论。
    """
    nfail = sum(1 for r in rows if r["status"] == "FAIL")
    nskip = sum(1 for r in rows if r["status"] == "SKIP")
    out = {}
    if only:
        # 让「这不是交付结论」写在文件自己身上, 而不是只写在 README 顶部（评审 P2-14）。
        # 放在 dict 开头, 于是 json 里它也是第一个键。
        out["_warning"] = (
            "PROBE_NOT_A_VERDICT: 这一份是只跑子集的探针产物。未跑的步在这里记 SKIP, "
            "所以 all_green 必然为 false —— 那不是矩阵崩了。整表结论只看 "
            "results/matrix.json。本次过滤的步名: %s" % ",".join(sorted(only)))
    out.update({"started_utc": started, "steps": len(rows),
           "n_pass": sum(1 for r in rows if r["status"] == "PASS"),
           "failed": nfail, "skipped": nskip,
           "n_total_in_table": N_TOTAL,
           "all_green": nfail == 0 and nskip == 0 and len(rows) == N_TOTAL,
           "ran_subset": bool(only), "rows": rows,
           "rtl_sha256_head": hashlib.sha256(
               open(os.path.join("rtl", "epv32_core.v"), "rb").read()).hexdigest()[:16]})
    os.makedirs("results", exist_ok=True)
    write_json_atomic(os.path.join("results", matrix_path(only)), out)
    return out


def matrix_path(only):
    """整表跑才写 matrix.json; 子集跑另开一份, 免得一次手滑洗掉认证结论。"""
    return "matrix.json" if not only else "_probe_subset_NOT_A_VERDICT.json"


def parse_args(argv):
    """把参数分成「--list 这种开关」与「步名」两类, 并且**跑之前**就把不认识的
    名字判死。返回 (list_only, only_names)。

    以前的形状是 only = argv or None —— 任何拼错的步名都会匹配到 0 个步,
    于是这趟「什么都没跑」却仍然把 matrix.json 覆了一遍。那是自毁, 不是宽松。
    """
    if "--list" in argv:
        return True, None
    bad = [a for a in argv if a.startswith("-")]
    if bad:
        raise SystemExit("未知开关 %s ; 本表认识的只有 --list 与步名。%s"
                         % (" ".join(bad), " ".join("run_all.py --list 看全集")))
    known = set(n for n, _f in STEPS)
    unknown = [a for a in argv if a not in known]
    if unknown:
        raise SystemExit("未知步名 %s ; 本表只有 %s"
                         % (" ".join(unknown), " ".join(sorted(known))))
    return False, (set(argv) if argv else None)


def main():
    list_only, only = parse_args(sys.argv[1:])
    if list_only:
        for i, (name, _fn) in enumerate(STEPS, 1):
            print("%2d  %s" % (i, name))
        for name, _fn in POST_STEPS:
            print("%2d  %s   (后置格: 读本趟刚落盘的矩阵)" % (N_TOTAL, name))
        print("N_TOTAL=%d (前 %d 步产证据)" % (N_TOTAL, len(STEPS)))
        return 0
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    ctx, rows = {}, []
    for name, fn in STEPS:
        if only and name not in only:
            continue
        t0 = time.time()
        try:
            ev = fn(ctx)
            rows.append({"step": name, "status": "PASS",
                         "wall_s": round(time.time() - t0, 1), "evidence": ev})
            print("PASS  %-16s %s" % (name, json.dumps(ev, ensure_ascii=False)),
                  flush=True)
        except Exception as e:
            rows.append({"step": name, "status": "FAIL",
                         "wall_s": round(time.time() - t0, 1), "error": str(e)[:2400]})
            print("FAIL  %-16s %s" % (name, str(e)[:220]), flush=True)

    # 后置格必须先让矩阵落盘, 它核的就是这份文件
    out = emit(started, rows, only)
    print("--- %s 已落盘(%d/%d), 跑后置同源核对 ---"
          % ("results/" + matrix_path(only), out["n_pass"], out["steps"]))
    for name, fn in POST_STEPS:
        t0 = time.time()
        if only:
            rows.append({"step": name, "status": "SKIP", "wall_s": 0.0,
                         "reason": "只跑了子集, 本趟结论在 results/%s 而不是 matrix.json"
                                   % matrix_path(only)})
            print("SKIP  %-16s ran_subset" % name, flush=True)
            continue
        try:
            ev = fn(ctx)
            rows.append({"step": name, "status": "PASS",
                         "wall_s": round(time.time() - t0, 1), "evidence": ev})
            print("PASS  %-16s %s" % (name, json.dumps(ev, ensure_ascii=False)),
                  flush=True)
        except Exception as e:
            rows.append({"step": name, "status": "FAIL",
                         "wall_s": round(time.time() - t0, 1), "error": str(e)[:2400]})
            print("FAIL  %-16s %s" % (name, str(e)[:220]), flush=True)
    out = emit(started, rows, only)
    # 逐行 flush 的理由只有一句话：flow/demo.sh 把本表的 stdout 直接当捕获件
    # （results/demo/09_matrix.txt），而链条最后一格 demo_artifacts 读的就是这个文件。
    # Python 对文件是 8 KB 块缓冲 —— 实测 12:57 那一趟全程只落了 4448 字节，于是那一格
    # 读到空文件、报出「快照判据在空转」。那是仪器故障，不是片子的问题。
    print("MATRIX  %d/%d  all_green=%s  -> results/%s"
          % (out["n_pass"], out["steps"], out["all_green"], matrix_path(only)), flush=True)
    if only:
        print("NOTE    这一趟只跑了子集, %s 没被本次运行改过。"
              % os.path.join("results", "matrix.json"))
    return 0 if out["all_green"] else 1


if __name__ == "__main__":
    sys.exit(main())
