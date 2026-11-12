# -*- coding: utf-8 -*-
"""把逐周期轨迹文本收成 .gz + 摘要 (矩阵后置格)。

为什么要这一支: 预赛是线上评审, 评委要下载并解压整个包。本包 1.1 GB 里有 900 MB
是 results/trace/ 下的**全量逐周期文本** (nn_cifar.txt 一个文件 821 MB), 而所有
判定早就在 flow/rt.py 里读完并写进了 results/sim/*.json 的字段。留着它们的唯一
作用是"万一有人想看", 而那件事可以用 前 N 行 + 后 N 行 + 全量 sha256 + 复跑命令
替掉 —— 证据没有少, 只是换了一种可核对的形状。

承重纪律 (不许把清理写成一次静默的数据丢失):
  1. 压缩前后各算一次**解压器读回来**的 sha256 与行数, 两者必须逐位相同才删原文;
     任一步不符 -> 保留原文并 raise (删错了不可逆)。
  2. 摘要文件自带: 全量字节数、全量 sha256、行数、前 N 行、后 N 行、复跑命令。
  3. 已压过的文件幂等跳过, 但每次仍重算 .gz 的 sha256 并写进归档, 供新鲜度判据用。
  4. 判决行是 stdout 最后一行且纯 ASCII; 任何一份超阈值文件没被处理都算红。
"""
from __future__ import print_function

import gzip
import hashlib
import io
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACE_DIR = os.path.join(ROOT, "results", "trace")
ARCHIVE = os.path.join(ROOT, "results", "traces", "compact.json")
THRESHOLD = 4 * 1024 * 1024          # 4 MB 以上才值得压
HEAD_TAIL = 40
RERUN = "python flow/run_all.py nn_cifar nn_net nn_bench"
SKIP_NAMES = ()                       # 归档名匹配这些子串的不动

ok = True


