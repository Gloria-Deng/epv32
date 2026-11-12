OUTPUT_ARCH( "riscv" )
ENTRY(_start)

MEMORY
{
  RAM (rwx) : ORIGIN = 0x80000000, LENGTH = 256K
}

SECTIONS
{
  .text.init : { *(.text.init) } > RAM
  .text      : { *(.text .text.*) } > RAM
  .rodata    : { *(.rodata .rodata.*) } > RAM
  .data      : { *(.sdata .sdata.* .data .data.*) } > RAM
  . = ALIGN(4);
  __bss_start = .;
  .bss (NOLOAD) : { *(.bss .bss.* COMMON) } > RAM
  . = ALIGN(4);
  __bss_end = .;
  _end = .;
  PROVIDE(end = .);
}
__stack_top = ORIGIN(RAM) + LENGTH(RAM);
PROVIDE(stack_top = __stack_top);
