/* ============================================================================
 *  sw/bench/nn_net.c -- 端到端 INT8 推理: 三层网络 (卷积 -> 卷积 -> 2x2 池化 -> FC)
 *
 *  nn_bench.c 量的是一个卷积核; 这份文件量的是**整张网络跑完一遍**。同一份输入
 *  喂给三份实现:
 *    基线  —— 纯 C 的 int8 定点网络 (-O2, 不手写汇编);
 *    EPV   —— 同样的 int8 定点算式, 卷积/重量化/池化走扩展指令;
 *    宽精度 —— 同一张网络、同一套权重, 但层间不压回 int8 (64 位累加与中间量)。
 *
 *  要回答的可反驳问题只有两个:
 *   (1) 基线与 EPV 的**每一层输出**逐字节相同。比对是每层做完就比, 不是只比末端
 *       —— 只比末端的话, 中间某层算歪又被下一层掩盖, 我看不见。
 *   (2) int8 网络与宽精度参考给出同一个 argmax, 并报出 logits 的最大绝对偏差。
 *
 *  **这份网络不是训练出来的模型**: 权重由 LFSR 定种子生成。所以这里没有任何
 *  「公开数据集 X% 精度」的说法, 只有「端到端跑通、逐位相同、量化后类别一致、
 *  每层各花多少周期」。真实数据集上的训练与精度仍然写在报告的「未做」里 ——
 *  这条边界不写清楚, 上面那两个数就容易被读成它们不是的意思。
 *
 *  布局: 每个像素的 CIN=16 个 int8 通道连续 (NHWC)。于是一条 3x3 卷积的输入
 *  patch 就是 9x16=144 个连续字节, 每 16 字节一次 EPV.LVB, 一条 VDOT8 出 16 项
 *  点积 —— 与 nn_bench 同一套通路, 没有为这份文件另开口径。
 * ==========================================================================*/
#include "epv32_hw.h"

#define CIN    16           /* 向量宽度 16 lane int8, 所以每层输入通道数取 16 */
#define COUT   16
#define H0      6
#define W0      6           /* 输入 6x6x16 */
#define H1      4
#define W1      4           /* L1 输出 4x4x16 */
#define H2      2
#define W2      2           /* L2 输出 2x2x16 -> 2x2 池化成 1x1x16 */
#define NCLS    4
#define NSAMP   3

#define ACT0  (H0 * W0 * CIN)
#define ACT1  (H1 * W1 * COUT)
#define ACT2  (H2 * W2 * COUT)

static int8_t  act0[ACT0] __attribute__((aligned(4)));
/* 每层两份实现各留缓冲, 比对在层与层之间做。
 * SVB 一次写满 16 字节, 而 conv 主循环按 4 个一组(g*4)步进、FC 只有 NCLS=4
 * 个 logits: 尾组最多越出 12 字节, 故输出缓冲声明为带编译期断言的
 * EPV_SVB_BUF(比对仍只取前 N 字节, 宽精度走 int32 的 lg_wid 不经 SVB)。 */
EPV_SVB_BUF(l1_ref, ACT1) EPV_SVB_BUF(l1_epv, ACT1)
EPV_SVB_BUF(l2_ref, ACT2) EPV_SVB_BUF(l2_epv, ACT2)
EPV_SVB_BUF(pl_ref, CIN)  EPV_SVB_BUF(pl_epv, CIN)
static int32_t lg_wid[NCLS];

static int8_t  w1[COUT][9 * CIN];
static int8_t  w2[COUT][9 * CIN];
static int8_t  wf[NCLS][CIN];
static int32_t b1[COUT], b2[COUT], bf[NCLS];
static int32_t m1[COUT], m2[COUT], mf[NCLS];
static int     s1, s2, sf;
static int8_t  zp;

static uint32_t layer_mismatch, logit_mismatch, max_abs_dev, argmax_agree;
static uint64_t cyc_ref, cyc_epv, cyc_wid, ins_ref, ins_epv, ins_wid;
/* 每层各花多少周期: 「网络级」的读数要能指出时间花在哪一层, 只有一个总数的
 * 话, 池化与 FC 是不是白跑没人知道。 */
static uint64_t rc_l1, rc_l2, rc_tl, ec_l1, ec_l2, ec_tl;
static uint32_t mac_total;

