/* ============================================================================
 *  sw/bench/micro_bisect.c -- 计数器写语义 与 PMP 的实测探针 (M9)
 *
 *  为什么单独一个文件, 而不是「rv32mi 过了就算」:
 *    rv32mi/instret_overflow 与 rv32mi/pmpaddr 各只给我一个 TESTNUM。
 *    TESTNUM=4 到底是「高位没写进去」、「低位的进位没穿到高位」还是「自增
 *    抑制没生效」—— 用例本身分不出来, 而这三种毛病修法互不相同。所以这里把
 *    官方那一段拆成**每一步都单独读一次**的形状, 让每一步自己报数。
 *    PMP 那条更要紧: rv32mi/pmpaddr 考的是 CSR 的 WARL 回读, 它一个字都不考
 *    「命中之后许不许访问」。要主张「PMP 生效」, 必须自己造违例并看 cause。
 *
 *  参考值的独立性: 每条期望值由规范文本给出 (特权规范 3.7 节 PMP / Zicntr 对
 *    「写入那一拍抑制自增」的说明), 不由 RTL 反推。没有把握的读数 (mcycle
 *    写入那一拍的自增) 只打印、不主张。
 *
 *  一条结构性限制决定了本探针的形状: 表项优先级按索引, 而 L=1 之后该表项
 *  在复位前不可改 —— 所以「一次运行里只有一份锁定表项可用」。于是
 *    entry0 先当「未锁定但 W=0」的对照 (E1), 再当「锁定 NAPOT」的主体 (E2);
 *    entry1 只做兜底 (2 GiB NAPOT, L=0), 用来证明「比它低编号的表项先命中时
 *    轮不到它」以及「它命中而 L=0 时 M 模式照样放行」。
 *  TOR/NA4 两条模式的**强制行为**因此没有可观测窗口 (见 E5 后面的说明),
 *  只能按「RTL 里实现了、未实测」写, 不许并入「PMP 已生效」这句话。
 *
 *  commit 值: 0 = 全过; 1xx = 计数器段; 2xx = PMP 的 WARL; 3xx/4xx = PMP 的强制。
 *  UART 逐项打印 got, 便于人对着归档核。
 * ==========================================================================*/
#include "epv32_hw.h"

#define CSR_PMPCFG0        0x3A0
#define CSR_PMPADDR0       0x3B0
#define CSR_PMPADDR1       0x3B1
#define CSR_PMPADDR2       0x3B2
#define CSR_PMPADDR3       0x3B3
#define CSR_MCOUNTINHIBIT  0x320

/* 表项字节: L=bit0 R=1 W=2 X=3 A[5:4] (0=OFF 1=TOR 2=NA4 3=NAPOT) */
#define PC_L    0x01u
#define PC_R    0x02u
#define PC_W    0x04u
#define PC_X    0x08u
#define A_OFF   0u
#define A_TOR   1u
#define A_NA4   2u
#define A_NAP   3u
/* e = 表项编号 0..3 */
#define CFG(entry, a, lock, r, w, x) \
  ((uint32_t)((((a) << 4u) | ((lock) ? PC_L : 0u) | ((r) ? PC_R : 0u) | \
               ((w) ? PC_W : 0u) | ((x) ? PC_X : 0u)) << ((entry) * 8u)))

/* 兜底项: NAPOT 覆盖 0x8000_0000 起 2 GiB (RAM + 全部外设)。
 * 编码: a2 = addr[31:2] 的域里, 基址 a2=0x2000_0000、可变位 29 个
 *       => pmpaddr = 0x2000_0000 | 0x1FFF_FFFF = 0x3FFF_FFFF ... 那是全 1,
 *          掩码会把边界位一起吃掉 => 命中一切。所以退一档取 1 GiB:
 *          基址 a2=0x2000_0000、可变位 28 => pmpaddr = 0x2FFF_FFFF。
 *       验证: m = 0x2FFF_FFFF ^ 0x3000_0000 = 0x1FFF_FFFF ^ 0x...
 *       —— 这条不靠手算定, 由 E2 的「blk[4] 落在区间外照常可写」与
 *          「blk[0..3] 被 entry0 拦住」两件事一起把它钉死。 */
