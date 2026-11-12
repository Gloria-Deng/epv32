// ============================================================================
//  epv32_defs.vh  --  EPV32 SoC 全局常量 / 操作码 / 自定义指令编码
//
//  本文件是 rtl / tb/golden ISS / sw intrinsics 头 三方共享的唯一编码真源。
//  修改任何一个 funct 值都必须同时改 tb/golden/epv_iss.py 与
//  sw/inc/epv32_hw.h，tools/check_encoding_consistency.py 会在回归里
//  逐条比对这三处的编码表，任一处不一致即判红。
//
//  标准 RV32I / RV32M / Zicsr 主操作码遵循 The RISC-V Instruction Set
//  Manual Volume I (riscv-privileged/unprivileged, 见 docs/refs/SOURCES.md)。
//  自定义指令使用 RISC-V 保留的 CUSTOM0 / CUSTOM1 主操作码。
// ============================================================================
`ifndef EPV32_DEFS_VH
`define EPV32_DEFS_VH

// ---------------------------------------------------------------- 主操作码
`define OP_LUI      7'h37
`define OP_AUIPC    7'h17
`define OP_JAL      7'h6F
`define OP_JALR     7'h67
`define OP_BRANCH   7'h63
`define OP_LOAD     7'h03
`define OP_STORE    7'h23
`define OP_OPIMM    7'h13
`define OP_OP       7'h33
`define OP_FENCE    7'h0F
`define OP_SYSTEM   7'h73
`define OP_CUSTOM0  7'h0B   // 保留给自定义扩展 (RISC-V Unprivileged, Ch.27)
`define OP_CUSTOM1  7'h2B   // 保留给自定义扩展

// ---------------------------------------------------------------- ALU 操作
`define ALU_ADD  4'd0
`define ALU_SUB  4'd1
`define ALU_SLL  4'd2
`define ALU_SLT  4'd3
`define ALU_SLTU 4'd4
`define ALU_XOR  4'd5
`define ALU_SRL  4'd6
`define ALU_SRA  4'd7
`define ALU_OR   4'd8
`define ALU_AND  4'd9
`define ALU_MUL  4'd10   // 走 muldiv 单元, ALU 结果忽略
`define ALU_DIV  4'd11   // 走 muldiv 单元
`define ALU_PASS 4'd15   // 直通 rs1 (LUI / CSR 读)

// -------------------------------------------------------------- MUL/DIV 选择
`define MD_MUL     2'd0   // 有符号低 32
`define MD_MULH    2'd1   // 有符号高 32
`define MD_MULHU   2'd2   // 无符号高 32
`define MD_MULHSU  2'd3   // rs1 有符号 x rs2 无符号, 高 32

// ============================================================ EPV 扩展编码
//  扩展名: EPV = "Embedded Packed-Vector" 自定义指令扩展
//  数据通路: 8 x 128bit 向量寄存器 (v0..v7), 每寄存器 16 lane int8 /
//            8 lane int16 / 4 lane int32; 外加 1 个 32bit 配置寄存器 vcfg。
//  所有 EPV 指令均在流水级 EX 完成 (访存类多周期), 向量寄存器只在 EX 读/写。

// ---- vcfg (由 EPV.CFG / EPV.CFGR 读写的 32bit 配置寄存器) ----
//   vcfg[ 7: 0] = S      算术右移位数, 仅 0..31 有意义, 大于 31 按 31 饱和
//   vcfg[15: 8] = RB     重量化后叠加的输出偏置 (有符号 int8, 非对称量化的 zero-point)
//   vcfg[16]    = RND    1=移位前加 1<<(S-1) 做四舍五入, 0=算术右移直接截断
//   vcfg[17]    = SAT    1=VADD8/VSUB8 饱和到 [-128,127], 0=按 int8 回卷
//   vcfg[31:18] = 保留, 读为 0, 写忽略

