# 外部来源登记表（外部读数的出处与取回状态）

本文件登记 `docs/附件A_缺陷与门禁台账.md` 第 A.9 节「与现有同类技术的横向对照」里每一个**外部来源**：
本包引用了它的哪些读数、在自己的环境里把它取回到了哪一层、以及读者用哪一条命令可以自己重新取一遍。
它同时是 `rtl/epv32_defs.vh` 第 10 行那句「标准 RV32I / RV32M / Zicsr 主操作码遵循 The RISC-V
Instruction Set Manual Volume I (riscv-privileged/unprivileged, 见 docs/refs/SOURCES.md)」的落点。
本表**不主张任何性能优势、也不复述任何结论**，它只回答一个问题：A.9 里那些不是本包自己量出来的数字，
分别是从哪一份外部文本取的、取到了什么程度。取回状态一律来自本包实际执行过的取回尝试（逐条原始读数见
第二节「本次实际取回的回执」），只允许四个档位——`已在线核对（摘要级）`（只取到标题与摘要，凡摘要里没有的
读数一律在该行就地标注「摘要级无法确认该读数」而**不升级为正文级**）、`已在线核对（正文级）`（取到了承载
该读数的源文件文本本身并在那里看见了它）、`未取回（原因）`、`本包未随附（可公开获取）`。
最后一件要交代的事：下列原件**一份都没有随包分发**（本包只带自己的 RTL、软件、测试台与归档），
所以「复核入口」那一列给的是联网命令而不是包内路径。

---

## 一、外部来源登记表

列名里的「A.9 的哪一列」指 `docs/附件A_缺陷与门禁台账.md` A.9 对照表的列标题（方案 / 8-bit MAC/周期/核 /
加速比 / 扩展的代价 / 时钟工艺 / 数值格式 / 一手出处 / 与本方案不可比之处），读数一律照 A.9 原样抄录，
不改写、不四舍五入、不换计数单位。

