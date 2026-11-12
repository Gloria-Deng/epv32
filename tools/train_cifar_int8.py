# ============================================================================
#  tools/train_cifar_int8.py
#
#  Trains a small CNN on real CIFAR-10 pixels, quantizes it into exactly the
#  int8 fixed point sw/bench/nn_cifar.c runs on the RTL, measures top-1 of that
#  integer network on the whole 10000-image test set, and exports the weights
#  plus the quantized images the simulator arm will run.
#
#  The layer sequence is the one nn_cifar.c implements, so the accuracy and
#  cycle numbers describe the same network:
#     conv valid 3x3  8x8 x16 -> 6x6 x16   requant + relu
#     conv valid 3x3  6x6 x16 -> 4x4 x16   requant + relu
#     global max pool 4x4 -> 1x1           (max over int8: no rescale)
#     FC 16 -> 10                    requant, no relu
#  CIFAR's 3 channels are zero-padded to the 16-lane vector width; the padding
#  lanes are reported as measured MAC-efficiency cost.
#
#  Calibration is done entirely in the int8 domain (max over the int8
#  accumulator x the exact per-channel product scale), because the requantized
#  values the hardware produces are what the reported accuracy describes.
#
#  requant_int8() transcribes requant() from sw/bench/nn_net.c line by line,
#  including "round half away from zero" on the magnitude; --selftest checks it
#  against a scalar port on random operands and on both saturation edges.
#
#  Provenance: cifar-10-python.tar.gz from the official
#  https://www.cs.toronto.edu/~kriz/ URL.  sha256 is recomputed and written
#  into the archive, never hardcoded.  The batch members are pickle files --
#  that is the dataset's published format -- read only from the local copy
#  whose tarball hash was just recorded.
# ============================================================================
import argparse
import hashlib
import json
import os
import pickle
import sys
import time

import numpy as np

DATA_DIR = r"D:/Desktop/_ext_data"
TARBALL = os.path.join(DATA_DIR, "cifar-10-python.tar.gz")
BATCHES = os.path.join(DATA_DIR, "cifar-10-batches-py")
PKG = r"D:/Desktop/RSIC-V高校"
RESULTS = os.path.join(PKG, "results", "bench")
STATE = os.path.join(RESULTS, "cifar_float_state.pt")
ARCHIVE = os.path.join(RESULTS, "cifar10_int8.json")
HEADER = os.path.join(PKG, "sw", "bench", "cifar_int8.h")

NCH_IN, CIN, COUT = 3, 16, 16
H0 = W0 = 8
H1 = W1 = 6
H2 = W2 = 4
NCLS = 10
NSIM = 24
TRAIN_N, CAL_N = 20000, 2000
EPOCHS, BATCH, LR = 14, 256, 2e-3
SEED = 20260925
AB = 127.0                     # symmetric int8: +-127 used, -128 left out
ACC_BOUND = 9 * CIN * 127 * 127


def log(*a):
    print(*a, flush=True)


