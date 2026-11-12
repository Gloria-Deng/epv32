#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flow/check_demo_artifacts.py -- 方案 Demo 成片与现役归档之间的同源判据（只读）。

它接进认证矩阵的 `demo_artifacts` 格，堵一个真实缺陷：「N 步全绿 = 全套证据成立」
当年没有任何一步看过成片。判据按**内容**而非按**字节**（哈希）判：语料自带时间戳，
每复跑一趟哈希就变，按字节判必然永远红。具体：

  J3  屏上每个承重读数（文件 + 归档键 + 字面打印串）必须仍逐字等于现归档同键解出的值；
  J4  成片必须与现役矩阵属**同一代 RTL**（比 `rtl_sha256_head`，不比时刻）；
  J5  `09_matrix.txt` 快照每行的判决与字段必须与现役矩阵对应行一致（易变字段只比存在性）；
  J2  时长与体积按**容器回读**判，而非按 sidecar 自述判（两者还必须互等）；
  J6  写了「有解说音轨」就必须真解得出**非静音**音轨（有流不等于有声）。

哈希仍记录（`corpus_sha256`），但那是来历，不是判据。

用法
----
    python flow/check_demo_artifacts.py              # 判盘上那份成片（默认即真判决）
    python flow/check_demo_artifacts.py --selftest   # 夹具含正对照；末行分数由 FIXTURE_NAMES 现算
