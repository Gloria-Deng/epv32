/* ============================================================================
 *  platform.c  --  EPV32 裸机运行时支撑 (不依赖 newlib, -nostdlib 直链)
 *
 *  只放本设计真的用到的东西: UART 字符输出、无符号/有符号十进制打印、
 *  简单 malloc-free 的整数解析, 以及退出。刻意不引 libc, 因为「裸机 + 自研
 *  工具链选项」是本设计要立住的口径: 报告里出现的每一个周期数都能在
 *  results/sim/*.json 里追到同一次运行。
 * ==========================================================================*/
#include "epv32_hw.h"

void epv_putc(char c)
{
  /* 等发送器空闲 (仿真下 DIV=1, 一字符 10 周期) */
  while ((*EPV_UART_STATUS & EPV_STATUS_TX_IDLE) == 0)
    ;
  *EPV_UART_DATA = (uint32_t)(uint8_t)c;
}

void epv_puts(const char *s)
{
  while (*s) {
    if (*s == '\n') epv_putc('\r');
    epv_putc(*s++);
  }
}

void epv_putu(uint32_t v)
{
  char buf[12];
  int i = 0;
  if (v == 0) { epv_putc('0'); return; }
  while (v) { buf[i++] = (char)('0' + (v % 10u)); v /= 10u; }
  while (i) epv_putc(buf[--i]);
}

void epv_puti(int32_t v)
{
  if (v < 0) { epv_putc('-'); epv_putu((uint32_t)(-(int64_t)v)); }
  else       epv_putu((uint32_t)v);
}

void epv_putx(uint32_t v)
{
  static const char h[] = "0123456789abcdef";
  for (int i = 28; i >= 0; i -= 4) epv_putc(h[(v >> i) & 0xFu]);
}

/* 打印一个 64 位计数: mcycle / minstret 都是 64bit, 只读低 32 会在
 * 长基准上溢出, 所以单独给一条路径。 */
void epv_putu64(uint64_t v)
{
  char buf[21];
  int i = 0;
  if (v == 0) { epv_putc('0'); return; }
  while (v) { buf[i++] = (char)('0' + (int)(v % 10u)); v /= 10u; }
  while (i) epv_putc(buf[--i]);
}

void epv_exit(int code)
{
  *EPV_SYSC_COMMIT = (uint32_t)code;
  for (;;)
    ;
}

/* 让 -nostdlib 链接器满意的几个空桩: 本设计不用堆、不用环境变量 */
void  *_sbrk(int incr) { (void)incr; return (void *)-1; }
int    _write(int fd, const void *b, int n) { (void)fd; (void)b; return n; }
int    _read(int fd, void *b, int n) { (void)fd; (void)b; (void)n; return 0; }
int    _close(int fd) { (void)fd; return 0; }
int    _lseek(int fd, int o, int w) { (void)fd; (void)o; (void)w; return 0; }
void   _exit(int code) { epv_exit(code); }
