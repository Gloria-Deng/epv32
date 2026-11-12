#!/usr/bin/env python3
"""归档落盘的唯一实现：临时文件 + fsync + 一步换名。

为什么不是直接 open(path,"w"): Windows 上写已存在文件会先截成零长并预占簇,
进程在 write 后、close 前被杀, 盘上留一整份 NUL 字节的「归档」(实测整表认证
在 isco 一步被中断后留下两份 1700 字节全零, 门禁报 JSONDecodeError 而不是
「哪份坏了、该重跑哪一步」)。换成 tmp+rename 后被杀只会多出 <path>.tmp, 内容
要么上一版要么这一版, 不会再出现「文件在、读不出」的第三种状态。

用法（flow/ 与 tools/ 两侧都 import 这一份, 避免各写一遍）:
    sys.path.insert(0, os.path.join(ROOT, "flow"))
    from atomic_json import write_json_atomic
"""
import io
import json
import os


def write_json_atomic(path, obj, **kw):
    """把 obj 以 UTF-8 JSON 落到 path，中途被杀不会留下半个 path。"""
    d = os.path.dirname(os.path.abspath(path))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    kw.setdefault("indent", 2)
    kw.setdefault("ensure_ascii", False)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, **kw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path
