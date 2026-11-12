# -*- coding: utf-8 -*-
"""
sim_fingerprint.py -- 「盘上这个仿真器二进制是不是由现在这几份源文件编出来的」

不判 mtime（两个方向都会错）：内容没变但 mtime 更新会假红，源比目标新而未重链会假绿。
改为输入指纹：把真正喂给编译器的东西（RTL 源、C++ 测试台、build_sim.sh 配方、臂宏与
覆盖率开关）逐个取 sha256 写进 <MDIR>/<OUT>.rtlfp.json。判据三条合取：侧车在；记录的
指纹 == 现算指纹；记录的 exe sha == 盘上 exe sha。任何一条不成立 => plan() 报 stale。

用法:
  python flow/sim_fingerprint.py --plan cert       # 只读: 要不要重编 + 为什么
  python flow/sim_fingerprint.py --record cert     # 构建完成后记账
  python flow/sim_fingerprint.py --verify cert     # 三条合取, 打印读数
  python flow/sim_fingerprint.py --selftest        # 夹具 (全在沙盒里跑)
退出码: 0 好 / 1 判红 / 2 用法或夹具之外的故障。判决行只有 ASCII。
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

# 每一臂的构建目录、可执行名与「换了这一串就不是同一个二进制」的开关。
# 三臂各占一个目录一个名字是 build_sim.sh 的既有约定（认证臂的二进制被归档按
# sim_sha256 点名，断言臂与覆盖率臂绝不许原地覆盖它）。
ARMS = {
    "cert":   {"mdir": os.path.join("build", "sim"),
               "out": "epv32sim",
               "recipe_extra": ""},
    "assert": {"mdir": os.path.join("build", "sim_assert"),
               "out": "epv32sim_assert",
               "recipe_extra": "+define+EPV_ASSERTIONS"},
    "cov":    {"mdir": os.path.join("build", "sim_cov"),
               "out": "epv32sim_cov",
               "recipe_extra": "--coverage-line --coverage-toggle --coverage-user"},
}

# rtl/synth_wrappers.v 只被综合脚本读，不参与仿真器 —— 把它算进来会让每一个
# 只动后端的改动都白白重编一次仿真器（也白白换一次 sim_sha256）。
RTL_EXCLUDE = {"synth_wrappers.v"}
TB_EXTRA = [os.path.join("tb", "sim_main.cpp")]
RECIPE_FILES = [os.path.join("flow", "build_sim.sh")]


def digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def sources(arm, root=ROOT):
    """这一臂的全部输入源文件（相对 root 的路径），排序后返回。"""
    if arm not in ARMS:
        raise KeyError("unknown arm %r" % arm)
    rtl_dir = os.path.join(root, "rtl")
    files = []
    if os.path.isdir(rtl_dir):
        for name in sorted(os.listdir(rtl_dir)):
            if name.endswith((".v", ".vh")) and name not in RTL_EXCLUDE:
                files.append(os.path.join("rtl", name))
    for rel in TB_EXTRA + RECIPE_FILES:
        if os.path.isfile(os.path.join(root, rel.replace("/", os.sep))):
            files.append(rel)
    return files


def fingerprint(arm, root=ROOT):
    """(指纹 hex, [(相对路径, 文件 sha256), ...])。

    源文件集合为空时 raise —— 那不是「没有输入所以永远新鲜」，那是本步
    无条件通过，正是要防的形状。"""
    files = sources(arm, root)
    if not files:
        raise RuntimeError("FINGERPRINT-EMPTY source set is empty for arm %s" % arm)
    rows = []
    for rel in files:
        p = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(p):
            raise RuntimeError("FINGERPRINT-MISSING %s" % rel)
        rows.append((rel.replace(os.sep, "/"), digest(p)))
    blob = "arm=%s extra=%s\n" % (arm, ARMS[arm]["recipe_extra"])
    for rel, sha in rows:
        blob += "%s %s\n" % (rel, sha)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest(), rows


def exe_path(arm, root=ROOT):
    p = os.path.join(root, ARMS[arm]["mdir"].replace("/", os.sep),
                     ARMS[arm]["out"] + ".exe")
    if not os.path.isfile(p):
        p2 = p[:-4] if p.endswith(".exe") else p
        p = p2 if os.path.isfile(p2) else p
    return p


def sidecar_path(arm, root=ROOT):
    return os.path.join(root, ARMS[arm]["mdir"].replace("/", os.sep),
                        ARMS[arm]["out"] + ".rtlfp.json")


def read_sidecar(arm, root=ROOT):
    p = sidecar_path(arm, root)
    if not os.path.isfile(p):
        return None
    try:
        return json.load(io.open(p, encoding="utf-8"))
    except Exception as ex:
        raise RuntimeError("SIDECAR-UNREADABLE %s: %r" % (p, ex))


def write_sidecar(arm, root=ROOT):
    """把「现在这几份源 == 盘上这个 exe」记下来。只在构建成功后调用。"""
    fp, rows = fingerprint(arm, root)
    exe = exe_path(arm, root)
    if not os.path.isfile(exe):
        raise RuntimeError("RECORD-NO-EXE %s" % exe)
    rec = {"arm": arm,
           "fingerprint": fp,
           "exe_sha256": digest(exe),
           "exe_bytes": os.path.getsize(exe),
           "n_sources": len(rows),
           "sources": dict(rows)}
    d = os.path.dirname(sidecar_path(arm, root))
    if not os.path.isdir(d):
        os.makedirs(d)
    tmp = sidecar_path(arm, root) + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(rec, f, indent=1, sort_keys=True)
    os.replace(tmp, sidecar_path(arm, root))
    return rec


def plan(arm, root=ROOT):
    """只读判决：要不要重编，以及是哪一条不成立。"""
    fp, rows = fingerprint(arm, root)
    rec = read_sidecar(arm, root)
    exe = exe_path(arm, root)
    reasons = []
    if rec is None:
        reasons.append("no-sidecar")
    else:
        if rec.get("fingerprint") != fp:
            reasons.append("fingerprint-mismatch")
        if not os.path.isfile(exe):
            reasons.append("exe-missing")
        elif rec.get("exe_sha256") != digest(exe):
            reasons.append("exe-sha-mismatch")
    return {"arm": arm, "stale": bool(reasons), "reasons": reasons,
            "fingerprint": fp, "n_sources": len(rows),
            "exe": os.path.basename(exe),
            "exe_present": os.path.isfile(exe),
            "exe_sha256": digest(exe) if os.path.isfile(exe) else None,
            "recorded": (rec or {}).get("fingerprint"),
            "recorded_exe_sha256": (rec or {}).get("exe_sha256")}


def verify(arm, root=ROOT):
    """重编之后的合取核对：不等就 raise（调用方判红，而不是悄悄再编一次）。"""
    pl = plan(arm, root)
    if pl["stale"]:
        raise RuntimeError("SIM-FP-MISMATCH arm=%s reasons=%s recorded=%s now=%s "
                           "exe_sha=%s recorded_exe_sha=%s"
                           % (arm, ",".join(pl["reasons"]), str(pl["recorded"])[:12],
                              pl["fingerprint"][:12], str(pl["exe_sha256"])[:12],
                              str(pl["recorded_exe_sha256"])[:12]))
    return pl


def cross_check(arm, root=ROOT):
    """侧车里逐文件的 sha 与现在重算的逐文件 sha 差几份 —— 判决行要带这条，
    因为「总指纹相等」已经蕴含它为 0，但把 0 印出来才挡得住「分母塌了」。"""
    rec = read_sidecar(arm, root)
    _fp, rows = fingerprint(arm, root)
    if rec is None:
        return len(rows), -1
    now = dict(rows)
    got = rec.get("sources") or {}
    diff = sum(1 for k in set(now) | set(got) if now.get(k) != got.get(k))
    return len(rows), diff


# --------------------------------------------------------------- 夹具自测
FIXTURE_NAMES = (
    "identical-rewrite-stays-fresh",
    "one-byte-change-forces-rebuild",
    "missing-sidecar-forces-rebuild",
    "swapped-exe-forces-rebuild",
    "recipe-file-change-forces-rebuild",
    "cross-check-zero-when-fresh",
    "cross-check-names-the-file",
    "empty-source-set-raises",
    "real-arm-plan-is-shaped",
)


def _sandbox():
    """造一棵最小的假树：rtl/ 两个文件 + tb/sim_main.cpp + flow/build_sim.sh
    + build/sim/epv32sim.exe。夹具一律在沙盒里跑，绝不碰真的构建目录。"""
    root = tempfile.mkdtemp(prefix="simfp_")
    for d in ("rtl", os.path.join("tb"), os.path.join("flow"),
              os.path.join("build", "sim")):
        os.makedirs(os.path.join(root, d))
    w = os.path.join(root, "rtl", "a.v")
    io.open(w, "w", encoding="utf-8", newline="\n").write("module a; endmodule\n")
    io.open(os.path.join(root, "rtl", "b.vh"), "w", encoding="utf-8",
            newline="\n").write("`define B 1\n")
    io.open(os.path.join(root, "rtl", "synth_wrappers.v"), "w", encoding="utf-8",
            newline="\n").write("module w; endmodule\n")
    io.open(os.path.join(root, "tb", "sim_main.cpp"), "w", encoding="utf-8",
            newline="\n").write("int main(){return 0;}\n")
    io.open(os.path.join(root, "flow", "build_sim.sh"), "w", encoding="utf-8",
            newline="\n").write("#!/bin/bash\necho ok\n")
    io.open(os.path.join(root, "build", "sim", "epv32sim.exe"), "w",
            encoding="utf-8", newline="\n").write("EXE-BYTES-V1\n")
    return root


def _touch_same_bytes(path):
    """把文件按**完全相同**的内容重写一遍，并把 mtime 推到未来 ——
    这就是 A/B 与 assert_selfcheck 的 restore() 留下的现场。"""
    txt = io.open(path, encoding="utf-8", newline="").read()
    io.open(path, "w", encoding="utf-8", newline="").write(txt)
    st = os.stat(path)
    os.utime(path, (st.st_atime + 3600, st.st_mtime + 3600))


def _fx_identical_rewrite(root):
    _sandbox_from(root)
    write_sidecar("cert", root)
    _touch_same_bytes(os.path.join(root, "rtl", "a.v"))
    pl = plan("cert", root)
    if pl["stale"]:
        return "still stale after identical rewrite: %s" % pl["reasons"]
    return None


def _fx_one_byte(root):
    _sandbox_from(root)
    write_sidecar("cert", root)
    p = os.path.join(root, "rtl", "a.v")
    txt = io.open(p, encoding="utf-8", newline="").read()
    io.open(p, "w", encoding="utf-8", newline="").write(txt.replace("a;", "a2;"))
    pl = plan("cert", root)
    if not pl["stale"] or "fingerprint-mismatch" not in pl["reasons"]:
        return "one-byte change not detected: %s" % pl["reasons"]
    return None


def _fx_missing_sidecar(root):
    _sandbox_from(root)
    write_sidecar("cert", root)
    os.remove(sidecar_path("cert", root))
    pl = plan("cert", root)
    if pl["reasons"] != ["no-sidecar"]:
        return "expected exactly no-sidecar, got %s" % pl["reasons"]
    return None


def _fx_swapped_exe(root):
    _sandbox_from(root)
    write_sidecar("cert", root)
    io.open(exe_path("cert", root), "w", encoding="utf-8",
            newline="\n").write("EXE-BYTES-TAMPERED\n")
    pl = plan("cert", root)
    if "exe-sha-mismatch" not in pl["reasons"]:
        return "tampered exe not detected: %s" % pl["reasons"]
    return None


def _fx_recipe_change(root):
    _sandbox_from(root)
    write_sidecar("cert", root)
    p = os.path.join(root, "flow", "build_sim.sh")
    io.open(p, "w", encoding="utf-8", newline="\n").write(
        "#!/bin/bash\necho ok\n# flag change\n")
    pl = plan("cert", root)
    if "fingerprint-mismatch" not in pl["reasons"]:
        return "recipe change not detected: %s" % pl["reasons"]
    return None


def _fx_empty_set(root):
    empty = tempfile.mkdtemp(prefix="simfp_empty_")
    os.makedirs(os.path.join(empty, "build", "sim"))
    try:
        fingerprint("cert", empty)
    except RuntimeError as ex:
        if "FINGERPRINT-EMPTY" not in str(ex):
            return "wrong error: %r" % str(ex)[:80]
        return None
    finally:
        shutil.rmtree(empty, ignore_errors=True)
    return "empty source set did not raise"


def _fx_real_arm_shape(root):
    """正对照：真的那一臂也要能解出一个形状完整的 plan。
    只核形状，不核 stale 与否 —— 侧车可能还没记（那是 --rebuild 的事）。"""
    pl = plan("cert", ROOT)
    for k in ("fingerprint", "n_sources", "reasons", "exe", "stale"):
        if k not in pl:
            return "missing key %s" % k
    if len(pl["fingerprint"]) != 64:
        return "fingerprint not 64 hex: %r" % pl["fingerprint"]
    if pl["n_sources"] < 5:
        return "suspiciously small source set: %s" % pl["n_sources"]
    return None


def _sandbox_from(root):
    """夹具共用一个已建好的沙盒：把 _sandbox() 的骨架拷进给定 root。"""
    src = _sandbox()
    for dirpath, _dirs, names in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        dst = os.path.normpath(os.path.join(root, rel))
        if not os.path.isdir(dst):
            os.makedirs(dst)
        for n in names:
            shutil.copyfile(os.path.join(dirpath, n),
                            os.path.join(dst, n))
    shutil.rmtree(src, ignore_errors=True)


def _fx_cross_zero(root):
    _sandbox_from(root)
    write_sidecar("cert", root)
    n, diff = cross_check("cert", root)
    if diff != 0:
        return "fresh pair reports %d mismatched files" % diff
    if n < 3:
        return "denominator collapsed: n_sources=%d" % n
    return None


def _fx_cross_names(root):
    """侧车被人手改过一格（总指纹没跟着改）—— cross_check 必须数得出是 1 份。
    这条挡住的是「只核总指纹、逐文件那本账其实是第二份手抄」这一格。"""
    _sandbox_from(root)
    write_sidecar("cert", root)
    p = sidecar_path("cert", root)
    d = json.load(io.open(p, encoding="utf-8"))
    d["sources"]["rtl/a.v"] = "0" * 64
    io.open(p, "w", encoding="utf-8", newline="\n").write(json.dumps(d))
    _n, diff = cross_check("cert", root)
    if diff != 1:
        return "expected exactly 1 mismatched file, got %s" % diff
    return None


FIXTURES = {
    "identical-rewrite-stays-fresh": _fx_identical_rewrite,
    "one-byte-change-forces-rebuild": _fx_one_byte,
    "missing-sidecar-forces-rebuild": _fx_missing_sidecar,
    "swapped-exe-forces-rebuild": _fx_swapped_exe,
    "recipe-file-change-forces-rebuild": _fx_recipe_change,
    "cross-check-zero-when-fresh": _fx_cross_zero,
    "cross-check-names-the-file": _fx_cross_names,
    "empty-source-set-raises": _fx_empty_set,
    "real-arm-plan-is-shaped": _fx_real_arm_shape,
}


def selftest():
    ok = 0
    for name in FIXTURE_NAMES:
        fn = FIXTURES.get(name)
        if fn is None:
            print("FAIL no implementation for %s" % name)
            continue
        root = tempfile.mkdtemp(prefix="simfp_fx_")
        try:
            bad = fn(root)
        except Exception as ex:
            bad = "raised %r" % (ex,)
        finally:
            shutil.rmtree(root, ignore_errors=True)
        if bad:
            print("FAIL %s: %s" % (name, bad))
        else:
            ok += 1
            print("ok   %s" % name)
    tot = len(FIXTURE_NAMES)
    print("SIM_FINGERPRINT_SELFTEST %s %d/%d" %
          ("OK" if ok == tot else "FAIL", ok, tot))
    return 0 if ok == tot else 1


def main(argv):
    if not argv or argv[0] not in ("--plan", "--record", "--verify", "--selftest"):
        print("usage: sim_fingerprint.py --plan|--record|--verify <arm> | --selftest")
        return 2
    if argv[0] == "--selftest":
        return selftest()
    arm = argv[1] if len(argv) > 1 else "cert"
    if arm not in ARMS:
        print("SIM_FINGERPRINT_FAIL unknown-arm=%s" % arm)
        return 2
    if argv[0] == "--record":
        rec = write_sidecar(arm)
        print("SIM_FINGERPRINT_RECORDED arm=%s fp=%s exe_sha=%s n_sources=%s"
              % (arm, rec["fingerprint"][:12], rec["exe_sha256"][:12],
                 rec["n_sources"]))
        return 0
    pl = plan(arm)
    print("SIM_FINGERPRINT arm=%s stale=%s reasons=%s fp=%s recorded=%s "
          "exe=%s exe_sha=%s recorded_exe_sha=%s n_sources=%s"
          % (arm, pl["stale"], ",".join(pl["reasons"]) or "-",
             pl["fingerprint"][:12], str(pl["recorded"])[:12], pl["exe"],
             str(pl["exe_sha256"])[:12], str(pl["recorded_exe_sha256"])[:12],
             pl["n_sources"]))
    if argv[0] == "--verify":
        try:
            verify(arm)
        except Exception as ex:
            print("SIM_FINGERPRINT_VERIFY FAIL %s" % str(ex)[:160])
            return 1
        n, diff = cross_check(arm)
        # mismatched_files 直接印差值而不是印一个 0：判据在调用方按「=0」收，
        # 这一行本身要能把「侧车记的逐文件 sha 与现在重算的不一样」说出来。
        print("SIM_FINGERPRINT_VERIFY %s arm=%s n_sources=%d mismatched_files=%d"
              % ("OK" if diff == 0 else "FAIL", arm, n, diff))
        return 0 if diff == 0 else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
