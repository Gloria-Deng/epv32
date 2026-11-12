#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
#  epv_iss.py -- GOLDEN REFERENCE INSTRUCTION-SET SIMULATOR for the EPV32 core
#
#  WHAT THIS IS
#    纯标准库 (Python 3.11) 的提交级指令模拟器, 覆盖 rtl/epv32_soc.v +
#    rtl/epv32_core.v (RV32IM + Zicsr + machine mode) 与自定义 EPV 扩展
#    (CUSTOM0/CUSTOM1)。它是核对 RTL 的黄金模型: 一次 step() == 一条退休指令,
#    与 o_tr_* 退休轨迹 1:1 对齐; 流水线停顿、32 拍除法器、4 拍 LVB/SVB 都折进单次提交。
#
#  SOURCE OF TRUTH (read these before changing anything in here)
#    rtl/epv32_defs.vh   opcodes / EPV encoding / memory map / exception codes
#    rtl/epv32_epv.v     exact arithmetic of every EPV operation
#    rtl/epv32_core.v    legality decode + trap priority + retire trace
#    rtl/epv32_csr.v     CSR behaviour, interrupt arbitration, mcycle/minstret
#    rtl/epv32_muldiv.v  RV32M corner cases
#    rtl/epv32_ram.v     byte addressed, 4 byte enables, index = addr[AW+1:2]
#
#  DELIBERATE DEVIATIONS FROM THE RTL
#    历史上这里有三条「模型按规范、RTL 有 bug」的开关。到 M8 为止三条都已经在
#    RTL 里修掉了，所以 MIRROR_RTL_DECODE_BUGS 现在**换不出任何行为差异**——
#    留着它不是为了兼容，而是为了让"合流"这件事本身可被反驳：自检里有一条断言
#    专核 rtl_* == spec_*，并且要求 legacy_*（旧 RTL 形状）确实与两者不同，
#    否则那条合流断言就是空跑。三条的来历：
#    MIRROR_RTL_DECODE_BUGS (default False)
#      (1) 旧 epv32_core.v 的 `imm_op_bad` 把每条 SLLI 都判非法、且把 SRAI
#          (inst[30]=1) 与 ORI(imm[10]=1) 一起判非法 —— rv32ui/add 在第一条
#          slli 就 cause=2。现行判据见上面 imm_bad_* 四个函数。
#      (2) 旧 `m_f3_ok` 只收 f3 ∈ {0,1,2,3,6,7}，于是 DIV(f3=4)/DIVU(f3=5)
#          被判非法 —— rv32um/div 栽过。现行 RTL 恒不判非法。
#      (3) 旧 `other_f3_bad` 对 LUI/AUIPC/JAL 也校验 [14:12]，而那是 U/J 型
#          立即数的一部分 —— 任何 imm[14:12]!=0 的 lui/auipc/jal 都陷入。
#          现行 RTL 只对 JALR 校验。
#    MIRROR_RTL_MTVEC_MODE_BIT (default False)
#      epv32_csr.v:81 `wire vmode_vec = mtvec[1];` decodes the vectored mode
#      from bit 1, but mtvec MODE is a 2-bit WARL field where 0 = Direct and
#      1 = Vectored, so the standard encoding mtvec = base|1 selects DIRECT and
#      base|2 selects VECTORED on this hardware -- the opposite of the spec (and
#      of the design brief).  Golden: (mtvec & 3) == 1 means vectored.
#    MIRROR_RTL_BRANCH_OPERAND_BUG (default False)
#      epv32_core.v:254 gives a BRANCH `d_b_imm=1`, and epv32_core.v:367 is
#      `assign alu_b = idex_b_imm ? idex_imm : fwd2;` -- so the branch ALU
#      computes rs1 OP branch_offset and BEQ/BNE/BLT/BGE test THAT result
#      (only BLTU/BGEU, which compare fwd1/fwd2 directly at line 405, work).
#      In hardware BEQ is therefore taken iff rs1 == offset, and BLT iff
#      rs1 < offset.  The golden model compares the two GPR operands; set the
#      flag True to reproduce the hardware's branch decisions exactly.
#    EPV_MEM_ADDR_STRIDE_X4 (default True)
#      Today's epv32_epv.v walks the four beats as base + wc*4 (four
#      consecutive words) -- which is what the golden default models.  Setting
#      it False reproduces the PRE-fix addressing bug (the ledger-defect-11
#      era: base + wc, so all four beats hit one word); the switch exists so
#      that historical failure mode stays reproducible, not because the
#      hardware still behaves that way.
#    EPV_MEM_IMM_UNUSED (default True)
#      The R-type migration removed the immediate from EPV.LVB / EPV.SVB
#      entirely: epv32_epv.v still takes i_imm but nothing consumes it, and
#      the transfer address is exactly rs1 + 4*beat.  The software intrinsic
#      keeps off in the base register for that reason (sw/inc/epv32_hw.h
#      epv_lvb).  Setting it False revives the I-type-era behaviour (adding
#      imm[11:0], zero- or sign-extended per EPV_IMM_ZERO_EXTENDS) so the old
#      encoding-collision analyses stay reproducible; quirk 17 records what
#      that era implied.
#
#  CYCLE MODEL v2 -- READ THIS BEFORE USING mtime / mcycle / UART STATUS / IRQs
#  (design brief item 5; v1 counted 1 cycle per retired instruction, which made
#   mcycle/mtime a lower bound and TIMER-IRQ timing "not a golden property".
#   That is gone: since the EPV trace comparison the ISS carries an EXACT
#   microarchitectural clock, validated point-by-point against the mcycle read
#   values the RTL records in its retirement traces.)
#    One step() == one retired instruction, AND a precise place on the RTL's
#    clock.  For instruction i the model computes the cycle where its EX stage
#    actively evaluates (reset fill: first instruction is in EX at cycle 2,
#    counting the first post-reset cycle as 0), then:
#      +1  load-use hazard: the previous instruction is a LOAD whose rd field
#          matches this instruction's rs1/rs2 FIELD, using the RTL's coarse
#          per-opcode uses_rs1/uses_rs2 wires (epv32_core.v:272-278) -- an EPV
#          op counts as using both fields even when it semantically does not;
#      +2  the previous instruction redirected (taken branch/jump, mret, trap
#          or IRQ): two flushed slots ahead of the target;
#      +1  the previous instruction was a word/half access CROSSING a word
#          boundary: its second MEM beat holds the pipeline one extra cycle
#          (only for single-cycle successors; a DIV/LVB started during that
#          hold absorbs it into its own busy count);
#     +32  DIV/REM/REMU/DIVU frozen in EX (1 start + 32 working beats,
#          epv32_muldiv.v "完成拍 = 第 33 拍");
#      4+g LVB/SVB: 4 granted bus beats; g = grant waits caused by the
#          previous instruction still occupying MEM (1 for a plain load/store,
#          2 if it crossed a word boundary; 0 when a load-use hazard already
#          delayed this instruction past the previous MEM beat).
#    Everything else is 1 cycle.  Bus reads sample the device state at the
#    instruction's MEM cycle (EX+1); LVB/SVB beats sample at their own beat
#    cycles; CSR reads sample mcycle at the EX cycle.  The UART send shift
#    register is modelled edge-by-edge (see _uart_edge), so a STATUS poll
#    returns the live tx_idle bit -- busy-wait loops now wait exactly as long
#    as the hardware does.  mtime is the real free-running clock; mcycle is
#    the same clock unless software writes it or inhibits it (overlay, see
#    _mcycle_now).  `cycles_per_step` / `set_cycle_model()` still exist as a
#    legacy escape hatch: setting either switches the ISS back to flat
#    per-step charging and disables the exact placement.
#    CONSEQUENCE: TIMER-IRQ timing and UART poll values ARE golden properties
#    now, and a mcycle read value in a retirement trace is a point check of
#    the whole model (all of them must match the RTL bit-for-bit).
#
#  RTL_QUIRKS mirrored faithfully by this model (deliberately NOT "fixed"):
#   1. Trap priority is exactly epv32_core.v:425-434: illegal > instruction
#      access fault > branch target > jump target > ebreak > ecall > address
#      misaligned > access fault.
#   2. Control-transfer targets are checked in EX with the SAME expression on
#      both sides: a branch is judged only when it is TAKEN (M5), and both
#      branch and jump targets must be in the memory map AND satisfy IALIGN=32
#      (M7: jalr already cleared bit0, so the alignment test looks at bit1).
#      Either way the cause is 0 with mtval = target.  History: before M5 a
#      not-taken branch trapped anyway; before M7 an in-range but halfword
#      misaligned target did NOT trap at all -- it fetched the aligned word and
#      used the misaligned address as PC, so auipc came out 2 bytes off
#      (measured: results/sim/misalign_bisect_pre_m7.json, uart "C2
#      flag=11ce0000").  Both deviations are gone; this entry documents what
#      the model must NOT reproduce.
#   3. (pre-M6 legacy, kept reachable) addr_ok() used to demand 4-byte
#      alignment for EVERY access, so LB/SB at addr%4!=0 reported LOAD/STORE
#      ACCESS FAULT (5/7).  Now the alignment tier follows the access width
#      (byte: none; half: addr[0]; word: addr[1:0]), and with SUPPORT_MISALIGNED
#      the core splits a crossing access in MEM instead of trapping.  Set
#      ADDR_OK_OLDED_ALIGNMENT=True to reproduce the legacy reading.
#   4. Exceptions enter at mtvec with the low two MODE BITS MASKED OFF
#      (epv32_core.v: `trap_target = irq_now ? csr_irq_target : csr_mtvec_base`
#      where csr_mtvec_base = {mtvec[31:2], 2'b00}); interrupts use
#      mtvec_base / the vectored offset.  An older core line selected o_mtvec
#      verbatim and the model mirrored that (the old quirk 4); under that
#      reading a vectored-mode mtvec sent exceptions to a misaligned address.
#      The CSR REGISTER still holds the raw mode bits unwritten (no WARL
#      masking on write) -- it is only the exception ENTRY that is masked.
#   5. LOAD with f3==7 is not rejected (ld_f3_bad covers only 3 and 6) and
#      behaves like LW; OP / OP-IMM with an unhandled funct3 fall through to ADD.
#   6. Undefined CSR address: reads 0, writes ignored, NO trap (documented in
#      epv32_csr.v:10-12 as an intentional boundary).  Read-only CSRs (mip,
#      misa, mvendorid..., vcfg) also swallow writes.  M9 moved mcycle*/
#      minstret* out of that list: they are writable and the writing
#      instruction suppresses its own increment (quirk 18).
#   7. CSRRS/CSRRC suppress the write when the FORWARDED DATA is 0, not when
#      the register number is x0 (epv32_csr.v:127 tests i_wdata == 32'h0).
#   8. MPP reads as 00 until the first trap / mret / mstatus write forces 11:
#      epv32_csr.v resets mstatus to 0 and only ever assigns bits 12:11.
#   9. mret retires nothing (ex_commit excludes mret_go), so minstret does not
#      count mret and the retire trace has no mret entry.
#  10. EPV field_bad (epv32_epv.v:96-98) is THREE PER-OP MASKS, not an
#      unconditional rd/rs2/rs1 check: rd_is_vec covers MVX plus every CUSTOM1
#      op except VDOT8; rs2_is covers LVB/SVB plus the two-source CUSTOM1 ops;
#      rs1_is covers CUSTOM1 except VDOT8/VZERO.  So XMV / CFG / CFGR / LVB /
#      SVB / VDOT8 all legally take rd >= 8, MVX / XMV / CFG / CFGR may carry
#      rs2[4:3] != 0, and no CUSTOM0 op checks rs1.  This model used to check
#      rd/rs2 unconditionally and its own self test pinned that (as "VDOT8
#      rd=8 -> illegal"); the RTL never rejected those encodings -- the whole
#      program trace comparison died on `EPV.CFGR x11`.  Corrected here and
#      re-pinned per-op, both directions, in self test section 10.
#  11. EPV.XMV reads the rs2 FIELD (epv32_epv.v `c0_xmv ? s2[31:0]`, "XMV 的
#      向量号在 [24:20] => s2"), matching sw/inc/epv32_hw.h epv_xmv(rd, vs2)
#      which puts vs2 in [24:20] and x0 in rs1.  An earlier RTL line read s1
#      and this model mirrored that as the old quirk 11 ("reads vrf[rs1f]");
#      the EPV trace comparison caught the staleness the day the UART cycle
#      model let it reach the first VDOT8 -- a vreg loaded via LVB came back
#      as lane-0-of-v0 instead of the requested vector.  Corrected here; the
#      field-presence legality (XMV's rd is a real GPR, rs2 is a vec number)
#      was already right and stands.
#  12. VREQ4 adds RB SIGN-EXTENDED (int8 zero point): the current epv32_epv.v
#      drives `rb_ext = {{56{v_rb[7]}}, v_rb}`.  History: the first RTL
#      concatenated {55'b0, v_rb} -- unsigned, so RB=0xFF added +255 instead
#      of -1 (ledger defect 13; the golden model flagged it and the old self
#      test only covered positive RB, which is why it could not see the bug).
#      This model mirrored the bug as the old quirk 12 and went stale when
#      the RTL got fixed; the EPV trace comparison caught that too
#      (epv_selftest read +127 where the hardware gives -30).  Both numbers
#      are still pinned, now pointing the right way.
#  13. EPV illegal encodings never reach the bus, and EPV LVB/SVB bus accesses
#      can never fault: the EPV datapath drives neither idex_mem_read nor
#      idex_mem_write, so misaligned/bus_err (gated on c_ld/c_st) cannot fire.
#  14. be_of()'s halfword arm tests off[0] (epv32_core.v:70) where off[1] is the
#      meaningful bit, so SH at addr%4==2 would get be=0b0011 instead of 0b1100.
#      Latent only: such a store never commits because addr_ok() rejects
#      addr%4!=0, so both readings agree on every reachable store.  Kept
#      verbatim for that reason.
#  15. Peripheral reads used to be CLOCKED (rdata <= ... in epv32_soc.v), so a
#      peripheral load returned the PREVIOUS peripheral access's value (STATUS
#      read 3 out of reset); the golden model flagged it (ledger defect 14) and
#      the RTL now drives rdata combinationally (epv32_soc.v:118-124).  The
#      model returns the CURRENT value, which is what today's RTL does; with
#      CYCLE MODEL v2 the UART side of "current" is exact (live tx_idle at the
#      load's MEM cycle), so a STATUS mismatch is once again always a bug.
#  16. epv32_defs.vh:117 documents SYSC CTRL bit0 as "allow commit to end the
#      sim" but epv32_sysc has no CTRL register: ANY write to offset 0 raises
#      commit.  Mirrored (a write to SYSC+0 halts whatever CTRL holds).
#  17. EPV.LVB / EPV.SVB are R-TYPE with the vector number in inst[24:20]
#      (rs2 field): LVB's DESTINATION vector is rs2f[2:0] (epv32_epv.v
#      `vrf[(c0_lvb ? mv_n : vd_n)]`, mv_n = i_rs2f), SVB's SOURCE vector is
#      rs2f[2:0] likewise, rd is x0 in every real encoding, and the immediate
#      field is dead (EPV_MEM_IMM_UNUSED).  On the retirement trace SVB is
#      INVISIBLE as a store: idex_mem_write=0 for CUSTOM0, so o_tr_memwr=0
#      and the testbench prints no S line for it -- the four bus beats exist
#      on the bus but not in the trace.  History: the first encoding was
#      I-type with imm12 = inst[31:20] covering the vs2 field, which made any
#      displacement with imm[4] or imm[3] set unencodable and made "EPV.SVB
#      v6, 0(x9)" transfer from x9+6; the model mirrored that era and its self
#      test pinned it (including four per-SVB store records that the RTL
#      trace never had).  After the R-type migration the collision is gone;
#      the surviving constraint is just the field_bad mask -- the vector
#      number must fit 0..7, i.e. inst[24:20][4:3] == 0 for LVB/SVB.
#  18. minstret / mcycle reads return the value BEFORE the reading instruction
#      counts itself.  As of M9 the RTL increments at the commit stage (EX)
#      rather than at WB -- see epv32_core.v's csr_cnt_wr note for the real
#      ordering bug that forced the move.  The model's observable is unchanged
#      (it counts in program order at commit), so this reading rule stands.
#  19. mcountinhibit.CY(bit0) stops mcycle in the RTL but NOT the SoC's mtime
#      (separate counters in separate modules).  CYCLE MODEL v2 split them:
#      `cycle` is the real free-running clock and drives mtime; the mcycle
#      mirror is an overlay on top of it that honours CY and counter writes
#      (_mcycle_now).  One deliberate simplification: on the edge where one
#      half of mcycle is written, the RTL still increments the OTHER half that
#      same edge (two NBAs, the write only overrides its own half); the overlay
#      suppresses that one increment.  No ISS-vs-RTL comparison reaches a
#      mcycle write today (the suites never read cycle CSRs; micro_bisect has
#      no golden-model arm), so nothing observable rests on the difference.
#      The core-side reading lives in sw/bench/micro_bisect.c item C7.
#  20. PMP *enforcement* (NAPOT/TOR/NA4 match + the three M-mode implicit
#      rules) is in the RTL and in sw/bench/micro_bisect.c, not in this model.
#      None of the three compliance suites can reach it (rv32mi ships pmpaddr
#      only, which tests CSR WARL), so leaving it out cannot mask a mismatch;
#      adding it would mean the trace comparison agrees for a reason it never
#      exercised.  Recorded as an uncovered model path, not as agreement.
# =============================================================================
"""Golden ISS for the EPV32 core: RV32IM + Zicsr + machine mode + EPV ext."""

import sys

__all__ = ["ISS", "StepResult", "Result", "Trap", "SimHalt",
           "ADDR_RAM_BASE", "ADDR_UART_BASE", "ADDR_TIMER_BASE",
           "ADDR_SYSC_BASE"]

# ------------------------------------------------------------------- tunables
MIRROR_RTL_DECODE_BUGS = False       # header deviations 1 and 4
MIRROR_RTL_BRANCH_OPERAND_BUG = False  # header deviation 4 (branches)
MIRROR_RTL_MTVEC_MODE_BIT = False      # header deviation 5 (mtvec mode decode)
EPV_MEM_ADDR_STRIDE_X4 = True        # hardware: four consecutive words
EPV_MEM_IMM_UNUSED = True            # hardware: R-type, the imm field is dead
EPV_IMM_ZERO_EXTENDS = True          # only consulted when EPV_MEM_IMM_UNUSED=0
# rtl/epv32_core.v 的 parameter SUPPORT_UNALIGNED 的镜像:
#   True  = 现在这份核的行为 —— 非对齐访存由硬件拆成两次对齐访问, 不陷入
#   False = 老核的行为 —— 按 cause 4/6 精确陷入
SUPPORT_MISALIGNED = True
# quirk 3 (老核的 addr_ok 要求任何访问都 4 字节对齐) 已经修掉了; 留开关,
# 因为轨迹比对靠的就是"两边同一时刻必须是同一份规则"。
ADDR_OK_OLDED_ALIGNMENT = False

# ------------------------------------------------- opcodes (epv32_defs.vh)
OP_LUI, OP_AUIPC, OP_JAL, OP_JALR, OP_BRANCH, OP_LOAD, OP_STORE = (
    0x37, 0x17, 0x6F, 0x67, 0x63, 0x03, 0x23)
OP_OPIMM, OP_OP, OP_FENCE, OP_SYSTEM, OP_CUSTOM0, OP_CUSTOM1 = (
    0x13, 0x33, 0x0F, 0x73, 0x0B, 0x2B)

# ------------------------------------------------------- ALU ops (defs.vh)
ALU_ADD, ALU_SUB, ALU_SLL, ALU_SLT, ALU_SLTU, ALU_XOR = 0, 1, 2, 3, 4, 5
ALU_SRL, ALU_SRA, ALU_OR, ALU_AND, ALU_MUL, ALU_DIV = 6, 7, 8, 9, 10, 11
ALU_PASS = 15

# ------------------------------------------------- memory map (epv32_defs)
ADDR_RAM_BASE, ADDR_RAM_LAST = 0x8000_0000, 0x8003_FFFF
ADDR_UART_BASE, ADDR_UART_LAST = 0x9000_0000, 0x9000_003F
ADDR_TIMER_BASE, ADDR_TIMER_LAST = 0x9001_0000, 0x9001_003F
ADDR_SYSC_BASE, ADDR_SYSC_LAST = 0x9002_0000, 0x9002_003F

UART_OF_DATA, UART_OF_STATUS, UART_OF_DIV = 0x0, 0x4, 0x8
TMR_OF_MTIME, TMR_OF_MTIMEHI, TMR_OF_CMP, TMR_OF_CMPHI = 0x0, 0x4, 0x8, 0xC
SYSC_OF_COMMIT, SYSC_OF_CTRL, SYSC_OF_SYSID = 0x0, 0x4, 0x8
SYSC_SYSID = 0xE01C_9A53

# ------------------------------------------------ exception codes (defs.vh)
EXC_IAFAIL_ALIGN, EXC_IAF, EXC_ILLEGAL, EXC_BREAK = 0, 1, 2, 3
EXC_LAMF, EXC_LAF, EXC_SAMF, EXC_SAF, EXC_ECM = 4, 5, 6, 7, 11
EXC_NAMES = {0: "instr-address-misaligned", 1: "instr-access-fault",
             2: "illegal-instruction", 3: "breakpoint",
             4: "load-address-misaligned", 5: "load-access-fault",
             6: "store-address-misaligned", 7: "store-access-fault",
             11: "ecall-from-machine-mode"}
IRQ_NAMES = {3: "machine-software-interrupt", 7: "machine-timer-interrupt",
             11: "machine-external-interrupt"}


# ------------------------------------------------ RV32M 的保留编码判据
# 四条规则放在一起，是因为「谁判非法」这件事上模型与 RTL 各有一份实现，
# 一旦两份不同步，逐条轨迹比对就会在某个奇怪的偏移上分叉 —— 而分叉既可能
# 是核错了也可能是模型错了（M8 这次是核错：官方 rv32mi/shamt 只有 ISS 过）。
#   spec   : RISC-V 卷 I（RV32 的 shamt 只有 5 位, inst[25] 必须为 0）
#   rtl    : epv32_core.v 的 imm_op_bad / muldiv_bad, 逐位镜像
#   legacy : M8 之前的 RTL, 只留给自检造反例用
def imm_bad_spec(op, f3, f7):
    return (op == OP_OPIMM) and ((f3 == 1 and f7 != 0x00) or
                                 (f3 == 5 and f7 not in (0x00, 0x20)))


def md_bad_spec(op, f3, f7):
    return False                      # RV32M 八个 funct3 全部合法


def imm_bad_rtl(op, f3, f7):
    return imm_bad_spec(op, f3, f7)   # M8 之后与规范合流


def md_bad_rtl(op, f3, f7):
    return md_bad_spec(op, f3, f7)    # 现在的 RTL 是 `&& !1'b1`, 恒不触发


def imm_bad_legacy(op, f3, f7):
    return (op == OP_OPIMM) and (f3 == 1 or (f3 == 5 and ((f7 >> 5) & 1))
                                 or (f3 == 6 and ((f7 >> 5) & 1)))


def md_bad_legacy(op, f3, f7):
    """旧 RTL 的 muldiv_bad：`m_f3_ok` 只收 {0,1,2,3,6,7}，DIV(4)/DIVU(5) 被漏掉。"""
    return (op == OP_OP) and (f7 == 0x01) and \
        not ((f3 <= 3) or (f3 == 6) or (f3 == 7))

MISA_VALUE = 0x4000_1200             # epv32_csr.v:96
IMPID_VALUE = 0x0000_0003            # epv32_core.v:23 parameter

# ---- M9: 与 rtl/epv32_csr.v 的 parameter / localparam 一一对应
PMP_N = 4                                  # epv32_csr.v parameter PMP_N
PMPCFG_WARL = 0x3F                    # L[0] R[1] W[2] X[3] A[5:4]
PMPADDR_WARL = 0x3FFF_FFFF            # G=0: bit31/30 无对应物理地址位
MCNTINHIBIT_WARL = 0x5                # 只有 CY(bit0) 与 IR(bit2)
CSR_COUNTER = (0xB00, 0xB80, 0xB02, 0xB82)
CSR_VCFG = 0x7C0                     # epv32_defs.vh:91
EPV_VRF_WORDS = 8                    # 8 x 128-bit vector registers

# ------------------------------------------- EPV funct3 / funct7 (defs.vh)
EPV_F3_LVB, EPV_F3_SVB, EPV_F3_MVX = 0, 1, 2
EPV_F3_XMV, EPV_F3_CFG, EPV_F3_CFGR = 3, 4, 5
EPV_F7_VDOT8, EPV_F7_VMAC4, EPV_F7_VADD8, EPV_F7_VREQ4 = 1, 2, 3, 4
EPV_F7_VMAX8, EPV_F7_VRELU8, EPV_F7_VSRA4, EPV_F7_VZERO = 5, 6, 7, 8
EPV_F7_VPOOL2, EPV_F7_VSUB8, EPV_F7_VMIN8 = 9, 10, 11
EPV_C0_F3_OK = frozenset(range(6))          # EPV_N_C0 = 6
EPV_C1_F7_OK = frozenset(range(1, 12))      # EPV_N_C1 = 11

# ---- epv32_epv.v:96-98 的三条"这个字段是向量号"掩码，逐指令类不同 (quirk 10)
#      不在掩码里的指令，那个字段就是真正的 GPR 编号 / 未被读的字段，允许 0..31。
EPV_RD_VEC_C0 = frozenset((EPV_F3_MVX,))                       # 除 XMV/CFGR/CFG/LVB/SVB
EPV_RS2_VEC_C0 = frozenset((EPV_F3_LVB, EPV_F3_SVB))           # MVX/XMV/CFG/CFGR 不查 rs2
EPV_RD_VEC_C1 = frozenset((EPV_F7_VMAC4, EPV_F7_VADD8, EPV_F7_VSUB8, EPV_F7_VMAX8,
                           EPV_F7_VMIN8, EPV_F7_VREQ4, EPV_F7_VRELU8, EPV_F7_VSRA4,
                           EPV_F7_VZERO, EPV_F7_VPOOL2))       # VDOT8 的 [11:7] 是 GPR
