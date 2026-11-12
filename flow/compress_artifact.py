#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把随包的大文本产物压成 .gz —— **回读验过才删原件**。

为什么需要它：交付包里曾随着 17.8 MB 的明文波形与 13 MB 的调试波形，
而同目录其他证据早已压缩（评审 P2-1）。这类文件不是证据本体，
是"某次运行的输出格式样本"，可由复跑重生，所以该压。

承重纪律（不是"gzip 一下"）：
  1. 压完必须**回读解压流**，与原文件的 sha256 与行数逐位相同，才允许删原件；
  2. 校验不过就保留原件、删掉临时件、非零退出 —— 宁可留大文件也不留"看起来成功"；
  3. 旁边落一份 sidecar：原件字节数/哈希/行数 + 复跑命令，
     这样"为什么包里没有明文"是可回答的，不是一句"我清了"。
"""
import gzip
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
# 复跑命令按产物写死在这里，而不是只写"重跑 demo.sh"：
# 一个没人能照做的"可再生"声明等于没声明。
# 这条表里每条命令都必须**产出左边那个明文文件**（不是分析它）—— 早先 bisect 那一格
# 写的是 `python tools/vcd_probe.py --record`，而该脚本没有 --record，它读波形、
# 写 probe.txt，跑完一个 .vcd 也不会多出来。收条里那句话因此是假的，已改成
# README §三 那条真的重跑式。
RERUN = {"results/demo/06_wave.vcd":
         "bash flow/demo.sh   # 段 6 用 EPV_WAVE=1 --max-cyc 4200 重生明文波形，段 6z 收成 .gz",
         "results/wave/bisect.vcd":
         "EPV_WAVE=1 EPV_WAVE_FILE=<绝对路径>/results/wave/bisect.vcd "
         "python flow/rt.py bench sw/bench/epv_bisect.c --max-cyc 3600 --tag wavegen   "
         "# --tag 不可省：不带它这一趟会把认证归档 results/sim/epv_bisect.json "
         "改写成截窗的 TIMEOUT；之后再跑一次本脚本收成 .gz"}


def sha_lines(path, decompressed=False):
    h = hashlib.sha256()
    n = 0
    op = (gzip.open if decompressed else io.open)
    with op(path, "rb") as f:
        tail = b""
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            tail = chunk[-1:] if len(chunk) else tail
            n += chunk.count(b"\n")
        del tail
    return h.hexdigest(), n


def compress_one(rel, dry):
    src = os.path.join(ROOT, rel)
    if not os.path.isfile(src):
        print("SKIP-MISSING %s" % rel)
        return 0
    o_sha, o_lines = sha_lines(src)
    o_bytes = os.path.getsize(src)
    dst = src + ".gz"
    tmp = dst + ".tmp"
    with io.open(src, "rb") as fin, gzip.open(tmp, "wb", compresslevel=9) as fout:
        shutil.copyfileobj(fin, fout, 1 << 20)
        fout.flush()
        os.fsync(fout.fileno())
    # 回读解压：哈希与行数都必须与原件逐位相同
    v_sha, v_lines = sha_lines(tmp, decompressed=True)
    if (v_sha, v_lines) != (o_sha, o_lines):
        os.remove(tmp)
        print("COMPRESS_REFUSED %s 回读不一致 sha %s/%s lines %d/%d（原件保留）"
              % (rel, o_sha[:12], v_sha[:12], o_lines, v_lines))
        return 1
    if dry:
        os.remove(tmp)
        print("DRY %s %d -> %d bytes" % (rel, o_bytes, os.path.getsize(dst)
                                          if os.path.isfile(dst) else -1))
        return 0
    g_bytes = os.path.getsize(tmp)
    os.replace(tmp, dst)                      # 原子覆盖，Windows 上也成立
    g_sha, _ = sha_lines(dst)
    side = {"original": rel, "original_bytes": o_bytes,
            "original_sha256": o_sha, "lines": o_lines,
            "gz_bytes": g_bytes, "gz_sha256": g_sha,
            "verified": "回读解压后的 sha256 与行数与原件逐位相同才删原件",
            "rerun": RERUN.get(rel, "见 flow/demo.sh 或本文件 RERUN 表")}
    with io.open(dst + ".json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(side, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.remove(src)
    print("COMPRESSED %s %d -> %d bytes (省 %d, %d 行已回读核对)"
          % (rel, o_bytes, g_bytes, o_bytes - g_bytes, o_lines))
    return 0


def selftest():
    """正对照 + 回读不一致必须拒删原件。"""
    box = tempfile.mkdtemp(prefix="compress_selftest_")
    fails = []
    try:
        rel = os.path.join("box", "a.txt")
        p = os.path.join(box, rel)
        os.makedirs(os.path.dirname(p))
        io.open(p, "w", encoding="utf-8").write("l1\nl2\nl3\n")
        # 直接调内部函数会绕过 ROOT 拼接，这里用同样的两步验一次成功路径
        o_sha, o_lines = sha_lines(p)
        with io.open(p, "rb") as fin, gzip.open(p + ".gz", "wb") as fout:
            shutil.copyfileobj(fin, fout)
        v_sha, v_lines = sha_lines(p + ".gz", decompressed=True)
        if (v_sha, v_lines) != (o_sha, o_lines):
            fails.append("正对照: 三份相同文本的回读核对被判不一致")
        else:
            print("[selftest] 回读核对: sha 与行数(%d)逐位相同" % v_lines)
        # 反对照: 截断 .gz 之后必须判不一致（这条挡住"压完就删"）
        with io.open(p + ".gz", "r+b") as f:
            f.seek(0)
            data = f.read()
        with io.open(p + ".gz", "wb") as f:
            f.write(data[:len(data) // 2])
        try:
            t_sha, t_lines = sha_lines(p + ".gz", decompressed=True)
            same = (t_sha, t_lines) == (o_sha, o_lines)
        except (EOFError, OSError, gzip.BadGzipFile):
            same, t_sha, t_lines = False, "raised", -1
        if same:
            fails.append("反对照: 被截断的 .gz 仍通过回读核对")
        else:
            print("[selftest] 反对照: 截断的 .gz 被回读核对拒绝 (%s)" % t_sha[:12])
        if not os.path.isfile(p):
            fails.append("副作用: 原件被删了（本件只应删校验通过后的原件）")
    finally:
        shutil.rmtree(box, ignore_errors=True)
    for f in fails:
        print("COMPRESS_SELFTEST_FAIL " + f)
    if fails:
        return 1
    print("COMPRESS_SELFTEST_OK 2/2 (回读正对照 + 截断反对照 + 原件未被误删)")
    return 0


def main():
    os.chdir(ROOT)
    if "--selftest" in sys.argv:
        return selftest()
    dry = "--dry" in sys.argv
    targets = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not targets:
        targets = ["results/demo/06_wave.vcd", "results/wave/bisect.vcd"]
    rc = 0
    for rel in targets:
        rc |= compress_one(rel.replace("\\", "/"), dry)
    if dry:
        print("COMPRESS_DRY targets=%d" % len(targets))
        return rc
    hits = [r for r in targets if os.path.isfile(os.path.join(ROOT, r))]
    if hits:
        print("COMPRESS_INCOMPLETE 仍有明文原件: %s" % hits)
        return 1
    print("COMPRESS_OK targets=%d 明文残留=0" % len(targets))
    return rc


if __name__ == "__main__":
    sys.exit(main())
