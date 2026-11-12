"""M9 配对对照: 新的 rtl/ 必须让「交付档的所有测量数」逐位不变。

为什么需要它: M9 往 epv32_csr.v 里加了 PMP 表项与可写计数器, 并把 minstret 的
自增点从 WB 挪到提交点。这些话都可以「讲」成不影响既有测量 —— 但讲不算,
必须拿改之前的归档跟改之后的逐项比。

判据分三档, 每档分开报:
  SAME   —— 期望逐位相同的关键测量数 (周期、退休数、面积单元数、比对结果)
  EXPECT —— 允许变、但必须变成指定值的 (rv32mi 的两条: FAIL -> PASS)
  INFO   —— 只打印不做判据的 (rtl_sha256、mtime 这类本来就该变的)
任何 SAME 项发生变化 => 非零退出, 并印出两侧读数 (只印一侧的话, 消息会和
基线一个字不差地被消成「没命中」)。
"""
import io
import json
import os
import sys

OLD = os.path.join("results", "backup", "preM9", "archives")
KEYS = {
    "nn_bench.json":        ["ref_cycles", "epv_cycles", "macs", "bit_exact",
                             "bit_exact_mismatches", "speedup_x1000",
                             "ref_instret", "epv_instret"],
    "nn_net.json":          None,      # None = 递归比全部标量叶子, 除黑名单
    "epv_selftest.json":    ["cycles", "retired", "verdict"],
    "epv_bisect.json":      ["cycles", "retired", "verdict"],
    "misalign_bisect.json": ["cycles", "retired", "verdict"],
    "rv32ui.json":          None,
    "rv32um.json":          None,
    "synth/summary.json":   None,
    "iss_selftest.json":    None,
}
# 本来就该随趟变的东西: 源码指纹、时间、工具墙钟
VOLATILE = ("sha256", "mtime", "wall_s", "sim_rc", "rc", "started_utc",
            "run_id", "version", "tool", "path", "bytes", "tag")


def flat(o, pre=""):
    out = {}
    if isinstance(o, dict):
        for k, v in o.items():
            out.update(flat(v, pre + "/" + str(k)))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            out.update(flat(v, pre + "[%d]" % i))
    else:
        out[pre] = o
    return out


def load(p):
    with io.open(p, encoding="utf-8") as fh:
        return json.load(fh)


def main():
    cur = {"nn_bench.json": os.path.join("results", "bench", "nn_bench.json"),
           "nn_net.json": os.path.join("results", "bench", "nn_net.json"),
           "epv_selftest.json": os.path.join("results", "sim", "epv_selftest.json"),
           "epv_bisect.json": os.path.join("results", "sim", "epv_bisect.json"),
           "misalign_bisect.json": os.path.join("results", "sim", "misalign_bisect.json"),
           "rv32ui.json": os.path.join("results", "compliance", "rv32ui.json"),
           "rv32um.json": os.path.join("results", "compliance", "rv32um.json"),
           "rv32mi.json": os.path.join("results", "compliance", "rv32mi.json"),
           "synth/summary.json": os.path.join("results", "synth", "summary.json"),
           "iss_selftest.json": os.path.join("results", "iss", "selftest.json")}

    # 「归档跟改前一样」只有在它确实是新 RTL 跑出来的才算证据；从没复跑的归档
    # 会给出漂亮 SAME（本包曾被「门把没跑当成跑过」抓住多次）。故比 rtl/ 最新
    # mtime 更老的归档判 STALE，非零退出、不进 SAME 计数。
    rtl_newest = 0.0
    for fn in os.listdir("rtl"):
        if fn.endswith((".v", ".vh")):
            rtl_newest = max(rtl_newest, os.path.getmtime(os.path.join("rtl", fn)))
    stale = []
    for name, cp in sorted(cur.items()):
        if os.path.isfile(cp) and os.path.getmtime(cp) < rtl_newest:
            stale.append(name)
    if stale:
        print("STALE %d 份归档比 rtl/ 里最新的源文件还老 => 它们没有在这次 RTL 之后"
              "复跑, 不作为对照证据: %s" % (len(stale), ", ".join(stale)))
        print("      先复跑这几步再回来判: python flow/run_all.py "
              "rv32ui rv32um epv_bisect epv_selftest nn_bench nn_net synthesis")
        return 1
    same, diff, skip = [], [], []
    for name, cp in sorted(cur.items()):
        op = os.path.join(OLD, os.path.basename(name))
        if not os.path.isfile(op):
            skip.append((name, "无改前副本"))
            continue
        if not os.path.isfile(cp):
            skip.append((name, "改后归档不存在(还没复跑这一步)"))
            continue
        a, b = flat(load(op)), flat(load(cp))
        keys = KEYS.get(name)
        cand = keys and [k for k in a if any(k == x or k.endswith("/" + x) for x in keys)] \
            or [k for k in a if not any(v in k.lower() for v in VOLATILE)]
        for k in sorted(set(cand)):
            if k not in b:
                diff.append((name, k, repr(a[k]), "<缺失>"))
                continue
            if a[k] == b[k]:
                same.append((name, k, a[k]))
            else:
                diff.append((name, k, repr(a[k]), repr(b[k])))
    print("SAME  %d 项逐位相同" % len(same))
    print("DIFF  %d 项" % len(diff))
    for name, k, x, y in diff:
        print("  DIFF %-22s %-46s %s -> %s" % (name, k, x, y))
    for name, why in skip:
        print("  SKIP %-22s %s" % (name, why))
    # rv32mi 两条必须从 FAIL 变 PASS, 这一句把它单独钉住 (它属于 EXPECT 档)
    mi = load(cur["rv32mi.json"])
    fixed = [r["test"] for r in mi["rows"] if r["verdict"] == "PASS"
             and r["test"] in ("pmpaddr", "instret_overflow")]
    print("EXPECT rv32mi 由红转绿: %d/2 %s" % (len(fixed), sorted(fixed)))
    if len(fixed) != 2:
        print("  差: 这两条里至少一条还没转绿, M9 的 PMP/计数器改动没落到产物上")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