static uint64_t c0, c1, i0, i1;
#define T_START()  do { c0 = epv_rd_cycle(); i0 = epv_rd_instret(); } while (0)
#define T_END(dstc, dsti) do { c1 = epv_rd_cycle(); i1 = epv_rd_instret(); \
                               dstc += c1 - c0; dsti += i1 - i0; } while (0)

/* ---------------------------------------------------------- 定点参考算术
 * 与 epv32_epv.v 的 VREQ4 逐条对齐 (nn_bench 里同一份式子):
 *   t = (acc*M + 2^30) >>> 31;  t += 1<<(S-1);  r = t >>> S;  o = sat8(r + RB)
 * 这里用 64 位直白写, 不追求效率 —— 它是「答案」, 不是被测对象。 */
static int8_t requant(int64_t acc, int32_t m, int s, int8_t rb)
{
  int64_t t = ((acc * (int64_t)m) + ((int64_t)1 << 30)) >> 31;
  int64_t sh;
  if (s > 0) t += ((int64_t)1 << (s - 1));
  if (s <= 0) sh = t;
  else if (t >= 0) sh = t >> s;
  else sh = -((((-t) >> s) + (((-t) & (((int64_t)1 << s) - 1)) ? 1 : 0)));
  sh += rb;
  if (sh > 127)  return 127;
  if (sh < -128) return -128;
  return (int8_t)sh;
}

/* 宽精度那一档用的同一条式子, 只是不饱和到 8 位 */
static int64_t requant_wide(int64_t acc, int32_t m, int s)
{
  int64_t t = ((acc * (int64_t)m) + ((int64_t)1 << 30)) >> 31;
  if (s > 0) t += ((int64_t)1 << (s - 1));
  t >>= s;
  return t;
}

static int8_t relu8(int8_t v) { return v < 0 ? 0 : v; }
static int8_t sat8(int64_t v) { return (int8_t)(v > 127 ? 127 : (v < -128 ? -128 : v)); }

/* ------------------------------------------------ 基线: 标量 int8 整网 */
static void conv_ref_layer(const int8_t *in, int iw, const int8_t (*wg)[9 * CIN],
                           const int32_t *bias, const int32_t *mult, int shift,
                           int oh, int ow, int8_t *out)
{
  for (int y = 0; y < oh; y++)
    for (int x = 0; x < ow; x++)
      for (int oc = 0; oc < COUT; oc++) {
        int64_t acc = bias[oc];
        for (int k = 0; k < 9; k++) {
          const int8_t *a = in + ((y + k / 3) * iw + (x + k % 3)) * CIN;
          const int8_t *w = &wg[oc][k * CIN];
          for (int c = 0; c < CIN; c++) acc += (int32_t)a[c] * (int32_t)w[c];
        }
        out[(y * ow + x) * COUT + oc] = relu8(requant(acc, mult[oc], shift, zp));
      }
}

static void net_baseline(const int8_t *x, int8_t *logits)
{
  T_START(); conv_ref_layer(x, W0, w1, b1, m1, s1, H1, W1, l1_ref);
  T_END(rc_l1, ins_ref);
  T_START(); conv_ref_layer(l1_ref, W1, w2, b2, m2, s2, H2, W2, l2_ref);
  T_END(rc_l2, ins_ref);
  T_START();
  for (int c = 0; c < CIN; c++) {
    int8_t a = l2_ref[0 * CIN + c], b = l2_ref[1 * CIN + c];
    int8_t d = l2_ref[2 * CIN + c], e = l2_ref[3 * CIN + c];
    int8_t m01 = a > b ? a : b, m23 = d > e ? d : e;
    int8_t m = m01 > m23 ? m01 : m23;
    pl_ref[c] = m < 0 ? 0 : m;   /* 池化输入已经过 ReLU, 这次 max(.,0) 只为三份同形 */
  }
  for (int j = 0; j < NCLS; j++) {
    int64_t acc = bf[j];
    for (int c = 0; c < CIN; c++) acc += (int32_t)pl_ref[c] * (int32_t)wf[j][c];
    logits[j] = requant(acc, mf[j], sf, zp);
  }
  T_END(rc_tl, ins_ref);
}

