/* ============================================================================
 *  sw/bench/epv_bisect.c  --  EPV 通路二分探针
 *
 *  目的只有一个: 把「16 字节搬运对不对」和「乘法/归约对不对」分开判。
 *  三步各自独立可反驳:
 *    A 内存 -> LVB -> SVB -> 内存 的逐字节回环   (不碰任何运算)
 *    B MVX(标量广播) -> SVB -> 内存              (不碰 LVB)
 *    C VDOT8, 输入用 A 里已经验过的装载路径, 且同时给一组「全 1」和一组
 *      「只有 lane0 = 1」的退化输入, 让期望值可以口算
 *  commit 值: 0=全过; 1xx=A 挂; 2xx=B 挂; 3xx=C 挂, 后两位是 lane 号。
 * ==========================================================================*/
#include "epv32_hw.h"

static uint8_t  src[16];
static uint8_t  dst[16];
static int32_t  dv[4];
static uint32_t dvb[4];
/* dst/dv/dvb 各正好一组(16 字节), 配 n=0 留白断言: 少写一个元素就编译不过。 */
EPV_SVB_HAS_TAIL(dst, 0) EPV_SVB_HAS_TAIL(dv, 0) EPV_SVB_HAS_TAIL(dvb, 0)

int main(void)
{
  uint32_t bs = (uint32_t)(uintptr_t)src;
  uint32_t bd = (uint32_t)(uintptr_t)dst;
  int i;
  uint32_t sum, exp;

  /* ---------------- A: LVB -> SVB 逐字节回环 ---------------- */
  for (i = 0; i < 16; i++) src[i] = (uint8_t)(0xA0 + i);
  for (i = 0; i < 16; i++) dst[i] = 0x00;
  epv_lvb(0, 0, bs);
  epv_svb(0, 0, bd);
  epv_puts("A: ");
  for (i = 0; i < 16; i++) { epv_putx(dst[i]); epv_putc(' '); }
  epv_puts("\n");
  for (i = 0; i < 16; i++)
    if (dst[i] != src[i]) { epv_puts("A FAIL lane "); epv_putu(i);
                            epv_puts("\n"); epv_exit(100 + i); }
  epv_puts("A OK\n");

  /* 偏移量也要验: LVB 带 4 的偏移 */
  for (i = 0; i < 16; i++) dst[i] = 0;
  epv_lvb(1, 4, bs);            /* 读 src[4..19] */
  epv_svb(1, 0, bd);
  epv_puts("A2 got: ");
  for (i = 0; i < 16; i++) { epv_putx(dst[i]); epv_putc(' '); }
  epv_puts("\n");

  /* ---------------- B: MVX 广播 ---------------- */
  for (i = 0; i < 4; i++) dvb[i] = 0xDEADBEEF;
  epv_mvx(2, 0x11223344u);
  epv_svb(2, 0, (uint32_t)(uintptr_t)dvb);
  epv_puts("B: ");
  for (i = 0; i < 4; i++) { epv_putx(dvb[i]); epv_putc(' '); }
  epv_puts("\n");
  for (i = 0; i < 4; i++)
    if (dvb[i] != 0x11223344u) { epv_puts("B FAIL word "); epv_putu(i);
                                 epv_puts("\n"); epv_exit(200 + i); }
  epv_puts("B OK\n");

  /* ---------------- C: VDOT8, 口算输入 ---------------- */
  /* C1: va 全 1, vb 全 1 -> 16 */
  for (i = 0; i < 16; i++) { src[i] = 1; dst[i] = 1; }
  epv_lvb(3, 0, (uint32_t)(uintptr_t)src);
  epv_lvb(4, 0, (uint32_t)(uintptr_t)dst);
  epv_vdot8(sum, 3, 4);
  epv_puts("C1 dot(all1,all1)="); epv_puti((int32_t)sum); epv_puts("\n");
  if ((int32_t)sum != 16) { epv_puts("C1 FAIL\n"); epv_exit(301); }

  /* C2: va[0]=2, 其余 0; vb[0]=3 -> 6 */
  for (i = 0; i < 16; i++) { src[i] = 0; dst[i] = 0; }
  src[0] = 2; dst[0] = 3;
  epv_lvb(3, 0, (uint32_t)(uintptr_t)src);
  epv_lvb(4, 0, (uint32_t)(uintptr_t)dst);
  epv_vdot8(sum, 3, 4);
  epv_puts("C2 dot(lane0)="); epv_puti((int32_t)sum); epv_puts("\n");
  if ((int32_t)sum != 6) { epv_puts("C2 FAIL\n"); epv_exit(302); }

  /* C3: 只有 lane 7 非零 -> 排除 lane 顺序错位 */
  for (i = 0; i < 16; i++) { src[i] = 0; dst[i] = 0; }
  src[7] = 5; dst[7] = -6;
  epv_lvb(3, 0, (uint32_t)(uintptr_t)src);
  epv_lvb(4, 0, (uint32_t)(uintptr_t)dst);
  epv_vdot8(sum, 3, 4);
  epv_puts("C3 dot(lane7)="); epv_puti((int32_t)sum); epv_puts(" exp=-30\n");
  if ((int32_t)sum != (uint32_t)(-30)) { epv_puts("C3 FAIL\n"); epv_exit(303); }

  /* C4: VMAC4 单 lane */
  for (i = 0; i < 4; i++) dv[i] = 0;
  for (i = 0; i < 16; i++) { src[i] = 0; dst[i] = 0; }
  src[0] = 4; dst[0] = 5;                    /* lane0 -> 累加进 word0 */
  epv_lvb(3, 0, (uint32_t)(uintptr_t)src);
  epv_lvb(4, 0, (uint32_t)(uintptr_t)dst);
  epv_mvx(5, 0);                             /* v5 = 4 x int32 零 */
  epv_vmac4(5, 3, 4);
  epv_svb(5, 0, (uint32_t)(uintptr_t)dv);
  epv_puts("C4 mac4: ");
  for (i = 0; i < 4; i++) { epv_puti(dv[i]); epv_putc(' '); }
  epv_puts(" exp=20 0 0 0\n");
  exp = 20;
  if ((uint32_t)dv[0] != exp || dv[1] || dv[2] || dv[3]) {
    epv_puts("C4 FAIL\n"); epv_exit(304);
  }
  epv_puts("C OK\n");
  epv_exit(0);
  return 0;
}
