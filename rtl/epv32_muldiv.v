// ============================================================================
//  epv32_muldiv.v  --  RV32M 乘除单元
//
//  乘法  : 单周期。只用一个 64bit 无符号乘法器, 有符号的高 32 位靠符号修正:
//            (sa*sb) = (ua*ub) - a[31]*(b<<32) - b[31]*(a<<32)
//          即 MULH = hi - (a31?b:0) - (b31?a:0); MULHSU = hi - (a31?b:0)。
//          不走 "$signed()*$signed()" 三条积: 拼接结果在 Verilog 里恒为无符号,
//          三目链又会把符号上下文重传一遍 —— 实测有一版让 mulhu / mulhsu 互换
//          (rv32um test_7 两边各拿到了对方的值)。改成一个可手算的减法。
//
//  除法  : 32 拍移位-减法, 余数寄存器 33bit (移位后可能借到一位)。
//          操作数在 start 那一拍打快照, 运行期只吃快照 —— 核会把本指令冻在 EX
//          达 33 拍, 而 EX 的前递源每拍在往前流并最终排空, 直接吃 i_a/i_b 会
//          退化成 ID 阶段读到的陈旧值。
//          快照必须在 start 拍从 i_a/i_b **现算**; 上一版在同一个时钟沿里
//          既「装载 a_q」又「用 a_q 算 a_abs 去装 quo」, 于是 quo 装的是上一次
//          除法的被除数 (实测 rv32um/div 的 20/6 得到 0)。
//
//  握手  : o_busy / o_done 均为组合输出, 结果在完成拍组合有效; 核在
//          (i_valid && !o_busy) 那一拍把 o_result 写进 EX/MEM。
//          乘法完成拍 = 第 0 拍; 除法完成拍 = 第 33 拍 (1 拍装载 + 32 拍手)。
//
//  角例  : 除零 -> DIV/DIVU = -1, REM/REMU = 被除数;
//          INT_MIN / -1 -> 商 = INT_MIN, 余数 = 0。 (均按 RISC-V 规范)
//          除零那一支用的是**原始被除数**而不是 |被除数|：上一版取的是 dvnd_abs,
//          于是 `rem` 在被除数为负、除数为 0 时给的是 |-a| 而不是 a
//          (实测 rem x17,x22,x23 且 x22=0xffeaf37c, x23=0 -> 核给 0x00150c84,
//           黄金模型给 0xffeaf37c)。官方 rv32um/p-rem 的八个用例不含除零角例，
//           所以「rv32um 8/8 通过」覆盖不到这一格；抓到它的是随机指令流差分
//           (`flow/rand_trace.py`, 种子 20260926 的第 94 拍)。
// ============================================================================