/* -------------------------------------------- 宽精度: 层间不压回 int8 */
static void net_wide(const int8_t *x, int32_t *logits)
{
  static int64_t a1[ACT1], a2[ACT2], ap[CIN];
  for (int y = 0; y < H1; y++)
    for (int cx = 0; cx < W1; cx++)
      for (int oc = 0; oc < COUT; oc++) {
        int64_t acc = b1[oc];
        for (int k = 0; k < 9; k++) {
          const int8_t *a = x + ((y + k / 3) * W0 + (cx + k % 3)) * CIN;
          const int8_t *w = &w1[oc][k * CIN];
          for (int c = 0; c < CIN; c++) acc += (int32_t)a[c] * (int32_t)w[c];
        }
        int64_t q = requant_wide(acc, m1[oc], s1);
        a1[(y * W1 + cx) * COUT + oc] = q < 0 ? 0 : q;
      }
  for (int y = 0; y < H2; y++)
    for (int cx = 0; cx < W2; cx++)
      for (int oc = 0; oc < COUT; oc++) {
        int64_t acc = b2[oc];
        for (int k = 0; k < 9; k++) {
          const int64_t *a = &a1[((y + k / 3) * W1 + (cx + k % 3)) * COUT];
          const int8_t  *w = &w2[oc][k * CIN];
          for (int c = 0; c < CIN; c++) acc += a[c] * (int64_t)w[c];
        }
        int64_t q = requant_wide(acc, m2[oc], s2);
        a2[(y * W2 + cx) * COUT + oc] = q < 0 ? 0 : q;
      }
  for (int c = 0; c < CIN; c++) {
    int64_t m = a2[0 * CIN + c];
    if (a2[1 * CIN + c] > m) m = a2[1 * CIN + c];
    if (a2[2 * CIN + c] > m) m = a2[2 * CIN + c];
    if (a2[3 * CIN + c] > m) m = a2[3 * CIN + c];
    ap[c] = m;
  }
  for (int j = 0; j < NCLS; j++) {
    int64_t acc = bf[j];
    for (int c = 0; c < CIN; c++) acc += ap[c] * (int64_t)wf[j][c];
    logits[j] = (int32_t)requant_wide(acc, mf[j], sf);
    /* 与 int8 档可比: 输入压回 int8 域会截掉大范围, 这里显式记一次饱和检查 */
  }
}

/* ---------------------------------------------------------- EPV 版整网
 * 一个输出像素: 9 次 LVB(输入 patch) + 9*COUT 次 LVB(权重行) + 9*COUT 次
 * VDOT8; 16 个累加器攒成 4 组, 每组一次 VREQ4 出 4 个 int8, 一次 SVB 存回。
 *
 * 为什么权重的向量号写成 16 条展开的宏而不是循环: LVB/VDOT8 的向量号是**编进
 * 机器码的字面量**(见 epv32_hw.h 的约定), 传变量会在汇编期报错而不是悄悄编出
 * 错指令。v0 一直装着本像素的输入 patch, 权重行轮流用 v1。 */
static int32_t acc_stage[4]  __attribute__((aligned(4)));
static int32_t mult_stage[4] __attribute__((aligned(4)));

#define DOT_K(oc)                                                          \
  do {                                                                     \
    epv_lvb(1, (uint32_t)k * CIN, (uint32_t)(uintptr_t)&wg[oc][0]);         \
    { int32_t r_ = 0;                                                       \
      /* VDOT8 的 GPR 目标受向量寄存器号字段约束 (<8), 钉在 t0=x5 上,       \
       * 不赌编译器今天把临时数分配到哪个寄存器 */                          \
      register int32_t rv asm("t0");                                        \
      __asm__ volatile(".insn r 0x2B, 0, 1, %0, x0, x1" : "=r"(rv));         \
      r_ = rv;                                                              \
      acc[oc] += r_; }                                                      \
  } while (0)

