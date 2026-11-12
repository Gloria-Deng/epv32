/* ============================================================================
 *  epv32_hw.h  --  EPV32 SoC 裸机寄存器定义 + EPV 自定义指令 intrinsics
 *
 *  为什么用 .insn 而不是 .word
 *  ----------------------------
 *  EPV 指令的两类操作数来源不同:
 *    * 向量寄存器号 v0..v7 由程序员在源码里写死 -> 是编译期常量;
 *    * 参与运算的标量地址/数据在通用寄存器里 -> 由编译器分配。
 *  ".word <C 表达式>" 这条路走不通: 汇编器读不懂 C 的类型转换, 而把整条指令
 *  写成字面 16 进制又要求 GPR 号也是常量 (等于要手工分配寄存器)。
 *  GNU as 的 .insn 支持任意 opcode + 寄存器/立即数操作数, 正好把两者分开:
 *  向量号写成 x<N> 常量 token (汇编器只往位域里填编号 N, 本核把 [11:7]/[24:20]/
 *  [19:15] 的低 3 位解释成向量号), 标量操作数交给 %0/%1 由编译器分配。
 *  每条 intrinsic 的机器码由 tools/check_encoding_consistency.py 真汇编 +
 *  反汇编, 与 rtl/epv32_defs.vh 的表逐位比对, 不由这里的人眼判断。
 *
 *  编码真源: rtl/epv32_defs.vh; 黄金模型: tb/golden/epv_iss.py。
 * ==========================================================================*/
#ifndef EPV32_HW_H
#define EPV32_HW_H

#include <stdint.h>

/* ------------------------------------------------------------- 地址图 */
#define EPV_RAM_BASE      0x80000000u
#define EPV_RAM_SIZE      0x00040000u          /* 256 KiB */
#define EPV_STACK_TOP     (EPV_RAM_BASE + EPV_RAM_SIZE)

#define EPV_UART_DATA     ((volatile uint32_t *)0x90000000u)
#define EPV_UART_STATUS   ((volatile uint32_t *)0x90000004u)
#define EPV_UART_DIV      ((volatile uint32_t *)0x90000008u)
#define EPV_TIMER_MTIME   ((volatile uint32_t *)0x90010000u)
#define EPV_TIMER_MTIMEHI ((volatile uint32_t *)0x90010004u)
#define EPV_TIMER_CMP     ((volatile uint32_t *)0x90010008u)
#define EPV_TIMER_CMPHI   ((volatile uint32_t *)0x9001000Cu)
#define EPV_SYSC_COMMIT   ((volatile uint32_t *)0x90020000u)
#define EPV_SYSC_SYSID    ((volatile uint32_t *)0x90020008u)

#define EPV_STATUS_TX_IDLE  1u
#define EPV_STATUS_RX_VALID 2u

/* ------------------------------------------------------------- CSR */
#define CSR_MSTATUS   0x300
#define CSR_MISA      0x301
#define CSR_MIE       0x304
#define CSR_MTVEC     0x305
#define CSR_MSCRATCH  0x340
#define CSR_MEPC      0x341
#define CSR_MCAUSE    0x342
#define CSR_MTVAL     0x343
#define CSR_MIP       0x344
#define CSR_MCYCLE    0xB00
#define CSR_MCYCLEH   0xB80
#define CSR_MINSTRET  0xB02
#define CSR_MINSTRETH 0xB82
#define CSR_VCFG      0x7C0

#define MSTATUS_MIE   (1u << 3)
#define MSTATUS_MPIE  (1u << 7)
#define MIE_MTIP      (1u << 7)
#define MIE_MSIP      (1u << 3)

/* ------------------------------------------------------ vcfg 位域 */
#define VCFG_S(x)     ((((uint32_t)(x)) & 0x3Fu) << 0)
#define VCFG_RB(x)    ((((uint32_t)(x)) & 0xFFu) << 8)
#define VCFG_RND      (1u << 16)
#define VCFG_SAT      (1u << 17)

