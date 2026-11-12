// ============================================================================
//  epv32_assert_epv.vh -- EPV 向量单元的结构性不变量
//
//  与 epv32_assert_core.vh 同一套装载方式 (只在 `DEFV_ASSERTIONS` 下被包含,
//  flow/build_sim.sh ASSERT=1 那一臂带 -DEFV_ASSERTIONS, 每条检查由
//  flow/assert_selfcheck.py 的注入臂证明会开火)。
//
//  这一份要挡的是什么: 「向量寄存器只在 EX 组合读、只在完成拍写」是本设计省掉
//  整套向量前递网络的前提 (见 epv32_epv.v 头注)。它一旦丢, 症状是某个多周期
//  搬运还在跑时 vrf 被半截结果写脏 —— 输出不会立刻错, 要等下一条用该向量的
//  指令才错, 而且错在一个离根因很远的地方。这是事后比对最难定位的一类失效,
//  所以它必须在前仿期间就能开火。
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
      //    门控取 o_busy 而不是 mem_ph: o_busy = i_valid && !o_done, 也就是
      //    「这一拍还没做完」。做完那一拍 (o_done=1) 之后核本来就该放行下一条,
      //    拿 mem_ph 当门会把这一格判成违规 —— 实测首跑 epv_selftest 27 次、
      //    epv_bisect 14 次全是我这条断言自己写错, 不是设计坏了。
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
