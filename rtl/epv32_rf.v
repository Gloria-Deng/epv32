//  epv32_rf.v — 32×32 通用寄存器堆。
//  读口同周期写穿 bypass 只覆盖 WB/EX 同级的极端情形；跨 2~3 级相关由核内 EX 前递网络处理。

module epv32_rf (
  input  wire        clk,
  input  wire        we,           // 写使能 (WB 级)
  input  wire [4:0]  waddr,
  input  wire [31:0] wdata,
  input  wire [4:0]  raddr1,
  output wire [31:0] rdata1,
  input  wire [4:0]  raddr2,
  output wire [31:0] rdata2
);
  reg [31:0] rf [0:31];

  integer i;
  initial begin
    for (i = 0; i < 32; i = i + 1) rf[i] = 32'h0;
  end

  always @(posedge clk) begin
    if (we && (waddr != 5'd0)) rf[waddr] <= wdata;
  end

  assign rdata1 = (raddr1 == 5'd0)                     ? 32'h0
                : (we && waddr == raddr1)              ? wdata
                : rf[raddr1];
  assign rdata2 = (raddr2 == 5'd0)                     ? 32'h0
                : (we && waddr == raddr2)              ? wdata
                : rf[raddr2];

endmodule