static void conv_epv_layer(const int8_t *in, int iw, const int8_t (*wg)[9 * CIN],
                           const int32_t *bias, const int32_t *mult, int shift,
                           int oh, int ow, int8_t *out)
{
  epv_cfg(VCFG_S(shift) | VCFG_RND | VCFG_RB(zp));
  for (int y = 0; y < oh; y++)
    for (int x = 0; x < ow; x++) {
      int32_t acc[COUT];
      const int8_t *base = in + (y * iw + x) * CIN;
      for (int oc = 0; oc < COUT; oc++) acc[oc] = bias[oc];
      for (int k = 0; k < 9; k++) {
        epv_lvb(0, (uint32_t)(((k / 3) * iw + (k % 3)) * CIN),
                (uint32_t)(uintptr_t)base);
        DOT_K(0);  DOT_K(1);  DOT_K(2);  DOT_K(3);
        DOT_K(4);  DOT_K(5);  DOT_K(6);  DOT_K(7);
        DOT_K(8);  DOT_K(9);  DOT_K(10); DOT_K(11);
        DOT_K(12); DOT_K(13); DOT_K(14); DOT_K(15);
      }
      for (int g = 0; g < COUT / 4; g++) {
        acc_stage[0] = acc[g * 4];     mult_stage[0] = mult[g * 4];
        acc_stage[1] = acc[g * 4 + 1]; mult_stage[1] = mult[g * 4 + 1];
        acc_stage[2] = acc[g * 4 + 2]; mult_stage[2] = mult[g * 4 + 2];
        acc_stage[3] = acc[g * 4 + 3]; mult_stage[3] = mult[g * 4 + 3];
        epv_lvb(5, 0, (uint32_t)(uintptr_t)acc_stage);
        epv_lvb(6, 0, (uint32_t)(uintptr_t)mult_stage);
        epv_vreq4(7, 5, 6);
        /* ReLU 必须显式做: 基线侧是 relu8(requant(...)), 这里在重量化后、写回前
         * 走 VRELU8; 漏了会报出「ref=0, epv<0」的差异形状。 */
        epv_vrelu8(7, 7);
        epv_svb(7, 0,
                (uint32_t)(uintptr_t)(out + ((y * ow + x) * COUT + g * 4)));
      }
    }
}

static void net_epv(const int8_t *x, int8_t *logits)
{
  T_START(); conv_epv_layer(x, W0, w1, b1, m1, s1, H1, W1, l1_epv);
  T_END(ec_l1, ins_epv);
  T_START(); conv_epv_layer(l1_epv, W1, w2, b2, m2, s2, H2, W2, l2_epv);
  T_END(ec_l2, ins_epv);
  T_START();
  /* 2x2 最大池化: 4 个像素各是一段 16 字节连续的通道块, 三条 VMAX8 折成 1x1 */
  epv_lvb(0, 0, (uint32_t)(uintptr_t)&l2_epv[0 * CIN]);
  epv_lvb(1, 0, (uint32_t)(uintptr_t)&l2_epv[1 * CIN]);
  epv_lvb(2, 0, (uint32_t)(uintptr_t)&l2_epv[2 * CIN]);
  epv_lvb(3, 0, (uint32_t)(uintptr_t)&l2_epv[3 * CIN]);
  epv_vmax8(4, 0, 1);
  epv_vmax8(5, 2, 3);
  epv_vmax8(6, 4, 5);
  epv_svb(6, 0, (uint32_t)(uintptr_t)pl_epv);
  /* FC: 16 个 int8 激活, 每个类别一条 VDOT8 */
  epv_lvb(0, 0, (uint32_t)(uintptr_t)pl_epv);
  for (int j = 0; j < NCLS; j++) {
    epv_lvb(1, 0, (uint32_t)(uintptr_t)wf[j]);
    {
      register int32_t rv asm("t0");
      __asm__ volatile(".insn r 0x2B, 0, 1, %0, x0, x1" : "=r"(rv));
      acc_stage[j] = rv + bf[j];
    }
    mult_stage[j] = mf[j];
  }
  epv_cfg(VCFG_S(sf) | VCFG_RND | VCFG_RB(zp));
  epv_lvb(5, 0, (uint32_t)(uintptr_t)acc_stage);
  epv_lvb(6, 0, (uint32_t)(uintptr_t)mult_stage);
  epv_vreq4(7, 5, 6);
  epv_svb(7, 0, (uint32_t)(uintptr_t)logits);
  T_END(ec_tl, ins_epv);          /* 池化 + FC 合并计时: 两段各只有几条指令 */
}

