# -*- coding: utf-8 -*-
"""flow/demo_narration.py -- 给方案 Demo 成片配一条中文解说轨（合成语音）与同源字幕。

上一版成片是无声的归档回放片，sidecar 的 `not` 写着「没有解说音轨」，而评审标准里有
「讲解效果」—— 这是整包里「不做就一定丢分」的一格。三条承重设计：

1) **旁白文本只有一个真源**：`demo/演示脚本与讲稿.md` 里每段的「> 讲：…」块。
   字幕与音轨读同一串字符，不会「说的是一套、屏上是另一套」。时长装不下时按**整句**
   截断，并把「用了多少字／一共多少字」记进 sidecar，不许静默丢内容或另写短版旁白。
2) **音频落盘前必须自证它有声**：从成片容器再解出音轨测时长与 RMS。只查「有 Audio 流」
   是坏判据—— 一条全零采样的音轨也能过，与无声对观众没有区别。
3) **装不下即停手**：某段旁白比它那段画面还长 ⇒ 抛错并点名，而不是把语速调到听不清、
   也不是偷偷拉长画面时长（时长是赛题硬指标）。

用法（调试）
------------
    python flow/demo_narration.py --smoke        # 一句旁白 -> wav -> 量时长与 RMS
    python flow/demo_narration.py --smoke-mux    # 再把现有成片当视频流混一段测试音轨
两个模式都只往 `scratch_narr/` 里写，不碰 results/。
"""
from __future__ import print_function
import io
import json
import os
import re
import subprocess
import sys
import wave

WAV_SR = 16000                       # 只用来量时长/RMS 的降采样率，不是音轨质量
VOICE = "Microsoft Huihui Desktop"   # 本机唯一 zh-CN 语音，由 GetInstalledVoices 现读确认
MAX_RATE = 2                         # 语速上限：超过这个就不是「讲得清楚」的范围

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT_MD = os.path.join(ROOT, "demo", u"演示脚本与讲稿.md")
PS_BODY = """param([string]$Manifest)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Speech
$lines = [System.IO.File]::ReadAllLines($Manifest, [System.Text.Encoding]::UTF8)
foreach ($ln in $lines) {
  if ($ln.Trim().Length -eq 0) { continue }
  $p = $ln.Split("`t")
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $s.SelectVoice('%(voice)s') | Out-Null
  $s.Rate = [int]$p[2]
  $txt = [System.IO.File]::ReadAllText($p[0], [System.Text.Encoding]::UTF8)
  $s.SetOutputToWaveFile($p[1])
  $s.Speak($txt)
  $s.Dispose()
}
Write-Output ("TTS_LINES=" + $lines.Count)
""" % {"voice": VOICE}


def ffmpeg_exe():
    """取 ffmpeg 二进制。本机系统 PATH 上没有，只有 imageio_ffmpeg 自带的那一份。"""
    import imageio_ffmpeg
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    if not os.path.isfile(exe):
        raise AssertionError("imageio_ffmpeg 报出的可执行文件不在盘上: %s" % exe)
    return exe


# --------------------------------------------------------------- 讲稿解析
SEG_HEAD = re.compile(r"^### 段\s*(\d+)\s*·\s*(.*)$")
# 只有以「说话动词」开头的引用块才是口播稿。段 12 里同时存在
# 「> 念：…」(口播) 与 「> 这一句本轮被改写过：…」(对稿子的注释)，
# 后者含「本轮／评审／台账」这类过程语言，一旦被念出来或被烧进字幕，
# 就把刚被消掉的那个缺陷又放回成片里。所以按动词白名单收，不按「是不是引用块」收。
NARR_HEAD = re.compile(r"^>\s*(讲|念下去|念|说|收尾一句|补一句|强调)\s*[：:]\s*(.*)$")
META_HEAD = re.compile(r"^>\s*[^讲念说收补强]")
SENT_END = re.compile(u"[。！？]")
# 不许进画面/音轨的字样（与随包正文那条禁令同一组词）。
BANNED_IN_NARRATION = (u"评委", u"评审", u"收口表", u"台账", u"本轮", u"上一轮",
                       u"第三方", u"扣分", u"我原稿")


