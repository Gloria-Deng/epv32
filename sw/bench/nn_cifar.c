/* ============================================================================
 *  sw/bench/nn_cifar.c -- 真实数据集上的端到端 INT8 推理 (CIFAR-10)
 *
 *  与 nn_net.c 的分工要说清楚, 因为两者的读数不能互相顶替:
 *    nn_net.c  —— 权重由 LFSR 生成、**没有训练过**, 它证的是「整张网络的算术
 *                 在两条实现上逐位相同 + 每层各花多少周期」, 给不出精度;
 *    这一份    —— 权重是 CIFAR-10 上训练出来的、BN 折叠后按逐输出通道量化成
 *                 int8 的真权重 (由 tools/train_cifar_int8.py 从训练好的模型
 *                 导出到 cifar_int8.h), 输入是 CIFAR-10 **测试集**的真实图像。
 *                 所以这里可以给 top-1。
 *
 *  三条独立的核对, 每条都能单独把这一份判红:
 *   (1) 标量 C 基线 vs EPV: 每张图的 l1 / l2 / 池化 / logits **逐层逐字节**相同;
 *   (2) 标量 C 基线 vs python 侧 (numpy 实现): 每层字节和 + logits 逐字节相同
 *       —— python 侧与 RV32 侧是两套独立实现, 期望值 cif_exp_* 由 python 算,
 *       C 侧只拿来比对, 绝不当输入;
 *   (3) 类别: 报告 ref/EPV 两个 top-1 命中数与它们与 python 的 argmax 一致数。
 *
 *  网络形状与 python 侧一致, 也是 nn_cifar.c 能跑的形状:
 *    conv 3x3 valid  8x8x16 -> 6x6x16   requant(Q31 逐通道) + ReLU
 *    conv 3x3 valid  6x6x16 -> 4x4x16   requant + ReLU
 *    全局最大池化 4x4 -> 1x1            (16 次 LVB + 树形 VMAX8, 不重新量化)
 *    FC 16 -> 10                        requant, 无 ReLU
 *  CIFAR 的 3 个通道零填充到 16 lane, 所以第一层每条 VDOT8 只有 3/16 的 lane
 *  携带像素 —— 这个浪费按 MACS_EXEC / MACS_INFO 两个数一起报, 不折进加速比。
 *
 *  权重与图像的唯一定义在 cifar_int8.h (生成件, 别手改)。
 * ==========================================================================*/
#include "epv32_hw.h"
#include "cifar_int8.h"

#define KPIX   (9 * CIF_CIN)
#define ACT0_N (CIF_H0 * CIF_W0 * CIF_CIN)
#define L1_N   (CIF_H1 * CIF_W1 * CIF_COUT)
#define L2_N   (CIF_H2 * CIF_W2 * CIF_COUT)

static int8_t act0[ACT0_N] __attribute__((aligned(4)));
/* SVB 一次写满 16 字节, 而 conv/FC 按 4 个一组往后写, 末组会越出逻辑长度
 * 最多 12 字节, 踩坏紧随其后的计时静态量。故每个输出缓冲按 16 字节向上留白,
 * 两臂一起留(比对只取前 N 字节), 留白量由 EPV_SVB_BUF 的 _Static_assert 编译期钉住。 */
EPV_SVB_BUF(l1r, L1_N) EPV_SVB_BUF(l1e, L1_N)
EPV_SVB_BUF(l2r, L2_N) EPV_SVB_BUF(l2e, L2_N)
EPV_SVB_BUF(pfr, CIF_CIN) EPV_SVB_BUF(pfe, CIF_CIN)
/* EPV 按 4 个一组写 */
EPV_SVB_BUF(lgr, CIF_CIN) EPV_SVB_BUF(lge, CIF_CIN)

static int32_t acc_stage[CIF_CIN] __attribute__((aligned(4)));
static int32_t mult_stage[CIF_CIN] __attribute__((aligned(4)));

