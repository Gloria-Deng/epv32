# flow/ 说明（部分生成件）

本目录是「一条命令跑齐全套证据」的那条链：`run_all.py` 是矩阵本体（每步既跑命令也
断言归档里的字段，命令退 0 而没产出预期字段就判红），其余是它逐步调用的门禁与归档
写入者 —— 面积/布线/波形/文档/字数/成片各一支，跑完把读数落进 `results/`。步序不
是摆设：好几步读的是**前一步刚落盘**的那份归档（例如 `isco_trace` 读
`results/compliance/*.json` 当分母、`consistency` 读本趟的 docx 与 pptx），所以
「能不能打乱顺序」这件事的答案，写在下面那张表的「写归档」一列里。

下面那一格表是生成件：步名一个都不手抄，由 `tools/make_navigation.py` import 本目录
的 `run_all.py` 现读 `STEPS` / `POST_STEPS` 生成。「写归档 / 不写归档」这一格按该模块
自己的源码与 `results/HISTORICAL_ARCHIVES.json` 的 `produced_by` 登记推，推不出来
的印 `UNKNOWN` 而不是猜一个 —— 要改本目录的说明文字请改标记**之外**的段落，标记
**之内**改了会在下一次生成时被抹掉。

<!-- FLOW_STEPS:auto -->
| # | 步骤 | 它产出什么 | 写归档 / 不写归档 | 它的证据文件 |
|---:|---|---|---|---|
| 1 | `lint` | 调 `lint_rtl.sh`；落归档 | 写归档（它点名的子脚本自己写 results/） | 由它调用的子脚本写，登记里没记这一步 |
| 2 | `build_sim` | 调 `build_sim.sh`；落归档 | 写归档（它点名的子脚本自己写 results/） | 由它调用的子脚本写，登记里没记这一步 |
| 3 | `iss_selftest` | 调 `epv_iss.py`；落归档 | 写归档（`HISTORICAL_ARCHIVES.json` 记它为 produced_by） | `results/iss/selftest.json` |
| 4 | `epv_bisect` | 调 `rt.py`；落归档 | 写归档（它点名的子脚本自己写 results/） | 由它调用的子脚本写，登记里没记这一步 |
| 5 | `epv_selftest` | 调 `rt.py`；落归档 | 写归档（它点名的子脚本自己写 results/） | 由它调用的子脚本写，登记里没记这一步 |
| 6 | `nn_bench` | 调 `rt.py`, `bench_archive.py`；落归档 | 写归档（它点名的子脚本自己写 results/） | 由它调用的子脚本写，登记里没记这一步 |
| 7 | `nn_net` | 调 `net_archive.py`, `rt.py`；落归档 | 写归档（它点名的子脚本自己写 results/） | 由它调用的子脚本写，登记里没记这一步 |
| 8 | `cifar10_net` | 调 `cifar_archive.py`, `rt.py`；落归档 | 写归档（`HISTORICAL_ARCHIVES.json` 记它为 produced_by） | `results/bench/cifar10_int8.json` |
| 9 | `mem_sensitivity` | 调 `mem_sensitivity.py`；落归档 | 写归档（`HISTORICAL_ARCHIVES.json` 记它为 produced_by） | `results/bench/mem_sensitivity.json` |
| 10 | `rv32ui` | 调 `rt.py`；落归档 | 写归档（`HISTORICAL_ARCHIVES.json` 记它为 produced_by） | `results/compliance/rv32ui.json` |
| 11 | `rv32um` | 调 `rt.py`；落归档 | 写归档（`HISTORICAL_ARCHIVES.json` 记它为 produced_by） | `results/compliance/rv32um.json` |
| 12 | `rv32mi` | 调 `rt.py`；落归档 | 写归档（`HISTORICAL_ARCHIVES.json` 记它为 produced_by） | `results/compliance/rv32mi.json` |
| 13 | `micro_bisect` | 调 `rt.py`；落归档 | 写归档（它点名的子脚本自己写 results/） | 由它调用的子脚本写，登记里没记这一步 |
| 14 | `isco_trace` | 调 `epv_iss.py`, `iss_compare.py`；落归档 | 写归档（它点名的子脚本自己写 results/） | 由它调用的子脚本写，登记里没记这一步 |
| 15 | `rand_trace` | 调 `rand_trace.py`；落归档 | 写归档（它点名的子脚本自己写 results/） | 由它调用的子脚本写，登记里没记这一步 |
| 16 | `synthesis` | 调 `synth_archive.py`；落归档 | 写归档（本模块内可达源码里有指向 results/ 的写动作） | `results/synth`（写目标按源码里的路径字面量，未进归档登记） |
| 17 | `encoding_gate` | 调 `check_encoding_consistency.py` | 不写归档（可达源码里没有任何指向 results/ 的写动作） | UNKNOWN |
| 18 | `pnr_gate` | 在本进程内直接跑模型/断言；落归档 | 写归档（本模块内可达源码里有指向 results/ 的写动作） | `results/pnr`（写目标按源码里的路径字面量，未进归档登记） |
| 19 | `doc_gate` | 调 UNKNOWN 名字: flow/zz_nope.py | UNKNOWN（子脚本名解不到盘上文件: flow/zz_nope.py（若它是本步自带的反例夹具名, 那是有意为之, 本格仍不猜）） | `results/bench`, `results/compliance`, `results/demo` |
| 20 | `word_gate` | 调 `check_word_limits.py`, `word_style_count.py`；落归档 | 写归档（`HISTORICAL_ARCHIVES.json` 记它为 produced_by） | `results/word_limits.json`, `results/word_style.json` |
| 21 | `figures` | 调 `make_figures.py` | UNKNOWN（点名的子脚本自己写不写 results/ 解不出） | UNKNOWN |
| 22 | `docx` | 调 `build_docx.py`, `verify_docx.py` | 不写归档（可达源码里没有任何指向 results/ 的写动作） | UNKNOWN |
| 23 | `ppt` | 调 `build_ppt.py`, `verify_ppt.py`；落归档 | 写归档（`HISTORICAL_ARCHIVES.json` 记它为 produced_by） | `results/ppt/verify.json` |
| 24 | `consistency` | 调 `check_deliverable_consistency.py` | 不写归档（可达源码里没有任何指向 results/ 的写动作） | UNKNOWN |
| 25 | `pair_gates` | 调 `assert_selfcheck.py`, `bram_arms_check.py`, `coverage_report.py`；落归档 | 写归档（它点名的子脚本自己写 results/） | 由它调用的子脚本写，登记里没记这一步 |
| 26 | `archive_clobber`（后置格：读本趟刚落盘的矩阵） | 调 `check_archive_clobber.py` | UNKNOWN（点名的子脚本自己写不写 results/ 解不出） | UNKNOWN |
| 27 | `compact_traces`（后置格：读本趟刚落盘的矩阵） | 调 `compact_traces.py` | UNKNOWN（点名的子脚本自己写不写 results/ 解不出） | UNKNOWN |
| 28 | `deliverables`（后置格：读本趟刚落盘的矩阵） | 调 `verify_deliverables.py` | 不写归档（可达源码里没有任何指向 results/ 的写动作） | UNKNOWN |
| 29 | `table_shape`（后置格：读本趟刚落盘的矩阵） | 调 `check_table_shape.py` | 不写归档（可达源码里没有任何指向 results/ 的写动作） | UNKNOWN |
| 30 | `navigation`（后置格：读本趟刚落盘的矩阵） | 调 `make_navigation.py`；落归档 | 写归档（它点名的子脚本自己写 results/） | 由它调用的子脚本写，登记里没记这一步 |
| 31 | `demo_artifacts`（后置格：读本趟刚落盘的矩阵） | 调 `check_demo_artifacts.py`；落归档 | 写归档（它点名的子脚本自己写 results/） | 由它调用的子脚本写，登记里没记这一步 |
| 32 | `package_hygiene`（后置格：读本趟刚落盘的矩阵） | 调 `check_package_hygiene.py` | UNKNOWN（点名的子脚本自己写不写 results/ 解不出） | UNKNOWN |

