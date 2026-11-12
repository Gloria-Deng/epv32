// ============================================================================
//  epv32_epv.v  --  EPV (Embedded Packed-Vector) 自定义指令向量运算单元
//
//  数据通路
//    * `EPV_VRF_WORDS 个 128bit 向量寄存器 (默认 8 个), 同一份位流可按
//      16 x int8 / 8 x int16 / 4 x int32 三种视角使用 (lane i 占 bit[i*W +: W])
//    * 16 x (8bit x 8bit) 有符号乘法器 + int32 归约树   -> VDOT8 / VMAC4
//    * 4  x (32bit x 32bit) 有符号乘法器 + 64bit 舍入移位 -> VREQ4 逐通道重量化
//    * 逐 lane int8 加/减/取大/取小/ReLU/2x1 池化        -> generate 展开
//    * 多周期状态机借核的数据总线做 16 字节搬运          -> LVB / SVB (4 次字访问)
//    * 32bit 配置寄存器 vcfg: S / RB / RND / SAT
//
//  流水线约定
//    本单元整个生命周期都待在 EX 级: 向量寄存器只在 EX 组合读、只在完成拍的
//    时钟沿写, 而下一条 EPV 指令最早只能在完成拍的下一拍进入 EX, 所以向量 RAW
//    相关由 o_busy 造成的 EX 冻结天然串行化, 不需要额外的向量前递网络。
//    需要写回 GPR 的三条 (VDOT8 / XMV / CFGR) 走 o_xres, 由核并入 EX 结果选择器。
//
//  语义与 tb/golden/epv_iss.py、sw/inc/epv32_hw.h 逐条对齐;
//  tools/check_encoding_consistency.py 三方比对编码表。
// ============================================================================
`include "epv32_defs.vh"

module epv32_epv (
  input  wire         clk,
  input  wire         rst_n,
  // ---- EX 级送来的指令与已前递操作数
  input  wire         i_valid,
  input  wire         i_is_c0,      // opcode == CUSTOM0
  input  wire [2:0]   i_f3,
  input  wire [6:0]   i_f7,
  input  wire [4:0]   i_rd,         // vd (搬运/计算) 或 rd (VDOT8/XMV/CFGR)
  input  wire [4:0]   i_rs1f,       // vs1 号; 对 LVB/SVB/MVX/CFG 同时也是 GPR 号
  input  wire [4:0]   i_rs2f,       // vs2 号
  input  wire [31:0]  i_gpr1,
  input  wire [31:0]  i_gpr2,       // 预留: 标量-向量混合运算用, 当前未接入
  input  wire [11:0]  i_imm,
  // ---- 数据总线申请 (由核与 MEM 级访存做仲裁)
  output wire         o_mem_req,
  output wire [31:0]  o_mem_addr,
  output wire         o_mem_we,
  output wire [31:0]  o_mem_wdata,
  input  wire [31:0]  i_mem_rdata,
  input  wire         i_mem_grant,
  // ---- 握手与结果
  output wire [31:0]  o_xres,
  output wire         o_xwr,
  output wire         o_done,
  output wire         o_busy,
  output wire         o_illegal,
  output reg  [31:0]  o_vcfg,
  // ---- 可见性 (仿真断言 / 覆盖率用)
  output wire [127:0] o_v0,
  output wire         o_vrf_wr
);
  localparam NV = `EPV_VRF_WORDS;

  // ------------------------------------------------------------ 向量寄存器堆
  reg [127:0] vrf [0:NV-1];
  wire [2:0]   vd_n  = i_rd[2:0];
  wire [2:0]   vs1_n = i_rs1f[2:0];
  wire [2:0]   vs2_n = i_rs2f[2:0];
  wire [127:0] s1    = vrf[vs1_n];
  wire [127:0] s2    = vrf[vs2_n];
  wire [127:0] sdst  = vrf[vd_n];
  assign o_v0 = vrf[0];

  // ------------------------------------------------------------ 解码
  wire c0_lvb  = i_is_c0 && (i_f3 == `EPV_F3_LVB);
  wire c0_svb  = i_is_c0 && (i_f3 == `EPV_F3_SVB);
  wire c0_mvx  = i_is_c0 && (i_f3 == `EPV_F3_MVX);
  wire c0_xmv  = i_is_c0 && (i_f3 == `EPV_F3_XMV);
  wire c0_cfg  = i_is_c0 && (i_f3 == `EPV_F3_CFG);
  wire c0_cfgr = i_is_c0 && (i_f3 == `EPV_F3_CFGR);
  wire c0_ok   = c0_lvb | c0_svb | c0_mvx | c0_xmv | c0_cfg | c0_cfgr;

  wire c1_dot   = !i_is_c0 && (i_f7 == `EPV_F7_VDOT8);
  wire c1_mac4  = !i_is_c0 && (i_f7 == `EPV_F7_VMAC4);
  wire c1_add8  = !i_is_c0 && (i_f7 == `EPV_F7_VADD8);
  wire c1_req4  = !i_is_c0 && (i_f7 == `EPV_F7_VREQ4);
  wire c1_max8  = !i_is_c0 && (i_f7 == `EPV_F7_VMAX8);
  wire c1_relu8 = !i_is_c0 && (i_f7 == `EPV_F7_VRELU8);
  wire c1_sra4  = !i_is_c0 && (i_f7 == `EPV_F7_VSRA4);
  wire c1_zero  = !i_is_c0 && (i_f7 == `EPV_F7_VZERO);
  wire c1_pool2 = !i_is_c0 && (i_f7 == `EPV_F7_VPOOL2);
  wire c1_sub8  = !i_is_c0 && (i_f7 == `EPV_F7_VSUB8);
  wire c1_min8  = !i_is_c0 && (i_f7 == `EPV_F7_VMIN8);
  wire c1_ok    = c1_dot | c1_mac4 | c1_add8 | c1_sub8 | c1_max8 | c1_min8 |
                  c1_relu8 | c1_sra4 | c1_req4 | c1_zero | c1_pool2;

  wire c1_f3_bad = !i_is_c0 && (i_f3 != 3'd0);
  /* 向量号字段的非法判定。三条规矩, 逐字段写清, 不用条件运算符:
   *   [11:7] 是向量号  <=> 除 XMV / CFGR / VDOT8 之外的所有 EPV 指令
   *                      (那三条的 [11:7] 是真正的 GPR 目的寄存器, 允许 0..31)
   *   [24:20] 是向量号 <=> 用到 vs2 的计算类
   *   [19:15] 是向量号 <=> 用到 vs1 的计算类 (CUSTOM0 的 rs1 是 GPR 基址/源)
   * 只允许低 3 位非零, 高 2 位必须为 0。 */
  wire rd_is_vec  = c0_mvx |
                    c1_mac4 | c1_add8 | c1_sub8 | c1_max8 | c1_min8 |
                    c1_req4 | c1_relu8 | c1_sra4 | c1_zero | c1_pool2;
  wire rs2_is_vec = c0_lvb | c0_svb |
                    c1_mac4 | c1_add8 | c1_sub8 | c1_max8 | c1_min8 | c1_req4;
  wire rs1_is_vec = c1_mac4 | c1_add8 | c1_sub8 | c1_max8 | c1_min8 | c1_req4 |
                    c1_relu8 | c1_sra4 | c1_pool2;
  wire field_bad = ((i_rd   [4:3] != 2'b00) & rd_is_vec)  |
                   ((i_rs2f [4:3] != 2'b00) & rs2_is_vec) |
                   ((i_rs1f [4:3] != 2'b00) & rs1_is_vec);


  assign o_illegal = i_valid && (field_bad || c1_f3_bad ||
                                 (i_is_c0 && !c0_ok) || (!i_is_c0 && !c1_ok));
  wire active = i_valid && !o_illegal;
  wire is_mem = c0_lvb | c0_svb;
  /* SVB 刻意编成 I 型: 源向量号占 [11:7] 而不是 S 型的 [24:20]。
   * 原因是本机 binutils 不支持 ".insn s"/".insn ss" (实测报
   * "illegal operands ... extension `f' required"), 而 ".insn i" 可用;
   * I 型还顺带保住了 12 位偏移量。软件侧 epv32_hw.h 的 epv_svb() 与此一致,
   * 由 tools/check_encoding_consistency.py 汇编-反汇编逐位核对。 */
  /* LVB/SVB 现在是 R 型: 向量号在 [24:20] (rs2 字段), 基址就是 rs1, 不带
   * 立即数。改用 R 型的原因: 本机 GAS 对 ".insn i" 的 simm12 操作数会填进
   * 别的东西 (实测 epv_lvb(0,0,bs) 编出 0x0004800b, imm=72 而不是 0),
   * 而 ".insn r" 的六个字段逐位核对正确。 */
  wire [2:0]   mv_n    = i_rs2f[2:0];
  wire [127:0] svb_src = vrf[mv_n];
  wire mem_ph = active && is_mem;         // 本拍真的处于搬运流程

  // ------------------------------------------------------------ vcfg 字段
  wire        v_s_sat = (o_vcfg[7:0] > 8'd31);
  wire [4:0]  v_s     = v_s_sat ? 5'd31 : o_vcfg[4:0];
  wire        v_rnd   = o_vcfg[16];
  wire        v_sat   = o_vcfg[17];
  wire signed [7:0] v_rb = o_vcfg[15:8];

  // ------------------------------------------------------------ 饱和函数
  function [7:0] sat8_9;
    input signed [8:0] v;
    input              sat;
    begin
      if      (sat &&  v > 9'sd127)   sat8_9 =  8'sd127;
      else if (sat &&  v < -9'sd128)  sat8_9 = -8'sd128;
      else                            sat8_9 =  v[7:0];
    end
  endfunction

  function [7:0] sat8_64;
    input signed [63:0] v;
    begin
      if      (v > 64'sd127)   sat8_64 =  8'sd127;
      else if (v < -64'sd128)  sat8_64 = -8'sd128;
      else                     sat8_64 =  v[7:0];
    end
  endfunction

  // ------------------------------------------------------------ int8 逐 lane
  wire [127:0] r_add8, r_sub8, r_max8, r_min8, r_relu8, r_pool2;
  genvar g;
  generate
    for (g = 0; g < 16; g = g + 1) begin : G_I8
      wire signed [7:0]  x  = s1[g*8 +: 8];
      wire signed [7:0]  y  = s2[g*8 +: 8];
      wire signed [8:0]  su = {x[7], x} + {y[7], y};
      wire signed [8:0]  ss = {x[7], x} - {y[7], y};
      assign r_add8[g*8 +: 8]  = sat8_9(su, v_sat);
      assign r_sub8[g*8 +: 8]  = sat8_9(ss, v_sat);
      assign r_max8[g*8 +: 8]  = (x > y) ? x : y;
      assign r_min8[g*8 +: 8]  = (x < y) ? x : y;
      assign r_relu8[g*8 +: 8] = (x < 8'sd0) ? 8'sd0 : x;
    end
    for (g = 0; g < 8; g = g + 1) begin : G_POOL
      wire signed [7:0] p = s1[g*16 +: 8];
      wire signed [7:0] q = s1[g*16 + 8 +: 8];
      assign r_pool2[g*8 +: 8] = (p > q) ? p : q;
    end
  endgenerate
  assign r_pool2[127:64] = 64'h0;

  // ------------------------------------------------------------ 16 路 8x8 乘积
  wire [16*18-1:0] prod18;
  generate
    for (g = 0; g < 16; g = g + 1) begin : G_DOT
      assign prod18[g*18 +: 18] = $signed(s1[g*8 +: 8]) * $signed(s2[g*8 +: 8]);
    end
  endgenerate

  // ------------------------------------------------------------ VDOT8 归约
  reg signed [31:0] dotv;
  integer kd;
  always @* begin
    dotv = 32'sd0;
    for (kd = 0; kd < 16; kd = kd + 1)
      dotv = dotv + $signed({{14{prod18[kd*18 + 17]}}, prod18[kd*18 +: 18]});
  end

  // ------------------------------------------------------------ VMAC4
  // vd.w[j] = vd.w[j] + sum_{m=0..3} vs1.b[4j+m] * vs2.b[4j+m]
  wire [127:0] r_mac4;
  generate
    for (g = 0; g < 4; g = g + 1) begin : G_MAC
      wire signed [31:0] acc0 = $signed(sdst[g*32 +: 32]);
      wire signed [17:0] q0   = $signed(prod18[(4*g+0)*18 +: 18]);
      wire signed [17:0] q1   = $signed(prod18[(4*g+1)*18 +: 18]);
      wire signed [17:0] q2   = $signed(prod18[(4*g+2)*18 +: 18]);
      wire signed [17:0] q3   = $signed(prod18[(4*g+3)*18 +: 18]);
      assign r_mac4[g*32 +: 32] = acc0
            + $signed({{14{q0[17]}}, q0}) + $signed({{14{q1[17]}}, q1})
            + $signed({{14{q2[17]}}, q2}) + $signed({{14{q3[17]}}, q3});
    end
  endgenerate

  // ------------------------------------------------------------ VSRA4
  wire [127:0] r_sra4;
  generate
    for (g = 0; g < 4; g = g + 1) begin : G_SRA
      wire signed [31:0] xv    = $signed(s1[g*32 +: 32]);
      wire signed [31:0] bias  = (v_rnd && (v_s != 5'd0)) ? (32'sd1 << (v_s - 5'd1))
                                                          : 32'sd0;
      wire        [32:0] sum9  = {xv[31], xv} + {bias[31], bias};
      wire [32:0] rsh9 = $signed(sum9) >>> v_s;
      assign r_sra4[g*32 +: 32] = rsh9[31:0];
    end
  endgenerate

  // ------------------------------------------------------------ VREQ4
  //  t = (acc * M + 2^30) >>> 31;   r = (t + rnd) >>> S;   o = sat8(r + RB)
  wire [127:0] r_req4;
  generate
    for (g = 0; g < 4; g = g + 1) begin : G_REQ
      wire signed [31:0] av  = $signed(s1[g*32 +: 32]);
      wire signed [31:0] mv  = $signed(s2[g*32 +: 32]);
      wire signed [63:0] prd = { {32{av[31]}}, av } * { {32{mv[31]}}, mv };
      wire signed [63:0] hi  = (prd + 64'sh4000_0000) >>> 31;
      wire signed [63:0] rnd = (v_rnd && (v_s != 5'd0)) ? (64'sd1 << (v_s - 5'd1))
                                                        : 64'sd0;
      wire signed [63:0] sh  = (hi + rnd) >>> v_s;
      /* RB 是有符号 int8 零点偏置, 必须**符号**扩展: 原先拼成 {55'b0,v_rb}
       * 即零扩展, RB=-1 会变成 +255。自测此前只覆盖正 RB, 抓不到。 */
      wire signed [63:0] rb_ext = {{56{v_rb[7]}}, v_rb};
      wire signed [63:0] of  = sh + rb_ext;
      assign r_req4[g*8 +: 8] = sat8_64(of);
    end
  endgenerate
  assign r_req4[127:32] = 96'h0;

  // ------------------------------------------------------------ MVX / 搬运拼接
  wire [127:0] r_mvx = {4{i_gpr1}};

  reg  [1:0]   wc;
  reg  [127:0] macc;
  reg          mrun;
  /* 立即数必须按 I 型语义符号扩展: 零扩展会让负偏移 (如 lw 的 -10) 算成
   * rs1+4086, 静默读到别处去。 */
  /* 基址同样需要快照: LVB/SVB 的 4 次总线访问跨多拍, 而这期间 rs1 的前递源
   * 会排空。start_addr 打上一次, 整段搬运用它。 */
  reg  [31:0]  base_q;
  wire         addr_start = i_valid && is_mem && !o_illegal && !mrun;
  wire [31:0]  base       = mrun ? base_q : i_gpr1;
  wire         granted  = mem_ph && i_mem_grant;
  wire [127:0] macc_nxt = macc | ({96'b0, i_mem_rdata} << {wc, 5'b0});
  wire         mem_fin  = granted && (wc == 2'd3);

  assign o_mem_req   = mem_ph;
  /* wc 是「第几个字」(0..3), 地址偏移要乘 4: 之前写成 base + wc,
   * 于是四次访问落在 base..base+3 同一个字内 (且 1/2/3 是非对齐地址),
   * 而 RAM 按 addr[..:2] 索引 -> 四次都读到同一个字。
   * 实测: LVB -> SVB 逐字节回环拿不到源数据。 */
  assign o_mem_addr  = base + {28'b0, wc, 2'b00};
  assign o_mem_we    = c0_svb && mem_ph;
  assign o_mem_wdata = svb_src[{wc, 5'b0} +: 32];

  // ------------------------------------------------------------ 结果选择
  wire [127:0] res_vec =
      c0_lvb   ? macc_nxt
    : c0_mvx   ? r_mvx
    : c1_mac4  ? r_mac4
    : c1_add8  ? r_add8
    : c1_sub8  ? r_sub8
    : c1_max8  ? r_max8
    : c1_min8  ? r_min8
    : c1_relu8 ? r_relu8
    : c1_sra4  ? r_sra4
    : c1_req4  ? r_req4
    : c1_pool2 ? r_pool2
    : c1_zero  ? 128'h0
    :            128'h0;

  wire vec_wr = c0_lvb | c0_mvx | c1_mac4 | c1_add8 | c1_sub8 | c1_max8 |
                c1_min8 | c1_relu8 | c1_sra4 | c1_req4 | c1_pool2 | c1_zero;

  assign o_xres = c1_dot  ? dotv
                : c0_xmv  ? s2[31:0]      /* XMV 的向量号在 [24:20] => s2, 不是 s1 */
                : c0_cfgr ? o_vcfg
                :           32'h0;
  assign o_xwr  = active && (c1_dot | c0_xmv | c0_cfgr);

  assign o_done = mem_ph ? mem_fin : active;
  assign o_busy = i_valid && !o_done;
  assign o_vrf_wr = o_done && vec_wr;

  // ------------------------------------------------------------ 时序部分
  integer vi;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      wc     <= 2'd0;
      macc   <= 128'h0;
      o_vcfg <= 32'h0;
      for (vi = 0; vi < NV; vi = vi + 1) vrf[vi] <= 128'h0;
    end else begin
      /* 地址快照单独一条 if。之前把它塞进了搬运链的 if/else if 里, 结果
       * else if (granted) 挂到了 if (addr_start) 上 —— 第 1 拍的传输整拍被
       * 跳过, LVB -> SVB 回环读回全 0 (实测 epv_bisect 的 A 段)。 */
      if (addr_start)            base_q <= i_gpr1;

      /* 每条搬运指令的状态必须在「本条完成拍」清空, 不能等「下一拍没有搬运指令」。
       * 原写法在两条搬运指令紧邻时永不触发: LVB 完成拍的下一拍 SVB 已经进了 EX,
       * mem_ph 全程为 1 -> mrun 保持 1 -> addr_start 不再触发 -> SVB 沿用 LVB 的
       * base_q 与 wc=3, 只写一个字且写错地址 (实测 epv_bisect 的 dst 全 0,
       * 而波形里 LVB 自己拼出的 0xAFAEADAC_A7A6A5A4_A3A2A1A0 是正确的)。 */
      if (mem_fin || !mem_ph) begin
        wc <= 2'd0; macc <= 128'h0; mrun <= 1'b0;
      end else begin
        if (!mrun)       mrun <= 1'b1;
        if (granted) begin
          if (c0_lvb)    macc <= macc_nxt;
          if (wc != 2'd3) wc <= wc + 2'd1;
        end
      end
      if (o_done && vec_wr)      vrf[(c0_lvb ? mv_n : vd_n)] <= res_vec;
      if (o_done && c0_cfg)      o_vcfg    <= i_gpr1 & 32'h0003_FFFF;
    end
  end

  /* i_gpr2 由核侧的前递结果 fwd2 驱动, 当前不参与任何架构可见输出。
   * 「不参与」不是注释而是被实测过的一条性质: tb/epv_unit.cpp 的 GPR2 反事实臂
   * 把该端口换成全 1 激励, 17 条指令的输出逐位不变 (归档 results/epv_unit/)。
   * 所以它不是悬空端口, 而是一个已证明惰性的预留接口 —— 保留它比删掉它便宜:
   * 删要动核侧连线, 而 fwd2 本来就已经算好了。
   * 本行也放在 `ifdef 之外: _unused_ok 那一份吃掉的是 lint, 与断言臂无关。 */
  wire _unused_ok = &{1'b0, i_gpr2, 1'b0};

`ifdef EPV_ASSERTIONS
  `include "epv32_assert_epv.vh"
`endif

endmodule