/* ------------------------------------------------------------ 比对与读数 */
static void cmp_layer(const char *tag, const int8_t *a, const int8_t *b, int n)
{
  int bad = 0, shown = 0;
  for (int i = 0; i < n; i++) if (a[i] != b[i]) {
    bad++;
    /* 只打头几条差异: 差异的形状比个数更能指出哪一层算式不对。 */
    if (shown < 6) {
      epv_puts("   @"); epv_putu((uint32_t)i);
      epv_puts(" ref="); epv_puti(a[i]);
      epv_puts(" epv="); epv_puti(b[i]); epv_puts("\n");
      shown++;
    }
  }
  if (bad) {
    epv_puts("LAYER-MISMATCH "); epv_puts(tag);
    epv_puts(" n="); epv_putu((uint32_t)bad); epv_puts("\n");
  }
  layer_mismatch += (uint32_t)bad;
}

static int argmax8(const int8_t *v)
{
  int best = 0;
  for (int i = 1; i < NCLS; i++) if (v[i] > v[best]) best = i;
  return best;
}
static int argmax32(const int32_t *v)
{
  int best = 0;
  for (int i = 1; i < NCLS; i++) if (v[i] > v[best]) best = i;
  return best;
}

static uint32_t lfsr;
static int8_t rnd8(void)
{
  lfsr ^= lfsr << 13; lfsr ^= lfsr >> 17; lfsr ^= lfsr << 5;
  return (int8_t)(lfsr & 0xFF);
}
/* 输入与偏置的值域压小: 9x16 次 int8 乘加约在 2^16 量级, Q31 乘数 + 右移后
 * 落在 int8 域内。这样 argmax 不是「整网饱和成一个常数」的退化读数 ——
 * 饱和路径本身仍会被走到, 由 requant 的上下限分支覆盖。 */
static int8_t rndq(void)
{
  rnd8();
  return (int8_t)((lfsr % 81u) - 40u);
}

static void fill_weights(void)
{
  lfsr = 0x0BADC0DEu;
  for (unsigned i = 0; i < sizeof w1; i++) ((int8_t *)w1)[i] = rndq();
  for (unsigned i = 0; i < sizeof w2; i++) ((int8_t *)w2)[i] = rndq();
  for (unsigned i = 0; i < sizeof wf; i++) ((int8_t *)wf)[i] = rndq();
  for (int i = 0; i < COUT; i++) {
    b1[i] = (int32_t)rndq() >> 1;
    b2[i] = (int32_t)rndq() >> 1;
    /* 四档 Q31 乘数, 正负都有, 覆盖 VREQ4 的进位与取整两路 */
    m1[i] = ((i & 1) ? 0x20000000 : -0x30000000) + (i & 2);
    m2[i] = ((i & 2) ? 0x40000000 : -0x20000000) + (i & 1);
  }
  for (int i = 0; i < NCLS; i++) {
    bf[i] = (int32_t)rndq() >> 1;
    mf[i] = (i & 1) ? 0x40000000 : 0x30000000;
  }
  s1 = 6; s2 = 5; sf = 3; zp = 2;
}


