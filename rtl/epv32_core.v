// ============================================================================
//  epv32_core.v  --  EPV32-C5: 5 级流水 RV32IM + Zicsr + EPV 自定义扩展核
//
//  级: IF 取指 | ID 译码·读寄存器 | EX 执行·分支·陷入判定 | MEM 访存 | WB 写回
//
//  五条刻意的微架构决定 (对应 docs/01_设计规格.md 的条目号):
//   M1 所有精确陷入都在 EX 判定。EX 自带地址译码 addr_ok(), 在真正访存之前
//      就把「该地址不存在」判出来, 于是全核只有一个陷入源, 不需要在 MEM 级
//      做二次冲刷。代价: 译码逻辑复制一份, 面积换验证简易度。
//   M2 乘 1 拍 / 除 32 拍 / EPV 搬运多周期, 共用一条 ex_stall: 冻结 PC 与
//      IF/ID、ID/EX, 同时向 EX/MEM 注入气泡, 完成拍才放真结果。
//      由此向量寄存器无需前递网络 (EPV 只在 EX 读、只在完成拍写)。
//   M3 分支在 EX 裁决, 静态预测「不跳」, 误判冲刷 IF/ID 与 ID/EX 两级。
//   M4 陷入/中断/分支/跳转的重定向一律排除在 ex_stall 期内, 保证一条多周期
//      指令不会中途被丢掉 (trap_fire 显式与 !ex_stall 相与)。
//   M5 FENCE / FENCE.I / WFI 按 NOP; 只有机器模式; 中断响应点 = EX 那条指令
//      提交之前。这三条记入「已知边界」清单, 不当作已实现特性宣称。
//   M6 非对齐访存按「拆成两次对齐访问」实现 (SUPPORT_UNALIGNED=1): MEM 多占
//      一拍, 读侧用 64bit 窗口拼接、写侧用 64bit 字节通道掩码分两拍写。规范
//      允许硬件处理与陷入两种做法, 这里把另一种留成参数档 (=0 时按 cause
//      4/6 精确陷入, mtval 给原始地址)。跨字方向的合法性也在 EX 判: 只要
//      任一半落在校验范围外就整体陷入, 不许悄悄写坏邻字。
// ============================================================================
`include "epv32_defs.vh"

module epv32_core #(
  parameter EPV_ENABLE = 1,
  parameter SUPPORT_UNALIGNED = 1,
  parameter IMPID      = 32'h0000_0003
)(
  input  wire        clk,
  input  wire        rst_n,
  output wire [31:0] o_imem_addr,
  input  wire [31:0] i_imem_rdata,
  output wire [31:0] o_dmem_addr,
  output wire [31:0] o_dmem_wdata,
  output wire [3:0]  o_dmem_be,
  output wire        o_dmem_we,
  output wire        o_dmem_en,
  input  wire [31:0] i_dmem_rdata,
  input  wire        i_irq_timer,
  input  wire        i_irq_soft,
  input  wire        i_irq_ext,
  output wire        o_tr_valid,
  output wire [31:0] o_tr_pc,
  output wire [4:0]  o_tr_rd,
  output wire [31:0] o_tr_wdata,
  output wire        o_tr_regwr,
  output wire        o_tr_memwr,
  output wire [31:0] o_tr_addr,
  output wire [31:0] o_tr_mdata,
  output wire [3:0]  o_tr_mbe,
  output wire [31:0] o_mcycle,
  output wire [31:0] o_minstret,
  output wire        o_mie,
  // ---- 陷入可见性: 测试台据此把「哪条指令为什么陷」写进归档, 不靠猜
  output reg  [31:0] o_dbg_mepc,
  output reg  [31:0] o_dbg_mtval,
  output reg  [31:0] o_dbg_mcause,
  output reg  [31:0] o_dbg_last_inst,
  output reg  [31:0] o_dbg_trap_cnt
);

  // ================================================================ 工具函数
  function addr_ok;
    input [31:0] a;
    begin
      /* addr_ok 只回答「地址在不在地址图上」, 不做对齐检查 —— 对齐不是架构规则:
       * 子字访存天然可落在字内任意字节, 由字节通道掩码 m_be64 与 ld_ext() 处理。
       * 曾额外要求 a[1:0]==0, 使 rv32ui 的 8 个子字访存用例全部 cause=5 陷入。 */
      addr_ok = ( (a >= `ADDR_RAM_BASE   && a <= `ADDR_RAM_LAST)   ||
          (a >= `ADDR_UART_BASE  && a <= `ADDR_UART_LAST)  ||
          (a >= `ADDR_TIMER_BASE && a <= `ADDR_TIMER_LAST) ||
          (a >= `ADDR_SYSC_BASE  && a <= `ADDR_SYSC_LAST) );
    end
  endfunction

  /* be_of() 已删除: M6 之后字节通道由 64bit 窗口掩码 m_be64 一处算出, 对齐与
   * 非对齐共用同一条式子。旧教训搬到 m_widen 注释上 —— SH 决定选高半字的是
   * **bit1** 而不是 bit0 (bit0 对合法 SH 恒为 0)。 */

  function [31:0] ld_ext;
    input [2:0]  f3;
    input [1:0]  off;
    input [31:0] d;
    reg  [7:0]   b;
    reg  [15:0]  h;
    begin
      b = d[8*off +: 8];
      h = off[1] ? d[31:16] : d[15:0];
      case (f3)
        3'd0:    ld_ext = {{24{b[7]}},  b};
        3'd1:    ld_ext = {{16{h[15]}}, h};
        3'd4:    ld_ext = {24'b0, b};
        3'd5:    ld_ext = {16'b0, h};
        default: ld_ext = d;
      endcase
    end
  endfunction

  // ================================================================ 声明
  reg  [31:0] pc_q;
  wire [31:0] pc    = pc_q;
  wire [31:0] inst  = i_imem_rdata;

  reg         ifid_valid;
  reg  [31:0] ifid_pc, ifid_pc4, ifid_inst;

  wire [6:0]  opcode = ifid_inst[6:0];
  wire [2:0]  f3     = ifid_inst[14:12];
  wire [6:0]  f7     = ifid_inst[31:25];
  wire [4:0]  rd_f   = ifid_inst[11:7];
  wire [4:0]  rs1_f  = ifid_inst[19:15];
  wire [4:0]  rs2_f  = ifid_inst[24:20];
  wire [31:0] imm_i   = {{20{ifid_inst[31]}}, ifid_inst[31:20]};
  wire [31:0] imm_s   = {{20{ifid_inst[31]}}, ifid_inst[31:25], ifid_inst[11:7]};
  wire [31:0] imm_b   = {{19{ifid_inst[31]}}, ifid_inst[31], ifid_inst[7],
                         ifid_inst[30:25], ifid_inst[11:8], 1'b0};
  wire [31:0] imm_u   = {ifid_inst[31:12], 12'b0};
  wire [31:0] imm_j   = {{11{ifid_inst[31]}}, ifid_inst[31], ifid_inst[19:12],
                         ifid_inst[20], ifid_inst[30:21], 1'b0};
  wire [31:0] imm_csr = {27'b0, ifid_inst[19:15]};
  wire [11:0] sys_code  = ifid_inst[31:20];   // 位宽 = 该字段本身的 12 位 (原写 20 位 ⇒ 零扩展)

  wire        uses_rs1, uses_rs2, illegal_id, op_custom;
  wire        rf_we;
  wire [4:0]  rf_waddr;
  wire [31:0] rf_wdata, rf_r1, rf_r2;

  // ID 级组合控制
  reg        d_reg_write, d_mem_read, d_mem_write, d_is_ldst, d_is_md, d_is_epv;
  reg        d_is_csr, d_is_jmp, d_is_br, d_wr_pc4, d_illegal, d_jal_is;
  reg        d_do_ebreak, d_do_ecall, d_do_mret, d_do_wfi;
  reg [3:0]  d_alu_op;
  reg        d_a_pc, d_a_immu, d_b_imm;
  reg [2:0]  d_md_op;
  reg [31:0] d_imm;

  // ID/EX 寄存器

  reg        idex_valid, idex_reg_write, idex_mem_read, idex_mem_write;
  reg        idex_is_ldst, idex_is_md, idex_is_epv, idex_is_csr, idex_is_jmp;
  reg        idex_is_br, idex_wr_pc4, idex_illegal, idex_do_ebreak;
  reg        idex_do_ecall, idex_do_mret, idex_jal_is;
  reg [3:0]  idex_alu_op;
  reg        idex_a_pc, idex_a_immu, idex_b_imm;
  reg [2:0]  idex_md_op, idex_f3;
  reg [6:0]  idex_f7, idex_opcode;
  reg [4:0]  idex_rd, idex_rs1, idex_rs2;
  reg [31:0] idex_pc, idex_pc4, idex_imm, idex_inst, idex_r1d, idex_r2d;

  // EX/MEM 寄存器
  reg        exmem_valid, exmem_reg_write, exmem_mem_read, exmem_mem_write;
  reg [4:0]  exmem_rd;
  reg [2:0]  exmem_f3;
  reg [31:0] exmem_result, exmem_rs2, exmem_addr, exmem_pc;

  // MEM/WB 寄存器
  reg        memwb_valid, memwb_reg_write, memwb_mem_write;
  reg [4:0]  memwb_rd;
  reg [3:0]  memwb_be;
  reg [31:0] memwb_wdata, memwb_addr, memwb_raw, memwb_pc;

  // EX 级连线
  wire [31:0] fwd1, fwd2, alu_a, alu_b, alu_y, mem_addr, br_target, jr_target;
  wire [31:0] md_result, ex_result;
  wire        md_done, md_busy;
  wire [31:0] epv_xres, epv_maddr, epv_mwdata, epv_vcfg, csr_rdata, csr_mcycle;
  wire [31:0] csr_minstret, csr_irq_target, csr_mret_target, csr_mtvec_base;
  wire        epv_xwr, epv_done, epv_busy, epv_illegal, epv_vrf_wr;
  wire        epv_mreq, epv_mwe, epv_grant, csr_wr_now, csr_irq_any, csr_mie;
  wire [127:0] epv_v0;

  wire        ex_stall, hazard, pc_stall, hold_ifid, flush_ifid, flush_idex;
  /* M6: 跨字访问的 MEM 相位。phase=0 那一拍读/写低字并冻结前端, phase=1 拍
   * 读/写高字并把合并结果交给 WB。声明放在这里是因为 ex_stall 的赋值在前。 */
  reg         mem_x_phase;
  reg  [31:0] mem_x_lo;
  wire        mem_hold, m_cross;
  wire        ex_exc, irq_now, trap_fire, mret_go, branch_take, jump_take;
  wire        redirect, ex_commit, memop_in_mem;
  wire [4:0]  exc_cause;
  wire [31:0] exc_tval;
  wire [31:0] trap_target;

  assign o_imem_addr = pc;
  assign o_mcycle    = csr_mcycle;
  assign o_minstret  = csr_minstret;
  assign o_mie       = csr_mie;
  wire [31:0] csr_dbg_mtval;
  wire [31:0] csr_dbg_mcause;
  wire [31:0] csr_mepc_w;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      o_dbg_mepc <= 32'h0; o_dbg_mtval <= 32'h0; o_dbg_mcause <= 32'h0;
      o_dbg_last_inst <= 32'h0; o_dbg_trap_cnt <= 32'h0;
    end else if (trap_fire) begin
      o_dbg_mepc    <= idex_pc;
      o_dbg_mtval   <= exc_tval;   /* CSR 里那份同拍才更新, 读它会拿到旧值 */
      o_dbg_mcause  <= csr_dbg_mcause;
      o_dbg_last_inst <= idex_inst;
      o_dbg_trap_cnt  <= o_dbg_trap_cnt + 32'd1;
    end
  end

  // ================================================================ IF/ID
  assign pc_stall = hazard || ex_stall;
  assign hold_ifid = ex_stall || hazard;
  assign flush_ifid = redirect;
  assign flush_idex = redirect || hazard;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      ifid_valid <= 1'b0; ifid_pc <= `ADDR_RAM_BASE;
      ifid_pc4   <= `ADDR_RAM_BASE + 32'd4; ifid_inst <= 32'h00000013;
    end else if (flush_ifid) begin
      ifid_valid <= 1'b0; ifid_inst <= 32'h00000013;
      ifid_pc <= pc_q; ifid_pc4 <= pc_q + 32'd4;
    end else if (!hold_ifid) begin
      ifid_valid <= 1'b1;
      ifid_pc    <= pc;  ifid_pc4 <= pc + 32'd4;
      ifid_inst  <= inst;
    end
  end

  // ================================================================ ID 译码
  assign op_custom = (opcode == `OP_CUSTOM0) || (opcode == `OP_CUSTOM1);
  wire op_known = (opcode==`OP_LUI)|(opcode==`OP_AUIPC)|(opcode==`OP_JAL)
                |(opcode==`OP_JALR)|(opcode==`OP_BRANCH)|(opcode==`OP_LOAD)
                |(opcode==`OP_STORE)|(opcode==`OP_OPIMM)|(opcode==`OP_OP)
                |(opcode==`OP_FENCE)|(opcode==`OP_SYSTEM);
  /* M8: RV32 里 SLLI 要求 inst[31:25]==0; SRLI/SRAI 要求 inst[31:25] ∈ {0,0x20}
   * (bit30 是算术/逻辑选择位, bit25=shamt[5] 必须为 0)。上版只核 inst[31:26]
   * 漏了 bit25, 且没管 SLLI, rv32mi/shamt 的 slli a0,a0,32 不陷入。 */
  wire imm_op_bad = (opcode==`OP_OPIMM) &&
                    ((f3==3'd1 && f7 != 7'h00) ||
                     (f3==3'd5 && f7 != 7'h00 && f7 != 7'h20));
  wire reg_op_bad = (opcode==`OP_OP) && (f7!=7'h00) && (f7!=7'h20) && (f7!=7'h01);
  /* RV32M 的八个 funct3 全部合法 (MUL/MULH/MULHSU/MULHU/DIV/DIVU/REM/REMU);
   * 早先漏了 4 和 5, rv32um/div 以 cause=2 非法指令陷入。 */
  wire m_f3_ok    = 1'b1;   /* f3 是 3bit, 天然 0..7 全覆盖 */
  wire muldiv_bad = (opcode==`OP_OP) && (f7==7'h01) && !m_f3_ok;
  wire sys_bad    = (opcode==`OP_SYSTEM) && (f3==3'd0) &&
                    (sys_code!=12'h000) && (sys_code!=12'h001) &&
                    (sys_code!=12'h302) && (sys_code!=12'h105);
  wire csr_f3_bad = (opcode==`OP_SYSTEM) && (f3!=3'd0) &&
                    (f3!=3'd1) && (f3!=3'd2) && (f3!=3'd3) &&
                    (f3!=3'd5) && (f3!=3'd6) && (f3!=3'd7);
  wire br_f3_bad  = (opcode==`OP_BRANCH) && (f3==3'd2 || f3==3'd3);
  wire ld_f3_bad  = (opcode==`OP_LOAD) && (f3==3'd3 || f3==3'd6);
  wire st_f3_bad  = (opcode==`OP_STORE) && (f3>3'd2);
  /* 只有 JALR 的 [14:12] 是真 funct3: LUI/AUIPC/JAL 那三位属于立即数。按 JALR
   * 的规矩校验会把任何 imm[14:12]!=0 的这三条全判非法 (实测 rv32ui 卡在 auipc)。 */
  wire other_f3_bad = (opcode==`OP_JALR) && (f3 != 3'd0);
  assign illegal_id = (!op_known && !op_custom) || imm_op_bad || reg_op_bad ||
                      muldiv_bad || sys_bad || csr_f3_bad || br_f3_bad ||
                      ld_f3_bad || st_f3_bad || other_f3_bad;

  assign uses_rs1 = (opcode==`OP_OP)|(opcode==`OP_OPIMM)|(opcode==`OP_BRANCH)
                  |(opcode==`OP_LOAD)|(opcode==`OP_STORE)|(opcode==`OP_JALR)
                  |((opcode==`OP_SYSTEM) && f3!=3'd0 && !f3[2])
                  |op_custom;
  assign uses_rs2 = (opcode==`OP_OP)|(opcode==`OP_BRANCH)|(opcode==`OP_STORE)
                  |((opcode==`OP_SYSTEM) && f3!=3'd0 && !f3[2])
                  |op_custom;

  epv32_rf u_rf (.clk(clk), .we(rf_we), .waddr(rf_waddr),
    .wdata(rf_wdata), .raddr1(rs1_f), .rdata1(rf_r1),
    .raddr2(rs2_f), .rdata2(rf_r2));

  always @* begin
    d_reg_write=0; d_mem_read=0; d_mem_write=0; d_is_ldst=0; d_is_md=0; d_is_epv=0;
    d_is_csr=0; d_is_jmp=0; d_is_br=0; d_wr_pc4=0; d_illegal=0; d_jal_is=0;
    d_do_ebreak=0; d_do_ecall=0; d_do_mret=0; d_do_wfi=0;
    d_alu_op=`ALU_ADD; d_a_pc=0; d_a_immu=0; d_b_imm=0; d_md_op=3'd0; d_imm=imm_i;

    if (illegal_id || (!EPV_ENABLE && op_custom)) begin
      d_illegal = 1'b1; d_alu_op = `ALU_PASS;
    end else begin
      case (opcode)
        `OP_LUI:    begin d_reg_write=1; d_a_immu=1; d_imm=imm_u; d_alu_op=`ALU_PASS; end
        `OP_AUIPC:  begin d_reg_write=1; d_a_pc=1; d_b_imm=1; d_imm=imm_u; end
        `OP_JAL:    begin d_reg_write=1; d_is_jmp=1; d_jal_is=1; d_wr_pc4=1;
                            d_a_pc=1; d_b_imm=1; d_imm=imm_j; end
        `OP_JALR:   begin d_reg_write=1; d_is_jmp=1; d_wr_pc4=1; d_b_imm=1; d_imm=imm_i; end
        `OP_BRANCH: begin
          /* ALU 两操作数必须是 rs1 与 rs2 (分支比较寄存器); 跳转目标走另一条
           * pc+imm_b 的加法 (br_target), 不借 ALU 的 b 端。早先误写 d_b_imm=1,
           * BEQ 实际算 rs1-imm_b, rv32ui 全部卡死在 reset_vector 的 beqz/jr。 */
          d_is_br=1; d_b_imm=0; d_imm=imm_b;
          d_alu_op = f3[2] ? (f3[1] ? `ALU_SLTU : `ALU_SLT) : `ALU_SUB;
        end
        `OP_LOAD:   begin d_reg_write=1; d_mem_read=1; d_is_ldst=1; d_b_imm=1; d_imm=imm_i; end
        `OP_STORE:  begin d_mem_write=1; d_is_ldst=1; d_b_imm=1; d_imm=imm_s; end
        `OP_OPIMM:  begin
          d_reg_write=1; d_b_imm=1; d_imm=imm_i;
          case (f3)
            3'd0: d_alu_op=`ALU_ADD;  3'd1: d_alu_op=`ALU_SLL;
            3'd2: d_alu_op=`ALU_SLT;  3'd3: d_alu_op=`ALU_SLTU;
            3'd4: d_alu_op=`ALU_XOR;  3'd5: d_alu_op=f7[5]?`ALU_SRA:`ALU_SRL;
            3'd6: d_alu_op=`ALU_OR;   3'd7: d_alu_op=`ALU_AND;
            default: d_alu_op=`ALU_ADD;
          endcase
        end
        `OP_OP:     begin
          d_reg_write=1;
          if (f7 == 7'h01) begin d_is_md=1; d_md_op={f3[2],f3[1:0]}; end
          else case (f3)
            3'd0: d_alu_op=f7[5]?`ALU_SUB:`ALU_ADD;
            3'd1: d_alu_op=`ALU_SLL;   3'd2: d_alu_op=`ALU_SLT;
            3'd3: d_alu_op=`ALU_SLTU;  3'd4: d_alu_op=`ALU_XOR;
            3'd5: d_alu_op=f7[5]?`ALU_SRA:`ALU_SRL;
            3'd6: d_alu_op=`ALU_OR;    3'd7: d_alu_op=`ALU_AND;
            default: d_alu_op=`ALU_ADD;
          endcase
        end
        `OP_FENCE:  d_alu_op = `ALU_PASS;
        `OP_SYSTEM: begin
          if (f3 == 3'd0) begin
            d_imm = imm_i;
            if      (sys_code == 12'h000) d_do_ecall  = 1'b1;
            else if (sys_code == 12'h001) d_do_ebreak = 1'b1;
            else if (sys_code == 12'h302) d_do_mret   = 1'b1;
            else                                  d_do_wfi    = 1'b1;
          end else begin
            d_is_csr=1; d_reg_write=1; d_alu_op=`ALU_PASS;
            d_imm = f3[2] ? imm_csr : imm_i;
          end
        end
        `OP_CUSTOM0, `OP_CUSTOM1: begin
          d_is_epv=1; d_b_imm=1; d_imm=imm_i;
          if (opcode == `OP_CUSTOM1)
            d_reg_write = (f7 == `EPV_F7_VDOT8);
          else
            d_reg_write = (f3 == `EPV_F3_XMV) || (f3 == `EPV_F3_CFGR);
        end
        default: d_alu_op = `ALU_PASS;
      endcase
    end
  end

  assign hazard = idex_valid && idex_mem_read && (idex_rd != 5'd0) &&
                  ((idex_rd == rs1_f && uses_rs1) || (idex_rd == rs2_f && uses_rs2));

  // ================================================================ ID/EX
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      idex_valid<=0; idex_reg_write<=0; idex_mem_read<=0; idex_mem_write<=0;
      idex_is_ldst<=0; idex_is_md<=0; idex_is_epv<=0; idex_is_csr<=0;
      idex_is_jmp<=0; idex_is_br<=0; idex_wr_pc4<=0; idex_illegal<=0;
      idex_do_ebreak<=0; idex_do_ecall<=0; idex_do_mret<=0; idex_jal_is<=0;
      idex_alu_op<=`ALU_ADD; idex_a_pc<=0; idex_a_immu<=0; idex_b_imm<=0;
      idex_md_op<=0; idex_f3<=0; idex_f7<=0; idex_opcode<=0;
      idex_rd<=0; idex_rs1<=0; idex_rs2<=0;
      idex_pc<=0; idex_pc4<=0; idex_imm<=0; idex_inst<=0;
      idex_r1d<=0; idex_r2d<=0;
    end else if (ex_stall) begin
      idex_valid   <= idex_valid;              // M2: 冻结, 保持 EX 级指令
    end else if (flush_idex) begin
      idex_valid    <= 1'b0;
      idex_reg_write<= 1'b0; idex_mem_read <= 1'b0; idex_mem_write<= 1'b0;
      idex_is_ldst  <= 1'b0; idex_is_md    <= 1'b0; idex_is_epv   <= 1'b0;
      idex_is_csr   <= 1'b0; idex_is_jmp   <= 1'b0; idex_is_br    <= 1'b0;
      idex_wr_pc4   <= 1'b0; idex_illegal  <= 1'b0;
      idex_do_ebreak<= 1'b0; idex_do_ecall <= 1'b0; idex_do_mret  <= 1'b0;
      idex_jal_is   <= 1'b0;
      idex_pc <= ifid_pc; idex_pc4 <= ifid_pc4; idex_inst <= 32'h00000013;
    end else begin
      idex_valid    <= ifid_valid;
      idex_reg_write<= d_reg_write; idex_mem_read <= d_mem_read;
      idex_mem_write<= d_mem_write; idex_is_ldst  <= d_is_ldst;
      idex_is_md    <= d_is_md;     idex_is_epv   <= d_is_epv;
      idex_is_csr   <= d_is_csr;    idex_is_jmp   <= d_is_jmp;
      idex_is_br    <= d_is_br;     idex_wr_pc4   <= d_wr_pc4;
      idex_illegal  <= d_illegal;   idex_do_ebreak<= d_do_ebreak;
      idex_do_ecall <= d_do_ecall;  idex_do_mret  <= d_do_mret;
      idex_jal_is   <= d_jal_is;
      idex_alu_op   <= d_alu_op;    idex_a_pc     <= d_a_pc;
      idex_a_immu   <= d_a_immu;    idex_b_imm    <= d_b_imm;
      idex_md_op    <= d_md_op;     idex_f3       <= f3;
      idex_f7       <= f7;          idex_opcode   <= opcode;
      idex_rd       <= rd_f;        idex_rs1      <= rs1_f;
      idex_rs2      <= rs2_f;
      idex_pc       <= ifid_pc;     idex_pc4      <= ifid_pc4;
      idex_imm      <= d_imm;       idex_inst     <= ifid_inst;
      idex_r1d      <= rf_r1;       idex_r2d      <= rf_r2;
    end
  end

  // ================================================================ EX
  assign fwd1 = (exmem_valid && exmem_reg_write && exmem_rd != 5'd0 &&
                 exmem_rd == idex_rs1) ? exmem_result
            : (memwb_valid && memwb_reg_write && memwb_rd != 5'd0 &&
                 memwb_rd == idex_rs1) ? memwb_wdata
            : idex_r1d;
  assign fwd2 = (exmem_valid && exmem_reg_write && exmem_rd != 5'd0 &&
                 exmem_rd == idex_rs2) ? exmem_result
            : (memwb_valid && memwb_reg_write && memwb_rd != 5'd0 &&
                 memwb_rd == idex_rs2) ? memwb_wdata
            : idex_r2d;

  assign alu_a = idex_a_pc ? idex_pc : (idex_a_immu ? idex_imm : fwd1);
  assign alu_b = idex_b_imm ? idex_imm : fwd2;

  epv32_alu u_alu (.op(idex_alu_op), .a(alu_a), .b(alu_b), .y(alu_y));

  epv32_muldiv u_md (.clk(clk), .rst_n(rst_n),
    .i_valid(idex_valid && idex_is_md), .i_op(idex_md_op),
    .i_a(fwd1), .i_b(fwd2), .o_result(md_result), .o_done(md_done),
    .o_busy(md_busy));

  generate if (EPV_ENABLE != 0) begin : G_EPV
    epv32_epv u_epv (.clk(clk), .rst_n(rst_n),
      .i_valid(idex_valid && idex_is_epv), .i_is_c0(idex_opcode == `OP_CUSTOM0),
      .i_f3(idex_f3), .i_f7(idex_f7), .i_rd(idex_rd), .i_rs1f(idex_rs1),
      .i_rs2f(idex_rs2), .i_gpr1(fwd1), .i_gpr2(fwd2), .i_imm(idex_imm[11:0]),
      .o_mem_req(epv_mreq), .o_mem_addr(epv_maddr), .o_mem_we(epv_mwe),
      .o_mem_wdata(epv_mwdata), .i_mem_rdata(i_dmem_rdata),
      .i_mem_grant(epv_grant), .o_xres(epv_xres), .o_xwr(epv_xwr),
      .o_done(epv_done), .o_busy(epv_busy), .o_illegal(epv_illegal),
      .o_vcfg(epv_vcfg), .o_v0(epv_v0), .o_vrf_wr(epv_vrf_wr));
  end else begin : G_NOEPV
    assign epv_xres=32'h0; assign epv_xwr=1'b0; assign epv_done=1'b0;
    assign epv_busy=1'b0;  assign epv_illegal=1'b0; assign epv_mreq=1'b0;
    assign epv_maddr=32'h0; assign epv_mwe=1'b0; assign epv_mwdata=32'h0;
    assign epv_vcfg=32'h0;  assign epv_v0=128'h0;  assign epv_vrf_wr=1'b0;
  end endgenerate

  // ---- 地址与条件
  assign mem_addr  = alu_y;
  assign br_target = idex_pc + idex_imm;
  assign jr_target = (idex_jal_is ? (idex_pc + idex_imm) : (fwd1 + idex_imm))
                     & 32'hFFFF_FFFE;
  wire br_eq = (alu_y == 32'h0);
  wire br_lt = alu_y[0];
  wire slt_u = (idex_f3==3'd4) || (idex_f3==3'd5) || (idex_f3==3'd6) || (idex_f3==3'd7);
  /* 比较结果一律取自 ALU 的那一位: f3=0/1 用 SUB 的零标志,
   * f3=4/5 用 SLT, f3=6/7 用 SLTU。之前 f3=6/7 在核外又写了一遍
   * (fwd1 < fwd2), 等于同一件事有两处真源 -- 现在只留 ALU 这一处。 */
  wire cond_take = (idex_f3==3'd0) ?  br_eq :
                   (idex_f3==3'd1) ? ~br_eq :
                   (idex_f3==3'd2) ?  br_eq :
                   (idex_f3==3'd3) ? ~br_eq :
                   (idex_f3==3'd4) ?  br_lt :
                   (idex_f3==3'd5) ? ~br_lt :
                   (idex_f3==3'd6) ?  br_lt :
                                     ~br_lt;
  wire c_ld = idex_valid && idex_mem_read;
  wire c_st = idex_valid && idex_mem_write;

  /* M2 + M6: 前端冻结与「不许中途重定向」这一条线共用。mem_hold 只在跨字访问
   * 的第一拍为 1 —— 那一拍既不提交新指令, 也不让 EX/MEM 里的访问被顶掉。 */
  assign ex_stall = (idex_is_md && md_busy) || (idex_is_epv && epv_busy) || mem_hold;

  /* 对齐要求按访问宽度分三档, funct3 分组不能凭位序:
   *   1 字节: LB=0, LBU=4, SB=0      -> 无对齐要求
   *   2 字节: LH=1, LHU=5, SH=1      -> 只需 2 字节对齐 (看 addr[0])
   *   4 字节: LW=2, SW=2             -> 需 4 字节对齐 (看 addr[1:0])
   * SW 与 LW 共用 funct3=2, **不存在 SW=6 这种编码**。曾把 LHU(5) 与 LW(2) 划进
   * 同一档, rv32ui/lhu 的 lhu a4,2(sp) 以 cause=4 陷入。 */
  wire ma_half = (idex_f3==3'd1) && mem_addr[0];
  wire ma_word = (idex_f3==3'd2) && (mem_addr[1:0] != 2'b00);
  wire misaligned = (c_ld || c_st) && (ma_half || ma_word);
  /* M6: 要不要「陷入」由参数决定; 检测本身两档都要算, 因为 =0 档仍要精确报
   * cause 4/6。=1 档下这条线不再进异常或mux, 改由 MEM 拆两拍处理。 */
  wire ma_trap = (SUPPORT_UNALIGNED == 0) && misaligned;
  /* 跨越判断只看「访问对象是否出得了一个字」: 起始字节 + 宽度 > 4。
   * 宽度按 funct3 分档, 不许按位序猜 (见上面 LHU 的教训)。 */
  wire [2:0] x_wb = (idex_f3==3'd2)                   ? 3'd4
                  : (idex_f3==3'd1 || idex_f3==3'd5) ? 3'd2 : 3'd1;
  wire       x_cross = (c_ld || c_st) && (SUPPORT_UNALIGNED != 0) &&
                       (({2'b00, mem_addr[1:0]} + {1'b0, x_wb}) > 4'd4);
  wire [31:0] x_hi_addr = {mem_addr[31:2] + 30'd1, 2'b00};
  /* 两半都必须存在, 否则整条访问按「访问错误」陷入, mtval 仍是原始地址 ——
   * 规范里这是「本次访问做不到」, 不是「做到一半」。 */
  wire x_bound  = x_cross && !addr_ok(x_hi_addr);
  /* M9: PMP 违例折进**同一条** bus_err, 不新增陷入源 (3.2 节那条「全核只有
   * 一个陷入源」是已经过穷举验证的结构)。规范里 PMP 违例的原因码本来就是
   * load access fault(5) / store access fault(7), 与「地址不存在」同一个出口、
   * 同一个 mtval=访存地址, 所以这里不需要在 cause mux 里加新的分支。 */
  wire pmp_rd_deny, pmp_wr_deny;
  wire bus_err    = ((c_ld || c_st) && (!addr_ok(mem_addr) || x_bound))
                    || pmp_rd_deny || pmp_wr_deny;
  wire fetch_err  = idex_valid && !addr_ok(idex_pc);
  /* 只有分支真的会跳时目标地址才进入控制流; 不跳的分支不该因为目标恰好
   * 落在非法地址而陷入 (此前无条件判, 与规范不符)。 */
  /* M7: IALIGN=32 的指令地址非对齐检查。jalr 已按规范清掉 bit0, 唯一可能越界
   * 的是 bit1 —— 上版只判地址图范围, 「跳到 mis_fn+2」不陷入, 还把它当 PC 参与
   * auipc 计算, la 目标算偏 2 字节。规范要的是 cause 0。JAL 目标走 jr_err 同一条
   * 式子; 分支只在真的跳时判 (见上一行)。 */
  wire br_err     = idex_valid && idex_is_br && cond_take &&
                    (!addr_ok(br_target) || br_target[1]);
  wire jr_err     = idex_valid && idex_is_jmp &&
                    (!addr_ok(jr_target) || jr_target[1]);
  wire epv_bad    = idex_valid && idex_is_epv && epv_illegal;
  wire sys_exc    = idex_valid && (idex_do_ebreak || idex_do_ecall);
  wire ill_exc    = idex_valid && (idex_illegal || epv_bad);

  reg [4:0]  c_cause;
  reg [31:0] c_tval;
  always @* begin
    if (ill_exc)            begin c_cause=`EXC_ILLEGAL; c_tval=idex_inst; end
    else if (fetch_err)     begin c_cause=`EXC_IAF;     c_tval=idex_pc;   end
    else if (br_err)        begin c_cause=5'd0;         c_tval=br_target; end
    else if (jr_err)        begin c_cause=5'd0;         c_tval=jr_target; end
    else if (idex_do_ebreak)begin c_cause=`EXC_IBREAK;  c_tval=idex_pc;   end
    else if (idex_do_ecall) begin c_cause=`EXC_ECM;     c_tval=32'h0;     end
    else if (ma_trap)       begin c_cause=c_ld?`EXC_LAMF:`EXC_SAMF; c_tval=mem_addr; end
    else                    begin c_cause=c_ld?`EXC_LAF:`EXC_SAF;   c_tval=mem_addr; end
  end
  assign exc_cause = c_cause;
  assign exc_tval  = c_tval;

  assign ex_exc   = idex_valid && (ill_exc || fetch_err || br_err || jr_err ||
                                   sys_exc || ma_trap || bus_err) && !ex_stall;
  assign irq_now  = csr_irq_any && idex_valid && !ex_stall && !ex_exc;
  assign trap_fire= ex_exc || irq_now;
  assign mret_go  = idex_valid && idex_do_mret && !ex_exc && !irq_now && !ex_stall;
  /* 异常入口取屏蔽过低两位的 mtvec_base: mtvec[1:0] 是模式域, 原值直接当
   * 地址用会把模式位漏进向量地址 (黄金 ISS 独立指出)。 */
  assign trap_target = irq_now ? csr_irq_target : csr_mtvec_base;

  assign branch_take = idex_valid && idex_is_br  && cond_take && !trap_fire && !ex_stall;
  assign jump_take   = idex_valid && idex_is_jmp && !trap_fire && !ex_stall;
  assign redirect    = trap_fire || mret_go || branch_take || jump_take;

  assign ex_result = idex_is_md  ? md_result
                   : idex_is_epv ? epv_xres
                   : idex_is_csr ? csr_rdata
                   : idex_wr_pc4 ? idex_pc4
                   : idex_a_immu ? idex_imm
                   : alu_y;

  assign ex_commit = idex_valid && !ex_stall && !trap_fire && !mret_go;

  wire [31:0] csr_mtvec_mux;
  wire [4:0]  irq_cause = i_irq_ext ? `IRQ_M_EXT_NUM
                          : (i_irq_timer ? 5'd7 : 5'd3);

  // M9: 只有写 mcycle/mcycleh/minstret/minstreth 这四个地址才抑制自增;
  //      cycle/instret (0xC00/0xC02) 是只读镜像, 写被 CSR 模块忽略, 不抑制自增。
  //      minstret 的自增点从 WB 挪到**提交点 (EX)**: 写与自增必须在同一拍且互斥,
  //      否则 `csrw minstret,-1`+`csrw minstreth,-1` 之间退休的 li 会把已写成
  //      0xFFFF_FFFF 的低位先翻 0 并丢掉进位 (hi 覆盖了进位)。口径: minstret
  //      数的是「已提交的指令数」, 读它的那条自己不计。
  wire [11:0] csr_addr_ex = idex_inst[31:20];
  wire        csr_cnt_wr  = csr_wr_now &&
      (csr_addr_ex == 12'hB00 || csr_addr_ex == 12'hB80 ||
       csr_addr_ex == 12'hB02 || csr_addr_ex == 12'hB82);

  // M9: PMP 判定结果的线在上面 bus_err 旁边声明 (两处真源必然各自演化)。

  epv32_csr #(.IMPID(IMPID), .PMP_N(4)) u_csr (
    .clk(clk), .rst_n(rst_n),
    .i_csr_en(idex_valid && idex_is_csr && !ex_stall),
    .i_csr_op(idex_f3[1:0]), .i_csr_imm(idex_f3[2]),
    .i_addr(idex_inst[31:20]),
    .i_wdata(idex_f3[2] ? {27'b0, idex_rs1} : fwd1),
    .o_rdata(csr_rdata), .o_wr(csr_wr_now),
    .o_mtvec(csr_mtvec_mux), .o_mtvec_base(csr_mtvec_base),
    .o_mepc(csr_mepc_w), .o_mie(csr_mie),
    .o_dbg_mtval(csr_dbg_mtval), .o_dbg_mcause(csr_dbg_mcause),
    .i_trap(trap_fire), .i_cause(irq_now ? irq_cause : exc_cause),
    .i_is_irq(irq_now),
    .i_trap_pc(idex_pc), .i_tval(irq_now ? 32'h0 : exc_tval), .i_mret(mret_go),
    .i_irq_timer(i_irq_timer), .i_irq_soft(i_irq_soft), .i_irq_ext(i_irq_ext),
    .o_irq_any(csr_irq_any), .o_irq_target(csr_irq_target),
    .o_mret_target(csr_mret_target),
    .i_inst_retired(ex_commit && !csr_cnt_wr), .i_clk_tick(1'b1),
    .i_vcfg(epv_vcfg), .o_mcycle(csr_mcycle), .o_minstret(csr_minstret),
    .i_pmp_a0(mem_addr), .i_pmp_a1(x_hi_addr), .i_pmp_a1_en(x_cross),
    .i_pmp_rd_en(c_ld), .i_pmp_wr_en(c_st),
    .o_pmp_rd_deny(pmp_rd_deny), .o_pmp_wr_deny(pmp_wr_deny));

  // ---- PC 更新
  wire [31:0] pc_next = redirect   ? (trap_fire ? trap_target
                                    : mret_go  ? csr_mret_target
                                    : jump_take ? jr_target : br_target)
                : pc_stall ? pc : pc + 32'd4;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) pc_q <= `ADDR_RAM_BASE;
    else        pc_q <= pc_next;
  end

  // ================================================================ EX/MEM
  assign memop_in_mem = exmem_valid && (exmem_mem_read || exmem_mem_write);
  assign epv_grant    = epv_mreq && !memop_in_mem;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      exmem_valid<=0; exmem_reg_write<=0; exmem_mem_read<=0;
      exmem_mem_write<=0; exmem_rd<=0; exmem_f3<=0; exmem_result<=0;
      exmem_rs2<=0; exmem_addr<=0; exmem_pc<=0;
    end else if (mem_hold) begin
      /* 跨字访问的第一拍: EX/MEM 必须原地不动, 否则访存指令会被 EX 里那条
       * 冻结住的指令顶掉 (ex_commit 此时已被 ex_stall 压成 0)。 */
      exmem_valid     <= exmem_valid;
      exmem_reg_write <= exmem_reg_write;
      exmem_mem_read  <= exmem_mem_read;
      exmem_mem_write <= exmem_mem_write;
      exmem_rd        <= exmem_rd;
      exmem_f3        <= exmem_f3;
      exmem_result    <= exmem_result;
      exmem_rs2       <= exmem_rs2;
      exmem_addr      <= exmem_addr;
      exmem_pc        <= exmem_pc;
    end else begin
      exmem_valid     <= ex_commit;
      exmem_reg_write <= ex_commit && idex_reg_write;
      exmem_mem_read  <= ex_commit && idex_mem_read;
      exmem_mem_write <= ex_commit && idex_mem_write;
      exmem_rd        <= idex_rd;
      exmem_f3        <= idex_f3;
      exmem_result    <= ex_result;
      exmem_rs2       <= fwd2;
      exmem_addr      <= mem_addr;
      exmem_pc        <= idex_pc;
    end
  end

  // ================================================================ MEM
  /* ---- M6: 非对齐访问拆成两次对齐访问 ------------------------------------ */
  wire [1:0]  m_off   = exmem_addr[1:0];
  /* 宽度只看 funct3 低两位与 LHU 那一档: SW 与 LW 共用 funct3=2, **没有 funct3=6
   * 这种访存编码** (第一版把 SW 记成 6, m_widen 只搬 rs2[7:0], sw 非对齐只写
   * 一个字节)。 */
  wire [2:0]  m_wb    = (exmem_f3==3'd2)            ? 3'd4
                      : (exmem_f3==3'd1 || exmem_f3==3'd5) ? 3'd2 : 3'd1;
  wire        m_active = exmem_valid && (exmem_mem_read || exmem_mem_write);
  assign m_cross  = m_active && (SUPPORT_UNALIGNED != 0) &&
                    (({2'b00, m_off} + {1'b0, m_wb}) > 4'd4);
  assign mem_hold = m_cross && !mem_x_phase;

  wire [31:0] m_word0 = {exmem_addr[31:2], 2'b00};
  wire [31:0] m_word1 = m_word0 + 32'd4;
  wire [6:0]  m_sh    = {2'b00, m_off, 3'b000};           // 8*off, 0..24
  wire [6:0]  m_wb8   = {1'b0, m_wb, 3'b000};             // 8*宽度: 8/16/32
  /* 写侧: 把操作数按字节通道摆进 64bit 窗口, 低字/高字各取一半。式子对**对齐**
   * 访问退化得与旧写法逐项相同, 所以对齐/非对齐共用一条数据通路。 */
  wire [63:0] m_widen = (exmem_mem_read) ? 64'd0            /* 读侧不用它 */
                      : (exmem_f3==3'd2) ? {32'b0, exmem_rs2}
                      : (exmem_f3==3'd1) ? {48'b0, exmem_rs2[15:0]}
                      :                    {56'b0, exmem_rs2[7:0]};
  wire [63:0] m_val64 = m_widen << m_sh;
  wire [63:0] m_lmask = {64{1'b1}} >> (7'd64 - m_wb8);
  wire [63:0] m_be64  = m_lmask << m_sh;
  /* m_be64 的「1」站在**位**通道上, 而 o_dmem_be 一位代表一个字节通道 —— 必须
   * 按字节归约 (直接接 m_be64[3:0] 只有 off=0 的访问正确, SB@+1 会得到 be=0)。 */
  wire [3:0]  m_be0   = { |m_be64[31:24], |m_be64[23:16],
                          |m_be64[15:8],  |m_be64[7:0]  };
  wire [3:0]  m_be1   = { |m_be64[63:56], |m_be64[55:48],
                          |m_be64[47:40], |m_be64[39:32] };
  /* 读侧: 一律「把被取对象挪到窗口最低字节, 再按 off=0 扩展」, 字内非对齐但不
   * 跨界的情形也走同一条式子。旧 ld_ext(off) 只认 off[1], lh @ +1 读成 0x0100
   * 而非 0x0201。 */
  wire [63:0] m_win     = m_cross ? {i_dmem_rdata, mem_x_lo}
                                  : {i_dmem_rdata, i_dmem_rdata};
  wire [63:0] m_shift64 = m_win >> m_sh;
  wire [31:0] m_rdata   = m_shift64[31:0];

  assign o_dmem_en    = memop_in_mem || (epv_mreq && epv_grant);
  assign o_dmem_addr  = memop_in_mem ? (mem_x_phase ? m_word1 : m_word0) : epv_maddr;
  assign o_dmem_we    = (memop_in_mem && exmem_mem_write) || (epv_mwe && epv_grant);
  assign o_dmem_wdata = memop_in_mem ? (mem_x_phase ? m_val64[63:32] : m_val64[31:0])
                                     : epv_mwdata;
  assign o_dmem_be    = memop_in_mem ? (mem_x_phase ? m_be1 : m_be0)
                                     : 4'b1111;

  wire [31:0] mem_load = exmem_mem_read
                       ? ld_ext(exmem_f3[2:0], 2'b00, m_rdata)
                       : exmem_result;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      mem_x_phase <= 1'b0; mem_x_lo <= 32'd0;
    end else if (m_cross && !mem_x_phase) begin
      mem_x_phase <= 1'b1;
      mem_x_lo    <= i_dmem_rdata;          // 低字留到下一拍拼接用
    end else if (mem_x_phase) begin
      mem_x_phase <= 1'b0;
    end
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      memwb_valid<=0; memwb_reg_write<=0; memwb_rd<=0; memwb_wdata<=0;
      memwb_mem_write<=0; memwb_be<=0; memwb_addr<=0; memwb_raw<=0; memwb_pc<=0;
    end else if (mem_hold) begin
      /* 第一拍向下游注入气泡: 否则 WB 会把同一条指令退休两次
       * (退休轨迹与 minstret 都会翻倍, tohost 回读也会看到重复写)。 */
      memwb_valid<=0; memwb_reg_write<=0; memwb_rd<=0; memwb_wdata<=0;
      memwb_mem_write<=0; memwb_be<=0; memwb_addr<=0; memwb_raw<=0; memwb_pc<=0;
    end else begin
      memwb_valid     <= exmem_valid;
      memwb_reg_write <= exmem_reg_write;
      memwb_rd        <= exmem_rd;
      memwb_wdata     <= mem_load;
      memwb_mem_write <= exmem_mem_write;
      /* 退休轨迹记的是「这条指令按语义写了哪几个字节通道」—— 两半的并集,
       * 地址仍是原始(可能非对齐)地址。总线上当拍看到的是分开的两次写。 */
      memwb_be        <= (m_be0 | m_be1) & {4{exmem_mem_write}};
      memwb_addr      <= exmem_addr;
      memwb_raw       <= exmem_rs2;
      memwb_pc        <= exmem_pc;
    end
  end

  // ================================================================ WB
  assign rf_we    = memwb_valid && memwb_reg_write && (memwb_rd != 5'd0);
  assign rf_waddr = memwb_rd;
  assign rf_wdata = memwb_wdata;

  assign o_tr_valid = memwb_valid;
  assign o_tr_pc    = memwb_pc;
  assign o_tr_rd    = memwb_rd;
  assign o_tr_wdata = memwb_wdata;
  assign o_tr_regwr = rf_we;
  assign o_tr_memwr = memwb_mem_write;
  assign o_tr_addr  = memwb_addr;
  assign o_tr_mdata = memwb_raw;
  assign o_tr_mbe   = memwb_be;

  // ================================================================ 结构不变式
  /* 只在仿真那一臂 (`DEFV_ASSERTIONS) 下装载; 综合与两档 lint 看不到这一行,
   * 所以面积/周期读数与本节无关 —— 这一句是可核的: 不带该宏重跑 lint, 判据
   * 逐位不变 (flow/assert_selfcheck.py 的 LINT_UNAFFECTED 臂)。 */
`ifdef EPV_ASSERTIONS
  `include "epv32_assert_core.vh"
`endif

  wire _unused = &{1'b0, md_done, epv_xwr, epv_done, epv_vrf_wr, epv_v0,
                   csr_wr_now, slt_u, d_do_wfi, uses_rs2, 1'b0};

endmodule