EPV_RS2_VEC_C1 = frozenset((EPV_F7_VMAC4, EPV_F7_VADD8, EPV_F7_VSUB8, EPV_F7_VMAX8,
                            EPV_F7_VMIN8, EPV_F7_VREQ4))       # 单源类不查 vs2
EPV_RS1_VEC_C1 = frozenset((EPV_F7_VMAC4, EPV_F7_VADD8, EPV_F7_VSUB8, EPV_F7_VMAX8,
                            EPV_F7_VMIN8, EPV_F7_VREQ4, EPV_F7_VRELU8, EPV_F7_VSRA4,
                            EPV_F7_VPOOL2))                    # VDOT8/VZERO 不查 vs1

MASK32 = 0xFFFF_FFFF
MASK64 = 0xFFFF_FFFF_FFFF_FFFF
MASK128 = (1 << 128) - 1


# ------------------------------------------------------------------- helpers
def u32(v):
    return v & MASK32


def s32(v):
    v &= MASK32
    return v - (1 << 32) if v >> 31 else v


def u64(v):
    return v & MASK64


def s64(v):
    v &= MASK64
    return v - (1 << 64) if v >> 63 else v


def s8(v):
    v &= 0xFF
    return v - 256 if v >> 7 else v


def sext(v, bits):
    m = (1 << bits) - 1
    v &= m
    return v - (1 << bits) if (v >> (bits - 1)) & 1 else v


def v_lane_i8(vec, i):
    """int8 lane i of a 128-bit vector: bits [8i+7:8i], signed."""
    return s8(vec >> (8 * i))


def v_lane_w(vec, j):
    """int32 lane j of a 128-bit vector: bits [32j+31:32j], signed."""
    return s32(vec >> (32 * j))


def v_put_byte(vec, i, b):
    return (vec & ~(0xFF << (8 * i))) | ((b & 0xFF) << (8 * i))


def v_put_word(vec, j, w):
    return (vec & ~(MASK32 << (32 * j))) | ((w & MASK32) << (32 * j))


def sat8_9(v, sat):
    """epv32_epv.v:109 sat8_9 -- 9-bit signed add/sub result."""
    if sat and v > 127:
        return 0x7F
    if sat and v < -128:
        return 0x80
    return v & 0xFF


def sat8_64(v):
    """epv32_epv.v:119 sat8_64 -- always saturates."""
    if v > 127:
        return 0x7F
    if v < -128:
        return 0x80
    return v & 0xFF


