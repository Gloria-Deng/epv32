#!/usr/bin/env python3
"""Cross-check certified run archives against the certification matrix.

Why this exists: an extra experiment (the VCD page of the demo) re-ran
epv_bisect with a 4200-cycle window and wrote its TIMEOUT verdict into the
**same** archive path the certified run uses.  Nothing went red anywhere --
the matrix simply re-ran the step afterwards and re-overwrote the file, so
"green" depended on step ordering instead of on the archive being the object
that was actually certified.  This gate makes that invisible in both
directions:

  R1 archive-exists   every certified step has an archive we can open
  R2 evidence-match   the numbers the matrix recorded == the numbers the
                      archive holds right now (either side may drift)
  R3 untagged         a certified archive was not produced by a --tag run
  R4 natural-stop     the run stopped on its own (cycles < max_cyc), not at
                      the window edge
  R5 filing           any tagged archive lives at <name>_<tag>.json
  R6 step-table       the checker's step list still matches the matrix steps
  R7 readable         every archive on a certified path parses as JSON
                      (a killed run used to leave NUL-filled files there)

stdout is ASCII only (the Windows console here is GBK and a non-ASCII print
fails in a way that looks like success).  --selftest mutates substitute
copies in a temp dir and must go red once per injected defect, then re-reads
the real archives to prove the injections had zero side effects.
"""
import io
import json
import os
import sys
import shutil
import hashlib
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# load() 会把「存在但读不出」的路径记到这里，只为了在 matrix.json 上把
# 「没有归档」与「归档坏了」这两种红分开报 —— 后者的修法是重跑那一步。
UNREADABLE = set()

# step -> archives -> [(key the matrix recorded, key the archive holds)].
# nn_bench's headline numbers are re-summed by tools/bench_archive.py into
# results/bench/, so those keys come from there; the raw run archive is still
# checked for the run window (R4).
CERT = {
    "epv_bisect":   [("results/sim/epv_bisect.json",
                      [("cycles", "cycles"), ("retired", "retired"),
                       ("verdict", "verdict"), ("src_sha", "src_sha")])],
    "epv_selftest": [("results/sim/epv_selftest.json",
                      [("cycles", "cycles"), ("retired", "retired"),
                       ("verdict", "verdict"), ("src_sha", "src_sha")])],
    "nn_bench":     [("results/bench/nn_bench.json",
                      [("ref_cyc", "ref_cycles"), ("epv_cyc", "epv_cycles"),
                       ("speedup", "speedup"),
                       ("mismatch", "bit_exact_mismatches")]),
                     ("results/sim/nn_bench.json",
                      [("src_sha", "src_sha")])],
}


def die(msg):
    raise SystemExit("ARCHIVE_CLOBBER_FAIL: " + msg)


def load(path):
    if not os.path.isfile(path):
        return None
    try:
        with io.open(path, encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, UnicodeDecodeError) as e:
        # 读不出 != 不存在：这里只返回 None，把「哪个归档坏了、该重跑哪一步」留给 R7 去报。
        UNREADABLE.add(os.path.abspath(path))
        return None


def sweep_readability(root):
    """R7: 认证路径上不许有「文件在、但不是合法 JSON」的归档。

    起因: Windows 上 open(path,'w') 会先截零再预占簇, 中断的运行会留下全零
    文件。门禁要指名道姓报出坏的是哪一份, 而不是只给一屏 traceback。
    """
    bad = []
    want = [os.path.join("results", "matrix.json")]
    simdir = os.path.join(root, "results", "sim")
    if os.path.isdir(simdir):
        want += ["results/sim/" + f for f in sorted(os.listdir(simdir))
                 if f.endswith(".json")]
    for step, archives in CERT.items():
        for rel, _p in archives:
            want.append(rel)
    for rel in dict.fromkeys(want):
        p = os.path.join(root, *rel.split("/"))
        if not os.path.isfile(p):
            continue
        try:
            with io.open(p, encoding="utf-8") as f:
                json.load(f)
        except (ValueError, UnicodeDecodeError) as e:
            raw = open(p, "rb").read()
            bad.append("R7 unreadable-archive: %s exists (%d bytes) but is not "
                       "valid JSON (%s; leading bytes %r) -- a half-written or "
                       "hand-damaged archive; rerun the step that owns it"
                       % (rel, len(raw), e.__class__.__name__, raw[:16]))
    return bad


