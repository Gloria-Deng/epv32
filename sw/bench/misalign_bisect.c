/* ============================================================================
 *  sw/bench/misalign_bisect.c -- 非对齐访存与非对齐跳转目标的实测探针
 *
 *  为什么单独一个文件: 报告的 3.5 节要写两句边界话 ——
 *    (a) 标量 load/store 的非对齐访问由硬件拆成两次对齐访问, 不陷入;
 *    (b) 跳转/取指目标只判「在不在地址图上」, 不判对齐。
 *  这两句不能由我读 RTL 推断, 也不能由黄金模型说(模型和核是同一个人写的,
 *  会一起错)。只有核自己跑一遍才算实测。
 *
 *  参考值的独立性: 期望值一律用**字节访问**拼出来(任何字节地址都合法, 不依赖
 *  被测特性)。如果参考值也用非对齐访问算, 这个探针就是在跟自己做假设。
 *
 *  commit 值: 0 = 全过; 1nn = 第 nn 项读挂; 2nn = 第 nn 项写挂; 3nn = 跳转挂。
 *  UART 逐项打印 got / want, 便于人看。
 * ==========================================================================*/
#include "epv32_hw.h"

static uint8_t pat[16];
static uint8_t buf[16];

/* ---- C 段用的跳板: 地址由链接器钉死, 编译器排布动不了它 ---- */
static volatile uint32_t mis_flag;
extern void mis_fn(void);
__asm__(
  "  .text\n"
  "  .p2align 2\n"                 /* 保证 mis_fn % 4 == 0, 于是 +2 真的是半字非对齐 */
  "  .globl mis_fn\n"
  "  .type  mis_fn, @function\n"
  "mis_fn:\n"
  "  li   t0, 0xA11CE\n"
  "  la   a0, mis_flag\n"
  "  sw   t0, 0(a0)\n"
  "  jalr x0, t1, 0\n"             /* 返回地址走 t1, 不碰 ra */
  "  .size mis_fn, .-mis_fn\n"
);

/* 极简陷阱处理器: 只认 cause 0(指令地址非对齐/越界), 记下三元组、把 mepc 往前
 * 挪一条指令再 mret, 于是「陷入的是哪条、mtval 报的是哪个地址、架构状态有没有
 * 被碰过」三件事全部由软件侧独立读到(与核的 dbg_* 观测口互为对照)。
 * 非 0 的 cause 一律不吞: 不挪 mepc 就会原地重陷, 归档以 MAXCYC 报红 ——
 * 探针宁可挂成超时, 也不许把一条没预期的异常悄悄咽下去。 */
static volatile uint32_t mis_caught[4];   /* cause, mepc, tval, 次数 */
static uint32_t mis_tstack[64];
extern void mis_trap_vec(void);
__asm__(
  "  .text\n"
  "  .p2align 2\n"
  "  .globl mis_trap_vec\n"
  "  .type  mis_trap_vec, @function\n"
  "mis_trap_vec:\n"
  "  csrrw sp, mscratch, sp\n"
  "  sw ra,  0(sp)\n"
  "  sw a0,  4(sp)\n"
  "  sw t0,  8(sp)\n"
  "  sw t1, 12(sp)\n"
  "  sw t2, 16(sp)\n"
  "  csrr t0, mcause\n"
  "  csrr t1, mepc\n"
  "  csrr t2, mtval\n"
  "  la   a0, mis_caught\n"
  "  sw   t0, 0(a0)\n"
  "  sw   t1, 4(a0)\n"
  "  sw   t2, 8(a0)\n"
  "  lw   ra, 12(a0)\n"
  "  addi ra, ra, 1\n"
  "  sw   ra, 12(a0)\n"
  "  bne  t0, x0, 1f\n"
  "  addi t1, t1, 4\n"
  "  csrw mepc, t1\n"
  "1:\n"
  "  lw ra,  0(sp)\n"
  "  lw a0,  4(sp)\n"
  "  lw t0,  8(sp)\n"
  "  lw t1, 12(sp)\n"
  "  lw t2, 16(sp)\n"
  "  csrrw sp, mscratch, sp\n"
  "  mret\n"
  "  .size mis_trap_vec, .-mis_trap_vec\n"
);

static void mis_arm_trap(void)
{
  /* 处理器只往下写 0(sp)..16(sp), 所以栈顶要留出这 32 字节, 不能正好给到数组尾。 */
  uint32_t base = (uint32_t)(uintptr_t)mis_tstack;
  uint32_t top  = (base + sizeof mis_tstack - 32u) & ~3u;
  uint32_t vec  = (uint32_t)(uintptr_t)mis_trap_vec & ~3u;
  uint32_t msk  = 0x88u;
  __asm__ volatile("csrw mscratch, %0" : : "r"(top));
  /* 本核 mtvec 取的是寄存器原值(quirk 4), 所以这里直接把模式位清成 0。 */
  __asm__ volatile("csrw mtvec, %0" : : "r"(vec));
  /* MIE=0 且 MPIE=0: mret 之后仍然不接中断, 免得计时器中断闯进这个只服务
   * cause 0 的处理器。 */
  __asm__ volatile("csrc mstatus, %0" : : "r"(msk) : "memory");
}


