// ============================================================================
//  epv32_alu.v  --  RV32I 整数 ALU (含 PASS 直通, 供 LUI / CSR 读复用)
//
//  纯组合。移位量取 b[4:0] (RV32)。SLT/SLTU 结果按无符号零扩展输出。
// ============================================================================
`include "epv32_defs.vh"

module epv32_alu (
  input  wire [3:0]  op,
  input  wire [31:0] a,
  input  wire [31:0] b,
  output wire [31:0] y
);
  wire [4:0]        shamt = b[4:0];
  wire signed [31:0] sa   = a;
  wire signed [31:0] sb   = b;

  wire [32:0] sra33 = $signed({a[31], a}) >>> shamt;
  wire [32:0] srl33 = {1'b0, a} >> shamt;
  wire [31:0] r_sra = sra33[31:0];
  wire [31:0] r_srl = srl33[31:0];

  assign y =
      (op == `ALU_ADD)  ? (a + b)
    : (op == `ALU_SUB)  ? (a - b)
    : (op == `ALU_SLL)  ? (a << shamt)
    : (op == `ALU_SLT)  ? {31'b0, (sa < sb)}
    : (op == `ALU_SLTU) ? {31'b0, (a  < b)}
    : (op == `ALU_XOR)  ? (a ^ b)
    : (op == `ALU_SRL)  ? r_srl
    : (op == `ALU_SRA)  ? r_sra
    : (op == `ALU_OR)   ? (a | b)
    : (op == `ALU_AND)  ? (a & b)
    : (op == `ALU_PASS) ? a
    : 32'h0000_0000;

endmodule