退出码：0 = 同源成立；1 = 内容缺陷；2 = 仪器缺料（SETUP-FAIL，不是「没问题」）。
"""
from __future__ import print_function
import io
import json
import hashlib
import os
import re
import sys
import tempfile
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 快照与归档里「每趟都会变」的字段：只比存在性，不比值。排除面必须印出来，
# 只留一个计数就等于把「我放宽了哪些」藏在门里。
VOLATILE = re.compile(r"(mtime|_utc$|^utc|started|elapsed|wall|run_id|_sec$|"
                      r"timestamp|sha|now|_s$)")
PASS_LINE = re.compile(r"^PASS\s+(\S+)\s+(\{.*\})\s*$")

# 「数自己脚印」的那几格：值随每一趟跑动自己变化（链条写日志、清理件按当趟现场清），
# 把它们在两趟之间按值钉住 = 一道按构造过不去的门。点名登记，不许悄悄放行：
# 每一条都写清它为什么每趟不同，排除掉几条一并印进判决行（footprint_excluded=）。
FOOTPRINT = {
    ("navigation", "logs"): u"results/logs 里日志文件的条数，每一趟都会多几份（含本门自己写的明细件）",
    ("package_hygiene", "scanned"): u"被扫的文件条数，随证据树长大",
    ("package_hygiene", "cleaned"): u"本趟清掉的中间件条数，取决于上一趟留下多少",
}
MATRIX_LINE = re.compile(r"^MATRIX\s+(\d+)/(\d+)\s+all_green=(\w+)")
FIXTURE_NAMES = ("healthy", "reading-drift", "cross-generation", "stale-snapshot",
                 "no-reading-ledger", "silent-track", "sidecar-lies",
                 "volatile-only-changed", "matrix-agg-mid-run", "vacuous-snapshot")


def canon(v):
    """把一条读数收成与打印顺序无关的规范形（dict 递归按键排序）。

    dict 的 json.dumps 按插入序打印，同值不同序会被判成过期；而 dict 插入序由生成端
    代码决定，重出片子不解决问题，所以先收成规范形再比。"""
    if isinstance(v, dict):
        return dict((k, canon(v[k])) for k in sorted(v, key=str))
    if isinstance(v, (list, tuple)):
        return [canon(x) for x in v]
    return v


def same(a, b):
    """两条读数是否同一个值 —— 只认规范形，不认打印顺序。"""
    return shown(canon(a)) == shown(canon(b))


def read(p):
    with io.open(p, encoding="utf-8", errors="replace") as f:
        return f.read()


def jload(p):
    return json.loads(read(p))


def dig(d, parts, miss, fullkey):
    """按点分键解一份归档；解不出就记进 miss（不许静默给 None）。"""
    cur = d
    for i, seg in enumerate(parts):
        if isinstance(cur, dict) and seg in cur:
            cur = cur[seg]
        elif isinstance(cur, list) and seg.isdigit() and int(seg) < len(cur):
            cur = cur[int(seg)]
        else:
            miss.append(fullkey)
            return None
    return cur


def shown(v):
    """与出片端同一个打印式：字面量或 json.dumps，不许 %.6g。"""
    return v if isinstance(v, str) else json.dumps(v)


def measure_container(path):
    """独立回读容器（cv2），不复用 sidecar 的任何字段。"""
    import cv2
    if not os.path.isfile(path):
        return None
    v = cv2.VideoCapture(path)
    if not v.isOpened():
        return None
    fps = v.get(cv2.CAP_PROP_FPS)
    frames = int(v.get(cv2.CAP_PROP_FRAME_COUNT))
    v.release()
    if not fps or frames <= 0:
        return None
    return {"frames": frames, "fps": fps, "seconds": round(frames / fps, 2),
            "bytes": os.path.getsize(path)}


def probe_audio(path):
    """把成片里的音轨解出来量时长与能量。返回 None 表示根本没有音轨。"""
    import imageio_ffmpeg
    import numpy as np
    import wave
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    work = tempfile.mkdtemp(prefix="epv32_demogate_")
    try:
        wav = os.path.join(work, "a.wav")
        import subprocess
        o = subprocess.run([exe, "-hide_banner", "-y", "-loglevel", "error",
                            "-i", path, "-map", "0:a:0", "-ac", "1", "-ar", "8000", wav],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if o.returncode != 0 or not os.path.isfile(wav) or os.path.getsize(wav) < 200:
            return None
        with wave.open(wav, "rb") as w:
            n, sr, sw = w.getnframes(), w.getframerate(), w.getsampwidth()
            raw = w.readframes(n)
        a = np.frombuffer(raw, dtype=np.int16 if sw == 2 else np.uint8).astype(float)
        if sw == 1:
            a = a - 128.0
        rms = float(np.mean(np.abs(a))) / (32768.0 if sw == 2 else 128.0)
        return {"seconds": round(n / float(sr), 2), "rms": round(rms, 6)}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------- 判据
def audit(root=ROOT):
    """返回 (problems, readings)。problems 为空即这一格判绿。"""
    P = []
    sj = os.path.join(root, "results", "demo", "demo_video.json")
    mp4 = os.path.join(root, "results", "demo", "demo_video.mp4")
    mtx = os.path.join(root, "results", "matrix.json")
    snap = os.path.join(root, "results", "demo", "09_matrix.txt")
    for p in (sj, mp4, mtx, snap):
        if not os.path.isfile(p):
            P.append(("SETUP", "缺输入: %s" % os.path.relpath(p, root)))
    if P:
        return P, 0
    side = jload(sj)
    matrix = jload(mtx)

    # J2 时长/体积：按容器回读判，并要求 sidecar 自述与量出来的一致
    meas = measure_container(mp4)
    if not meas:
        P.append(("LIMITS", "成片回读不了容器: %s" % mp4))
        return P, 0
    if meas["seconds"] > float(side.get("limit_duration_s", 600)):
        P.append(("LIMITS", "容器实测 %.1f s 超上限 %s s"
                  % (meas["seconds"], side.get("limit_duration_s"))))
    if meas["bytes"] > int(side.get("limit_size_bytes", 100 * 1048576)):
        P.append(("LIMITS", "容器实测 %.1f MB 超上限" % (meas["bytes"] / 1048576.0)))
    if abs(meas["seconds"] - float(side.get("duration_s", -1))) > 1.0:
        P.append(("LIES", "sidecar 自述 duration_s=%s 而容器量出来 %.1f s —— "
                          "成片比 sidecar 新（或反之）" % (side.get("duration_s"),
                                                       meas["seconds"])))
    if meas["bytes"] != int(side.get("size_bytes", -1)):
        P.append(("LIES", "sidecar 自述 size_bytes=%s 而盘上 %d"
                  % (side.get("size_bytes"), meas["bytes"])))

    # J4 代际：比 RTL 哈希，不比时刻
    gen = side.get("generation") or {}
    if not gen:
        P.append(("SHAPE", "sidecar 没有 generation 块 —— 那是加这条判据之前出的片，"
                           "重跑 python flow/make_demo_video.py"))
    else:
        head_now = matrix.get("rtl_sha256_head")
        if gen.get("rtl_sha256_head") != head_now:
            P.append(("GEN", "成片属于 RTL 代 %s 而现役矩阵是 %s —— 重出成片"
                      % (str(gen.get("rtl_sha256_head"))[:12], str(head_now)[:12])))
        # J4b 代际：RTL 哈希只能说明源码代际，不能说明矩阵快照和成片来自同一轮。
        # sidecar 必须同时钉住矩阵与 09_matrix.txt 的全量字节哈希。
        def _sha256(path):
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for b in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(b)
            return h.hexdigest()
        # run_all 在后置门开始前先落一份只含前 25 步的中间矩阵；此时
        # demo_artifacts 只能核内容一致性，不能拿这份中间矩阵的哈希要求
        # 成片已经绑定最终 32 步矩阵。最终矩阵（steps == n_total）以及
        # 独立调用本门时仍严格要求两条全量哈希相等。
        partial_matrix = (matrix.get("steps") or 0) < (matrix.get("n_total_in_table") or 0)
        if not partial_matrix:
            want_m = _sha256(mtx)
            want_s = _sha256(snap)
            if not gen.get("matrix_sha256") or not gen.get("snapshot_sha256"):
                P.append(("GEN", "sidecar 缺 matrix_sha256/snapshot_sha256 —— 矩阵→快照→成片链未封口，重出成片"))
            else:
                if gen.get("matrix_sha256") != want_m:
                    P.append(("GEN", "sidecar 的 matrix_sha256 与现役 results/matrix.json 不同 —— 重跑最终矩阵并重出成片"))
                if gen.get("snapshot_sha256") != want_s:
                    P.append(("GEN", "sidecar 的 snapshot_sha256 与 09_matrix.txt 不同 —— 先固定快照再重出成片"))

    # J3 承重读数账：每条 (文件, 键, 打印串) 必须仍然逐字成立
    prov = side.get("number_provenance") or {}
    kr = prov.get("key_readings") or []
    if not kr:
        P.append(("DENOM", "sidecar 没有 key_readings 账（条数 0）—— 分母塌了，"
                           "这一格无从判同源，重出成片"))
    miss = []
    drift = 0
    # `results/matrix.json` 的**汇总格**不按逐条回归档重解：链条只在末尾落终表，
    # 所以这一格在跑动中读到的是**本趟的中间表**（`steps`=产证据那几格），
    # 而成片是在上一趟的终表上取的数 —— 两边不等是生命周期不同，不是成片过期。
    # 这几个数另有判据管着：下面 J5 拿「捕获件里的 MATRIX 头/PASS 行」与
    # **同一趟**的矩阵行逐个对，那才是同生命周期的比法。
    AGG_TOP_KEYS = ("steps", "n_pass", "all_green", "started_utc", "now_utc",
                    "rtl_sha256_head", "wall_s", "failed")
    agg_excluded = 0
    for e in kr:
        frel = str(e.get("file", "")).replace("\\", "/")
        ktop = str(e.get("key", "")).split(".")[0]
        if frel == "results/matrix.json" and ktop in AGG_TOP_KEYS:
            agg_excluded += 1
            continue
        fp = os.path.join(root, str(e.get("file", "")).replace("/", os.sep))
        if not os.path.isfile(fp):
            miss.append("缺文件 %s" % e.get("file"))
            continue
        try:
            v = dig(jload(fp), str(e.get("key", "")).split("."), [], e.get("key"))
        except Exception as ex:
            miss.append("%s[%s] 解不出: %s" % (e.get("file"), e.get("key"),
                                               repr(ex)[:60]))
            continue
        if shown(v) != str(e.get("printed")):
            drift += 1
            miss.append("DRIFT %s[%s] 片上=%s 现盘=%s"
                        % (e.get("file"), e.get("key"), e.get("printed"), shown(v)))
    if miss:
        P.append(("READINGS", "%d 条承重读数不再成立，首例 %s" % (len(miss), miss[0])))

    # J5 快照行：09_matrix.txt 的每一行必须与现役矩阵的对应行一致
    rows = {r.get("step"): r for r in (matrix.get("rows") or [])}
    # 链条的 emit() 在证据步跑完、后置格开跑之前先落一份半的矩阵（steps=25），
    # 于是「本趟最后一格读到的 matrix.json」可能就是本趟自己那一份半的。
    # 这时候后置格在矩阵里还不存在，把它判成「快照引了一个不存在的步骤」是读错了对象：
    # 那一格只是还没跑。判据改成——半份矩阵只比它已经写出来的那些行，跳过的条数印出来。
    partial = (matrix.get("steps") or 0) < (matrix.get("n_total_in_table") or 0)
    excl = 0
    fp_excl = 0
    partial_skipped = 0
    bad = []
    for ln in read(snap).splitlines():
        mm = MATRIX_LINE.match(ln)
        if mm:
            n_pass, n_tot, ag = int(mm.group(1)), int(mm.group(2)), mm.group(3)
            if partial:
                partial_skipped += 1
            elif (n_tot != matrix.get("steps") or n_pass != matrix.get("n_pass")
                    or (ag == "True") != bool(matrix.get("all_green"))):
                bad.append("快照头 %s/%s all_green=%s 而矩阵是 %s/%s %s"
                           % (n_pass, n_tot, ag, matrix.get("n_pass"),
                              matrix.get("steps"), matrix.get("all_green")))
            continue
        m = PASS_LINE.match(ln)
        if not m:
            continue
        name, blob = m.group(1), m.group(2)
        try:
            ev_old = json.loads(blob)
        except Exception:
            bad.append("%s 那行的字段块解不出" % name)
            continue
        row = rows.get(name)
        if row is None:
            if partial:
                partial_skipped += 1     # 这一格还没跑，不是不存在
                continue
            bad.append("快照里的步骤 %s 在现役矩阵里不存在" % name)
            continue
        ev_new = row.get("evidence") or {}
        for k, v in sorted(ev_old.items()):
            if (name, k) in FOOTPRINT:
                # 存在性照核（这一格不许整个消失），只放行「值每趟不同」这一件事。
                fp_excl += 1
                if k not in ev_new:
                    bad.append("%s 的脚印键 %s 在现役矩阵里没了" % (name, k))
                continue
            if VOLATILE.search(k):
                excl += 1
                if k not in ev_new:
                    bad.append("%s 的易变键 %s 在现役归档里没了" % (name, k))
                continue
            if k not in ev_new:
                bad.append("%s 快照有键 %s 而现役证据里没有" % (name, k))
            elif not same(ev_new[k], v):
                bad.append("SNAPSHOT-STALE %s.%s 快照=%s 现役=%s"
                           % (name, k, shown(v), shown(ev_new[k])))
    if bad:
        # 全名单落到明细件：只印首例的那一发会把「还剩几条」藏起来，而这几条各有各的成因
        # （实测这一格先报 ppt.tiers 的打印序，再报 navigation.logs 的每代自增计数）。
        try:
            det = os.path.join(root, "results", "logs",
                               "demo_artifacts_snapshot.txt")
            if os.path.isdir(os.path.dirname(det)):
                with io.open(det, "w", encoding="utf-8", newline="\n") as _f:
                    _f.write("\n".join(bad) + "\n")
                    _f.write("SNAPSHOT_TOTAL %d printed %d\n"
                             % (len(bad), min(4, len(bad))))
        except Exception:
            pass
        P.append(("SNAPSHOT", "%d 处快照与现役矩阵不一致，前 %d 条 %s（全名单见 "
                  "results/logs/demo_artifacts_snapshot.txt）"
                  % (len(bad), min(4, len(bad)), " | ".join(bad[:4]))))

    # J6 音轨：说了有就必须真解得出非静音
    claims_audio = u"没有解说音轨" not in (side.get("not") or [])
    aud = probe_audio(mp4)
    if claims_audio:
        if not aud:
            P.append(("AUDIO", "sidecar 说有成片有解说音轨，但从容器解不出音流"))
        elif aud["rms"] <= 1e-4:
            P.append(("AUDIO", "音轨解出来是静音 rms=%.6f —— 有流不等于有声" % aud["rms"]))
    # J5 的分母守卫：一行都没比过 ≠ 比过且全对。捕获件里 PASS 行的形状是
    # `PASS <步名> {json}`，而成片那一屏抄的是同一些行 —— 若解析面塌了（链条改了
    # 打印格式、或捕获件被截断），这一格会静默变成「什么都没判」。
    nsnap = sum(1 for l in read(snap).splitlines() if PASS_LINE.match(l))
    if nsnap == 0:
        P.append(("SNAP-DENOM", "捕获件 %s 里一行 PASS 都没解析到 —— 快照判据在空转，"
                                "先看打印格式再看成片" % os.path.join("results", "demo",
                                                                      "09_matrix.txt")))
    return P, {"readings": len(kr), "drift": len(miss), "volatile_excluded": excl,
               "agg_excluded": agg_excluded,
               "partial_skipped": partial_skipped,
               "footprint_excluded": fp_excl,
               "seconds": meas["seconds"], "mib": round(meas["bytes"] / 1048576.0, 1),
               "audio": ("rms=%s/%ss" % (aud["rms"], aud["seconds"]) if aud else "none"),
               "snapshot_lines": nsnap}


# --------------------------------------------------------------- 夹具
def _mkmp4(path, seconds=2.0, fps=10.0, wav=None, silent=False):
    """造一间替身房用的极小成片：几帧 + 可选一条音轨（正弦或全零）。"""
    import cv2
    import numpy as np
    n = int(round(seconds * fps))
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (160, 90))
    if not vw.isOpened():
        raise AssertionError("替身房里开不出 mp4")
    for _ in range(n):
        vw.write(np.zeros((90, 160, 3), dtype=np.uint8))
    vw.release()
    if wav is None:
        return path
    import imageio_ffmpeg
    import subprocess
    tmp = path + ".aud.mp4"
    tone = os.path.join(os.path.dirname(wav), "tone.wav")
    sr = 8000
    cnt = int(sr * seconds)
    a = (np.zeros(cnt, dtype=np.int16) if silent else
         (3200 * np.sin(2 * np.pi * 440 * np.arange(cnt) / sr)).astype(np.int16))
    import wave
    with wave.open(tone, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(a.tobytes())
    o = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel",
                        "error", "-y", "-i", path, "-i", tone, "-map", "0:v:0",
                        "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-shortest", tmp],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if o.returncode != 0 or not os.path.isfile(tmp):
        raise AssertionError("替身房混流失败: %s" % o.stdout.decode("utf-8", "replace")[-200:])
    os.replace(tmp, path)
    return path


def build_house(with_audio=True, silent=False):
    """造一间完整的替身房：矩阵 + 快照 + 一份 bench 归档 + 成片 + sidecar。"""
    root = tempfile.mkdtemp(prefix="epv32_demohouse_")
    os.makedirs(os.path.join(root, "results", "demo"))
    os.makedirs(os.path.join(root, "results", "bench"))
    head = "aa" * 32
    ev = {"assertions": 365, "failed": 0, "exe_mtime": 1790268775}
    rows = [{"step": "iss_selftest", "status": "PASS", "wall_s": 1.0, "evidence": ev},
            {"step": "nn_bench", "status": "PASS", "wall_s": 2.0,
             "evidence": {"speedup": 15.734, "bit_exact": True}}]
    json.dump({"started_utc": "2026-09-27T01:00:00Z", "steps": 2, "n_pass": 2,
               "failed": 0, "skipped": 0, "all_green": True,
               "rtl_sha256_head": head, "rows": rows},
              io.open(os.path.join(root, "results", "matrix.json"), "w",
                      encoding="utf-8"), indent=1)
    with io.open(os.path.join(root, "results", "bench", "nn_bench.json"), "w",
                 encoding="utf-8") as f:
        json.dump({"speedup": 15.734, "bit_exact": True, "macs": 36864}, f)
    with io.open(os.path.join(root, "results", "demo", "09_matrix.txt"), "w",
                 encoding="utf-8") as f:
        f.write(u"PASS  iss_selftest     %s\n" % json.dumps(ev))
        f.write(u"PASS  nn_bench         %s\n" % json.dumps({"speedup": 15.734,
                                                             "bit_exact": True}))
        f.write(u"MATRIX  2/2  all_green=True\n")
    mp4 = os.path.join(root, "results", "demo", "demo_video.mp4")
    if with_audio:
        _mkmp4(mp4, wav=os.path.join(root, "results", "demo", "_a.wav"), silent=silent)
    else:
        _mkmp4(mp4)
    meas = measure_container(mp4)
    def _house_sha(path):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            h.update(f.read())
        return h.hexdigest()
    side = {
        "not": ([] if with_audio and not silent else [u"没有解说音轨"]) + [u"没有出镜"],
        "duration_s": meas["seconds"], "limit_duration_s": 600.0,
        "size_bytes": meas["bytes"], "limit_size_bytes": 100 * 1048576,
        "generation": {"rtl_sha256_head": head, "matrix_steps": 2,
                       "matrix_n_pass": 2,
                       "matrix_sha256": _house_sha(os.path.join(root, "results", "matrix.json")),
                       "snapshot_sha256": _house_sha(os.path.join(root, "results", "demo", "09_matrix.txt"))},
        "number_provenance": {"key_readings": [
            {"file": "results/bench/nn_bench.json", "key": "speedup",
             "printed": "15.734"},
            {"file": "results/bench/nn_bench.json", "key": "macs",
             "printed": "36864"},
            # 汇总格也进账：真实成片确实印过它（第 12 屏那句「N 步」），
            # 有这一条才谈得上「豁免面被数到」而不是被假设。
            {"file": "results/matrix.json", "key": "steps", "printed": shown(2)}],
           "corpus_sha256_is_a_verdict": False},
    }
    json.dump(side, io.open(os.path.join(root, "results", "demo", "demo_video.json"),
                            "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return root


def _edit_json(path, mutate):
    d = jload(path)
    mutate(d)
    json.dump(d, io.open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest()

def _refresh_house_generation(root, matrix=True, snapshot=False):
    sidep = os.path.join(root, "results", "demo", "demo_video.json")
    side = jload(sidep)
    gen = side.setdefault("generation", {})
    if matrix:
        gen["matrix_sha256"] = _sha256_file(os.path.join(root, "results", "matrix.json"))
    if snapshot:
        gen["snapshot_sha256"] = _sha256_file(os.path.join(root, "results", "demo", "09_matrix.txt"))
    json.dump(side, io.open(sidep, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def selftest():
    fails = []

    def expect_ok(root, why):
        probs, st = audit(root)
        if probs:
            fails.append("%s: 健康替身房被判红 %s" % (why, probs[:2]))
        return st

    # 1) 正对照：一切都同源
    root = build_house(with_audio=True)
    st = expect_ok(root, "healthy")
    if st.get("readings", 0) < 2:
        fails.append("healthy: 承重读数账分母 <2（夹具自己形状不对）: %s" % st)

    # 2) 某条屏上读数在归档里变了 -> 必须点名 READINGS
    _edit_json(os.path.join(root, "results", "bench", "nn_bench.json"),
               lambda d: d.update({"speedup": 15.999}))
    probs, _ = audit(root)
    if not any(c == "READINGS" and "15.734" in m and "15.999" in m
               for c, m in probs):
        fails.append("reading-drift: 改了归档里的 speedup 竟然没红: %s" % probs[:2])

    # 2b) 撤销这条改动，换一种红法：代际
    _edit_json(os.path.join(root, "results", "bench", "nn_bench.json"),
               lambda d: d.update({"speedup": 15.734}))
    probs, _ = audit(root)
    if probs:
        fails.append("reading-drift 复原后仍红: %s" % probs[:1])

    # 3) 跨代：矩阵换了 RTL 哈希
    _edit_json(os.path.join(root, "results", "matrix.json"),
               lambda d: d.update({"rtl_sha256_head": "bb" * 32}))
    probs, _ = audit(root)
    if not any(c == "GEN" for c, _m in probs):
        fails.append("cross-generation: 换代会导致红却没红: %s" % probs[:2])
    _edit_json(os.path.join(root, "results", "matrix.json"),
               lambda d: d.update({"rtl_sha256_head": "aa" * 32}))

    # 4) 快照过期：09_matrix.txt 写着 365 而矩阵变到 400
    _edit_json(os.path.join(root, "results", "matrix.json"),
               lambda d: d["rows"][0]["evidence"].update({"assertions": 400}))
    _refresh_house_generation(root)
    probs, _ = audit(root)
    if not any(c == "SNAPSHOT" and "assertions" in m for c, m in probs):
        fails.append("stale-snapshot: 快照与矩阵不一致却没红: %s" % probs[:2])
    _edit_json(os.path.join(root, "results", "matrix.json"),
               lambda d: d["rows"][0]["evidence"].update({"assertions": 365}))

    # 5) 分母守卫：账本是空的（老版本出的片）
    _edit_json(os.path.join(root, "results", "demo", "demo_video.json"),
               lambda d: d["number_provenance"].update({"key_readings": []}))
    probs, _ = audit(root)
    if not any(c == "DENOM" for c, _m in probs):
        fails.append("no-reading-ledger: 空账本竟然放行: %s" % probs[:2])

    # 6) 「有音轨」但解出来是静音
    r2 = build_house(with_audio=True, silent=True)
    _edit_json(os.path.join(r2, "results", "demo", "demo_video.json"),
               lambda d: d.update({"not": [u"没有出镜"]}))   # 声称有音轨
    probs, _ = audit(r2)
    if not any(c == "AUDIO" for c, _m in probs):
        fails.append("silent-track: 全零音轨被当成有声: %s" % probs[:2])

    # 7) sidecar 自述与容器不符
    r3 = build_house(with_audio=True)
    _edit_json(os.path.join(r3, "results", "demo", "demo_video.json"),
               lambda d: d.update({"size_bytes": d["size_bytes"] + 7}))
    probs, _ = audit(r3)
    if not any(c == "LIES" for c, _m in probs):
        fails.append("sidecar-lies: 字节数不符没红: %s" % probs[:2])

    # 8) 正对照（最重要的一条）：**只有**易变字段变了，必须仍然绿。
    #    这条夹具挡的就是「把逐份哈希相等当判据」造出的那发永远红的门。
    r4 = build_house(with_audio=True)
    _edit_json(os.path.join(r4, "results", "matrix.json"),
               lambda d: d.update({"started_utc": "2027-01-01T00:00:00Z"}))
    _edit_json(os.path.join(r4, "results", "matrix.json"),
               lambda d: d["rows"][0]["evidence"].update({"exe_mtime": 1799999999}))
    _refresh_house_generation(r4)
    probs, st4 = audit(r4)
    if probs:
        fails.append("volatile-only-changed: 只改时间戳就判红（门太严，会被删）: %s"
                     % probs[:2])
    if st4.get("volatile_excluded", 0) < 1:
        fails.append("volatile-only-changed: 排除面没被数到（0 条说明那条键没被豁免）")

    # 9) 中间表 ≠ 漂移：链条只在末尾落终表，所以 `demo_artifacts` 跑的时候盘上那份
    #    `matrix.json` 的**汇总格**天生是这一趟的中间态（产证据那几格），而成片是在
    #    上一趟的终表上取的数。J3 不许把这个当成「屏上的数变了」，但同一件事必须由
    #    J5 接手（快照头与矩阵行数同生命周期，那条不一致照样要红）—— 只挪责任，不开洞。
    r5 = build_house(with_audio=True)
    _edit_json(os.path.join(r5, "results", "matrix.json"),
               lambda d: d.update({"steps": 5, "n_pass": 4}))
    probs, st5 = audit(r5)
    if any(c == "READINGS" and "matrix.json" in m for c, m in probs):
        fails.append("matrix-agg-mid-run: 汇总格被当成屏上读数漂移: %s" % probs[:1])
    if st5.get("agg_excluded", 0) < 1:
        fails.append("matrix-agg-mid-run: 豁免面没被数到（0 条＝那条排除根本没生效）")
    if not any(c == "SNAPSHOT" for c, _m in probs):
        fails.append("matrix-agg-mid-run: J3 让开之后 J5 也没接手 —— 这条没人判了(洞)")

    # 10) 快照判据空转：捕获件里一行 PASS 都没有（链条改了打印形状就会这样）。
    #     「比了 0 行」与「比了 24 行且全对」必须能分开，否则这条门是装饰。
    r6 = build_house(with_audio=True)
    with io.open(os.path.join(r6, "results", "demo", "09_matrix.txt"), "w",
                 encoding="utf-8") as f:
        f.write(u"MATRIX  2/2  all_green=True\n")
    probs, st6 = audit(r6)
    if not any(c == "SNAP-DENOM" for c, _m in probs):
        fails.append("vacuous-snapshot: PASS 行为 0 却没判红，快照门在空转: %s" % probs[:1])
    if st6.get("snapshot_lines", -1) != 0:
        fails.append("vacuous-snapshot: 分母读数与构造不符: %s" % st6.get("snapshot_lines"))

    for r in (root, r2, r3, r4, r5, r6):
        shutil.rmtree(r, ignore_errors=True)

    for i, n in enumerate(FIXTURE_NAMES, 1):
        print("[selftest] %-24s %s" % (n, "see above lines" if fails else "ok"))
    for f in fails:
        print("SELFTEST-FAIL: " + f)
    print("DEMO_ARTIFACTS_SELFTEST %s %d/%d" % ("OK" if not fails else "FAIL",
                                                len(FIXTURE_NAMES) - len(fails),
                                                len(FIXTURE_NAMES)))
    return 1 if fails else 0


def main(argv):
    if "--selftest" in argv:
        return selftest()
    root = ROOT
    probs, st = audit(root)
    if probs and all(c == "SETUP" for c, _m in probs):
        for c, m in probs:
            print("SETUP-FAIL %s" % m.encode("ascii", "backslashreplace").decode("ascii"))
        print("DEMO_ARTIFACTS_SETUP_FAIL inputs=%d" % len(probs))
        return 2
    for c, m in probs:
        # 明细可能含中文（路径/步骤名），判决行本身保持 ASCII
        print("PROBLEM [%s] %s" % (c, m.encode("ascii", "backslashreplace").decode("ascii")))
    ok = (not probs)
    print("DEMO_ARTIFACTS_%s readings=%s drift=%s volatile_excluded=%s "
          "agg_excluded=%s partial_skipped=%s footprint_excluded=%s "
          "snapshot_lines=%s duration_s=%s size_mib=%s "
          "audio=%s problems=%d"
          % ("OK" if ok else "FAIL", st.get("readings"), st.get("drift"),
             st.get("volatile_excluded"), st.get("agg_excluded"),
             st.get("partial_skipped"), st.get("footprint_excluded"),
             st.get("snapshot_lines"),
             st.get("seconds"), st.get("mib"),
             str(st.get("audio")).encode("ascii", "backslashreplace").decode("ascii"),
             len(probs)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
