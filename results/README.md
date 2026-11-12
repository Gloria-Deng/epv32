# `results/` —— 证据底座，以及「哪份是真源」

官方 4.1.5 一.3 要求「数据尽可能以图表或表格形式呈现，图表需标注数据来源」，
本目录就是那些来源的落盘处：**交付正文里每一个数都指向这里的一份归档**，
判定读的是归档字段而不是命令退出码（为什么不许看退出码，见 `../README.md` §四）。

## 一、真源 vs 副本 vs 历史对照

这是最容易被误读的一层，尤其 `results/pnr/*/rtl/` 那些与 `rtl/` **同名**的文件：

- **`../../rtl/` 是唯一真源。** 各臂目录里的那几份 `epv32_*.v` 是**综合器实际吃进去的输入
  快照**：跑某一臂时由 `../flow/make_td_arms.py` 逐字节 `copyfile` 进去的副本。
  它们与 `rtl/` 的一致性由 sha256 门禁保证（`../tools/pnr_archive.py` 逐文件记 `rtl/` 的
  sha256 作为新鲜度键，矩阵的 `pnr_gate` 只采信哈希等于当前工作树的那一份归档）。
- **副本不许"顺手同步"。** 一份已经落盘的归档里，`rtl/` 副本记录的是**那一趟真跑过的是
  什么**。把新文本刷进旧副本，等于伪造一次从没发生过的综合输入 —— 所以历史归档里的
  注释文本即便已经过期，也**保留原样**，过期这件事由本节说明而不是由改写掩盖。
  （实例：几份 `results/pnr/*/rtl/` 的注释里写着 `sw/inc/epv_intrinsics.h`，该路径现已
  是 `sw/inc/epv32_hw.h`。现役 `rtl/` 里这两处已按现名核对；归档副本按上述纪律不动。）
- **历史对照**集中在 `backup/`：某一轮改动之前的整份镜像（按原相对路径存），
  供改前/改后配对比较读取。它们是**读取端**，不是垃圾 —— 删掉它们，那条配对臂会
  静默失效而所有门禁照绿。

- **读数分三类，由一道门现算。** `../tools/historical_archives.py` 按 `rtl/` 的当前哈希把本目录每份归档分成「当前树复现的」「旧代取到、矩阵不再复跑的」「不记逐文件 rtl 指纹的」，逐份记在 `HISTORICAL_ARCHIVES.json` 里（类别、理由、历史那条属于哪一代的 16 位前缀）。正文引用一份**旧代或不记指纹**的归档时，那份必须在登记里 —— 读者因此能分辨「这一步每代重跑」与「某次一次性探针留下的读数」，而不必猜。

## 二、目录性质速查

| 子目录 | 是什么 | 谁写的 |
|---|---|---|
| `compliance/` | 官方 riscv-tests 三套件逐用例判决 | `../flow/rt.py` |
| `isco/` | 黄金模型 ↔ RTL 逐条退休轨迹双向比对（连两侧源码哈希一起记） | `../flow/iss_compare.py` |
| `iss/` | 黄金模型自检断言读数 | `../tb/golden/epv_iss.py` |
| `bench/` | 加速比、访存敏感性、CIFAR-10 在环 | `../flow/rt.py` |
| `synth/` `pnr/` | 安路 TD 两档综合与器件口径（含 `summary2.json` 的逐文件 sha256） | `../flow/make_td_arms.py`、`../tools/pnr_archive.py` |
| `lint/` `trace/` `wave/` | Verilator lint、退休轨迹、波形 | `../flow/lint_rtl.sh`、`../tb/sim_main.cpp` |
| `demo/` | Demo 成片与它的容器读数 | `../flow/make_demo_video.py` |
| `logs/` | 每一步的原始 stdout（**未认证的运行也留**，按 UTC 时刻命名） | `../flow/run_all.py` |
| `matrix.json` | 整趟矩阵的汇总结论 | `../flow/run_all.py` |
| `_probe_subset_NOT_A_VERDICT.json` | **只跑子集的探针产物，不是交付结论**（旧名与 `matrix.json` 只差一个词，2026-09-27 改名） | `../flow/run_all.py <步名>`（按步名过滤；本脚本没有 `--only` 这个开关，未知开关会被直接拒绝） |

`_probe_subset_NOT_A_VERDICT` 那一份为什么单独警示：它由「只跑几步」的调用写出，未跑的步在文件里
呈现为 SKIP，汇总行 `all_green` 必然为 False。把它当结论读会得出「矩阵崩了」，
而真正的结论只在 `matrix.json`。