| 来源标识 | 类型 | 本包引用了它的哪些读数 | 取回状态 | 复核入口 |
|---|---|---|---|---|
| `arXiv:1801.06601` | 预印本论文（arXiv 摘要页） | 8-bit MAC/周期/核 列：`未公布 → **UNVERIFIED**（其 int8 走 SIMD16 打包：SMLAD / `__SXTB16`）`；加速比 列：`**2.6–5.4×**，基线＝CMSIS-DSP 卷积 + 软件 pooling/ReLU`；扩展的代价 列：`未公布 → **UNVERIFIED**`；时钟/工艺 列：`216 MHz（STM32F746 硅片）`；数值格式 列：`逐通道 multiplier(Q31)+shift+offset+clamp`；不可比之处 列：`该文卷积是 5×5 且未给 MAC/周期；M7 有 Harvard 缓存，与本方案的单周期理想总线不同源`。**摘要级无法确认以上任何一条读数**：取回的摘要只给出另外两个数（4.6X runtime/throughput、4.9X energy efficiency），`2.6`、`5.4`、`216`、`STM32F746`、`SMLAD`、`__SXTB16`、`5x5` 逐条在摘要文本里归零；本次未取 PDF，因此不判定这组读数在该文正文中的落点，只登记到摘要级 | 已在线核对（摘要级） | `curl -sL https://arxiv.org/abs/1801.06601` |
| `arXiv:1908.11263（DOI 10.1098/rsta.2019.0155）` | 预印本论文 + 期刊 DOI（Crossref） | 8-bit MAC/周期/核 列：`簇合计最高 **15.5**（INT8）`；加速比 列：`相对**单核顺序 RISC-V** 最高 **63×**；相对 CMSIS-NN 少用 **30×/19.6×** 周期；比 STM32H7/L4 快 **2.54×/4.51×**`；扩展的代价 列：`簇级，未单列扩展面积`；时钟/工艺 列：`170 MHz，28 nm ASIC`；数值格式 列：`INT8 逐层定点`；不可比之处 列：`8 核并行 vs 本方案单核；"相对 CMSIS-NN 的倍数"是跨实现比较，不等于本方案与它们的比较`。**摘要级确认到的**：`15.5`（up to 15.5 MACs/cycle on INT-8）、`63`（up to 63x with respect to a sequential implementation on a single RISC-V core）、`30`/`19.6`（30x and 19.6x less clock cycles than … CMSIS-NN … on STM32L4 and STM32H7）。**摘要级无法确认该读数**：`2.54×/4.51×`、`170 MHz`、`28 nm`（摘要里 STM32 那两组对比给的是另外两个数，且没出现时钟与工艺节点）；A.9 的 `30×/19.6×` 未在此写明 L4/H7 的对应次序，摘要的次序是「L4、H7 分别对应 30x、19.6x」 | 已在线核对（摘要级） | `curl -sL https://arxiv.org/abs/1908.11263` 与 `curl -s https://api.crossref.org/works/10.1098/rsta.2019.0155` |
| `arXiv:2011.14325` | 预印本论文（arXiv 摘要页） | 8-bit MAC/周期/核 列：`**4**（单周期 8-bit SIMD 点积）`；加速比 列：`融合 `nn_sdotp` 相对**同核未融合的 SIMD** 为 **1.53×**；4/2-bit 相对 8-bit-only 簇 6×/8×`；扩展的代价 列：`**0.041 mm²，比基线核 +17.5%**（基线 35,131 µm²）`；时钟/工艺 列：`450 MHz @0.65V，GF22FDX 22 nm`；数值格式 列：`点积 + 移位，未把 requantize 收进指令`；不可比之处 列：`ASIC vs FPGA、22 nm 面积与 LUT 数不同量纲；该文只给点积，覆盖的算子面不同`。**摘要级确认到的**：`6 x`/`8 x`（4- 与 2-bit 相对 8-bit-only 基线簇）、`GF22FDX`。**摘要级无法确认该读数**：`0.041 mm²`、`+17.5%`、`35,131 µm²`、`450 MHz @0.65V`、`1.53×`、`nn_sdotp`、MAC/周期 `4` —— 摘要里那条融合收益写的是 `up to 1.64x peak MAC/cycle improvement`，与 A.9 的 `1.53×` 不是同一个字符串，本次没有取 PDF，因此**不判定两者谁对**，只登记「摘要级无法确认该读数」 | 已在线核对（摘要级） | `curl -sL https://arxiv.org/abs/2011.14325` |
| `arXiv:2110.09101` | 预印本论文（arXiv 摘要页） | 8-bit MAC/周期/核 列：`每核最高 15.5，簇 27`；加速比 列：`相对软件实现 **3×**`；扩展的代价 列：`整片 12 mm² / 1.8 Mgates，每核 70 kGE`；时钟/工艺 列：`32 kHz–450 MHz，22 nm FD-SOI`；数值格式 列：`INT/FP 混合`；不可比之处 列：`整片级 SoC 与"核 + 扩展"不是同一比较对象`。**摘要级确认到的**：这是一片 10 核 SoC（one core for SoC and IO management and a 9-cores cluster supporting multi-precision SIMD integer and floating-point computation），与「INT/FP 混合」「簇」两条说法一致。**摘要级无法确认该读数**：`15.5`、`27`、`3×`、`12 mm²`、`1.8 Mgates`、`70 kGE`、`32 kHz–450 MHz`、`22 nm FD-SOI` 全部不在摘要文本里 | 已在线核对（摘要级） | `curl -sL https://arxiv.org/abs/2110.09101` |
| `arXiv:2304.10319` | 预印本论文（arXiv 摘要页） | 8-bit MAC/周期/核 列：`int8 MAC/周期 未公布 → **UNVERIFIED**`；加速比 列：`相对标量为 FP32 kernel 的 40–84%（不是 int8 网络加速）`；扩展的代价 列：`未公布 → **UNVERIFIED**`；时钟/工艺 列：`1.0 GHz（Allwinner D1 硅片）`；数值格式 列：`RVV 向量，128-bit 寄存器`；不可比之处 列：`面向 FP32 HPC 负载，与 Q8 逐通道推理不同题`。**摘要级确认到的**：A.9 行名「玄铁 C906（RVV 0.7.1）」的主体身份——摘要字面出现 `XuanTie C906 processor`、`Allwinner D1 SoC`、`supporting RVV`，且负载定位是 HPC（RAJA Performance Suite）。**摘要级无法确认该读数**：`40–84%`、`1.0 GHz`、`RVV 0.7.1`、`128-bit`；注意该文标题并不是 C906 专文（见下面回执节的标题原文），A.9 用行名指代的是它测的那颗核 | 已在线核对（摘要级） | `curl -sL https://arxiv.org/abs/2304.10319` |
| `arXiv:2309.08186` | 预印本论文（arXiv 摘要页） | 加速比 列：`**1.6–14.6×**，基线＝**FPGA 上的 XpulpNN**`；扩展的代价 列：`该文库 233.3 kLUT；XpulpNN 基线 220.4 kLUT / 80 DSP`；时钟/工艺 列：`200 MHz，ZCU102`；数值格式 列：`多精度 2–16 bit`；不可比之处 列：`网络与层形状不同；该文 DSP 单元格排版有歧义，引用前需回 PDF 表格复核`。**摘要级确认到的**：`1.6~14.6×`（improves inference throughput by 1.6$\sim$14.6× … compared to the prior art, XpulpNN）、`ZCU102`（Xilinx ZCU102 FPGA）、`2-bit to 16-bit`。**摘要级无法确认该读数**：`233.3 kLUT`、`220.4 kLUT`、`80 DSP`、`200 MHz`。A.9 自己已经把 kLUT/DSP 那一格标成「引用前需回 PDF 表格复核」，本次取回没有推翻也没有证实它 | 已在线核对（摘要级） | `curl -sL https://arxiv.org/abs/2309.08186` |
| `arXiv:1806.08342` | 预印本论文（arXiv 摘要页） | 数值格式 列：`逐通道 multiplier + shift + offset + clamp，四步`；不可比之处 列：`见下方"约定差别"一条`。**摘要级确认到的**：「逐通道」这半条——摘要字面 `Per-channel quantization of weights and per-layer quantization of activations to 8-bits`，以及 `We recommend that per-channel quantization of weights … be the preferred quantization scheme for hardware acceleration and kernel optimization`。**摘要级无法确认该读数**：`multiplier / shift / offset / clamp` 这条四步序列本身（四个词在摘要里全部归零）；它的字面量是在下面三行（两份 CMSIS-NN 文件 + 一份 tflite-micro 头文件）里逐项看到的，逐行各自记了自己对上与没对上的部分 | 已在线核对（摘要级） | `curl -sL https://arxiv.org/abs/1806.08342` |
| CMSIS-NN `arm_nn_vec_mat_mult_t_per_ch_s8.c` | 上游仓库源文件（ARM-software/CMSIS-NN，main 分支） | 数值格式 列（与下一行合起来）：`逐通道 multiplier + shift + offset + clamp，四步`，以及 A.9「约定差别」段里本方案 `VREQ4` 与之对齐的那四步（乘、舍入移位、加零点、饱和）。**正文级确认到的**：文件头 `Title: arm_nn_vec_mat_mult_t_per_ch_s8`、`Project: CMSIS NN Library`、`$Revision: V.1.2.0`；函数签名里 `multiplier`、`shift`、`offset` 三个自变量（`dst_offset`、`dst_multiplier`、`dst_shift`）。**没有确认到的**：`clamp` 这个词在这个文件里出现 0 次——四步里的饱和那一步不在这个文件里，住在下一行那份头文件 | 已在线核对（正文级） | `curl -sL https://raw.githubusercontent.com/ARM-software/CMSIS-NN/main/Source/NNSupportFunctions/arm_nn_vec_mat_mult_t_per_ch_s8.c`（A.9 只写了文件名不带目录，按 `Source/BasicMathFunctions/` 猜路径会得到 404；正确路径由 `curl -sL https://api.github.com/repos/ARM-software/CMSIS-NN/git/trees/main?recursive=1` 现查） |
| CMSIS-NN `arm_nnsupportfunctions.h` | 上游仓库头文件（ARM-software/CMSIS-NN，main 分支） | 数值格式 列：`逐通道 multiplier + shift + offset + clamp，四步` 里的 clamp（饱和）那一步。**正文级确认到的**：`clamp` 出现 46 次、`offset` 171 次、`shift` 187 次，例如文档串 `@param[in] activation_min minimum value to clamp the output to. Range : int8`。**与 A.9 的一处口径要交代**：A.9 在 CMSIS-NN 那一行把 `SMLAD / `__SXTB16`` 归给 arXiv:1801.06601，而这两个词在这份头文件的当前版本里出现 0 次——本表不下「A.9 写错了」的结论，只登记「这两个词在本次取到的这份头文件里没有」 | 已在线核对（正文级） | `curl -sL https://raw.githubusercontent.com/ARM-software/CMSIS-NN/main/Include/arm_nnsupportfunctions.h` |
| tflite-micro `common.h` | 上游仓库头文件（tensorflow/tflite-micro，main 分支） | A.9「约定差别」段点名的两个函数：`SaturatingRoundingDoublingHighMul`（doubling-high-mul，`2·x·m >> 32`）与 `RoundingDivideByPOT`。**正文级确认到的**：两个名字都在取回的文本里，且相邻成对，字面形状是 `return RoundingDivideByPOT( SaturatingRoundingDoublingHighMul(x, quantized_multiplier), -left_shift); }`——即 A.9 写的「先 doubling-high-mul 再按 POT 舍入移位」这条顺序。本方案 `VREQ4` 用的是 `r = M / 2^(31+S)`，两者约定不同，所以 A.9 只写「同一条四步」而不写「与 TFLite 完全等价」 | 已在线核对（正文级） | `curl -sL https://raw.githubusercontent.com/tensorflow/tflite-micro/main/tensorflow/lite/kernels/internal/common.h` |
| The RISC-V Instruction Set Manual, Volume I：Unprivileged ISA（仓库 `riscv/riscv-isa-manual`，`src/unpriv/rv-32-64g.adoc`）＋ CUSTOM0/CUSTOM1 主操作码保留 | 规范原文（仓库源文本） | 本包从规范取的两件事：① `rtl/epv32_defs.vh` 第 10 行声明 RV32I / RV32M / Zicsr 主操作码遵循该手册；② `rtl/epv32_defs.vh` 第 28-29 行把 `OP_CUSTOM0` / `OP_CUSTOM1` 两个宏定义成 `7'h0B` / `7'h2B`，以及 `docs/参赛项目设计报告.md` 第二节「RISC-V 明文保留 CUSTOM0(0x0B)/CUSTOM1(0x2B)/custom-2/custom-3 供用户使用，这是本项目的合法性来源」与 3.3 节编码表里的 `CUSTOM0 0x0B`、`CUSTOM1 0x2B` 两行。**正文级确认到的**：主操作码图里 `_custom-0_`（inst[6:5]=00，inst[4:2]=010）与 `_custom-1_`（inst[6:5]=01，inst[4:2]=010）两个格子，以及规范原句 "Major opcodes marked as _custom-0_ through _custom-3_ will be avoided by future standard extensions and are recommended for use by custom instruction-set extensions within the base 32-bit instruction format."。**要交代的一处间接**：那张图给出的是 inst[6:5]/inst[4:2] 两个位段而不是 `0x0B`/`0x2B` 字面量，包内写死的 `7'h0B`/`7'h2B` 是位段拼出来的值（00_01011、01_01011），A.9 与报告写的是拼好之后的数；另一件要交代的是本行取回的是 `riscv/riscv-isa-manual` main 分支的**源文本**而不是任何已发布 PDF 的某一页，所以本表对它不写页码也不写章节号（`rtl/epv32_defs.vh` 第 28 行注释里自写的「Ch.27」这一点，本表既不确认也不更正） | 已在线核对（正文级） | `curl -sL https://raw.githubusercontent.com/riscv/riscv-isa-manual/main/src/unpriv/rv-32-64g.adoc`（该文件里搜 `custom-0`） |

