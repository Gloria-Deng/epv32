// ============================================================================
//  epv32_ram.v  --  主存行为模型 (哈佛双口: 取指口 + 数据口)
//
//  读为异步 (组合) 读、写为同步 + 4 个字节使能。核的总线协议据此定为
//  「恒 1 周期就绪」, 见 docs/01_设计规格.md 的「存储子系统假设」一节。
//
//  刻意不进核的综合预算: 异步读的大容量阵列在任何 FPGA 上都无法用 BRAM
//  实现, 所以面积口径分两处给 -- 核/扩展/外设单独综合, 主存按容量单独折算。
//  本文件只被仿真与 SoC 级流程使用。
// ============================================================================

module epv32_ram #(
  parameter WORDS = 65536                    // 256 KiB, 与地址图一致
)(
  input  wire         clk,
  // 数据口
  input  wire         d_en,
  input  wire         d_we,
  input  wire [3:0]   d_be,
  input  wire [31:0]  d_addr,
  input  wire [31:0]  d_wdata,
  output wire [31:0]  d_rdata,
  // 取指口
  input  wire [31:0]  i_addr,
  output wire [31:0]  i_rdata,
  // 仿真后门: 测试台预装载镜像与导出结果
  input  wire         bld_en,
  input  wire [31:0]  bld_addr,
  input  wire [31:0]  bld_wdata
);
  localparam AW = $clog2(WORDS);

  reg [31:0] mem [0:WORDS-1];

  wire [AW-1:0] didx = d_addr[AW+1:2];
  wire [AW-1:0] iidx = i_addr[AW+1:2];
  wire [AW-1:0] bidx = bld_addr[AW+1:2];

  assign d_rdata = mem[didx];
  assign i_rdata = mem[iidx];

  always @(posedge clk) begin
    if (bld_en) begin
      mem[bidx] <= bld_wdata;
    end else if (d_en && d_we) begin
      if (d_be[0]) mem[didx][7:0]   <= d_wdata[7:0];
      if (d_be[1]) mem[didx][15:8]  <= d_wdata[15:8];
      if (d_be[2]) mem[didx][23:16] <= d_wdata[23:16];
      if (d_be[3]) mem[didx][31:24] <= d_wdata[31:24];
    end
  end
endmodule