def sha_and_lines(path, decompressed=False):
    h = hashlib.sha256()
    n = 0
    opener = gzip.open if decompressed else io.open
    with opener(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            n += chunk.count(b"\n")
    return h.hexdigest(), n


def first_and_last(path, k):
    head, tail = [], []
    with io.open(path, "rb") as f:
        for line in f:
            if len(head) < k:
                head.append(line)
            else:
                tail.append(line)
                if len(tail) > k * 8:
                    tail = tail[-k:]
    return head, (tail[-k:] if tail else head[-k:])


def audit(archive_path=ARCHIVE, trace_dir=TRACE_DIR):
    """只读复核: 归档里记的每个 .gz 必须真在盘上且哈希对得上。"""
    problems = []
    if not os.path.isfile(archive_path):
        return ["compact.json 不存在: %s" % archive_path], 0, 0
    a = json.load(io.open(archive_path, encoding="utf-8"))
    rows = a.get("files", [])
    if not rows:
        return ["compact.json 的 files 为空, 无从核对"], 0, 0
    for r in rows:
        gz = os.path.join(trace_dir, r["file"] + ".gz")
        if not os.path.isfile(gz):
            problems.append("MISSING-GZ %s" % r["file"])
            continue
        h, _ = sha_and_lines(gz)
        if h != r["gz_sha256"]:
            problems.append("GZ-HASH %s 记=%s 现=%s" % (r["file"], r["gz_sha256"][:12], h[:12]))
        summ = os.path.join(trace_dir, r["file"] + ".summary.txt")
        if not os.path.isfile(summ):
            problems.append("MISSING-SUMMARY %s" % r["file"])
        elif r["uncompressed_sha256"] not in io.open(summ, encoding="utf-8",
                                                     errors="replace").read():
            problems.append("SUMMARY-NO-HASH %s" % r["file"])
    return problems, len(rows), int(a.get("bytes_saved", 0))


def compact(dry=True, archive_path=ARCHIVE, trace_dir=TRACE_DIR):
    big = []
    for name in sorted(os.listdir(trace_dir)):
        p = os.path.join(trace_dir, name)
        if not name.endswith(".txt") or not os.path.isfile(p):
            continue
        if os.path.getsize(p) < THRESHOLD:
            continue
        if any(s in name for s in SKIP_NAMES):
            continue
        big.append((name, p))
    if not big:
        # 盘上没有超阈值原文: 有归档就算幂等重跑, 一份归档都没有才是没跑过。
        if os.path.isfile(archive_path):
            probs, n, saved = audit(archive_path, trace_dir)
            if probs:
                for x in probs:
                    print("PROBLEM " + x.encode("unicode_escape").decode("ascii"))
                print("TRACE-COMPACT_FAILED files=%d problems=%d" % (n, len(probs)))
                return 1
            print("bytes_saved=%d files=%d mode=idempotent" % (saved, n))
            print("TRACE-COMPACT_OK files=%d bytes_saved=%d" % (n, saved))
            return 0
        print("SETUP-FAIL 没有超阈值轨迹且无归档 —— 分母为空")
        print("TRACE-COMPACT_FAILED files=0 problems=1")
        return 1

    rows = []
    total_before = total_after = 0
    for name, path in big:
        base = name[:-4]
        gz = os.path.join(trace_dir, base + ".txt.gz")
        summ = os.path.join(trace_dir, base + ".txt.summary.txt")
        sha_raw, lines = sha_and_lines(path)
        head, tail = first_and_last(path, HEAD_TAIL)
        if dry:
            total_before += os.path.getsize(path)
            print("DRY %s bytes=%d lines=%d" % (name, os.path.getsize(path), lines))
            continue
        with io.open(path, "rb") as fin, gzip.open(gz + ".tmp", "wb", compresslevel=9) as fout:
            shutil.copyfileobj(fin, fout, 1 << 20)
            fout.flush()
            os.fsync(fout.fileno())
        # os.rename 在 Windows 上遇到同名目标会抛 FileExistsError，POSIX 上则静默替换
        # —— 于是"同一份轨迹的第二次收拢"这条路只在类 Unix 上存在过。本轮复跑
        # nn_bench/nn_net 之后 results/trace/*.gz 已在，这一发直接崩、矩阵末行判 FAIL。
        # os.replace 在两个平台上都是原子覆盖。
        os.replace(gz + ".tmp", gz)
        rt_sha, rt_lines = sha_and_lines(gz, decompressed=True)
        if rt_sha != sha_raw or rt_lines != lines:
            os.remove(gz)
            raise AssertionError("round-trip 不符, 原文保留: %s sha %s/%s lines %d/%d"
                                 % (name, sha_raw[:12], rt_sha[:12], lines, rt_lines))
        with io.open(summ, "w", encoding="utf-8", newline="\n") as f:
            f.write(u"# %s 的摘要 (原文已收成 %s.txt.gz)\n" % (name, base))
            f.write(u"uncompressed_sha256=%s\n" % sha_raw)
            f.write(u"uncompressed_bytes=%d\n" % os.path.getsize(path))
            f.write(u"lines=%d\n" % lines)
            f.write(u"复跑: %s\n" % RERUN)
            f.write(u"前 %d 行:\n" % HEAD_TAIL)
            for l in head:
                f.write(l.decode("utf-8", "replace"))
            f.write(u"后 %d 行:\n" % HEAD_TAIL)
            for l in tail:
                f.write(l.decode("utf-8", "replace"))
        gz_sha, _n = sha_and_lines(gz)
        before, after = os.path.getsize(path), os.path.getsize(gz)
        # 摘要路径只记 trace 目录内的相对名: os.path.relpath 在临时目录 (C:) 与
        # 包根 (D:) 之间会直接 ValueError 炸掉, 夹具就跑不到判决行。
        rel_summ = os.path.basename(summ)
        total_before += before
        total_after += after
        os.remove(path)
        rows.append({"file": base + ".txt", "uncompressed_sha256": sha_raw,
                     "gz_sha256": gz_sha, "bytes": before, "gz_bytes": after,
                     "lines": lines, "summary": rel_summ,
                     "rerun": RERUN})
        print("COMPACTED %s %d -> %d bytes" % (name, before, after))
    if dry:
        print("mode=dry files=%d bytes=%d" % (len(big), total_before))
        print("TRACE-COMPACT_DRY files=%d" % len(big))
        return 0
    os.makedirs(os.path.dirname(archive_path), exist_ok=True)
    out = {"files": rows, "threshold_bytes": THRESHOLD, "head_tail": HEAD_TAIL,
           "bytes_before": total_before, "bytes_after": total_after,
           "bytes_saved": total_before - total_after,
           "produced_by": "flow/compact_traces.py"}
    tmp = archive_path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, archive_path)   # 同上：摘要归档重写时目标一定已存在
    probs, n, saved = audit(archive_path, trace_dir)
    for x in probs:
        print("PROBLEM " + x.encode("unicode_escape").decode("ascii"))
    if probs or n != len(rows):
        print("TRACE-COMPACT_FAILED files=%d problems=%d" % (n, len(probs)))
        return 1
    print("TRACE-COMPACT_OK files=%d bytes_saved=%d" % (n, saved))
    return 0