/* ------------------------------------------------------ CSR 读写 */
static inline uint32_t epv_rd_csr(uint16_t a) {
  uint32_t v;
  /* 走标准助记符 (需 -march 带 _zicsr)。实测本机 as 会拒绝
   * ".insn i 0x73, 2, ..." 这种写法 (CSR 编号校验报 extension 缺失),
   * 所以 CSR 不套用自定义指令的 .insn 通道。 */
  __asm__ volatile ("csrr %0, %1" : "=r"(v) : "i"(a));
  return v;
}
static inline void epv_wr_csr(uint16_t a, uint32_t v) {
  __asm__ volatile ("csrw %0, %1" :: "i"(a), "r"(v) : "memory");
}
static inline uint64_t epv_rd_cycle(void) {
  uint32_t lo, hi, hi2;
  do { hi = epv_rd_csr(CSR_MCYCLEH); lo = epv_rd_csr(CSR_MCYCLE);
       hi2 = epv_rd_csr(CSR_MCYCLEH); } while (hi != hi2);
  return ((uint64_t)hi << 32) | lo;
}
static inline uint64_t epv_rd_instret(void) {
  uint32_t lo, hi, hi2;
  do { hi = epv_rd_csr(CSR_MINSTRETH); lo = epv_rd_csr(CSR_MINSTRET);
       hi2 = epv_rd_csr(CSR_MINSTRETH); } while (hi != hi2);
  return ((uint64_t)hi << 32) | lo;
}

/* ==========================================================================
 *  EPV 17 条自定义指令
 *    CUSTOM0 = 0x0B (搬运/配置, funct3 选)
 *    CUSTOM1 = 0x2B (向量计算, funct7 选, funct3 恒 0)
 *  约定: 所有 v* 形参必须是字面量 0..7。传变量会在汇编期报「invalid register」,
 *  而不是悄悄编出错指令 —— 这是刻意的失败方向。
 * ==========================================================================*/

/* ---- CUSTOM0 ---- */
/* EPV.LVB vd, off(rs1): 16B 内存 -> vd, 4 次整字访问。
 * 编码用 R 型: 向量号放 rs2 字段 [24:20], 基址放 rs1 [19:15], 偏移由 C 折进
 * 基址寄存器。原因: 本机 GAS 对 ".insn i" 的 simm12 操作数会填入别的东西
 * (实测 epv_lvb(0,0,bs) 编出 0x0004800b, imm 段是 72 而不是 0), 而 ".insn r"
 * 的六个字段逐位核对正确 (见 tools/check_encoding_consistency.py)。 */
#define epv_lvb(vd, off, rs1) \
  __asm__ volatile (".insn r 0x0B, 0, 0, x0, %0, x" #vd \
                    :: "r"((uint32_t)((uint32_t)(rs1) + (uint32_t)(off))) : "memory")
/* EPV.SVB vs3, off(rs1): vs3 -> 16B 内存, 4 次整字写。
 * 源向量号同样在 [24:20]。RTL 侧 epv32_epv.v 的 svb_src = vrf[i_rs2f[2:0]]
 * 与此一致。 */
#define epv_svb(vs3, off, rs1) \
  __asm__ volatile (".insn r 0x0B, 1, 0, x0, %0, x" #vs3 \
                    :: "r"((uint32_t)((uint32_t)(rs1) + (uint32_t)(off))) : "memory")
/* ---- SVB 尾组留白: SVB 一次写满 16 字节, 尾组会越出逻辑长度最多 12 字节,
 * 曾越界踩坏紧随其后的 .bss 静态量。缓冲一律用 EPV_SVB_BUF 声明: 少了尾置就
 * 编译失败; 尺寸与从前手写的 "[N + 16]" 逐字节相同, .bss 布局与周期数不变。
 * 判据 sizeof(name) >= (n) + EPV_SVB_TAIL, 用 sizeof(无符号) 而非 size_t
 * (裸机侧只保证有 stdint.h)。两条宏都以分号结尾, 可一行连写两个声明。 */
