"""列出 sw/bench/micro_bisect.c 里**含非 ASCII 的字符串字面量** (不含注释)。

做法: 逐字符扫, 只在双引号内部收集, 处理 \\ 转义与跨行的相邻字面量拼接。
不用正则是因为反斜杠类经 shell 通道会被吞 (踩过)。
"""
import io
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "sw/bench/micro_bisect.c"
t = io.open(path, encoding="utf-8").read()

# 先剥掉注释, 避免把注释里的中文当字面量
out = []
i, n = 0, len(t)
while i < n:
    c = t[i]
    if c == "/" and i + 1 < n and t[i + 1] == "/":
        j = t.find("\n", i)
        i = n if j < 0 else j
        continue
    if c == "/" and i + 1 < n and t[i + 1] == "*":
        j = t.find("*/", i + 2)
        i = n if j < 0 else j + 2
        continue
    out.append(c)
    i += 1
code = "".join(out)

lits = []
i, n = 0, len(code)
while i < n:
    if code[i] == '"':
        j = i + 1
        buf = []
        while j < n:
            if code[j] == "\\":
                buf.append(code[j:j + 2])
                j += 2
                continue
            if code[j] == '"':
                break
            buf.append(code[j])
            j += 1
        lits.append("".join(buf))
        i = j + 1
        continue
    i += 1


def nonascii_count(s):
    return sum(1 for ch in s if ord(ch) > 127)


bad = [l for l in lits if nonascii_count(l)]
print("STRING-LITERALS %d   NON-ASCII-BEARING %d" % (len(lits), len(bad)))
for l in bad:
    print("  [%3d] %r" % (nonascii_count(l), l))