---

## 二、本次实际取回的回执

下面是每一条取回尝试的**原始读数**：HTTP 状态、跟随重定向之后的最终 URL、以及取回文本里的标题字符串原样。
判定标题是否与 A.9 的说法相符看的是最终 URL 与页面里的标题，不是状态码本身（一条 200 但落在登录页的取回
不算取到）。本次没有任何一条取回落到登录页 / 拦截页——有两类失败是别的形状：猜错的仓库路径报 404，
以及 arXiv export API 报 406，两者都在下面逐条点名。
另一件必须交代的是：本表取的是 arXiv 摘要页与上游仓库 main 分支的**当前快照**（那份 CMSIS-NN 源文件自己
报出 `$Date: 22 September 2026`、`$Revision: V.1.2.0`），而 A.9 写下那些格子时看到的可能是更早的版本；
因此「本表取到的文本里没有某个读数」有两种成因（读数本来就住在正文/更深一层，或者版本变了），
本表只登记取到了什么，不替读者判定是哪一种。

- `arXiv:1801.06601` — 200，final `https://arxiv.org/abs/1801.06601`（未跳转）— 标题原文：
  `CMSIS-NN: Efficient Neural Network Kernels for Arm Cortex-M CPUs` — **相符**（A.9 行名 CMSIS-NN / Cortex-M7）。
