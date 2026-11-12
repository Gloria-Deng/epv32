// ============================================================================
//  epv32_assert_epv.vh -- EPV 向量单元的结构性不变量
//
//  装载方式同 epv32_assert_core.vh (只在 `DEFV_ASSERTIONS` 下被包含,
//  flow/build_sim.sh ASSERT=1 一臂带 -DEFV_ASSERTIONS, flow/assert_selfcheck.py
//  注入证明每条检查会开火)。
//
//  要挡什么: 「向量寄存器只在 EX 组合读、只在完成拍写」是省掉整套向量前递网络的
//  前提 (见 epv32_epv.v 头注)。它一旦丢, 多周期搬运还在跑时 vrf 会被半截结果
//  写脏 —— 输出不会立刻错, 要等下一条用该向量的指令才错, 且错在离根因很远的地方;
//  事后比对最难定位, 所以必须在前仿期间就能开火。
// ============================================================================

  integer epv_assert_n_e = 0;
  reg         as_busy_q = 1'b0;
  reg  [2:0]  as_f3_q = 3'h0;
  reg  [6:0]  as_f7_q = 7'h0;
  reg  [4:0]  as_rd_q = 5'h0;
  reg  [4:0]  as_rs1_q = 5'h0;
  reg         as_is_c0_q = 1'b0;
  reg         as_valid_q = 1'b0;

  initial $display("EPVASSERT EPV_ARMED n=6");

  always @(posedge clk) begin
    if (!rst_n) begin
      as_busy_q    <= 1'b0;
      as_valid_q   <= 1'b0;
    end else begin
      // E1 只在完成拍写向量寄存器堆
      if (o_vrf_wr && !o_done) begin
        epv_assert_n_e = epv_assert_n_e + 1;
        $display("EPVASSERT FAIL E1_VRF_WR_NOT_ON_DONE t=%0t", $time);
      end
      // E2 非法指令不许留下任何架构可见的写
      if (o_illegal && (o_vrf_wr || o_xwr)) begin
        epv_assert_n_e = epv_assert_n_e + 1;
        $display("EPVASSERT FAIL E2_ILLEGAL_STILL_WRITES t=%0t", $time);
      end
      // E3 标量结果写回的时机与向量一致 (都在 done 那一拍)
      if (o_xwr && !o_done) begin
        epv_assert_n_e = epv_assert_n_e + 1;
        $display("EPVASSERT FAIL E3_XWR_NOT_ON_DONE t=%0t", $time);
      end
      // E4 搬运流程期间总线地址必须整字对齐 (SVB 的分字节合并走 wdata/be,
      //    不是让总线去访问半个字)
      if (o_mem_req && (o_mem_addr[1:0] !== 2'b00)) begin
        epv_assert_n_e = epv_assert_n_e + 1;
        $display("EPVASSERT FAIL E4_EPV_BUS_UNALIGNED t=%0t", $time);
      end
      // E5 总线授予只发生在单元确实申请的那一拍
      if (i_mem_grant && !o_mem_req) begin
        epv_assert_n_e = epv_assert_n_e + 1;
        $display("EPVASSERT FAIL E5_GRANT_WITHOUT_REQ t=%0t", $time);
      end
      // E6 一条多周期搬运在跑的时候, EX 不许把这条指令换掉
      //    (换掉就是 ex_stall 没冻住前端 —— 与核侧 A4 是同一件事的两个视角)
      //    门控取 o_busy 而不是 mem_ph: o_busy = i_valid && !o_done 即「这一拍
      //    还没做完」; 拿 mem_ph 当门会把做完那一拍 (o_done=1) 误判成违规。
      if (as_busy_q
          && (i_valid !== as_valid_q || i_is_c0 !== as_is_c0_q
              || i_f3 !== as_f3_q || i_f7 !== as_f7_q
              || i_rd !== as_rd_q || i_rs1f !== as_rs1_q)) begin
        epv_assert_n_e = epv_assert_n_e + 1;
        $display("EPVASSERT FAIL E6_EPV_INST_CHANGED_MIDFLIGHT t=%0t", $time);
      end

      as_busy_q    <= o_busy;
      as_valid_q   <= i_valid;
      as_is_c0_q   <= i_is_c0;
      as_f3_q      <= i_f3;
      as_f7_q      <= i_f7;
      as_rd_q      <= i_rd;
      as_rs1_q     <= i_rs1f;
    end
  end
