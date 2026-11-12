.text
.globl start
start:
    /* i 型 .insn: opcode=0x0b(CUSTOM0), funct3=0, rd=x0, rs1=x10, simm12=0 */
    .insn i 0x0b, 0, x0, x10, 0
    .insn i 0x0b, 0, x0, x10, 72
    .insn i 0x0b, 0, x0, x10, -1
    /* r 型 .insn 作正对照: 六字段应逐位如手册 */
    .insn r 0x0b, 0, 5, x0, x10, x11
