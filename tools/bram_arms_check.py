#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/bram_arms_check.py -- 核那八臂 BRAM 模板/容量实测的归档，**不重跑 TD**。

为什么需要它：正文与附件 A 断言「换 BRAM 模板也装不下」「TD 在 gate 阶段不拦容量溢出」，
凭据是一次约 40 分钟的 TD 批次（两臂在 900 s 上限内没返回），太贵不能进链条，
故链条里跑这一发便宜的核对，规则是：

 1. 臂源码没被人动过      sha256(<arm>.v) == 归档记的            否则 DRIFT-SOURCE
 2. 归档没有说谎          归档记的每一行 verdict 逐字还在原始 log 里   否则 LIES
 3. 结论三条仍然成立      异步读各臂「0 Logic BRAMs」；bram 模板在
                          256 Kbit 时 #bram<=64；在 2048 Kbit 时 #bram>64
                          **且 TD 的 rc 仍为 0**（这条是最容易被误读成一格的：
                          「报了 BRAM」不等于「装得下」）
 4. 分母守卫              八臂齐全、capacity 解得出、area 报告在

跑法与夹具（`--selftest`）里有一条**正对照**：只改时间戳必须仍然绿 ——
否则这就是一发每次复跑都会红的门，而那种门的下场是被删。

    python tools/bram_arms_check.py            # 判盘上那份归档
    python tools/bram_arms_check.py --selftest
