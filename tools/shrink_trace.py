"""把随机差分臂里那发分歧程序削减成最小复现件。

为什么要这一份: 未归因的分歧有两种, 而且它们的处置完全相反 —— 核与模型真的在某个
角上不是一回事 (要记缺陷、要改 RTL), 或者只是我的削减/激励把某个不该成立的前提破了
(要修台子)。分辨二者只有一个办法: 把程序削到不能再削, 削到只剩几条还分歧, 那几条就
是可以手算的。本件不猜原因, 只把「谁错了」缩小到一个能一眼看完的规模。

做法是标准的 top-down ddmin-lite: 体里的指令是一条条独立可删的 (setup 与
TEST_PASSFAIL 永远保留), 每轮试删一个块, 删完仍然分歧就接受这次删减。
判据只有「compare_one 报分歧」这一条, 而且每次判定都重跑同一道比对器
(flow/iss_compare.py 里那份唯一的逐条轨迹比对), 不另写一份。
"""
import io
import os
import random
import re
import sys

PKG = "D:/Desktop/RSIC-V" + "高校"
sys.path.insert(0, os.path.join(PKG, "flow"))
import rt            # noqa: E402
import iss_compare   # noqa: E402
import rand_trace    # noqa: E402

WORK = os.path.join(os.environ.get("TEMP", "/tmp"), "randshrink")
os.makedirs(WORK, exist_ok=True)

SEED = 20260926
OPS = 120


def body_lines(seed, nops):
    """把生成出来的程序拆成 (setup, body, tail) 三段; 只有 body 可删。"""
    text = rand_trace.gen_program(seed, nops)
    lines = text.splitlines()
    i0 = next(k for k, l in enumerate(lines) if l.strip() == "RVTEST_CODE_BEGIN") + 1
    i1 = next(k for k, l in enumerate(lines) if l.strip() == "TEST_PASSFAIL")
    head, body, tail = lines[:i0], lines[i0:i1], lines[i1:]
    # setup = 到 la x28 与那串 li 为止 (生成器固定: 到第一条随机指令之前)
    nsetup = 0
    for k, l in enumerate(body):
        if re.match(r"^\s{2}\S", l) and "li x" not in l and "la x28" not in l:
            nsetup = k
            break
    else:
        nsetup = len(body)
    setup = body[:nsetup]
    return head, setup, body[nsetup:], tail


def assemble(head, setup, body, tail, tag):
    text = "\n".join(head + setup + body + [""] + tail) + "\n"
    p = os.path.join(WORK, "%s.S" % tag)
    io.open(p, "w", encoding="utf-8", newline="\n").write(text)
    return p


_counter = [0]


def diverges(head, setup, body, tail):
    _counter[0] += 1
    p = assemble(head, setup, body, tail, "sh%04d" % _counter[0])
    try:
        row = iss_compare.compare_one(p, "shrink__%04d" % _counter[0])
    except Exception as exc:
        return ("ERR", repr(exc)[:160])
    if row.get("agree"):
        return False
    return (row.get("first_divergence_index"), row.get("rtl_commits"))


def main():
    head, setup, body, tail = body_lines(SEED, OPS)
    print("setup=%d body=%d" % (len(setup), len(body)))
    r0 = diverges(head, setup, body, tail)
    print("full program:", r0)
    if not r0:
        print("NO_DIVERGENCE 全程序就不分歧了 —— 说明削减器或环境变了, 不是一件事")
        return 1

    cur = list(body)
    chunk = max(1, len(cur) // 2)
    while chunk >= 1:
        i = 0
        while i < len(cur):
            trial = cur[:i] + cur[i + chunk:]
            if not trial:
                i += chunk
                continue
            res = diverges(head, setup, trial, tail)
            if res and not str(res[0]).startswith("ERR"):
                cur = trial
                print("  kept shrink len=%d chunk=%d -> %s" % (len(cur), chunk, res))
            else:
                i += chunk
        if chunk == 1:
            break
        chunk = max(1, chunk // 2)

    print("MINIMAL body instructions = %d" % len(cur))
    out = os.path.join(WORK, "minimal.S")
    io.open(out, "w", encoding="utf-8", newline="\n").write(
        "\n".join(head + setup + cur + [""] + tail) + "\n")
    print("WROTE %s" % out)
    print("re-verify:", diverges(head, setup, cur, tail))
    for k, l in enumerate(cur):
        print("   %02d %s" % (k, l.strip()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