def trunc_div(a, b):
    """C-style truncating division (RISC-V DIV); Python's // floors."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


# ------------------------------------------------------------------- results
class SimHalt(Exception):
    """Raised when software writes the SYSC COMMIT register (epv32_soc.v:180).

    value = the data written; step = StepResult of the storing instruction, so
    the harness can still fold that final commit into its trace.
    """

    def __init__(self, value, step=None):
        super().__init__("SYSC COMMIT value=0x%08x" % (value & MASK32))
        self.value = value & MASK32
        self.step = step


class Trap(object):
    """Exception / interrupt descriptor (mirrors mcause / mtval / mepc)."""

    __slots__ = ("cause", "is_irq", "tval", "pc", "target", "name")

    def __init__(self, cause, is_irq, tval, pc, target):
        self.cause = cause & 0x1F
        self.is_irq = bool(is_irq)
        self.tval = tval & MASK32
        self.pc = pc & MASK32
        self.target = target & MASK32
        self.name = (IRQ_NAMES if self.is_irq else EXC_NAMES).get(
            self.cause, "cause-%d" % self.cause)

    @property
    def mcause(self):
        return ((1 << 31) if self.is_irq else 0) | self.cause

    def __repr__(self):
        return ("Trap(%s cause=%d is_irq=%s tval=0x%08x pc=0x%08x -> 0x%08x)"
                % (self.name, self.cause, self.is_irq, self.tval, self.pc,
                   self.target))


class StepResult(object):
    """One retired (or trapping) instruction.  Feeds the harness commit trace.

    rd / wdata follow epv32_core.v:542 rf_we, i.e. a write to x0 is NOT a
    commit (both stay None), matching o_tr_regwr.  mem_addr / mem_wdata /
    mem_be describe the single committed store of the common case; `stores`
    lists every committed store as (addr, wdata, be) -- EPV.SVB contributes
    four, an ordinary SW/SH/SB at most one.
    """

    __slots__ = ("pc", "instr", "rd", "wdata", "mem_addr", "mem_wdata",
                 "mem_be", "stores", "loads", "trap", "next_pc", "retired",
                 "vregs_written", "vcfg", "vcfg_written", "uart_bytes",
                 "halt", "kind", "csr_addr", "csr_old", "cycles", "epv")

    def __init__(self):
        self.pc = 0
        self.instr = 0
        self.rd = None
        self.wdata = None
        self.mem_addr = None
        self.mem_wdata = None
        self.mem_be = None
        self.stores = []
        self.loads = []
        self.trap = None
        self.next_pc = 0
        self.retired = False
        self.vregs_written = []
        self.vcfg = 0
        self.vcfg_written = False
        self.uart_bytes = b""
        self.halt = None
        self.kind = ""
        self.csr_addr = None
        self.csr_old = None
        self.cycles = 0
        self.epv = False

    def __repr__(self):
        if self.trap:
            return "Step(pc=0x%08x instr=0x%08x TRAP %s)" % (
                self.pc, self.instr, self.trap)
        out = ["Step(pc=0x%08x instr=0x%08x" % (self.pc, self.instr)]
        if self.rd is not None:
            out.append("x%d=0x%08x" % (self.rd, self.wdata))
        for a, d, b in self.stores:
            out.append("mem[0x%08x]=0x%08x/be=%x" % (a, d, b))
        for v in self.vregs_written:
            out.append("v%d=<128b>" % v)
        if self.vcfg_written:
            out.append("vcfg=0x%08x" % self.vcfg)
        out.append("-> 0x%08x)" % self.next_pc)
        return " ".join(out)


class Result(object):
    """Outcome of run()."""

    __slots__ = ("passed", "testnum", "n_instr", "uart_text", "halt_reason",
                 "commit_value", "trap")

    def __init__(self, passed, testnum, n_instr, uart_text, halt_reason,
                 commit_value=None, trap=None):
        self.passed = bool(passed)
        self.testnum = testnum
        self.n_instr = n_instr
        self.uart_text = uart_text
        self.halt_reason = halt_reason
        self.commit_value = commit_value
        self.trap = trap

    def __repr__(self):
        return ("Result(passed=%s testnum=%s n_instr=%d halt=%s uart=%r)"
                % (self.passed, self.testnum, self.n_instr, self.halt_reason,
                   self.uart_text))


class _Dec(object):
    """Decoded control word -- field for field the ID stage of epv32_core.v."""

    __slots__ = ("opcode", "f3", "f7", "rd", "rs1", "rs2", "imm_i", "imm_s",
                 "imm_b", "imm_u", "imm_j", "sys_code", "illegal",
                 "reg_write", "mem_read", "mem_write", "is_md", "is_epv",
                 "is_csr", "is_jmp", "is_br", "wr_pc4", "jal_is",
                 "do_ebreak", "do_ecall", "do_mret", "do_wfi", "alu_op",
                 "a_pc", "a_immu", "b_imm", "md_op", "imm", "is_c0",
                 "epv_illegal")

    def __init__(self):
        self.illegal = False
        self.reg_write = self.mem_read = self.mem_write = False
        self.is_md = self.is_epv = self.is_csr = False
        self.is_jmp = self.is_br = self.wr_pc4 = self.jal_is = False
        self.do_ebreak = self.do_ecall = self.do_mret = self.do_wfi = False
        self.alu_op = ALU_ADD
        self.a_pc = self.a_immu = self.b_imm = False
        self.md_op = 0
        self.imm = 0
        self.is_c0 = False
        self.epv_illegal = False


# ========================================================== the machine model
class ISS(object):
    """Core + SoC machine model.  See the module header for the ground rules."""

    def __init__(self, ram_words=65536, enable_epv=True):
        self.ram_words = ram_words
        self.enable_epv = enable_epv
        self.regs = [0] * 32                  # x0..x31
        self.vregs = [0] * EPV_VRF_WORDS      # 8 x 128-bit, one int each
        self.vcfg = 0
        self.ram = {}                         # word index -> 32-bit (sparse)
        self.uart_tx = bytearray()
        self.uart_div = 434
        self.mtimecmp = MASK64
        self.mtime_bias = 0                   # mtime = cycle + bias
        self.cycle = 0
        self.n_instr = 0                      # minstret (ex_commit count)
        self.mcountinhibit = 0
        self.pmpcfg = [0] * PMP_N
        self.pmpaddr = [0] * PMP_N
        self.pc = ADDR_RAM_BASE
        self.mstatus = self.mie = self.mtvec = 0
        self.mscratch = self.mepc = self.mcause = self.mtval = 0
        self.irq_soft = False                 # tied 0 in epv32_soc.v:247
        self.irq_ext = False                  # tied 0 in epv32_soc.v:247
        self._exact_cycles = True             # CYCLE MODEL v2; hooks switch it off
        self.cycles_per_step = 1
        self._cycle_model = None
        self._init_clock_state()
        self.steps = 0
        self._commit_req = None
        self.reset(ADDR_RAM_BASE)

    def _init_clock_state(self):
        """Clock-side state that survives load_image but not reset()."""
        # pipeline placement (CYCLE MODEL v2): last EX cycle of the previous
        # instruction; None until the first step after reset.
        self._last_ex = None
        self._prev = None
        # UART send shift register, one entry per RTL reg (epv32_uart.v:27-33).
        # _u_clk = cycle whose state is materialised; a DATA write performed at
        # bus cycle C is applied synchronously as the edge C -> C+1.
        self._u_run = False
        self._u_sh = 0x3FF                    # {idle, msb..lsb, start}, all idle
        self._u_cnt = 0
        self._u_bit = 0
        self._u_byte = 0
        self._u_start = False
        self._u_clk = 0
        # mcycle overlay: untouched -> mcycle IS the real clock (self.cycle).
        self._mcyc_touched = False
        self._mcyc_base = 0
        self._mcyc_land = 0                   # cycle from which _mcyc_base holds
        self._mcyc_frozen = False             # mcountinhibit.CY latched
        # per-instruction facts registered by _step_body for _record_prev
        self._cur_cross = False
        self._cur_memop = False
        self._cur_redirect = False
        self._cur_load_rd = None
        self._plan = None                     # placement of the current step
        self._cur_ex = 0                      # EX cycle of the current step

    # --------------------------------------------------------- lifecycle
    def reset(self, start_pc):
        """Reset core + SoC state.  RAM survives (load before or after)."""
        self.pc = start_pc & MASK32
        self.regs = [0] * 32
        self.vregs = [0] * EPV_VRF_WORDS
        self.vcfg = 0
        self.uart_tx = bytearray()
        self.uart_div = 434
        self.mtimecmp = MASK64
        self.mtime_bias = 0
        self.cycle = 0
        self.n_instr = 0
        self.steps = 0
        self.mcountinhibit = 0
        self.pmpcfg = [0] * PMP_N
        self.pmpaddr = [0] * PMP_N
        self.mstatus = self.mie = self.mtvec = 0
        self.mscratch = self.mepc = self.mcause = self.mtval = 0
        self.irq_soft = self.irq_ext = False
        self._commit_req = None
        self._init_clock_state()

    def set_cycle_model(self, fn):
        """Legacy escape hatch: fn(iss, step_index, pc, instr) -> cycles charged
        at the END of that step.  Setting a model (or a cycles_per_step != 1)
        switches the ISS back to flat per-step charging and DISABLES the exact
        pipeline placement of CYCLE MODEL v2 -- UART poll values and TIMER-IRQ
        timing stop being golden properties until you pass fn=None again."""
        self._cycle_model = fn
        self._exact_cycles = fn is None

    def _get_cps(self):
        return self._cps

    def _set_cps(self, v):
        self._cps = v
        self._exact_cycles = self._exact_cycles and (v == 1)

    cycles_per_step = property(_get_cps, _set_cps,
                               doc="flat per-step charge; != 1 switches the "
                                   "ISS to legacy cycle accounting")

    # ------------------------------------------------- exact clock helpers
    def _uart_edge(self, wr_byte=None):
        """One posedge of epv32_uart's send side, applied to the state of
        cycle self._u_clk; produces the state of cycle _u_clk+1.

        `wr_byte` mirrors `en && we && off==UART_OF_DATA` being active during
        the CURRENT cycle: the RTL captures tx_byte/tx_start at the edge that
        ENDS that cycle (epv32_uart.v:55-61).  The load condition
        `tx_start && tx_idle` is evaluated with the OLD tx_start, so a char
        written at cycle C starts shifting at C+2 and holds tx_run for exactly
        10*divisor cycles (C+2 .. C+2+10*div-1) -- verified against the
        busy-wait polls in the RTL retirement traces."""
        c = self._u_clk
        start, run = self._u_start, self._u_run
        cnt, bit, sh, tbyte = self._u_cnt, self._u_bit, self._u_sh, self._u_byte
        div = self.uart_div if self.uart_div else 1
        if wr_byte is not None:
            self._u_byte = wr_byte & 0xFF
            self._u_start = True
        else:
            self._u_start = False
        if start and not run:                 # load: {stop, data[7:0], start}
            self._u_sh = 0x200 | ((tbyte & 0xFF) << 1)
            self._u_run = True
            self._u_cnt = 0
            self._u_bit = 0
        elif run:
            if cnt == div - 1:
                self._u_sh = 0x200 | (sh >> 1)
                self._u_cnt = 0
                if bit == 9:
                    self._u_run = False
                else:
                    self._u_bit = bit + 1
            else:
                self._u_cnt = cnt + 1
        self._u_clk = c + 1

    def _uart_sync(self, target):
        """Advance the UART model to the state of cycle `target`."""
        t = max(0, int(target))
        while self._u_clk < t:
            self._uart_edge()

    @property
    def _tx_idle(self):
        """UART STATUS bit0 = !tx_run, at the currently materialised cycle."""
        return not self._u_run

    def _mcycle_now(self):
        """mcycle mirror at clock point self.cycle (CYCLE MODEL v2).

        Untouched -> the counter IS the real clock.  After a write or a
        mcountinhibit.CY latch it continues affinely from the recorded base
        (the write lands on the edge that ends the writing instruction's EX
        cycle, i.e. it is visible from land = ex+1)."""
        if not self._mcyc_touched:
            return u64(self.cycle)
        if self._mcyc_frozen:
            return u64(self._mcyc_base)
        return u64(self._mcyc_base + max(0, self.cycle - self._mcyc_land))

    def _mcycle_write(self, addr, data, ex_cycle):
        """Counter-half write landing at edge ex_cycle -> ex_cycle+1.

        Deliberate simplification (quirk 19): the RTL's OTHER half still
        increments on that edge (two NBAs); here the write replaces the whole
        recorded base, so that one increment is not modelled."""
        cur = int(self._mcycle_now())
        if addr == 0xB00:
            val = (cur & 0xFFFF_FFFF_0000_0000) | (data & MASK32)
        else:
            val = (cur & MASK32) | ((data & MASK32) << 32)
        self._mcyc_touched = True
        self._mcyc_frozen = False
        self._mcyc_base = u64(val)
        self._mcyc_land = ex_cycle + 1

    def _mcycle_inhibit(self, data, ex_cycle):
        """mcountinhibit write: CY(bit0) freezes mcycle from ex_cycle+1 on."""
        if data & 1 and not self._mcyc_frozen:
            # the increment on the writing edge still happens (old CY was 0)
            self._mcyc_touched = True
            self._mcyc_base = u64(int(self._mcycle_now()) + 1)
            self._mcyc_land = ex_cycle + 1
            self._mcyc_frozen = True
        elif not (data & 1) and self._mcyc_frozen:
            self._mcyc_land = ex_cycle + 1     # base stays; counting resumes
            self._mcyc_frozen = False
        self._mcyc_touched = self._mcyc_touched or bool(data & 1)

    # ------------------------------------------------- pipeline placement
    @staticmethod
    def _uses_rs1(d):
        """epv32_core.v:272-275 verbatim (raw opcode fields, legality aside)."""
        op = d.opcode
        return (op in (OP_OP, OP_OPIMM, OP_BRANCH, OP_LOAD, OP_STORE, OP_JALR)
                or (op == OP_SYSTEM and d.f3 != 0 and not (d.f3 & 4))
                or op in (OP_CUSTOM0, OP_CUSTOM1))

    @staticmethod
    def _uses_rs2(d):
        """epv32_core.v:276-278 verbatim."""
        op = d.opcode
        return (op in (OP_OP, OP_BRANCH, OP_STORE)
                or (op == OP_SYSTEM and d.f3 != 0 and not (d.f3 & 4))
                or op in (OP_CUSTOM0, OP_CUSTOM1))

    def _place_ex(self, d):
        """CYCLE MODEL v2: cycle where this instruction's EX stage actively
        evaluates, plus the placement facts its own bus accesses need.

        Returns (plan, last_ex): plan is a dict with
          active -- the EX cycle CSR reads sample (mcycle) and the IRQ check
                    samples (mtime >= mtimecmp);
          fe/g0  -- LVB/SVB only: first beat cycle and the grant waits ahead
                    of beat 0 (the previous instruction still sitting in MEM);
          last   -- the cycle whose closing edge commits this instruction
                    (EX/MEM capture), i.e. its LAST EX cycle.
        """
        if self._prev is None:
            # reset fill: IF during cycle 0, ID during 1, first EX during 2.
            L, redirect, load_rd, cross, memop = 1, False, None, False, False
        else:
            p = self._prev
            L = p["last"]
            redirect, load_rd = p["redirect"], p["load_rd"]
            cross, memop = p["cross"], p["memop"]
        # two flushed slots on redirect: the target is fetched during L+1,
        # in ID during L+2, in EX during L+3.  After a redirect there is no
        # load-use hazard (the flushed slots separate the load from its
        # consumer) and no crossing-MEM hold (a redirecting instruction is
        # never a load/store) -- but the target itself can still be a
        # multi-cycle DIV/LVB/SVB, so this only fixes fe and FALLS THROUGH
        # to the multi-cycle logic below.  (An earlier version returned
        # early here and charged every post-redirect instruction 1 cycle;
        # the EPV loops in sw/bench pay +3 cycles per iteration for exactly
        # that reason -- the replay probe caught it against the RTL wave.)
        fe = L + 3 if redirect else L + 1
        if not redirect and load_rd is not None and (
                (self._uses_rs1(d) and d.rs1 == load_rd)
                or (self._uses_rs2(d) and d.rs2 == load_rd)):
            fe += 1                       # load-use hazard: one ID stall
        is_div = d.is_md and d.md_op in (4, 5, 6, 7)
        is_move = (d.is_epv and d.is_c0
                   and d.f3 in (EPV_F3_LVB, EPV_F3_SVB))
        if is_div:
            # 1 start + 32 working beats in EX (epv32_muldiv.v: 完成拍=第33拍).
            # A crossing predecessor's MEM hold overlaps the divider's own
            # first busy beat, so it costs nothing extra here.
            last = fe + 32
            return {"active": last, "fe": fe, "g0": 0, "last": last}, last
        if is_move:
            # 4 granted beats; the previous MEM occupant blocks the beats that
            # would overlap it (ex_commit is 0 for the rest of the freeze, so
            # MEM fills with bubbles after that).
            g0 = 0
            if memop and not redirect:
                g0 = sum(1 for c in ([L + 1] + ([L + 2] if cross else []))
                         if c >= fe)
            last = fe + g0 + 3
            return {"active": last, "fe": fe, "g0": g0, "last": last}, last
        # single-cycle EX; a crossing predecessor's second MEM beat holds the
        # pipeline one extra cycle before this instruction's EX is live.
        active = fe + (1 if (cross and not redirect) else 0)
        return {"active": active, "fe": active, "g0": 0, "last": active}, active

    def _record_prev(self, plan):
        """Facts the NEXT step's placement needs (CYCLE MODEL v2).

        self._cur_cross / self._cur_redirect are registered by _step_body at
        the point the facts become known (cross when the access is classified,
        redirect on every control-transfer exit path)."""
        self._prev = {
            "last": plan["last"],
            "redirect": bool(self._cur_redirect),
            "load_rd": self._cur_load_rd,
            "cross": bool(self._cur_cross),
            "memop": bool(self._cur_memop),
        }

    def load_image(self, base_addr, data):
        """Little-endian blob into RAM, byte granular (any length / offset)."""
        a = base_addr & MASK32
        for i, b in enumerate(data):
            addr = (a + i) & MASK32
            w = (addr >> 2) & (self.ram_words - 1)
            sh = (addr & 3) * 8
            cur = self.ram.get(w, 0)
            self.ram[w] = u32((cur & ~(0xFF << sh)) | ((b & 0xFF) << sh))

    # -------------------------------------------------------- address decode
    @staticmethod
    def _in(a, lo, hi):
        return lo <= a <= hi

    def addr_ok(self, a):
        """Inside a decoded window.  The OLD core also demanded 4-byte
        alignment for every access (quirk 3); that is off by default now, kept
        reachable through ADDR_OK_OLDED_ALIGNMENT so a run can reproduce it."""
        a &= MASK32
        if ADDR_OK_OLDED_ALIGNMENT and (a & 3):
            return False
        return (self._in(a, ADDR_RAM_BASE, ADDR_RAM_LAST)
                or self._in(a, ADDR_UART_BASE, ADDR_UART_LAST)
                or self._in(a, ADDR_TIMER_BASE, ADDR_TIMER_LAST)
                or self._in(a, ADDR_SYSC_BASE, ADDR_SYSC_LAST))

    def _ram_peek(self, a):
        """Raw RAM read; mirrors epv32_ram.v's masked index addr[AW+1:2]."""
        return self.ram.get((a >> 2) & (self.ram_words - 1), 0)

    def _fetch(self, a):
        """Fetch.  Never faults here: epv32_core.v:416 raises the access fault
        from addr_ok(idex_pc) on top of whatever the port returned."""
        return self._ram_peek(a)

    def _read_word(self, a):
        """Bus read, address decoded.  Golden: combinational (see quirk 15).

        Device state is sampled at self.cycle (the caller sets it to the
        access's MEM cycle for loads/stores, or to the LVB/SVB beat cycle):
        the UART is advanced edge-by-edge to that point and STATUS returns the
        live tx_idle bit; mtime is the free-running clock at that point."""
        a &= MASK32
        off = a & 0x3F
        if self._in(a, ADDR_UART_BASE, ADDR_UART_LAST):
            if off == UART_OF_DATA:
                return 0                       # {23'b0, rx_valid, rx_data}
            if off == UART_OF_STATUS:
                self._uart_sync(self.cycle)
                return 1 if self._tx_idle else 0   # {30'b0, rx_valid, tx_idle}
            if off == UART_OF_DIV:
                return self.uart_div & MASK32
            return 0
        if self._in(a, ADDR_TIMER_BASE, ADDR_TIMER_LAST):
            m = self.mtime
            if off == TMR_OF_MTIME:
                return m & MASK32
            if off == TMR_OF_MTIMEHI:
                return (m >> 32) & MASK32
            if off == TMR_OF_CMP:
                return self.mtimecmp & MASK32
            if off == TMR_OF_CMPHI:
                return (self.mtimecmp >> 32) & MASK32
            return 0
        if self._in(a, ADDR_SYSC_BASE, ADDR_SYSC_LAST):
            return SYSC_SYSID if off == SYSC_OF_SYSID else 0
        return self._ram_peek(a)

    def _write_word(self, a, data, be):
        """Bus write with byte enables.  Returns the bytes this write TXed.

        The write is active during cycle self.cycle; the RTL captures it at
        the edge ENDING that cycle, so clocked side effects (UART tx_byte /
        tx_start, mtime re-base) are applied synchronously as the edge
        self.cycle -> self.cycle+1 and the clock point advances past it."""
        a &= MASK32
        data &= MASK32
        off = a & 0x3F
        if self._in(a, ADDR_UART_BASE, ADDR_UART_LAST):
            if off == UART_OF_DATA:
                b = data & 0xFF
                self._uart_sync(self.cycle)    # materialise the write cycle
                self._uart_edge(b)             # tx_byte/tx_start at this edge
                self.cycle += 1
                self.uart_tx.append(b)
                return bytes((b,))
            if off == UART_OF_DIV:
                d = data & 0xFFFF
                self._uart_sync(self.cycle)
                self._uart_edge()              # divisor lands on this edge
                self.cycle += 1
                self.uart_div = 1 if d == 0 else d
            return b""
        if self._in(a, ADDR_TIMER_BASE, ADDR_TIMER_LAST):
            # mtime ticks every cycle; the write lands on the edge that ends
            # this one, so the value visible from cycle+1 is (mtime+1) with
            # the written half replaced (the +1 is the 64-bit tick itself).
            m_now = u64(self.mtime + 1)
            if off in (TMR_OF_MTIME, TMR_OF_MTIMEHI):
                if off == TMR_OF_MTIME:
                    m = u64((m_now & 0xFFFF_FFFF_0000_0000) | data)
                else:
                    m = u64((m_now & MASK32) | ((data & MASK32) << 32))
                self.cycle += 1
                self.mtime_bias = u64(m - self.cycle)
            elif off == TMR_OF_CMP:
                self.mtimecmp = u64((self.mtimecmp & ~MASK32) | data)
            elif off == TMR_OF_CMPHI:
                self.mtimecmp = u64((self.mtimecmp & MASK32)
                                    | ((data & MASK32) << 32))
            return b""
        if self._in(a, ADDR_SYSC_BASE, ADDR_SYSC_LAST):
            # epv32_sysc.v:180 -- offset 0 raises commit, CTRL is not modelled
            if off == SYSC_OF_COMMIT:
                self._commit_req = data
            return b""
        w = (a >> 2) & (self.ram_words - 1)
        cur = self.ram.get(w, 0)
        new = cur
        for i in range(4):
            if (be >> i) & 1:
                sh = i * 8
                new = u32((new & ~(0xFF << sh)) | (((data >> sh) & 0xFF) << sh))
        self.ram[w] = new
        return b""

    # ---------------------------------------------------------- peripherals
    @property
    def mtime(self):
        return u64(self.cycle + self.mtime_bias)

    @property
    def timer_pending(self):
        return self.mtime >= self.mtimecmp

    @property
    def mip(self):
        """epv32_csr.v:60 -- wired OR of the interrupt lines, read-only."""
        v = 0
        if self.irq_soft:
            v |= 1 << 3
        if self.timer_pending:
            v |= 1 << 7
        if self.irq_ext:
            v |= 1 << 11
        return v

    # ------------------------------------------------------------------ CSRs
    def _csr_read(self, addr):
        addr &= 0xFFF
        if addr == 0x300:
            return self.mstatus & 0x1888                    # MIE/MPIE/MPP
        if addr == 0x301:
            return MISA_VALUE
        if addr == 0x304:
            return self.mie
        if addr == 0x305:
            return self.mtvec
        if addr == 0x340:
            return self.mscratch
        if addr == 0x341:
            return self.mepc
        if addr == 0x342:
            return self.mcause
        if addr == 0x343:
            return self.mtval
        if addr == 0x344:
            return self.mip
        if addr == CSR_VCFG:
            return self.vcfg if self.enable_epv else 0
        if addr in (0xB00, 0xC00):
            return self._mcycle_now() & MASK32
        if addr in (0xB80, 0xC80):
            return (self._mcycle_now() >> 32) & MASK32
        if addr in (0xB02, 0xC02):
            return self.n_instr & MASK32
        if addr in (0xB82, 0xC82):
            return (self.n_instr >> 32) & MASK32
        if addr == 0x320:
            return self.mcountinhibit & MCNTINHIBIT_WARL
        if addr == 0x3A0:
            return sum((self.pmpcfg[k] & PMPCFG_WARL) << (8 * k)
                       for k in range(PMP_N))
        if 0x3B0 <= addr <= 0x3B0 + PMP_N - 1:
            return self.pmpaddr[addr - 0x3B0] & PMPADDR_WARL
        if addr in (0xF11, 0xF12, 0xF14):
            return 0
        if addr == 0xF13:
            return IMPID_VALUE
        return 0                             # undefined: reads 0, no trap

    def _csr_write(self, addr, data):
        """epv32_csr.v:154-169 -- only these seven accept a write."""
        addr &= 0xFFF
        data &= MASK32
        if addr == 0x300:
            self.mstatus = u32((self.mstatus & ~0x1888) | (data & 0x88)
                               | 0x1800)                    # MPP <- 11 always
        elif addr == 0x304:
            self.mie = data
        elif addr == 0x305:
            self.mtvec = data                               # no WARL masking
        elif addr == 0x340:
            self.mscratch = data
        elif addr == 0x341:
            self.mepc = u32(data & 0xFFFF_FFFE)
        elif addr == 0x342:
            self.mcause = data
        elif addr == 0x343:
            self.mtval = data
        elif addr in CSR_COUNTER:
            # M9: 计数器可写。两半 composing 成同一个 64 位量 —— 自检里有
            # 一条专门把 0xFFFF_FFFF_FFFF_FFFF + 1 打到 0, 挡住「两半各自独立」
            # 这一类退回 (实测 rv32mi/instret_overflow 就是靠这条抓出来的)。
            if addr in (0xB00, 0xB80):
                # CYCLE MODEL v2: mcycle 的镜像有自己的 overlay (CYCLE MODEL
                # 块与 quirk 19) —— self.cycle 是墙钟, 不能被计数器写劫持。
                self._mcycle_write(addr, data, getattr(self, "_cur_ex", 0))
            elif addr == 0xB02:
                self.n_instr = (self.n_instr & 0xFFFF_FFFF_0000_0000) | data
            else:
                self.n_instr = (self.n_instr & 0xFFFF_FFFF) | (data << 32)
        elif addr == 0x320:
            self.mcountinhibit = data & MCNTINHIBIT_WARL
            self._mcycle_inhibit(data, getattr(self, "_cur_ex", 0))
        elif addr == 0x3A0:
            for k in range(PMP_N):
                if not (self.pmpcfg[k] & 1):      # L=1 => 复位前不可改
                    self.pmpcfg[k] = ((data >> (8 * k)) & 0xFF) & PMPCFG_WARL
        elif 0x3B0 <= addr <= 0x3B0 + PMP_N - 1:
            k = addr - 0x3B0
            if not (self.pmpcfg[k] & 1):          # 锁定的表项连 addr 一起保护
                self.pmpaddr[k] = data & PMPADDR_WARL
        # default: read-only or undefined -> silently ignored, no trap

    # ---------------------------------------------------------------- decode
    def _decode(self, instr):
        d = _Dec()
        op = d.opcode = instr & 0x7F
        f3 = d.f3 = (instr >> 12) & 7
        f7 = d.f7 = (instr >> 25) & 0x7F
        d.rd = (instr >> 7) & 0x1F
        d.rs1 = (instr >> 15) & 0x1F
        d.rs2 = (instr >> 20) & 0x1F
        d.imm_i = u32(sext(instr >> 20, 12))
        d.imm_s = u32(sext(((instr >> 25) << 5) | ((instr >> 7) & 0x1F), 12))
        b31 = (instr >> 31) & 1
        d.imm_b = u32(sext((b31 << 12) | (((instr >> 7) & 1) << 11)
                           | (((instr >> 25) & 0x3F) << 5)
                           | (((instr >> 8) & 0xF) << 1), 13))
        d.imm_u = u32(instr & 0xFFFF_F000)
        d.imm_j = u32(sext((b31 << 20) | (((instr >> 12) & 0xFF) << 12)
                           | (((instr >> 20) & 1) << 11)
                           | (((instr >> 21) & 0x3FF) << 1), 21))
        d.sys_code = (instr >> 20) & 0xFFFFF
        d.imm = d.imm_i

        op_known = op in (OP_LUI, OP_AUIPC, OP_JAL, OP_JALR, OP_LOAD,
                          OP_STORE, OP_OPIMM, OP_OP, OP_FENCE, OP_SYSTEM,
                          OP_BRANCH)
        op_custom = op in (OP_CUSTOM0, OP_CUSTOM1)

        # ---- 移位/乘除的非法编码判据：规范侧一份，RTL 侧一份。
        # M8 之后 epv32_core.v 的 imm_op_bad 与规范一致（SLLI 要 inst[31:25]==0；
        # SRLI/SRAI 要 ∈{0,0x20}），muldiv_bad 恒不触发（RV32M 八个 funct3 全合法），
        # 所以两份规则现在**逐位相同**。两个名字都留着：合流是"今天"的事实而不是
        # 永久属性 —— 自检里有一条断言专核 rtl_* == good_*，RTL 一旦回退，那条就红，
        # MIRROR_RTL_DECODE_BUGS 这个开关也重新有意义（历史上它镜像过「每条 SLLI 都
        # 非法」与「DIV/DIVU 被判非法」两版真缺陷，rv32ui/add 与 rv32um/div 都栽过）。
        # 三条规则都是模块级函数：解码处与自检处读同一份实现，不各写一遍。
        imm_op_bad = (imm_bad_rtl(op, f3, f7) if MIRROR_RTL_DECODE_BUGS
                      else imm_bad_spec(op, f3, f7))
        muldiv_bad = (md_bad_rtl(op, f3, f7) if MIRROR_RTL_DECODE_BUGS
                      else md_bad_spec(op, f3, f7))

        reg_op_bad = (op == OP_OP) and f7 not in (0x00, 0x20, 0x01)
        sys_bad = ((op == OP_SYSTEM) and f3 == 0
                   and d.sys_code not in (0x00000, 0x00001, 0x00302, 0x00105))
        csr_f3_bad = (op == OP_SYSTEM) and f3 not in (0, 1, 2, 3, 5, 6, 7)
        br_f3_bad = (op == OP_BRANCH) and f3 in (2, 3)
        ld_f3_bad = (op == OP_LOAD) and f3 in (3, 6)
        st_f3_bad = (op == OP_STORE) and f3 > 2
        # LUI / AUIPC 是 U 型、JAL 是 J 型：inst[14:12] 属于它们的立即数, 只有
        # JALR 的 [14:12] 是真 funct3。旧 RTL 对四者都校验, 于是
        # `lui x3,0x12345`、`jal x0,0x1000` 全被判非法; M8 之前修好。
        # 自检里 other_f3_legacy() 留着那个形状, 用来证明这条判据确实在管事。
        other_f3_bad = (op == OP_JALR and f3 != 0)
        illegal_id = ((not op_known and not op_custom) or imm_op_bad
                      or reg_op_bad or muldiv_bad or sys_bad or csr_f3_bad
                      or br_f3_bad or ld_f3_bad or st_f3_bad or other_f3_bad)

        if illegal_id or (not self.enable_epv and op_custom):
            d.illegal = True
            d.alu_op = ALU_PASS
            return d

        if op == OP_LUI:
            d.reg_write, d.a_immu = True, True
            d.imm, d.alu_op = d.imm_u, ALU_PASS
        elif op == OP_AUIPC:
            d.reg_write = True
            d.a_pc, d.b_imm, d.imm = True, True, d.imm_u
        elif op == OP_JAL:
            d.reg_write = d.is_jmp = d.jal_is = d.wr_pc4 = True
            d.a_pc = d.b_imm = True
            d.imm = d.imm_j
        elif op == OP_JALR:
            d.reg_write = d.is_jmp = d.wr_pc4 = d.b_imm = True
            d.imm = d.imm_i
        elif op == OP_BRANCH:
            d.is_br = d.b_imm = True
            d.imm = d.imm_b
            d.alu_op = (ALU_SLTU if f3 & 2 else ALU_SLT) if f3 & 4 else ALU_SUB
        elif op == OP_LOAD:
            d.reg_write = d.mem_read = d.b_imm = True
            d.imm = d.imm_i
        elif op == OP_STORE:
            d.mem_write = d.b_imm = True
            d.imm = d.imm_s
        elif op == OP_OPIMM:
            d.reg_write = d.b_imm = True
            d.imm = d.imm_i
            d.alu_op = {0: ALU_ADD, 1: ALU_SLL, 2: ALU_SLT, 3: ALU_SLTU,
                        4: ALU_XOR,
                        5: (ALU_SRA if (f7 >> 5) & 1 else ALU_SRL),
                        6: ALU_OR, 7: ALU_AND}.get(f3, ALU_ADD)
        elif op == OP_OP:
            d.reg_write = True
            if f7 == 0x01:
                d.is_md = True
                d.md_op = f3 & 7                     # {f3[2],f3[1:0]} == f3
            else:
                d.alu_op = {0: (ALU_SUB if (f7 >> 5) & 1 else ALU_ADD),
                            1: ALU_SLL, 2: ALU_SLT, 3: ALU_SLTU, 4: ALU_XOR,
                            5: (ALU_SRA if (f7 >> 5) & 1 else ALU_SRL),
                            6: ALU_OR, 7: ALU_AND}.get(f3, ALU_ADD)
        elif op == OP_FENCE:
            d.alu_op = ALU_PASS
        elif op == OP_SYSTEM:
            if f3 == 0:
                d.imm = d.imm_i
                if d.sys_code == 0x00000:
                    d.do_ecall = True
                elif d.sys_code == 0x00001:
                    d.do_ebreak = True
                elif d.sys_code == 0x00302:
                    d.do_mret = True
                else:
                    d.do_wfi = True
            else:
                d.is_csr = d.reg_write = True
                d.alu_op = ALU_PASS
                d.imm = ((d.rs1 & 0x1F) if (f3 & 4) else d.imm_i) & MASK32
        elif op_custom:
            d.is_epv = d.b_imm = True
            d.imm = d.imm_i
            d.is_c0 = (op == OP_CUSTOM0)
            d.epv_illegal = self._epv_illegal(d)
            if op == OP_CUSTOM1:
                d.reg_write = (f7 == EPV_F7_VDOT8)
            else:
                d.reg_write = (f3 in (EPV_F3_XMV, EPV_F3_CFGR))
        else:
            d.alu_op = ALU_PASS
        return d

    @staticmethod
    def _epv_illegal(d):
        """epv32_epv.v:69-97 decode of the CUSTOM0 / CUSTOM1 space.

        field_bad 是**逐指令类**的三条掩码（rd_is_vec / rs2_is_vec / rs1_is_vec），
        不是无条件查 rd/rs2/rs1：VDOT8、XMV、CFGR 的 [11:7] 是真 GPR，允许 0..31，
        单源类 (VRELU8/VSRA4/VPOOL2) 不查 vs2，VDOT8/VZERO 不查 vs1，
        CUSTOM0 全部不查 rs1。旧版本在这里无条件查，于是 `EPV.CFGR x11` 与
        `EPV.VDOT8 x11,...` 被误判成 illegal-instruction —— 而编译器把目的寄存器
        分到 x8 以上太常见了，整程序轨迹比对一接上就撞死在那条指令上。
        """
        if d.is_c0:
            if d.f3 not in EPV_C0_F3_OK:
                return True
            if d.f3 in EPV_RD_VEC_C0 and (d.rd >> 3) != 0:
                return True
            return d.f3 in EPV_RS2_VEC_C0 and (d.rs2 >> 3) != 0
        if (d.f3 != 0 or d.f7 not in EPV_C1_F7_OK
                or ((d.f7 in EPV_RD_VEC_C1) and (d.rd >> 3) != 0)
                or ((d.f7 in EPV_RS2_VEC_C1) and (d.rs2 >> 3) != 0)):
            return True
        return d.f7 in EPV_RS1_VEC_C1 and (d.rs1 >> 3) != 0

    # ------------------------------------------------------------------- ALU
    @staticmethod
    def alu(op, a, b):
        a, b = u32(a), u32(b)
        if op == ALU_ADD:
            return u32(a + b)
        if op == ALU_SUB:
            return u32(a - b)
        if op == ALU_SLL:
            return u32(a << (b & 0x1F))
        if op == ALU_SLT:
            return 1 if s32(a) < s32(b) else 0
        if op == ALU_SLTU:
            return 1 if a < b else 0
        if op == ALU_XOR:
            return a ^ b
        if op == ALU_SRL:
            return a >> (b & 0x1F)
        if op == ALU_SRA:
            return u32(s32(a) >> (b & 0x1F))
        if op == ALU_OR:
            return a | b
        if op == ALU_AND:
            return a & b
        if op == ALU_PASS:
            return a
        return 0

    # ------------------------------------------------ muldiv (epv32_muldiv.v)
    @staticmethod
    def muldiv(op, a, b):
        a, b = u32(a), u32(b)
        sa, sb = s32(a), s32(b)
        if op == 0:                                          # MUL
            return u32(sa * sb)
        if op == 1:                                          # MULH
            return u32((sa * sb) >> 32)
        if op == 2:                                          # MULHSU (spec f3=2)
            return u32((sa * b) >> 32)
        if op == 3:                                          # MULHU  (spec f3=3)
            return u32((a * b) >> 32)
        is_rem = op in (6, 7)
        is_u = op in (5, 7)
        if b == 0:                                           # divide by zero
            return a if is_rem else MASK32
        if (not is_u) and a == 0x8000_0000 and b == MASK32:
            return 0 if is_rem else 0x8000_0000              # INT_MIN / -1
        if is_u:
            q, r = a // b, a % b
        else:
            q = trunc_div(sa, sb)
            r = sa - q * sb
        return u32(r if is_rem else q)

    # ------------------------------------------------------- EPV vector ops
    @staticmethod
    def _vcfg_fields(vcfg):
        """epv32_epv.v:102-106: S saturates at 31; RB is the raw 8-bit field."""
        s_raw = vcfg & 0xFF
        s = 31 if s_raw > 31 else (vcfg & 0x1F)
        return s, (vcfg >> 16) & 1, (vcfg >> 17) & 1, (vcfg >> 8) & 0xFF

    def _epv_op(self, d):
        """Compute one CUSTOM0 / CUSTOM1 result.

        Returns (vec_wr, vdst, vec_value, x_wr, x_result).  LVB/SVB/CFG keep
        their side effects in _step_body because they touch the bus / vcfg.
        """
        s, rnd, sat, rb = self._vcfg_fields(self.vcfg)
        vd, vs1, vs2 = d.rd & 7, d.rs1 & 7, d.rs2 & 7
        s1, s2, sdst = self.vregs[vs1], self.vregs[vs2], self.vregs[vd]
        wv, val, xwr, xres = False, 0, False, 0

        if d.is_c0:
            if d.f3 == EPV_F3_MVX:
                g = u32(self._x(d.rs1))
                val = (g | (g << 32) | (g << 64) | (g << 96)) & MASK128
                wv = True
            elif d.f3 == EPV_F3_XMV:
                # quirk 11: 向量号在 rs2 字段 (epv32_hw.h 把 vs2 放 [24:20]、rs1 放 x0).
                xwr, xres = True, u32(s2)      # s2 = vrf[rs2f[2:0]]
            elif d.f3 == EPV_F3_CFGR:
                xwr, xres = True, self.vcfg
            return wv, vd, val, xwr, xres

        if d.f7 == EPV_F7_VDOT8:
            acc = 0
            for i in range(16):
                acc += v_lane_i8(s1, i) * v_lane_i8(s2, i)
            xwr, xres = True, u32(acc)
        elif d.f7 == EPV_F7_VMAC4:
            val = sdst
            for j in range(4):
                acc = v_lane_w(sdst, j)
                for m in range(4):
                    k = 4 * j + m
                    acc += v_lane_i8(s1, k) * v_lane_i8(s2, k)
                val = v_put_word(val, j, u32(acc))
            wv = True
        elif d.f7 in (EPV_F7_VADD8, EPV_F7_VSUB8):
            add = d.f7 == EPV_F7_VADD8
            val = 0
            for i in range(16):
                x, y = v_lane_i8(s1, i), v_lane_i8(s2, i)
                val = v_put_byte(val, i, sat8_9(x + y if add else x - y, sat))
            wv = True
        elif d.f7 == EPV_F7_VREQ4:
            val = 0
            bias = (1 << (s - 1)) if (rnd and s != 0) else 0
            rb_signed = (rb ^ 0x80) - 0x80      # quirk 12: int8 zero point
            for g in range(4):
                av, mv = v_lane_w(s1, g), v_lane_w(s2, g)
                prd = s64(av * mv)
                hi = s64(prd + (1 << 30)) >> 31
                sh = s64(hi + bias) >> s
                val = v_put_byte(val, g, sat8_64(s64(sh + rb_signed)))
            wv = True
        elif d.f7 in (EPV_F7_VMAX8, EPV_F7_VMIN8):
            want_max = d.f7 == EPV_F7_VMAX8
            val = 0
            for i in range(16):
                x, y = v_lane_i8(s1, i), v_lane_i8(s2, i)
                pick = x if ((x > y) if want_max else (x < y)) else y
                val = v_put_byte(val, i, pick & 0xFF)
            wv = True
        elif d.f7 == EPV_F7_VRELU8:
            val = 0
            for i in range(16):
                x = v_lane_i8(s1, i)
                val = v_put_byte(val, i, (0 if x < 0 else x) & 0xFF)
            wv = True
        elif d.f7 == EPV_F7_VSRA4:
            val = 0
            bias = (1 << (s - 1)) if (rnd and s != 0) else 0
            for g in range(4):
                val = v_put_word(val, g, u32((v_lane_w(s1, g) + bias) >> s))
            wv = True
        elif d.f7 == EPV_F7_VZERO:
            val, wv = 0, True
        elif d.f7 == EPV_F7_VPOOL2:
            val = 0
            for g in range(8):
                p, q = v_lane_i8(s1, 2 * g), v_lane_i8(s1, 2 * g + 1)
                val = v_put_byte(val, g, (p if p > q else q) & 0xFF)
            wv = True
        return wv, vd, val, xwr, xres

    def _epv_base(self, d):
        """EPV.LVB/SVB transfer base.  Hardware (R-type): exactly rs1 -- the
        imm field is dead (EPV_MEM_IMM_UNUSED; see quirk 17).  Setting that
        flag False revives the I-type-era offset for reproduction."""
        if EPV_MEM_IMM_UNUSED:
            return u32(self._x(d.rs1))
        raw = d.imm & 0xFFF
        off = raw if EPV_IMM_ZERO_EXTENDS else sext(raw, 12)
        return u32(self._x(d.rs1) + off)

    @staticmethod
    def _epv_stride(k):
        return (4 * k) if EPV_MEM_ADDR_STRIDE_X4 else k

    # ---------------------------------------------------------- register file
    def _x(self, i):
        return self.regs[i & 31] if (i & 31) else 0

    def _set_x(self, i, v):
        if (i & 31):
            self.regs[i & 31] = u32(v)

    # ------------------------------------------------------------------- trap
    def _enter_trap(self, sr, cause, is_irq, tval):
        """epv32_csr.v:143-149 + epv32_core.v:480.  The instruction dies."""
        mie = (self.mstatus >> 3) & 1
        self.mstatus = u32((self.mstatus & ~0x1888) | (mie << 7) | 0x1800)
        self.mepc = u32(sr.pc & 0xFFFF_FFFE)
        self.mcause = u32(((1 << 31) if is_irq else 0) | (cause & 0x1F))
        self.mtval = u32(tval)
        if is_irq:
            base = u32(self.mtvec & 0xFFFF_FFFC)
            if MIRROR_RTL_MTVEC_MODE_BIT:      # RTL: vmode_vec = mtvec[1]
                vectored = bool((self.mtvec >> 1) & 1)
            else:                              # spec: mode == 1 is vectored
                vectored = (self.mtvec & 3) == 1
            target = u32(base + 4 * (cause & 0x1F)) if vectored else base
        else:
            # quirk 4: 异常入口用 csr_mtvec_base, 低两位 MODE 被清零
            # (trap_target = irq_now ? csr_irq_target : csr_mtvec_base).
            target = u32(self.mtvec & 0xFFFF_FFFC)
        self.pc = target
        sr.trap = Trap(cause, is_irq, tval, sr.pc, target)
        sr.next_pc = target
        return sr

    def _irq_pending(self):
        """epv32_csr.v:64-78 and epv32_core.v:459: ext(11) > timer(7) > soft(3)."""
        if not ((self.mstatus >> 3) & 1):
            return None
        pend = (self.mie & self.mip) & MASK32
        if pend == 0:
            return None
        for bit in (11, 7, 3):
            if (pend >> bit) & 1:
                return bit
        return None

    # ------------------------------------------------------------------- step
    def step(self):
        """Execute exactly one retired (or trapping) instruction."""
        pc = self.pc
        instr = self._fetch(pc)
        d = self._decode(instr)
        sr = StepResult()
        sr.pc, sr.instr = pc, instr
        idx = self.steps
        self.steps += 1
        plan = None
        if self._exact_cycles:
            # CYCLE MODEL v2: place this instruction on the RTL clock BEFORE
            # the body runs -- device reads and CSR reads sample the clock.
            plan, _last = self._place_ex(d)
            self._plan = plan
            self._cur_ex = plan["active"]
            self.cycle = plan["active"]
            self._cur_cross = self._cur_memop = False
            self._cur_redirect = False
            self._cur_load_rd = d.rd if d.mem_read else None
        try:
            self._step_body(pc, instr, d, sr)
        finally:
            if plan is not None:
                self._record_prev(plan)
                base = self._last_ex if self._last_ex is not None else 1
                sr.cycles = plan["last"] - base
                self._last_ex = plan["last"]
            else:
                cyc = self._cycles_for(idx, pc, instr)
                self.cycle += cyc
                sr.cycles = cyc
        return sr

    def _cycles_for(self, idx, pc, instr):
        if self._cycle_model is not None:
            return int(self._cycle_model(self, idx, pc, instr))
        return self.cycles_per_step

    # ------------------------------------------------------ one instruction
    def _step_body(self, pc, instr, d, sr):
        rs1v, rs2v = self._x(d.rs1), self._x(d.rs2)
        alu_a = pc if d.a_pc else (d.imm if d.a_immu else rs1v)
        # epv32_core.v:254 sets d_b_imm for BRANCH, so the hardware ALU computes
        # rs1 OP branch_offset and BEQ/BNE/BLT/BGE test THAT (see header
        # deviation 4).  Golden: a branch compares the two GPR operands.
        use_imm_b = d.b_imm and not (d.is_br and not MIRROR_RTL_BRANCH_OPERAND_BUG)
        alu_b = d.imm if use_imm_b else rs2v
        alu_y = self.alu(d.alu_op, alu_a, alu_b)
        mem_addr = u32(alu_y)
        br_target = u32(pc + s32(d.imm))
        jr_target = u32((pc + s32(d.imm) if d.jal_is else u32(rs1v + d.imm))
                        & 0xFFFF_FFFE)
        f3 = d.f3
        c_ld, c_st = d.mem_read, d.mem_write
        # epv32_core.v 的 M6: 对齐档按访问宽度分三档, 且「硬件拆分」档不陷入。
        # 分组照抄 RTL, 按 funct3 而不是按位序: 1 字节 LB=0/LBU=4/SB=0 无要求,
        # 2 字节 LH=1/LHU=5/SH=1 看 addr[0], 4 字节 LW=2/SW=2 看 addr[1:0]。
        # (SW 与 LW 共用 funct3=2, 由读/写位区分 —— RV32 里 f3=6/7 的访存是 RV64
        #  的 LWU/SD, 本核判非法; 早先这里写成 f3 in (2,6), 与 RTL 的教训同源。)
        word_acc = (f3 == 2)
        half_acc = (f3 in (1, 5))
        misaligned = (c_ld or c_st) and (
            (half_acc and (mem_addr & 1))
            or (word_acc and (mem_addr & 3) != 0))
        ma_trap = misaligned and not SUPPORT_MISALIGNED
        x_wb = 4 if word_acc else (2 if half_acc else 1)
        x_cross = bool((c_ld or c_st) and SUPPORT_MISALIGNED
                       and ((mem_addr & 3) + x_wb) > 4)
        # CYCLE MODEL v2: facts the NEXT step's placement needs.  Registered
        # here because the exception chains below return early.
        plan = getattr(self, "_plan", None)
        self._cur_cross = x_cross
        self._cur_memop = bool(c_ld or c_st)
        x_hi_addr = ((mem_addr & ~3) + 4) & MASK32
        bus_err = (c_ld or c_st) and (not self.addr_ok(mem_addr)
                                      or (x_cross and not self.addr_ok(x_hi_addr)))
        fetch_err = not self.addr_ok(pc)
        ill_exc = d.illegal or (d.is_epv and d.epv_illegal)
        # M7: 与 epv32_core.v 的 br_err/jr_err 同式 —— 分支只有真的跳才判目标,
        # 且目标除「在地址图上」还要过 IALIGN=32 (jalr 已清 bit0, 所以看 bit1)。
        br_take = d.is_br and self._br_taken(d, rs1v, rs2v, alu_y)
        br_err = d.is_br and br_take and (not self.addr_ok(br_target)
                                          or bool((br_target >> 1) & 1))
        jr_err = d.is_jmp and (not self.addr_ok(jr_target)
                               or bool((jr_target >> 1) & 1))

        # ---- exception chain, priority order of epv32_core.v:425-434
        exc = None
        if ill_exc:
            exc = (EXC_ILLEGAL, instr)
        elif fetch_err:
            exc = (EXC_IAF, pc)
        elif br_err:
            exc = (EXC_IAFAIL_ALIGN, br_target)
        elif jr_err:
            exc = (EXC_IAFAIL_ALIGN, jr_target)
        elif d.do_ebreak:
            exc = (EXC_BREAK, pc)
        elif d.do_ecall:
            exc = (EXC_ECM, 0)
        elif ma_trap:
            exc = (EXC_LAMF if c_ld else EXC_SAMF, mem_addr)
        elif bus_err:
            exc = (EXC_LAF if c_ld else EXC_SAF, mem_addr)
        if exc is not None:
            self._commit_req = None
            self._cur_redirect = True           # trap redirects like a branch
            return self._enter_trap(sr, exc[0], False, exc[1])

        # ---- interrupt: taken before the EX instruction commits
        irq = self._irq_pending()
        if irq is not None:
            self._commit_req = None
            self._cur_redirect = True
            return self._enter_trap(sr, irq, True, 0)

        # =================================================== commit (ex_commit)
        tx0 = len(self.uart_tx)
        self._commit_req = None
        # NOTE: the retire count is bumped AFTER the CSR read below, so a
        # csrr of minstret / instret sees "instructions retired before me",
        # which is what the hardware's WB-stage counter holds while the reader
        # is still in EX.  quirk 9: mret never retires.
        def _retire():
            if not d.do_mret:
                # M9: 写计数器那条指令自己不计入 minstret (规范: 被写入的值
                # 会被下一条指令原样读到), 但它照样退休、照样进轨迹。mcountinhibit
                # 的 IR 位停的是 minstret。
                if not (csr_do_wr and csr_addr in CSR_COUNTER):
                    if not (self.mcountinhibit & 0x4):
                        self.n_instr += 1
                sr.retired = True

        # ---- CSR: read the old value now, the write lands after the commit
        csr_old, csr_do_wr, csr_nxt, csr_addr = 0, False, 0, None
        if d.is_csr:
            csr_addr = (instr >> 20) & 0xFFF
            cop = f3 & 3                                  # 01 RW 10 RS 11 RC
            imm_form = bool(f3 & 4)
            wdata = ((d.rs1 & 0x1F) if imm_form else rs1v) & MASK32
            csr_old = self._csr_read(csr_addr)
            csr_do_wr = cop == 1 or (cop in (2, 3)
                                     and not (wdata == 0 and not imm_form))
            csr_nxt = ((csr_old & ~wdata) if cop == 3 else
                       (csr_old | wdata) if cop == 2 else wdata) & MASK32
            sr.csr_addr, sr.csr_old = csr_addr, csr_old
        _retire()

        # ---- EPV: vector operation + vcfg + the 4-beat LVB / SVB transfer
        epv_xwr, epv_xres = False, 0
        if d.is_epv:
            sr.epv = True
            sr.vcfg = self.vcfg
            if d.is_c0 and d.f3 in (EPV_F3_LVB, EPV_F3_SVB):
                base = self._epv_base(d)
                if d.f3 == EPV_F3_LVB:
                    v = 0
                    for k in range(4):
                        if plan is not None:
                            # CYCLE MODEL v2: beat k samples the bus at its
                            # granted cycle (4 beats + any grant waits).
                            self.cycle = plan["fe"] + plan["g0"] + k
                        a = u32(base + self._epv_stride(k))
                        w = self._read_word(a)
                        v = v_put_word(v, k, w)
                        sr.loads.append((a, w))
                    # quirk 17: the LVB destination vector is the rs2 field
                    self.vregs[d.rs2 & 7] = v
                    sr.vregs_written.append(d.rs2 & 7)
                else:
                    src = self.vregs[d.rs2 & 7]
                    for k in range(4):
                        if plan is not None:
                            self.cycle = plan["fe"] + plan["g0"] + k
                        a = u32(base + self._epv_stride(k))
                        w = u32(src >> (32 * k))
                        self._write_word(a, w, 0xF)
                    # quirk 17, trace shape: the four bus beats are real and
                    # are applied above, but SVB retires with o_tr_memwr=0
                    # (idex_mem_write=0 for CUSTOM0), so the retirement trace
                    # -- and this StepResult's store records -- carry NONE of
                    # them.  The S projection therefore sees scalar stores
                    # only; SVB's effects are covered transitively through
                    # the R projection (programs that read the bytes back).
            else:
                wv, vd, val, epv_xwr, epv_xres = self._epv_op(d)
                if wv:
                    self.vregs[vd] = val & MASK128
                    sr.vregs_written.append(vd)
                if d.is_c0 and d.f3 == EPV_F3_CFG:
                    self.vcfg = u32(rs1v) & 0x0003_FFFF   # epv32_epv.v:274
                    sr.vcfg_written = True
                    sr.vcfg = self.vcfg

        # ---- EX result mux (epv32_core.v:449)
        if d.is_md:
            ex_result = self.muldiv(d.md_op, rs1v, rs2v)
        elif d.is_epv:
            ex_result = u32(epv_xres) if epv_xwr else 0
        elif d.is_csr:
            ex_result = csr_old
        elif d.wr_pc4:
            ex_result = u32(pc + 4)
        elif d.a_immu:
            ex_result = u32(d.imm)
        else:
            ex_result = u32(alu_y)

        # ---- MEM stage
        # M6 镜像: 与被核对的核共用同一条 64bit 窗口式子。读侧一律「把对象挪到
        # 窗口最低字节再按 off=0 扩展」; 写侧按字节通道掩码分成低字/高字两次写。
        load_val = None
        off_b = mem_addr & 3
        sh = 8 * off_b
        word0 = mem_addr & ~3
        if c_ld:
            # 与 RTL 同一条式子: 先把访问对象右移 8*off 挪到窗口最低字节,
            # 再一律按 off=0 扩展。早先这里给 LH/LHU 写了一套「看 ext_off 的位
            # 选 16 位切片」的近似, 于是**字内**非对齐的 lh/lhu(addr%4==1)取到了
            # 低半字 —— rv32ui/ma_data 的第 0 小节(lh at +1)就是靠逐条轨迹比对
            # 把这条抓出来的: 核给 0x201(测试要的), 模型给 0x100。
            if plan is not None:
                self.cycle = plan["active"] + 1    # load samples bus in MEM
            if x_cross:
                lo = self._read_word(word0)
                if plan is not None:
                    self.cycle = plan["active"] + 2    # second beat, next cyc
                hi = self._read_word(word0 + 4)
                raw = (((hi << 32) | lo) >> sh) & MASK32
            else:
                raw = (self._read_word(word0) >> sh) & MASK32
            if f3 == 0:
                load_val = u32(sext(raw & 0xFF, 8))
            elif f3 == 1:
                load_val = u32(sext(raw & 0xFFFF, 16))
            elif f3 == 4:
                load_val = raw & 0xFF
            elif f3 == 5:
                load_val = raw & 0xFFFF
            else:
                load_val = raw                # LW, and the f3==7 hole (quirk 5)
            sr.loads.append((mem_addr, load_val))
        if c_st:
            width = x_wb
            v = (rs2v & 0xFF) if width == 1 else ((rs2v & 0xFFFF) if width == 2
                                                  else (rs2v & MASK32))
            val64 = (v << sh) & ((1 << 64) - 1)
            be64 = (((1 << (8 * width)) - 1) << sh) & ((1 << 64) - 1)
            # be64 的 1 站在「位」上, o_dmem_be 的 1 站在「字节通道」上: 逐字节归约
            lanes = [1 if (be64 >> (8 * i)) & 0xFF else 0 for i in range(8)]
            be_lo = lanes[0] | (lanes[1] << 1) | (lanes[2] << 2) | (lanes[3] << 3)
            be_hi = lanes[4] | (lanes[5] << 1) | (lanes[6] << 2) | (lanes[7] << 3)
            if plan is not None:
                self.cycle = plan["active"] + 1    # store drives bus in MEM
            self._write_word(word0, (val64 >> 0) & MASK32, be_lo)
            if be_hi:
                if plan is not None:
                    self.cycle = plan["active"] + 2    # crossing second beat
                self._write_word(word0 + 4, (val64 >> 32) & MASK32, be_hi)
            # 退休轨迹记「这条指令按语义写了哪几个字节通道」= 两半并集 + 原始地址,
            # 与 epv32_core.v 的 memwb_be 同一条口径(对齐时 be_hi 恒 0, 退化一致)。
            sr.stores.append((mem_addr, rs2v, be_lo | be_hi))
        if d.is_csr and csr_do_wr:
            self._csr_write(csr_addr, csr_nxt)

        # ---- WB (epv32_core.v:542: a write to x0 is not a commit)
        w = load_val if c_ld else ex_result
        if d.reg_write and d.rd != 0:
            self._set_x(d.rd, w)
            sr.rd, sr.wdata = d.rd, u32(w)
        if sr.stores:
            sr.mem_addr, sr.mem_wdata, sr.mem_be = sr.stores[0]
        sr.uart_bytes = bytes(self.uart_tx[tx0:])

        # ---- PC update (epv32_core.v:480)
        if d.do_mret:
            mpie = (self.mstatus >> 7) & 1
            self.mstatus = u32((self.mstatus & ~0x1888) | (mpie << 3) | 0x80
                               | 0x1800)
            self.pc = u32(self.mepc & 0xFFFF_FFFE)
            sr.kind = "mret"
            self._cur_redirect = True
        else:
            if d.is_jmp:
                self.pc = jr_target
                self._cur_redirect = True
            elif d.is_br and self._br_taken(d, rs1v, rs2v, alu_y):
                self.pc = br_target
                self._cur_redirect = True
            else:
                self.pc = u32(pc + 4)
            sr.kind = ("epv" if d.is_epv else "csr" if d.is_csr
                       else "mem" if (c_ld or c_st) else "int")
        sr.next_pc = self.pc

        if self._commit_req is not None:
            v, self._commit_req = self._commit_req, None
            sr.halt = v
            raise SimHalt(v, sr)
        return sr

    @staticmethod
    def _br_taken(d, a, b, y):
        """epv32_core.v:398-406: BEQ/BNE test ALU(SUB)==0, BLT/BGE test the
        SLT result bit 0, BLTU/BGEU compare the forwarded operands unsigned."""
        f3 = d.f3
        eq = (y == 0)
        lt = y & 1
        if f3 == 0:
            return eq
        if f3 == 1:
            return not eq
        if f3 == 4:
            return bool(lt)
        if f3 == 5:
            return not lt
        if f3 == 6:
            return u32(a) < u32(b)
        return u32(a) >= u32(b)

    @staticmethod
    def _be_of(f3, off):
        """epv32_core.v:64 be_of, verbatim (see quirk 14 about the dead arm)."""
        if f3 == 0:
            return 1 << off
        if f3 == 1:
            return 0b1100 if (off & 1) else 0b0011
        return 0b1111

    # -------------------------------------------------------------------- run
    def run(self, max_instr, tohost_addr, max_steps=None):
        """Run until SYSC commit, a non-zero tohost, or the budget runs out.

        Trapped and interrupted instructions never retire (quirk: the RTL's
        minstret only counts ex_commit), so bounding the loop by retired
        instructions alone would spin forever on a trap loop.  `max_steps`
        caps the raw number of step() calls; it defaults to 4*max_instr + 64
        and reports halt_reason="max_steps" when it bites.
        """
        if max_steps is None:
            max_steps = 4 * max_instr + 64
        reason = "max_instr"
        start_steps = self.steps
        passed, testnum, commit_value, last_trap = False, None, None, None
        while self.n_instr < max_instr and (self.steps - start_steps) < max_steps:
            try:
                sr = self.step()
            except SimHalt as h:
                commit_value = h.value
                reason = "commit"
                passed = (commit_value == 1)
                testnum = 0 if commit_value == 1 else (commit_value >> 1)
                break
            if sr.trap is not None:
                last_trap = sr.trap
            if tohost_addr:
                th = self._ram_peek(tohost_addr)
                if th != 0:
                    passed = (th == 1)
                    testnum = 0 if th == 1 else (th >> 1)
                    reason = "tohost"
                    break
        if (self.n_instr >= max_instr and reason == "max_instr"
                or (self.steps - start_steps) >= max_steps
                and reason == "max_instr" and self.n_instr < max_instr):
            reason = "max_steps" if self.n_instr < max_instr else "max_instr"
        return Result(passed, testnum, self.n_instr,
                      bytes(self.uart_tx).decode("utf-8", "replace"),
                      reason, commit_value, last_trap)