def parse_script(path=SCRIPT_MD):
    """把讲稿解成 [(段号, 标题, 旁白全文)]，顺序即段号顺序。

    引用块之间的分隔符就是一根光杆 `>`，所以**按块**判而不是按行判：
    一块的第一行以说话动词开头 ⇒ 整块是口播；否则整块是「给录屏的人看的注释」。
    段 12 就是这种混排：三块口播（念 / 念下去 / 收尾一句）夹着两块对稿子的注释，
    而后者写着「本轮」「评审」「台账」——按行判会把它们念出来，等于把刚消掉
    的过程语言又烧回成片里。
    """
    if not os.path.isfile(path):
        raise AssertionError("讲稿不在盘上: %s" % path)
    raw = io.open(path, encoding="utf-8").read().splitlines()
    segs, cur, block = [], None, []

    def flush_block():
        if not block or cur is None:
            del block[:]
            return
        head = NARR_HEAD.match(block[0])
        if head:
            body = " ".join([head.group(2)] + [l.lstrip(">").strip() for l in block[1:]])
            cur[2] += (" " if cur[2] else "") + body
        del block[:]

    for ln in raw:
        m = SEG_HEAD.match(ln)
        if m or (not ln.startswith(">") and ln.strip()):
            flush_block()
        if m:
            if cur and cur[2].strip():
                segs.append(cur)
            cur = [int(m.group(1)), m.group(2).strip(), ""]
            continue
        if ln.startswith(">"):
            if ln.strip() in (">", "*>"):
                flush_block()
                continue
            block.append(ln)
    flush_block()
    if cur and cur[2].strip():
        segs.append(cur)
    out = []
    for num, title, body in segs:
        body = clean_narration(body)
        if body:
            out.append((num, title, body))
    if len(out) < 2:
        raise AssertionError("讲稿只解出 %d 段的口播，解析面塌了" % len(out))
    return out


def clean_narration(body):
    """去掉画面指令与 Markdown 装饰，只留「话」。"""
    body = body.replace("`", "")
    body = re.sub(r"\*\*([^*]*)\*\*", r"\1", body)
    body = re.sub(r"\[[^]]*\]\([^)]*\)", "", body)          # markdown 链接留文字
    body = re.sub(u"[“”\"]", "", body)
    body = re.sub(r"[（(][^)）]*(results/|sw/|rtl/|docs/|flow/)[^)）]*[)）]", "", body)
    body = re.sub(r"(python|bash)\s+\S+", "", body)          # 命令行不是「讲」的内容
    body = re.sub(r"\s+", " ", body).strip()
    return body


def sentences(text):
    """按整句切；不切开半句（半句被截断时字幕与音轨都会长得像坏了）。"""
    parts, cur = [], ""
    for ch in text:
        cur += ch
        if SENT_END.match(ch):
            parts.append(cur.strip())
            cur = ""
    if cur.strip():
        parts.append(cur.strip())
    return [p for p in parts if p]


def page_groups(page_titles):
    """把画面页按「同一段的第 i/n 页」归组：连续的同名 base 归一组。"""
    groups = []
    for i, t in enumerate(page_titles):
        b = base_of_page(t)
        if groups and groups[-1][0] == b:
            groups[-1][1].append(i)
        else:
            groups.append((b, [i]))
    return groups


