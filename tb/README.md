# tb/ 说明

本目录只有两份东西：`tb/sim_main.cpp`（Verilator 生成的 SoC 模型的 C++ 主程序，也就是
**核外面那一层台架**）与 `tb/golden/epv_iss.py`（黄金参考模型 + 它的自测）。它既不是
UVM 也不是 SV 台架，所以「本包的验证拓扑是什么」这个问题的答案全在这两个文件里，而不
在别处的配置文件里。

本件是手写的，因此它**一个数都不写**：断言条数、周期数、通过数、用例数一律指向
`results/` 里现算出它们的那份归档。要数数就去看归档与它自己的判决行，不要信这里。

## tb/sim_main.cpp 的四条观察通道

四条通道各自独立，配置一律走环境变量而不是 plusarg —— Verilator 的 plusarg 只认
`+verilator+` 前缀，自己解析容易做出「参数没进去而程序照跑」的静默形状（这句话的依据
写在该文件头部注释里）。

1. **退休轨迹（retire trace）** —— `EPV_TRACE` 给路径。每拍若 `o_tr_valid` 为真就计入
   `retired`，并向该文件写两类定长记录：寄存器写 `R <pc> <rd> <wdata>`（要求
   `o_tr_regwr` 且 `o_tr_rd != 0`，所以 x0 写与纯访存指令不产 R 行）与存储写
   `S <addr> <data> <be>`（`o_tr_memwr` 为真时）。`o_tr_valid` 为假的那些拍累计进
   `nonretire_cycles`，这就是「这一拍没退休」的可见证据。
   这条通道被 `flow/iss_compare.py` 与 `flow/rt.py` 消费，逐用例的读数落在
   `results/sim/`，与黄金模型逐条比对的结果落在 `results/isco/` 和
   `results/isco_probe/`。
2. **总线轨迹（含 UART 侧信道）** —— 上面那批 `S` 记录就是总线写轨迹；台架另外把
   「写到 `0x9000_0000`」这一件事单独摘出来当作字符输出，攒成 `uart` 字符串写进结果
   JSON。也就是说 UART 不是另接一根线，而是从总线轨迹里按地址切出来的。哪些用例用到
   它，看 `results/sim/` 里对应用例的 `uart` 字段，以及 `flow/run_all.py` 中
   `micro_bisect` / `epv_selftest` 那一步的 `need_fields=("uart",)`。
3. **陷入 CSR 窗口（trap CSR）** —— `o_dbg_trap_cnt` / `o_dbg_mepc` / `o_dbg_mtval` /
   `o_dbg_mcause` / `o_dbg_last_inst` 五个观测点，从核里直接引出，落在结果 JSON 的
   `dbg_*` 字段里（结果路径由 `EPV_RESULT` 给，默认 `results/sim/last.json`）。
   这一档的用途是「程序没 retire 出可见痕迹」时给出原因：陷入次数、PC、mtval、cause
   与最后一条指令原文。非对齐访存与 RV32M 保留编码那几条判据的实际读数在
   `results/sim/` 与 `results/asserts/`。
4. **波形（VCD）** —— `EPV_WAVE` 非零才开；路径由 `EPV_WAVE_FILE` 给，默认
   `results/wave/epv.vcd`；时钟每拍 dump 两次（`2*cyc` 与 `2*cyc+1`），因此波形保住了
   两个沿上的电平，这是「分派为什么没起来」这类问题的唯一看法。压缩件与收条在
   `results/wave/`。

除这四条之外，同一份结果 JSON 还记 `cycles` / `retired` / `nonretire_cycles` /
`stop_reason`（三态：`COMMIT`、`TOHOST`、`MAXCYC`——读到上限而没停就是没跑完）/
`commit_seen` / `commit_val` / `tohost_addr` / `image_words`。程序装载走**后门**：
`bld_en` / `bld_addr` / `bld_wdata` 在复位期间逐字写主存，装载完成后释放复位。
另有一块 `#if VM_COVERAGE` 内的覆盖率落盘（`EPV_COV`），认证臂不带该宏，所以认证
二进制里没有这段代码；覆盖率那一档的读数在 `results/cov/`。