> 本块由 `tools/make_navigation.py` 生成：import `flow/run_all.py` 读到 `STEPS`=25 + `POST_STEPS`=7 ⇒ `N_TOTAL`=32；该模块此刻 sha256 前 16 位 = `fb4bac67299a0192`、mtime = `2026-10-17T06:43:43Z`。统计：写归档 22 步、不写归档 5 步、UNKNOWN 5 步（三个数相加 = N_TOTAL）。
> 「写归档 / 不写归档 / UNKNOWN」这一格的推法（三条按强弱排, 都不成立才 UNKNOWN）：① 这一步在 `results/HISTORICAL_ARCHIVES.json` 里被记为某份归档的 `produced_by`；② 它**在本模块内可达的源码**（含它调的 helper，如 `_bench` / `_check_suite`）里有写动作且目标是 `results/` 下的对象 —— 写原语与路径常隔一行，所以顺带认变量绑定，但替身房（`box` / `tempfile` / `rmtree`）里那份与纯只读语境（`jload` / `rd`）都不算；③ 它点名的子脚本（`.py` / `.sh`，盘上能定位到的）自己写 `results/`。子脚本名解不到盘上文件、或写动作的目标解不出来 ⇒ 印 `UNKNOWN`，绝不猜。
<!-- /FLOW_STEPS:auto -->