def sha256_file(path, chunk = 1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def atomic_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok = True)
    tmp = path + ".part"
    with open(tmp, "w", encoding = "utf-8") as f:
        json.dump(obj, f, ensure_ascii = False, indent = 1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    log("WROTE", os.path.basename(path))


# --------------------------------------------------------------- dataset
def load_cifar():
    xs, ys = [], []
    for i in range(1, 6):
        with open(os.path.join(BATCHES, "data_batch_%d" % i), "rb") as f:
            d = pickle.load(f, encoding = "bytes")
        xs.append(np.asarray(d[b"data"], dtype = np.uint8))
        ys.append(np.asarray(d[b"labels"], dtype = np.int64))
    xtr = np.concatenate(xs).reshape(-1, 3, 32, 32)
    ytr = np.concatenate(ys)
    with open(os.path.join(BATCHES, "test_batch"), "rb") as f:
        d = pickle.load(f, encoding = "bytes")
    xte = np.asarray(d[b"data"], dtype = np.uint8).reshape(-1, 3, 32, 32)
    yte = np.asarray(d[b"labels"], dtype = np.int64)
    return xtr, ytr, xte, yte


def downsample(x):
    """[N,3,32,32] uint8 -> float64 [N,3,8,8] in [-1,1] (4x4 area average)."""
    a = x.astype(np.float64)
    a = a.reshape(a.shape[0], 3, 8, 4, 8, 4).mean(axis = (3, 5))
    return a / 127.5 - 1.0


def to_nhwc_padded(x):
    n = x.shape[0]
    out = np.zeros((n, H0, W0, CIN), dtype = np.float64)
    out[:, :, :, :NCH_IN] = np.transpose(x, (0, 2, 3, 1))
    return out


# ------------------------------------------- the int8 arithmetic itself
def requant_int8(acc, m, s, rb):
    """Vectorised requant() of sw/bench/nn_net.c.

    t = (acc*M + 2^30) >> 31;  if s>0: t += 1<<(s-1);  then shift by s;  sat8(t+RB)

    The shift is ASYMMETRIC in the C reference (nn_net.c: `else if (t >= 0)
    sh = t >> s;` but for t<0 it shifts the magnitude and adds 1 when a
    remainder is left), and this function must stay asymmetric the same way; a
    symmetric "round half away from zero" is wrong and --selftest catches it.
    """
    acc = np.asarray(acc, dtype = np.int64)
    m = np.asarray(m, dtype = np.int64)
    t = (acc * m + (np.int64(1) << 30)) >> 31
    if s > 0:
        t = t + (np.int64(1) << (s - 1))
        neg = t < 0
        mag = np.where(neg, -t, t)
        q = mag >> s                                    # positive: floor
        q_neg = np.where((mag & ((np.int64(1) << s) - 1)) != 0, q + 1, q)
        sh = np.where(neg, -q_neg, q)                   # negative: away from 0
    else:
        sh = t
    return np.clip(sh + rb, -128, 127).astype(np.int8)


def requant_scalar(acc, m, s, rb):
    """Line-by-line port of the C, the second opinion used by --selftest."""
    t = (int(acc) * int(m) + (1 << 30)) >> 31
    if s > 0:
        t += 1 << (s - 1)
    if s <= 0:
        sh = t
    elif t >= 0:
        sh = t >> s
    else:
        sh = -((((-t) >> s) + (1 if ((-t) & ((1 << s) - 1)) else 0)))
    sh += rb
    return 127 if sh > 127 else (-128 if sh < -128 else int(sh))


def pick_layer_shift(ratio, tag):
    """Largest common shift s with all |M_oc| <= 2^31-1 (vcfg.S is per layer)."""
    ratio = np.asarray(ratio, dtype = np.float64)
    if not np.all(ratio > 0.0) or not np.all(np.isfinite(ratio)):
        raise SystemExit("SETUP-FAIL %s: bad requant ratios (min %.3g max %.3g)"
                         " -- calibration bug" % (tag, ratio.min(), ratio.max()))
    for s in range(31, -1, -1):
        M = np.round(ratio * (2.0 ** (31 + s)))
        if np.all(np.abs(M) <= 2**31 - 1):
            Mi = M.astype(np.int64).astype(np.int32)
            real = Mi.astype(np.float64) / (2.0 ** (31 + s))
            return int(s), Mi, real
    raise SystemExit("SETUP-FAIL %s: no shift 0..31 fits |M|<=2^31-1 "
                     "(max ratio %.3e)" % (tag, ratio.max()))


def conv_int8_acc(a, Wq, oh, ow):
    # float64 dgemm, but the operands are int8*int8 so every partial sum is an
    # exact integer as long as it stays below 2^53; ACC_BOUND is the analytical
    # worst case and it is also checked against the int32 range the RTL uses.
    n, h, w, cin = a.shape
    oc = Wq.shape[0]
    acc = np.zeros((n, oh, ow, oc), dtype = np.float64)
    for k in range(9):
        dy, dx = k // 3, k % 3
        win = a[:, dy:dy + oh, dx:dx + ow, :].reshape(-1, cin)
        acc += (win @ Wq[:, k, :].astype(np.float64).T).reshape(n, oh, ow, oc)
    amax = int(np.abs(acc).max())
    if amax > min(ACC_BOUND, 2**31 - 1):
        raise SystemExit("SETUP-FAIL: accumulator magnitude %d exceeds the "
                         "int32 range the RTL uses (bound %d)" % (amax, ACC_BOUND))
    if not np.all(acc == np.round(acc)):
        raise SystemExit("SETUP-FAIL: float64 accumulator lost integrality")
    return acc.astype(np.int64), amax


def conv_float(a, W, bias, oh, ow):
    """float reference conv, same kernel-major order. W is [OC,C,3,3]."""
    n, h, w, cin = a.shape
    oc = W.shape[0]
    if W.shape[1] != cin:
        # 权重只有 CIF_NPAD 个真实输入通道, 激活已零填充到 16 lane: 把权重的
        # 通道维一起补零, 两边形状才对得上, 且填充 lane 的贡献恒为 0。
        pad = np.zeros((oc, cin - W.shape[1], W.shape[2], W.shape[3]))
        W = np.concatenate([W, pad], axis = 1)
    Wk = W.transpose(0, 2, 3, 1).reshape(oc, 9, cin)
    out = np.zeros((n, oh, ow, oc), dtype = np.float64)
    for k in range(9):
        dy, dx = k // 3, k % 3
        win = a[:, dy:dy + oh, dx:dx + ow, :].reshape(-1, cin)
        out += (win @ Wk[:, k, :].T).reshape(n, oh, ow, oc)
    return out + bias


def q_weight(W, rng):
    """[OC,C,3,3] float + per-OC range -> ([OC,9,CIN] int8, [OC] scale)."""
    w = W.transpose(0, 2, 3, 1).reshape(W.shape[0], 9, W.shape[1])
    q = np.clip(np.round(w * (AB / rng[:, None, None])), -127, 127)
    out = np.zeros((q.shape[0], 9, CIN), dtype = np.int8)
    out[:, :, :W.shape[1]] = q[:, :, :W.shape[1]]
    if not np.all(np.abs(q[:, :, :W.shape[1]] - w * (AB / rng[:, None, None])) <= 0.5):
        raise SystemExit("SETUP-FAIL: weight rounding is not nearest")
    return out, rng / AB


# --------------------------------------------------------------- float net
def build_model():
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    class Net(nn.Module):
        def __init__(s):
            super().__init__()
            s.c1 = nn.Conv2d(NCH_IN, COUT, 3, bias = True)
            s.b1 = nn.BatchNorm2d(COUT)
            s.c2 = nn.Conv2d(COUT, COUT, 3, bias = True)
            s.b2 = nn.BatchNorm2d(COUT)
            s.fc = nn.Linear(COUT, NCLS, bias = True)

        def forward(s, x):
            x = F.relu(s.b1(s.c1(x)))
            x = F.relu(s.b2(s.c2(x)))
            x = F.adaptive_max_pool2d(x, 1)
            return s.fc(x.reshape(x.size(0), -1))

    return Net()


def stage_train(epochs = EPOCHS):
    import torch
    import torch.nn.functional as F
    torch.manual_seed(SEED)
    rng = np.random.default_rng(SEED)
    xtr, ytr, xte, yte = load_cifar()
    idx = rng.choice(xtr.shape[0], TRAIN_N, replace = False)
    Xtr = torch.from_numpy(downsample(xtr[idx]).astype(np.float32))
    Ytr = torch.from_numpy(ytr[idx])
    Xte = torch.from_numpy(downsample(xte).astype(np.float32))
    Yte = torch.from_numpy(yte)
    log("DATA train", tuple(Xtr.shape), "test", tuple(Xte.shape))
    model = build_model()

    def acc_of(m, X, Y, bs = 2000):
        good = 0
        with torch.no_grad():
            for i in range(0, X.shape[0], bs):
                good += int((m(X[i:i + bs]).argmax(1) == Y[i:i + bs]).sum())
        return good / X.shape[0]

    opt = torch.optim.Adam(model.parameters(), lr = LR)
    sch = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max = epochs)
    n = Xtr.shape[0]
    for ep in range(epochs):
        perm = torch.randperm(n)
        tot = 0.0
        for i in range(0, n, BATCH):
            sel = perm[i:i + BATCH]
            opt.zero_grad()
            loss = F.cross_entropy(model(Xtr[sel]), Ytr[sel])
            loss.backward()
            opt.step()
            tot += float(loss) * sel.numel()
        sch.step()
        log("EPOCH %2d loss %.4f tr %.4f te %.4f" % (
            ep + 1, tot / n, acc_of(model, Xtr[:5000], Ytr[:5000]),
            acc_of(model, Xte, Yte)))
    float_te = acc_of(model, Xte, Yte)
    os.makedirs(RESULTS, exist_ok = True)
    torch.save(model.state_dict(), STATE)
    log("FLOAT_TEST_ACC %.6f" % float_te)