## tb/golden/epv_iss.py 的断言是怎么分组的

- 断言机器是 `_Check.ck(name, got, want, why)`：通过就印 `ok`，失败就印 `FAIL` 加
  期望值、实际值与一句说明，并把名字收进 `failures` 列表。**没有裸 `assert`**，所以
  任何一条失败都自带它当时的三个操作数，而不是一句 traceback。
- 分组是 `selftest()` 内部的**编号横幅注释**（`grep '^    # =' tb/golden/epv_iss.py`
  现数、现看），从 `encoder sanity` 起、到 `EPV with a scalar tail` 止，中间按
  RV32I / 分支跳转 / 访存 / 精确陷入 / 系统指令 / CSR / RV32M / 定时器中断 / 外设 /
  EPV 译码表 / EPV 各条指令族 / 退休轨迹形状 / `run()` 这样的次序排。要说清楚的是：
  这些组只是**阅读上的分组**，计数器 `C.passed` 与 `C.failed` 是全局一本账，不分池，
  所以任何一组红都会汇到末行那一句上，而末行不会告诉你是谁。逐条的名字才是定位信息。
- 期望值全部手工推：指令由本文件自带的 `enc_r` / `enc_i` / `enc_s` / `enc_b` /
  `enc_u` / `enc_j` 与 `epv_c0` / `epv_c1` 现场拼，语义按 `rtl/epv32_defs.vh` 与
  `rtl/epv32_epv.v` 推出来，**没有一条期望值是从模型输出里读回来的**。
- 三条「黄金模型故意与 RTL 不一致」的开关（`MIRROR_RTL_DECODE_BUGS`、
  `EPV_MEM_ADDR_STRIDE_X4`、`EPV_IMM_ZERO_EXTENDS`）在自测里被临时翻转，用来把已知
  偏差本身钉成断言（两边各一条，方向相反），用完复原。这几处偏差的清单与后果写在该
  文件头部的 deviates / quirk 段，以及 `docs/` 附件 A 的对应台账里。
- 末行那句 `self test: N assertions passed, M failed` 就是链条唯一取的数：
  `flow/run_all.py` 的 `iss_selftest` 步解析它，并把 N、M、退出码连同那份 `.py` 当时的
  哈希头一起写进 `results/iss/selftest.json`。**N 与 M 的真值只看那份归档**，本件不抄。

## 本包没有模块级 EPV 测试台（这条是事实，不是遗漏的措辞）

没有 unit-level 的 EPV 测试台。三条可核的依据：`tb/` 下只有上面两份文件；
`find rtl tb sw -name "*.sv"` 为空（整个包没有 SystemVerilog 台架文件）；盘上也不存在
`results/epv_unit/` 这个目录。EPV 目前只在**整核**这一层被观察——`tb/sim_main.cpp`
实例化的是 `Vepv32_soc`，所以一个单元级的错误只有当它能改变退休轨迹、总线写或
`dbg_*` 时才看得见。单元级另有**面积**那一档：`results/synth/epv_only.txt` 是 yosys
单独综合 `epv32_epv` 的读数，那不是测试台，也不给任何行为判决。

这条缺口的代价已经被记下来了：`flow/assert_selfcheck.py` 的 `NO_CHEAP_MUTATION` 名单
里那几条断言（`E3_XWR_NOT_ON_DONE`、`E6_EPV_INST_CHANGED_MIDFLIGHT` 一类）声明「注不出
廉价反例」，给出的理由正是「要注出来得先有 `tb/epv_unit.cpp`，而它不存在」。也就是说，
这里少的是一个台架，而不是一份被漏掉的报告；哪些断言因此只有正向证据，看
`results/asserts/` 里那一档存档的 `no_cheap` 计数与它当时的逐文件源码指纹。
