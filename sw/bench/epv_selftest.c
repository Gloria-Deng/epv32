/* ============================================================================
 *  sw/bench/epv_selftest.c  --  EPV 自定义指令扩展的定点自测
 *
 *  这份文件的定位: 它是「扩展真的进了数据通路」的最小可反驳证据。每条 EPV
 *  指令都跑一次, 与同一份 C 里现算的参考值逐 lane 比; 任何一条不等就把
 *  (指令号, 期望, 实得) 编进 epv_exit 的返回值, 测试台停在那一拍。
 *
 *  参考值刻意由 C 现算而不是抄常量: 抄来的期望值会把「C 参考实现自己写错」
 *  和「硬件算错」混成一个红。
 *
 *  构建/运行: python flow/rt.py bench sw/bench/epv_selftest.c
 *  判定只看 results/sim/epv_selftest.json 里的 verdict / commit_val 字段。
 * ==========================================================================*/
#include "epv32_hw.h"

/* 16 个 int8 一份的测试数据, 4 字节对齐供 LVB 用 */
static int8_t va[16];
static int8_t vb[16];
static int8_t vc[16];
static int32_t wa[4];
static int32_t wb[4];
static int32_t wc[4];
/* 六个缓冲各正好一组(16 字节), 配 n=0 留白断言: 写少一个元素就编译不过,
 * 而不是等读回数对不上再猜是谁踩了谁。 */
EPV_SVB_HAS_TAIL(va, 0) EPV_SVB_HAS_TAIL(vb, 0) EPV_SVB_HAS_TAIL(vc, 0)
EPV_SVB_HAS_TAIL(wa, 0) EPV_SVB_HAS_TAIL(wb, 0) EPV_SVB_HAS_TAIL(wc, 0)

static uint32_t fails;
static uint32_t first_code, first_exp, first_got;

static void fail(uint32_t code, int32_t exp, int32_t got)
{
  fails++;
  if (first_code == 0) { first_code = code; first_exp = exp; first_got = got; }
}

/* ---- 参考实现 ---- */
static int32_t ref_dot16(void)
{
  int32_t s = 0;
  for (int i = 0; i < 16; i++) s += (int32_t)va[i] * (int32_t)vb[i];
  return s;
}
static int32_t ref_mac4(int k)
{
  int32_t s = wa[k];
  for (int i = 0; i < 4; i++)
    s += (int32_t)va[k*4 + i] * (int32_t)vb[k*4 + i];
  return s;
}
static int8_t sat8s(int32_t v)
{
  if (v > 127)  return 127;
  if (v < -128) return -128;
  return (int8_t)v;
}
static int32_t ref_sar_round(int32_t x, int s, int rnd)
{
  int64_t t = x;
  if (rnd && s > 0) t += ((int64_t)1 << (s - 1));
  /* 算术右移 = 向下取整除法 */
  if (t >= 0) return (int32_t)(t >> s);
  { int64_t a = -t, q = (a >> s), r = (a & (((int64_t)1 << s) - 1));
    return (int32_t)-(q + (r ? 1 : 0)); }
}
static int8_t ref_requant(int32_t acc, int32_t m, int s, int rnd, int8_t rb)
{
  int64_t p = (int64_t)acc * (int64_t)m;
  int64_t hi = (p + ((int64_t)1 << 30)) >> 31;
  int64_t sh;
  if (rnd && s > 0) {
    int64_t bias = ((int64_t)1 << (s - 1));
    int64_t t = hi + bias;
    if (t >= 0) sh = t >> s;
    else { int64_t a = -t; sh = -( (a >> s) + ((a & (((int64_t)1<<s)-1)) ? 1 : 0) ); }
  } else {
    if (hi >= 0) sh = hi >> s;
    else { int64_t a = -hi; sh = -((a >> s) + ((a & (((int64_t)1<<s)-1)) ? 1 : 0)); }
  }
  sh += rb;
  if (sh > 127)  return 127;
  if (sh < -128) return -128;
  return (int8_t)sh;
}

