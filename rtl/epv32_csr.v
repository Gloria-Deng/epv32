// ============================================================================
//  epv32_csr.v  --  机器模式控制/状态寄存器 + 中断判定
//
//  支持: mstatus(MIE/MPIE/MPP)  mie  mip(只读合成)  mtvec(直接/向量化)
//        mscratch  mepc  mcause  mtval  misa  mvendorid  marchid  mimpid
//        mhartid  mcycle/mcycleh  minstret/minstreth  以及 U 型别名
//        cycle/cycleh/instret/instreth  和自定义 vcfg 只读镜像 0x7C0
//
//  两条刻意的设计决定 (写进 docs/01_设计规格.md 的「已知边界」一节):
//    1) 未定义的 CSR 地址: 读回 0、写忽略、不触发非法指令异常。
//       这样做的代价是 rv32mi/csr 里「访问保留地址应当陷入」的那一段期望
//       与本核行为不一致, 该用例会被记为不通过而不是被悄悄跳过。
//    2) 只有机器模式: MPP 恒为 2'b11, 对 mstatus 的 MPP 位写入被丢弃。
//
//  M9 新增 (两块都带自己的已知边界, 不许当成完整实现读):
//    3) 周期/退休计数器可写, 且「写入那一拍抑制自增」—— 规范要求的语义
//       (rv32mi/instret_overflow 考的就是这条)。抑制的是**写计数器那条指令
//       自己**的那一次自增, 由核把 exmem/memwb 的 cnt_wr 标志串下来后从
//       i_inst_retired 里扣掉, 本模块不知道流水级。
//       mcycle 不做同样的抑制: 它是墙钟周期数, 写入不该改变时间流逝;
//       用例也没考它。这句话是设计选择, 不是实测结论。
//    4) PMP: PMP_N 个表项、pmpcfg/pmpaddr 的 WARL 行为、OFF/TOR/NA4/NAPOT
//       四种模式、以及规范第 3.7.2 节那三条 M 模式隐含规则 (L=0 时 M 模式
//       无视权限位; 无锁定表项且无命中时 M 模式放行; 有锁定表项但无命中时
//       M 模式失败)。**只对 load/store 强制**, 取指侧的 X 位不判 ——
//       判它就得在 IF 造第二个陷入源, 而 3.2 节「全核只有一个陷入源」
//       是已经过穷举验证的结构, 不为这条可选项去换它。
//       粒度 G=0 (4 字节): pmpaddr 的高两位对应不存在的物理地址位, 读回恒 0,
//       于是「写全 1 再读回」的最低置位是 bit0。这条直接决定了
//       rv32mi/pmpaddr 走它自己注释里声明的平凡通过路径, 不是绕过了它。
// ============================================================================

