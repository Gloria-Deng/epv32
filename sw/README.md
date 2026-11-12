# `sw/` 目录说明

这里是跑在那颗核上的软件：裸机运行时（入口、链接脚本、UART 打印、扩展指令的 intrinsic 宏）与全部自写用例。它**不是一套独立固件项目**，而是设计报告里那些周期数与逐位比对读数的产生端；地址图与指令编码在这里只是抄本，真源是 `rtl/epv32_defs.vh`，两边是否一致由 `tools/check_encoding_consistency.py` 判，不靠人记。本目录没有 Makefile，也没有任何产物——编译入口是 `flow/rt.py`，产物落 `build/elf/`，判决落 `results/`。

| 文件 | 职责 | 谁产出 | 可否手改 | 报告落点 |
|---|---|---|---|---|
| `inc/epv32_hw.h` | 裸机侧唯一的一层：SoC 地址图的软件抄本、架构计数器读法（`epv_rd_cycle` / `epv_rd_instret`）、每条 EPV 指令一个 `.insn` intrinsic，以及 `EPV_SVB_HAS_TAIL` 那条 `_Static_assert`（搬运目的缓冲的留白量在编译期钉住，少一个元素就编不过而不是等读数对不上） | 人写 | 可以；改编码必须同步跑 `python tools/check_encoding_consistency.py`。刻意保留的失败方向别去掉：向量寄存器号要求是源码里的字面量，传变量应在汇编期就报错 | 报告 3.3、3.10；附件 A.12 |
| `lib/platform.c` | 运行时支撑：UART 逐字符输出、十进制/有符号/十六进制/64 位打印、`epv_exit`，以及几个把 newlib 调用引回本层的最小桩。刻意不链 libc | 人写 | 可以；它由 `flow/rt.py` 每次 bench 编译自动带上，改它等于改所有用例的输出格式 | 报告 3.10 |
| `mem/crt0.S` | 入口：设栈、清 `.bss`、把 UART 分频改成 1、跳 `main`、再把 `main` 的返回值写进 SYSC 提交口（测试台据此收工）。分频那一步的理由写在文件头，与报告 3.4 那段计时口径是同一件事 | 人写 | 可以；动它会挪动所有用例的周期读数 | 报告 3.4、3.10 |
| `mem/epv32.ld` | 链接脚本：RAM 段布局与栈顶符号。数据段基址与官方测试环境的 `refs/riscv-tests/env/p/link.ld` 一致，因此合规套件不必改一行就能装载 | 人写 | 可以 | 报告 3.4、5.2 |
| `bench/epv_selftest.c` | 验什么：每条 EPV 指令各跑一次，与同一份 C 里现算的参考值逐 lane 比（参考值不抄常量，这样「模型自己写错」与「硬件算错」不会混成一个红）。怎么跑：`python flow/rt.py bench sw/bench/epv_selftest.c --max-cyc 400000`。判据：`VERDICT=PASS`，逐项读数看归档的 `uart` 字段 | 人写；跑法归 `flow/rt.py` | 可以 | 报告 5.4 |
| `bench/epv_bisect.c` | 验什么：把「16 字节搬运对不对」与「乘法/归约对不对」拆成互不依赖的三段，挂在哪一段由退出码的段位编码。怎么跑：`python flow/rt.py bench sw/bench/epv_bisect.c --max-cyc 400000`。判据：`VERDICT=PASS`。录波形那一页跑的是同一份源码的截窗，必须带 `--tag`，否则会把认证归档覆成截窗结论（见 `flow/demo.sh`） | 人写；跑法归 `flow/rt.py` | 可以 | 报告 5.4 |
| `bench/nn_bench.c` | 验什么：同一件 INT8 卷积累加，标量 C 基线与 EPV 两条实现吃同一份输入与权重、输出逐 int8 相同，再各自用架构计数器计时（打印排在计时区之外）。怎么跑：`python flow/rt.py bench sw/bench/nn_bench.c --max-cyc 4000000` 然后 `python tools/bench_archive.py`。判据：`bit_exact=True`（由 `tools/bench_archive.py` 印），读数在 `results/bench/nn_bench.json` | 人写；汇综归 `tools/bench_archive.py` | 可以；改算式或改基线写法都会动加速比，改后要重跑矩阵那一步 | 报告 5.4；附件 A.20 |
| `bench/nn_net.c` | 验什么：整张三层网络（两次卷积 + 池化 + 全连接）跑完一遍，基线与 EPV **逐层**输出逐字节相同，并与宽精度对照给出 logits 偏差。权重由 LFSR 定种子生成、没有训练过，所以这份不给任何数据集精度。怎么跑：`python flow/rt.py bench sw/bench/nn_net.c --max-cyc 9000000` 然后 `python tools/net_archive.py`。判据：`NET_ARCHIVE_OK`（由 `tools/net_archive.py` 印），读数在 `results/bench/nn_net.json` | 人写；汇综归 `tools/net_archive.py` | 可以 | 报告 5.4 |
| `bench/nn_cifar.c` | 验什么：CIFAR-10 上训练、折叠 BN、逐输出通道量化出的真权重在环跑整网，三条实现互核（标量 C、EPV、python 导出侧）。它给的是精度与在环读数，与上面那份合成网络互不顶替。怎么跑：`python flow/rt.py bench sw/bench/nn_cifar.c --max-cyc 8000000` 然后 `python tools/cifar_archive.py`，或整格复跑 `python flow/run_all.py cifar10_net`。判据：`CIFAR_ARCHIVE_OK`（由 `tools/cifar_archive.py` 印），读数在 `results/bench/cifar10_net.json` | 人写；汇综归 `tools/cifar_archive.py` | 可以；但权重与图像不许在这里另写，只能来自下面那份生成头 | 报告 5.4；附件 A.10、A.16 |
| `bench/cifar_int8.h` | 生成件：量化后的真权重、真实测试图、python 侧各层字节和与期望值，是 `bench/nn_cifar.c` 的唯一数据源 | `tools/train_cifar_int8.py`（训练 → 折叠 → 量化 → 导出，重跑即覆写本文件；数据集在包外，取不到那一步自己报 SETUP-FAIL） | **不可手改**——`tools/cifar_archive.py` 还要从它的 `#define` 现解分母，手改会让「被核对的那次比对」与「加速比的分母」变成同源，那道自检就永远绿 | 报告 5.4；附件 A.10、A.16 |
| `bench/misalign_bisect.c` | 验什么：非对齐访存与非对齐跳转目标这两条边界的实测形状（硬件拆分是否真的发生、跳板地址由链接器钉死、期望值一律用字节访问拼出来以免与被测特性同源）。怎么跑：`python flow/rt.py bench sw/bench/misalign_bisect.c --max-cyc 400000`——这一格由 `flow/demo.sh` 跑，`flow/run_all.py` 的步表里没有它。判据：`VERDICT=PASS`，逐项读数在 `results/sim/misalign_bisect.json` | 人写；跑法归 `flow/demo.sh` | 可以；它是报告 3.5 那两句边界话的唯一实测出处 | 报告 3.5；附件 A.1（读数为 `results/sim/misalign_bisect.json` 的那一条） |
| `bench/micro_bisect.c` | 验什么：计数器「写入那一拍抑制自增」与 PMP 强制行为的逐项读数——官方用例只给一个 TESTNUM，「高位没写进去」「进位没穿过去」「抑制没生效」三种毛病修法互不相同，这里把每一步单独读一次。怎么跑：`python flow/rt.py bench sw/bench/micro_bisect.c --max-cyc 4000000`，或整格复跑 `python flow/run_all.py micro_bisect`。判据：`VERDICT=PASS` 且归档 `commit_val` 为零，逐项读数在 `results/sim/micro_bisect.json` 的 `uart` 字段 | 人写；跑法归 `flow/rt.py` | 可以；没把握的读数它只打印不主张，别把它写成断言 | 报告 5.2、5.5；附件 A.4、A.22 |