def allocate(page_titles, page_seconds, segs, cps=6.0, reserve=1.2, max_chars=None):
    """把每段口播分到它那(几)页上，返回 ({页标题: 字幕文本}, 每页统计)。

    对应关系取**顺序**而非标题字符串：讲稿段标题与成片段标题不是同一写法，按前缀配
    会静默配错或配不上（段号与成片一一对应，成片里第 9 段占两页、讲稿仍按一段讲）。
    组数与段数不等即抛 —— 静默少配一段就等于那一屏没人讲。句子装不下时按整句丢，并
    分别记「因长度丢」与「因过程语言丢」两类，不许悄悄把一段话抹掉。
    """
    groups = page_groups(page_titles)
    if len(groups) != len(segs):
        raise AssertionError(
            "成片有 %d 组画面而讲稿有 %d 段口播，两者不是一一对应。画面组: %s "
            "/ 讲稿段: %s" % (len(groups), len(segs),
                             [g[0][:10] for g in groups][:20],
                             [s[1][:10] for s in segs][:20]))
    caps, stats = {}, {}
    for (base, idxs), (num, title, body) in zip(groups, segs):
        sents = sentences(body)
        banned = [s for s in sents if any(b in s for b in BANNED_IN_NARRATION)]
        usable = [s for s in sents if not any(b in s for b in BANNED_IN_NARRATION)]
        cursor, gsec = 0, sum(page_seconds[i] for i in idxs)
        for k, i in enumerate(idxs):
            # 预算 = 可用秒数 x 每秒字数（cps 由 measure_cps() 实量，不是猜的 6）。
            # 排错方向的签名很隐蔽：写成除以 cps 时每一页的预算都变成个位数，
            # 于是所有整句都「放不下」，14 页全部配到空旁白而分配本身不报错。
            budget = max(0.0, page_seconds[i] - reserve) * cps
            # 第二个约束：字幕条实际画得下几行。少算这一条，分配器会把一句
            # 「时长上讲得完」但「画面上写不下」的话派给这一页，然后渲染端抛错。
            cap_chars = budget if not max_chars else min(budget, float(max_chars))
            chunk, used, mid = [], 0, False
            while cursor < len(usable) and used + len(usable[cursor]) <= cap_chars:
                chunk.append(usable[cursor])
                used += len(usable[cursor])
                cursor += 1
            if not chunk and usable:
                # 一整句就超预算：这一句必须讲（段 12「边界」就是这种情况，它是
                # 全片最该讲的一段），但在最后一个顿号/分号处收，不许把数字劈成
                # 两半，也不许假装它讲完了 —— 被切掉的那一截记进 clauses_cut，
                # 由 sidecar 的 caveat 明写「这一段有 N 句是在句中停顿处收的」。
                s = usable[cursor]
                cut = None
                for punc in (u"；", u";", u"、", u"，"):
                    pos = s.rfind(punc, 0, max(1, int(budget)))
                    if pos > 0:
                        cut = s[:pos + 1]
                        break
                chunk = [cut or s[:int(budget)]]
                used = len(chunk[0])
                cursor += 1
                mid = True
            # 「被字幕容量挤掉几句」与「被时长挤掉几句」分开数：前者是画面几何问题
            # （改字号/加行就能拿回），后者是叙述长度问题（要改讲稿）。混成一个数，
            # 下次就不知道该动哪一边。
            eused, ej, extra = used, cursor, 0
            while ej < len(usable) and eused + len(usable[ej]) <= budget:
                eused += len(usable[ej])
                ej += 1
                extra += 1
            txt = " ".join(chunk)
            caps[page_titles[i]] = txt
            stats[page_titles[i]] = {
                "segment_no": num, "script_title": title,
                "chars_used": len(txt), "chars_total": len(body),
                "sentences_used": len(chunk), "sentences_total": len(sents),
                "sentences_dropped_for_caption_space": extra,
                "sentences_dropped_by_length": (len(usable) - cursor) - extra,
                "sentences_dropped_for_process_language": len(banned),
                "mid_sentence_cut": mid,
                "page_seconds": page_seconds[i], "group_pages": len(idxs),
                "page_index_in_group": k + 1, "seconds_of_group": round(gsec, 1),
            }
    return caps, stats


PAGE_SUFFIX = re.compile(u"（\\d+/\\d+）$")


def base_of_page(title):
    return PAGE_SUFFIX.sub("", title)


# --------------------------------------------------------------- 合成与装配
HOST_PATH_RE = re.compile(r"[A-Za-z]:[\\/](?:[^\s\"'<>|;)]+)")