static uint32_t layer_mismatch, py_layer_mismatch, py_logit_mismatch;
static uint32_t argmax_ref_py, argmax_ref_epv, top1_ref, top1_epv;
static uint64_t cyc_ref, cyc_epv, ins_ref, ins_epv, mac_exec, mac_info;
static uint32_t shown;

static uint64_t c0, c1, i0, i1;
#define T_START()  do { c0 = epv_rd_cycle(); i0 = epv_rd_instret(); } while (0)
#define T_END(dc, di) do { c1 = epv_rd_cycle(); i1 = epv_rd_instret(); \
                           dc += c1 - c0; di += i1 - i0; } while (0)

/* 与 nn_net.c / 导出脚本同一条定点式子:
 *   t = (acc*M + 2^30) >>> 31; t += 1<<(S-1); t >>>= S (正向截断、负向远离零);
 *   o = sat8(t + RB) */
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

static int8_t relu8(int8_t v) { return v < 0 ? 0 : v; }

/* -nostdlib 下无 libc: 图像拷贝循环会被 GCC 收成 memset/memcpy 调用, 链接期报
 * undefined reference。故补最小自有实现, 不动 sw/lib/platform.c(它在已认证运行的
 * build_inputs 里, 动它会让别的归档读成过期)。 */
void *memset(void *d, int c, unsigned long n)
{
  unsigned char *p = (unsigned char *)d;
  while (n--) *p++ = (unsigned char)c;
  return d;
}

void *memcpy(void *d, const void *s, unsigned long n)
{
  unsigned char *pd = (unsigned char *)d;
  const unsigned char *ps = (const unsigned char *)s;
  while (n--) *pd++ = *ps++;
  return d;
}

/* ------------------------------------------------------- 标量 C 基线 */
static void conv_ref(const int8_t *in, int iw, int oh, int ow,
                     const int8_t *wg, const int32_t *bias,
                     const int32_t *mult, int shift, int8_t *out)
{
  for (int y = 0; y < oh; y++)
    for (int x = 0; x < ow; x++)
      for (int oc = 0; oc < CIF_COUT; oc++) {
        int64_t acc = bias[oc];
        for (int k = 0; k < 9; k++) {
          const int8_t *a = in + ((y + k / 3) * iw + (x + k % 3)) * CIF_CIN;
          const int8_t *w = wg + (long)oc * KPIX + k * CIF_CIN;
          for (int c = 0; c < CIF_CIN; c++) acc += (int32_t)a[c] * (int32_t)w[c];
        }
        out[(y * ow + x) * CIF_COUT + oc] =
            relu8(requant(acc, mult[oc], shift, CIF_RB));
      }
}

static void pool_ref(const int8_t *l2, int8_t *out)
{
  for (int c = 0; c < CIF_CIN; c++) {
    int8_t m = l2[c];
    for (int p = 1; p < 16; p++) if (l2[p * CIF_CIN + c] > m) m = l2[p * CIF_CIN + c];
    out[c] = m;                       /* 输入已 ReLU, 池化不再重新量化 */
  }
}

static void fc_ref(const int8_t *p, int8_t *logits)
{
  for (int j = 0; j < CIF_NCLS; j++) {
    int64_t acc = cif_bf[j];
    for (int c = 0; c < CIF_CIN; c++)
      acc += (int32_t)p[c] * (int32_t)cif_wf[j * CIF_CIN + c];
    logits[j] = requant(acc, cif_mf[j], CIF_SF, CIF_RB);
  }
}

static void net_ref(void)
{
  T_START();
  conv_ref(act0, CIF_W0, CIF_H1, CIF_W1, cif_w1, cif_b1, cif_m1, CIF_S1, l1r);
  T_END(cyc_ref, ins_ref);
  T_START();
  conv_ref(l1r, CIF_W1, CIF_H2, CIF_W2, cif_w2, cif_b2, cif_m2, CIF_S2, l2r);
  T_END(cyc_ref, ins_ref);
  T_START();
  pool_ref(l2r, pfr);
  fc_ref(pfr, lgr);
  T_END(cyc_ref, ins_ref);
}