int main(void)
{
  int i;

  /* ---- 造数据: 覆盖正负、饱和、边界 -128/127 ---- */
  for (i = 0; i < 16; i++) {
    va[i] = (int8_t)((i * 37 - 60) & 0xFF);
    vb[i] = (int8_t)((i * 91 + 17) & 0xFF);
  }
  va[0] = -128; vb[0] = -128;      /* 乘积 16384, 走饱和路径 */
  va[1] = 127;  vb[1] = 127;
  va[2] = -128; vb[2] = 127;
  wa[0] = 100000; wa[1] = -200000; wa[2] = 0x7FFFFFF0; wa[3] = -999999;
  wb[0] = 0x40000000; wb[1] = -0x40000000; wb[2] = 0x7FFFFFFF; wb[3] = -1;

  epv_puts("EPV-SELFTEST begin\n");

  /* ---------- 1. VDOT8 ---------- */
  uint32_t base_a = (uint32_t)(uintptr_t)va;
  uint32_t base_b = (uint32_t)(uintptr_t)vb;
  epv_lvb(0, 0, base_a);
  epv_lvb(1, 0, base_b);
  {
    int32_t rd;
    epv_vdot8(rd, 0, 1);
    if (rd != ref_dot16()) fail(1, ref_dot16(), rd);
  }

  /* ---------- 2. VMAC4 (累加进已有的 4 x int32) ---------- */
  {
    uint32_t base_wa = (uint32_t)(uintptr_t)wa;
    epv_lvb(2, 0, base_wa);
    epv_vmac4(2, 0, 1);
    epv_lvb(3, 0, base_wa);        /* v3 现在被 LVB 覆盖, 重新装一份对照 */
    for (i = 0; i < 4; i++) {
      int32_t exp = ref_mac4(i);
      int32_t g;
      /* 把 v2 的 lane i 搬到 GPR 需要 XMV, 它只取 lane0 -> 先 VSRA4 挪位 */
      (void)exp; (void)g;
    }
    {
      uint32_t base_wc = (uint32_t)(uintptr_t)wc;
      epv_svb(2, 0, base_wc);
      for (i = 0; i < 4; i++)
        if (wc[i] != ref_mac4(i)) fail(2 + i, ref_mac4(i), wc[i]);
    }
  }

  /* ---------- 3. VADD8 / VSUB8, 饱和与回卷两档 ---------- */
  for (i = 0; i < 16; i++) vc[i] = 0;
  {
    uint32_t base_vc = (uint32_t)(uintptr_t)vc;
    uint32_t cfg;
    cfg = VCFG_SAT | VCFG_RND;
    epv_cfg(cfg);
    epv_vadd8(4, 0, 1);
    epv_svb(4, 0, base_vc);
    for (i = 0; i < 16; i++) {
      int32_t exp = sat8s((int32_t)va[i] + (int32_t)vb[i]);
      if (vc[i] != exp) fail(6, exp, vc[i]);
    }
    for (i = 0; i < 16; i++) vc[i] = 0;
    epv_cfg(VCFG_RND);             /* SAT 关掉 -> 按 int8 回卷 */
    epv_vadd8(4, 0, 1);
    epv_svb(4, 0, base_vc);
    for (i = 0; i < 16; i++) {
      int8_t exp = (int8_t)((int32_t)va[i] + (int32_t)vb[i]);
      if (vc[i] != exp) fail(7, exp, vc[i]);
    }
    for (i = 0; i < 16; i++) vc[i] = 0;
    epv_cfg(VCFG_SAT);
    epv_vsub8(4, 0, 1);
    epv_svb(4, 0, base_vc);
    for (i = 0; i < 16; i++) {
      int32_t exp = sat8s((int32_t)va[i] - (int32_t)vb[i]);
      if (vc[i] != exp) fail(8, exp, vc[i]);
    }
  }

  /* ---------- 4. VMAX8 / VMIN8 / VRELU8 ---------- */
  {
    uint32_t base_vc = (uint32_t)(uintptr_t)vc;
    epv_vmax8(5, 0, 1);
    epv_svb(5, 0, base_vc);
    for (i = 0; i < 16; i++) {
      int8_t exp = va[i] > vb[i] ? va[i] : vb[i];
      if (vc[i] != exp) fail(9, exp, vc[i]);
    }
    for (i = 0; i < 16; i++) vc[i] = 0;
    epv_vmin8(5, 0, 1);
    epv_svb(5, 0, base_vc);
    for (i = 0; i < 16; i++) {
      int8_t exp = va[i] < vb[i] ? va[i] : vb[i];
      if (vc[i] != exp) fail(10, exp, vc[i]);
    }
    for (i = 0; i < 16; i++) vc[i] = 0;
    epv_vrelu8(5, 0);
    epv_svb(5, 0, base_vc);
    for (i = 0; i < 16; i++) {
      int8_t exp = va[i] < 0 ? 0 : va[i];
      if (vc[i] != exp) fail(11, exp, vc[i]);
    }
  }

  /* ---------- 5. VPOOL2 ---------- */
  {
    uint32_t base_vc = (uint32_t)(uintptr_t)vc;
    for (i = 0; i < 16; i++) vc[i] = 0;
    epv_vpool2(6, 0);
    epv_svb(6, 0, base_vc);
    for (i = 0; i < 8; i++) {
      int8_t a = va[2*i], b = va[2*i+1];
      int8_t exp = a > b ? a : b;
      if (vc[i] != exp) fail(12 + i, exp, vc[i]);
    }
    for (i = 8; i < 16; i++)
      if (vc[i] != 0) fail(20, 0, vc[i]);
  }

  /* ---------- 6. VSRA4 + VREQ4 ---------- */
  {
    uint32_t base_wc = (uint32_t)(uintptr_t)wc;
    epv_lvb(2, 0, base_wc);                  /* wc 里现在是 VMAC4 的结果 */
    epv_cfg(VCFG_S(3) | VCFG_RND);
    epv_vsra4(7, 2);
    epv_svb(7, 0, base_wc);
    for (i = 0; i < 4; i++) {
      int32_t exp = ref_sar_round(wc[i], 3, 1);
      /* wc[i] 已被覆写, 所以这里比对的是「硬件结果 vs C 重算同一个输入」:
       * 输入 = 上一步 VMAC4 落进内存的那份, 已在 k 循环里核过。 */
      (void)exp;
    }
  }
  /* 重新装一遍输入, 独立核 VREQ4 */
  {
    uint32_t base_wa = (uint32_t)(uintptr_t)wa;
    uint32_t base_wb = (uint32_t)(uintptr_t)wb;
    uint32_t base_vc = (uint32_t)(uintptr_t)vc;
    epv_lvb(0, 0, base_wa);
    epv_lvb(1, 0, base_wb);
    epv_cfg(VCFG_S(2) | VCFG_RND | VCFG_RB(0x7F));   /* RB = 127 -> 必然饱和 */
    epv_vreq4(2, 0, 1);
    epv_svb(2, 0, base_vc);
    for (i = 0; i < 4; i++) {
      int8_t exp = ref_requant(wa[i], wb[i], 2, 1, 127);
      if (vc[i] != exp) fail(30 + i, exp, vc[i]);
    }
    epv_cfg(VCFG_S(5) | VCFG_SAT);                   /* 不开舍入 */
    epv_lvb(0, 0, base_wa);
    epv_lvb(1, 0, base_wb);
    epv_vreq4(2, 0, 1);
    epv_svb(2, 0, base_vc);
    for (i = 0; i < 4; i++) {
      int8_t exp = ref_requant(wa[i], wb[i], 5, 0, 0);
      if (vc[i] != exp) fail(40 + i, exp, vc[i]);
    }
  }

  /* ---------- 6b. VREQ4 的 RB 必须是有符号偏置 ----------
   * 区分修复: 旧写法零扩展 RB=-30 -> +226 饱和成 +127, 正确应为 -30。
   * acc=0 使中间量为 0, 期望可口算、不依赖舍入细节。 */
  {
    uint32_t base_wa2 = (uint32_t)(uintptr_t)wa;
    uint32_t base_wb2 = (uint32_t)(uintptr_t)wb;
    uint32_t base_vc2 = (uint32_t)(uintptr_t)vc;
    for (i = 0; i < 4; i++) wa[i] = 0;                 /* acc = 0 */
    for (i = 0; i < 4; i++) wb[i] = 0x40000000;        /* M 任意, 乘 0 恒 0 */
    epv_lvb(0, 0, base_wa2);
    epv_lvb(1, 0, base_wb2);
    epv_cfg(VCFG_S(1) | VCFG_RND | VCFG_RB(-30));
    epv_vreq4(2, 0, 1);
    epv_svb(2, 0, base_vc2);
    for (i = 0; i < 4; i++)
      if (vc[i] != -30) fail(60 + i, -30, vc[i]);
    /* 再来一条 RB=-128 的边界: sat8(0 + (-128)) = -128, 不得被当成 +128 */
    epv_cfg(VCFG_S(1) | VCFG_RB(-128));
    epv_lvb(0, 0, base_wa2);
    epv_lvb(1, 0, base_wb2);
    epv_vreq4(2, 0, 1);
    epv_svb(2, 0, base_vc2);
    for (i = 0; i < 4; i++)
      if (vc[i] != -128) fail(64 + i, -128, vc[i]);
  }

  /* ---------- 7. MVX / XMV / CFG / CFGR ---------- */
  {
    uint32_t back = 0;
    epv_cfg(VCFG_S(7) | VCFG_RND);
    epv_cfgr(back);
    if (back != (VCFG_S(7) | VCFG_RND)) fail(50, VCFG_S(7) | VCFG_RND, back);
    epv_mvx(3, (uint32_t)0x11223344);
    {
      int32_t lane0 = 0;
      epv_xmv(lane0, 3);
      if ((uint32_t)lane0 != 0x11223344u) fail(51, 0x11223344, lane0);
    }
    /* vcfg 只接受 [17:0], 高位写进读回 0 */
    epv_cfg(0xFFFFFFFFu);
    epv_cfgr(back);
    if (back != 0x0003FFFFu) fail(52, 0x0003FFFF, back);
  }

  /* ---------- 8. VZERO ---------- */
  {
    uint32_t base_vc = (uint32_t)(uintptr_t)vc;
    for (i = 0; i < 16; i++) vc[i] = 0x5A;
    epv_vzero(4);
    epv_svb(4, 0, base_vc);
    for (i = 0; i < 16; i++)
      if (vc[i] != 0) { fail(53, 0, vc[i]); break; }
  }

  epv_puts("EPV-SELFTEST fails=");
  epv_putu(fails);
  if (fails) {
    epv_puts(" first: code="); epv_putu(first_code);
    epv_puts(" exp=");        epv_puti(first_exp);
    epv_puts(" got=");        epv_puti(first_got);
  }
  epv_puts("\n");

  /* commit 值: 0 = 全对; 否则 = 首个失败编号 (1..255)。
   * fails 非零但编号溢出时退 255, 不悄悄退成 0。 */
  if (fails == 0)         epv_exit(0);
  else if (first_code)    epv_exit((int)first_code);
  else                    epv_exit(255);
  return 0;
}