退出码 0 通过 / 1 内容缺陷 / 2 缺料（SETUP-FAIL，不等于「没问题」）。
"""
from __future__ import print_function
import hashlib
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
ARCH = os.path.join("results", "pnr", "bram_template_arms.json")
DIR = os.path.join("results", "pnr", "bram_template_arms")
FIXTURES = ("healthy", "archive-lies", "source-drift", "missing-arm",
            "timestamp-only-changed", "overflow-rc-noted")


def sha(p):
    return hashlib.sha256(io.open(p, "rb").read()).hexdigest()


def read(p):
    return io.open(p, encoding="utf-8", errors="replace").read()


def bram_count(line):
    m = re.search(r"#bram\s+(\d+)\s+out of\s+(\d+)", line or "")
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def audit(root=ROOT):
    P = []
    ap = os.path.join(root, ARCH)
    if not os.path.isfile(ap):
        return [("SETUP", "缺归档 %s" % ARCH)], {}
    d = json.loads(read(ap))
    arms = d.get("arms") or []
    if len(arms) < 8:
        P.append(("DENOM", "只登记了 %d 臂（应为 8）—— 分母塌了" % len(arms)))
    by = {}
    async_zero = async_tot = 0
    ov_used = ov_cap = ov_rc = None
    fit_used = None
    for a in arms:
        name = a.get("arm")
        adir = os.path.join(root, DIR, name)
        v = os.path.join(adir, name + ".v")
        out = os.path.join(adir, name + ".out")
        area = os.path.join(adir, "gate.area")
        if not (os.path.isfile(v) and os.path.isfile(out)):
            P.append(("MISSING", "%s 的源码或 log 不在包内" % name))
            continue
        if sha(v) != a.get("source_sha256"):
            P.append(("DRIFT-SOURCE", "%s 的 .v 与归档记的 sha 不符 ⇒ 这批臂已被人改过，"
                                      "结论要重跑: %s" % (name, os.path.join(DIR, name,
                                                                            name + ".v"))))
        for k in ("infer_summary", "bram_total_line", "qor_ram_line"):
            v2 = a.get(k)
            if not v2:
                continue
            hay = read(out) if k == "infer_summary" else (
                read(area) if k == "bram_total_line" else
                read(os.path.join(adir, "gate.qor")))
            if v2 not in hay:
                P.append(("LIES", "%s 的 %s 记为 %r，但它声称来自的那份文件里没有这一行"
                          % (name, k, v2[:46])))
        infer = a.get("infer_summary") or ""
        if name.startswith("ram_async"):
            async_tot += 1
            if "0 Logic BRAMs" in infer:
                async_zero += 1
        used, cap = bram_count(a.get("bram_total_line"))
        if name == "ram_bram_256k":
            ov_used, ov_cap, ov_rc = used, cap, a.get("rc")
        if name == "ram_bram_32k":
            fit_used = used
        by[name] = (used, cap)
    if async_tot and async_zero != async_tot:
        P.append(("VERDICT", "异步读各臂应当**全部**推不出 BRAM，实得 %d/%d"
                  % (async_zero, async_tot)))
    if fit_used is None or fit_used > 64:
        P.append(("VERDICT", "注册读模板在 256 Kbit 那臂应当装进器件，实得 %s" % fit_used))
    if ov_used is None or ov_cap is None or ov_used <= ov_cap:
        P.append(("VERDICT", "2048 Kbit 那臂应当溢出（used>capacity），实得 %s/%s"
                  % (ov_used, ov_cap)))
    if ov_rc not in (0, None) or ov_rc is None:
        P.append(("VERDICT", "溢出那臂的 rc 记录不可读（=%s）—— 「TD 不拦容量溢出」这条"
                             "结论要求它确实跑完了且退出码为 0" % ov_rc))
    return P, {"arms": len(arms), "async_zero_bram": "%d/%d" % (async_zero, async_tot),
               "fit_used": fit_used, "overflow": "%s/%s" % (ov_used, ov_cap),
               "overflow_rc": ov_rc}


def selftest():
    fails = []
    src_dir = os.path.join(ROOT, DIR)
    base = tempfile.mkdtemp(prefix="epv32_bramt_")
    try:
        house = os.path.join(base, "pkg")
        os.makedirs(os.path.dirname(os.path.join(house, ARCH)))
        shutil.copytree(src_dir, os.path.join(house, DIR))
        shutil.copy2(os.path.join(ROOT, ARCH), os.path.join(house, ARCH))
        P, st = audit(house)
        if P:
            fails.append("healthy: 替身房被判红 %s" % (P[:2],))
        if st.get("arms") != 8:
            fails.append("healthy: 替身房里只数到 %s 臂" % st.get("arms"))

        def probs(h):
            return audit(h)[0]

        # 1) 归档说谎：把某条 verdict 行改成一句 log 里不存在的话
        p = os.path.join(house, ARCH)
        d = json.loads(read(p))
        d["arms"][0]["infer_summary"] = "SYN-1023 : Infer 99 Logic BRAMs, 99 Logic DRAMs."
        json.dump(d, io.open(p, "w", encoding="utf-8"), ensure_ascii=False)
        if not any(c == "LIES" for c, _ in probs(house)):
            fails.append("archive-lies: 改了归档里的 verdict 行竟然没红")
        # 2) 源码漂移：只动 .v（加一行注释）→ 必须点名 DRIFT-SOURCE
        d = json.loads(read(p))
        d["arms"][0]["infer_summary"] = json.loads(read(os.path.join(ROOT, ARCH)))["arms"][0]["infer_summary"]
        json.dump(d, io.open(p, "w", encoding="utf-8"), ensure_ascii=False)
        arm0 = d["arms"][0]["arm"]
        vp = os.path.join(house, DIR, arm0, arm0 + ".v")
        io.open(vp, "a", encoding="utf-8").write(u"\n// tampered\n")
        if not any(c == "DRIFT-SOURCE" for c, _ in probs(house)):
            fails.append("source-drift: 改了臂源码竟然没红")
        shutil.copy2(os.path.join(src_dir, arm0, arm0 + ".v"), vp)
        if probs(house):
            fails.append("复原后仍红: %s" % (probs(house)[:1],))
        # 3) 缺一臂 → MISSING，不能读成「没问题」
        os.remove(os.path.join(house, DIR, arm0, arm0 + ".out"))
        if not any(c in ("MISSING", "DENOM") for c, _ in probs(house)):
            fails.append("missing-arm: 删掉一份原始 log 竟然没红")
        shutil.copy2(os.path.join(src_dir, arm0, arm0 + ".out"),
                     os.path.join(house, DIR, arm0, arm0 + ".out"))
        # 4) 正对照：只改时间戳必须仍然绿（否则这就是永红的门）
        d = json.loads(read(p))
        for a in d["arms"]:
            a["start_utc"] = "2031-01-01T00:00:00Z"
            a["end_utc"] = "2031-01-01T00:00:01Z"
        json.dump(d, io.open(p, "w", encoding="utf-8"), ensure_ascii=False)
        if probs(house):
            fails.append("timestamp-only-changed: 只改时间戳就判红（门太严，会被删）: %s"
                         % (probs(house)[:1],))
        # 5) 溢出那臂的 rc 被改成非 0 ⇒ 「TD 不拦容量溢出」这条结论失去凭据
        d = json.loads(read(p))
        for a in d["arms"]:
            if a["arm"] == "ram_bram_256k":
                a["rc"] = 3
        json.dump(d, io.open(p, "w", encoding="utf-8"), ensure_ascii=False)
        if not any(c == "VERDICT" for c, _ in probs(house)):
            fails.append("overflow-rc-noted: 溢出那臂的 rc 不再为 0 竟然没红")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    for f in fails:
        print("SELFTEST-FAIL " + f.encode("ascii", "backslashreplace").decode("ascii"))
    print("BRAM_ARMS_SELFTEST %s %d/%d" % ("OK" if not fails else "FAIL",
                                           len(FIXTURES) - len(fails), len(FIXTURES)))
    return 1 if fails else 0


def main(argv):
    if "--selftest" in argv:
        return selftest()
    P, st = audit(ROOT)
    if P and all(c == "SETUP" for c, _ in P):
        for c, m in P:
            print("SETUP-FAIL " + m.encode("ascii", "backslashreplace").decode("ascii"))
        print("BRAM_ARMS_SETUP_FAIL")
        return 2
    for c, m in P:
        print("PROBLEM [%s] %s" % (c, m.encode("ascii", "backslashreplace").decode("ascii")))
    print("BRAM_ARMS_%s arms=%s async_zero_bram=%s fit_256kbit=%s overflow=%s "
          "overflow_rc=%s problems=%d"
          % ("OK" if not P else "FAIL", st.get("arms"), st.get("async_zero_bram"),
             st.get("fit_used"), st.get("overflow"), st.get("overflow_rc"), len(P)))
    return 0 if not P else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