判据 marker 的出处是可以自己复核的：`flow/demo.sh` 里那段 `MARKS` 逐行列了「哪份产物必须印出哪一句」，其余几句由印它们的工具自己印（`flow/rt.py` 印 `VERDICT=PASS`，`tools/bench_archive.py` 印 `bit_exact=True`，`tools/net_archive.py` 印 `NET_ARCHIVE_OK`，`tools/cifar_archive.py` 印 `CIFAR_ARCHIVE_OK`）。判定一律读归档字段而不是命令退出码，理由写在 `flow/rt.py` 头部。

## 读这个目录的正确顺序

1. `inc/epv32_hw.h` —— 先看一条 intrinsic 为什么写成 `.insn`，以及 `EPV_SVB_HAS_TAIL` 在钉什么；这两处决定了后面所有用例的写法。
2. `mem/epv32.ld` 与 `mem/crt0.S` —— 程序落在哪个地址、怎么开始、怎么宣布结束。
3. `lib/platform.c` —— 读数是怎么变成屏幕上一行字的，以及为什么打印不能混进计时区。
4. `bench/epv_selftest.c` → `bench/epv_bisect.c` —— 先看逐条自测，再看把搬运与归约拆开的那份，这两份解释了「扩展真的进了数据通路」这句话。
5. `bench/nn_bench.c` → `bench/nn_net.c` → `bench/nn_cifar.c`（配合 `bench/cifar_int8.h`）—— 一个算子、一张合成网络、一张真权重网络，三份的读数互不顶替，顺序也别倒过来读。
6. 两份边界探针 `bench/misalign_bisect.c`、`bench/micro_bisect.c` 最后读：它们回答的是「这条边界到底是怎么实现的」，需要前面那层的对照才有意义。

## 不在这个目录里／容易误以为在这里

- **没有构建系统。** 编译器路径、`-march`/`-mabi`、链接脚本、`mem/crt0.S` 与 `lib/platform.c` 的拼接、装载、仿真、判定、落归档，全在 `flow/rt.py` 一处。本目录里找不到 Makefile，也不必新增一份。
- **产物不在这里。** 编出来的 ELF 与 BIN 在 `build/elf/`（那份目录说明见 `build/MANIFEST.md`），逐用例判决在 `results/sim/`，汇综读数在 `results/bench/`，官方套件的逐例结果在 `results/compliance/`。
- **官方合规用例不在这里。** 它们在 `refs/riscv-tests/`，由 `flow/rt.py` 按套件目录直接枚举并改用官方自己的 `refs/riscv-tests/env/p/link.ld` 链接；本目录的 `mem/epv32.ld` 只服务自写用例。
- **黄金模型不在这里。** 软件侧的 intrinsic 与 `tb/golden/epv_iss.py` 是一对对偶，但期望值在这里是「同一份 C 现算」，在那里是「独立实现」，两份故意不共用代码。
- **数据集与权重不随包。** CIFAR-10 原始数据由 `tools/train_cifar_int8.py` 从包外目录读，随包的只有它导出后的那份 `bench/cifar_int8.h`；想凭这份头文件反推原始样本是反不过去的。
- **计时口径的换算不在这里。** 用例只读架构计数器；把周期变成加速比、逐层对照与新鲜度键的那几道汇综全在 `tools/` 里，本目录任何文件都不写「结果」。