#define PMP_CATCHALL 0x2FFFFFFFu
#define PMP_CATCHALL_TOP 0x10000000u     /* 本项覆盖到 0x8000_0000 + 1 GiB */

/* NAPOT 的区域大小 = 2^(G+3) 字节, G = pmpaddr 尾 1 的个数。
 * 要 16 字节区域就得**恰好一个尾 1** (=> 编码是 |1, 不是 |3)。
 * 这条不是抠字: 第一版用 |3 配 16 字节对齐, 实测 (E2b) blk+16 那一字也被拦,
 * 说明区域是 32 字节档 —— 夹具的编码写错, 报红的是夹具而不是核。
 * 块地址取 32 字节对齐, 保证 (blk>>2) 的低 3 位是 0, 于是 |1 之后尾 1 个数
 * 恰好是 1, 由下面那条现算自检钉住。 */
static volatile uint32_t pmp_blk[8] __attribute__((aligned(32)));
static volatile uint32_t pmp_free_box;

/* 极简陷阱处理器: 只认 cause 5(load access fault) 与 7(store access fault),
 * 记下 (cause, mepc, mtval) 三元组与次数, 把 mepc 往前挪一条再 mret。
 * 其它 cause 一律不吞 —— 不挪 mepc 就会原地重陷, 归档以 MAXCYC 报红:
 * 探针宁可挂成超时, 也不许把一条没预期的异常悄悄咽下去。 */
static volatile uint32_t pmp_caught[4];      /* cause, mepc, tval, 次数 */
static uint32_t pmp_tstack[64];
extern void pmp_trap_vec(void);
__asm__(
  "  .text\n"
  "  .p2align 2\n"
  "  .globl pmp_trap_vec\n"
  "  .type  pmp_trap_vec, @function\n"
  "pmp_trap_vec:\n"
  "  csrrw sp, mscratch, sp\n"
  "  sw ra,  0(sp)\n"
  "  sw a0,  4(sp)\n"
  "  sw a1,  8(sp)\n"
  "  sw t0, 12(sp)\n"
  "  sw t1, 16(sp)\n"
  "  sw t2, 20(sp)\n"
  "  csrr t0, mcause\n"
  "  csrr t1, mepc\n"
  "  csrr t2, mtval\n"
  "  la   a0, pmp_caught\n"
  "  sw   t0, 0(a0)\n"
  "  sw   t1, 4(a0)\n"
  "  sw   t2, 8(a0)\n"
  "  lw   a1, 12(a0)\n"
  "  addi a1, a1, 1\n"
  "  sw   a1, 12(a0)\n"
  "  li   a1, 5\n"
  "  beq  t0, a1, 1f\n"
  "  li   a1, 7\n"
  "  bne  t0, a1, 2f\n"
  "1:\n"
  "  addi t1, t1, 4\n"
  "  csrw mepc, t1\n"
  "2:\n"
  "  lw ra,  0(sp)\n"
  "  lw a0,  4(sp)\n"
  "  lw a1,  8(sp)\n"
  "  lw t0, 12(sp)\n"
  "  lw t1, 16(sp)\n"
  "  lw t2, 20(sp)\n"
  "  csrrw sp, mscratch, sp\n"
  "  mret\n"
  "  .size pmp_trap_vec, .-pmp_trap_vec\n"
);

static void arm_trap(void)
{
  uint32_t base = (uint32_t)(uintptr_t)pmp_tstack;
  uint32_t top  = (base + sizeof pmp_tstack - 32u) & ~7u;
  uint32_t vec  = (uint32_t)(uintptr_t)pmp_trap_vec & ~3u;
  epv_wr_csr(CSR_MSCRATCH, top);
  epv_wr_csr(CSR_MTVEC, vec);              /* 本核只支持 mode 0, 模式位保持 0 */
  /* MIE=0 且 MPIE=0: mret 之后仍不接中断, 免得计时中断闯进这个只认 5/7 的处理器。 */
  __asm__ volatile("csrc mstatus, %0" : : "r"(0x88u) : "memory");
  pmp_caught[0] = pmp_caught[1] = pmp_caught[2] = pmp_caught[3] = 0;
}

