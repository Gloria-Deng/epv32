// ============================================================================
//  epv32_soc.v  --  片上外设 + 总线 + SoC 顶层
//
//  内含 4 个模块:
//    epv32_uart  : 真实可上板的 8N1 发送/接收器 (分频 + 移位寄存器), 非纯仿真桩
//    epv32_timer : 64bit mtime + mtimecmp -> 机器计时中断
//    epv32_sysc  : 平台签名 / 仿真结束提交口
//    epv32_soc   : 核 + RAM + 外设 + 地址译码
// ============================================================================
`include "epv32_defs.vh"

// ------------------------------------------------------------------ UART
module epv32_uart #(
  parameter DEFAULT_DIV = 16'd434           // 100 MHz / 16 / 115200
)(
  input  wire        clk,
  input  wire        rst_n,
  input  wire        en,
  input  wire        we,
  input  wire [5:0]  off,
  input  wire [31:0] wdata,
  output wire  [31:0] rdata,
  output wire        o_tx,
  output wire        o_tx_busy,
  input  wire        i_rx
);
  reg  [15:0] divisor;
  reg  [7:0]  tx_byte;
  reg         tx_start;
  reg  [3:0]  tx_bit;
  reg  [15:0] tx_cnt;
  reg  [9:0]  tx_sh;            // {idle, msb..lsb, start}
  reg         tx_run;

  reg         rx_prev;
  reg  [15:0] rx_cnt;
  reg  [3:0]  rx_bit;
  reg  [2:0]  rx_ph;            // 0=空闲 1=起点校准 2=数据位 3=停止位
  reg  [7:0]  rx_data;
  reg         rx_valid;

  wire tx_idle = !tx_run;
  assign o_tx      = tx_sh[0];
  assign o_tx_busy = tx_run;
  wire [15:0] half = (divisor >> 1);

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      divisor <= DEFAULT_DIV; tx_run <= 1'b0; tx_sh <= 10'h3FF;
      tx_cnt <= 16'd0; tx_bit <= 4'd0; ;
      rx_ph <= 3'd0; rx_cnt <= 16'd0; rx_bit <= 4'd0;
      rx_data <= 8'h0; rx_valid <= 1'b0; rx_prev <= 1'b1;
    end else begin
      // ---------------- 编程
      if (en && we) begin
        case (off)
          `UART_OF_DATA: begin tx_byte <= wdata[7:0]; tx_start <= 1'b1; end
          `UART_OF_DIV:  divisor <= (wdata[15:0] == 16'd0) ? 16'd1 : wdata[15:0];
          default: ;
        endcase
      end else tx_start <= 1'b0;

      // ---------------- 发送: {stop, data[7:0], start} 从位 0 起移出
      if (tx_start && tx_idle) begin
        tx_sh  <= {1'b1, tx_byte, 1'b0};
        tx_run <= 1'b1; tx_cnt <= 16'd0; tx_bit <= 4'd0;
      end else if (tx_run) begin
        if (tx_cnt == divisor - 16'd1) begin
          tx_cnt <= 16'd0;
          tx_sh  <= {1'b1, tx_sh[9:1]};
          if (tx_bit == 4'd9) tx_run <= 1'b0;
          else                tx_bit <= tx_bit + 4'd1;
        end else tx_cnt <= tx_cnt + 16'd1;
      end

      // ---------------- 接收: 起点下降沿 -> 半波特校准 -> 每整波特采一位
      rx_prev <= i_rx;
      rx_valid <= rx_valid & ~read_rx;
      case (rx_ph)
        3'd0: if (rx_prev === 1'b1 && i_rx === 1'b0) begin
                rx_ph <= 3'd1; rx_cnt <= 16'd0; rx_bit <= 4'd0;
              end
        3'd1: begin                              // 走到起点中点
                if (rx_cnt == half - 16'd1) begin
                  rx_cnt <= 16'd0; rx_ph <= (i_rx === 1'b0) ? 3'd2 : 3'd0;
                end else rx_cnt <= rx_cnt + 16'd1;
              end
        3'd2: begin                              // 依次采 8 个数据位 (LSB first)
                if (rx_cnt == divisor - 16'd1) begin
                  rx_cnt <= 16'd0;
                  if (i_rx === 1'b1) rx_data[rx_bit] <= 1'b1;
                  if (rx_bit == 4'd7) rx_ph <= 3'd3;
                  else                rx_bit <= rx_bit + 4'd1;
                end else rx_cnt <= rx_cnt + 16'd1;
              end
        default: begin                           // 停止位: 收下一帧
                if (rx_cnt == divisor - 16'd1) begin
                  rx_ph    <= 3'd0;
                  rx_valid <= 1'b1;
                end else rx_cnt <= rx_cnt + 16'd1;
              end
      endcase

      // ---------------- 读寄存器
      if (en && !we) begin
        case (off)
          `UART_OF_DATA:   ;
          `UART_OF_STATUS: ;
          `UART_OF_DIV:    ;
          default:         ;
        endcase
      end
    end
  end

  wire read_rx = (en && !we && off == `UART_OF_DATA);

  /* 读端口必须是组合读: 总线协议是「恒 1 周期就绪」, 核在同一个时钟沿
   * 采样 i_dmem_rdata。原先 rdata 是 clocked reg -> 外设读会拿到上一次的值
   * (首次读 STATUS 得到复位值, 读 mtime 慢一拍)。黄金 ISS 独立指出此条。 */
  assign rdata = (off == `UART_OF_DATA)   ? {23'b0, rx_valid, rx_data}
               : (off == `UART_OF_STATUS) ? {30'b0, rx_valid, tx_idle}
               : (off == `UART_OF_DIV)    ? {16'b0, divisor}
               : 32'h0;
endmodule

// ------------------------------------------------------------------ TIMER
module epv32_timer (
  input  wire        clk,
  input  wire        rst_n,
  input  wire        tick,
  input  wire        en,
  input  wire        we,
  input  wire [5:0]  off,
  input  wire [31:0] wdata,
  output wire  [31:0] rdata,
  output wire        o_irq
);
  reg [63:0] mtime;
  reg [63:0] mtimecmp;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      mtime <= 64'h0; mtimecmp <= 64'hFFFF_FFFF_FFFF_FFFF;
      ;
    end else begin
      if (tick) mtime <= mtime + 64'd1;
      if (en && we) begin
        case (off)
          `TMR_OF_MTIME:   mtime[31:0]  <= wdata;
          `TMR_OF_MTIMEHI: mtime[63:32] <= wdata;
          `TMR_OF_CMP:     mtimecmp[31:0]  <= wdata;
          `TMR_OF_CMPHI:   mtimecmp[63:32] <= wdata;
          default: ;
        endcase
      end
      if (en && !we) begin
        case (off)
          `TMR_OF_MTIME:   ;
          `TMR_OF_MTIMEHI: ;
          `TMR_OF_CMP:     ;
          `TMR_OF_CMPHI:   ;
          default:         ;
        endcase
      end
    end
  end
  assign o_irq = (mtime >= mtimecmp);

  assign rdata = (off == `TMR_OF_MTIME)   ? mtime[31:0]
               : (off == `TMR_OF_MTIMEHI) ? mtime[63:32]
               : (off == `TMR_OF_CMP)     ? mtimecmp[31:0]
               : (off == `TMR_OF_CMPHI)   ? mtimecmp[63:32]
               : 32'h0;
endmodule

// ------------------------------------------------------------------ SYSC
module epv32_sysc (
  input  wire        clk,
  input  wire        rst_n,
  input  wire        en,
  input  wire        we,
  input  wire [5:0]  off,
  input  wire [31:0] wdata,
  output wire  [31:0] rdata,
  output reg         o_commit,
  output reg  [31:0] o_commit_val
);
  localparam SYSID = 32'hE01C_9A53;         // "EPV32 SoC" 平台签名
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      o_commit <= 1'b0; o_commit_val <= 32'h0; ;
    end else begin
      o_commit <= 1'b0;
      if (en && we && off == `SYSC_OF_COMMIT) begin
        o_commit     <= 1'b1;
        o_commit_val <= wdata;
      end
      if (en && !we) ;
    end
  end

  assign rdata = (off == `SYSC_OF_SYSID) ? SYSID : 32'h0;
endmodule

// ------------------------------------------------------------------ 译码
module epv32_dec (
  input  wire [31:0] addr,
  output wire        ram,
  output wire        uart,
  output wire        timer,
  output wire        sysc,
  output wire [5:0]  off
);
  assign ram   = (addr >= `ADDR_RAM_BASE   ) && (addr <= `ADDR_RAM_LAST);
  assign uart  = (addr >= `ADDR_UART_BASE  ) && (addr <= `ADDR_UART_LAST);
  assign timer = (addr >= `ADDR_TIMER_BASE ) && (addr <= `ADDR_TIMER_LAST);
  assign sysc  = (addr >= `ADDR_SYSC_BASE  ) && (addr <= `ADDR_SYSC_LAST);
  assign off   = addr[5:0];
endmodule

// ------------------------------------------------------------------ SoC
module epv32_soc #(
  parameter EPV_ENABLE = 1,
  parameter WORDS      = 65536
)(
  input  wire        clk,
  input  wire        rst_n,
  output wire        o_uart_tx,
  input  wire        i_uart_rx,
  // 仿真后门
  input  wire        bld_en,
  input  wire [31:0] bld_addr,
  input  wire [31:0] bld_wdata,
  output wire        o_commit,
  output wire [31:0] o_commit_val,
  // 退休跟踪
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
  output wire [31:0] o_dbg_mepc,
  output wire [31:0] o_dbg_mtval,
  output wire [31:0] o_dbg_mcause,
  output wire [31:0] o_dbg_last_inst,
  output wire [31:0] o_dbg_trap_cnt
);
  wire [31:0] icore_addr, dcore_addr, dcore_wdata, d_rdata;
  wire [3:0]  d_be;
  wire        d_we, d_en;
  wire [31:0] i_rdata;

  wire dec_ram, dec_uart, dec_timer, dec_sysc;
  wire [5:0] dec_off;

  epv32_core #(.EPV_ENABLE(EPV_ENABLE)) u_core (
    .clk(clk), .rst_n(rst_n),
    .o_imem_addr(icore_addr), .i_imem_rdata(i_rdata),
    .o_dmem_addr(dcore_addr), .o_dmem_wdata(dcore_wdata), .o_dmem_be(d_be),
    .o_dmem_we(d_we), .o_dmem_en(d_en), .i_dmem_rdata(d_rdata),
    .i_irq_timer(irq_timer), .i_irq_soft(1'b0), .i_irq_ext(1'b0),
    .o_tr_valid(o_tr_valid), .o_tr_pc(o_tr_pc), .o_tr_rd(o_tr_rd),
    .o_tr_wdata(o_tr_wdata), .o_tr_regwr(o_tr_regwr), .o_tr_memwr(o_tr_memwr),
    .o_tr_addr(o_tr_addr), .o_tr_mdata(o_tr_mdata), .o_tr_mbe(o_tr_mbe),
    .o_mcycle(o_mcycle), .o_minstret(o_minstret), .o_mie(o_mie),
    .o_dbg_mepc(o_dbg_mepc), .o_dbg_mtval(o_dbg_mtval),
    .o_dbg_mcause(o_dbg_mcause), .o_dbg_last_inst(o_dbg_last_inst),
    .o_dbg_trap_cnt(o_dbg_trap_cnt));

  epv32_dec u_dec (.addr(dcore_addr), .ram(dec_ram), .uart(dec_uart),
                   .timer(dec_timer), .sysc(dec_sysc), .off(dec_off));

  epv32_ram #(.WORDS(WORDS)) u_ram (
    .clk(clk),
    .d_en(d_en && dec_ram), .d_we(d_we), .d_be(d_be),
    .d_addr(dcore_addr), .d_wdata(dcore_wdata), .d_rdata(ram_rdata),
    .i_addr(icore_addr), .i_rdata(i_rdata),
    .bld_en(bld_en), .bld_addr(bld_addr), .bld_wdata(bld_wdata));
  wire [31:0] ram_rdata;

  wire [31:0] uart_rdata, timer_rdata, sysc_rdata;
  wire        irq_timer;

  // o_tx_busy 有意不引出: 交付顶层没有消费者 (裸机用例靠轮询 UART 数据寄存器),
  // 且会给已经 537 I/O > 188 用户 IO 的顶层再加一根针。故显式留空并就地关掉
  // 这条 lint 判据。
  /* verilator lint_off PINCONNECTEMPTY */
  epv32_uart u_uart (.clk(clk), .rst_n(rst_n),
    .en(d_en && dec_uart), .we(d_we), .off(dec_off),
    .wdata(dcore_wdata), .rdata(uart_rdata),
    .o_tx(o_uart_tx), .o_tx_busy(), .i_rx(i_uart_rx));
  /* verilator lint_on PINCONNECTEMPTY */

  epv32_timer u_timer (.clk(clk), .rst_n(rst_n), .tick(1'b1),
    .en(d_en && dec_timer), .we(d_we), .off(dec_off),
    .wdata(dcore_wdata), .rdata(timer_rdata), .o_irq(irq_timer));

  epv32_sysc u_sysc (.clk(clk), .rst_n(rst_n),
    .en(d_en && dec_sysc), .we(d_we), .off(dec_off),
    .wdata(dcore_wdata), .rdata(sysc_rdata),
    .o_commit(o_commit), .o_commit_val(o_commit_val));

  assign d_rdata = dec_uart  ? uart_rdata
                 : dec_timer ? timer_rdata
                 : dec_sysc  ? sysc_rdata
                 :             ram_rdata;
endmodule
