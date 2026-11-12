"""改动前快照: 给包内每个受版本控制的文本/二进制文件记 sha256 与字节数。

用途有两个:
  1. RTL/文档换代后能回答「这一版相对哪一版改了什么」(本包不是 git 仓库);
  2. 给「配对对照」提供基线字节 —— 交付档周期数不许因改版而变, 靠这份清单可核。
"""
import os
import io
import json
import hashlib
import sys

ROOTS = ["docs", "rtl", "results", "flow", "tools", "tb", "sw", "demo"]
SKIP_DIR = ("backup", "__pycache__", ".git")


def sha_of(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        "results", "backup", "preM9", "manifest.json")
    man = {}
    for r in ROOTS:
        for dp, dn, fn in os.walk(r):
            if any(s in dp for s in SKIP_DIR):
                continue
            for f in fn:
                p = os.path.join(dp, f)
                key = p.replace(os.sep, "/")
                if key == out.replace(os.sep, "/"):
                    continue
                try:
                    man[key] = [sha_of(p), os.path.getsize(p)]
                except Exception as e:
                    print("SKIP %s (%s)" % (key, e))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with io.open(out, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"n_files": len(man), "files": man},
                            ensure_ascii=False, indent=1))
    print("SNAPSHOT-FILES %d -> %s" % (len(man), out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