static uint32_t ld32(const void *p, uint32_t off)
{
  uint32_t v;
  __asm__ volatile("lw %0, %1(%2)" : "=r"(v) : "I"(0), "r"((const uint8_t *)p + off));
  return v;
}
static uint32_t ld16u(const void *p, uint32_t off)
{
  uint32_t v;
  __asm__ volatile("lhu %0, %1(%2)" : "=r"(v) : "I"(0), "r"((const uint8_t *)p + off));
  return v;
}
static int32_t ld16s(const void *p, uint32_t off)
{
  int32_t v;
  __asm__ volatile("lh %0, %1(%2)" : "=r"(v) : "I"(0), "r"((const uint8_t *)p + off));
  return v;
}
static int32_t ld8s(const void *p, uint32_t off)
{
  int32_t v;
  __asm__ volatile("lb %0, %1(%2)" : "=r"(v) : "I"(0), "r"((const uint8_t *)p + off));
  return v;
}
static void st32(void *p, uint32_t off, uint32_t v)
{
  __asm__ volatile("sw %2, %1(%0)" : : "r"((uint8_t *)p + off), "I"(0), "r"(v) : "memory");
}
static void st16(void *p, uint32_t off, uint32_t v)
{
  __asm__ volatile("sh %2, %1(%0)" : : "r"((uint8_t *)p + off), "I"(0), "r"(v) : "memory");
}

static uint32_t ref16(uint32_t off)   /* 只用地地道道的字节读拼参考值 */
{
  return (uint32_t)pat[off] | ((uint32_t)pat[off + 1] << 8);
}
static uint32_t ref32(uint32_t off)
{
  return (uint32_t)pat[off] | ((uint32_t)pat[off + 1] << 8)
       | ((uint32_t)pat[off + 2] << 16) | ((uint32_t)pat[off + 3] << 24);
}