# ------------------------------------------------- quantize + evaluate
def make_int8_model(model, Xcal):
    """Fold BN, calibrate in the int8 domain, return the packed layer dict."""
    import torch
    with torch.no_grad():
        def fold(conv, bn):
            w = conv.weight.detach().numpy().astype(np.float64)
            b = conv.bias.detach().numpy().astype(np.float64)
            g = bn.weight.detach().numpy().astype(np.float64)
            be = bn.bias.detach().numpy().astype(np.float64)
            mu = bn.running_mean.detach().numpy().astype(np.float64)
            va = bn.running_var.detach().numpy().astype(np.float64)
            k = g / np.sqrt(va + bn.eps)
            return w * k[:, None, None, None], b * k + be - mu * k
        Wc1, bc1 = fold(model.c1, model.b1)
        Wc2, bc2 = fold(model.c2, model.b2)
    Wf = model.fc.weight.detach().numpy().astype(np.float64)
    bf = model.fc.bias.detach().numpy().astype(np.float64)

    # self-check (a): BN-folded numpy float conv must reproduce the torch model
    with torch.no_grad():
        ref = model(torch.from_numpy(Xcal[:64].astype(np.float32))).numpy()
    chk = dict(W1 = Wc1, b1 = bc1, W2 = Wc2, b2 = bc2, Wf = Wf, bf = bf)
    got = forward_float(Xcal[:64], chk)
    fold_dev = float(np.abs(ref.astype(np.float64) - got).max())
    log("BN_FOLD_MAX_ABS_DEV %.3e" % fold_dev)
    if fold_dev > 5e-6:
        raise SystemExit("SETUP-FAIL: BN folding disagrees with torch "
                         "(max abs dev %.3e)" % fold_dev)

    xp = to_nhwc_padded(Xcal)
    S_in = float(np.abs(xp).max()) / AB
    a0 = np.clip(np.round(xp / S_in), -127, 127).astype(np.int8)
    if np.abs(xp / S_in).max() > 127.5:
        raise SystemExit("SETUP-FAIL: input quantization saturates")

    rng1 = np.abs(Wc1).max(axis = (1, 2, 3))
    rng2 = np.abs(Wc2).max(axis = (1, 2, 3))
    rngf = np.abs(Wf).max(axis = 1)
    W1q, Sw1 = q_weight(Wc1, rng1)
    W2q, Sw2 = q_weight(Wc2, rng2)
    # FC is [NCLS,CIN]: quantized straight as an int8 matrix, per-class scale
    Wfq = np.clip(np.round(Wf * (AB / rngf[:, None])), -127, 127).astype(np.int8)
    Swf = rngf / AB

    # ---- L1: calibrate the output scale from the int8 accumulator itself
    acc1, amax1 = conv_int8_acc(a0, W1q, H1, W1)
    real1 = (acc1.astype(np.float64) * S_in) * Sw1[None, None, None, :]
    S_l1 = float(real1.max()) / AB
    r1 = (S_in * Sw1) / S_l1
    s1, M1, fit1 = pick_layer_shift(r1, "l1")
    B1 = np.round(bc1 / (S_in * Sw1)).astype(np.int64)
    l1 = np.maximum(requant_int8(acc1 + B1, M1, s1, 0), 0).astype(np.int8)

    # ---- L2 (input scale is S_l1: the int8 activations of L1)
    acc2, amax2 = conv_int8_acc(l1, W2q, H2, W2)
    real2 = (acc2.astype(np.float64) * S_l1) * Sw2[None, None, None, :]
    S_l2 = float(real2.max()) / AB
    r2 = (S_l1 * Sw2) / S_l2
    s2, M2, fit2 = pick_layer_shift(r2, "l2")
    B2 = np.round(bc2 / (S_l1 * Sw2)).astype(np.int64)
    l2 = np.maximum(requant_int8(acc2 + B2, M2, s2, 0), 0).astype(np.int8)

    # ---- global max pool over int8: no rescale, so the FC input scale is S_l2
    p = l2.max(axis = (1, 2))
    accf = p.astype(np.float64) @ Wfq.astype(np.float64).T
    accf = accf.astype(np.int64)
    if int(np.abs(accf).max()) > 2**31 - 1:
        raise SystemExit("SETUP-FAIL: FC accumulator exceeds int32")
    realf = (accf.astype(np.float64) * S_l2) * Swf[None, :]
    S_fc = float(np.abs(realf).max()) / AB
    rf = (S_l2 * Swf) / S_fc
    sf, Mf, fitf = pick_layer_shift(rf, "fc")
    Bf = np.round(bf / (S_l2 * Swf)).astype(np.int64)

    P = dict(a0_scale = S_in, S = dict(inp = S_in, l1 = S_l1, l2 = S_l2, fc = S_fc),
             W1q = W1q, W2q = W2q, Wfq = Wfq, B1 = B1, B2 = B2, Bf = Bf,
             M1 = M1, M2 = M2, Mf = Mf,
             shifts = (s1, s2, sf), mults = (M1, M2, Mf),
             Sw = (Sw1, Sw2, Swf), rng = (rng1, rng2, rngf),
             acc = dict(l1 = amax1, l2 = amax2, fc = int(np.abs(accf).max())),
             fit_err = dict(l1 = float(np.abs(fit1 / r1 - 1).max()),
                            l2 = float(np.abs(fit2 / r2 - 1).max()),
                            fc = float(np.abs(fitf / rf - 1).max())),
             float = chk, fold_dev = fold_dev)
    return P