# ==================================================================== test kit
def _fmt(v):
    """Readable 16/8/2-base rendering, so a FAIL line says *why* it failed."""
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, int):
        if v < 0:
            return "0x%08x (%d) [negative]" % (v & MASK32, v)
        if v > 0xFFFFFFFF:
            return "0x%032x (%d)" % (v, v)
        return "0x%08x (%d)" % (v, v)
    if isinstance(v, (list, tuple)):
        inner = ", ".join(_fmt(x) for x in v)
        return "(%s)" % inner if isinstance(v, tuple) else "[%s]" % inner
    return repr(v)


class _Check(object):
    """Every failure prints expected / actual / a note; nothing is bare assert."""

    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.failures = []

    def ck(self, name, got, want, why=""):
        if got == want:
            self.passed += 1
            print("ok    %-64s %s" % (name, _fmt(got)))
            return True
        self.failed += 1
        print("FAIL  %s" % name)
        print("      expected: %s" % _fmt(want))
        print("      actual  : %s" % _fmt(got))
        if why:
            print("      note    : %s" % why)
        self.failures.append(name)
        return False


# ------------------------------------------------- hand assemblers (no tools)
def enc_r(f7, rs2, rs1, f3, rd, op):
    return (((f7 & 0x7F) << 25) | ((rs2 & 0x1F) << 20) | ((rs1 & 0x1F) << 15)
            | ((f3 & 7) << 12) | ((rd & 0x1F) << 7) | (op & 0x7F))


def enc_i(imm12, rs1, f3, rd, op):
    return (((imm12 & 0xFFF) << 20) | ((rs1 & 0x1F) << 15) | ((f3 & 7) << 12)
            | ((rd & 0x1F) << 7) | (op & 0x7F))


def enc_s(imm12, rs1, rs2, f3, op):
    return ((((imm12 >> 5) & 0x7F) << 25) | ((rs2 & 0x1F) << 20)
            | ((rs1 & 0x1F) << 15) | ((f3 & 7) << 12)
            | ((imm12 & 0x1F) << 7) | (op & 0x7F))


def enc_b(off, rs1, rs2, f3):
    """B-type: signed 13-bit, 2-byte-granular branch displacement."""
    o = (off >> 1) & 0xFFF
    return ((((o >> 11) & 1) << 31) | (((o >> 4) & 0x3F) << 25)
            | ((rs2 & 0x1F) << 20) | ((rs1 & 0x1F) << 15) | ((f3 & 7) << 12)
            | ((o & 0xF) << 8) | (((o >> 10) & 1) << 7) | OP_BRANCH)


def enc_u(imm20, rd, op):
    return (((imm20 & 0xFFFFF) << 12) | ((rd & 0x1F) << 7) | (op & 0x7F))


def enc_j(off, rd):
    """J-type, field by field: inst[31]=imm[20], inst[30:21]=imm[10:1],
    inst[20]=imm[11], inst[19:12]=imm[19:12]; imm[0] is implicitly 0."""
    imm = off & 0x1F_FFFF
    return ((((imm >> 20) & 1) << 31) | (((imm >> 1) & 0x3FF) << 21)
            | (((imm >> 11) & 1) << 20) | (((imm >> 12) & 0xFF) << 12)
            | ((rd & 0x1F) << 7) | OP_JAL)


def epv_c0(f3, vd, rs1, imm12, rs2=0):
    """CUSTOM0: [11:7]=vd/rd [19:15]=rs1 [24:20]=rs2/vs2 [31:20]=imm12."""
    return enc_i(imm12, rs1, f3, vd, OP_CUSTOM0) | ((rs2 & 0x1F) << 20)


def epv_c1(f7, vd, vs1, vs2, f3=0):
    """CUSTOM1 R-type: [31:25]=funct7 [24:20]=vs2 [19:15]=vs1 [14:12]=funct3."""
    return enc_r(f7, vs2, vs1, f3, vd, OP_CUSTOM1)


def bytes16(*vals):
    """Pack up to 16 int8 lanes into the 128-bit value; lane 0 = bits [7:0]."""
    v = 0
    for i, x in enumerate(vals):
        v |= (x & 0xFF) << (8 * i)
    return v


def words4(*vals):
    v = 0
    for i, x in enumerate(vals):
        v |= (x & MASK32) << (32 * i)
    return v


def lanes_w(vec):
    return [u32(vec >> (32 * j)) for j in range(4)]


def lanes_b(vec):
    return [(vec >> (8 * i)) & 0xFF for i in range(16)]