def _mask(text):
    """把本机绝对路径隐掉再打印。

    合成用的临时目录在包外（`%TEMP%` 下面），而这一行会进步骤日志 ——
    步骤日志属证据层，`flow/check_package_hygiene.py` 的 R-b 判据就是抓
    「证据层泄露主机绝对路径」，本轮它真的抓到过一次（`results/demo/14_video.txt`）。
    临时目录对复现者没有信息量（它每次都不一样），所以修法是在打印端隐去而不是
    给证据层开口子。
    """
    return HOST_PATH_RE.sub("<本机绝对路径已隐去>", text)


def synthesize(items, workdir):
    """items = [(文本, 输出 wav, 语速)]；一次 PowerShell 跑完，返回 {wav: (秒, rms)}。

    items 里的文本按「每段一个 UTF-8 txt 文件」传，不走命令行 —— 中文经命令行
    会被 ANSI 代码页换掉（本机控制台是 GBK）。
    """
    if not items:
        return {}
    if not os.path.isdir(workdir):
        os.makedirs(workdir)
    man = os.path.join(workdir, "manifest.tsv")
    rows = []
    for i, (txt, wav, rate) in enumerate(items):
        tf = os.path.join(workdir, "seg_%02d.txt" % i)
        with io.open(tf, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(u"%s" % txt)
        rows.append("\t".join([tf, wav, str(int(rate))]))
    with io.open(man, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(u"%s" % "\n".join(rows))        # 只写 \n，由 newline 翻成 CRLF
    ps = os.path.join(workdir, "tts.ps1")
    with io.open(ps, "w", encoding="ascii", newline="\r\n") as f:
        f.write(PS_BODY)
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", ps, "-Manifest", man]
    sys.stderr.write("TTS: %d 段 -> %s\n" % (len(items), _mask(workdir)))
    o = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out = o.stdout.decode("utf-8", "replace")
    if o.returncode != 0 or ("TTS_LINES=%d" % len(items)) not in out:
        raise AssertionError("PowerShell TTS 没跑成 rc=%d: %s"
                             % (o.returncode, _mask(out.strip()[-400:])))
    meas = {}
    for _t, wav, _r in items:
        meas[wav] = read_wav(wav)
    return meas


def read_wav(path):
    """(时长秒, 峰值绝对值均值)。文件不存在或长度为零都抛，不返回 0。"""
    if not os.path.isfile(path) or os.path.getsize(path) < 100:
        raise AssertionError("TTS 没产出可用的 wav: %s (bytes=%s)"
                             % (path, os.path.getsize(path) if os.path.isfile(path) else None))
    with wave.open(path, "rb") as w:
        n, sw, sr = w.getnframes(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(n)
    if sr <= 0:
        raise AssertionError("wav 采样率为 0: %s" % path)
    import numpy as np
    dt = {1: np.uint8, 2: np.int16, 4: np.int32}.get(sw)
    if dt is None:
        raise AssertionError("没料到的 wav 位宽 %s 字节: %s" % (sw, path))
    a = np.frombuffer(raw, dtype=dt).astype(float)
    if sw == 1:
        a = a - 128.0
    if a.size == 0:
        raise AssertionError("wav 里没有采样: %s" % path)
    return round(n / float(sr), 3), float(np.mean(np.abs(a)) / (128.0 if sw == 1 else 32768.0))


CPS_SAMPLE = (u"这条句子用来量本机合成语音的语速，字符数与音频时长一起给出每秒字数。")


def measure_cps(workdir, rate=1):
    """实量「这个语音引擎每秒讲多少个汉字」，用来定每页能配多少旁白。

    不许写死一个 6：写死了就是拿假设当参数，而语速决定的是「哪些句子讲得完」，
    猜高了会把旁白配到画面讲不完（装配时抛错），猜低了会静默少讲。
    """
    if not os.path.isdir(workdir):
        os.makedirs(workdir)
    w = os.path.join(workdir, "cps_ref.wav")
    meas = synthesize([(CPS_SAMPLE, w, rate)], workdir)
    dur, rms = meas[w]
    if rms <= 1e-4:
        raise AssertionError("语速标定样本解出来是静音 (rms=%.6f)" % rms)
    return {"chars": len(CPS_SAMPLE), "seconds": dur, "rate": rate,
            "cps": round(len(CPS_SAMPLE) / dur, 3)}


def assemble(seg_wavs, page_seconds, out_wav, sr=24000):
    """按页时长把各段音频摆到时间轴上，不足处补静音；超出即抛。"""
    import numpy as np
    total = sum(page_seconds)
    track = np.zeros(int(round(total * sr)) + sr, dtype=np.int16)
    off = 0.0
    detail = []
    for (path, dur, rms), sec in zip(seg_wavs, page_seconds):
        slot = int(round(sec * sr))
        if dur > sec:
            raise AssertionError("第 %.0f s 的画面放不下 %.1f s 的旁白"
                                 % (sec, dur))
        if path and os.path.isfile(path):
            with wave.open(path, "rb") as w:
                srate, nch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
                raw = w.readframes(w.getnframes())
            a = np.frombuffer(raw, dtype=np.int16 if sw == 2 else np.uint8).astype(float)
            if sw == 1:
                a = (a - 128.0) * 256.0
            if nch > 1:
                a = a.reshape(-1, nch).mean(axis=1)
            if srate != sr:
                # linspace 的返回值是 float，直接当索引会抛
                # IndexError('arrays used as indices must be of integer type')。
                idx = np.linspace(0, a.size - 1,
                                  int(round(a.size * sr / float(srate)))).astype(np.int64)
                a = a[idx]
            a = a.astype(np.int16)
            need = int(round(off * sr)) + a.size
            if need > track.size:
                track = np.resize(track, need)
            track[int(round(off * sr)):int(round(off * sr)) + a.size] = a
        detail.append({"start_s": round(off, 2), "audio_s": dur, "rms": round(rms, 5),
                       "page_seconds": sec})
        off += sec
    with wave.open(out_wav, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(track.tobytes())
    return {"track": out_wav, "track_seconds": round(track.size / float(sr), 2),
            "video_seconds": round(total, 1), "per_page": detail}


def mux(video, audio, out, video_codec="copy", crf=26, preset="veryfast"):
    exe = ffmpeg_exe()
    cmd = [exe, "-hide_banner", "-y", "-i", video, "-i", audio,
           "-map", "0:v:0", "-map", "1:a:0"]
    if video_codec == "copy":
        cmd += ["-c:v", "copy"]
    else:
        cmd += ["-c:v", video_codec, "-preset", preset, "-crf", str(crf),
                "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-b:a", "112k", "-ac", "1", "-movflags", "+faststart",
            "-shortest", out]
    o = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if o.returncode != 0 or not os.path.isfile(out) or os.path.getsize(out) < 10000:
        tail = o.stdout.decode("utf-8", "replace").strip()[-700:]
        raise AssertionError("混流失败 rc=%d，产物=%s\n%s"
                             % (o.returncode, out if os.path.isfile(out) else None, tail))
    return out


def probe(path, workdir):
    """从容器里回读「有没有音轨、解出来有没有声」—— 一条全零音轨与无声等价。"""
    exe = ffmpeg_exe()
    wav = os.path.join(workdir, "probe_audio.wav")
    if os.path.isfile(wav):
        os.remove(wav)
    o = subprocess.run([exe, "-hide_banner", "-y", "-i", path, "-map", "0:a:0",
                        "-ac", "1", "-ar", str(WAV_SR), wav],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    err = o.stdout.decode("utf-8", "replace")
    if o.returncode != 0 or not os.path.isfile(wav):
        raise AssertionError("成片里解不出音轨 rc=%d: %s" % (o.returncode, err.strip()[-400:]))
    dur, rms = read_wav(wav)
    if rms <= 1e-4:
        raise AssertionError("音轨解出来是**静音** (rms=%.6f) —— 有流不等于有声" % rms)
    import cv2
    v = cv2.VideoCapture(path)
    if not v.isOpened():
        raise AssertionError("混流后的成片回读不到视频流: %s" % path)
    fps = v.get(cv2.CAP_PROP_FPS)
    frames = int(v.get(cv2.CAP_PROP_FRAME_COUNT))
    v.release()
    if frames <= 0 or fps <= 0:
        raise AssertionError("混流后容器里的帧数/帧率不合法 frames=%d fps=%s" % (frames, fps))
    has = [l.strip() for l in err.splitlines() if "Stream #" in l]
    return {"audio_stream_seconds": dur, "audio_rms_mean_abs": round(rms, 5),
            "video_frames": frames, "video_fps": fps,
            "video_seconds_from_container": round(frames / fps, 2),
            "streams": has}


DEFAULT_CPS = 4.0        # 只在「还没有实测语速」的第一发出片时用：偏保守=少讲，不会溢出


def emit_audio(page_titles, page_seconds, caps, video, workdir, crf=26):
    """合成 -> 装配 -> 混流 -> 从容器回读自证，返回写进 sidecar 的 audio 块。

    任何一步不成就抛，不返回「半个音轨」：调用方在抛错时必须把 sidecar 的
    `not` 写回「没有解说音轨」，否则盘上是一段无声片而归档说它有声音。
    """
    if not os.path.isdir(workdir):
        os.makedirs(workdir)
    ref = measure_cps(workdir)
    cps = ref["cps"]
    todo = [(caps[t], os.path.join(workdir, "p%02d.wav" % i), 1)
            for i, t in enumerate(page_titles) if caps.get(t)]
    meas = synthesize(todo, workdir)
    byw = {w: (d, r) for w, (d, r) in meas.items()}
    segs = []
    for i, t in enumerate(page_titles):
        w = os.path.join(workdir, "p%02d.wav" % i)
        segs.append((w, byw[w][0], byw[w][1]) if caps.get(t) else ("", 0.0, 0.0))
    track = assemble(segs, page_seconds, os.path.join(workdir, "track.wav"))
    # 混流中间件必须与成片**同目录**：`os.replace` 不能跨磁盘驱动器，而临时目录在 C:、
    # 交付件在 D:（实测 OSError(18, 系统无法将文件移到不同的磁盘驱动器)）。
    # 放在交付件旁边还有一层好处：失败时不会有半截文件停在系统临时目录里没人清。
    tmp = video + ".muxing.tmp.mp4"
    try:
        if os.path.isfile(tmp):
            os.remove(tmp)
        mux(video, track["track"], tmp, video_codec="libx264", crf=crf)
        pr = probe(tmp, workdir)
        if abs(pr["video_seconds_from_container"] - track["video_seconds"]) > 1.5:
            raise AssertionError("混流后容器时长 %.1f s 与音轨 %.1f s 差超过 1.5 s"
                                 % (pr["video_seconds_from_container"],
                                    track["video_seconds"]))
        os.replace(tmp, video)
    finally:
        # 成功路径上 tmp 已经被换名走了；这一支只挡失败路径往交付目录里留中间件。
        if os.path.isfile(tmp):
            os.remove(tmp)
    return {
        "engine": "Windows System.Speech", "voice": VOICE,
        "synthetic": True,
        "replacement": "真人配音可替换：python flow/make_demo_video.py --emit --no-tts 之后 "
                       "用你自己录的音轨跑 flow/demo_narration.py 的 mux 一步",
        "speech_rate_cps_measured": cps, "cps_reference": ref,
        "default_cps_before_measurement": DEFAULT_CPS,
        "narration_source": u"demo/演示脚本与讲稿.md 的每段「> 讲：/念：/收尾一句：」块",
        "caption_equals_audio_text": True,
        "pages_with_narration": len(todo), "pages_total": len(page_titles),
        "chars_spoken": sum(len(caps.get(t, "")) for t in page_titles),
        "per_page": [dict(s, page=t, text_chars=len(caps.get(t, "")))
                     for t, s in zip(page_titles, track["per_page"])],
        "track_seconds": track["track_seconds"],
        "container_after_mux": pr,
    }


# --------------------------------------------------------------- 调试入口
def a(s):
    """控制台是 GBK：中文打印会崩或糊，而崩了长得像「没跑到」。判决行一律 ASCII。"""
    return s.encode("ascii", "backslashreplace").decode("ascii")


def main(argv):
    os.chdir(ROOT)
    segs = parse_script()
    print("SEGMENTS %d" % len(segs))
    if "--list" in argv:
        for n, t, b in segs:
            print("  seg %-2d chars=%-4d sents=%-2d | %s" % (n, len(b), len(sentences(b)), a(t)))
        return 0
    if "--plan" in argv:
        # 拿成片真实的时间轴验一次「段数 == 画面组数」，这是在接线之前先撞一次
        sys.path.insert(0, os.path.join(ROOT, "flow"))
        import make_demo_video as V
        S = V.plan(V.corpus())
        titles = [x[0] for x in S]
        secs = [x[3] for x in S]
        print("PAGES %d GROUPS %d TOTAL_S %.0f"
              % (len(S), len(page_groups(titles)), sum(secs)))
        wk = os.path.join(ROOT, "scratch_narr")
        if not os.path.isdir(wk):
            os.makedirs(wk)
        ref = measure_cps(wk)
        print("CPS_REF %s" % json.dumps(ref))
        caps, stats = allocate(titles, secs, segs, cps=ref["cps"])
        empty = [t for t in titles if not caps[t]]
        print("ALLOC pages=%d with_narration=%d without=%d chars_used=%d/%d"
              % (len(caps), len(caps) - len(empty), len(empty),
                 sum(s["chars_used"] for s in stats.values()),
                 sum(s["chars_total"] for s in stats.values())))
        print("DROPPED_FOR_PROCESS_LANGUAGE %d  BY_LENGTH %d"
              % (sum(s["sentences_dropped_for_process_language"] for s in stats.values()),
                 sum(s["sentences_dropped_by_length"] for s in stats.values())))
        for t in titles:
            st = stats[t]
            print("  %-6ds use=%-4d tot=%-4d sents=%d/%d speech~%4.1fs  %s"
                  % (st["page_seconds"], st["chars_used"], st["chars_total"],
                     st["sentences_used"], st["sentences_total"],
                     st["chars_used"] / ref["cps"], a(t)))
        if empty:
            print("PAGES_WITHOUT_NARRATION %d" % len(empty))
        with io.open(os.path.join(wk, "plan_preview.txt"), "w",
                     encoding="utf-8", newline="\n") as f:
            for t in titles:
                f.write(u"%s | %s\n%s\n\n" % (t, stats[t]["page_seconds"], caps[t]))
        return 0
    work = os.path.join(ROOT, "scratch_narr")
    if not os.path.isdir(work):
        os.makedirs(work)
    if "--smoke" in argv:
        txt = sentences(segs[0][2])[0]
        w = os.path.join(work, "smoke.wav")
        meas = synthesize([(txt, w, 1)], work)
        print("SMOKE voice=%s chars=%d -> %s" % (a(VOICE), len(txt), json.dumps(meas[w])))
        return 0
    if "--smoke-mux" in argv:
        src = os.path.join(ROOT, "results", "demo", "demo_video.mp4")
        if not os.path.isfile(src):
            print("NO_SOURCE_VIDEO")
            return 1
        txt = " ".join(sentences(segs[0][2])[:3])
        w = os.path.join(work, "smoke.wav")
        meas = synthesize([(txt, w, 1)], work)
        track = assemble([(w, meas[w][0], meas[w][1])] * 3, [20.0, 20.0, 20.0],
                         os.path.join(work, "track.wav"))
        out = mux(src, track["track"], os.path.join(work, "smoke_mux.mp4"))
        print("SMOKE_MUX track_s=%s" % json.dumps(track["track_seconds"]))
        print("SMOKE_MUX probe=%s" % a(json.dumps(probe(out, work), ensure_ascii=False)))
        print("NARR_SMOKE_OK")
        return 0
    print("usage: --list | --plan | --smoke | --smoke-mux")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
