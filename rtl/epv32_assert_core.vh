// ============================================================================
//  epv32_assert_core.vh -- 核的结构性不变量, 「可机器开火」的那一版
//
//  为什么要有这份文件: 这些规则此前只写在 epv32_core.v 的注释里 (M2「ex_stall
//  冻结 PC 与前端」、M4「重定向一律排除在 ex_stall 期内」)。注释不是门禁 ——
//  下一版改动把这条性质弄丢时, 没有任何东西会响, 只能靠官方套件恰好踩到才现形。
//  本文件把已经写进注释的结构不变量变成前仿期间就会开火的检查。
//
//  装载方式 (三条都必须成立, 少一条就是「写了但没人跑」):
//    1) 只在 `DEFV_ASSERTIONS` 下被 epv32_core.v 包含 —— 综合与日常两档 lint
//       根本看不到本文件, 所以面积/周期读数与本文件无关 (这是可核的: 不定义该
//       宏重跑 lint, 判据逐位不变);
//    2) flow/build_sim.sh ASSERT=1 那一臂带 -DEFV_ASSERTIONS;
//    3) flow/assert_selfcheck.py 把「每条检查真的会红」做成注入证明 —— 一条从不
//       开火的断言与一条不存在的断言在报告里长得一样。
//
//  判决形状: 每条失败打一行 `EPVASSERT FAIL <tag> t=<time>`, 末了由
//  `EPVASSERT CORE_ARMED n=<检查条数>` 证明这段代码真进了方程 (没有 heartbeat
//  就等于这一臂没编进去)。不 $fatal, 让一趟跑完把所有违规都数出来。
// ============================================================================

  integer epv_assert_n = 0;

