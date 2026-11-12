"""读一份退休轨迹的尾部若干条, 打印 pc/inst/rd/rd_wdata/mem 字段。

用在「官方用例报了某个 TESTNUM, 但我推不出核当时读到什么」这类场合 ——
不许靠读 RTL 猜, 直接把那几条退休下来的目的寄存器值印出来。
"""
import io
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "results/trace/rv32mi__instret_overflow.txt"
n = int(sys.argv[2]) if len(sys.argv) > 2 else 30
lines = io.open(path, encoding="utf-8", errors="replace").read().splitlines()
print("FILE %s  LINES=%d" % (path, len(lines)))
print("FIRST-LINE %r" % (lines[0] if lines else ""))
print("SECOND-LINE %r" % (lines[1] if len(lines) > 1 else ""))
print("--- tail %d ---" % n)
out = io.open("results/tmp_trace_tail.txt", "w", encoding="utf-8")
for ln in lines[-n:]:
    out.write(ln + "\n")
out.close()
print("WROTE results/tmp_trace_tail.txt")
