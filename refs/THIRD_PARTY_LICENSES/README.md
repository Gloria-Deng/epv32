# 第三方组件许可证随包情况

本目录由 `python tools/third_party_licenses.py` 生成，登记表是 `manifest.json`。这里的每一份文本都是**从本机安装树逐字节复制**的，
复制后立刻比对两侧 sha256 与字节数；不一致即判 `CORRUPT`，
本机确实没有全文的组件走 `DECLARED_UNAVAILABLE` 并**必须带一句理由** —— 没有任何一份文本是凭记忆写的。

| 组件 | 用途 | 许可证标识 | 状态 | 随包文件 | 字节 | sha256 前 12 位 |
|---|---|---|---|---|---|---|
| yosys | 综合与等价核对 | ISC | COPIED | `refs/THIRD_PARTY_LICENSES/yosys/LICENSE` | 777 | 6998b5724d4c |
| verilator | 周期精确仿真台（tb/sim_main.cpp 的宿主） | 随包这份是 LGPL-3.0 全文（上游另列 Artistic-2.0 为可选许可，但本机安装树里只有 LGPL 那一份 → 未随包） | COPIED | `refs/THIRD_PARTY_LICENSES/verilator/LICENSE` | 42098 | 996af0513df2 |
| binutils | 交叉汇编与链接（as/ld，rv32im 目标） | GPL 系四份全文（COPYING=GPL-2.0、COPYING3=GPL-3.0 及两份 LGPL 版本），以随包文本为准 | COPIED | `refs/THIRD_PARTY_LICENSES/binutils/COPYING` | 18002 | 231f7edcc735 |
| 〃 | 〃 | 〃 | COPIED | `refs/THIRD_PARTY_LICENSES/binutils/COPYING3` | 35147 | 8ceb4b9ee5ad |
| 〃 | 〃 | 〃 | COPIED | `refs/THIRD_PARTY_LICENSES/binutils/COPYING.LIB` | 25291 | 56bdea73b614 |
| 〃 | 〃 | 〃 | COPIED | `refs/THIRD_PARTY_LICENSES/binutils/COPYING3.LIB` | 7639 | a853c2ffec17 |
| gcc | 交叉 C 编译器与 libgcc | GPL-3.0 + GCC Runtime Library Exception 3.1（两份都随包） | COPIED | `refs/THIRD_PARTY_LICENSES/gcc/COPYING3` | 35147 | 8ceb4b9ee5ad |
| 〃 | 〃 | 〃 | COPIED | `refs/THIRD_PARTY_LICENSES/gcc/COPYING.RUNTIME` | 3324 | 9d6b43ce4d8d |
| mingw-crt | 本机宿主侧运行时（Verilator 产物的 C 运行时） | ZPL-2.1（文件自陈：其中被显著标注为 Public Domain / BSD / LGPL 的部分按各自许可） | COPIED | `refs/THIRD_PARTY_LICENSES/mingw-crt/COPYING` | 2326 | 99a696609811 |
| riscv-tests | 官方合规套件（ISA 三套件用例来源） | BSD-3-Clause（UC Berkeley Regents 文本，三条条件齐全） | COPIED | `refs/THIRD_PARTY_LICENSES/riscv-tests/LICENSE` | 1402 | 43b730921329 |
| anlogic-td | 国产 FPGA 全流程（综合/PnR/位流） | Proprietary-EULA（厂商最终用户许可协议） | DECLARED_UNAVAILABLE | `refs/THIRD_PARTY_LICENSES/anlogic-td/LICENSE` | — | — |
|  |  |  |  |  |  | 理由：本机安装树 license/ 下只有机器密钥 Anlogic.lic，协议全文不在磁盘上；全文随厂商装机合同分发，包内只指向其安装位置而不复制 |
| newlib | 裸机 libc（-nostdlib 下仍取它的头与启动件） | BSD-4-clause-Style（含厂商特款条款） | DECLARED_UNAVAILABLE | `refs/THIRD_PARTY_LICENSES/newlib/COPYING` | — | — |
|  |  |  |  |  |  | 理由：本机交叉工具链是自建前缀，MSYS2 的 share/licenses/ 下没有 newlib 条目，磁盘上没有可逐字节取回的全文副本 |

复跑与核验：

```
python tools/third_party_licenses.py            # 收编并自证
python tools/third_party_licenses.py --check     # 只核, 不复写
python tools/third_party_licenses.py --selftest  # 跑 8 条夹具（含双向注入）, 末行 LICENSE_SELFTEST_OK N/N
```
