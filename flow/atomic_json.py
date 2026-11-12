#!/usr/bin/env python3
"""归档落盘的唯一实现：临时文件 + fsync + 一步换名。

为什么不是 `open(path,"w")` 直接写：Windows 上那样打开一个**已存在**的文件会
立刻把它截成零长并按内核的分配单元预占簇，进程若在 write 之后、close 之前被
杀掉，盘上留下的就是一整份 NUL 字节的「归档」。这不是假想的坑——实测过：一次
后台整表认证跑到 isco 那一步被中断，results/sim/rv32ui__or_isco.json 与
results/sim/rv32ui__ma_data_isco.json 各留下 1700 字节全零，
tools/check_archive_clobber.py 读到它就抛 JSONDecodeError，红是红了，报的却
是「解析不了」而不是「哪个归档坏了、该重跑哪一步」。

换成 tmp+rename 之后，被杀掉只会多出一个 <path>.tmp；认证路径上的内容要么
是上一版、要么是这一版，不会出现第三种「文件在、读不出」的状态。

用法（flow/ 与 tools/ 两侧都 import 这一份，避免"补丁端与门禁端各写一遍"）：
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