def check(root):
    """Return the list of violations (empty == green)."""
    bad = sweep_readability(root)                    # R7 first
    mpath = os.path.join(root, "results", "matrix.json")
    mat = load(mpath)
    if mat is None:
        if os.path.abspath(mpath) in UNREADABLE:
            die("matrix archive is unreadable (see R7 above)")
        die("no matrix archive at %s (run flow/run_all.py first)" % mpath)
    rows = {r["step"]: r for r in mat.get("rows", [])}

    # R6: the step table must still describe the matrix it is checking.
    for step in CERT:
        if step not in rows:
            bad.append("R6 step-missing: matrix has no step '%s' but the "
                       "checker expects it -- one of the two was renamed"
                       % step)

    for step, archives in CERT.items():
        row = rows.get(step)
        if row is None:
            continue
        ev = row.get("evidence", {})
        # `covered` 必须按**步**累计，不能每份归档清零：一个步骤可以由两份归档
        # 共同作证（nn_bench 的头条数在 results/bench/，源码指纹在 results/sim/），
        # 按份清零会让 R2b 把另一份已经覆盖的键报成没人管。
        covered = set()
        for rel, pairs in archives:
            ap = os.path.join(root, *rel.split("/"))
            a = load(ap)
            if a is None:                                              # R1
                r1 = ("R1 archive-unreadable: %s" % rel
                      if os.path.abspath(ap) in UNREADABLE
                      else "R1 archive-missing: %s" % rel)
                bad.append("%s (recorded by step %s)" % (r1, step))
                continue
            for mk, ak in pairs:                                       # R2
                covered.add(mk)
                if mk not in ev:
                    bad.append("R2 key-missing-in-matrix: step %s recorded no "
                               "'%s' but the checker certifies it from %s"
                               % (step, mk, rel))
                    continue
                if ak not in a:
                    bad.append("R2 key-missing-in-archive: %s has no field '%s'"
                               % (rel, ak))
                    continue
                if a[ak] != ev[mk]:
                    bad.append("R2 drift: %s says %s=%r while matrix step %s "
                               "recorded %s=%r -- one of them was rewritten "
                               "after the other"
                               % (rel, ak, a[ak], step, mk, ev[mk]))
            if rel.startswith("results/sim/"):
                if a.get("tag"):                                       # R3
                    bad.append("R3 tagged-write: certified archive %s was "
                               "written by a --tag %s run" % (rel, a["tag"]))
                cyc, mx = a.get("cycles"), a.get("max_cyc")
                if isinstance(cyc, int) and isinstance(mx, int):
                    if cyc >= mx:                                      # R4
                        bad.append("R4 window-hit: %s ran %s/%s cycles, i.e. "
                                   "it was cut off by its own window and "
                                   "cannot be a certified run" % (rel, cyc, mx))
                if a.get("stop_reason") not in ("COMMIT", "TOHOST", "HALT"):
                    bad.append("R4 stop-reason: %s stopped with %r"
                               % (rel, a.get("stop_reason")))
        # R2b 在**步**这一层判：covered 已经吃进了这一步所有归档覆盖的键。
        for mk in sorted(set(ev) - covered):
            bad.append("R2b uncovered: matrix step %s recorded '%s'=%r "
                       "which no archive check covers -- a number joined "
                       "the evidence without a source to trace it to"
                       % (step, mk, ev[mk]))

    # R5: every tagged archive must be filed under name_tag.json.
    simdir = os.path.join(root, "results", "sim")
    if os.path.isdir(simdir):
        for fn in sorted(os.listdir(simdir)):
            if not fn.endswith(".json"):
                continue
            a = load(os.path.join(simdir, fn))
            if not a or not a.get("tag"):
                continue
            want = "%s_%s.json" % (a.get("name"), a["tag"])
            if fn != want:
                bad.append("R5 misfiled: %s carries tag=%s name=%s, it should "
                           "be at %s (a tagged run must never sit on a "
                           "certified path)" % (fn, a["tag"], a.get("name"),
                                               want))
    return bad


def report(bad):
    for b in bad:
        print("  " + b)
    if bad:
        print("ARCHIVE_CLOBBER_FAIL: %d violation(s)" % len(bad))
        return 1
    print("ARCHIVE_CLOBBER_OK: matrix evidence == archives, "
          "no certified archive written by a truncated/tagged run")
    return 0


def digest_all(paths):
    out = {}
    for p in paths:
        if os.path.isfile(p):
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for blk in iter(lambda: f.read(65536), b""):
                    h.update(blk)
            out[p] = h.hexdigest()
    return out