#define EPV_SVB_TAIL 16
#define EPV_SVB_BUF(name, n) \
  int8_t name[(n) + EPV_SVB_TAIL]; \
  EPV_SVB_HAS_TAIL(name, n)
#define EPV_SVB_HAS_TAIL(name, n) \
  _Static_assert(sizeof(name) >= (n) + EPV_SVB_TAIL, \
                 "EPV.SVB 16-byte tail group would run past this buffer");
/* 已经用别的方式声明的缓冲, 单独挂这一条也能钉住。 */
/* EPV.MVX vd, rs1: GPR 广播到 4 x int32 */
#define epv_mvx(vd, rs1) \
  __asm__ volatile (".insn r 0x0B, 2, 0, x" #vd ", %0, x0" :: "r"(rs1) : "memory")
/* EPV.XMV rd, vs2: vs2 的 lane0 (int32) -> GPR */
#define epv_xmv(rd, vs2) \
  __asm__ volatile (".insn r 0x0B, 3, 0, %0, x0, x" #vs2 : "=r"(rd))
/* EPV.CFG rs1: GPR -> vcfg */
#define epv_cfg(rs1) \
  __asm__ volatile (".insn r 0x0B, 4, 0, x0, %0, x0" :: "r"(rs1) : "memory")
/* EPV.CFGR rd: vcfg -> GPR */
#define epv_cfgr(rd) \
  __asm__ volatile (".insn r 0x0B, 5, 0, %0, x0, x0" : "=r"(rd))

/* ---- CUSTOM1 ---- */
#define epv_vdot8(rd, vs1, vs2) \
  __asm__ volatile (".insn r 0x2B, 0, 1, %0, x" #vs1 ", x" #vs2 : "=r"(rd))
#define epv_vmac4(vd, vs1, vs2) \
  __asm__ volatile (".insn r 0x2B, 0, 2, x" #vd ", x" #vs1 ", x" #vs2 ::: "memory")
#define epv_vadd8(vd, vs1, vs2) \
  __asm__ volatile (".insn r 0x2B, 0, 3, x" #vd ", x" #vs1 ", x" #vs2 ::: "memory")
#define epv_vreq4(vd, vs1, vs2) \
  __asm__ volatile (".insn r 0x2B, 0, 4, x" #vd ", x" #vs1 ", x" #vs2 ::: "memory")
#define epv_vmax8(vd, vs1, vs2) \
  __asm__ volatile (".insn r 0x2B, 0, 5, x" #vd ", x" #vs1 ", x" #vs2 ::: "memory")
#define epv_vrelu8(vd, vs1) \
  __asm__ volatile (".insn r 0x2B, 0, 6, x" #vd ", x" #vs1 ", x0" ::: "memory")
#define epv_vsra4(vd, vs1) \
  __asm__ volatile (".insn r 0x2B, 0, 7, x" #vd ", x" #vs1 ", x0" ::: "memory")
#define epv_vzero(vd) \
  __asm__ volatile (".insn r 0x2B, 0, 8, x" #vd ", x0, x0" ::: "memory")
#define epv_vpool2(vd, vs1) \
  __asm__ volatile (".insn r 0x2B, 0, 9, x" #vd ", x" #vs1 ", x0" ::: "memory")
#define epv_vsub8(vd, vs1, vs2) \
  __asm__ volatile (".insn r 0x2B, 0, 10, x" #vd ", x" #vs1 ", x" #vs2 ::: "memory")
#define epv_vmin8(vd, vs1, vs2) \
  __asm__ volatile (".insn r 0x2B, 0, 11, x" #vd ", x" #vs1 ", x" #vs2 ::: "memory")

/* ------------------------------------------------------ 平台服务 */
void epv_putc(char c);
void epv_puts(const char *s);
void epv_putu(uint32_t v);
void epv_putu64(uint64_t v);
void epv_puti(int32_t v);
void epv_putx(uint32_t v);
void epv_exit(int code);      /* 写 SYSC COMMIT -> 结束仿真 */

#endif /* EPV32_HW_H */