def forward_int8(x, P, want_layers = False):
    """The nn_cifar.c arithmetic, on [N,3,8,8] float pixels -> int8 logits."""
    xp = to_nhwc_padded(x)
    a0 = np.clip(np.round(xp / P["a0_scale"]), -127, 127).astype(np.int8)
    acc1, _ = conv_int8_acc(a0, P["W1q"], H1, W1)
    l1 = np.maximum(requant_int8(acc1 + P["B1"], P["M1"], P["shifts"][0], 0), 0)
    acc2, _ = conv_int8_acc(l1.astype(np.int8), P["W2q"], H2, W2)
    l2 = np.maximum(requant_int8(acc2 + P["B2"], P["M2"], P["shifts"][1], 0), 0)
    p = l2.max(axis = (1, 2)).astype(np.int8)
    accf = (p.astype(np.float64) @ P["Wfq"].astype(np.float64).T).astype(np.int64)
    lg = requant_int8(accf + P["Bf"], P["Mf"], P["shifts"][2], 0)
    if want_layers:
        return lg, dict(a0 = a0, l1 = l1, l2 = l2, pool = p, logits = lg)
    return lg


def forward_float(x, P):
    f = P.get("float", P)
    xp = to_nhwc_padded(x)
    z1 = np.maximum(conv_float(xp, f["W1"], f["b1"], H1, W1), 0.0)
    z2 = np.maximum(conv_float(z1, f["W2"], f["b2"], H2, W2), 0.0)
    return z2.max(axis = (1, 2)) @ f["Wf"].T + f["bf"]