static void hx(const char *tag, uint32_t v)
{
  epv_puts(tag); epv_putx(v); epv_putc('\n');
}

/* ============================================================ 计数器段
 * 每一项都在**一条 asm 里**跑完自己那段序列, 于是「中间隔了几条指令」由我
 * 钉死, 不由编译器决定。 */
static void probe_counters(void)
{
  uint32_t a, b, c;

  epv_puts("[C] counter write semantics\n");

  /* C1: 官方 test_2 的形状 —— 写 0 之后下一条读到的就该是 0。 */
  __asm__ volatile("csrw minstret, x0\n csrr %0, minstret\n"
                   : "=r"(a) : : "memory");
  hx("  C1 csrw minstret,0 -> minstret=", a);
  if (a != 0u) epv_exit(101);

  /* C2: 紧接着读高位。复位以来退休数远小于 2^32, 高位该是 0。 */
  __asm__ volatile("csrr %0, minstreth\n" : "=r"(b) : : "memory");
  hx("  C2 minstreth=", b);
  if (b != 0u) epv_exit(102);

  /* C3: 进位穿不穿得过去 —— 把 64 位放成 0x0000_0000_FFFF_FFFF, 再退休一条,
   * 低位该翻成 0、高位该变成 1。这一项与官方用例无关, 它只回答「是不是
   * 两个独立的 32 位计数器」。 */
  __asm__ volatile(
      "  csrw minstreth, x0\n"
      "  li   t0, -1\n"
      "  csrw minstret,  t0\n"
      "  nop\n"
      "  csrr %0, minstret\n"
      "  csrr %1, minstreth\n"
      : "=r"(a), "=r"(b) : : "t0", "memory");
  hx("  C3 lo(want 0)=", a);
  hx("  C3 hi(want 1)=", b);
  if (a != 0u) epv_exit(103);
  if (b != 1u) epv_exit(104);

  /* C4: 官方 test_3/test_4 的形状 —— 两半都写 -1, 隔一条之后两半都该是 0
   * (0xFFFF_FFFF_FFFF_FFFF + 1 回到 0)。 */
  __asm__ volatile(
      "  li   t0, -1\n"
      "  csrw minstret,  t0\n"
      "  csrw minstreth, t0\n"
      "  nop\n"
      "  csrr %0, minstret\n"
      "  csrr %1, minstreth\n"
      : "=r"(a), "=r"(b) : : "t0", "memory");
  hx("  C4 lo(want 0)=", a);
  hx("  C4 hi(want 0)=", b);
  if (a != 0u) epv_exit(105);
  if (b != 0u) epv_exit(106);

  /* C5: 只写高位也算「写 instret」(规范明写), 所以它自己那次自增也要被抑制。
   * 形状: 先对齐到已知小值, 只写高位, 然后读两半。这一条只主张高位落值,
   * 低位只许单调不减 (两条之间退休的指令会加它)。 */
  __asm__ volatile("csrw minstreth, x0\n csrw minstret, x0\n nop\n"
                   "csrr %0, minstret\n" : "=r"(a) : : "memory");
  __asm__ volatile("li t0, 0x10\n csrw minstreth, t0\n"
                   "csrr %0, minstret\n csrr %1, minstreth\n"
                   : "=r"(b), "=r"(c) : : "t0", "memory");
  hx("  C5 base lo=", a);
  hx("  C5 lo(>=base)=", b);
  hx("  C5 hi(want 0x10)=", c);
  if (c != 0x10u) epv_exit(107);
  if (b < a) epv_exit(108);

  /* C6: mcycle 写入 —— 规范没规定它要不要抑制自增, 本实现选择不抑制 (写在
   * RTL 文件头的设计决定里)。这一项只主张「墙钟计数器确实在走」。 */
  __asm__ volatile("csrw mcycle, x0\n csrr %0, mcycle\n" : "=r"(a) : : "memory");
  __asm__ volatile("csrr %0, mcycle\n" : "=r"(b) : : "memory");
  hx("  C6 mcycle write 0 then read at once=", a);
  hx("  C6 read again=", b);
  if (b <= a) epv_exit(109);

  /* C7: mcountinhibit 的 CY/IR 两位真能停表。 */
  __asm__ volatile("csrw mcountinhibit, %0" :: "r"(0x4u) : "memory");
  __asm__ volatile("csrr %0, minstret\n nop\n nop\n nop\n csrr %1, minstret\n"
                   : "=r"(a), "=r"(b) : : "memory");
  hx("  C7 IR=1: minstret before=", a);
  hx("  C7 IR=1: after (must equal)=", b);
  if (a != b) epv_exit(110);
  __asm__ volatile("csrw mcountinhibit, %0" :: "r"(0x5u) : "memory");
  __asm__ volatile("csrr %0, mcycle\n nop\n csrr %1, mcycle\n"
                   : "=r"(a), "=r"(b) : : "memory");
  hx("     CY|IR=1: mcycle before=", a);
  hx("     CY|IR=1: after (must equal)=", b);
  if (a != b) epv_exit(111);
  __asm__ volatile("csrw mcountinhibit, x0" :: : "memory");
  __asm__ volatile("csrr %0, mcountinhibit\n" : "=r"(a) : : "memory");
  hx("     readback after clear=", a);
  if (a != 0u) epv_exit(112);
}