// ---- CUSTOM0 (0x0B) : 搬运 / 配置类。字段布局见下行注释 ----
//  I-type: [31:20]=imm12  [19:15]=rs1   [14:12]=funct3  [11:7]=vd/rd  [6:0]=op
//  R-type: [31:25]=funct7 [24:20]=rs2/vs [19:15]=rs1   [14:12]=funct3 [11:7]=vd/rd
`define EPV_F3_LVB   3'd0   // EPV.LVB   vd, imm(rs1)   16B 内存 -> vd   (4 次总线读)
`define EPV_F3_SVB   3'd1   // EPV.SVB   vs3, imm(rs1)  vs3 -> 16B 内存 (4 次总线写); vs3 在 [11:7] 字段 (I 型, 见下方说明)
`define EPV_F3_MVX   3'd2   // EPV.MVX   vd, rs1        GPR 广播到 4 x int32 lane
`define EPV_F3_XMV   3'd3   // EPV.XMV   rd, vs2        vs2 lane0 (int32) -> GPR
`define EPV_F3_CFG   3'd4   // EPV.CFG   rs1            GPR -> vcfg
`define EPV_F3_CFGR  3'd5   // EPV.CFGR  rd             vcfg -> GPR
`define EPV_N_C0     6      // CUSTOM0 已定义子编码条数 (funct3 = 0..5)

// ---- CUSTOM1 (0x2B) : 向量计算类, 全 R-type, funct3 恒为 0 ----
`define EPV_F7_VDOT8  7'd1  // EPV.VDOT8  rd, vs1, vs2   16 lane int8 点积 -> int32 GPR
`define EPV_F7_VMAC4  7'd2  // EPV.VMAC4  vd, vs1, vs2   vd.w[j] += dot(vs1.b[4j+:4],vs2.b[4j+:4])
`define EPV_F7_VADD8  7'd3  // EPV.VADD8  vd, vs1, vs2   16 lane int8 加 (SAT 决定饱和/回卷)
`define EPV_F7_VREQ4  7'd4  // EPV.VREQ4  vd, vs1, vs2   4 x int32 -> 4 x int8 逐通道重量化
`define EPV_F7_VMAX8  7'd5  // EPV.VMAX8  vd, vs1, vs2   16 lane int8 逐元素取大
`define EPV_F7_VRELU8 7'd6  // EPV.VRELU8 vd, vs1        16 lane int8 逐元素 max(x,0)
`define EPV_F7_VSRA4  7'd7  // EPV.VSRA4  vd, vs1        4 x int32 算术右移 vcfg.S (可舍入)
`define EPV_F7_VZERO  7'd8  // EPV.VZERO  vd             向量寄存器清零
`define EPV_F7_VPOOL2 7'd9  // EPV.VPOOL2 vd, vs1        相邻 int8 两两取大 -> 低 8 lane
`define EPV_F7_VSUB8  7'd10 // EPV.VSUB8  vd, vs1, vs2   16 lane int8 减
`define EPV_F7_VMIN8  7'd11 // EPV.VMIN8  vd, vs1, vs2   16 lane int8 逐元素取小
`define EPV_N_C1      11    // CUSTOM1 已定义 funct7 条数
`define EPV_N_INSTR   17    // 参与编码一致性核对的 EPV 指令总条数 = 6 + 11
`define EPV_VRF_WORDS 8     // 向量寄存器个数 (v0..v7, 128bit each)
`define CSR_VCFG      12'h7C0   // 只读镜像: 通过 CSRR 读回 vcfg (便于与 CSR 链混用)

// ---------------------------------------------------------------- SoC 地址图
`define ADDR_RAM_BASE   32'h8000_0000
`define ADDR_RAM_LAST   32'h8003_FFFF   // 256 KiB
`define ADDR_UART_BASE  32'h9000_0000
`define ADDR_UART_LAST  32'h9000_003F
`define ADDR_TIMER_BASE 32'h9001_0000
`define ADDR_TIMER_LAST 32'h9001_003F
`define ADDR_SYSC_BASE  32'h9002_0000
`define ADDR_SYSC_LAST  32'h9002_003F

// UART 寄存器偏移
`define UART_OF_DATA    6'h0   // [7:0] tx 数据 / rx 数据
`define UART_OF_STATUS  6'h4   // bit0 = tx idle, bit1 = rx valid
`define UART_OF_DIV     6'h8   // 波特率分频

// TIMER (机器模式计时器) 寄存器偏移
`define TMR_OF_MTIME    6'h0   // 低 32bit, 每周期 +1
`define TMR_OF_MTIMEHI  6'h4
`define TMR_OF_CMP      6'h8
`define TMR_OF_CMPHI    6'hC

// SYSC (平台控制) 寄存器偏移
`define SYSC_OF_COMMIT  6'h0   // 写任意值 = 提交/结束仿真 (tb 捕获)
`define SYSC_OF_SYSID   6'h8   // 只读, 平台签名
`define SYSC_OF_CTRL    6'h4   // bit0 = 允许 commit 结束仿真

// ---------------------------------------------------------------- 异常因果
`define EXC_IAF      5'd1    // instruction access fault
`define EXC_ILLEGAL  5'd2
`define EXC_IBREAK   5'd3
`define EXC_LAMF     5'd4    // load address misaligned
`define EXC_LAF      5'd5    // load access fault
`define EXC_SAMF     5'd6
`define EXC_SAF      5'd7
`define EXC_ECM      5'd11   // ecall from machine mode

// ---------------------------------------------------------------- mcause 高位的中断编码
`define IRQ_M_BIT    31      // mcause[31] = 1 表示中断
`define IRQ_M_TIMER  3'd7
`define IRQ_M_SOFT   3'd3
`define IRQ_M_EXT    3'd11
`define IRQ_M_EXT_NUM 5'd11

`endif // EPV32_DEFS_VH