/* ------------------------------------------------------------- EPV 版 */
/* 一个输出像素: 9 次 LVB 装 patch 到 v0, 每条权重行一次 LVB 到 v1,
 * 一次 VDOT8 出该 (像素,输出通道) 的 16 项点积。向量号是编码进指令的字面量,
 * 所以 16 个输出通道手写展开而不是循环 (见 epv32_hw.h 的约定)。 */
#define DOT_K(oc)                                                        \
  do {                                                                   \
    epv_lvb(1, (uint32_t)(k * CIF_CIN),                                  \
            (uint32_t)(uintptr_t)(wg + (long)(oc) * KPIX));               \
    register int32_t rv asm("t0");                                       \
    __asm__ volatile(".insn r 0x2B, 0, 1, %0, x0, x1" : "=r"(rv));         \
    acc[oc] += rv;                                                       \
  } while (0)

static void conv_epv(const int8_t *in, int iw, int oh, int ow,
                     const int8_t *wg, const int32_t *bias,
                     const int32_t *mult, int shift, int8_t *out)
{
  epv_cfg(VCFG_S(shift) | VCFG_RND | VCFG_RB(CIF_RB));
  for (int y = 0; y < oh; y++)
    for (int x = 0; x < ow; x++) {
      int32_t acc[CIF_COUT];
      const int8_t *base = in + (y * iw + x) * CIF_CIN;
      for (int oc = 0; oc < CIF_COUT; oc++) acc[oc] = bias[oc];
      for (int k = 0; k < 9; k++) {
        epv_lvb(0, (uint32_t)(((k / 3) * iw + (k % 3)) * CIF_CIN),
                (uint32_t)(uintptr_t)base);
        DOT_K(0);  DOT_K(1);  DOT_K(2);  DOT_K(3);
        DOT_K(4);  DOT_K(5);  DOT_K(6);  DOT_K(7);
        DOT_K(8);  DOT_K(9);  DOT_K(10); DOT_K(11);
        DOT_K(12); DOT_K(13); DOT_K(14); DOT_K(15);
      }
      for (int g = 0; g < CIF_COUT / 4; g++) {
        acc_stage[0] = acc[g * 4];     mult_stage[0] = mult[g * 4];
        acc_stage[1] = acc[g * 4 + 1]; mult_stage[1] = mult[g * 4 + 1];
        acc_stage[2] = acc[g * 4 + 2]; mult_stage[2] = mult[g * 4 + 2];
        acc_stage[3] = acc[g * 4 + 3]; mult_stage[3] = mult[g * 4 + 3];
        epv_lvb(5, 0, (uint32_t)(uintptr_t)acc_stage);
        epv_lvb(6, 0, (uint32_t)(uintptr_t)mult_stage);
        epv_vreq4(7, 5, 6);
        epv_vrelu8(7, 7);           /* 与基线同侧: 重量化之后、写回之前 */
        epv_svb(7, 0, (uint32_t)(uintptr_t)(out + ((y * ow + x) * CIF_COUT
                                                   + g * 4)));
      }
    }
}

/* 全局 4x4 -> 1x1 最大池化: 16 个像素各是一段 16 字节连续的通道块,
 * 每 4 个像素一组折成 v6, 再并进 v7。 */
#define PBLK(p0, p1, p2, p3, FIRST)                                       \
  do {                                                                    \
    epv_lvb(0, (uint32_t)((p0) * CIF_CIN), (uint32_t)(uintptr_t)base);     \
    epv_lvb(1, (uint32_t)((p1) * CIF_CIN), (uint32_t)(uintptr_t)base);     \
    epv_lvb(2, (uint32_t)((p2) * CIF_CIN), (uint32_t)(uintptr_t)base);     \
    epv_lvb(3, (uint32_t)((p3) * CIF_CIN), (uint32_t)(uintptr_t)base);     \
    epv_vmax8(4, 0, 1); epv_vmax8(5, 2, 3); epv_vmax8(6, 4, 5);            \
    if (FIRST) epv_vmax8(7, 6, 6); else epv_vmax8(7, 7, 6);                \
  } while (0)