/* ============================================================ PMP WARL 段 */
static void probe_pmp_warl(void)
{
  uint32_t v;

  epv_puts("[P] PMP registers WARL\n");

  /* P1: 软件按规范探测粒度 —— 写 0 进 pmpcfg0、写全 1 进 pmpaddr0、读回,
   * 最低置位就是 G。本实现 G=0 (4 字节粒度) => 读回 0x3FFF_FFFF:
   * bit31/bit30 对应不存在的物理地址位, 必须读 0。
   * 这也正是 rv32mi/pmpaddr 自己注释里声明的「G=0 => 平凡通过」分支。 */
  epv_wr_csr(CSR_PMPCFG0, 0u);
  epv_wr_csr(CSR_PMPADDR0, 0xFFFFFFFFu);
  v = epv_rd_csr(CSR_PMPADDR0);
  hx("  P1 pmpaddr0 write all-ones readback=", v);
  if (v != 0x3FFFFFFFu) epv_exit(201);

  /* P2: pmpcfg0 每个表项的位 7:6 保留, 读回必须 0。
   *
   * 这里**故意不写全 1**: 第一版探针写的就是 0xFFFF_FFFF, 于是四个表项的 L 位
   * 全被立起来, 紧接着那句「清成 0」被硬件正确忽略, P3 就再也写不进
   * pmpaddr3 了 —— 报红的是探针自己, 不是核。这条误报本身值得留成读数:
   * 锁定在复位之前不可解, 连「清 pmpcfg」这个动作也不行, 见 E4。
   * 判据取 0xFE 而不是 0xFF: 位 7:6 照样置起来测掩码, 而 L(bit0) 保持 0。 */
  epv_wr_csr(CSR_PMPCFG0, 0xFEFEFEFEu);
  v = epv_rd_csr(CSR_PMPCFG0);
  hx("  P2 pmpcfg0 write 0xFEFEFEFE readback=", v);
  if (v != 0x3E3E3E3Eu) epv_exit(202);
  epv_wr_csr(CSR_PMPCFG0, 0u);

  /* P3: pmpaddr 的全部 30 位都可保留 (G=0), 所以写 0x1234_5678 应逐字回读。
   * 这条把「粒度到底是多少」从措辞变成读数。 */
  epv_wr_csr(CSR_PMPADDR3, 0x12345678u);
  v = epv_rd_csr(CSR_PMPADDR3);
  hx("  P3 pmpaddr3 readback=", v);
  if (v != 0x12345678u) epv_exit(203);
  epv_wr_csr(CSR_PMPADDR3, 0u);

  epv_puts("  P4 no locked entry: RAM/periph read+write normal (this function is the proof)\n");
}

