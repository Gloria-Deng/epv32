# EPV32

A RISC-V RV32IM SoC with a custom instruction extension (**EPV**) that accelerates
edge-side INT8 neural-network inference.

> **中文简介**：EPV32 是一款面向端侧 INT8 神经网络推理的 RISC-V SoC。它在五级流水
> RV32IM 处理器核上扩展了 17 条 EPV 自定义指令，把 INT8 卷积里的搬运、广播、点积与
> 乘加等热点操作下沉为单条指令。相比 `-O2` 纯 C 基线，同一份 INT8 卷积实测加速
> **15.73×**，输出逐 int8 全等；核心通过官方 riscv-tests 合规集，并与独立黄金 ISS
> 做逐条退休轨迹比对，零已知分歧。

---

## Overview

EPV32 is a small, self-contained RISC-V system-on-chip written in SystemVerilog:

- A **5-stage in-order pipeline** implementing **RV32IM + Zicsr**.
- A custom **EPV vector unit** with **17 instructions** that lower the hot loops of
  INT8 convolutions (tile load/broadcast, dot-product, multiply-accumulate, etc.)
  into single instructions.
- A complete bare-metal **software stack**: C runtime, UART/printf, linker scripts,
  and a set of benchmark programs.
- A cycle-accurate **Verilator testbench** plus an independent **golden instruction-set
  simulator (ISS)** written in pure-Python stdlib, used for per-instruction
  retire-trace comparison.

The extension keeps the base ISA untouched: EPV instructions live in the
`CUSTOM0`/`CUSTOM1` major-opcode space, so standard RV32IM software keeps working
unchanged.

## Measured results

All numbers below are backed by the JSON archives in [`results/`](results/).

| Metric | Result | Evidence |
|---|---|---|
| rv32um official compliance | 8 / 8 | `results/compliance/rv32um.json` |
| rv32ui official compliance | 42 / 42 (incl. `ma_data` — misaligned access handled in hardware) | `results/compliance/rv32ui.json` |
| rv32mi machine-mode tests | 16 / 16 | `results/isco/rv32mi.json` |
| EPV 17-instruction selftest | fails = 0 (reference computed in C) | `results/sim/epv_selftest.json` |
| INT8 conv (3×3×16→4, 36,864 MAC) | baseline 361,967 → EPV 23,005 cycles = **15.73×** | `results/bench/nn_bench.json` |
| End-to-end INT8 net (3 samples / 138,432 MAC) | 2,172,053 → 109,569 cycles = **19.82×** | `results/bench/nn_net.json` |
| Output bit-exactness | 256 / 256 int8 identical, 0 mismatch | `results/bench/nn_net.json` |
| Core area (Yosys, w/ vs w/o EPV) | 1670 vs 1010 cells (+660) | `results/synth/summary.json` |
| RTL lint (EPV on / off) | 0 error / 0 warning | `results/lint/` |
| Golden ISS self-check | 374 assertions pass | `results/iss/selftest.json` |
| Golden ISS ↔ RTL retire trace | rv32um 8/8, rv32ui 42/42, rv32mi 16/16 — zero divergence | `results/isco/` |

FPGA backend (Anlogic EG4, via Tang Dynasty 6.2.1): synthesis reports **+4,946 LUT /
+24 DSP** for the EPV datapath versus the baseline, with routed Fmax **27.615 MHz vs
28.611 MHz** (−3.48%), leaving a net wall-clock speedup of ~15.19×. The place-and-route
archives are regenerable (see below) and are not committed to this repository.

## Repository layout

```
rtl/        SystemVerilog RTL — pipeline core, EPV unit, ALU, CSRs, RAM, SoC top
sw/         Bare-metal software — CRT0, linker script, platform lib, INT8 benchmarks
tb/         Verilator testbench (sim_main.cpp) + golden ISS (tb/golden/epv_iss.py)
flow/       Build / run / lint / benchmark driver scripts (rt.py, run_all.py, ...)
tools/      Helper scripts (wrappers, archivers, encoding-consistency checks, ...)
refs/       Upstream riscv-tests compliance suite + third-party licenses
docs/       Technical references (docs/refs) and data figures (docs/附图)
results/    Lightweight evidence archives (JSON); heavy traces/pnr/demo are regenerable
```

## Toolchain requirements

- **RISC-V GCC** — `riscv64-unknown-elf-gcc` (bare-metal, RV32IM).
- **Verilator** ≥ 5.0 — cycle-accurate simulator generation.
- **Yosys** — optional, for the cell-area figures.
- **Anlogic Tang Dynasty** — optional, for the FPGA place-and-route backend.
- **Python 3** — testbench drivers, golden ISS, and archivers.

## Building and running

```bash
bash flow/lint_rtl.sh                 # full RTL lint, EPV on/off
bash flow/build_sim.sh                # build the cycle-accurate simulator

python flow/rt.py riscv rv32ui        # official compliance suite
python flow/rt.py riscv rv32um
python flow/rt.py riscv rv32mi

python flow/rt.py bench sw/bench/nn_bench.c  --max-cyc 400000   # INT8 conv: C vs EPV
python flow/rt.py bench sw/bench/nn_net.c    --max-cyc 400000   # end-to-end INT8 net
python flow/rt.py bench sw/bench/epv_selftest.c --max-cyc 400000
python flow/rt.py bench sw/bench/epv_bisect.c   --max-cyc 400000

python flow/iss_compare.py rv32ui     # golden ISS <-> RTL retire-trace comparison
```

The driver `flow/rt.py` compiles, loads, simulates, checks, and writes the result
archives under `results/`. `flow/run_all.py` is the full one-shot verification matrix;
note that some of its later steps target the original report/PPT/document generators
and the FPGA backend, which require the external EDA tools and are not part of this
source release.

## License

The project's own code is licensed under the [MIT License](LICENSE).
Third-party components retain their original licenses — see
[`refs/THIRD_PARTY_LICENSES/`](refs/THIRD_PARTY_LICENSES/) and the
`riscv-tests` upstream (BSD-3-Clause).