module epv32_csr #(
  parameter IMPID = 32'h0000_0003,
  parameter PMP_N = 4                     // 实现 4 个表项 (pmpcfg0 / pmpaddr0..3)
)(
  input  wire        clk,
  input  wire        rst_n,
  // ---- CSR 读改写 (在 EX 级单拍完成)
  input  wire        i_csr_en,
  input  wire [1:0]  i_csr_op,      // 01=RW 10=RS 11=RC
  input  wire        i_csr_imm,     // 1 = 使用 5bit 无符号立即数形式 (csrrwi/si/ci)
  input  wire [11:0] i_addr,
  input  wire [31:0] i_wdata,
  output wire [31:0] o_rdata,       // 恒为写之前的旧值
  output wire        o_wr,
  output wire [31:0] o_mtvec,
  output wire [31:0] o_mtvec_base,
  output wire [31:0] o_mepc,
  output wire        o_mie,
  // ---- 陷入 / 返回
  input  wire        i_trap,
  input  wire [4:0]  i_cause,
  input  wire        i_is_irq,
  input  wire [31:0] i_trap_pc,
  input  wire [31:0] i_tval,
  input  wire        i_mret,
  // ---- 中断请求 (来自片上外设)
  input  wire        i_irq_timer,
  input  wire        i_irq_soft,
  input  wire        i_irq_ext,
  output wire        o_irq_any,
  output wire [31:0] o_irq_target,
  output wire [31:0] o_mret_target,
  // ---- 计数与可见性
  input  wire        i_inst_retired,
  input  wire        i_clk_tick,
  input  wire [31:0] i_vcfg,
  output wire [31:0] o_mcycle,
  output wire [31:0] o_minstret,
  output wire [31:0] o_dbg_mtval,
  output reg  [31:0] o_dbg_mcause,
  // ---- M9: PMP 判定 (只判 load/store; 取指侧 X 不判, 见文件头第 4 条)
  //      a1 只在一次访问跨两个字时存在 (核的 M6 拆两拍路径)。
  input  wire [31:0] i_pmp_a0,
  input  wire [31:0] i_pmp_a1,
  input  wire        i_pmp_a1_en,
  input  wire        i_pmp_rd_en,       // 本拍 EX 里有一条 load 需要判
  input  wire        i_pmp_wr_en,       // 本拍 EX 里有一条 store 需要判
  output wire        o_pmp_rd_deny,
  output wire        o_pmp_wr_deny
);
  reg [31:0] mstatus, mie, mtvec, mscratch, mepc, mcause, mtval;
  reg [63:0] mcycle;
  reg [63:0] minstret;

  // ================================================================ M9 计数与 PMP
  // mcountinhibit: 只实现 CY(bit0) 与 IR(bit2)。TM(bit1) 与本核无关 (不实现
  // time CSR), HPM3..31 无对应计数器 —— 全部读 0、写忽略, 见下面 WARL 掩码。
  reg [31:0] mcountinhibit;
  localparam [31:0] MCI_WARL = 32'h0000_0005;   // 只有 bit0(CY) 与 bit2(IR)

  localparam [7:0]  PMPCFG_WARL   = 8'h3F;      // L[0] R[1] W[2] X[3] A[5:4]
  localparam [31:0] PMPADDR_WARL  = 32'h3FFF_FFFF; // 高两位 = 不存在的地址位
  // A 字段编码 (priv 规范: PMP_NA4=0x10 => A 在 bit[5:4], 不在 bit[7:6])
  localparam [1:0]  A_OFF = 2'b00, A_TOR = 2'b01, A_NA4 = 2'b10, A_NAP = 2'b11;

  reg [7:0]  pmpcfg  [0:PMP_N-1];
  reg [31:0] pmpaddr [0:PMP_N-1];

  // ---- 单表项匹配 (a 是字节地址; 粒度 4 字节 => 一律在 addr[31:2] 域里比)
  function pmp_match;
    input integer k;
    input [31:0]  a;
    reg   [31:0]  m;
    reg   [29:0]  a2, lo2;
    begin
      a2  = a[31:2];
      // 下界取上一个表项; k==0 时下界是 0。索引一律夹在 [0,PMP_N-1] 内,
      // 不写 pmpaddr[k-1] 这种在 k=0 时会展开成负下标的形式 (综合器会当场拒)。
      lo2 = (k == 0) ? 30'd0 : pmpaddr[(k == 0) ? 0 : (k - 1)][29:0];
      case (pmpcfg[k][5:4])
        A_OFF:   pmp_match = 1'b0;
        A_TOR:   pmp_match = (a2 >= lo2) && (a2 < pmpaddr[k][29:0]);
        A_NA4:   pmp_match = (a2 == pmpaddr[k][29:0]);
        // NAPOT: pmpaddr 以 k 个 1 结尾, 掩码 = pmpaddr ^ (pmpaddr+1) 的低 k+1 位
        default: begin
          m = pmpaddr[k] ^ (pmpaddr[k] + 32'd1);
          pmp_match = (((a2 ^ pmpaddr[k][29:0]) & ~m[29:0]) == 30'd0);
        end
      endcase
    end
  endfunction

  // ---- 规范第 3.7.2 节的三条 M 模式隐含规则, 按优先编码器逐表项走一遍:
  //      命中的最低编号表项说了算; 它 L=0 则 M 模式无视权限位放行。
  //      一条都不命中时: 存在任何 locked 表项 => M 模式失败, 否则放行。
  //   这条不是「顺手写的」: 没有 locked 表项时全部放行, 才让既有 16 条
  //   rv32mi 与全部基准在复位状态下行为不变。
  function pmp_deny;
    input [31:0] a;
    input integer want;          // 1 = R, 2 = W
    integer i;
    reg     found, any_lock;
    begin
      found = 1'b0; any_lock = 1'b0; pmp_deny = 1'b0;
      for (i = 0; i < PMP_N; i = i + 1) begin
        if (pmpcfg[i][0]) any_lock = 1'b1;
        if (!found && pmp_match(i, a)) begin
          found = 1'b1;
          if (pmpcfg[i][0]) begin                 // 只有 L=1 才约束 M 模式
            if (want == 1)  pmp_deny = ~pmpcfg[i][1];
            else            pmp_deny = ~pmpcfg[i][2];
          end
        end
      end
      if (!found && any_lock) pmp_deny = 1'b1;
    end
  endfunction

  // 一次访问跨两个字时两半都要判 —— 与核里 x_bound 那条判据同口径
  // (「两半任一不行就整条按访问错误陷入」, 不是做到一半)。
  assign o_pmp_rd_deny = i_pmp_rd_en &
                         (pmp_deny(i_pmp_a0, 1) |
                          (i_pmp_a1_en & pmp_deny(i_pmp_a1, 1)));
  assign o_pmp_wr_deny = i_pmp_wr_en &
                         (pmp_deny(i_pmp_a0, 2) |
                          (i_pmp_a1_en & pmp_deny(i_pmp_a1, 2)));

  wire MIE_BIT  = mstatus[3];
  wire MPIE_BIT = mstatus[7];

  wire [31:0] mip = (i_irq_soft  ? 32'h0000_0008 : 32'h0)
                  | (i_irq_timer ? 32'h0000_0080 : 32'h0)
                  | (i_irq_ext   ? 32'h0000_0800 : 32'h0);

  wire [31:0] pending  = mie & mip;
  wire [31:0] en_pend  = MIE_BIT ? pending : 32'h0;
  assign o_irq_any     = (en_pend != 32'h0);

  // 优先级: 外部(11) > 计时(7) > 软件(3)
  function [3:0] pick;
    input [31:0] v;
    begin
      if      (v[11]) pick = 4'd11;
      else if (v[7])  pick = 4'd7;
      else if (v[3])  pick = 4'd3;
      else            pick = 4'd0;
    end
  endfunction
  wire [3:0] irq_id = pick(en_pend);

  wire [31:0] mtvec_base = {mtvec[31:2], 2'b00};
  /* RISC-V 规定 mtvec[1:0]: 0=Direct, 1=Vectored -- 选择位是 **bit0**。
   * 曾误用 mtvec[1]。本核只支持 mode 0/1, 其余按 Direct 处理。 */
  wire        vmode_vec = (mtvec[1:0] == 2'b01);
  assign o_irq_target  = vmode_vec ? (mtvec_base + {25'b0, irq_id, 2'b00})
                                  : mtvec_base;
  assign o_mret_target = {mepc[31:1], 1'b0};
  assign o_mtvec       = mtvec;
  assign o_mtvec_base  = mtvec_base;
  assign o_dbg_mtval   = mtval;
  assign o_mepc        = mepc;
  assign o_mie         = MIE_BIT;
  assign o_mcycle      = mcycle[31:0];
  assign o_minstret    = minstret[31:0];

  // ------------------------------------------------------------ 读
  reg [31:0] cur;
  always @* begin
    case (i_addr)
      12'h300: cur = mstatus & 32'h0000_1888;   // MPP/MPIE/MIE
      12'h301: cur = 32'h4000_1200;             // misa: MXL=1, I+M+X
      12'h304: cur = mie;
      12'h305: cur = mtvec;
      12'h340: cur = mscratch;
      12'h341: cur = mepc;
      12'h342: cur = mcause;
      12'h343: cur = mtval;
      12'h344: cur = mip;
      12'h320: cur = mcountinhibit & MCI_WARL;
      // pmpcfg0 打包表项 0..3 (entry0 在低位); pmpcfg1..3 对应的表项未实现 => 0
      12'h3A0: cur = {pmpcfg[3], pmpcfg[2], pmpcfg[1], pmpcfg[0]} & 32'h3F3F_3F3F;
      12'h3B0: cur = pmpaddr[0];
      12'h3B1: cur = pmpaddr[1];
      12'h3B2: cur = pmpaddr[2];
      12'h3B3: cur = pmpaddr[3];
      12'h7C0: cur = i_vcfg;
      12'hB00: cur = mcycle[31:0];
      12'hB80: cur = mcycle[63:32];
      12'hB02: cur = minstret[31:0];
      12'hB82: cur = minstret[63:32];
      12'hC00: cur = mcycle[31:0];
      12'hC80: cur = mcycle[63:32];
      12'hC02: cur = minstret[31:0];
      12'hC82: cur = minstret[63:32];
      12'hF11: cur = 32'h0;                     // mvendorid: 非商业实现
      12'hF12: cur = 32'h0;                     // marchid
      12'hF13: cur = IMPID;                     // mimpid
      12'hF14: cur = 32'h0;                     // mhartid
      default: cur = 32'h0;
    endcase
  end
  assign o_rdata = cur;

  // ------------------------------------------------------------ 写使能判定
  wire rw = (i_csr_op == 2'b01);
  wire rs = (i_csr_op == 2'b10);
  wire rc = (i_csr_op == 2'b11);
  // csrrs/csrrc 且源为 x0 (寄存器形式) => 只读, 不写
  assign o_wr = i_csr_en && (rw | ((rs | rc) && !(i_wdata == 32'h0 && !i_csr_imm)));

  wire [31:0] nxt = rc ? (cur & ~i_wdata)
                : rs ? (cur |  i_wdata)
                :        i_wdata;

  // ------------------------------------------------------------ 时序
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      mstatus <= 32'h0; mie <= 32'h0; mtvec <= 32'h0; mscratch <= 32'h0;
      mepc    <= 32'h0; mcause <= 32'h0; mtval <= 32'h0;
      mcycle  <= 64'h0; minstret <= 64'h0;
      mcountinhibit <= 32'h0;
      pmpcfg[0] <= 8'h0; pmpcfg[1] <= 8'h0;
      pmpcfg[2] <= 8'h0; pmpcfg[3] <= 8'h0;
      pmpaddr[0] <= 32'h0; pmpaddr[1] <= 32'h0;
      pmpaddr[2] <= 32'h0; pmpaddr[3] <= 32'h0;
    end else begin
      // M9: 自增可被 mcountinhibit 挡下 (CY 管 mcycle, IR 管 minstret)。
      // 复位值取 0 (计数器默认在跑), 规范允许实现自选, 这条写在 3.5 节边界里。
      if (i_clk_tick     && !mcountinhibit[0]) mcycle   <= mcycle   + 64'd1;
      if (i_inst_retired && !mcountinhibit[2]) minstret <= minstret + 64'd1;

      if (i_trap) begin
        o_dbg_mcause <= {i_is_irq, 26'b0, i_cause};
        mstatus[3]     <= 1'b0;                 // MIE  <- 0
        mstatus[7]     <= MIE_BIT;              // MPIE <- MIE
        mstatus[12:11] <= 2'b11;                // MPP  <- M
        mepc   <= i_trap_pc & 32'hFFFF_FFFE;
        mcause <= {i_is_irq, 26'b0, i_cause};
        mtval  <= i_tval;
      end else if (i_mret) begin
        mstatus[3]     <= MPIE_BIT;
        mstatus[7]     <= 1'b1;
        mstatus[12:11] <= 2'b11;
      end else if (o_wr) begin
        case (i_addr)
          12'h300: begin
            mstatus[3]     <= nxt[3];
            mstatus[7]     <= nxt[7];
            mstatus[12:11] <= 2'b11;            // 只有 M 模式, MPP 写被丢弃
          end
          12'h304: mie      <= nxt;
          12'h305: mtvec    <= {nxt[31:2], nxt[1:0]};
          12'h320: mcountinhibit <= nxt & MCI_WARL;
          12'h340: mscratch <= nxt;
          12'h341: mepc     <= {nxt[31:1], 1'b0};
          12'h342: mcause   <= nxt;
          12'h343: mtval    <= nxt;
          // ---- M9: 计数器可写。规范语义「写入那一拍的自增被抑制」由**核**
          //      保证 (它把 cnt_wr 标志串到退休那一拍再扣掉自增), 这里只管落值。
          12'hB00: mcycle[31:0]   <= nxt;
          12'hB80: mcycle[63:32]  <= nxt;
          12'hB02: minstret[31:0] <= nxt;
          12'hB82: minstret[63:32] <= nxt;
          // ---- M9: PMP。锁定的表项连自己的 cfg 和 addr 都不许改,
          //      且解锁只能靠复位 —— 这条是规范里 L 位的全部意义。
          12'h3A0: begin
            if (!pmpcfg[0][0]) pmpcfg[0] <= nxt[7:0]   & PMPCFG_WARL;
            if (!pmpcfg[1][0]) pmpcfg[1] <= nxt[15:8]  & PMPCFG_WARL;
            if (!pmpcfg[2][0]) pmpcfg[2] <= nxt[23:16] & PMPCFG_WARL;
            if (!pmpcfg[3][0]) pmpcfg[3] <= nxt[31:24] & PMPCFG_WARL;
          end
          12'h3B0: if (!pmpcfg[0][0]) pmpaddr[0] <= nxt & PMPADDR_WARL;
          12'h3B1: if (!pmpcfg[1][0]) pmpaddr[1] <= nxt & PMPADDR_WARL;
          12'h3B2: if (!pmpcfg[2][0]) pmpaddr[2] <= nxt & PMPADDR_WARL;
          12'h3B3: if (!pmpcfg[3][0]) pmpaddr[3] <= nxt & PMPADDR_WARL;
          default: ;                            // 只读或未定义: 忽略
        endcase
      end
    end
  end

  wire _unused = &{1'b0, rw, 1'b0};

endmodule