def stage_quant_eval(export = True, full_eval = True):
    t0 = time.time()
    import torch
    if not os.path.isdir(BATCHES):
        raise SystemExit("SETUP-FAIL %s missing (dataset not extracted)" % BATCHES)
    if not os.path.isfile(STATE):
        raise SystemExit("SETUP-FAIL %s missing (run --stage train first)" % STATE)
    model = build_model()
    model.load_state_dict(torch.load(STATE, map_location = "cpu",
                                     weights_only = True))
    model.eval()
    xtr, ytr, xte, yte = load_cifar()
    rng = np.random.default_rng(SEED)
    cidx = rng.choice(xtr.shape[0], CAL_N, replace = False)
    Xcal = downsample(xtr[cidx])
    P = make_int8_model(model, Xcal)

    # self-check (b): the int8 path must be deterministic and saturating rarely
    lg_c, lay = forward_int8(Xcal[:200], P, want_layers = True)
    sat = {k: int(np.count_nonzero(v == -128) + np.count_nonzero(v == 127))
           for k, v in lay.items()}
    log("CALIB_SHIFTS", P["shifts"], "ACC_ABSMAX", P["acc"])
    log("CALIB_FIT_ERR", P["fit_err"])
    log("SAT_COUNTS_200IMG", sat)

    # ---- accuracy over the real test set
    # int8 logits lie in [-128,127], so ties are possible; argmax takes the
    # lowest class index, which can cost accuracy. Tied-image count is logged
    # next to accuracy to size that effect.
    Xte = downsample(xte)
    n = Xte.shape[0]
    good_i = good_f = 0
    tie = tie_correct = 0
    CH = 2000
    for i in range(0, n, CH):
        xb, yb = Xte[i:i + CH], yte[i:i + CH]
        li = forward_int8(xb, P).astype(np.int64)
        lf = forward_float(xb, P)
        ai = li.argmax(axis = 1)
        good_i += int((ai == yb).sum())
        good_f += int((lf.argmax(axis = 1) == yb).sum())
        nmax = (li == li.max(axis = 1)[:, None]).sum(axis = 1)
        hit = np.nonzero(nmax > 1)[0]
        tie += int(hit.size)
        tie_correct += int((ai[hit] == yb[hit]).sum())
    acc_i = good_i / n
    acc_f = good_f / n
    log("INT8_TIE_IMAGES %d of %d (argmax lowest-index rule hit %d)"
        % (tie, n, tie_correct))
    te_float_torch = None
    with torch.no_grad():
        Xt = torch.from_numpy(Xte.astype(np.float32))
        Yt = torch.from_numpy(yte)
        g = 0
        for i in range(0, n, 2000):
            g += int((model(Xt[i:i + 2000]).argmax(1) == Yt[i:i + 2000]).sum())
        te_float_torch = g / n
    log("INT8_TEST_ACC %.6f (%d/%d)" % (acc_i, good_i, n))
    log("FLOAT_NUMPY_ACC %.6f  FLOAT_TORCH_ACC %.6f" % (acc_f, te_float_torch))
    log("QUANT_LOSS_PT %+.3f" % ((acc_i - te_float_torch) * 100))

    # ---- the images the RTL arm will run: first NSIM test images
    Xs = Xte[:NSIM]
    lg_s, lay_s = forward_int8(Xs, P, want_layers = True)
    tar_hash = sha256_file(TARBALL) if os.path.isfile(TARBALL) else None
    arch = dict(
        benchmark = "cifar10_int8: real CIFAR-10, int8 arithmetic of nn_cifar.c",
        dataset = "CIFAR-10 (kriz, cs.toronto.edu), 32x32 RGB, 10 classes",
        dataset_tarball = TARBALL.replace("\\", "/"),
        dataset_tarball_sha256 = tar_hash,
        dataset_source_url = "https://www.cs.toronto.edu/~kriz/cifar-10-python.tar.gz",
        preprocessing = "4x4 area-average 32x32->8x8, /127.5-1, 3ch zero-padded to 16 lanes",
        network = "conv3x3valid 8x8->6x6 x16 | conv3x3valid 6x6->4x4 x16 | "
                  "global maxpool 4x4->1x1 | FC 16->10",
        trained_on = "CIFAR-10 train subset, %d images, %d epochs, Adam lr=%g, seed %d"
                     % (TRAIN_N, EPOCHS, LR, SEED),
        float_state_sha256 = sha256_file(STATE),
        torch_version = torch.__version__,
        numpy_version = np.__version__,
        test_images = int(n),
        top1_int8 = acc_i, top1_int8_hit = good_i,
        top1_float_numpy = acc_f, top1_float_torch = te_float_torch,
        quantization_loss_pt = (acc_i - te_float_torch) * 100.0,
        shifts = list(P["shifts"]),
        scales = P["S"],
        acc_absmax = P["acc"],
        mult_fit_relerr = P["fit_err"],
        saturation_counts_200calib = sat,
        bn_fold_max_abs_dev = P["fold_dev"],
        input_pad_efficiency = "3 of 16 lanes carry pixels: MAC efficiency %.1f%%"
                               % (100.0 * NCH_IN / CIN),
        sim_images = NSIM,
        sim_expected_logit_argmax = [int(v) for v in
                                     lg_s.astype(np.int64).argmax(axis = 1)],
        sim_labels = [int(v) for v in yte[:NSIM]],
        wall_s = round(time.time() - t0, 1),
    )
    if full_eval:
        atomic_json(ARCHIVE, arch)
    if export:
        export_header(P, Xs, yte[:NSIM], lg_s, lay_s)
    np.savez(os.path.join(RESULTS, "cifar_int8_layers.npz"), **{
        k: v for k, v in lay_s.items()})
    return P, arch


