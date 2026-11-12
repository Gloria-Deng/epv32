"""把 sw/bench/micro_bisect.c 里**字符串字面量内**的中文换成 ASCII。

为什么只换字面量、不换注释: 注释不进 UART, 而字面量会。实测
results/sim/micro_bisect.json 的 uart 字段是双重编码的坏码 (源文件本身是干净
UTF-8), 说明 编译→UART→JSON 这条路上有一次转码。与其在那条路上追, 不如让
探针的屏幕输出走本包既有约定 —— sw/bench/misalign_bisect.c 的标签全是 ASCII。
ASCII 在任何转码下都不动, 所以这条同时把「归档里的中文是坏的」这个隐患关掉。

替换只作用于字符串字面量内部 (逐字符扫, 跳过注释), 按长短语优先。
跑完断言: 字面量里非 ASCII 字符数 == 0, 否则 die 并列出漏网的。
"""
import io
import sys

P = "sw/bench/micro_bisect.c"

MAP = [
    ("不变", "unchanged"),
    ("期望不变", "want unchanged"),
    (" blk>>2 的低 4 位=", " blk>>2 low 4 bits="),
    ("无 locked 表项时 RAM/外设照常可读写 (本函数全程即证据)",
     "no locked entry: RAM/periph read+write normal (this function is the proof)"),
    ("强制生效", "enforcement"),
    ("计数器写语义", "counter write semantics"),
    ("试图清 L 之后", "after trying to clear L"),
    ("锁定后", "after lock"),
    ("期望(仍是旧值)", "want (unchanged)"),
    ("期望", "want"),
    ("写全 1 读回", "write all-ones readback"),
    ("写 0 之后立刻读", "write 0 then read at once"),
    ("再读一次", "read again"),
    ("清零后回读", "readback after clear"),
    ("回读", "readback"),
    ("读回", "readback"),
    ("寄存器", "registers"),
    ("后(须相等)", "after (must equal)"),
    ("区间外", "outside region"),
    ("区间内", "inside region"),
    ("低 4 位", "low 4 bits"),
    ("对齐", "aligned"),
    ("外设", "peripherals"),
    ("写后", "after store"),
    ("的", " "),
    ("写", "write"),
    ("值", "value"),
    ("前", "before"),
]


def split_code_comments(t):
    """返回 [(text, is_code)] —— 只在 is_code 段里替换。"""
    parts, i, n, start = [], 0, len(t), 0
    while i < n:
        c = t[i]
        if c == "/" and i + 1 < n and t[i + 1] == "/":
            j = t.find("\n", i)
            j = n if j < 0 else j
        elif c == "/" and i + 1 < n and t[i + 1] == "*":
            j = t.find("*/", i + 2)
            j = n if j < 0 else j + 2
        else:
            i += 1
            continue
        parts.append((t[start:i], True))
        parts.append((t[i:j], False))
        i = start = j
    parts.append((t[start:], True))
    return parts


def in_literals(s):
    """把 code 段拆成 [(text, is_string_literal_body)]。"""
    out, i, n, start = [], 0, len(s), 0
    while i < n:
        if s[i] == '"':
            j = i + 1
            while j < n:
                if s[j] == "\\":
                    j += 2
                    continue
                if s[j] == '"':
                    break
                j += 1
            if j >= n:
                raise SystemExit("UNTERMINATED-STRING at %d" % i)
            out.append((s[start:i + 1], False))
            out.append((s[i + 1:j], True))
            i = start = j
        i += 1
    out.append((s[start:], False))
    return out


def main():
    t = io.open(P, encoding="utf-8").read()
    n_rep = 0
    rebuilt = []
    for text, is_code in split_code_comments(t):
        if not is_code:
            rebuilt.append(text)
            continue
        pieces = []
        for lit, is_body in in_literals(text):
            if is_body:
                for zh, en in MAP:
                    if zh in lit:
                        n_rep += lit.count(zh)
                        lit = lit.replace(zh, en)
            pieces.append(lit)
        rebuilt.append("".join(pieces))
    t2 = "".join(rebuilt)

    left = [(i + 1, ln) for i, ln in enumerate(t2.splitlines())
            if any(ord(ch) > 127 for ch in ln)
            and ('"' in ln.split("//")[0]) and ("hx(" in ln or "epv_puts(" in ln)]
    io.open(P, "w", encoding="utf-8", newline="").write(t2)
    print("UART-ASCII-FIED replacements=%d" % n_rep)
    if left:
        for ln, txt in left:
            print("  LEFTOVER-CJK line %d: %s" % (ln, txt.strip()))
        sys.exit(1)
    print("NO-NONASCII-IN-PRINT-CALLS-OK")


if __name__ == "__main__":
    sys.exit(main())