- `arXiv:1908.11263` — 200，final `https://arxiv.org/abs/1908.11263`（未跳转）— 标题原文：
  `PULP-NN: Accelerating Quantized Neural Networks on Parallel Ultra-Low-Power RISC-V Processors` — **相符**。
- `DOI 10.1098/rsta.2019.0155` — 200，final `https://api.crossref.org/works/10.1098/rsta.2019.0155` —
  Crossref 的 `title` 字段原文：`["PULP-NN: accelerating quantized neural networks on parallel ultra-low-power RISC-V processors"]`，
  `container-title`：`["Philosophical Transactions of the Royal Society A: Mathematical, Physical and Engineering Sciences"]`，
  `type`：`journal-article`，`published`：`2019-12-23`，`URL`：`https://doi.org/10.1098/rsta.2019.0155` — **相符**
  （与 arXiv 那份同名，只是大小写不同：A.9 把 DOI 写在同一个括号里，这个 DOI 指的是期刊版）。
- `arXiv:2011.14325` — 200，final `https://arxiv.org/abs/2011.14325`（未跳转）— 标题原文：
  `XpulpNN: Enabling Energy Efficient and Flexible Inference of Quantized Neural Network on RISC-V based IoT End Nodes` — **相符**。
- `arXiv:2110.09101` — 200，final `https://arxiv.org/abs/2110.09101`（未跳转）— 标题原文：
  `Vega: A 10-Core SoC for IoT End-Nodes with DNN Acceleration and Cognitive Wake-Up From MRAM-Based State-Retentive Sleep Mode` — **相符**（A.9 行名「Vega 类簇芯」）。
