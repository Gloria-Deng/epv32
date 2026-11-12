#!/usr/bin/env python3
"""单独跑文档门与后置同源核对，不碰 results/matrix.json（矩阵整趟十几分钟，这里只读正文/归档，秒级判文案）。
判定写 results/tmp_docgate.utf8.log 而非 stdout（GBK 会吞中文）。
"""
import io, os, sys, traceback, json
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "flow"))
os.chdir(HERE)
LOG = "results/tmp_docgate.utf8.log"
def w(s):
    with io.open(LOG, "a", encoding="utf-8") as f: f.write(s + "\n")
io.open(LOG, "w", encoding="utf-8").write("")
import run_all as R
w("N_TOTAL %d  STEPS %d  POST %d" % (R.N_TOTAL, len(R.STEPS), len(R.POST_STEPS)))
w("steps: " + ", ".join(n for n, _ in R.STEPS) + " | post: " + ", ".join(n for n, _ in R.POST_STEPS))
for name in ("doc_gate",) + tuple(n for n, _ in R.POST_STEPS):
    fn = dict(R.STEPS + R.POST_STEPS)[name]
    try:
        ev = fn({})
        w("PASS  %-12s %s" % (name, json.dumps(ev, ensure_ascii=False)[:400]))
    except Exception as e:
        w("FAIL  %-12s %s" % (name, traceback.format_exc(0).replace("\n", " | ")[:600]))