# ==================================================================== self test
def selftest():
    """Hand-assembled regression for the golden model.  No external toolchain.

    Every expectation below was worked out by hand from rtl/epv32_defs.vh and
    rtl/epv32_epv.v semantics -- none of them is read back out of the model.
    """
    if hasattr(sys.stdout, "reconfigure"):          # Windows console is GBK
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    C = _Check()
    RAM = ADDR_RAM_BASE

    def new(epv=True):
        i = ISS(ram_words=65536, enable_epv=epv)
        i.reset(RAM)
        return i

    def poke(i, addr, val):
        """Direct RAM write.  Setup only -- never a substitute for a store."""
        i.ram[widx(addr)] = u32(val)

    def widx(addr):
        """Word index, using epv32_ram.v's masked index addr[AW+1:2]."""
        return ((addr & MASK32) >> 2) & (65536 - 1)

    def prog(i, words, start=RAM):
        for k, w in enumerate(words):
            poke(i, start + 4 * k, w)

    def must_not_trap(i, n, tag):
        out = []
        for _ in range(n):
            sr = i.step()
            if sr.trap is not None:
                C.ck("%s: unexpected trap at pc=0x%08x instr=0x%08x"
                     % (tag, sr.pc, sr.instr),
                     "%s" % sr.trap, "no trap")
                return out, False
            out.append(sr)
        return out, True

    # ==================================================== 0. encoder sanity
    print("\n=== 0. hand encoders vs published RISC-V encodings ===")
    C.ck("add x1,x1,x2 == 0x002080B3", enc_r(0, 2, 1, 0, 1, OP_OP), 0x002080B3)
    C.ck("nop == 0x00000013", enc_i(0, 0, 0, 0, OP_OPIMM), 0x00000013)
    C.ck("ebreak == 0x00100073", enc_i(1, 0, 0, 0, OP_SYSTEM), 0x00100073)
    C.ck("ecall == 0x00000073", enc_i(0, 0, 0, 0, OP_SYSTEM), 0x00000073)
    C.ck("mret == 0x30200073", enc_i(0x302, 0, 0, 0, OP_SYSTEM), 0x30200073)
    C.ck("wfi == 0x10500073", enc_i(0x105, 0, 0, 0, OP_SYSTEM), 0x10500073)
    C.ck("jal x0,0 == 0x0000006F", enc_j(0, 0), 0x0000006F)
    C.ck("bne x0,x0,-4 == 0xFE001EE3", enc_b(-4, 0, 0, 1), 0xFE001EE3)
    i = new()
    prog(i, [enc_b(0, 0, 0, 0), enc_i(0, 0, 0, 1, OP_OPIMM)])   # beq self, nop
    i.step()
    C.ck("beq x0,x0,0 really decodes to offset 0 (spin in place)", i.pc, RAM)
    i = new()
    prog(i, [enc_b(-8, 1, 2, 1)], start=RAM + 0x40)           # bne x1,x2,-8
    i.regs[1], i.regs[2] = 1, 2
    i.pc = RAM + 0x40
    i.step()
    C.ck("BNE taken with offset -8 really goes to pc-8", i.pc, RAM + 0x38)
    i = new()
    prog(i, [enc_j(-8, 0)])
    i.mtvec = RAM + 0x40
    sr = i.step()
    C.ck("jal x0,-8 from RAM leaves the map -> cause 0",
         (sr.trap.cause if sr.trap else None, i.mtval, i.pc),
         (EXC_IAFAIL_ALIGN, 0x7FFF_FFF8, RAM + 0x40))
    i = new()
    prog(i, [enc_j(0x1000, 1)])                                # jal x1, 4096
    i.mtvec = RAM + 0x40
    sr = i.step()
    C.ck("JAL with inst[14:12] != 0 is legal (RTL: other_f3_bad bug)",
         (sr.trap, sr.wdata, i.pc), (None, RAM + 4, RAM + 0x1000),
         "inst[14:12] is imm[14:12] for the J-type format, not a funct3")

    # ============================================================ 1. RV32I ALU
    print("\n=== 1. RV32I base ALU ===")
    i = new()
    prog(i, [enc_r(0, 2, 1, 0, 3, OP_OP)])                    # add x3,x1,x2
    i.regs[1], i.regs[2] = 0xFFFF_FFF0, 0x0000_0020
    sr = i.step()
    C.ck("ADD wraps mod 2^32: 0xFFFFFFF0 + 0x20", sr.wdata, 0x0000_0010,
         "expect 0x10 (=-16+32); got %s" % _fmt(sr.wdata))
    C.ck("ADD commits rd=3, retires 1, pc -> pc+4",
         (sr.rd, sr.kind, i.n_instr, i.pc), (3, "int", 1, RAM + 4))
    i = new()
    prog(i, [enc_r(0, 2, 1, 0, 0, OP_OP)])                    # add x0,x1,x2
    i.regs[1], i.regs[2] = 1, 2
    sr = i.step()
    C.ck("a write to x0 is not a commit (rd/wdata None)",
         (sr.rd, sr.wdata, sr.retired), (None, None, True),
         "mirrors rf_we = ... && memwb_rd != 0 (epv32_core.v:542)")
    i = new()
    prog(i, [enc_r(0, 2, 1, 2, 4, OP_OP)])                    # slt x4,x1,x2
    i.regs[1], i.regs[2] = u32(-5), u32(-2)
    C.ck("SLT both negative: -5 < -2 -> 1", i.step().wdata, 1,
         "an unsigned compare would give 0: 0xFFFFFFFB !< 0xFFFFFFFE")
    i = new()
    prog(i, [enc_r(0, 2, 1, 3, 4, OP_OP)])                    # sltu
    i.regs[1], i.regs[2] = u32(-5), u32(-2)
    C.ck("SLTU 0xFFFFFFFB < 0xFFFFFFFE -> 1", i.step().wdata, 1)
    for f3, a, b, want, nm in ((4, 0xFFFF_FFF0, 0x0000_0020, 0xFFFF_FFD0, "XOR"),
                               (6, 0x0000_00F0, 0x0000_000F, 0x0000_00FF, "OR"),
                               (7, 0x0000_00FF, 0x0000_000F, 0x0000_000F, "AND"),
                               (3, 0x0000_0001, 0xFFFF_FFFF, 1, "SLTU 1 vs -1"),
                               (2, 0x0000_0001, 0xFFFF_FFFF, 0, "SLT 1 vs -1")):
        i = new()
        prog(i, [enc_r(0, 2, 1, f3, 5, OP_OP)])
        i.regs[1], i.regs[2] = a, b
        C.ck("OP funct3=%d is %s" % (f3, nm), i.step().wdata, want)
    for f3, a, imm, want, nm in ((0, 0xFFFF_FFFB, 3, 0xFFFF_FFFE, "ADDI -5+3"),
                                 (2, 0xFFFF_FFFB, 3, 1, "SLTI -5<3"),
                                 (3, 0xFFFF_FFFB, 3, 0, "SLTIU -5<3"),
                                 (4, 0x0F0F_0F0F, 0x0F0, 0x0F0F_0FFF, "XORI"),
                                 (6, 0x0000_00F0, 0x00F, 0x0000_00FF, "ORI"),
                                 (7, 0x0000_00FF, -16, 0x0000_00F0, "ANDI -16")):
        i = new()
        prog(i, [enc_i(imm & 0xFFF, 1, f3, 5, OP_OPIMM)])
        i.regs[1] = a
        C.ck("OP-IMM funct3=%d is %s" % (f3, nm), i.step().wdata, u32(want))
    i = new()
    prog(i, [enc_r(0, 2, 1, 2, 4, OP_OP)])                    # slt x4,x1,x2
    i.regs[1], i.regs[2] = 5, u32(-2)
    C.ck("SLT 5 < -2 -> 0", i.step().wdata, 0)
    i = new()
    prog(i, [enc_r(0x20, 2, 1, 0, 3, OP_OP)])                 # sub x3,x1,x2
    i.regs[1], i.regs[2] = 5, 9
    C.ck("SUB 5 - 9", i.step().wdata, u32(-4))
    i = new()
    prog(i, [enc_r(0x20, 2, 1, 5, 3, OP_OP)])                 # sra x3,x1,x2
    i.regs[1], i.regs[2] = 0x8000_0000, 4
    C.ck("SRA 0x80000000 >> 4 (register form)", i.step().wdata, 0xF800_0000)
    i = new()
    prog(i, [enc_r(0x00, 2, 1, 5, 3, OP_OP)])                 # srl x3,x1,x2
    i.regs[1], i.regs[2] = 0x8000_0000, 4
    C.ck("SRL 0x80000000 >> 4", i.step().wdata, 0x0800_0000)
    i = new()
    prog(i, [enc_r(0, 2, 1, 1, 3, OP_OP)])                    # sll x3,x1,x2
    i.regs[1], i.regs[2] = 0x8000_0001, 31
    C.ck("SLL shift amount taken mod 32 (shamt 31)", i.step().wdata, 0x8000_0000)
    i = new()
    prog(i, [enc_i(4, 1, 1, 3, OP_OPIMM)])                    # slli x3,x1,4
    i.regs[1] = 0x0000_00AB
    C.ck("SLLI x3 = x1 << 4  (legal: inst[31:25]==0)", i.step().wdata,
         0x0000_0AB0)
    i = new()
    prog(i, [enc_i(0x400 | 3, 1, 5, 3, OP_OPIMM)])            # srai x3,x1,3
    i.regs[1] = u32(-32)
    C.ck("SRAI -32 >>i 3 = -4  (inst[30]=1 IS a legal SRAI)", i.step().wdata,
         u32(-4))
    i = new()
    prog(i, [enc_i(0x400, 1, 6, 3, OP_OPIMM)])                 # ori x3,x1,0x400
    i.regs[1] = 1
    C.ck("ORI imm=0x400 stays legal (inst[30] 属于立即数, 不是 funct)",
         i.step().wdata, 0x0000_0401)
    # ---- M8: RV32 的 shamt 只有 5 位。官方 rv32mi/shamt 是唯一把这条缺口
    #      照出来的东西（改前核不陷入, tohost=7；ISS 在第 59 条退休处与核分叉）。
    i = new()
    bad_slli = enc_i(32, 1, 1, 3, OP_OPIMM)                    # slli x3,x1,32
    prog(i, [bad_slli])
    i.regs[1] = 1
    i.mtvec = RAM + 0x40
    sr = i.step()
    C.ck("SLLI shamt[5]=1 -> illegal (rv32mi/shamt 的 0x02051513)",
         (sr.trap.cause if sr.trap else None, i.mtval),
         (EXC_ILLEGAL, u32(bad_slli)))
    i = new()
    bad_srli = enc_i(0x20 | 32, 1, 5, 3, OP_OPIMM)             # srli, shamt[5]=1
    prog(i, [bad_srli])
    sr = i.step()
    C.ck("SRLI shamt[5]=1 -> illegal (旧 RTL 只看 inst[31:26], 漏的就是这一位)",
         (sr.trap.cause if sr.trap else None), EXC_ILLEGAL)
    i = new()
    prog(i, [enc_i(16, 1, 1, 3, OP_OPIMM)])                    # slli x3,x1,16
    i.regs[1] = 1
    C.ck("SLLI x3,x1,16 仍然合法 (shamt[4] 置位不该陷入)", i.step().wdata, 65536)
    i = new()
    prog(i, [enc_r(1, 2, 1, 4, 3, OP_OP)])                     # div x3,x1,x2
    i.regs[1], i.regs[2] = u32(-2), 3
    C.ck("DIV 仍然合法 (旧 RTL 把 f3=4/5 判非法, rv32um/div 栽过)",
         i.step().wdata, 0)
    # 合流断言: 规范侧与 RTL 镜像侧必须逐位相同, 而 M8 之前的旧形状必须与两者
    # 都不同 —— 否则「两份规则」这个结构本身就是空的, 开关也从未真的换过判据。
    probe = ((OP_OPIMM, 1, 0x01), (OP_OPIMM, 5, 0x01), (OP_OPIMM, 1, 0x00),
             (OP_OPIMM, 5, 0x20), (OP_OPIMM, 6, 0x40), (OP_OP, 1, 0x01))
    spec = [imm_bad_spec(*p) for p in probe]
    rtl = [imm_bad_rtl(*p) for p in probe]
    leg = [imm_bad_legacy(*p) for p in probe]
    C.ck("M8 之后 RTL 镜像 == 规范 (六条编码逐位相同)", (spec == rtl, spec),
         (True, [True, True, False, False, False, False]))
    C.ck("旧 RTL 形状确实与规范不同 (否则这条合流断言是空跑)", leg != spec, True)
    i = new()
    prog(i, [enc_u(0x12345, 3, OP_LUI), enc_u(0x00001, 4, OP_AUIPC)])
    C.ck("LUI x3, 0x12345", i.step().wdata, 0x1234_5000)
    C.ck("AUIPC x4, 1 at pc=0x80000004", i.step().wdata, 0x8000_1004)

    # ==================================================== 2. branches / jumps
    print("\n=== 2. branches, jumps, and the branch-to-nowhere quirk ===")
    i = new()
    prog(i, [enc_b(16, 1, 2, 0)])                             # beq x1,x2,+16
    i.regs[1], i.regs[2] = 5, 6
    sr = i.step()
    C.ck("BRANCH NOT TAKEN: pc+4, no redirect, no reg write",
         (sr.rd, sr.wdata, i.pc), (None, None, RAM + 4),
         "expect pc 0x%08x, got 0x%08x" % (RAM + 4, i.pc))
    C.ck("... a not-taken branch still retires (minstret +1)", i.n_instr, 1)
    i = new()
    prog(i, [enc_b(16, 1, 2, 0)])
    i.regs[1], i.regs[2] = 5, 5
    i.step()
    C.ck("BEQ taken -> pc + 16", i.pc, RAM + 16)
    for f3, a, b, taken, nm in ((1, 1, 1, False, "BNE equal"),
                                (1, 1, 2, True, "BNE differ"),
                                (4, u32(-1), 1, True, "BLT -1 < 1"),
                                (5, u32(-1), 1, False, "BGE -1, 1"),
                                (4, 1, u32(-1), False, "BLT 1 < -1"),
                                (5, 1, u32(-1), True, "BGE 1, -1"),
                                (6, u32(-1), 1, False, "BLTU 0xFFFFFFFF < 1"),
                                (7, u32(-1), 1, True, "BGEU 0xFFFFFFFF, 1")):
        i = new()
        prog(i, [enc_b(8, 1, 2, f3)])
        i.regs[1], i.regs[2] = a, b
        i.step()
        C.ck("%s -> taken=%s" % (nm, taken), i.pc, RAM + (8 if taken else 4))
    i = new()
    prog(i, [enc_b(-8, 1, 2, 0)], start=RAM + 0x40)          # beq backwards
    i.regs[1], i.regs[2] = 3, 3
    i.pc = RAM + 0x40
    i.step()
    C.ck("BEQ taken with offset -8: 0x80000040 -> 0x80000038", i.pc, RAM + 0x38)
    i = new()
    prog(i, [enc_j(8, 3)])
    sr = i.step()
    C.ck("JAL: rd = pc+4, pc = pc + imm", (sr.wdata, i.pc), (RAM + 4, RAM + 8))
    i = new()
    prog(i, [enc_j(-0x1000, 3)], start=RAM + 0x1000)
    i.pc = RAM + 0x1000
    sr = i.step()
    C.ck("JAL backwards: link = pc+4, target = pc-4096",
         (sr.wdata, i.pc), (RAM + 0x1004, RAM))
    i = new()
    prog(i, [enc_i(0, 1, 0, 3, OP_JALR)])
    i.regs[1] = RAM + 0x11
    sr = i.step()
    C.ck("JALR clears target bit0 (x1 = 0x80000011, imm = 0)",
         (sr.wdata, i.pc), (RAM + 4, RAM + 0x10))
    i = new()
    prog(i, [enc_i(-2, 1, 0, 3, OP_JALR)])
    i.regs[1] = RAM + 0x13
    i.step()
    C.ck("JALR 0x80000013-2 = 0x80000011 -> 0x80000010", i.pc, RAM + 0x10)
    i = new()
    prog(i, [enc_i(1, 1, 0, 3, OP_JALR)])                     # target 0x80000002
    i.regs[1] = RAM + 1
    i.mtvec = RAM + 0x40
    sr = i.step()
    C.ck("JALR to a halfword-misaligned target -> cause 0, mtval = target",
         (sr.trap.cause if sr.trap else None, i.mcause, i.mtval),
         (EXC_IAFAIL_ALIGN, 0, RAM + 2),
         "M7: target = (0x80000001+1) & ~1 = 0x80000002, 在地址图上但违 IALIGN=32")
    C.ck("... the jumping instruction does not retire", (i.n_instr, i.pc),
         (0, RAM + 0x40))
    # M5: 不跳的分支, 目标哪怕落在地址图外也不许陷入。
    i = new()
    prog(i, [enc_b(-0x1000, 1, 2, 0)])                        # target 0x7FFFF000
    i.regs[1], i.regs[2] = 1, 2                               # condition FALSE
    i.mtvec = RAM + 0x40
    sr = i.step()
    C.ck("not-taken BEQ to an unmapped target does NOT trap (M5)",
         (sr.trap, i.n_instr, i.pc), (None, 1, RAM + 4),
         "epv32_core.v 的 br_err 里 AND 了 cond_take; 早先的核不 AND, 那是缺陷")
    # ... 而真的跳的那一条照样要判
    i = new()
    prog(i, [enc_b(-0x1000, 1, 2, 0)])
    i.regs[1], i.regs[2] = 1, 1                               # condition TRUE
    i.mtvec = RAM + 0x40
    sr = i.step()
    C.ck("taken BEQ to an unmapped target traps cause 0",
         (sr.trap.cause if sr.trap else None, i.mcause, i.mtval),
         (EXC_IAFAIL_ALIGN, 0, 0x7FFF_F000))
    C.ck("... mepc = the branch pc, bit0 cleared", i.mepc, RAM)

    # ======================================================= 3. loads / stores
    print("\n=== 3. loads, stores, byte enables ===")
    i = new()
    prog(i, [enc_s(0x100, 1, 3, 2, OP_STORE),                 # sw x3,0x100(x1)
              enc_i(0x100, 1, 2, 4, OP_LOAD)])                # lw x4,0x100(x1)
    i.regs[1], i.regs[3] = RAM, 0xDEAD_BEEF
    sr = i.step()
    C.ck("SW commit tuple (addr, wdata, be)",
         (sr.mem_addr, sr.mem_wdata, sr.mem_be, len(sr.stores)),
         (RAM + 0x100, 0xDEAD_BEEF, 0xF, 1))
    C.ck("SW really wrote the RAM array", i.ram[widx(RAM + 0x100)], 0xDEAD_BEEF)
    C.ck("SW commits no register write and no loads",
         (sr.rd, sr.loads), (None, []))
    sr = i.step()
    C.ck("LW round trip: rd/wdata, stores empty",
         (sr.rd, sr.wdata, sr.stores, sr.loads),
         (4, 0xDEAD_BEEF, [], [(RAM + 0x100, 0xDEAD_BEEF)]))
    i = new()
    prog(i, [enc_i(0, 1, 0, 3, OP_LOAD), enc_i(0, 1, 4, 3, OP_LOAD),
              enc_i(0, 1, 1, 3, OP_LOAD), enc_i(0, 1, 5, 3, OP_LOAD)])
    poke(i, RAM + 0x100, 0x1234_7899)
    i.regs[1] = RAM + 0x100
    r = [i.step().wdata for _ in range(4)]
    C.ck("LB 0x99 -> 0xFFFFFF99", r[0], u32(-103))
    C.ck("LBU 0x99 -> 0x99", r[1], 0x99)
    C.ck("LH 0x7899 -> 0x7899", r[2], 0x7899)
    C.ck("LHU 0x7899 -> 0x7899", r[3], 0x7899)
    i = new()
    prog(i, [enc_s(0, 1, 2, 0, OP_STORE),                     # sb x2, 0(x1)
              enc_s(0, 1, 2, 1, OP_STORE)])                   # sh x2, 0(x1)
    poke(i, RAM + 0x100, 0xFFFF_FFFF)
    i.regs[1] = RAM + 0x100
    i.regs[2] = 0x0000_00AB
    sr = i.step()
    C.ck("SB at addr%4==0 -> be=0b0001",
         (sr.mem_be, i.ram[widx(RAM + 0x100)]), (0b0001, 0xFFFF_FFAB))
    i.regs[2] = 0x1234_CDDE
    sr = i.step()
    C.ck("SH at addr%4==0 -> be=0b0011",
         (sr.mem_be, i.ram[widx(RAM + 0x100)]), (0b0011, 0xFFFF_CDDE),
         "epv32_core.v:70's off[0] arm is unreachable: addr%4==2 never commits")
    i = new()
    prog(i, [enc_i(1, 1, 0, 5, OP_LOAD)])                     # lb x5, 1(x1)
    i.regs[1] = RAM + 0x100
    poke(i, RAM + 0x100, 0xFFFF_FFAB)
    sr = i.step()
    C.ck("LB at addr%4==1 does NOT fault and reads byte 1 (M6 hardware path)",
         (sr.trap, sr.wdata), (None, 0xFFFF_FFFF))
    # 字内/跨字的非对齐读全一档。off=1 的 LH/LHU 是**模型自己**错过的那格
    # (rv32ui/ma_data 的逐条轨迹比对抓到: 核给 0x201, 模型给 0x100)。
    pat_w, pat_w1 = 0x4433_2211, 0x8877_6655
    for f3v, nmv, offv, wantv in (
            (0, "lb", 0, 0x0000_0011), (0, "lb", 1, 0x0000_0022),
            (0, "lb", 3, 0x0000_0044), (4, "lbu", 3, 0x0000_0044),
            (1, "lh", 0, 0x0000_2211), (1, "lh", 2, 0x0000_4433),
            (1, "lh", 1, 0x0000_3322), (5, "lhu", 1, 0x0000_3322),
            (5, "lhu", 3, 0x0000_5544), (2, "lw", 0, 0x4433_2211),
            (2, "lw", 1, 0x5544_3322), (2, "lw", 2, 0x6655_4433),
            (2, "lw", 3, 0x7766_5544)):
        z = new()
        prog(z, [enc_i(offv, 1, f3v, 5, OP_LOAD)])
        poke(z, RAM + 0x100, pat_w)
        poke(z, RAM + 0x104, pat_w1)
        z.regs[1] = RAM + 0x100
        C.ck("%s @addr%%4==%d (off=%d 取字节 %d..%d)"
             % (nmv, offv, offv, offv, offv + (1 if f3v in (0, 4) else
                                               (2 if f3v in (1, 5) else 3))),
             (z.step().trap, z.regs[5]), (None, wantv))
    # quirk 3 仍然是可复现的, 只是它现在是「老核」的行为而不是当前核的
    global ADDR_OK_OLDED_ALIGNMENT
    keep3 = ADDR_OK_OLDED_ALIGNMENT
    ADDR_OK_OLDED_ALIGNMENT = True
    i2 = new()
    prog(i2, [enc_i(1, 1, 0, 5, OP_LOAD)])
    i2.regs[1] = RAM + 0x100
    poke(i2, RAM + 0x100, 0xFFFF_FFAB)
    C.ck("quirk 3 (legacy addr_ok demanded %4==0) still reproducible on demand",
         i2.step().trap.cause, EXC_LAF, "quirk 3")
    ADDR_OK_OLDED_ALIGNMENT = keep3
    i3 = new()
    prog(i3, [enc_i(1, 1, 2, 5, OP_LOAD)])                    # lw x5, 1(x1) 跨字
    i3.regs[1] = RAM + 0x100
    poke(i3, RAM + 0x100, 0xFFFF_FFAB)
    poke(i3, RAM + 0x104, 0xDDCC_BBAA)
    sr = i3.step()
    C.ck("LW across a word boundary is assembled from two aligned reads (M6)",
         (sr.trap, sr.wdata), (None, 0xAAFF_FFFF))
    i4 = new()
    prog(i4, [enc_s(1, 1, 5, 2, OP_STORE)])                   # sw x5, 1(x1) 跨字
    i4.regs[1] = RAM + 0x100
    i4.regs[5] = 0x4433_2211
    poke(i4, RAM + 0x100, 0x0000_0000)
    poke(i4, RAM + 0x104, 0x0000_0000)
    sr = i4.step()
    # 轨迹的形状照抄 RTL: o_tr_addr = 原始(非对齐)地址, o_tr_mdata = rs2 原值,
    # o_tr_mbe = 两半字节通道的并集 (epv32_core.v 的 memwb_* 那一组)。
    # 总线上是两次对齐写, 但退休轨迹是一条指令一条记录。
    C.ck("SW at addr%4==1 lands across two words, be is the byte-lane union",
         (sr.mem_addr, sr.mem_wdata, sr.mem_be,
          i4.ram[widx(RAM + 0x100)], i4.ram[widx(RAM + 0x104)]),
         (RAM + 0x101, 0x4433_2211, 0b1111, 0x3322_1100, 0x0000_0044))
    i = new()
    prog(i, [enc_i(0x10, 0, 2, 3, OP_LOAD)])                  # lw x3, 16(x0)
    poke(i, RAM + 0x10, 0xCAFE_F00D)
    C.ck("rs1=x0 bases the address at 0, so 16(x0) is off the map -> cause 5",
         i.step().trap.cause, EXC_LAF)
    i = new()
    prog(i, [enc_i(-4, 1, 2, 3, OP_LOAD)])                     # lw x3, -4(x1)
    poke(i, RAM + 0x100, 0xCAFE_F00D)
    i.regs[1] = RAM + 0x104
    C.ck("LW with a negative imm_i reads x1-4", i.step().wdata, 0xCAFE_F00D)
    i = new()
    prog(i, [enc_s(-4, 1, 2, 2, OP_STORE)])                    # sw x2, -4(x1)
    i.regs[1], i.regs[2] = RAM + 0x104, 0xFEED_FACE
    sr = i.step()
    C.ck("SW with a negative imm_s writes x1-4",
         (sr.mem_addr, i.ram[widx(RAM + 0x100)]), (RAM + 0x100, 0xFEED_FACE))
    C.ck("load_image is little-endian and byte granular",
         (lambda z: (z.load_image(RAM + 0x20, bytes([1, 2, 3, 4, 5])),
                     [z.ram[widx(RAM + 0x20)], z.ram[widx(RAM + 0x24)]])[1])
         (new()), [0x0403_0201, 5])
    C.ck("reads of unwritten words return 0 (sparse RAM)", new().ram.get(4095, 0), 0)

    # ============================================================ 4. exact traps
    print("\n=== 4. precise traps and RTL priority order ===")
    # 非对齐访存现在是**编译期参数** (epv32_core.v 的 SUPPORT_UNALIGNED), 两档都要
    # 在模型这边各跑一遍: 硬件拆分档不陷入, 陷入档报 4/6 且一条都不提交。
    global SUPPORT_MISALIGNED
    i = new()
    prog(i, [enc_i(2, 1, 2, 3, OP_LOAD)])                     # lw x3, 2(x1)
    poke(i, RAM + 0x100, 0x1122_3344)
    i.regs[1] = RAM + 0x100
    i.mtvec = RAM + 0x80
    sr = i.step()
    C.ck("hardware tier (M6): LW at addr%4==2 splits across two words, no trap",
         (sr.trap, sr.wdata), (None, 0x0000_1122))
    keep_ms = SUPPORT_MISALIGNED
    SUPPORT_MISALIGNED = False
    i = new()
    prog(i, [enc_i(2, 1, 2, 3, OP_LOAD)])
    poke(i, RAM + 0x100, 0x1122_3344)
    i.regs[1] = RAM + 0x100
    i.mtvec = RAM + 0x80
    sr = i.step()
    C.ck("trapping tier (=SUPPORT_UNALIGNED 0): cause 4, mtval = faulting address",
         (sr.trap.cause if sr.trap else None, i.mcause, i.mtval),
         (EXC_LAMF, 4, RAM + 0x102))
    C.ck("... nothing commits: rd None, retired False, minstret 0",
         (sr.rd, sr.retired, i.n_instr, sr.loads), (None, False, 0, []))
    C.ck("... mepc = pc with bit0 cleared, pc = mtvec_base (mode bits masked)",
         (i.mepc, sr.next_pc), (RAM, RAM + 0x80))
    i = new()
    prog(i, [enc_s(2, 1, 3, 2, OP_STORE)])                    # sw x3, 2(x1)
    i.regs[1], i.regs[3] = RAM + 0x100, 7
    sr = i.step()
    C.ck("trapping tier: UNALIGNED SW reports cause 6 and writes nothing",
         (sr.trap.cause, i.ram.get((RAM + 0x100) >> 2, 0)), (EXC_SAMF, 0))
    SUPPORT_MISALIGNED = keep_ms
    i = new()
    prog(i, [enc_i(0, 1, 2, 3, OP_LOAD)])
    i.regs[1] = 0xC000_0000
    C.ck("LW from aligned-but-unmapped -> cause 5", i.step().trap.cause, EXC_LAF)
    i = new()
    prog(i, [enc_s(0, 1, 2, 2, OP_STORE)])
    i.regs[1] = ADDR_RAM_BASE + 0x40000        # 0x80040000 aligned, off map
    i.regs[2] = 1
    C.ck("aligned SW one word past the last RAM word -> cause 7",
         i.step().trap.cause, EXC_SAF)
    i = new()
    prog(i, [enc_i(2, 1, 1, 3, OP_LOAD)])                     # lh 2(x1)
    poke(i, RAM + 0x100, 0x1122_3344)
    i.regs[1] = RAM + 0x100                    # effective address = word+2
    C.ck("M6 tier: LH at addr%4==2 is aligned for a halfword -> reads, no fault",
         i.step().wdata, 0x0000_1122,
         "旧核在这里报 5 而不是取值: 那是 legacy addr_ok 的档位, 下面单独复现")
    i = new()
    prog(i, [enc_i(3, 1, 5, 3, OP_LOAD)])                     # lhu 3(x1) 跨字
    poke(i, RAM + 0x100, 0x1122_3344)
    poke(i, RAM + 0x104, 0x0000_0077)
    i.regs[1] = RAM + 0x100
    C.ck("M6 tier: LHU at addr%4==3 splits across the two words",
         i.step().wdata, 0x0000_7711)
    keep3b = ADDR_OK_OLDED_ALIGNMENT
    ADDR_OK_OLDED_ALIGNMENT = True
    i = new()
    prog(i, [enc_i(2, 1, 1, 3, OP_LOAD)])
    poke(i, RAM + 0x100, 0x1122_3344)
    i.regs[1] = RAM + 0x100
    C.ck("legacy tier: LH at addr%4==2 -> LOAD ACCESS FAULT, not misaligned",
         i.step().trap.cause, EXC_LAF,
         "quirk 3: 老核的 addr_ok 要求每个访问 %4==0")
    ADDR_OK_OLDED_ALIGNMENT = keep3b
    i = new()
    prog(i, [0xFFFF_FFFF])
    i.mtvec = RAM + 0x40
    sr = i.step()
    C.ck("ILLEGAL instruction -> cause 2, mtval = whole instruction word",
         (sr.trap.cause if sr.trap else None, i.mcause, i.mtval),
         (EXC_ILLEGAL, 2, 0xFFFF_FFFF))
    for w, nm, want in (
            (enc_i(0, 0, 4, 0, OP_SYSTEM), "SYSTEM funct3=4 (csr_f3_bad)", 2),
            (enc_r(0x7F, 0, 1, 0, 2, OP_OP), "OP funct7=0x7F (reg_op_bad)", 2),
            (enc_r(0, 0, 1, 4, 2, OP_OP), "OP funct3=4 f7=0 = XOR (legal)", None),
            (enc_b(4, 1, 2, 2), "BRANCH funct3=2 (br_f3_bad)", 2),
            (enc_i(0, 1, 3, 2, OP_LOAD), "LOAD funct3=3 (ld_f3_bad)", 2),
            (enc_s(0, 1, 2, 3, OP_STORE), "STORE funct3=3 (st_f3_bad)", 2),
            (enc_i(0, 1, 1, 3, OP_JALR), "JALR funct3=1 (other_f3_bad)", 2),
            (enc_i(0x123, 0, 0, 0, OP_SYSTEM), "SYSTEM code 0x123 (sys_bad)", 2),
            (enc_i(0, 1, 7, 2, OP_LOAD), "LOAD funct3=7 NOT rejected (quirk 5)", None),
            (0x0000_007F, "opcode 0x7F unknown", 2),
            (enc_r(1, 2, 1, 4, 4, OP_OP), "OP f7=1 funct3=4 = DIV (legal)", None)):
        i = new()
        prog(i, [w])
        i.regs[1], i.regs[2] = RAM + 0x100, 4
        sr = i.step()
        C.ck(nm, sr.trap.cause if sr.trap else None, want)
    # priority: illegal decode beats the instruction access fault
    i = new()
    poke(i, RAM, 0xFFFF_FFFF)                                 # word 0 is illegal
    i.pc = 0xC000_0000
    i.mtvec = RAM + 0x40
    sr = i.step()
    C.ck("priority: illegal decode beats instruction access fault",
         (sr.trap.cause, i.mtval), (EXC_ILLEGAL, 0xFFFF_FFFF),
         "epv32_core.v:426 tests ill_exc before fetch_err")
    i = new()
    poke(i, RAM, enc_i(0, 0, 0, 0, OP_OPIMM))                 # word 0 is a nop
    i.pc = 0xC000_0000
    i.mtvec = RAM + 0x40
    sr = i.step()
    C.ck("instruction access fault: bad EX pc -> cause 1, mtval = pc",
         (sr.trap.cause, i.mcause, i.mtval), (EXC_IAF, 1, 0xC000_0000))
    i = new()
    prog(i, [0x30200073])                                     # mret
    i._csr_write(0x341, 0xC000_0001)                           # via the CSR path
    i.mtvec = RAM + 0x40
    i.step()
    C.ck("mret to a bad address: mepc write cleared bit0, pc = mepc",
         (i.pc, i.mepc), (0xC000_0000, 0xC000_0000))
    sr = i.step()
    C.ck("... and the next fetch faults with cause 1", sr.trap.cause, EXC_IAF)

    # ==================================================== 5. system instructions
    print("\n=== 5. ebreak / ecall / mret / wfi ===")
    i = new()
    prog(i, [0x00100073])
    i.mtvec = RAM + 0x40
    sr = i.step()
    C.ck("EBREAK -> cause 3, mtval = pc", (sr.trap.cause, i.mtval, i.mcause),
         (EXC_BREAK, RAM, 3))
    C.ck("... trapped instruction does not retire", i.n_instr, 0)
    i = new()
    prog(i, [0x00000073])
    i.mtvec = RAM + 0x40
    i.mstatus = 1 << 3
    sr = i.step()
    C.ck("ECALL from M -> cause 11, mtval 0", (sr.trap.cause, i.mtval), (EXC_ECM, 0))
    C.ck("... MIE cleared, MPIE <- MIE, MPP forced to 11", i.mstatus,
         0x1800 | 0x80, "mstatus = 0x%08x" % i.mstatus)
    i.mstatus = 0
    i._enter_trap(StepResult(), 11, False, 0)                 # MIE was 0
    C.ck("... MPIE <- 0 when MIE was already 0", i.mstatus, 0x1800)
    i = new()
    prog(i, [0x00000073])
    poke(i, RAM + 0x40, 0x30200073)
    i.mtvec = RAM + 0x40
    i.mstatus = 1 << 3
    i.step()
    C.ck("handler entry: pc = mtvec, mepc = ecall pc", (i.pc, i.mepc),
         (RAM + 0x40, RAM))
    before = i.n_instr
    sr = i.step()                                             # mret
    C.ck("MRET: pc <- mepc[31:1]0, MIE <- MPIE, MPIE <- 1",
         (i.pc, i.mstatus), (RAM, 0x1800 | 0x08 | 0x80))
    C.ck("MRET never retires (ex_commit excludes mret_go)",
         (i.n_instr, before, sr.retired, sr.rd), (0, 0, False, None))
    i = new()
    prog(i, [0x10500073])
    sr = i.step()
    C.ck("WFI = NOP (retires, pc+4, no reg write)",
         (sr.rd, i.pc, i.n_instr), (None, RAM + 4, 1))
    i = new()
    prog(i, [enc_i(0, 0, 0, 0, OP_FENCE), enc_i(0, 0, 1, 0, OP_FENCE)])
    sr = i.step()
    C.ck("FENCE = NOP", (sr.rd, i.pc, i.n_instr), (None, RAM + 4, 1))
    sr = i.step()
    C.ck("FENCE.I (funct3=1) = NOP", (sr.rd, i.pc, i.n_instr), (None, RAM + 8, 2))

    # ================================================================= 6. CSRs
    print("\n=== 6. Zicsr + machine-mode CSRs ===")
    i = new()
    prog(i, [enc_i(0x340, 1, 1, 0, OP_SYSTEM),                # csrrw mscratch,x1,x0
              enc_i(0x340, 0, 2, 2, OP_SYSTEM),               # csrrs x2,x0,mscratch
              enc_i(0x340, 3, 3, 0, OP_SYSTEM)])              # csrrc x0,x3,mscratch
    i.regs[1] = 0xCAFE_BABE
    sr = i.step()
    C.ck("CSRRW with rd=x0: no commit, CSR still written",
         (sr.rd, i.mscratch), (None, 0xCAFE_BABE))
    sr = i.step()
    C.ck("CSRRS with rs1=x0: reads the old value, write suppressed",
         (sr.wdata, i.mscratch), (0xCAFE_BABE, 0xCAFE_BABE))
    i.regs[3] = 0xCAFE_BABE
    sr = i.step()
    C.ck("CSRRC clears exactly the bits it read", (sr.rd, i.mscratch),
         (None, 0))
    i = new()
    prog(i, [enc_i(0x340, 9, 6, 2, OP_SYSTEM)])               # csrrwi x2,mscratch,9
    sr = i.step()
    C.ck("CSRRWI writes the 5-bit zimm, rd gets the old value",
         (sr.wdata, i.mscratch), (0, 9))
    i = new()
    prog(i, [enc_i(0x340, 9, 7, 2, OP_SYSTEM)])               # csrrci x2,mscratch,9
    i.mscratch = 0x0000_000F
    C.ck("CSRRCI clears the zimm bits", (i.step().wdata, i.mscratch), (0x0F, 0x06))
    i = new()
    prog(i, [enc_i(0x5FF, 1, 1, 2, OP_SYSTEM)])               # csrrw to 0x5FF
    i.regs[1] = 0xDEAD
    sr = i.step()
    C.ck("undefined CSR: NO TRAP, rd <- 0, write ignored",
         (sr.trap, sr.wdata, i._csr_read(0x5FF)), (None, 0, 0),
         "deliberate documented boundary, epv32_csr.v:10-12")
    i = new()
    C.ck("misa == 0x40001200", i._csr_read(0x301), 0x4000_1200)
    C.ck("mvendorid/marchid/mhartid = 0, mimpid = IMPID",
         [i._csr_read(a) for a in (0xF11, 0xF12, 0xF14, 0xF13)], [0, 0, 0, 3])
    i = new()
    C.ck("mstatus reads 0 out of reset (MPP not forced until written)",
         (i.mstatus, i._csr_read(0x300)), (0, 0))
    i._csr_write(0x300, 0xFFFF_FFFF)
    C.ck("a mstatus write only ever lands MIE / MPIE / MPP=11",
         (i.mstatus, i._csr_read(0x300)), (0x1888, 0x1888))
    i._csr_write(0x300, 0)
    C.ck("... and clearing it can never clear MPP", i.mstatus, 0x1800)
    i._csr_write(0x300, 0x8)
    C.ck("MIE is settable, MPIE stays 0, MPP stays 11", i.mstatus, 0x1808)
    i = new()
    i._csr_write(0x341, 0x1113)
    C.ck("mepc write clears bit0", i.mepc, 0x1112)
    i._csr_write(0x305, 0x8000_0103)
    C.ck("mtvec keeps the raw mode bits (no WARL masking)", i.mtvec, 0x8000_0103)
    i._csr_write(0x344, 0xFFFF)
    C.ck("mip is read-only", i.mip, 0)
    i._csr_write(0x301, 0xFFFF_FFFF)
    C.ck("misa write ignored", i._csr_read(0x301), 0x4000_1200)
    i._csr_write(0xB00, 0x1234)
    C.ck("M9: mcycle accepts a write (it used to be swallowed)",
         i._csr_read(0xB00), 0x1234)
    i._csr_write(0xB80, 0x5)
    C.ck("M9: mcycleh composes into the same 64-bit counter",
         i._csr_read(0xB00) | (i._csr_read(0xB80) << 32), 0x5_0000_1234)
    i = new()
    i._csr_write(0xB02, 0xFFFF_FFFF)
    i._csr_write(0xB82, 0xFFFF_FFFF)
    C.ck("M9: two half-writes make ONE 64-bit minstret",
         i.n_instr, 0xFFFF_FFFF_FFFF_FFFF)
    # 官方 rv32mi/instret_overflow 的形状在模型里复现一遍: 两半都写 -1,
    # 隔一条指令之后两半都该读回 0 (0xFFFF...F + 1 回到 0)。
    i = new()
    prog(i, [enc_i(0xFFF, 0, 0, 5, OP_OPIMM),        # addi x5, x0, -1
             enc_i(0xB02, 5, 1, 0, OP_SYSTEM),       # csrw minstret, x5
             enc_i(0xB82, 5, 1, 0, OP_SYSTEM),       # csrw minstreth, x5
             enc_i(0, 0, 0, 0, OP_OPIMM),            # addi x0, x0, 0  (nop)
             enc_i(0xB02, 0, 2, 4, OP_SYSTEM),       # csrr x4, minstret
             enc_i(0xB82, 0, 2, 6, OP_SYSTEM)])      # csrr x6, minstreth
    _ = [i.step() for _ in range(4)]
    C.ck("M9: the writer instruction suppresses its own increment (low)",
         i.step().wdata, 0)
    C.ck("M9: ... and the high half reads back 0 too", i.step().wdata, 0)
    i = new()
    i.n_instr = 3
    prog(i, [enc_i(0xB02, 0, 1, 0, OP_SYSTEM),        # csrw minstret, x0
             enc_i(0xB02, 0, 2, 4, OP_SYSTEM)])       # csrr x4, minstret
    i.step()
    C.ck("M9: csrw minstret,x0 leaves exactly 0, not 1", i.n_instr, 0)
    C.ck("M9: the very next read sees the written value", i.step().wdata, 0)
    i = new()
    i.n_instr = 4
    prog(i, [enc_i(0, 0, 0, 0, OP_OPIMM), enc_i(0, 0, 0, 0, OP_OPIMM),
             enc_i(0, 0, 0, 0, OP_OPIMM)])
    i._csr_write(0x320, 0x4)
    i.step()
    i.step()
    C.ck("M9: mcountinhibit.IR freezes minstret", i.n_instr, 4)
    i._csr_write(0x320, 0x0)
    i.step()
    C.ck("M9: ... and clearing IR restarts it", i.n_instr, 5)
    i = new()
    i._csr_write(0x320, 0xFFFF_FFF8)
    C.ck("M9: mcountinhibit is WARL to CY|IR only", i._csr_read(0x320), 0)
    i._csr_write(0x320, 0x7)
    C.ck("M9: mcountinhibit keeps CY and IR, drops TM/HPM",
         i._csr_read(0x320), 0x5)
    i = new()
    i._csr_write(0x3B0, 0xFFFF_FFFF)
    C.ck("M9: pmpaddr0 WARL drops the 2 bits with no physical address",
         i._csr_read(0x3B0), 0x3FFF_FFFF)
    i = new()
    i._csr_write(0x3A0, 0xFFFF_FFFF)
    C.ck("M9: pmpcfg0 keeps 6 writable bits per entry",
         i._csr_read(0x3A0), 0x3F3F_3F3F)
    C.ck("M9: ... and that same write locked all four entries",
         [i.pmpcfg[k] & 1 for k in range(PMP_N)], [1] * PMP_N)
    i._csr_write(0x3A0, 0)
    C.ck("M9: a locked pmpcfg cannot be cleared before reset",
         i._csr_read(0x3A0), 0x3F3F_3F3F)
    i._csr_write(0x3B0, 0x12)
    C.ck("M9: the lock protects the paired pmpaddr too",
         i._csr_read(0x3B0), 0)
    i = new()
    i._csr_write(0x3B1, 0x1234_5678)
    C.ck("M9: an unlocked pmpaddr retains all 30 implemented bits",
         i._csr_read(0x3B1), 0x1234_5678)
    C.ck("M9: pmpaddr4 (not implemented) still reads 0 and ignores writes",
         (i._csr_write(0x3B4, 0xFFFF), i._csr_read(0x3B4))[1], 0)
    i = new()
    prog(i, [enc_i(0xB00, 0, 2, 3, OP_SYSTEM),                # csrr x3, mcycle
              enc_i(0xB02, 0, 2, 4, OP_SYSTEM)])              # csrr x4, minstret
    i.n_instr = 7
    C.ck("csrr x3, mcycle samples the EX-stage clock (IF@0 ID@1 EX@2)",
         i.step().wdata, 2)
    C.ck("csrr x4, minstret reads the retired count", i.step().wdata, 8,
         "the mcycle read itself retired, so minstret is 8 not 7")
    i = new()
    i.cycle = 0x1_0000_0002
    i.n_instr = 0x1_0000_0003
    C.ck("mcycle/mcycleh and cycle/cycleh aliases",
         [i._csr_read(a) for a in (0xB00, 0xB80, 0xC00, 0xC80)], [2, 1, 2, 1])
    C.ck("minstret/minstreth and instret/instreth aliases",
         [i._csr_read(a) for a in (0xB02, 0xB82, 0xC02, 0xC82)], [3, 1, 3, 1])
    i.vcfg = 0x1234
    C.ck("0x7C0 mirrors vcfg", i._csr_read(CSR_VCFG), 0x1234)
    i._csr_write(CSR_VCFG, 0)
    C.ck("0x7C0 mirror is read-only", i.vcfg, 0x1234)
    C.ck("EPV_ENABLE=0 makes the 0x7C0 mirror read 0",
         (lambda z: (setattr(z, "enable_epv", False), z._csr_read(CSR_VCFG))[1])
         (i), 0)

    # =============================================================== 7. RV32M
    print("\n=== 7. RV32M with the two RISC-V corner cases ===")
    for op, a, b, want, nm in ((0, 0x0001_0001, 0x0001_0001, 0x0002_0001, "MUL"),
                               (0, 0xFFFF_FFFF, 0x0000_0002, 0xFFFF_FFFE, "MUL -1*2"),
                               (1, 0xFFFF_FFFF, 0xFFFF_FFFF, 0x0000_0000, "MULH -1*-1"),
                               (1, 0xFFFF_FFFF, 0x0000_0002, 0xFFFFFFFF, "MULH -1*2"),
                               (3, 0xFFFF_FFFF, 0xFFFF_FFFF, 0xFFFF_FFFE, "MULHU"),
                               (2, 0xFFFF_FFFF, 0x0000_0002, 0xFFFFFFFF, "MULHSU -1*2u"),
                               (2, 0x8000_0000, 0xFFFF_FFFF, 0x8000_0000,
                                "MULHSU INT_MIN*(2^32-1): -2^63+2^31"),
                               (4, u32(-20), 3, u32(-6), "DIV -20/3 trunc"),
                               (5, 0xFFFF_FFFF, 2, 0x7FFF_FFFF, "DIVU"),
                               (6, u32(-7), 2, u32(-1), "REM -7%2 = -1"),
                               (7, 0xFFFF_FFFF, 2, 1, "REMU"),
                               (6, 17, 5, 2, "REM 17%5"),
                               (4, 0x8000_0000, 0xFFFF_FFFF, 0x8000_0000,
                                "DIV INT_MIN/-1 -> INT_MIN"),
                               (6, 0x8000_0000, 0xFFFF_FFFF, 0,
                                "REM INT_MIN%-1 -> 0"),
                               (4, 12, 0, MASK32, "DIV by zero -> -1"),
                               (5, 12, 0, MASK32, "DIVU by zero -> -1"),
                               (6, 12, 0, 12, "REM by zero -> dividend"),
                               (7, 12, 0, 12, "REMU by zero -> dividend")):
        i = new()
        prog(i, [enc_r(1, 2, 1, op, 5, OP_OP)])               # f7=1, funct3=op
        i.regs[1], i.regs[2] = a, b
        C.ck(nm, i.step().wdata, want)
    C.ck("REM with a negative dividend keeps the dividend's sign (RTL sign fix)",
         ISS.muldiv(6, u32(-9), u32(-3)), u32(0))
    C.ck("MULHSU INT_MIN * INT_MAX: -2^62+2^31 -> high 0xC0000000",
         ISS.muldiv(2, 0x8000_0000, 0x7FFF_FFFF), 0xC000_0000)
    # the RTL decode bug, reproduced on demand
    global MIRROR_RTL_DECODE_BUGS
    # M8 之后这个开关换不出行为差异 —— RTL 与规范在这一条上合流了。
    # 于是这两条断言改判「开关两侧必须相同」（合流），而旧 RTL 的形状由
    # imm_bad_legacy / md_bad_legacy 单独复现，确保"合流"不是把两边都写错。
    keep = MIRROR_RTL_DECODE_BUGS
    divw = enc_r(1, 2, 1, 4, 4, OP_OP)                         # div x4, x1, x2
    sllw = enc_i(4, 1, 1, 3, OP_OPIMM)                         # slli x3, x1, 4
    outs = []
    for flag in (False, True):
        MIRROR_RTL_DECODE_BUGS = flag
        i = new()
        prog(i, [divw])
        i.regs[1], i.regs[2] = 20, 4
        sr = i.step()
        outs.append((sr.trap.cause if sr.trap else None, sr.wdata))
        i = new()
        prog(i, [sllw])
        i.regs[1] = 1
        sr = i.step()
        outs.append((sr.trap.cause if sr.trap else None, sr.wdata))
    MIRROR_RTL_DECODE_BUGS = keep
    C.ck("MIRROR_RTL_DECODE_BUGS 两侧一致 (DIV/SLLI 都不再被判非法)",
         outs, [(None, 5), (None, 16), (None, 5), (None, 16)])
    C.ck("旧 RTL 形状确实会把 DIV 与 SLLI 判非法 (合流断言不是空跑)",
         [md_bad_legacy(OP_OP, 4, 1), imm_bad_legacy(OP_OPIMM, 1, 0),
          md_bad_spec(OP_OP, 4, 1), imm_bad_spec(OP_OPIMM, 1, 0)],
         [True, True, False, False])
    C.ck("... and the flag is restored", MIRROR_RTL_DECODE_BUGS, keep)

    # ==================================================== 8. timer interrupts
    print("\n=== 8. machine timer interrupt ===")
    i = new()
    prog(i, [enc_i(1, 0, 0, 1, OP_OPIMM),                     # addi x1,x0,1
              enc_b(-4, 0, 0, 0)])                            # beq x0,x0,-4
    poke(i, RAM + 0x40, 0x00000073)                           # handler: ecall
    i.mtvec = RAM + 0x40
    i.mie = 1 << 7
    i.mstatus = 1 << 3
    i.mtimecmp = 3
    hit = None
    for _ in range(20):
        sr = i.step()
        if sr.trap is not None and sr.trap.is_irq:
            hit = sr
            break
    C.ck("TIMER IRQ: mcause = (1<<31)|7", i.mcause, (1 << 31) | 7)
    C.ck("... taken at the trap vector with mtval = 0", (i.pc, i.mtval),
         (RAM + 0x40, 0))
    C.ck("... the interrupted instruction does not commit",
         (hit.retired, hit.rd), (False, None))
    C.ck("... MIE cleared, MPIE set from the old MIE",
         (i.mstatus & 0x8, i.mstatus & 0x80), (0, 0x80))
    C.ck("... mepc = the preempted pc", i.mepc, RAM + 4)
    C.ck("... minstret counted the one instruction that retired first: "
         "mtime hits mtimecmp=3 during the branch's own EX (cycle 3)",
         i.n_instr, 1)
    C.ck("... the IRQ never re-fires because MIE is now 0",
         i._irq_pending(), None)
    for mode, want, nm in ((0, RAM + 0x100, "mode 0 (Direct)"),
                           (1, RAM + 0x11C, "mode 1 (Vectored, per spec)"),
                           (2, RAM + 0x100, "mode 2 (reserved -> direct)"),
                           (3, RAM + 0x100, "mode 3 (reserved -> direct)")):
        i = new()
        prog(i, [enc_b(0, 0, 0, 0)])
        i.mtvec = (RAM + 0x100) | mode
        i.mie, i.mstatus = 1 << 7, 1 << 3
        i.mtimecmp = 0
        i.step()
        C.ck("IRQ %s -> target 0x%08x" % (nm, want), i.pc, want,
             "cause 7 in vectored mode: base + 4*7 = +28")
    global MIRROR_RTL_MTVEC_MODE_BIT
    keep_m = MIRROR_RTL_MTVEC_MODE_BIT
    MIRROR_RTL_MTVEC_MODE_BIT = True
    i = new()
    prog(i, [enc_b(0, 0, 0, 0)])
    i.mtvec = (RAM + 0x100) | 1                                # spec vectored
    i.mie, i.mstatus = 1 << 7, 1 << 3
    i.mtimecmp = 0
    i.step()
    C.ck("MIRROR_RTL_MTVEC_MODE_BIT: the RTL reads mtvec[1], so mode 1 is DIRECT",
         i.pc, RAM + 0x100, "epv32_csr.v:81 `wire vmode_vec = mtvec[1];`")
    j = new()
    prog(j, [enc_b(0, 0, 0, 0)])
    j.mtvec = (RAM + 0x100) | 2
    j.mie, j.mstatus = 1 << 7, 1 << 3
    j.mtimecmp = 0
    j.step()
    C.ck("... and mode 2 is the one the RTL treats as vectored", j.pc, RAM + 0x11C)
    MIRROR_RTL_MTVEC_MODE_BIT = keep_m
    i = new()
    prog(i, [enc_b(0, 0, 0, 0)])
    i.mtvec = RAM + 0x40
    i.mie = 1 << 7
    i.mstatus = 0
    i.mtimecmp = 0
    C.ck("mstatus.MIE = 0 masks the interrupt", i.step().trap, None)
    i.mstatus = 1 << 3
    i.mie = 0
    C.ck("mie = 0 masks the interrupt", i.step().trap, None)
    i.mie = 1 << 7
    C.ck("mip bit7 tracks mtime >= mtimecmp", (i.mip >> 7) & 1, 1)
    i.mie = (1 << 11) | (1 << 7) | (1 << 3)
    i.irq_ext = i.irq_soft = True
    C.ck("priority: external (11) beats timer (7) beats soft (3)",
         i._irq_pending(), 11)
    i.irq_ext = False
    C.ck("... then timer (7)", i._irq_pending(), 7)
    i.mie = 1 << 3
    C.ck("... then software (3)", i._irq_pending(), 3)
    i.mie = 1 << 11
    i.irq_soft = False
    C.ck("mie without a matching mip source stays silent", i._irq_pending(), None)
    i = new()
    prog(i, [enc_b(0, 0, 0, 0)] * 4)
    i.cycles_per_step = 8
    i.step()
    C.ck("cycles_per_step hook charges 8 cycles per step", i.cycle, 8)
    i = new()
    prog(i, [enc_b(0, 0, 0, 0)] * 4)
    i.set_cycle_model(lambda iss, k, pc, ins: 100 + 10 * k)
    got = []
    for _ in range(3):
        i.step()
        got.append(i.cycle)
    C.ck("set_cycle_model hook drives the cycle counter", got, [100, 210, 330])
    # mtime is free-running in CYCLES and a load samples it at its MEM stage:
    # the first load's MEM is cycle 3 (IF@0 ID@1 EX@2 MEM@3), the second load's
    # MEM is cycle 4 (no load-use hazard: rs1=x1 != rd=x3).
    i = new()
    prog(i, [enc_i(0, 1, 2, 3, OP_LOAD), enc_i(0, 1, 2, 3, OP_LOAD)])
    i.regs[1] = ADDR_TIMER_BASE
    first, second = i.step().wdata, i.step().wdata
    C.ck("mtime counts real cycles; loads sample it at their MEM beat",
         (first, second), (3, 4))
    i = new()
    prog(i, [enc_s(0, 1, 2, 2, OP_STORE),                     # sw x2, 0(x1) MTIME
              enc_i(0, 1, 2, 3, OP_LOAD)])                    # lw x3, 0(x1) MTIME
    i.regs[1], i.regs[2] = ADDR_TIMER_BASE, 100
    must_not_trap(i, 1, "mtime-write")
    C.ck("writing TIMER mtime lands on the edge that ends the store's MEM "
         "beat, so the very next load's MEM beat reads it back unchanged",
         i.mtime, 100)
    C.ck("TIMER mtime is readable as a word", i.step().wdata, 100)
    i = new()
    prog(i, [enc_s(4, 1, 2, 2, OP_STORE),                     # sw x2, 4(x1) MTIMEHI
              enc_i(4, 1, 2, 3, OP_LOAD)])                    # lw x3, 4(x1)
    i.regs[1], i.regs[2] = ADDR_TIMER_BASE, 0xDEAD_BEEF
    must_not_trap(i, 1, "mtimehi-write")
    C.ck("TIMER mtimehi write lands in bits 63:32", i.mtime >> 32, 0xDEAD_BEEF)
    C.ck("TIMER mtimehi read-back", i.step().wdata, 0xDEAD_BEEF)

    # ======================================================= 9. peripherals
    print("\n=== 9. UART / TIMER / SYSC observable behaviour ===")
    i = new()
    prog(i, [enc_s(0, 1, 3, 2, OP_STORE),                     # sw x3, 0(x1) UART
              enc_i(4, 1, 4, 3, OP_LOAD)])                    # lbu x4, 4(x1) STATUS
    i.regs[1], i.regs[3] = ADDR_UART_BASE, ord('H')
    sr, ok = must_not_trap(i, 2, "uart")
    C.ck("UART DATA write appends the low byte to uart_tx",
         (bytes(sr[0].uart_bytes), bytes(i.uart_tx)), (b"H", b"H"))
    C.ck("UART STATUS reads tx-idle / rx-empty = 1", sr[1].wdata, 1)
    C.ck("a UART store shows up in the commit trace",
         (sr[0].mem_addr, sr[0].mem_be), (ADDR_UART_BASE, 0xF))
    i = new()
    prog(i, [enc_s(0, 1, 3, 0, OP_STORE)])                    # sb x3, 0(x1)
    i.regs[1], i.regs[3] = ADDR_UART_BASE, 0x1234_5A66
    i.step()
    C.ck("UART DATA keeps only data[7:0]", bytes(i.uart_tx), b"\x66")
    i = new()
    prog(i, [enc_s(8, 1, 3, 2, OP_STORE),                     # sw x3, 8(x1) CMP
              enc_s(0xC, 1, 3, 2, OP_STORE),                  # sw x3, 12(x1) CMPHI
              enc_s(0xC, 1, 3, 2, OP_STORE),                  # CMPHI = 0
              enc_s(8, 1, 3, 2, OP_STORE)])                    # CMP  = 0
    i.regs[1], i.regs[3] = ADDR_TIMER_BASE, 12345
    must_not_trap(i, 1, "tmcmp")
    C.ck("TIMER mtimecmp low write leaves the reset all-ones high half",
         i.mtimecmp, 0xFFFF_FFFF_0000_0000 | 12345)
    i.regs[3] = 0x12
    must_not_trap(i, 1, "tmcmphi")
    C.ck("TIMER mtimecmp high word writable", i.mtimecmp, 0x12_0000_3039)
    i.regs[3] = 0
    must_not_trap(i, 2, "tmcmp-zero")          # CMP=0 then CMPHI=0 -> fires
    C.ck("mtimecmp = 0 makes mtime >= mtimecmp, so mip bit7 rises",
         (i.mip >> 7) & 1, 1)
    C.ck("... and mtimecmph = 0 clears the top half", i.mtimecmp, 0)
    i = new()
    prog(i, [enc_s(0, 1, 3, 2, OP_STORE)])
    i.regs[1], i.regs[3] = ADDR_SYSC_BASE, 0x5A
    try:
        i.step()
        C.ck("SYSC COMMIT raises SimHalt", "no exception raised", "SimHalt")
    except SimHalt as h:
        C.ck("SYSC COMMIT raises SimHalt(written value)", h.value, 0x5A)
        C.ck("... StepResult.halt carries the same value", h.step.halt, 0x5A)
    i = new()
    C.ck("SYSC SYSID reads the platform signature",
         i._read_word(ADDR_SYSC_BASE + SYSC_OF_SYSID), SYSC_SYSID)
    C.ck("SYSC CTRL is not implemented: reads 0", 
         i._read_word(ADDR_SYSC_BASE + SYSC_OF_CTRL), 0)
    C.ck("UART DIV reads back the reset divisor",
         i._read_word(ADDR_UART_BASE + UART_OF_DIV), 434)
    C.ck("addr_ok (M6): RAM / UART aligned / UART+2 / hole / last word / past",
         [i.addr_ok(a) for a in (RAM, ADDR_UART_BASE, ADDR_UART_BASE + 2, 0x10,
                                 ADDR_RAM_BASE + 0x3FFFC, ADDR_RAM_BASE + 0x40000)],
         [True, True, True, False, True, False],
         "UART+2 落在外设窗口里就算「在地址图上」; 这次访问合不合法由宽度档决定, "
         "不再由 addr_ok 顺手管对齐")
    keep_leg = ADDR_OK_OLDED_ALIGNMENT
    ADDR_OK_OLDED_ALIGNMENT = True
    C.ck("legacy addr_ok rejected UART+2 too (that is what quirk 3 was)",
         i.addr_ok(ADDR_UART_BASE + 2), False)
    ADDR_OK_OLDED_ALIGNMENT = keep_leg
    C.ck("the whole 64-byte peripheral window decodes",
         [i.addr_ok(ADDR_UART_BASE + 0x3C), i.addr_ok(ADDR_UART_BASE + 0x40),
          i.addr_ok(ADDR_TIMER_BASE), i.addr_ok(ADDR_SYSC_BASE + 0x3C)],
         [True, False, True, True])

    # ---- CYCLE MODEL v2: the UART send shifter is clocked, and STATUS polls
    # see the live tx_idle bit at their own MEM beat (this is what makes the
    # busy-wait loops in sw/lib/platform.c wait exactly as long as the RTL).
    i = new()
    i.uart_div = 1                       # crt0.S programs divisor 1 in sim
    prog(i, [enc_s(0, 1, 3, 2, OP_STORE),               # sw 'H' -> UART DATA
              enc_i(4, 1, 4, 4, OP_LOAD)]               # lbu x4, 4(x1) STATUS
              + [enc_i(0, 0, 0, 0, OP_OPIMM)] * 3       # 3 nops
              + [enc_i(4, 1, 4, 4, OP_LOAD)] * 8)       # 8 more STATUS polls
    i.regs[1], i.regs[3] = ADDR_UART_BASE, ord('H')
    srs = [i.step() for _ in range(13)]
    polls = [s.wdata for s in srs if s.rd == 4]
    C.ck("UART shifter: 'H' written at bus cycle 3 loads the shifter on the "
         "next edge; tx_run holds cycles 5..14 (divisor 1, 10 bits)",
         polls, [1] + [0] * 7 + [1],
         "the polls sample STATUS at their MEM beats 4, 8..14 and 15")
    C.ck("... and the byte itself still reached uart_tx",
         bytes(i.uart_tx), b"H")

    # ---- CYCLE MODEL v2: pipeline placement, observed through mcycle reads.
    # Every EX-stage mcycle read returns the absolute cycle its EX is live in,
    # so these are point checks of the placement rules themselves.
    i = new()
    prog(i, [enc_b(4, 0, 0, 0),                         # beq x0,x0,+4: taken
              enc_i(0xB00, 0, 2, 3, OP_SYSTEM)])        # csrr x3, mcycle
    _br = i.step()
    C.ck("a taken branch flushes two slots: the target's EX lands at cycle 5",
         i.step().wdata, 5)
    i = new()
    prog(i, [enc_i(0, 1, 2, 5, OP_LOAD),                # lw x5, 0(x1)
              enc_i(0xB00, 5, 2, 2, OP_SYSTEM),         # csrrs x2, mcycle, x5
              enc_i(0xB00, 0, 2, 3, OP_SYSTEM)])        # csrr x3, mcycle
    i.regs[1] = RAM + 0x100
    poke(i, RAM + 0x100, 0)             # x5 loads 0: csrrs must NOT write
    _a, b, c = i.step(), i.step(), i.step()
    C.ck("load-use hazard costs one ID stall: the dependent CSR reads at 4",
         b.wdata, 4)
    C.ck("... and the pipeline recovers to one EX per cycle (next at 5)",
         c.wdata, 5)
    i = new()
    prog(i, [enc_r(0x01, 2, 1, 6, 5, OP_OP),            # div x5, x1, x2
              enc_i(0xB00, 0, 2, 3, OP_SYSTEM)])        # csrr x3, mcycle
    _dv = i.step()
    C.ck("DIV freezes EX for 33 beats: the next EX samples mcycle at 35",
         i.step().wdata, 35)
    i = new()
    prog(i, [enc_s(0, 1, 3, 2, OP_STORE),               # sw x3, 0(x1)
              enc_r(0, 0, 1, EPV_F3_LVB, 0, OP_CUSTOM0),  # LVB v0, 0(x1)
              enc_i(0xB00, 0, 2, 3, OP_SYSTEM)])        # csrr x3, mcycle
    i.regs[1] = RAM
    _s0, _s1, s2 = i.step(), i.step(), i.step()
    C.ck("an LVB behind a store waits one grant beat: its last EX is 7 and "
         "the following CSR samples mcycle at 8", s2.wdata, 8)
    i = new()
    prog(i, [enc_r(0, 0, 1, EPV_F3_LVB, 0, OP_CUSTOM0),  # LVB v0, 0(x1)
              enc_i(0xB00, 0, 2, 3, OP_SYSTEM)])        # csrr x3, mcycle
    i.regs[1] = RAM
    _l0, l1 = i.step(), i.step()
    C.ck("an LVB with a free bus takes 4 beats: the following CSR reads at 6",
         l1.wdata, 6)

    # ==================================================== 10. EPV decode table
    print("\n=== 10. EPV: all 17 opcodes decode legal, field_bad traps ===")
    for w, nm, want in (
            (epv_c0(EPV_F3_LVB, 1, 15, 0), "EPV.LVB v1, 0(x15) legal", None),
            (epv_c0(EPV_F3_SVB, 0, 15, 0, rs2=2), "EPV.SVB v2, 0(x15) legal", None),
            (epv_c0(EPV_F3_MVX, 7, 1, 0), "EPV.MVX v7, x1 legal", None),
            (epv_c0(EPV_F3_XMV, 5, 3, 0), "EPV.XMV x5, v3 legal (rd < 8)", None),
            (epv_c0(EPV_F3_CFG, 0, 1, 0), "EPV.CFG x1 legal", None),
            (epv_c0(EPV_F3_CFGR, 2, 0, 0), "EPV.CFGR x2 legal", None),
            (epv_c1(EPV_F7_VDOT8, 5, 1, 2), "EPV.VDOT8 x5,v1,v2 legal", None),
            (epv_c1(EPV_F7_VMAC4, 3, 1, 2), "EPV.VMAC4 legal", None),
            (epv_c1(EPV_F7_VADD8, 3, 1, 2), "EPV.VADD8 legal", None),
            (epv_c1(EPV_F7_VREQ4, 3, 1, 2), "EPV.VREQ4 legal", None),
            (epv_c1(EPV_F7_VMAX8, 3, 1, 2), "EPV.VMAX8 legal", None),
            (epv_c1(EPV_F7_VRELU8, 3, 1, 0), "EPV.VRELU8 legal", None),
            (epv_c1(EPV_F7_VSRA4, 3, 1, 0), "EPV.VSRA4 legal", None),
            (epv_c1(EPV_F7_VZERO, 3, 3, 0), "EPV.VZERO legal", None),
            (epv_c1(EPV_F7_VPOOL2, 3, 1, 0), "EPV.VPOOL2 legal", None),
            (epv_c1(EPV_F7_VSUB8, 3, 1, 2), "EPV.VSUB8 legal", None),
            (epv_c1(EPV_F7_VMIN8, 3, 1, 2), "EPV.VMIN8 legal", None),
            # ---- quirk 10: 三条掩码逐指令类不同，两个方向都钉 ----
            (epv_c0(EPV_F3_LVB, 8, 15, 0), "LVB rd=8 legal (rd not a vec field)", None),
            (epv_c0(EPV_F3_SVB, 8, 15, 0), "SVB [11:7]=8 legal (vs3, unchecked)", None),
            (epv_c0(EPV_F3_CFGR, 11, 0, 0), "CFGR x11 legal (the nn_bench encoding)", None),
            (epv_c0(EPV_F3_XMV, 8, 3, 0), "XMV rd=8 legal (rd is a GPR)", None),
            (epv_c0(EPV_F3_CFG, 0, 8, 0), "CFG rs1=8 legal (C0 never checks rs1)", None),
            (epv_c0(EPV_F3_MVX, 1, 1, 0, rs2=8), "MVX rs2=8 legal (rs2 unchecked)", None),
            (epv_c0(EPV_F3_MVX, 8, 1, 0), "MVX vd=8 -> illegal (rd_is_vec)", 2),
            (epv_c1(EPV_F7_VDOT8, 11, 9, 9), "VDOT8 rd/vs1/vs2 all >=8 legal", None),
            (epv_c1(EPV_F7_VZERO, 3, 8, 8), "VZERO vs1/vs2 >=8 legal", None),
            (epv_c1(EPV_F7_VRELU8, 3, 1, 8), "VRELU8 vs2=8 legal (single-source)", None),
            (epv_c1(EPV_F7_VPOOL2, 3, 1, 8), "VPOOL2 vs2=8 legal (single-source)", None),
            (epv_c1(EPV_F7_VSRA4, 3, 8, 0), "VSRA4 vs1=8 -> illegal (rs1_is_vec)", 2),
            (epv_c1(EPV_F7_VREQ4, 1, 1, 8), "VREQ4 vs2=8 -> illegal", 2),
            (epv_c1(EPV_F7_VADD8, 1, 1, 8), "C1 vs2=8 -> illegal", 2),
            (epv_c1(EPV_F7_VADD8, 1, 8, 1), "C1 vs1=8 -> illegal (rs1 checked)", 2),
            (epv_c1(EPV_F7_VADD8, 8, 1, 1), "C1 vd=8 -> illegal", 2),
            (epv_c1(EPV_F7_VADD8, 1, 1, 1, f3=1), "C1 funct3!=0 -> illegal", 2),
            (epv_c1(0, 1, 1, 1), "C1 funct7=0 undefined -> illegal", 2),
            (epv_c1(12, 1, 1, 1), "C1 funct7=12 undefined -> illegal", 2),
            (epv_c0(6, 1, 1, 0), "C0 funct3=6 undefined -> illegal", 2),
            (epv_c0(7, 1, 1, 0), "C0 funct3=7 undefined -> illegal", 2)):
        i = new()
        prog(i, [w])
        i.regs[15] = RAM + 0x2000
        sr = i.step()
        C.ck(nm, sr.trap.cause if sr.trap else None, want,
             "mtval must be the instruction word: 0x%08x" % sr.instr)
    i = new()
    prog(i, [epv_c1(EPV_F7_VADD8, 1, 1, 2)])
    i.enable_epv = False
    C.ck("EPV_ENABLE=0: CUSTOM1 becomes an illegal instruction",
         i.step().trap.cause, EXC_ILLEGAL)
    i = new()
    prog(i, [epv_c1(EPV_F7_VADD8, 1, 1, 2)])
    i.regs[1] = 0x0BAD_F00D
    C.ck("an EPV instruction never writes a GPR unless it is VDOT8/XMV/CFGR",
         i.step().rd, None)

    # ==================================== 11. EPV LVB + VMAC4 + VDOT8 (the brief)
    print("\n=== 11. EPV.LVB + EPV.VMAC4 + EPV.VDOT8 on a hand-built int8 vector ===")
    b = RAM + 0x2000
    i = new()
    # vs1 = 1,2,3,4 | -1,-2,-3,-4 | 10,10,10,10 | -128,127,-128,127
    poke(i, b + 0x00, words4(bytes16(1, 2, 3, 4)))
    poke(i, b + 0x04, words4(bytes16(-1, -2, -3, -4)))
    poke(i, b + 0x08, words4(bytes16(10, 10, 10, 10)))
    poke(i, b + 0x0C, words4(bytes16(-128, 127, -128, 127)))
    # vs2 = 1,1,1,1 | 1,1,1,1 | 2,3,4,5 | 2,2,-2,-2 -- lives at b+0x20 and is
    # reached through a second base register: the R-type LVB has no immediate
    # (quirk 17), the offset lives in the base register like epv_lvb does.
    poke(i, b + 0x20, words4(bytes16(1, 1, 1, 1)))
    poke(i, b + 0x24, words4(bytes16(1, 1, 1, 1)))
    poke(i, b + 0x28, words4(bytes16(2, 3, 4, 5)))
    poke(i, b + 0x2C, words4(bytes16(2, 2, -2, -2)))
    prog(i, [epv_c0(EPV_F3_LVB, 0, 9, 0x000, rs2=1),
              epv_c0(EPV_F3_LVB, 0, 10, 0x000, rs2=2),
              epv_c1(EPV_F7_VZERO, 3, 3, 0),
              epv_c1(EPV_F7_VMAC4, 3, 1, 2),
              epv_c1(EPV_F7_VMAC4, 3, 1, 2),
              epv_c1(EPV_F7_VDOT8, 5, 1, 2)])
    i.regs[9] = b
    i.regs[10] = b + 0x20
    srs, ok = must_not_trap(i, 3, "epv load+zero")
    C.ck("EPV.VZERO cleared v3 before the MACs", i.vregs[3], 0)
    srs2, ok2 = must_not_trap(i, 3, "epv mac+dot")
    srs = srs + srs2
    C.ck("the 6-instruction EPV program retires all 6", i.n_instr, 6)
    C.ck("EPV.LVB lanes are words at base+4k (little-endian)",
         lanes_w(i.vregs[1]), [0x0403_0201, 0xFCFD_FEFF, 0x0A0A_0A0A, 0x7F80_7F80],
         "byte i = bits [8i+7:8i]: 1,2,3,4 -> 0x04030201 by hand")
    C.ck("EPV.LVB v2 comes from the second base register (the imm field is "
         "dead on the R-type encoding)",
         lanes_w(i.vregs[2]), [0x0101_0101, 0x0101_0101, 0x0504_0302, 0xFEFE_0202])
    # The destination vector is the rs2 field (quirk 17): 0..7 legal, anything
    # with rs2[4:3] != 0 is field_bad -- and rd is not consulted at all.
    for vs2, want, nm in ((0, None, "v0"), (7, None, "v7"),
                          (8, EXC_ILLEGAL, "8"), (24, EXC_ILLEGAL, "24")):
        z = new()
        poke(z, b, words4(0x1111_1111, 0x2222_2222, 0x3333_3333, 0x4444_4444))
        prog(z, [epv_c0(EPV_F3_LVB, 0, 9, 0x000, rs2=vs2)])
        z.regs[9] = b
        srz = z.step()
        C.ck("EPV.LVB destination vector %s is %s"
             % (nm, "legal" if want is None else "illegal"),
             srz.trap.cause if srz.trap else None, want,
             "field_bad checks inst[24:20] = the destination vector number")
    z = new()
    poke(z, b, 0x1111_1111)
    poke(z, b + 4, 0x2222_2222)
    poke(z, b + 8, 0x3333_3333)
    poke(z, b + 12, 0x4444_4444)
    prog(z, [epv_c0(EPV_F3_LVB, 5, 9, 0x000, rs2=3)])
    z.regs[9] = b
    srz = z.step()
    C.ck("EPV.LVB writes the rs2-field vector and ignores rd entirely",
         (z.vregs[3], srz.rd),
         (words4(0x1111_1111, 0x2222_2222, 0x3333_3333, 0x4444_4444), None),
         "rd=5 is x5's number but LVB writes no GPR and no v5")
    C.ck("EPV.LVB byte lanes decode as the hand int8 vector",
         lanes_b(i.vregs[1]),
         [1, 2, 3, 4, 0xFF, 0xFE, 0xFD, 0xFC, 10, 10, 10, 10, 0x80, 0x7F, 0x80, 0x7F])
    C.ck("StepResult lists the vector register written", srs[1].vregs_written, [2])
    # 4-lane dots by hand: 10, -10, 140, 0 ; accumulated twice
    C.ck("EPV.VMAC4 per-int32-lane accumulate, run twice",
         lanes_w(i.vregs[3]), [20, u32(-20), 280, 0],
         "lane j = vd.w[j] + sum of bytes 4j..4j+3 products; "
         "expected [20,-20,280,0] got %s" % _fmt(lanes_w(i.vregs[3])))
    C.ck("EPV.VDOT8 16-lane int8 dot product -> GPR", i.regs[5], 140,
         "10 + (-10) + 140 + 0 = 140; VDOT8 writes rd=x5")
    C.ck("EPV.VDOT8 commits a register write and reports it",
         (srs[5].rd, srs[5].wdata), (5, 140))
    # VMAC4 into a seeded destination, and lane order check
    i = new()
    i.vregs[1] = bytes16(*([1] * 16))
    i.vregs[2] = bytes16(*([2] * 16))
    i.vregs[4] = words4(1000, u32(-1), 0, 7)
    prog(i, [epv_c1(EPV_F7_VMAC4, 4, 1, 2)])
    i.step()
    C.ck("EPV.VMAC4 accumulates on top of vd and uses lanes 4j..4j+3",
         lanes_w(i.vregs[4]), [1008, 7, 8, 15],
         "every lane gains 4 * (1*2) = 8")
    # int8 product extremes: -128 * -128 = 16384 per lane
    i = new()
    i.vregs[1] = bytes16(*([-128] * 16))
    i.vregs[2] = bytes16(*([-128] * 16))
    prog(i, [epv_c1(EPV_F7_VDOT8, 6, 1, 2), epv_c1(EPV_F7_VMAC4, 3, 1, 2)])
    i.step()
    C.ck("VDOT8 with 16 lanes of -128 * -128 = 16*16384", i.regs[6], 16 * 16384)
    i.step()
    C.ck("VMAC4 lanes of -128*-128 = 4*16384 each", lanes_w(i.vregs[3]),
         [65536] * 4)

    # ==================================================== 12. EPV.SVB round trip
    print("\n=== 12. EPV.SVB / LVB round trip through the memory map ===")
    i = new()
    b = RAM + 0x2100
    v = words4(0x1122_3344, 0x5566_7788, 0x99AA_BBCC, 0xDDEE_FF00)
    i.vregs[0] = v
    # real-intrinsic shape: vector number in the rs2 field, rd = x0 (quirk 17)
    prog(i, [epv_c0(EPV_F3_SVB, 0, 9, 0x000, rs2=0),
              epv_c0(EPV_F3_LVB, 0, 9, 0x000, rs2=7),
              epv_c0(EPV_F3_XMV, 5, 0, 0, rs2=7)])
    i.regs[9] = b
    srs, ok = must_not_trap(i, 3, "svb")
    C.ck("EPV.SVB stores 4 words at base+4k, lane k -> word k",
         [i.ram[widx(b) + k] for k in range(4)],
         [0x1122_3344, 0x5566_7788, 0x99AA_BBCC, 0xDDEE_FF00],
         "the hardware strides base + wc*4 (epv32_epv.v o_mem_addr); "
         "EPV_MEM_ADDR_STRIDE_X4=False reproduces the pre-fix one-word walk")
    C.ck("EPV.SVB retires with NO store record (o_tr_memwr=0, quirk 17): "
         "the S projection sees none of the four bus beats",
         (srs[0].stores, srs[0].mem_addr), ([], None),
         "the four beats are real on the bus and land in ram[] above; the "
         "retirement trace -- which the golden comparison reads -- does not "
         "observe them")
    C.ck("EPV.LVB <- EPV.SVB round-trips the vector", i.vregs[7], v)
    C.ck("EPV.XMV takes lane 0 of the rs2-field vector into a GPR (quirk 11)",
         i.regs[5], 0x1122_3344)
    # The SVB source vector is the rs2 field, and the address does NOT move
    # with it (the R-type migration killed the imm/vs2 collision, quirk 17):
    z = new()
    z.vregs[6] = v
    prog(z, [epv_c0(EPV_F3_SVB, 0, 9, 0x000, rs2=6)])
    z.regs[9] = b
    srz = z.step()
    C.ck("EPV.SVB vs2=6 transfers v6's content at exactly base (no imm shift)",
         (srz.stores, [z.ram[widx(b) + k] for k in range(4)]),
         ([], [0x1122_3344, 0x5566_7788, 0x99AA_BBCC, 0xDDEE_FF00]),
         "inst[24:20] is the SOURCE vector number only; the I-type era that "
         "folded it into the address is gone")
    # SVB to the UART: four bus writes, four TX bytes
    i = new()
    prog(i, [epv_c0(EPV_F3_SVB, 0, 9, 0, rs2=0)])
    i.vregs[0] = words4(ord('H'), ord('I'), ord('!'), ord('.'))
    i.regs[9] = ADDR_UART_BASE
    sr, ok = must_not_trap(i, 1, "svb-uart")
    C.ck("EPV.SVB into the UART window: 4 bus beats, only off==0 is DATA",
         (bytes(i.uart_tx), sr[0].stores), (b"H", []),
         "beat 1 lands on STATUS (write ignored), beat 2 on DIV, beat 3 on a "
         "void offset: only lane 0 reaches the transmitter; none of the beats "
         "appear as store records")

    # ====================================================== 13. MVX / CFG / CFGR
    print("\n=== 13. EPV.MVX / CFG / CFGR / vcfg field rules ===")
    i = new()
    prog(i, [epv_c0(EPV_F3_MVX, 5, 1, 0),
              epv_c0(EPV_F3_CFG, 0, 1, 0),
              epv_c0(EPV_F3_CFGR, 2, 0, 0)])
    i.regs[1] = 0xF00D_F00D
    must_not_trap(i, 1, "mvx")
    C.ck("EPV.MVX broadcasts the GPR into all 4 int32 lanes", i.vregs[5],
         words4(0xF00D_F00D, 0xF00D_F00D, 0xF00D_F00D, 0xF00D_F00D))
    must_not_trap(i, 1, "cfg")
    C.ck("EPV.CFG writes vcfg = x1 & 0x0003FFFF", i.vcfg, 0x0001_F00D,
         "0xF00DF00D & 0x3FFFF = 0x1F00D; epv32_epv.v:274 masks to 32'h0003_FFFF")
    must_not_trap(i, 1, "cfgr")
    C.ck("EPV.CFGR reads vcfg back into a GPR, and 0x7C0 mirrors it",
         (i.regs[2], i._csr_read(CSR_VCFG)), (0x0001_F00D, 0x0001_F00D))
    i = new()
    prog(i, [epv_c0(EPV_F3_CFG, 0, 1, 0)])
    i.regs[1] = 0xFFFF_FFFF
    i.step()
    C.ck("EPV.CFG keeps vcfg[17:0], bits 31:18 read 0", i.vcfg, 0x0003_FFFF)
    i = new()
    prog(i, [epv_c0(EPV_F3_CFG, 0, 1, 0)])
    i.regs[1] = 1 << 20
    i.step()
    C.ck("a reserved vcfg bit write is ignored (no SAT/RND/S side effect)",
         (i.vcfg, (i.vcfg >> 17) & 1), (0, 0))
    # S > 31 saturates to 31 -- checked through VSRA4 below

    # ============================================ 14. EPV.VREQ4 (brief item)
    print("\n=== 14. EPV.VREQ4 with a known M / S / RND / RB combination ===")
    b = RAM + 0x2200
    VCFG_REQ = 2 | (3 << 8) | (1 << 16) | (1 << 17)          # S=2 RB=3 RND=1 SAT=1
    C.ck("the hand-built vcfg word", VCFG_REQ, 0x0003_0302)
    i = new()
    poke(i, b + 0x00, 1)                       # acc lane 0 =  1
    poke(i, b + 0x04, 0xFFFF_FFFF)             # acc lane 1 = -1
    poke(i, b + 0x08, 0x7FFF_FFFF)             # acc lane 2 = INT_MAX
    poke(i, b + 0x0C, 0x8000_0000)             # acc lane 3 = INT_MIN
    prog(i, [epv_c0(EPV_F3_LVB, 0, 9, 0, rs2=1),
              epv_c0(EPV_F3_CFG, 0, 8, 0),
              epv_c0(EPV_F3_MVX, 2, 10, 0),
              epv_c1(EPV_F7_VREQ4, 3, 1, 2)])
    i.regs[8], i.regs[9], i.regs[10] = VCFG_REQ, b, 1 << 30
    must_not_trap(i, 4, "vreq4")
    C.ck("EPV.VREQ4 M=2^30 S=2 RND=1 RB=+3 lanes", i.vregs[3] & 0xFF_FFFF_FFFF,
         bytes16(3, 3, 127, -128),
         "t=(acc*2^30+2^30)>>>31 ; r=(t+2)>>>2 ; o=sat8(r+3) "
         "-> [3, 3, 127, -128]")
    C.ck("EPV.VREQ4 zeroes bits [127:32] (epv32_epv.v:213)", i.vregs[3] >> 32, 0)
    C.ck("EPV.VREQ4 result is 4 int8 lanes: word = 0x807F0303", i.vregs[3],
         0x807F_0303)

    def req4_lane0(vcfg_val, acc, m):
        z = new()
        bb = RAM + 0x2200
        poke(z, bb, words4(acc, 0, 0, 0))
        prog(z, [epv_c0(EPV_F3_LVB, 0, 9, 0, rs2=1),
                 epv_c0(EPV_F3_CFG, 0, 8, 0),
                 epv_c0(EPV_F3_MVX, 2, 10, 0),
                 epv_c1(EPV_F7_VREQ4, 3, 1, 2)])
        z.regs[8], z.regs[9], z.regs[10] = vcfg_val, bb, m
        must_not_trap(z, 4, "req4")
        return z.vregs[3]

    # acc=25, M=2^29: hi = (25*2^29 + 2^30) >>> 31 = 14495514624 >>> 31 = 6
    C.ck("VREQ4 RND=0 truncates: hi=6, S=2 -> 6>>>2 = 1",
         req4_lane0(2, 25, 1 << 29) & 0xFF, 1)
    C.ck("VREQ4 RND=1 rounds: (6+2)>>>2 = 2",
         req4_lane0(2 | (1 << 16), 25, 1 << 29) & 0xFF, 2)
    C.ck("VREQ4 RB=+5 is added after the shift (1 + 5 = 6)",
         req4_lane0(2 | (5 << 8), 25, 1 << 29) & 0xFF, 6)
    C.ck("VREQ4 RB=+127 (int8 max) can push a lane past +127 -> clamps: "
         "acc=25, M=2^29 gives sh=6, 6+127 = 133 -> 127",
         req4_lane0(127 << 8, 25, 1 << 29) & 0xFF, 127,
         "RB is an int8 field: 200 reads as -56 signed, not +200")
    C.ck("VREQ4 RB is SIGN-EXTENDED: RB=0xFF (-1 intended) gives -1 (0xFF); "
         "the zero-extension concat was ledger defect 13 and is fixed",
         req4_lane0((0xFF << 8), 0, 0) & 0xFF, 0xFF,
         "a zero-extended RB would add +255 and saturate the lane to 127")
    C.ck("VREQ4 negative accumulator saturates to -128",
         req4_lane0(0, u32(-2 ** 30), 1 << 30) & 0xFF, 0x80)
    C.ck("VREQ4 S=31 comes from a saturated vcfg.S field (raw 40)",
         req4_lane0(40, 1 << 30, 1 << 30) & 0xFF, 0)

    # =================================================== 15. EPV.VSRA4
    print("\n=== 15. EPV.VSRA4 arithmetic shift + rounding ===")
    b = RAM + 0x2300
    i = new()
    poke(i, b + 0, 8)
    poke(i, b + 4, 0xFFFF_FFF8)               # -8
    poke(i, b + 8, 0x7FFF_FFFF)               # INT_MAX
    poke(i, b + 12, 1)
    prog(i, [epv_c0(EPV_F3_LVB, 0, 9, 0, rs2=1),
              epv_c0(EPV_F3_CFG, 0, 8, 0),
              epv_c1(EPV_F7_VSRA4, 3, 1, 0)])
    i.regs[8], i.regs[9] = 4 | (1 << 16), b              # S=4 RND=1
    must_not_trap(i, 3, "vsra4")
    C.ck("EPV.VSRA4 S=4 RND=1 (bias=8): 8,-8,INT_MAX,1", lanes_w(i.vregs[3]),
         [1, 0, 134217728, 0],
         "lane 2 needs the 33-bit intermediate: (2147483647+8)>>4 = 134217728")
    i = new()
    poke(i, b + 0, 8)
    poke(i, b + 4, 0xFFFF_FFF8)
    poke(i, b + 8, 0x7FFF_FFFF)
    poke(i, b + 12, 1)
    prog(i, [epv_c0(EPV_F3_LVB, 0, 9, 0, rs2=1),
             epv_c0(EPV_F3_CFG, 0, 8, 0),
             epv_c1(EPV_F7_VSRA4, 3, 1, 0)])
    i.regs[8], i.regs[9] = 4, b                          # S=4, RND=0
    must_not_trap(i, 3, "vsra4-rnd0")
    C.ck("EPV.VSRA4 S=4 RND=0 truncates toward -inf", lanes_w(i.vregs[3]),
         [0, u32(-1), 134217727, 0],
         "-8>>>4 = -1 (floor), which a C-style >> on a positive would not give")
    i = new()
    poke(i, b + 0, 0xFFFF_FFFF)
    poke(i, b + 4, 1 << 30)
    poke(i, b + 8, 0x8000_0000)
    poke(i, b + 12, 0x7FFF_FFFF)
    prog(i, [epv_c0(EPV_F3_LVB, 0, 9, 0, rs2=1),
             epv_c0(EPV_F3_CFG, 0, 8, 0),
             epv_c1(EPV_F7_VSRA4, 3, 1, 0)])
    i.regs[8], i.regs[9] = 40, b                          # raw S=40 -> saturates 31
    must_not_trap(i, 3, "vsra4-s31")
    C.ck("EPV.VSRA4 with vcfg.S = 40 uses S=31 (epv32_epv.v:102-103)",
         lanes_w(i.vregs[3]), [u32(-1), 0, u32(-1), 0])
    C.ck("EPV.VSRA4 leaves vcfg untouched", i.vcfg, 40)

    # =================================================== 16. EPV.VADD8/VSUB8
    print("\n=== 16. EPV.VADD8 / VSUB8 with SAT on and off ===")
    b = RAM + 0x2400
    i = new()
    poke(i, b + 0x00, words4(bytes16(-128, 100, -5, 0)))
    poke(i, b + 0x04, words4(bytes16(127, -1, 0, 0)))
    poke(i, b + 0x20, words4(bytes16(-1, 50, -5, 0)))
    poke(i, b + 0x24, words4(bytes16(1, -1, 0, 0)))
    prog(i, [epv_c0(EPV_F3_LVB, 0, 9, 0, rs2=1),
              epv_c0(EPV_F3_LVB, 0, 10, 0, rs2=2),
              epv_c0(EPV_F3_CFG, 0, 8, 0),
              epv_c1(EPV_F7_VADD8, 3, 1, 2)])
    i.regs[8], i.regs[9] = 1 << 17, b                     # SAT=1
    i.regs[10] = b + 0x20
    must_not_trap(i, 4, "vadd8-sat")
    C.ck("VADD8 SAT=1: -128+-1 saturates to -128, 100+50 and 127+1 to +127",
         lanes_b(i.vregs[3])[:6], [0x80, 0x7F, 0xF6, 0x00, 0x7F, 0xFE],
         "word0 = 0x00F67F80, word1 = 0x0000FE7F")
    C.ck("VADD8 SAT=1 full vector", lanes_w(i.vregs[3]), [0x00F6_7F80, 0x0000_FE7F, 0, 0])
    i = new()
    poke(i, b + 0x00, words4(bytes16(-128, 100, -5, 0)))
    poke(i, b + 0x04, words4(bytes16(127, -1, 0, 0)))
    poke(i, b + 0x20, words4(bytes16(-1, 50, -5, 0)))
    poke(i, b + 0x24, words4(bytes16(1, -1, 0, 0)))
    prog(i, [epv_c0(EPV_F3_LVB, 0, 9, 0, rs2=1),
              epv_c0(EPV_F3_LVB, 0, 10, 0, rs2=2),
              epv_c0(EPV_F3_CFG, 0, 8, 0),
              epv_c1(EPV_F7_VADD8, 3, 1, 2)])
    i.regs[8], i.regs[9] = 0, b                           # SAT=0
    i.regs[10] = b + 0x20
    must_not_trap(i, 4, "vadd8-wrap")
    C.ck("VADD8 SAT=0 wraps mod 256 (-129 -> 0x7F, 150 -> 0x96, 128 -> 0x80)",
         lanes_w(i.vregs[3]), [0x00F6_967F, 0x0000_FE80, 0, 0])
    i = new()
    poke(i, b + 0x00, words4(bytes16(-128, 100, -5, 0)))
    poke(i, b + 0x04, words4(bytes16(127, -1, 0, 0)))
    poke(i, b + 0x20, words4(bytes16(-1, 50, -5, 0)))
    poke(i, b + 0x24, words4(bytes16(1, -1, 0, 0)))
    prog(i, [epv_c0(EPV_F3_LVB, 0, 9, 0, rs2=1),
              epv_c0(EPV_F3_LVB, 0, 10, 0, rs2=2),
              epv_c0(EPV_F3_CFG, 0, 8, 0),
              epv_c1(EPV_F7_VSUB8, 3, 1, 2)])
    i.regs[8], i.regs[9] = 1 << 17, b
    i.regs[10] = b + 0x20
    must_not_trap(i, 4, "vsub8")
    C.ck("VSUB8 SAT=1: -128-(-1), 100-50, -5-(-5), 0-0",
         lanes_b(i.vregs[3])[:4], [0x81, 0x32, 0x00, 0x00],
         "lane0 = -127 = 0x81, lane1 = 50 = 0x32; and lane4 127-1 = 126 = 0x7E")
    C.ck("VSUB8 SAT=1 lane4/5: 127-1 = 126, -1-(-1) = 0",
         lanes_b(i.vregs[3])[4:6], [0x7E, 0x00])
    i = new()
    poke(i, RAM + 0x2400, words4(bytes16(-128, 0, 0, 0)))
    poke(i, RAM + 0x2420, words4(bytes16(-1, 0, 0, 0)))
    prog(i, [epv_c0(EPV_F3_LVB, 0, 9, 0, rs2=1),
             epv_c0(EPV_F3_LVB, 0, 10, 0, rs2=2),
             epv_c0(EPV_F3_CFG, 0, 8, 0),
             epv_c1(EPV_F7_VSUB8, 3, 1, 2)])
    i.regs[8], i.regs[9] = 0, b
    i.regs[10] = b + 0x20
    must_not_trap(i, 4, "vsub8-edge")
    C.ck("VSUB8 SAT=0 edge: -128 - (-1) = -127 -> 0x81", i.vregs[3] & 0xFF, 0x81)
    # the saturation rail check: 127 - (-1) = 128
    i = new()
    poke(i, RAM + 0x2400, words4(bytes16(127, -128, 0, 0)))
    poke(i, RAM + 0x2420, words4(bytes16(-1, 127, 0, 0)))
    prog(i, [epv_c0(EPV_F3_LVB, 0, 9, 0, rs2=1),
             epv_c0(EPV_F3_LVB, 0, 10, 0, rs2=2),
             epv_c0(EPV_F3_CFG, 0, 8, 0),
             epv_c1(EPV_F7_VSUB8, 3, 1, 2)])
    i.regs[8], i.regs[9] = 1 << 17, b
    i.regs[10] = b + 0x20
    must_not_trap(i, 4, "vsub8-sat")
    C.ck("VSUB8 SAT=1 rails: 127-(-1) -> 127 and -128-127 -> -128",
         lanes_b(i.vregs[3])[:2], [0x7F, 0x80])
    i = new()
    prog(i, [epv_c0(EPV_F3_CFG, 0, 8, 0),
             epv_c1(EPV_F7_VSUB8, 1, 1, 1)])
    i.regs[8] = 1 << 17
    i.regs[1] = 0
    i.vregs[1] = bytes16(*([0] * 16))
    must_not_trap(i, 1, "cfg-only")
    must_not_trap(i, 1, "vsub8-zero")
    C.ck("VSUB8 of all-zero lanes is all-zero", i.vregs[1], 0)

    # ================================================ 17. EPV lane-wise min/max
    print("\n=== 17. EPV.VMAX8 / VMIN8 / VRELU8 ===")
    b = RAM + 0x2500
    i = new()
    poke(i, b + 0x00, words4(bytes16(-5, 5, 127, -128)))
    poke(i, b + 0x04, words4(bytes16(-1, 1, 0, 0)))
    poke(i, b + 0x20, words4(bytes16(3, -3, -128, 127)))
    poke(i, b + 0x24, words4(bytes16(2, -2, 0, 0)))
    prog(i, [epv_c0(EPV_F3_LVB, 0, 9, 0, rs2=1),
              epv_c0(EPV_F3_LVB, 0, 10, 0, rs2=2),
              epv_c1(EPV_F7_VMAX8, 3, 1, 2),
              epv_c1(EPV_F7_VMIN8, 4, 1, 2),
              epv_c1(EPV_F7_VRELU8, 5, 1, 0)])
    i.regs[9] = b
    i.regs[10] = b + 0x20
    must_not_trap(i, 5, "minmax")
    C.ck("EPV.VMAX8 signed per-lane max", lanes_b(i.vregs[3])[:8],
         [3, 5, 127, 127, 2, 1, 0, 0])
    C.ck("EPV.VMIN8 signed per-lane min", lanes_b(i.vregs[4])[:8],
         [u32(-5) & 0xFF, u32(-3) & 0xFF, 0x80, 0x80, u32(-1) & 0xFF,
          u32(-2) & 0xFF, 0, 0])
    C.ck("EPV.VRELU8 clamps negatives to 0, keeps positives",
         lanes_b(i.vregs[5])[:8], [0, 5, 127, 0, 0, 1, 0, 0])

    # =================================================== 18. EPV.VPOOL2 (brief)
    print("\n=== 18. EPV.VPOOL2 2x1 max-pool ===")
    b = RAM + 0x2600
    i = new()
    poke(i, b + 0x00, words4(bytes16(1, 2, 3, 4)))
    poke(i, b + 0x04, words4(bytes16(5, -5, -7, -7)))
    poke(i, b + 0x08, words4(bytes16(127, -128, 0, 0)))
    poke(i, b + 0x0C, words4(bytes16(0, 0, -1, -2)))
    prog(i, [epv_c0(EPV_F3_LVB, 0, 9, 0, rs2=1),
              epv_c1(EPV_F7_VPOOL2, 3, 1, 0)])
    i.regs[9] = b
    must_not_trap(i, 2, "vpool2")
    C.ck("EPV.VPOOL2 pairwise signed max into the LOW 8 lanes", lanes_b(i.vregs[3]),
         [2, 4, 5, 0xF9, 0x7F, 0, 0, 0xFF, 0, 0, 0, 0, 0, 0, 0, 0],
         "byte g = max(lane 2g, lane 2g+1): (1,2)->2 (3,4)->4 (5,-5)->5 "
         "(-7,-7)->-7 (127,-128)->127 (0,0)->0 (0,0)->0 (-1,-2)->-1; "
         "lanes 8..15 forced to 0 (epv32_epv.v:149)")
    C.ck("EPV.VPOOL2 words: 0xF9050402 / 0xFF00007F / 0 / 0",
         lanes_w(i.vregs[3]), [0xF905_0402, 0xFF00_007F, 0, 0])
    i = new()
    poke(i, b + 0x00, words4(bytes16(-1, -2, 0, 0)))
    prog(i, [epv_c0(EPV_F3_LVB, 0, 9, 0, rs2=1), epv_c1(EPV_F7_VPOOL2, 2, 1, 0)])
    i.regs[9] = b
    must_not_trap(i, 2, "vpool2-neg")
    C.ck("EPV.VPOOL2 of (-1,-2) picks -1 (0xFF), of (0,0) picks 0",
         lanes_b(i.vregs[2])[:3], [0xFF, 0, 0])
    i = new()
    prog(i, [epv_c1(EPV_F7_VZERO, 4, 4, 0), epv_c1(EPV_F7_VPOOL2, 5, 4, 0)])
    must_not_trap(i, 2, "vpool2-zero")
    C.ck("EPV.VZERO then VPOOL2 gives zero", (i.vregs[4], i.vregs[5]), (0, 0))

    # ================================================== 19. retire-trace shape
    print("\n=== 19. commit-trace shape and machine-state accessors ===")
    i = new()
    prog(i, [enc_i(5, 0, 0, 6, OP_OPIMM),                      # addi x6,x0,5
              enc_r(0, 6, 6, 6, 7, OP_OP),                     # or  x7,x6,x6
              enc_u(0x80000, 1, OP_LUI),                       # lui x1, 0x80000
              enc_s(0x100, 1, 7, 2, OP_STORE),                 # sw x7, 0x100(x1)
              enc_i(0x100, 1, 2, 8, OP_LOAD)])                 # lw x8, 0x100(x1)
    trace = []
    for _ in range(5):
        sr = i.step()
        if sr.rd is not None:
            trace.append((sr.pc, sr.rd, sr.wdata))
        for a, w, be in sr.stores:
            trace.append((a, w, be))
    C.ck("the trace built from step() has one entry per write, in order", trace,
         [(RAM, 6, 5), (RAM + 4, 7, 5), (RAM + 8, 1, 0x8000_0000),
          (RAM + 0x100, 5, 0xF), (RAM + 0x10, 8, 5)],
         "reg writes are (pc, rd, wdata), stores are (addr, wdata, be)")
    C.ck("machine-state accessors look as documented",
         (len(i.regs), len(i.vregs), len(i.ram)), (32, 8, len(i.ram)))
    C.ck("x0 stays 0 no matter what", (i._set_x(0, 0xFFFF), i.regs[0])[1], 0)
    C.ck("regs are plain 32-bit ints", all(0 <= v <= MASK32 for v in i.regs), True)
    C.ck("vregs are plain 128-bit ints", all(0 <= v < (1 << 128) for v in i.vregs),
         True)
    C.ck("vcfg is exposed on the ISS", i.vcfg, 0)
    C.ck("ram keys are word indices and every value fits 32 bits",
         all(isinstance(k, int) and 0 <= v <= MASK32 for k, v in i.ram.items()),
         True)
    C.ck("ram[] is addressable by word index (addr >> 2 masked to RAM)",
         i.ram[widx(RAM + 0x100)], 5)

    # ============================================================== 20. run()
    print("\n=== 20. run(): tohost, SYSC commit, max_instr ===")
    HOST = RAM + 0x3000
    i = new()
    prog(i, [enc_u(0x80003, 10, OP_LUI),                       # x10 = 0x80003000
              enc_i(1, 0, 0, 11, OP_OPIMM),                    # x11 = 1
              enc_s(0, 10, 11, 2, OP_STORE),                   # tohost = 1
              enc_b(0, 0, 0, 0)])                              # spin
    r = i.run(1000, HOST)
    C.ck("riscv-tests tohost = 1 -> PASS", (r.passed, r.testnum, r.halt_reason),
         (True, 0, "tohost"))
    C.ck("... three instructions retired before the poll", r.n_instr, 3)
    i = new()
    prog(i, [enc_u(0x80003, 10, OP_LUI),
              enc_i((3 << 1) | 1, 0, 0, 11, OP_OPIMM),         # x11 = 7
              enc_s(0, 10, 11, 2, OP_STORE),
              enc_b(0, 0, 0, 0)])
    r = i.run(1000, HOST)
    C.ck("tohost = (testnum<<1)|1 -> FAIL with testnum 3",
         (r.passed, r.testnum, r.halt_reason), (False, 3, "tohost"))
    i = new()
    prog(i, [enc_u(0x80003, 10, OP_LUI),
              enc_i(0, 0, 0, 11, OP_OPIMM),
              enc_s(0, 10, 11, 2, OP_STORE),                   # tohost = 0
              enc_b(0, 0, 0, 0)])
    r = i.run(50, HOST)
    C.ck("tohost = 0 keeps running until max_instr",
         (r.passed, r.testnum, r.halt_reason, r.n_instr),
         (False, None, "max_instr", 50))
    i = new()
    prog(i, [enc_u(0x90020, 10, OP_LUI),                       # x10 = SYSC base
              enc_i(1, 0, 0, 11, OP_OPIMM),                    # x11 = 1 (PASS)
              enc_s(0, 10, 11, 2, OP_STORE)])                   # sw -> COMMIT
    r = i.run(100, 0)
    C.ck("a write to SYSC COMMIT ends the run with reason=commit",
         (r.passed, r.commit_value, r.halt_reason), (True, 1, "commit"))
    C.ck("... and the committing store itself retired (lui+addi+sw)",
         r.n_instr, 3)
    i = new()
    prog(i, [enc_u(0x90000, 1, OP_LUI),                        # x1 = UART base
              enc_i(ord('H'), 0, 0, 2, OP_OPIMM),              # x2 = 'H'
              enc_s(0, 1, 2, 2, OP_STORE),                      # sw -> UART DATA
              enc_u(0x90020, 3, OP_LUI),                        # x3 = SYSC base
              enc_i(1, 0, 0, 4, OP_OPIMM),
              enc_s(0, 3, 4, 2, OP_STORE)], start=RAM)           # sw -> COMMIT
    r = i.run(20, 0)
    C.ck("run() reports the UART text", (r.uart_text, bytes(i.uart_tx),
                                         r.halt_reason),
         ("H", b"H", "commit"))
    i = new()
    prog(i, [0xFFFF_FFFF])                                     # always illegal
    i.mtvec = RAM                                              # handler = the trap
    r = i.run(64, 0)
    C.ck("an endless trap loop is bounded and reports the trap",
         (r.passed, r.halt_reason, r.n_instr, r.trap.cause),
         (False, "max_steps", 0, EXC_ILLEGAL),
         "nothing retires while every instruction traps, so the step bound "
         "is what stops run()")
    C.ck("... step() was still called (and cycles still charged) 320 times",
         i.steps, 320)
    C.ck("... and mcycle kept advancing", i.cycle > 300, True)

    # ============================================== 21. EPV with a scalar tail
    print("\n=== 21. mixed program: EPV + scalar, and the imm/stride switches ===")
    global EPV_MEM_ADDR_STRIDE_X4, EPV_IMM_ZERO_EXTENDS, EPV_MEM_IMM_UNUSED
    keep_s, keep_i = EPV_MEM_ADDR_STRIDE_X4, EPV_IMM_ZERO_EXTENDS
    keep_u = EPV_MEM_IMM_UNUSED
    b = RAM + 0x2700
    i = new()
    poke(i, b + 0, 0xAAAA_AAAA)
    poke(i, b + 4, 0xBBBB_BBBB)
    poke(i, b + 8, 0xCCCC_CCCC)
    poke(i, b + 12, 0xDDDD_DDDD)
    prog(i, [epv_c0(EPV_F3_LVB, 0, 9, 0x000, rs2=1),
              epv_c1(EPV_F7_VRELU8, 2, 1, 0),
              epv_c1(EPV_F7_VDOT8, 3, 2, 2),
              enc_r(0, 3, 3, 0, 4, OP_OP)])                     # add x4,x3,x3
    i.regs[9] = b
    must_not_trap(i, 4, "mixed")
    C.ck("all bytes of 0xAAAAAAAA.. have bit7 set -> VRELU8 gives all zeros",
         lanes_b(i.vregs[2]), [0] * 16)
    C.ck("EPV.LVB -> VRELU8 -> VDOT8 -> ADD chains vregs and GPRs",
         (i.regs[3], i.regs[4]), (0, 0))
    i = new()
    poke(i, b + 0, 0x41C3_22FF)                                # FF 22 C3 41
    poke(i, b + 4, 0)
    poke(i, b + 8, 0)
    poke(i, b + 12, 0)
    prog(i, [epv_c0(EPV_F3_LVB, 0, 9, 0x000, rs2=1),
              epv_c1(EPV_F7_VRELU8, 2, 1, 0),
              epv_c1(EPV_F7_VDOT8, 3, 2, 2),
              enc_r(0, 3, 3, 0, 4, OP_OP)])                     # add x4,x3,x3
    i.regs[9] = b
    must_not_trap(i, 4, "mixed2")
    # bytes -1,34,-61,65 -> relu 0,34,0,65 -> dot = 34^2 + 65^2 = 1156 + 4225
    C.ck("hand check: VRELU8 of FF 22 C3 41 = 00 22 00 41",
         lanes_w(i.vregs[2])[0], 0x4100_2200)
    C.ck("hand check: VDOT8 = 34*34 + 65*65 = 5381, ADD doubles it",
         (i.regs[3], i.regs[4]), (5381, 10762))
    EPV_MEM_ADDR_STRIDE_X4 = False
    i = new()
    poke(i, b + 0, 0xAAAA_AAAA)
    poke(i, b + 4, 0xBBBB_BBBB)
    poke(i, b + 8, 0xCCCC_CCCC)
    poke(i, b + 12, 0xDDDD_DDDD)
    prog(i, [epv_c0(EPV_F3_LVB, 0, 9, 0x000, rs2=1)])
    i.regs[9] = b
    i.step()
    C.ck("EPV_MEM_ADDR_STRIDE_X4=False reproduces the pre-fix walk (one word 4x)",
         lanes_w(i.vregs[1]), [0xAAAA_AAAA] * 4,
         "this is the ledger-defect-11 era addressing bug (base + wc); the "
         "golden default base+4k is today's hardware")
    EPV_MEM_ADDR_STRIDE_X4 = keep_s
    # Hardware (default): the R-type LVB has no immediate -- whatever sits in
    # inst[31:20] cannot move the transfer address (quirk 17).
    i = new()
    poke(i, b + 0, 0xAAAA_AAAA)
    poke(i, b + 4, 0xBBBB_BBBB)
    poke(i, b + 8, 0xCCCC_CCCC)
    poke(i, b + 12, 0xDDDD_DDDD)
    prog(i, [epv_c0(EPV_F3_LVB, 0, 9, 0xFE0, rs2=1)])           # imm = -32
    i.regs[9] = b
    srz = i.step()
    C.ck("EPV.LVB ignores the imm field: base is exactly rs1 even with imm=0xFE0",
         (srz.trap, lanes_w(i.vregs[1])),
         (None, [0xAAAA_AAAA, 0xBBBB_BBBB, 0xCCCC_CCCC, 0xDDDD_DDDD]),
         "the I-type era would have read b+4064 (or b-32); the R-type reads b "
         "and walks four consecutive words")
    # Legacy switch: reviving the imm keeps both extension readings reachable.
    EPV_MEM_IMM_UNUSED = False
    for zext, want, nm in ((True, [0, 0, 0, 0], "zero-extended"),
                           (False, [1, 2, 3, 4], "sign-extended")):
        EPV_IMM_ZERO_EXTENDS = zext
        i = new()
        poke(i, b - 32, 1)
        poke(i, b - 28, 2)
        poke(i, b - 24, 3)
        poke(i, b - 20, 4)
        prog(i, [epv_c0(EPV_F3_LVB, 0, 9, 0xFE0, rs2=1)])       # imm = -32
        i.regs[9] = b
        srz = i.step()
        C.ck("legacy imm mode, %s offset" % nm,
             (srz.trap, lanes_w(i.vregs[1])), (None, want),
             "base + 4064 reads unwritten RAM -> zeros; base - 32 reads the "
             "fixture words 1,2,3,4")
    EPV_IMM_ZERO_EXTENDS = keep_i
    EPV_MEM_IMM_UNUSED = keep_u
    i = new()
    poke(i, b, 0x1111_1111)
    prog(i, [epv_c0(EPV_F3_LVB, 0, 9, 0x000, rs2=24)])          # vector 24
    i.regs[9] = b
    C.ck("EPV.LVB with a vector number > 7 is field_bad (inst[24:20][4:3] != 0)",
         i.step().trap.cause, EXC_ILLEGAL)
    MIRROR_RTL_DECODE_BUGS = keep

    print("\n" + "=" * 72)
    print("self test: %d assertions passed, %d failed" % (C.passed, C.failed))
    if C.failures:
        print("FAILURES:")
        for f in C.failures:
            print("   - %s" % f)
    print("=" * 72)
    return C.failed == 0, C.passed, C.failed


if __name__ == "__main__":
    ok, npass, nfail = selftest()
    sys.exit(0 if ok else 1)
