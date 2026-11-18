# EPV32 — RISC-V SoC for INT8 inference

I built EPV32 to see what a small instruction extension could do for INT8 convolution. The core is a five-stage, in-order RV32IM + Zicsr design in SystemVerilog. Its EPV unit adds 17 instructions for loading tiles, broadcasting values, computing dot products, and accumulating results. They use the RISC-V custom opcode space, so the same SoC can also run the plain C implementation.

## What changed on the same core

| Workload | C implementation | EPV implementation | Speedup by cycle count |
| --- | ---: | ---: | ---: |
| [INT8 convolution](results/bench/nn_bench.json) | 361,967 cycles | 23,005 cycles | 15.73× |
| [Three-layer INT8 network](results/bench/nn_net.json) | 2,172,053 cycles | 109,569 cycles | 19.82× |

The convolution and network runs matched the C reference bit for bit in Verilator. The network result covers three input samples. The added datapath raises the Yosys core cell count from 1,010 to 1,670. [Synthesis summary](results/synth/summary.json)

I also wrote a Python instruction-set simulator and compared its retire trace against the RTL, one instruction at a time. The saved comparisons cover 42 RV32I, 8 RV32M, and 16 machine-mode tests, with zero divergences. [Comparison results](results/isco/)

## Try it

The instruction-set simulator runs with Python 3 and its standard library:

```sh
python tb/golden/epv_iss.py
```

To run the convolution on the RTL, use Windows with an MSYS2 shell, Verilator 5, and the bare-metal `riscv64-unknown-elf` GNU toolchain. If MSYS2 is installed somewhere other than `E:/msys64`, update the paths in [`flow/build_sim.sh`](flow/build_sim.sh) and [`flow/rt.py`](flow/rt.py) first.

```sh
bash flow/build_sim.sh
python flow/rt.py bench sw/bench/nn_bench.c --tag example --no-trace
```

## Where to look

- [`rtl/`](rtl/) contains the core, EPV datapath, and SoC top.
- [`sw/`](sw/) contains the bare-metal runtime and C benchmarks.
- [`tb/`](tb/) contains the Verilator harness and Python instruction-set simulator.
- [`flow/`](flow/) contains the build and simulation scripts. [`results/`](results/) holds the saved measurements.

Project code is [MIT licensed](LICENSE). [Third-party licenses](refs/THIRD_PARTY_LICENSES/)
