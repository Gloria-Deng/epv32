/* ============================================================================
 *  sw/bench/nn_bench.c  --  INT8 卷积累加内核: 纯 C 基线 vs EPV 扩展
 *
 *  回答一个可反驳的问题: EPV 让同一件计算少花多少周期, 且结果逐位一致。
 *  三条纪律:
 *    1) 两份实现吃同一份输入与权重, 输出逐 int8 比对 —— 加速比只有在算同一件事时
 *       才有意义。
 *    2) 计时只包 mcycle/minstret, 打印在计时区外(UART 一字符在仿真下也要 10 拍)。
 *    3) 基线不手写汇编、不钦点编译选项, 就用 -O2 普通 C 循环 —— 对照编译器给该
 *       ISA 的最好标量代码, 而非被写弱的稻草人。
 *
 *  数据布局: NHWC。3x3x16 -> 4, 一个输出像素的 144 个输入连续, 切成 9 个
 *  16 通道块, 每块一次 EPV.LVB。
 * ==========================================================================*/
#include "epv32_hw.h"

#define CIN      16
#define COUT      4
#define KSIZE     9          /* 3x3 */
#define PIX      64          /* 输出像素个数 */

/* 一个输出像素的输入 patch = KSIZE*CIN = 144 个 int8 */
static int8_t  act[PIX * KSIZE * CIN];
static int8_t  wgt[COUT][KSIZE * CIN];
static int32_t bias[COUT];
static int32_t mult[COUT];
static int     shift;
static int8_t  zero_point;

/* SVB 一次写满 16 字节, 这里每组只有 4 个 int8(步进 4 字节), 最后一组会越出
 * 逻辑长度最多 12 字节(台账第 22 条: 曾踩坏紧随其后的计时静态量)。故两臂一起
 * 留白, 由 EPV_SVB_BUF 里的 _Static_assert 在编译期钉住, 不靠人工约定。 */
EPV_SVB_BUF(out_ref, PIX * COUT)
EPV_SVB_BUF(out_epv, PIX * COUT)

static uint32_t mismatches;

/* --------------------------------------------------------------- 参考算术
 * 与 epv32_epv.v 的 VREQ4 逐条对齐: t = (acc*M + 2^30) >>> 31;
 * r = (t + (RND ? 1<<(S-1) : 0)) >>> S; o = sat8(r + RB)。
 * 这里用 int64 直白写, 不追求效率 —— 它是「答案」, 不是被测对象。 */
static int8_t requant_ref(int32_t acc, int32_t m, int s, int rnd, int8_t rb)
{
  int64_t p   = (int64_t)acc * (int64_t)m;
  int64_t hi  = (p + ((int64_t)1 << 30)) >> 31;
  int64_t t   = hi;
  if (rnd && s > 0) t += ((int64_t)1 << (s - 1));
  /* 算术右移 = 向 -inf 取整 */
  int64_t sh;
  if (s <= 0) sh = t;
  else if (t >= 0) sh = t >> s;
  else sh = -((((-t) >> s) + (((-t) & (((int64_t)1 << s) - 1)) ? 1 : 0)));
  sh += rb;
  if (sh > 127)  return 127;
  if (sh < -128) return -128;
  return (int8_t)sh;
}

/* --------------------------------------------------------------- 基线 */
static void conv_ref(void)
{
  for (int p = 0; p < PIX; p++) {
    const int8_t *a = &act[p * KSIZE * CIN];
    for (int oc = 0; oc < COUT; oc++) {
      int32_t acc = bias[oc];
      const int8_t *w = &wgt[oc][0];
      for (int k = 0; k < KSIZE; k++) {
        for (int ic = 0; ic < CIN; ic++)
          acc += (int32_t)a[k*CIN + ic] * (int32_t)w[k*CIN + ic];
      }
      out_ref[p*COUT + oc] = requant_ref(acc, mult[oc], shift, 1, zero_point);
    }
  }
}

/* --------------------------------------------------------------- EPV 版
 * 每 16 通道块: 1 次 LVB(激活) + 每输出通道 1 次 LVB(权重) + 1 次 VDOT8。
 * 4 个输出通道的累加器是普通 int32 标量, 由编译器分配。
 * 攒够 4 个 acc 后一次性交给 VREQ4: 先写回内存, 用 LVB 装成 4 x int32,
 * 乘数同样从内存 LVB 一份, 一条 VREQ4 出 4 个 int8。 */
static int32_t acc_stage[4] __attribute__((aligned(4)));
static int32_t mult_stage[4] __attribute__((aligned(4)));