- `arXiv:2304.10319` — 200，final `https://arxiv.org/abs/2304.10319`（未跳转）— 标题原文：
  `Test-driving RISC-V Vector hardware for HPC` — **部分相符**：标题里没有 C906，摘要里有 `XuanTie C906 processor`；
  A.9 那一行的行名叫的是被测核而不是论文标题。
- `arXiv:2309.08186` — 200，final `https://arxiv.org/abs/2309.08186`（未跳转）— 标题原文：
  `A Precision-Scalable RISC-V DNN Processor with On-Device Learning Capability at the Extreme Edge` — **部分相符**：
  标题不含 `XpulpNN` 也不含 `FPGA`，但摘要里 `compared to the prior art, XpulpNN` 与 `Xilinx ZCU102 FPGA` 两句都在，
  A.9 的行名「XpulpNN 的 FPGA 移植」是对该文的描述，不是该文自己的题名。
- `arXiv:1806.08342` — 200，final `https://arxiv.org/abs/1806.08342`（未跳转）— 标题原文：
  `Quantizing deep convolutional networks for efficient inference: A whitepaper` — **相符**（A.9 用它当重量化四步定义的出处之一）。
- CMSIS-NN `arm_nn_vec_mat_mult_t_per_ch_s8.c` — 首次按 `Source/BasicMathFunctions/` 猜路径 **404**；
  改由仓库树 API 现查到真实路径 `Source/NNSupportFunctions/arm_nn_vec_mat_mult_t_per_ch_s8.c` 后 **200**，
  final 与请求 URL 相同（未跳转），24,841 字节。
- CMSIS-NN `arm_nnsupportfunctions.h` — 首次按 `Source/NSupportFunctions/` 猜路径 **404**；真实路径
  `Include/arm_nnsupportfunctions.h` **200**，113,111 字节。