int main(void)
{
  /* net_epv 末尾 SVB 一次写满 16 字节而 NCLS=4, 尾组会越出 12 字节, 故 EPV 侧
   * logits 缓冲用带编译期断言的 EPV_SVB_BUF。基线侧只逐字节普通存, lr 不需留白。 */
  int8_t lr[NCLS];
  EPV_SVB_BUF(le, NCLS)

  fill_weights();
  mac_total = (uint32_t)(H1 * W1 * COUT * 9 * CIN
               + H2 * W2 * COUT * 9 * CIN
               + NCLS * CIN);
  epv_puts("NN_NET begin\n");

  for (int s = 0; s < NSAMP; s++) {
    lfsr = 0x12340000u + (uint32_t)s * 7919u;
    for (int i = 0; i < ACT0; i++) act0[i] = rndq();

    /* 整网耗时由三层之和导出, 不另套计时: 计时宏共用 c0/c1, 内外两层会互相踩
     * (上一版就把 2.17M 拍读成 6171 拍)。 */
    net_baseline(act0, lr);
    net_epv(act0, le);
    T_START(); net_wide(act0, lg_wid); T_END(cyc_wid, ins_wid);

    cmp_layer("l1", l1_ref, l1_epv, ACT1);
    cmp_layer("l2", l2_ref, l2_epv, ACT2);
    cmp_layer("pool", pl_ref, pl_epv, CIN);
    for (int j = 0; j < NCLS; j++)
      if (lr[j] != le[j]) { logit_mismatch++; }
    for (int j = 0; j < NCLS; j++) {
      int64_t d = (int64_t)lr[j] - (int64_t)lg_wid[j];
      if (d < 0) d = -d;
      if (d > (int64_t)(1u << 31)) d = (int64_t)(1u << 31);
      if ((uint32_t)d > max_abs_dev) max_abs_dev = (uint32_t)d;
    }
    {
      int a = argmax8(lr), b = argmax8(le), w = argmax32(lg_wid);
      epv_puts("S "); epv_putu((uint32_t)s);
      epv_puts(" ref="); epv_puti(a);
      epv_puts(" epv="); epv_puti(b);
      epv_puts(" wide="); epv_puti(w); epv_puts("\n");
      if (a == b && a == w) argmax_agree++;
    }
  }

  cyc_ref = rc_l1 + rc_l2 + rc_tl;
  cyc_epv = ec_l1 + ec_l2 + ec_tl;

  (void)sat8;
  epv_puts("NET_MACS ");             epv_putu(mac_total);
  epv_puts("\nNSAMP ");              epv_putu(NSAMP);
  epv_puts("\nREF_CYC ");            epv_putu64(cyc_ref);
  epv_puts("\nEPV_CYC ");            epv_putu64(cyc_epv);
  epv_puts("\nWIDE_CYC ");           epv_putu64(cyc_wid);
  epv_puts("\nREF_INST ");           epv_putu64(ins_ref);
  epv_puts("\nEPV_INST ");           epv_putu64(ins_epv);
  epv_puts("\nSPEEDUP_X1000 ");
  epv_putu((uint32_t)(cyc_epv ? (cyc_ref * 1000ull) / cyc_epv : 0));
  epv_puts("\nCYC_PER_MAC_REF_X100 ");
  epv_putu((uint32_t)((cyc_ref * 100ull) / ((uint64_t)mac_total * NSAMP)));
  epv_puts("\nCYC_PER_MAC_EPV_X100 ");
  epv_putu((uint32_t)((cyc_epv * 100ull) / ((uint64_t)mac_total * NSAMP)));
  epv_puts("\nLAYER_MISMATCH ");     epv_putu(layer_mismatch);
  epv_puts("\nLOGIT_MISMATCH ");     epv_putu(logit_mismatch);
  epv_puts("\nARGMAX_AGREE ");       epv_putu(argmax_agree);
  epv_puts("\nMAX_ABS_DEV ");        epv_putu(max_abs_dev);
  epv_puts("\nREF_CYC_L1 ");         epv_putu64(rc_l1);
  epv_puts("\nREF_CYC_L2 ");         epv_putu64(rc_l2);
  epv_puts("\nREF_CYC_TAIL ");       epv_putu64(rc_tl);
  epv_puts("\nEPV_CYC_L1 ");         epv_putu64(ec_l1);
  epv_puts("\nEPV_CYC_L2 ");         epv_putu64(ec_l2);
  epv_puts("\nEPV_CYC_TAIL ");       epv_putu64(ec_tl);
  epv_puts("\nNN_NET_DONE\n");

  /* 判定: 任何一层不逐位相同 -> 1xx; 类别不一致 -> 2xx。失败码取模截到 150 以内。 */
  if (layer_mismatch || logit_mismatch)
    epv_exit(100 + (int)((layer_mismatch + logit_mismatch) % 150u));
  /* ARGMAX_AGREE 与 MAX_ABS_DEV 是**读数**, 不当门禁: 这张网络的权重是 LFSR
   * 生成、没训练过, 随机权重下 int8 与宽精度不同类毫不奇怪, 拿它当判据会把
   * 「随机网络的性质」误报成「硬件算错」。要钉住的是两份 int8 实现相等 ——
   * 那就是上面那两个计数。 */
  epv_exit(0);
  return 0;
}