def _arr(name, ctype, data, per = 12):
    flat = np.asarray(data).reshape(-1)
    lines = ["static const %s %s[%d] = {" % (ctype, name, int(np.prod(data.shape)))]
    for i in range(0, flat.size, per):
        lines.append("  " + ", ".join(str(int(v)) for v in flat[i:i + per]) + ",")
    lines.append("};")
    return "\n".join(lines)


def _sums(lay, n):
    """Per-image int8 byte sums, the checksum nn_cifar.c recomputes itself.

    Deliberately a sum over the layer bytes: it is small enough to put in a
    header, and it makes the C arm and this python arm agree on EVERY layer,
    not just on the final argmax.
    """
    return {k: np.asarray(v, dtype = np.int32).reshape(n, -1).sum(axis = 1)
            for k, v in lay.items()}


def export_header(P, Xs, labels, lg_s, lay):
    xp = to_nhwc_padded(Xs)
    a0 = np.clip(np.round(xp / P["a0_scale"]), -127, 127).astype(np.int8)
    ns = _sums({k: lay[k] for k in ("l1", "l2", "pool", "logits")}, NSIM)
    amax = lg_s.astype(np.int64).argmax(axis = 1)
    out = ["/* GENERATED by tools/train_cifar_int8.py -- do not edit by hand.",
           " * Real CIFAR-10 weights + the quantized images the RTL arm runs.",
           " * Values are int8 in the SAME arithmetic nn_cifar.c executes.",
           " * The cif_exp_* arrays are this python arm's own answers: the C",
           " * program compares against them and never reads them as input. */",
           "#ifndef CIFAR_INT8_H",
           "#define CIFAR_INT8_H",
           "#include <stdint.h>",
           "#define CIF_H0 %d" % H0, "#define CIF_W0 %d" % W0,
           "#define CIF_H1 %d" % H1, "#define CIF_W1 %d" % W1,
           "#define CIF_H2 %d" % H2, "#define CIF_W2 %d" % W2,
           "#define CIF_CIN %d" % CIN, "#define CIF_COUT %d" % COUT,
           "#define CIF_NCLS %d" % NCLS, "#define CIF_NSIM %d" % NSIM,
           "#define CIF_NPAD %d" % NCH_IN,
           "#define CIF_S1 %d" % P["shifts"][0],
           "#define CIF_S2 %d" % P["shifts"][1],
           "#define CIF_SF %d" % P["shifts"][2],
           "#define CIF_RB 0",
           _arr("cif_w1", "int8_t", P["W1q"].reshape(COUT, 9 * CIN)),
           _arr("cif_w2", "int8_t", P["W2q"].reshape(COUT, 9 * CIN)),
           _arr("cif_wf", "int8_t", P["Wfq"].reshape(NCLS, CIN)),
           _arr("cif_b1", "int32_t", P["B1"]),
           _arr("cif_b2", "int32_t", P["B2"]),
           _arr("cif_bf", "int32_t", P["Bf"]),
           _arr("cif_m1", "int32_t", P["M1"]),
           _arr("cif_m2", "int32_t", P["M2"]),
           _arr("cif_mf", "int32_t", P["Mf"]),
           _arr("cif_img", "int8_t", a0.reshape(NSIM, H0 * W0 * CIN)),
           _arr("cif_lbl", "int8_t", labels),
           _arr("cif_expect_logits", "int8_t", lg_s.reshape(NSIM, NCLS)),
           _arr("cif_exp_l1", "int32_t", ns["l1"]),
           _arr("cif_exp_l2", "int32_t", ns["l2"]),
           _arr("cif_exp_pool", "int32_t", ns["pool"]),
           _arr("cif_exp_lgsum", "int32_t", ns["logits"]),
           _arr("cif_exp_argmax", "int8_t", amax),
           "#endif", ""]
    txt = "\n".join(out)
    tmp = HEADER + ".part"
    with open(tmp, "w", encoding = "utf-8", newline = "\n") as f:
        f.write(txt)
    os.replace(tmp, HEADER)
    log("WROTE", os.path.basename(HEADER), len(txt), "bytes")