int main(void)
{
  uint32_t i, got, want, off;

  for (i = 0; i < 16; i++) pat[i] = (uint8_t)i;
  for (i = 0; i < 16; i++) buf[i] = 0xFF;

  /* ---------------- A: 非对齐读 (lb/lh/lhu/lw, 含跨字) ---------------- */
  epv_puts("A lb/lh/lw\n");
  for (off = 0; off < 4; off++) {
    got = (uint32_t)ld8s(pat, off);                 /* pat[off] < 0x10, 无需符号扩展 */
    want = pat[off];
    epv_puts("  lb "); epv_putu(off); epv_putx(got); epv_putx(want); epv_putc('\n');
    if (got != want) epv_exit(100 + off);
  }
  for (off = 0; off < 3; off++) {
    got = ld16u(pat, off); want = ref16(off);
    epv_puts("  lhu "); epv_putu(off); epv_putx(got); epv_putx(want); epv_putc('\n');
    if (got != want) epv_exit(110 + off);
    got = (uint32_t)ld16s(pat, off);                /* 都 < 0x8000, 与无符号同值 */
    if (got != want) epv_exit(120 + off);
  }
  for (off = 0; off < 4; off++) {
    got = ld32(pat, off); want = ref32(off);
    epv_puts("  lw "); epv_putu(off); epv_putx(got); epv_putx(want); epv_putc('\n');
    if (got != want) epv_exit(130 + off);
  }

  /* ---------------- B: 非对齐写, 读回用地道字节读 ---------------- */
  epv_puts("B sw/sh byte-lane\n");
  for (i = 0; i < 16; i++) buf[i] = 0x00;
  for (off = 0; off < 4; off++) {
    uint32_t v = 0x44332211u + off * 0x01000000u;
    st32(buf, off, v);
    for (i = 0; i < 4; i++) {
      got = buf[off + i]; want = (v >> (8 * i)) & 0xFF;
      if (got != want) {
        epv_puts("  sw fail off="); epv_putu(off); epv_puts(" byte="); epv_putu(i);
        epv_putc(' '); epv_putx(got); epv_putx(want); epv_putc('\n');
        epv_exit(200 + off);
      }
    }
    epv_puts("  sw "); epv_putu(off); epv_putx(buf[off]) ; epv_putc('\n');
  }
  for (off = 0; off < 3; off++) {
    uint32_t v = 0x7766u + off;
    for (i = 0; i < 16; i++) buf[i] = 0x00;
    st16(buf, off, v);
    for (i = 0; i < 2; i++)
      if (buf[off + i] != ((v >> (8 * i)) & 0xFF)) epv_exit(220 + off);
    /* 写没写坏别处: 被清零后只允许 [off,off+1] 两个字节变非零 */
    for (i = 0; i < 16; i++)
      if (i != off && i != off + 1 && buf[i] != 0x00) epv_exit(230 + i);
    epv_puts("  sh "); epv_putu(off); epv_putx(buf[off]); epv_putc('\n');
  }
  /* 跨字一侧的边界: buf 全 0x5A 后 sw 到 off=3 覆盖 buf[3..6] = 44 33 22 11,
   * buf[7] 属于下一个字, 必须还是 0x5A。前一版这里我把 spill 的最后一字节
   * 写成了 buf[7], 探针自己错了一遍 —— 跨字写「多了哪几个字节」这件事
   * 只能靠这种边界检查钉住, 不能靠读 RTL 想。 */
  for (i = 0; i < 16; i++) buf[i] = 0x5A;
  st32(buf, 3, 0x11223344u);
  if (buf[6] != 0x11u || buf[7] != 0x5Au) {
    epv_puts("  spill boundary wrong "); epv_putx(buf[6]); epv_putx(buf[7]);
    epv_putc('\n'); epv_exit(240);
  }

  /* ---------------- C: 非对齐跳转目标 ---------------- */
  /* 为什么不用 C 的 &&label: 第一版用 GCC 的 computed goto 取标签地址, 结果
   * GCC 把 &&L1 解析成「L1 所在基本块的块首」, 而块首正好被它排到了本段开头那条
   * epv_puts 上 —— 探针于是无限重复打印同一句, 测的是编译器的代码排布, 不是核。
   * 现在钉一个真正的符号: 地址由链接器给, 编译器动不了。
   *
   * C1: 目标 |1  -> jalr 按规范清 bit0, 应当原样落到 mis_fn, 且不陷入。
   * C2: 目标 +2  -> 本核不实现 IALIGN=32 的「指令地址非对齐」陷入, 只判地址图
   *     范围。所以这一条要么把 a5+2 当字索引读回(行为由 RAM 决定), 要么落进
   *     别处; 到底哪种, 看归档的 dbg_trap_cnt/mcause 与这里打印的 flag。
   * 返回地址走 t1(不用 ra, 免得上游寄存器分配), mis_fn 末尾 jalr x0,t1 回 caller。 */
  epv_puts("C jalr/branch unaligned target\n");
  mis_caught[0] = mis_caught[1] = mis_caught[2] = 0;
  mis_caught[3] = 0;
  mis_flag = 0;
  __asm__ volatile("jalr t1, %0, 0" : : "r"((void *)((uintptr_t)mis_fn | 1))
                   : "t0", "t1", "a0", "memory");
  epv_puts("  C1 flag="); epv_putx(mis_flag);
  epv_puts(" traps="); epv_putu(mis_caught[3]); epv_putc('\n');
  /* bit0 由 jalr 自己清掉: 必须原样落到 mis_fn, 且一次都不许陷。 */
  if (mis_flag != 0xA11CEu) epv_exit(301);
  if (mis_caught[3] != 0u)  epv_exit(302);

  /* C2: 半字非对齐的跳转目标 -> 规范要 cause 0, mtval=目标, mepc=那条 jalr,
   * 且这条 jalr 不许留下任何架构效果(mis_flag 保持 0)。 */
  mis_arm_trap();
  mis_flag = 0;
  __asm__ volatile("jalr t1, %0, 0" : : "r"((void *)((uintptr_t)mis_fn + 2))
                   : "t0", "t1", "a0", "memory");
  epv_puts("  C2 cause="); epv_putx(mis_caught[0]);
  epv_puts(" tval=");      epv_putx(mis_caught[2]);
  epv_puts(" flag=");      epv_putx(mis_flag);      epv_putc('\n');
  if (mis_caught[0] != 0u)        epv_exit(303);
  if (mis_caught[2] != ((uintptr_t)mis_fn + 2u)) epv_exit(304);
  if (mis_flag != 0u)             epv_exit(305);

  /* C3: 「真的跳」的分支, 目标同样是半字非对齐 -> 同一条判据也要拦住。
   * 不跳的分支目标本核不判(见 RTL 里 br_err 那行注释), 所以这条必须取真。 */
  mis_caught[0] = mis_caught[2] = 0;
  __asm__ volatile("beq x0, x0, mis_fn+2\n" : : : "memory");
  epv_puts("  C3 cause="); epv_putx(mis_caught[0]);
  epv_puts(" tval=");      epv_putx(mis_caught[2]);
  epv_puts(" flag=");      epv_putx(mis_flag);      epv_putc('\n');
  if (mis_caught[0] != 0u)        epv_exit(306);
  if (mis_caught[2] != ((uintptr_t)mis_fn + 2u)) epv_exit(307);
  if (mis_flag != 0u)             epv_exit(308);
  if (mis_caught[3] != 2u)        epv_exit(309);

  epv_puts("MISALIGN_BISECT_OK\n");
  epv_exit(0);
}