`define EPV_ASSERT_FAIL(tag) \
  begin \
    epv_assert_n = epv_assert_n + 1; \
    $display("EPVASSERT FAIL %s t=%0t", tag, $time); \
  end

  // ---- 上一拍的采样 (见下面 A4/A5 的时序推导) ----
  reg         as_ex_stall_q = 1'b0;
  reg         as_hold_ifid_q = 1'b0;
  reg         as_flush_idex_q = 1'b0;
  reg  [31:0] as_pc_prev = 32'h0;
  reg  [31:0] as_ifid_pc_prev = 32'h0;
  reg  [31:0] as_ifid_inst_prev = 32'h0;
  reg  [31:0] as_trap_cnt_prev = 32'h0;

  initial $display("EPVASSERT CORE_ARMED n=12");

  // ---------------------------------------------------------------- 组合面检查
  always @(posedge clk) begin
    if (rst_n) begin
      // A1 陷入源唯一: 同一拍至多一个重定向来源。
      //   四条 assign (537/538/543/544) 都互斥地排除了彼此, 但那是四条独立的
      //   文字; 少写一个 !trap_fire 就会有两个来源同拍有效, 而 trap_target 的
      //   优先级 mux 会静默挑一个 —— 官方用例不一定踩得到。
      if ({trap_fire, mret_go, branch_take, jump_take}
          === 4'b0000 || ({trap_fire, mret_go, branch_take, jump_take}
          === 4'b0001 || {trap_fire, mret_go, branch_take, jump_take}
          === 4'b0010 || {trap_fire, mret_go, branch_take, jump_take}
          === 4'b0100 || {trap_fire, mret_go, branch_take, jump_take}
          === 4'b1000)) begin
        // ok: 0 或 1 个来源
      end else begin
        `EPV_ASSERT_FAIL("A1_TRAP_SRC_NOT_UNIQUE");
      end

      // A2 一条多周期指令不会中途被丢掉: 陷入不得在 ex_stall 期内发火
      if (trap_fire && ex_stall)   `EPV_ASSERT_FAIL("A2_TRAP_DURING_EXSTALL");
      // A3 同上, 分支/跳转/mret 三支重定向
      if ((branch_take | jump_take | mret_go) && ex_stall)
        `EPV_ASSERT_FAIL("A3_REDIRECT_DURING_EXSTALL");

      // A7 总线协议: 数据口只在字地址上被驱动 (RAM 按字索引; M6 跨字访问靠
      //   mem_x_phase 拆成两次整字访问, 而不是让总线吃半个字)
      if (o_dmem_en && (o_dmem_addr[1:0] !== 2'b00))
        `EPV_ASSERT_FAIL("A7_DMEM_UNALIGNED_PORT");
      // A8 恒 1 周期就绪: 没有 ready 输入, 所以「每次访问都落在地址图上」是
      //   这条协议的全部前提。地址不在图上时 RAM 的索引会回绕, 那是一个
      //   永远读不到值而核继续往下跑的失效形状。
      if (!addr_ok(o_imem_addr))
        `EPV_ASSERT_FAIL("A8_IFETCH_OFF_MAP");
      if (o_dmem_en && !addr_ok(o_dmem_addr))
        `EPV_ASSERT_FAIL("A8_DMEM_OFF_MAP");

      // A10 陷入计数只增: 它是归档里「这一趟真的陷过」的唯一见证
      if (o_dbg_trap_cnt < as_trap_cnt_prev)
        `EPV_ASSERT_FAIL("A10_TRAP_CNT_WENT_BACK");
      as_trap_cnt_prev <= o_dbg_trap_cnt;

      // A11 两个多周期单元不许同时占住 EX (乘/除 与 EPV 共用一条 ex_stall)
      if (epv_busy && md_busy)
        `EPV_ASSERT_FAIL("A11_TWO_BUSY_UNITS");

      // A12 一条指令不许同时是非法指令与 ecall/ebreak
      //   (译码把 do_ecall 与 illegal 分别置位; 同拍都真则 exc_cause 的 mux
      //    会静默丢掉其中一个)
      if (idex_valid && idex_illegal && (idex_do_ecall || idex_do_ebreak))
        `EPV_ASSERT_FAIL("A12_ILLEGAL_AND_SYSCALL");
    end
  end

  // ---------------------------------------------------------------- 时序面检查
  // A4 / A5 / A6 读的是「本拍开始前的状态」, 所以比较对象是上一拍存下来的采样。
  //   记号: 第 k 拍的状态 = 边沿 k-1 写入的值。在边沿 k 读 pc_q 得到第 k 拍的
  //   pc, 读 ex_stall 得到第 k 拍的 ex_stall。于是「第 k 拍 ex_stall 为真 ⇒ 第
  //   k+1 拍 pc 不变」在边沿 k+1 上判: ex_stall_q(=第 k 拍) && pc_q(=第 k+1 拍)
  //   !== as_pc_prev(=第 k 拍)。
  always @(posedge clk) begin
    if (rst_n) begin
      if (as_ex_stall_q && (pc_q !== as_pc_prev))
        `EPV_ASSERT_FAIL("A4_PC_MOVED_DURING_EXSTALL");
      // 任一项变了都算违约 —— 用 && 会要求「两个都变」才开火, 那是把守卫关掉一半。
      if (as_hold_ifid_q && ((ifid_pc !== as_ifid_pc_prev)
                             || (ifid_inst !== as_ifid_inst_prev)))
        `EPV_ASSERT_FAIL("A5_IFID_CHANGED_DURING_HOLD");
      if (as_flush_idex_q && (idex_valid !== 1'b0))
        `EPV_ASSERT_FAIL("A6_IDEX_NOT_FLUSHED");

      as_ex_stall_q    <= ex_stall;
      as_hold_ifid_q   <= hold_ifid && !flush_ifid;
      as_flush_idex_q  <= flush_idex;
      as_pc_prev       <= pc_q;
      as_ifid_pc_prev  <= ifid_pc;
      as_ifid_inst_prev<= ifid_inst;
    end else begin
      as_ex_stall_q    <= 1'b0;
      as_hold_ifid_q   <= 1'b0;
      as_flush_idex_q  <= 1'b0;
      as_pc_prev       <= 32'h0;
      as_ifid_pc_prev  <= 32'h0;
      as_ifid_inst_prev<= 32'h0;
      as_trap_cnt_prev <= 32'h0;
    end
  end