def selftest():
    """Each injection must produce exactly the named violation, on a copy."""
    real = [os.path.join(ROOT, p) for p in
            ("results/matrix.json", "results/sim/epv_bisect.json",
             "results/sim/epv_selftest.json", "results/sim/nn_bench.json",
             "results/bench/nn_bench.json")]
    if not all(os.path.isfile(p) for p in real):
        # A vacuous side-effect proof is worse than none: digest_all([])=={}
        # compares equal no matter what the injections did.
        for p in real:
            if not os.path.isfile(p):
                print("  missing real archive: " + p)
        die("SELFTEST_HAS_NO_BASELINE: cannot prove zero side effects")
    before = digest_all(real)

    # positive control first: the untouched corpus must be green
    base = check(ROOT)
    if base:
        print("[selftest] corpus is already red, cannot grade injections:")
        report(base)
        return 1

    # (label, archive, key, injected value, violation it must name,
    #  may_the_key_be_absent_originally)
    cases = [
        ("cycles-drift",
         "results/sim/epv_bisect.json", "cycles", 9000, "R2 drift", False),
        ("tag-on-certified",
         "results/sim/epv_selftest.json", "tag", "wave", "R3 tagged-write", True),
        ("window-hit",
         "results/sim/epv_selftest.json", "max_cyc", 4532, "R4 window-hit", False),
        ("bench-value-drift",
         "results/bench/nn_bench.json",
         "bit_exact_mismatches", 3, "R2 drift", False),
    ]
    fails = []
    tmp = tempfile.mkdtemp(prefix="clobber_selftest_")

    def stage(label):
        """A corpus copy holding only what check() reads -- the real results/
        tree carries a 16 MB VCD and compliance archives nobody reads here."""
        work = os.path.join(tmp, label)
        for rel in (["results/matrix.json", "results/bench/nn_bench.json"]
                    + ["results/sim/" + f for f in os.listdir(
                        os.path.join(ROOT, "results", "sim"))
                       if f.endswith(".json")]):
            src = os.path.join(ROOT, *rel.split("/"))
            if not os.path.isfile(src):
                continue
            dst = os.path.join(work, *rel.split("/"))
            if not os.path.isdir(os.path.dirname(dst)):
                os.makedirs(os.path.dirname(dst))
            shutil.copyfile(src, dst)
        return work

    def write_json(path, d):
        with io.open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)

    def expect(label, want, bad):
        hit = [b for b in bad if want in b]
        if not bad:
            fails.append("%s: injection produced NO violation" % label)
        elif not hit:
            fails.append("%s: red, but not for '%s': %s"
                         % (label, want, bad[0][:120]))
        else:
            print("[selftest] %-18s red as expected -> %s" % (label, hit[0][:96]))

    try:
        for label, rel, key, val, want, allow_new in cases:
            work = stage(label)
            p = os.path.join(work, *rel.split("/"))
            d = load(p)
            if key not in d and not allow_new:
                fails.append("%s: field %s not present in %s" % (label, key, rel))
                continue
            d[key] = val
            write_json(p, d)
            expect(label, want, check(work))

        # R2b: the matrix records a number no archive check covers
        work = stage("uncovered")
        p = os.path.join(work, "results", "matrix.json")
        d = load(p)
        for r in d["rows"]:
            if r["step"] == "epv_bisect":
                r["evidence"]["hand_tweaked_cycles"] = 999999
        write_json(p, d)
        expect("uncovered", "R2b uncovered", check(work))

        # R5: a tagged run sitting on a certified file name
        work = stage("misfiled")
        p = os.path.join(work, "results", "sim", "epv_bisect.json")
        d = load(p)
        d["tag"] = "wave"
        d["name"] = "epv_bisect"
        write_json(p, d)
        expect("misfiled", "R5 misfiled", check(work))

        # R6: rename the step in the matrix copy only
        work = stage("stepmissing")
        p = os.path.join(work, "results", "matrix.json")
        d = load(p)
        for r in d["rows"]:
            if r["step"] == "epv_bisect":
                r["step"] = "epv_bisect_v2"
        write_json(p, d)
        expect("stepmissing", "R6 step-missing", check(work))

        # R7: 模拟写一半被杀留下的全零文件（见 sweep_readability 的说明）。
        work = stage("unreadable")
        vp = os.path.join(work, "results", "sim", "epv_bisect.json")
        with open(vp, "wb") as f:
            f.write(b"\x00" * 512)
        r7 = check(work)
        expect("unreadable", "R7 unreadable-archive", r7)
        if any("R7 unreadable-archive" in b for b in r7):
            named = [b for b in r7 if "R7" in b]
            if len(named) != 1 or "epv_bisect.json" not in named[0]:
                fails.append("unreadable: R7 must name exactly the one damaged "
                             "file, got %r" % named)
            else:
                print("[selftest] R7 names one file -> %s" % named[0][:96])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # side-effect proof: the real archives were only ever read
    after = digest_all(real)
    if after != before:
        fails.append("SELFTEST TOUCHED REAL ARCHIVES")
    else:
        print("[selftest] real archives byte-identical after all injections (%d files)"
              % len(after))
    if fails:
        print("[selftest] SELFTEST_FAILED %d case(s)" % len(fails))
        for f in fails:
            print("   " + f)
        return 1
    print("ARCHIVE_CLOBBER_SELFTEST_OK: %d injections each named their reason"
          % (len(cases) + 4))
    return 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    root = ROOT
    i = sys.argv.index("--root") if "--root" in sys.argv else -1
    if i > 0:
        root = sys.argv[i + 1]
    return report(check(root))


if __name__ == "__main__":
    sys.exit(main())
