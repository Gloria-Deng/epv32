#!/usr/bin/env python3
"""访存代价敏感性: 把「15.73× 是理想单周期存储下的上界」这句自认弱点换成一条曲线。

评审第 9 条要的是「给权重 LVB 命中/未命中的周期模型曲线, 把 15.73× 的上界性质
定量化」。本工具的做法是: 每一个进入曲线的量都从**已归档的两个读数**或**退休轨迹
在计时窗口内的实测**里取, 一个都不靠手写的结构比例。

窗口怎么来的: 测试台逐条打印 (pc, rd, wdata) 与总线写, 而软件侧用 mcycle/minstret
成对读数圈出基线臂与 EPV 臂。所以「窗口」的边界就是那三条读 minstret 的指令本身 ——
与硬件用的是同一个事件, 不是按函数名猜的。判据要求恰好三条, 且第一条之后的段里没有
任何自定义指令(它才是基线臂)、第二条之后的段里有 VDOT8(它才是 EPV 臂); 形状不符
直接判 UNAVAILABLE, 不兜底。

哪些量是量出来的、哪些只能给区间:
  量出来  : 窗口内写回寄存器的指令条数 R、总线写条数 S(含 byte-enable 直方图)、
            标量取数条数 LD、自定义指令逐类计数、窗口周期与退休数(归档)
  看不见  : 只写向量寄存器的指令(LVB/VREQ4/…) 与不写 rd 的分支 —— 端口按设计不打印
  于是     : EPV 臂的向量装载字数给**区间**而不是点估计。下界 = 只算打印得到的字;
            上界 = 把「退休数 − R − S」这一整段不可见指令全部当成 4 字节的 LVB。
            曲线随字数单调下降, 所以上界那一侧就是加速比的**悲观下界** —— 交付的是
            「再怎么罚也还剩多少」, 不是「猜一个中间值」。
  取指     : 核没有 I-cache, 每条指令占一个整字, 所以取指字数 = 归档退休数(实测)。
            这条单独画一组: 「只罚数据」与「数据+取指一起罚」。

输出 results/bench/mem_sensitivity.json。--selftest 用注入证明判据会咬。
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, "flow"))

BASE = 0x80000000
OUT = os.path.join("results", "bench", "mem_sensitivity.json")
WPTS = (0, 1, 2, 4, 8, 16)
CSR_MINSTRET_LO = 0xB02
OP_SYS, OP_LOAD, CUSTOM0, CUSTOM1 = 0x73, 0x03, 0x0B, 0x2B
C0 = {0: "LVB", 1: "SVB", 2: "MVX", 3: "XMV", 4: "CFG", 5: "CFGR"}
C1 = {1: "VDOT8", 2: "VMAC4", 3: "VADD8", 4: "VREQ4", 5: "VMAX8", 6: "VRELU8",
      7: "VSRA4", 8: "VZERO", 9: "VPOOL2", 10: "VSUB8", 11: "VMIN8"}
# 一条 LVB/SVB 搬运 16 字节 = 4 个整字(与 RTL 的 4 次总线拍一致)
WORDS_PER_VEC_MOVE = 4


def log(*a):
    print(*a, flush=True)


def sha16(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()[:16]


def classify(w):
    op, f3, f7 = w & 0x7F, (w >> 12) & 7, (w >> 25) & 0x7F
    if op == CUSTOM0:
        return C0.get(f3, "CUSTOM0?") if f7 == 0 else "CUSTOM0??"
    if op == CUSTOM1 and f3 == 0:
        return C1.get(f7, "CUSTOM1?")
    if op == OP_LOAD:
        return "LD"
    return None


def is_minstret_lo(w):
    return (w & 0x7F) == OP_SYS and ((w >> 12) & 7) == 2 and (w >> 20) == CSR_MINSTRET_LO


def scan(case, arc, trace, binf):
    """按 minstret 读数切窗, 逐窗统计。返回 (per-case dict, problems)。"""
    p = []
    for f in (arc, trace, binf):
        if not os.path.isfile(f):
            return None, ["%s: 缺件 %s -> 本轮不出曲线" % (case, f)]
    a = json.load(io.open(arc, encoding="utf-8"))
    a["_path"], a["_image_sha"] = arc, sha16(binf)
    img = io.open(binf, "rb").read()
    nw = len(img) // 4
    seg = {"R": 0, "S": 0, "mix": {}, "be": {}}
    segs = []
    marks = 0
    offimg = 0
    # 每条读 minstret 的指令本身属于**它后面那个窗口之前**的边界: i0 把预热段收掉,
    # i1 交出基线窗口, i2 交出 EPV 窗口。所以三次读数 => 三段, 取后两段。
    with io.open(trace, encoding="utf-8", errors="replace") as f:
        for ln in f:
            t = ln.split()
            if t and t[0] == "S":
                be = int(t[3]) if len(t) > 3 else -1
                seg["S"] += 1
                seg["be"][be] = seg["be"].get(be, 0) + 1
                continue
            if not t or t[0] != "R":
                continue
            pc = int(t[1], 16)
            idx = (pc - BASE) >> 2
            if idx < 0 or idx >= nw:
                offimg += 1
                w = 0
            else:
                w = int.from_bytes(img[idx * 4:idx * 4 + 4], "little")
            seg["R"] += 1
            k = classify(w) or "_REGWR"
            seg["mix"][k] = seg["mix"].get(k, 0) + 1
            if is_minstret_lo(w):
                marks += 1
                segs.append(seg)
                seg = {"R": 0, "S": 0, "mix": {}, "be": {}}
    if offimg:
        p.append("%s: 有 %d 条 PC 落在镜像之外 -> 归属残缺" % (case, offimg))
    if marks != 3:
        return None, ["%s: 读到 %d 条 minstret 低字读数, 需要恰好 3 条"
                      "(预热前/基线后/EPV后) -> 本用例判 UNAVAILABLE: 逐层或逐图的"
                      "读数会把窗口切碎, 硬拼出来的段不再对应归档里那一对周期"
                      % (case, marks)]
    if len(segs) != 3:
        return None, ["%s: 切出 %d 段而读数为 3 条 —— 分段器与读数计数器不一致"
                      % (case, len(segs))]
    ref, epv = segs[1], segs[2]
    vec_c = [k for k in ref["mix"] if k in C0.values() or k in C1.values()]
    if vec_c:
        return None, ["%s: 判为基线臂的那一段里出现了自定义指令 %s -> 窗口配对不成立"
                      % (case, sorted(vec_c)[:4])]
    if "VDOT8" not in epv["mix"]:
        return None, ["%s: 判为 EPV 臂的那一段里没有 VDOT8 -> 窗口配对不成立" % case]
    for nm, s, inst in (("ref", ref, a["ref_instret"]), ("epv", epv, a["epv_instret"])):
        if s["R"] + s["S"] > inst:
            p.append("%s: %s 窗口打印到 %d 条(R=%d + S=%d) 大于归档退休数 %d"
                     " —— 端口模型或窗口边界读错了"
                     % (case, nm, s["R"] + s["S"], s["R"], s["S"], inst))
        if min(s["be"], default=0) < 0 or max(s["be"], default=15) > 15:
            p.append("%s: %s 窗口出现非法 byte-enable %s" % (case, nm, sorted(s["be"])))
    if p:
        return None, p
    return finalize(case, a, ref, epv), None


def words_of(s, inst, vec_side):
    """一侧的总线字数。vec_side=True 时给 [下界, 上界]:
      下界 = 标量取数 + 每条写至少 1 个整字 (向量搬运按最少也罚得到)
      上界 = 下界 + 把「窗口退休数 − 写回记录 − 写记录」这一整段**端口看不见的指令**
             全部当成 4 字节的 LVB, 并把每条写按 4 个字计
    两个界都只由实测数导出, 没有一个手写比例。"""
    ld = s["mix"].get("LD", 0)
    st = 0
    for be, c in s["be"].items():
        st += ((be.bit_count() + 3) // 4) * c if be >= 0 else c
    lo = ld + st
    hi = lo
    if vec_side:
        invisible = max(inst - s["R"] - s["S"], 0)
        hi = ld + WORDS_PER_VEC_MOVE * (s["S"] + invisible)
    return lo, hi, {"ld": ld, "store_words": st, "store_instr": s["S"],
                    "regwrite_records": s["R"]}


def finalize(case, a, ref, epv):
    ri, ei = a["ref_instret"], a["epv_instret"]
    rc, ec = a["ref_cycles"], a["epv_cycles"]
    rl, ru, rd_ = words_of(ref, ri, False)
    el, eu, ed = words_of(epv, ei, True)
    m = {"case": case, "archive": a["_path"], "archive_sha256": sha16(a["_path"]),
         "image_sha256": a["_image_sha"],
         "measured": {"ref_cycles": rc, "epv_cycles": ec, "ref_instret": ri,
                      "epv_instret": ei, "speedup_archive": a["speedup"],
                      "ref": dict(rd_, mix=sorted(ref["mix"].items()),
                                  be_hist=sorted(ref["be"].items())),
                      "epv": dict(ed, mix=sorted(epv["mix"].items()),
                                  be_hist=sorted(epv["be"].items()))},
         "invisible_instr_ref": ri - ref["R"] - ref["S"],
         "invisible_instr_epv": ei - epv["R"] - epv["S"],
         "words_ref_data": rl, "words_epv_data": [el, eu],
         "words_ref_all": rl + ri, "words_epv_all": [el + ei, eu + ei]}
    for tag, wr, we in (("data_only", rl, (el, eu)),
                        ("data_plus_fetch", rl + ri, (el + ei, eu + ei))):
        cur = []
        for W in WPTS:
            e_lo, e_hi = ec + W * we[0], ec + W * we[1]
            r = rc + W * wr
            cur.append({"wait_states": W, "ref_cycles": r,
                        "epv_cycles_lo": e_lo, "epv_cycles_hi": e_hi,
                        "speedup_at_epv_lo": round(r / float(e_lo), 4),
                        "speedup_pessimistic": round(r / float(e_hi), 4)})
        m["curve_" + tag] = cur
    m["zero_point_ok"] = abs(m["curve_data_only"][0]["speedup_at_epv_lo"]
                            - a["speedup"]) <= 5e-4 * max(a["speedup"], 1)
    return m


def problems(a):
    p = []
    if a.get("gate_cases", 0) == 0:
        return ["一条曲线都没算出来: 分母为零的判决不许被采信"]
    for c in a.get("cases", []):
        tag = c["case"]
        if not c.get("zero_point_ok"):
            p.append("%s: W=0 那点没对上归档加速比 %r, 模型不许一上来就先错"
                     % (tag, c.get("measured", {}).get("speedup_archive")))
        for key in ("curve_data_only", "curve_data_plus_fetch"):
            cv = c.get(key) or []
            if len(cv) != len(WPTS):
                p.append("%s: %s 有 %d 个点, 应为 %d" % (tag, key, len(cv), len(WPTS)))
            seq = [r["speedup_pessimistic"] for r in cv]
            if any(seq[i + 1] > seq[i] + 1e-9 for i in range(len(seq) - 1)):
                p.append("%s: %s 不降反升 —— 罚的字数比周期比还小, 这是读数不是噪声"
                         % (tag, key))
            for r in cv:
                if r["epv_cycles_lo"] > r["epv_cycles_hi"]:
                    p.append("%s: %s 的区间反了 (lo %d > hi %d)"
                             % (tag, key, r["epv_cycles_lo"], r["epv_cycles_hi"]))
        for side in ("ref", "epv"):
            if c["measured"][side]["regwrite_records"] <= 0:
                p.append("%s: %s 窗口一条写回记录都没有 -> 窗口是空的" % (tag, side))
        if c["measured"]["epv"]["ld"] < 0 or c["measured"]["ref"]["ld"] < 0:
            p.append("%s: 标量取数计数为负" % tag)
    return p


CASES = [("nn_bench", os.path.join("results", "bench", "nn_bench.json"),
          os.path.join("results", "trace", "nn_bench.txt"),
          os.path.join("build", "elf", "nn_bench.bin")),
         ("nn_net", os.path.join("results", "bench", "nn_net.json"),
          os.path.join("results", "trace", "nn_net.txt"),
          os.path.join("build", "elf", "nn_net.bin")),
         ("nn_cifar", os.path.join("results", "bench", "cifar10_net.json"),
          os.path.join("results", "trace", "nn_cifar.txt"),
          os.path.join("build", "elf", "nn_cifar.bin"))]


def build():
    out = {"model": "第 W 档 = 每个整字总线访问多付 W 拍; 基线臂按其实测字数加价, "
                     "EPV 臂给区间(下界=打印得到的字, 上界=把窗口内不可见指令全当成 "
                     "4 字向量装载)。两组曲线: 只罚数据 / 数据+取指(取指字数=退休数)。",
           "w_points": list(WPTS), "words_per_vec_move": WORDS_PER_VEC_MOVE,
           "cases": [], "unavailable": [], "note": ""}
    for case, arc, tr, binf in CASES:
        m, err = scan(case, arc, tr, binf)
        if m is None:
            out["unavailable"].append("%s: %s" % (case, "; ".join(err or ["未知"])))
            continue
        out["cases"].append(m)
    out["gate_cases"] = len(out["cases"])
    if not out["cases"]:
        out["note"] = "本轮没有任何用例出曲线"
    pr = problems(out)
    out["gate_problems"] = pr
    out["gate"] = "PASS" if not pr else "FAIL"
    from atomic_json import write_json_atomic
    write_json_atomic(OUT, out)
    log("MEMSENS gate=%s cases=%d unavailable=%d problems=%d"
        % (out["gate"], out["gate_cases"], len(out["unavailable"]), len(pr)))
    for x in pr:
        log("  PROBLEM: " + x)
    for u in out["unavailable"]:
        log("  SKIP: " + u)
    for c in out["cases"]:
        d, f = c["curve_data_only"], c["curve_data_plus_fetch"]
        log("  %-9s W0=%.2fx | data-only W=%s | +fetch W=%s | words ref=%d epv=[%d,%d]"
            % (c["case"], d[0]["speedup_at_epv_lo"],
               " ".join("%.1f-%.1f" % (r["speedup_pessimistic"],
                                       r["speedup_at_epv_lo"]) for r in d[1:]),
               " ".join("%.1f-%.1f" % (r["speedup_pessimistic"],
                                       r["speedup_at_epv_lo"]) for r in f[1:]),
               c["words_ref_data"], c["words_epv_data"][0], c["words_epv_data"][1]))
    if out["cases"]:
        c0 = out["cases"][0]
        pes = dict((r["wait_states"], r["speedup_pessimistic"])
                   for r in c0["curve_data_only"])
        pesf = dict((r["wait_states"], r["speedup_pessimistic"])
                    for r in c0["curve_data_plus_fetch"])
        out["doc_anchor"] = {"case": c0["case"],
                             "pessimistic_data_only": pes,
                             "pessimistic_data_plus_fetch": pesf}
        # 正文只许抄这一行; 每个数都是「悲观下界」, 不是区间中点
        log("DOC | %s | 悲观下界 只罚数据 W=1/2/4/8/16 = %.1f/%.1f/%.1f/%.1f/%.1f 倍"
            " | 数据+取指 W=16 = %.1f 倍"
            % (c0["case"], pes.get(1, 0), pes.get(2, 0), pes.get(4, 0),
               pes.get(8, 0), pes.get(16, 0), pesf.get(16, 0)))
    return 0 if out["gate"] == "PASS" else 1


def selftest():
    def curve(base, dec):
        return [{"wait_states": WPTS[i], "epv_cycles_lo": base + 10 * i,
                 "epv_cycles_hi": base + 20 * i,
                 "speedup_at_epv_lo": round(base / float(100 + 10 * i) * dec, 4),
                 "speedup_pessimistic": round(base / float(100 + 20 * i) * dec, 4)}
                for i in range(len(WPTS))]
    mk = lambda: {"case": "x", "zero_point_ok": True,
                  "words_ref_data": 100, "words_epv_data": [10, 20],
                  "measured": {"speedup_archive": 15.73,
                               "ref": {"regwrite_records": 10, "ld": 1},
                               "epv": {"regwrite_records": 10, "ld": 1}},
                  "curve_data_only": curve(100, 15.73),
                  "curve_data_plus_fetch": curve(100, 12.0)}
    cases = [("clean", {}, []),
             ("没有用例", {"gate_cases": 0}, ["分母为零"]),
             ("零号点没对上", {"zero": True}, ["一上来就先错"]),
             ("曲线不降", {"rise": True}, ["不降反升"]),
             ("区间反了", {"flip": True}, ["区间反了"]),
             ("空窗口", {"empty": True}, ["一条写回记录"])]
    bad = 0
    for tag, patch, want in cases:
        c = mk()
        d = {"gate_cases": 1, "cases": [c]}
        if tag == "没有用例":
            d["gate_cases"] = 0
            d["cases"] = []
        if "zero" in patch:
            c["zero_point_ok"] = False
        if "rise" in patch:
            for k in ("curve_data_only",):
                c[k] = [dict(x, speedup_pessimistic=1.0 + i, speedup_at_epv_lo=1.0 + i)
                        for i, x in enumerate(c[k])]
            d["cases"] = [c]
        if "flip" in patch:
            c["curve_data_only"][3]["epv_cycles_lo"] = 10 ** 9
        if "empty" in patch:
            c["measured"]["epv"]["regwrite_records"] = 0
        pr = problems(d)
        if tag == "clean":
            if pr:
                bad += 1
                log("SELFTEST-FAIL clean 却在开火: %s" % pr)
            continue
        hits = [x for x in pr if any(w in x for w in want)]
        if want and not hits:
            bad += 1
            log("SELFTEST-FAIL %-14s 期望 %s 开火, 实际 %s" % (tag, want, pr))
        else:
            log("SELFTEST-OK %-14s -> %s" % (tag, hits[0][:64]))
    # 判据自己也要能被证伪: 曲线缺一段必须红
    c = mk()
    del c["curve_data_plus_fetch"]
    pr = problems({"gate_cases": 1, "cases": [c]})
    if not [x for x in pr if "有 0 个点" in x]:
        bad += 1
        log("SELFTEST-FAIL 曲线整段缺失没被抓住: %s" % pr)
    else:
        log("SELFTEST-OK %-14s -> %s" % ("曲线缺失", pr[-1][:64]))
    # 条数由夹具自己数（+1 是上面那条不挂在 cases 里的「曲线整段缺失」探针）：
    # 文档里那句「N 条注入」以前是手抄的，加一条夹具没人回头改文档。
    inj = sum(1 for c in cases if c[0] != "clean") + 1
    log("MEMSENS_SELFTEST_%s (injections=%d bad=%d)"
        % ("OK" if not bad else "FAIL", inj, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    sys.exit(selftest() if ap.parse_args().selftest else build())