static void conv_epv(void)
{
  for (int p = 0; p < PIX; p++) {
    const int8_t *a = &act[p * KSIZE * CIN];
    int32_t acc0 = bias[0], acc1 = bias[1], acc2 = bias[2], acc3 = bias[3];
    for (int k = 0; k < KSIZE; k++) {
      int32_t r;
      /* 一个输出像素的 3x3x16 输入在 NHWC 下连续, 第 k 块就是 a + k*16 */
      epv_lvb(0, (uint32_t)k * CIN, (uint32_t)(uintptr_t)a);
      epv_lvb(1, 0, (uint32_t)(uintptr_t)&wgt[0][0] + (uint32_t)k * CIN);
      epv_vdot8(r, 0, 1); acc0 += r;
      epv_lvb(2, 0, (uint32_t)(uintptr_t)&wgt[1][0] + (uint32_t)k * CIN);
      epv_vdot8(r, 0, 2); acc1 += r;
      epv_lvb(3, 0, (uint32_t)(uintptr_t)&wgt[2][0] + (uint32_t)k * CIN);
      epv_vdot8(r, 0, 3); acc2 += r;
      epv_lvb(4, 0, (uint32_t)(uintptr_t)&wgt[3][0] + (uint32_t)k * CIN);
      epv_vdot8(r, 0, 4); acc3 += r;
    }
    acc_stage[0] = acc0; acc_stage[1] = acc1;
    acc_stage[2] = acc2; acc_stage[3] = acc3;
    mult_stage[0] = mult[0]; mult_stage[1] = mult[1];
    mult_stage[2] = mult[2]; mult_stage[3] = mult[3];
    epv_lvb(5, 0, (uint32_t)(uintptr_t)acc_stage);
    epv_lvb(6, 0, (uint32_t)(uintptr_t)mult_stage);
    epv_vreq4(7, 5, 6);
    {
      uint32_t wd = (uint32_t)(uintptr_t)&out_epv[p * COUT];
      epv_svb(7, 0, wd);           /* 低 32bit = 4 个 int8 结果, 正好 4 字节 */
    }
  }
}

/* --------------------------------------------------------------- 造数据 */
static uint32_t lfsr;
static int8_t rnd8(void)
{
  lfsr ^= lfsr << 13; lfsr ^= lfsr >> 17; lfsr ^= lfsr << 5;
  return (int8_t)(lfsr & 0xFF);
}

static void fill(void)
{
  lfsr = 0x12345678u;
  for (unsigned i = 0; i < sizeof act; i++) act[i] = rnd8();
  for (unsigned i = 0; i < sizeof wgt; i++) ((int8_t *)wgt)[i] = rnd8();
  for (int oc = 0; oc < COUT; oc++) {
    bias[oc] = (int32_t)(rnd8() & 0x7F) - 64;
    /* Q31 乘数: 取 0.25/0.5/0.75/-0.6 四档, 覆盖正负与进位路径 */
    static const int32_t mv[4] = { 0x40000000, 0x20000000, -0x30000000, -0x20000000 };
    mult[oc] = mv[oc];
  }
  shift = 6;
  zero_point = 3;
}

static int cmp_out(void)
{
  int bad = 0;
  for (int i = 0; i < PIX * COUT; i++)
    if (out_ref[i] != out_epv[i]) { bad++; if (mismatches == 0) {
        epv_puts("MISMATCH idx="); epv_putu((uint32_t)i);
        epv_puts(" ref="); epv_puti(out_ref[i]);
        epv_puts(" epv="); epv_puti(out_epv[i]); epv_puts("\n"); } }
  mismatches += (uint32_t)bad;
  return bad;
}

static uint64_t c0, i0, c1, i1, c2, i2;

int main(void)
{
  fill();
  /* vcfg: S=shift, 开舍入, RB=zero_point */
  epv_cfg(VCFG_S(shift) | VCFG_RND | VCFG_RB(zero_point));
  { uint32_t back = 0; epv_cfgr(back);
    if (back != (VCFG_S(shift) | VCFG_RND | VCFG_RB(zero_point))) {
      epv_puts("CFG RO FAIL\n"); epv_exit(200); } }

  /* --- 先各跑一遍热身 (排除首次取指/分支统计偏差), 再正式计时 --- */
  conv_ref(); conv_epv();

  c0 = epv_rd_cycle(); i0 = epv_rd_instret();
  conv_ref();
  c1 = epv_rd_cycle(); i1 = epv_rd_instret();
  conv_epv();
  c2 = epv_rd_cycle(); i2 = epv_rd_instret();

  {
    uint64_t ref_cyc = c1 - c0, epv_cyc = c2 - c1;
    uint64_t ref_in  = i1 - i0,  epv_in  = i2 - i1;
    int bad = cmp_out();
    epv_puts("MACS ");  epv_putu((uint32_t)(PIX * COUT * KSIZE * CIN));
    epv_puts("\nREF_CYC ");   epv_putu64(ref_cyc);
    epv_puts("\nREF_INST ");  epv_putu64(ref_in);
    epv_puts("\nEPV_CYC ");   epv_putu64(epv_cyc);
    epv_puts("\nEPV_INST ");  epv_putu64(epv_in);
    /* 定点放大 1000 倍, 免得报告里出现浮点有效位歧义 */
    epv_puts("\nSPEEDUP_X1000 ");
    epv_putu((uint32_t)(epv_cyc ? (ref_cyc * 1000ull) / epv_cyc : 0));
    epv_puts("\nCYC_PER_MAC_REF_X100 ");
    epv_putu((uint32_t)((ref_cyc * 100ull) / (PIX * COUT * KSIZE * CIN)));
    epv_puts("\nCYC_PER_MAC_EPV_X100 ");
    epv_putu((uint32_t)((epv_cyc * 100ull) / (PIX * COUT * KSIZE * CIN)));
    epv_puts("\nMISMATCH ");  epv_putu((uint32_t)bad);
    epv_puts("\n");
    if (bad) epv_exit(100 + (bad > 150 ? 150 : bad));
    epv_exit(0);
  }
  return 0;
}