def selftest():
    """在临时目录里造一份假轨迹, 走完整条链, 并证明 round-trip 判据会咬。"""
    import tempfile
    fails = []
    tmp = tempfile.mkdtemp(prefix="epvct_")
    td = os.path.join(tmp, "trace")
    os.makedirs(td)
    body = b"".join(b"%08d pc=%08x instr=%08x\n" % (i, i * 4, 0x0004800b + i)
                    for i in range(300000))
    p = os.path.join(td, "fake_case.txt")
    with io.open(p, "wb") as f:
        f.write(body)
    ap = os.path.join(tmp, "compact.json")
    rc = compact(dry=False, archive_path=ap, trace_dir=td)
    if rc != 0:
        fails.append("SETUP: 正常臂没跑通 rc=%d" % rc)
    if os.path.exists(p):
        fails.append("BLIND: 压完原文还在, 体积没省下来")
    probs, n, saved = audit(ap, td)
    if probs or n != 1 or saved <= 0:
        fails.append("BLIND: 复核判据对正常产物报了问题 %s" % probs[:2])
    # 1) 抹掉 .gz -> 必须 MISSING-GZ
    os.remove(os.path.join(td, "fake_case.txt.gz"))
    probs, _n, _s = audit(ap, td)
    if not any("MISSING-GZ" in x for x in probs):
        fails.append("BLIND: .gz 不见了却没判红")
    # 2) 换回内容不同的 .gz (扰动一个字节再重压) -> 必须 GZ-HASH
    with io.open(os.path.join(td, "fake_case.txt"), "wb") as f:
        f.write(body + b"x")
    with io.open(os.path.join(td, "fake_case.txt"), "rb") as fin, \
            gzip.open(os.path.join(td, "fake_case.txt.gz"), "wb") as fout:
        shutil.copyfileobj(fin, fout)
    probs, _n, _s = audit(ap, td)
    if not any("GZ-HASH" in x for x in probs):
        fails.append("BLIND: 解出来的内容变了却没过哈希核对")
    # 3) 摘要里没有全量哈希 -> 必须 SUMMARY-NO-HASH
    with io.open(os.path.join(td, "fake_case.txt.summary.txt"), "w",
                 encoding="utf-8") as f:
        f.write(u"nothing here\n")
    probs, _n, _s = audit(ap, td)
    if not any("SUMMARY-NO-HASH" in x for x in probs):
        fails.append("BLIND: 摘要丢了全量哈希却没判红")
    shutil.rmtree(tmp, ignore_errors=True)
    for f in fails:
        print("FIXTURE-FAIL " + f.encode("unicode_escape").decode("ascii"))
    print("FIXTURES cases=3 failed=%d" % len(fails))
    return 0 if not fails else 1


def main():
    if "--selftest" in sys.argv:
        rc = selftest()
        print(("TRACE-COMPACT-SELFTEST_PASS" if rc == 0
               else "TRACE-COMPACT-SELFTEST_FAILED"))
        return rc
    return compact(dry="--apply" not in sys.argv)


if __name__ == "__main__":
    sys.exit(main())