static void pool_epv(const int8_t *l2, int8_t *out)
{
  const int8_t *base = l2;
  PBLK(0, 1, 2, 3, 1);
  PBLK(4, 5, 6, 7, 0);
  PBLK(8, 9, 10, 11, 0);
  PBLK(12, 13, 14, 15, 0);
  epv_svb(7, 0, (uint32_t)(uintptr_t)out);
}

static void fc_epv(const int8_t *p, int8_t *logits)
{
  epv_lvb(0, 0, (uint32_t)(uintptr_t)p);
  epv_cfg(VCFG_S(CIF_SF) | VCFG_RND | VCFG_RB(CIF_RB));
  for (int j = 0; j < CIF_NCLS; j++) {
    register int32_t rv asm("t0");
    epv_lvb(1, 0, (uint32_t)(uintptr_t)(cif_wf + j * CIF_CIN));
    __asm__ volatile(".insn r 0x2B, 0, 1, %0, x0, x1" : "=r"(rv));
    acc_stage[j] = rv + cif_bf[j];
    mult_stage[j] = cif_mf[j];
  }
  /* 10 个类别按 4 个一组过 VREQ4: 最后一组有 2 条是填充 lane, 落进 lge 的
   * 空闲字节里, 比对只取前 CIF_NCLS 个。 */
  for (int g = 0; g < (CIF_NCLS + 3) / 4; g++) {
    epv_lvb(5, (uint32_t)(g * 16), (uint32_t)(uintptr_t)acc_stage);
    epv_lvb(6, (uint32_t)(g * 16), (uint32_t)(uintptr_t)mult_stage);
    epv_vreq4(7, 5, 6);
    epv_svb(7, 0, (uint32_t)(uintptr_t)(logits + g * 4));
  }
}

static void net_epv(void)
{
  T_START();
  conv_epv(act0, CIF_W0, CIF_H1, CIF_W1, cif_w1, cif_b1, cif_m1, CIF_S1, l1e);
  T_END(cyc_epv, ins_epv);
  T_START();
  conv_epv(l1e, CIF_W1, CIF_H2, CIF_W2, cif_w2, cif_b2, cif_m2, CIF_S2, l2e);
  T_END(cyc_epv, ins_epv);
  T_START();
  pool_epv(l2e, pfe);
  fc_epv(pfe, lge);
  T_END(cyc_epv, ins_epv);
}

/* ------------------------------------------------------- 比对与读数 */
static int32_t bytesum(const int8_t *v, int n)
{
  int32_t s = 0;
  for (int i = 0; i < n; i++) s += (int32_t)v[i];
  return s;
}

static void cmp_layer(const char *tag, const int8_t *a, const int8_t *b, int n)
{
  int bad = 0;
  for (int i = 0; i < n; i++) if (a[i] != b[i]) bad++;
  if (bad && shown < 8) {
    epv_puts("LMISMATCH "); epv_puts(tag);
    epv_puts(" n="); epv_putu((uint32_t)bad); epv_puts("\n");
    shown++;
  }
  layer_mismatch += (uint32_t)bad;
}

static void cmp_py(const char *tag, int32_t got, int32_t want, int *shownp)
{
  if (got != want) {
    py_layer_mismatch++;
    if (*shownp < 8) {
      epv_puts("PYMISMATCH "); epv_puts(tag);
      epv_puts(" got="); epv_puti(got);
      epv_puts(" want="); epv_puti(want); epv_puts("\n");
      (*shownp)++;
    }
  }
}

static int argmax8(const int8_t *v, int n)
{
  int best = 0;
  for (int i = 1; i < n; i++) if (v[i] > v[best]) best = i;
  return best;
}