module epv32_muldiv (
  input  wire        clk,
  input  wire        rst_n,
  input  wire        i_valid,    // EX 级正持有乘/除指令 (冻结期间保持稳定)
  input  wire [2:0]  i_op,       // 与 funct3 同值, 见下方 localparam
  input  wire [31:0] i_a,
  input  wire [31:0] i_b,
  output wire [31:0] o_result,
  output wire        o_done,     // 本拍结果有效
  output wire        o_busy      // 本拍未完成, 核须冻结流水线
);
  /* RV32M 的 funct3 (卷一 27.2):
   *   000 MUL   001 MULH   010 MULHSU  011 MULHU
   *   100 DIV   101 DIVU   110 REM     111 REMU
   * MULHSU 在前 (2)、MULHU 在后 (3), 与字母序相反; 这里曾抄反一次。 */
  localparam M_MUL    = 3'd0, M_MULH  = 3'd1, M_MULHSU = 3'd2, M_MULHU = 3'd3,
             M_DIV    = 3'd4, M_DIVU  = 3'd5, M_REM    = 3'd6, M_REMU  = 3'd7;

  wire is_div_op = (i_op == M_DIV) || (i_op == M_DIVU) ||
                   (i_op == M_REM) || (i_op == M_REMU);
  wire is_rem    = (i_op == M_REM) || (i_op == M_REMU);
  wire is_udiv   = (i_op == M_DIVU) || (i_op == M_REMU);

  // ------------------------------------------------------------ 乘法通路
  wire [63:0] p_uu   = { 32'b0, i_a } * { 32'b0, i_b };
  wire [31:0] hi_u   = p_uu[63:32];
  wire [31:0] corr_a = i_a[31] ? i_b : 32'h0;   // a 按有符号解释时要多减的部分
  wire [31:0] corr_b = i_b[31] ? i_a : 32'h0;

  wire [31:0] mul_res =
      (i_op == M_MUL)    ? p_uu[31:0]
    : (i_op == M_MULH)   ? (hi_u - corr_a - corr_b)
    : (i_op == M_MULHU)  ? hi_u
    : (i_op == M_MULHSU) ? (hi_u - corr_a)
    : 32'h0;

  // ------------------------------------------------------------ 除法: 活动值
  /* 下面四个 *_lv (live) 只允许在 start 那一拍被消费一次。 */
  wire [31:0] a_abs_lv = ((!is_udiv) && i_a[31]) ? (~i_a + 32'd1) : i_a;
  wire [31:0] b_abs_lv = ((!is_udiv) && i_b[31]) ? (~i_b + 32'd1) : i_b;
  wire        negq_lv  = (!is_udiv) && (i_a[31] ^ i_b[31]);
  wire        ovf_lv   = (!is_udiv) && (i_a == 32'h8000_0000) && (i_b == 32'hFFFF_FFFF);
  wire        zer_lv   = (i_b == 32'd0);

  // ------------------------------------------------------------ 除法: 快照
  reg  [31:0] dvnd_abs;    // |被除数|
  reg  [31:0] dvnd_raw;    // 原始被除数 —— REM/REMU 除零那一支要的就是它(规范: 余数=被除数)
  reg  [31:0] dvrs_abs;    // |除数|
  reg         rsign_r;     // REM 结果的符号跟随被除数, 但 REMU 无符号不跟随
  reg         negq_r;
  reg         zer_r;
  reg         ovf_r;
  reg         is_rem_r;

  reg  [32:0] rem;
  reg  [31:0] quo;
  reg  [5:0]  cnt;
  reg         running;

  wire start = i_valid && is_div_op && !running;
  wire last  = running && (cnt == 6'd31);

  wire [32:0] rem_shift = {rem[31:0], quo[31]};
  wire        do_sub    = (rem_shift >= {1'b0, dvrs_abs});
  wire [32:0] rem_next  = do_sub ? (rem_shift - {1'b0, dvrs_abs}) : rem_shift;
  wire [31:0] quo_next  = {quo[30:0], do_sub};

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      running  <= 1'b0;  rem <= 33'd0;  quo <= 32'd0;  cnt <= 6'd0;
      dvnd_abs <= 32'd0; dvnd_raw <= 32'd0; dvrs_abs <= 32'd0;
      rsign_r  <= 1'b0;  negq_r <= 1'b0;
      zer_r    <= 1'b0;  ovf_r <= 1'b0;  is_rem_r <= 1'b0;
    end else if (start) begin
      dvnd_abs <= a_abs_lv;
      dvnd_raw <= i_a;          // 原始位型：REM/REMU 除零时按规范原样给出
      dvrs_abs <= b_abs_lv;
      rsign_r  <= (!is_udiv) && i_a[31];   /* 曾写成 i_a[31] -> remu 在 bit31=1 的
                                              无符号被除数上把余数取反 */
      negq_r   <= negq_lv;
      zer_r    <= zer_lv;
      ovf_r    <= ovf_lv;
      is_rem_r <= is_rem;
      rem      <= 33'd0;
      quo      <= a_abs_lv;     // 第 1 步要用的商寄存器初值 = |被除数|
      cnt      <= 6'd0;
      running  <= 1'b1;
    end else if (running) begin
      rem <= rem_next;
      quo <= quo_next;
      if (cnt == 6'd31) begin running <= 1'b0; cnt <= 6'd0; end
      else                     cnt <= cnt + 6'd1;
    end
  end

  /* 完成拍: rem_next / quo_next 就是第 32 步之后的值, 不需要再等一拍 */
  wire [31:0] q_raw = negq_r   ? (~quo_next + 32'd1)        : quo_next;
  wire [31:0] r_raw = rsign_r  ? (~rem_next[31:0] + 32'd1)  : rem_next[31:0];

  wire [31:0] div_final =
      zer_r    ? (is_rem_r ? dvnd_raw : 32'hFFFF_FFFF)
    : ovf_r    ? (is_rem_r ? 32'd0 : 32'h8000_0000)
    : is_rem_r ? r_raw
    :            q_raw;

  assign o_done   = i_valid && (is_div_op ? last : 1'b1);
  assign o_busy   = i_valid && is_div_op && !last;
  assign o_result = is_div_op ? div_final : mul_res;

endmodule