# --------------------------------------------------------------- selftest
def stage_selftest():
    """Vectorised vs scalar requant, plus the multiplier fit's two edges."""
    bad = 0
    rng = np.random.default_rng(7)
    for trial in range(6):
        s = [0, 1, 3, 12, 31, 31][trial]
        n = 4000
        acc = rng.integers(-(2**22), 2**22, n)
        m = rng.integers(-(2**31), 2**31, n)
        rb = int(rng.integers(-100, 100))
        v = requant_int8(acc, m, s, rb)
        for i in range(0, n, 37):
            if int(v[i]) != requant_scalar(acc[i], m[i], s, rb):
                bad += 1
                log("SELFTEST-FAIL trial %d idx %d acc %d m %d s %d rb %d "
                    "vec %d scalar %d" % (trial, i, acc[i], m[i], s, rb,
                                          v[i], requant_scalar(acc[i], m[i], s, rb)))
    # saturation edges both ways
    for acc, m, s, want in [(10 ** 9, 2 ** 30, 1, 127), (-10 ** 9, 2 ** 30, 1, -128)]:
        got = int(requant_int8(np.array([acc]), np.array([m]), s, 0)[0])
        if got != want:
            bad += 1
            log("SELFTEST-FAIL saturation acc %d -> %d want %d" % (acc, got, want))
    # multiplier fit: ratios that all share one shift, and one that cannot fit
    s_ok, M_ok, fit = pick_layer_shift(np.array([0.5, 0.25, 0.125]), "selftest-ok")
    if s_ok != 32 - 1 + 1 or not np.all(np.abs(M_ok) <= 2**31 - 1):
        # 0.5 -> need s such that 0.5*2^(31+s) <= 2^31-1 -> s = 0
        if s_ok != 0:
            bad += 1
            log("SELFTEST-FAIL shift pick for 0.5 gave %d (want 0)" % s_ok)
    try:
        pick_layer_shift(np.array([1.5]), "selftest-bad")
        bad += 1
        log("SELFTEST-FAIL ratio 1.5 with no legal shift was accepted")
    except SystemExit as e:
        if "no shift" not in str(e):
            bad += 1
            log("SELFTEST-FAIL wrong rejection reason: %s" % e)
    log("SELFTEST %s (bad=%d)" % ("PASS" if bad == 0 else "FAIL", bad))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default = "all",
                    choices = ["train", "quant", "selftest", "all"])
    ap.add_argument("--epochs", type = int, default = EPOCHS)
    ap.add_argument("--no-export", action = "store_true")
    a = ap.parse_args()
    os.makedirs(RESULTS, exist_ok = True)
    rc = stage_selftest()
    if rc:
        return rc
    if a.stage in ("train", "all"):
        stage_train(epochs = a.epochs)
    if a.stage in ("quant", "all"):
        stage_quant_eval(export = not a.no_export)
    return 0


if __name__ == "__main__":
    sys.exit(main())