- tflite-micro `common.h` — **200**，final `https://raw.githubusercontent.com/tensorflow/tflite-micro/main/tensorflow/lite/kernels/internal/common.h`（未跳转），
  57,112 字节。
- RISC-V Unprivileged ISA 手册 — `src/unpriv/rv-32-64g.adoc` **200**，30,582 字符；
  另两条常见猜测路径 `src/chapters/isa-extensions.adoc` 与 `src/key_numbers.adoc` 返回 **404**（仓库布局不是这个形状，
  文件清单由 `https://api.github.com/repos/riscv/riscv-isa-manual/git/trees/main?recursive=1` 现查，200）。
- arXiv 官方 export API（`http://export.arxiv.org/api/query?id_list=<id>` 与它的 `https://` 版）在本机出口
  一律返回 **HTTP 406**，七条 id 全部如此，因此本表的摘要文本取自 `https://arxiv.org/abs/<id>` 页面本身。
  这不改变任何一行的判决（abs 页 200 且未跳转），但意味着「复核入口」那一列给的是 abs 页命令；
  读者若在自己的网络里能通 export API，也可以用 `curl -s "http://export.arxiv.org/api/query?id_list=2011.14325"`。

---

## 三、本包故意没有引用哪些

一张只列成功取回的登记表会高估这份材料的可核性，所以 A.9 自己写下的那串**否定式**限制照抄在这里
（下面那一整段含 ①②③ 三条，是 `docs/附件A_缺陷与门禁台账.md` A.9 节末尾「本方案不能主张什么」的原话，未改写）：

> **本方案不能主张什么**（口径不支持，写了就是夸大）：
> ① 不写"优于 Ultra-PULP / THRNN 的 X 倍"——这几项的一手数字在本环境取不到（IEEE
> 付费墙，预印本亦不在可达集），**UNVERIFIED 的数字一律不进正文**；
> ② 不写任何"能效更高 / 面积更小"的跨口径结论：FPGA 27.6 MHz 与 22 nm ASIC 450 MHz、
> 周期精确仿真与硅片流片、单层 3×3×16→4 与整网，三个维度同时不可比；
> ③ 不写"CMSIS-NN 的 MAC/周期低于我们"——它的一手文献根本没公布这个量。

把这几点翻译成登记表语言：

1. **Ultra-PULP、THRNN 这条线：一行都没有。** 它们的一手数字在 IEEE 付费墙后面，预印本也不在本次的可达集里，
   所以 A.9 的对照表里没有它们的行，本表也就没有可登记的对象——`未取回（IEEE 付费墙，预印本不在可达集）`
   这一档在本表里目前是空的，不是因为它们被核对过，而是因为**从未被引用**。
2. **A.9 表里就标成 UNVERIFIED 的格子，本表照原样登记而不替它补数**：
   CMSIS-NN 那一行的 `8-bit MAC/周期/核`（`未公布 → **UNVERIFIED**`）与 `扩展的代价`（`未公布 → **UNVERIFIED**`）；
   玄铁 C906 那一行的 `int8 MAC/周期`（`未公布 → **UNVERIFIED**`）与 `扩展的代价`（`未公布 → **UNVERIFIED**`）。
   这四格是「一手文献根本没公布这个量」，不是「本包没去找」。
3. **PULP-NN 的 `2.54×/4.51×`、XpulpNN 的 `0.041 mm² / +17.5% / 1.53× / 450 MHz @0.65V`、Vega 的
   `12 mm² / 1.8 Mgates / 70 kGE`、XpulpNN FPGA 移植那文库的 `233.3 kLUT / 220.4 kLUT / 80 DSP`
   本次没有升级到正文级**：摘要里没有、PDF 没有取，因此它们仍然只是「A.9 声称出自某篇文献」的读数。
   A.9 对最后一组本来就更保守（`该文 DSP 单元格排版有歧义，引用前需回 PDF 表格复核`）。
4. **随包分发的原件：零份。** 本表 11 行里没有任何一份外部原件住在包内，所以 A.9 的任何一个对照数字
   在本包内都无法离线复核；这一点正是本文件存在的第二个理由。