int main(void)
{
  int pshown = 0;

  epv_puts("CIF_NET begin\n");
  mac_exec = (uint64_t)CIF_NSIM
           * ((uint64_t)L1_N * KPIX + (uint64_t)L2_N * KPIX
              + (uint64_t)CIF_NCLS * CIF_CIN);
  mac_info = (uint64_t)CIF_NSIM
           * ((uint64_t)L1_N * 9 * CIF_NPAD + (uint64_t)L2_N * KPIX
              + (uint64_t)CIF_NCLS * CIF_CIN);

  for (int s = 0; s < CIF_NSIM; s++) {
    for (int i = 0; i < ACT0_N; i++)
      act0[i] = cif_img[(long)s * ACT0_N + i];

    net_ref();
    net_epv();

    cmp_layer("l1", l1r, l1e, L1_N);
    cmp_layer("l2", l2r, l2e, L2_N);
    cmp_layer("pool", pfr, pfe, CIF_CIN);
    for (int j = 0; j < CIF_NCLS; j++)
      if (lgr[j] != lge[j]) layer_mismatch++;

    /* 与 python 侧独立实现核对: 每层字节和 + logits 和 */
    cmp_py("l1", bytesum(l1r, L1_N), cif_exp_l1[s], &pshown);
    cmp_py("l2", bytesum(l2r, L2_N), cif_exp_l2[s], &pshown);
    cmp_py("pool", bytesum(pfr, CIF_CIN), cif_exp_pool[s], &pshown);
    cmp_py("logitsum", bytesum(lgr, CIF_NCLS), cif_exp_lgsum[s], &pshown);
    for (int j = 0; j < CIF_NCLS; j++)
      if (lgr[j] != cif_expect_logits[s * CIF_NCLS + j]) py_logit_mismatch++;

    {
      int ar = argmax8(lgr, CIF_NCLS), ae = argmax8(lge, CIF_NCLS);
      int ap = cif_exp_argmax[s];
      if (ar == ap) argmax_ref_py++;
      if (ar == ae) argmax_ref_epv++;
      if (ar == (int)cif_lbl[s]) top1_ref++;
      if (ae == (int)cif_lbl[s]) top1_epv++;
    }
  }

  epv_puts("NSAMP ");            epv_putu(CIF_NSIM);
  epv_puts("\nMACS_EXEC ");      epv_putu64(mac_exec);
  epv_puts("\nMACS_INFO ");      epv_putu64(mac_info);
  epv_puts("\nREF_CYC ");        epv_putu64(cyc_ref);
  epv_puts("\nEPV_CYC ");        epv_putu64(cyc_epv);
  epv_puts("\nREF_INST ");       epv_putu64(ins_ref);
  epv_puts("\nEPV_INST ");       epv_putu64(ins_epv);
  epv_puts("\nSPEEDUP_X1000 ");
  epv_putu((uint32_t)(cyc_epv ? (cyc_ref * 1000ull) / cyc_epv : 0));
  epv_puts("\nCYC_PER_MAC_EXEC_REF_X100 ");
  epv_putu((uint32_t)((cyc_ref * 100ull) / mac_exec));
  epv_puts("\nCYC_PER_MAC_EXEC_EPV_X100 ");
  epv_putu((uint32_t)((cyc_epv * 100ull) / mac_exec));
  epv_puts("\nTOP1_REF ");       epv_putu(top1_ref);
  epv_puts("\nTOP1_EPV ");       epv_putu(top1_epv);
  epv_puts("\nARGMAX_REF_VS_PY "); epv_putu(argmax_ref_py);
  epv_puts("\nARGMAX_REF_VS_EPV "); epv_putu(argmax_ref_epv);
  epv_puts("\nLAYER_MISMATCH "); epv_putu(layer_mismatch);
  epv_puts("\nPY_LAYER_MISMATCH "); epv_putu(py_layer_mismatch);
  epv_puts("\nPY_LOGIT_MISMATCH "); epv_putu(py_logit_mismatch);
  epv_puts("\nCIF_NET_DONE\n");

  /* 判定: 三条实现之间任何一处不逐位相同 -> 非零。top-1 是多少**不**进判据:
   * 精度是网络的属性, 不是硬件对错的属性, 拿它当门禁会把「网络没学好」报成
   * 「核算错了」。 */
  if (layer_mismatch || py_layer_mismatch || py_logit_mismatch)
    epv_exit(100 + (int)((layer_mismatch + py_layer_mismatch
                          + py_logit_mismatch) % 150u));
  epv_exit(0);
  return 0;
}
