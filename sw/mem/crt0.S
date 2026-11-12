/* ============================================================================
 *  crt0.S  --  EPV32 裸机入口
 *
 *  做四件事: 设栈 -> 清 .bss -> 把 UART 分频写成 1 -> 跳 main -> 把 main 的
 *  返回值写进 SYSC_COMMIT (测试台据此结束仿真)。
 *
 *  UART 分频那一步是刻意写下来的: RTL 里 DEFAULT_DIV = 434 对应「100 MHz 核心
 *  跑 115200 波特」的真实上板值; 仿真的测试台按轮询等待发送空闲, 若照抄 434
 *  则每输出一个字符要多花 4340 个周期, 会把任何计时读数都污染掉。
 *  所以仿真里由固件把它改成 1, 并在报告里注明「上板时需按实际晶振重设」。
 * ==========================================================================*/
        .section .text.init
        .global _start
        .equ UART_STATUS, 0x90000004
        .equ UART_DIV,    0x90000008
        .equ SYSC_COMMIT, 0x90020000

_start:
        la      sp, __stack_top
        /* 关中断 + 清 CSR (mtvec 留给测试自己装) */
        csrw    mscratch, x0

        /* ---- 清 .bss ---- */
        la      t0, __bss_start
        la      t1, __bss_end
1:      bgeu    t0, t1, 2f
        sw      zero, 0(t0)
        addi    t0, t0, 4
        j       1b
2:
        /* ---- 仿真用 UART 分频 = 1 ---- */
        li      t2, UART_DIV
        li      t3, 1
        sw      t3, 0(t2)

        call    main

        /* ---- main 返回值 -> commit, 然后原地自旋等测试台收工 ---- */
        li      t4, SYSC_COMMIT
        sw      a0, 0(t4)
3:      j       3b