/* ============================================================ PMP 强制段 */
static void probe_pmp_enforce(void)
{
  uint32_t blk     = (uint32_t)(uintptr_t)&pmp_blk[0];
  uint32_t out_addr = (uint32_t)(uintptr_t)&pmp_blk[4];
  uint32_t free_addr = (uint32_t)(uintptr_t)&pmp_free_box;
  uint32_t v;

  epv_puts("[E] PMP enforcement\n");
  hx("  blk(16B aligned)=", blk);
  hx("  blk+16 (outside region)=", out_addr);
  hx("  free_box=", free_addr);

  /* E1: 未锁定的表项对 M 模式不生效 (规范: L=0 时 M 模式无视权限位)。
   * entry0 = NA4 覆盖 free_box、R=1 W=0、L=0 => store 必须照常成功。
   * 这条排在最前面, 因为一旦有 locked 表项立着, 规范第 3.7.2 节第三条就会
   * 把所有「无命中」的 M 模式访问判死 —— 那时这条对照就不干净了。 */
  epv_wr_csr(CSR_PMPADDR0, free_addr >> 2);
  epv_wr_csr(CSR_PMPCFG0, CFG(0, A_NA4, 0, 1, 0, 1));
  arm_trap();
  pmp_free_box = 0x5A5Au;
  v = pmp_free_box;
  hx("  E1 cause(want 0)=", pmp_caught[0]);
  hx("  E1 free_box=", v);
  if (pmp_caught[0] != 0u) epv_exit(301);
  if (v != 0x5A5Au)        epv_exit(302);
  epv_wr_csr(CSR_PMPCFG0, 0u);
  epv_wr_csr(CSR_PMPADDR0, 0u);

  /* E2: 锁定表项 + 兜底项。entry0 = NAPOT 16 字节覆盖 blk[0..3], R=1 W=0 L=1;
   *     entry1 = 1 GiB NAPOT 覆盖 RAM 与外设, R=W=X=1, L=0。 */
  epv_wr_csr(CSR_PMPADDR1, PMP_CATCHALL);
  epv_wr_csr(CSR_PMPCFG0, CFG(1, A_NAP, 0, 1, 1, 1));
  /* 先证明这次编码**真的是 16 字节档**: 尾 1 的个数决定区域大小 (k 个尾 1
   * => 2^(k+3) 字节), 所以要求恰好两个尾 1。夹具的形状不现算就等着它悄悄
   * 变成 64 字节档, 那时 E2b 报的是夹具而不是核。 */
  {
    uint32_t pa0 = (blk >> 2) | 1u;
    hx("  E2 pmpaddr0=", pa0);
    if ((pa0 & 3u) != 1u) epv_exit(320);   /* 恰好一个尾 1 => 16 字节区域 */
    hx("  E2 blk>>2  low 4 bits=", (blk >> 2) & 0xFu);
  }
  epv_wr_csr(CSR_PMPADDR0, (blk >> 2) | 1u);          /* 16 字节 NAPOT 编码 */
  epv_wr_csr(CSR_PMPCFG0, CFG(0, A_NAP, 1, 1, 0, 1) |
                          CFG(1, A_NAP, 0, 1, 1, 1));
  arm_trap();
  pmp_blk[0] = 0xDEAD0000u;
  hx("  E2 blk[0] after store cause(want 7)=", pmp_caught[0]);
  hx("  E2 blk[0] value(wantunchanged)=", pmp_blk[0]);
  if (pmp_caught[0] != 7u)   epv_exit(303);
  if (pmp_blk[0] != 0u)      epv_exit(304);
  if (pmp_caught[3] != 1u)   epv_exit(305);

  /* E2b: 16 字节区间内另外三个字都要被拦住, 且 mtval 逐字对得上。 */
  {
    int i;
    for (i = 1; i < 4; i++) {
      arm_trap();
      pmp_blk[i] = 0u;
      hx("  E2b inside region cause=", pmp_caught[0]);
      hx("  E2b inside region tval=", pmp_caught[2]);
      if (pmp_caught[0] != 7u) epv_exit(306);
      if (pmp_caught[2] != (uint32_t)(uintptr_t)&pmp_blk[i]) epv_exit(307);
      if (pmp_caught[3] != 1u) epv_exit(308);
    }
    /* 区间外那一字必须放行 —— 这条同时钉住 NAPOT 的**边界**和
     * 「entry1 兜底命中 => 放行」两件事。 */
    arm_trap();
    pmp_blk[4] = 0xBEEF4u;
    hx("  E2b outside region cause(want 0)=", pmp_caught[0]);
    hx("  E2b outside regionvalue=", pmp_blk[4]);
    if (pmp_caught[0] != 0u)      epv_exit(309);
    if (pmp_blk[4] != 0xBEEF4u)   epv_exit(310);
  }

  /* E3: 被拦的 store 不许留下任何架构效果 (上面每格都回读过 0),
   * 而被允许的 load 一次都不该陷。 */
  arm_trap();
  v = pmp_blk[0];
  hx("  E3 inside region load value=", v);
  hx("  E3 inside region load cause(want 0)=", pmp_caught[0]);
  if (pmp_caught[0] != 0u) epv_exit(311);
  if (v != 0u)             epv_exit(312);

  /* E4: 锁定保护寄存器本身 —— 复位之前改不动。 */
  epv_wr_csr(CSR_PMPADDR0, out_addr >> 2);
  v = epv_rd_csr(CSR_PMPADDR0);
  hx("  E4 after lock pmpaddr0 readback=", v);
  hx("  E4 want (unchanged)=", (blk >> 2) | 1u);
  if (v != ((blk >> 2) | 1u)) epv_exit(313);
  epv_wr_csr(CSR_PMPCFG0, CFG(0, A_NAP, 0, 1, 1, 1) | CFG(1, A_NAP, 0, 1, 1, 1));
  v = epv_rd_csr(CSR_PMPCFG0);
  hx("  E4 after trying to clear L cfg0=", v);
  if ((v & 0x3Fu) != 0x3Bu) epv_exit(314);   /* L|R|W=0|X|A=NAPOT 必须原样立着 */

  /* E5: entry1 还在兜底 => 全机器照常。把 entry2/3 配成 OFF 也不许影响。 */
  arm_trap();
  pmp_free_box = 0x77u;
  v = pmp_free_box;
  hx("  E5 cause(want 0)=", pmp_caught[0]);
  hx("  E5 readback=", v);
  if (pmp_caught[0] != 0u || v != 0x77u) epv_exit(315);
  (void)A_OFF; (void)A_TOR; (void)PMP_CATCHALL_TOP;
}

/* 规范第 3.7.2 节第三条 (存在 locked 表项而本次访问无命中 => M 模式也失败)
 * 在本 SoC 上没有可观测窗口: 地址图之外的地址先被 addr_ok() 判掉, 报出来的是
 * 同一个 cause 5/7; 地址图之内的地址在上面这套配置里都被 entry1 兜底命中。
 * 所以这条只按「RTL 里实现了、未实测」写, 不并入任何结论。
 * 同理, TOR 与 NA4 的**强制行为**在本探针里也拿不到读数: 唯一可锁定的 entry0
 * 已经用于 NAPOT 边界测试, 而 L=0 的表项对 M 模式根本不生效。 */

int main(void)
{
  probe_counters();
  probe_pmp_warl();
  probe_pmp_enforce();
  epv_puts("MICRO_BISECT_OK\n");
  epv_exit(0);
  return 0;
}
