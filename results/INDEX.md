# results/ 阅读顺序（生成件，勿手改）

> 由 `python tools/make_navigation.py --write` 生成；`--check` 会核对盘上这份
> 与现在重生成的那份是否逐字节相同。体积、文件数、时刻全部由落盘这一刻走一遍
> 树算出，写完还把本文件读回来把每个数再核一次（对不上就点名 FIGURE-* 判红），
> 只有「这一格是什么角色」那句话归人写。

- 本目录的**唯一交付结论**是 `matrix.json`：`started_utc=2026-10-17T06:52:04Z`，`n_pass=32`，`failed=0`，`skipped=0`，`steps=32`，`all_green=True`，`ran_subset=False`。
- 上面这行属于**哪一趟**由这份文件自己的落盘时刻决定：`matrix.json` mtime = `2026-10-17T07:01:49Z`；它之后盘上若还落下别的证据，
  就说明本索引是在矩阵落盘**之前**写的，读到的结论属于上一趟。
- 这条判据另有具名一档：Demo 驱动的尾部产物 3 份（`results/demo/run.log`、`results/demo/demo_video.mp4`、`results/demo/demo_video.json`）不算「晚于判决的新证据」——按最终矩阵重出成片是 sidecar 指向现役矩阵的唯一次序，而这三份与现役归档是否同源由 `flow/check_demo_artifacts.py` 按内容判（J3 逐键读数 / J4 rtl 头 / J5 逐格判决），不靠 mtime。
- 本目录树里最新的证据（剔掉本工具的索引、人写的 README.md 与判决 json）：`results/demo/09_matrix.txt` @ `2026-10-17T06:58:05Z`。
- 引用任何一个数之前先核这一行的 `started_utc` 是不是你关心的那一趟；
  链条只在末尾落盘，跑动期间盘上这份属于**上一趟**。

| 先看/再看 | 名称 | 角色 | 文件数 | 体积 | 为什么 |
|---|---|---|---:|---:|---|
| ① | `matrix.json` | 现行结论 | 1 | 0.0 MiB | 唯一交付结论: 整表跑完那一趟的判决。格数与 pass/fail 由链条自己写进这份 json 的 n_total_in_table / n_pass / failed 三个键, 本索引不抄数; 只由 `flow/run_all.py` 不带步名地整表跑完时写 |
| ② | `_probe_subset_NOT_A_VERDICT.json` | 子集探针产物 | 1 | 0.0 MiB | 按**步名**过滤的那一趟写这里而不是 matrix.json (名字里就写着「这不是判决」; 它以前叫 matrix_subset.json, 与认证归档只差一个词, 误读风险实测存在)。用法是 `python flow/run_all.py <步名...>`; 本脚本没有 --only 这个开关, 未知开关会被直接拒绝); 里面的 all_green 为 false 只代表那趟只跑了几格, 不是矩阵崩了 |
| ③ | `HISTORICAL_ARCHIVES.json` | 现行登记 | 1 | 0.0 MiB | results/ 读数按 RTL 哈希分三类 (当前树复现 / 旧代历史 / 不记指纹), 由 tools/historical_archives.py 现填 |
| ④ | `README.md` | 现行说明 | 1 | 0.0 MiB | 本目录的读法与「真源 vs 副本 vs 历史对照」的分工 |
| ⑤ | `sim/` | 现行证据 | 216 | 0.5 MiB | 逐用例判决归档（随机流那一档的读数在 `rand_trace/`，EPV 单元级台架没有） |
| ⑥ | `isco/` | 现行证据 | 3 | 0.0 MiB | 黄金 ISS 与核的逐条退休轨迹比对, 连两侧源码哈希一起记 |
| ⑦ | `compliance/` | 现行证据 | 3 | 0.0 MiB | 官方 riscv-tests 三套件逐用例结果 |
| ⑧ | `bench/` | 现行证据 | 7 | 0.1 MiB | 周期数与加速比归档 |
| ⑨ | `synth/` | 现行证据 | 6 | 0.4 MiB | Yosys 面积口径归档 |
| ⑩ | `pnr/` | 现行证据 + 历史臂 | 699 | 63.3 MiB | 安路 TD 两档综合与布线读数; 其中的 rtl/ 是综合器实际吃进去的快照, 不许「顺手同步」成现役树 |
| ⑪ | `lint/` | 现行证据 | 2 | 0.0 MiB | Verilator 两档 (EPV_ENABLE 开/关) 0 error 0 warning |
| ⑫ | `logs/` | 过程日志 | 90 | 0.2 MiB | 矩阵与探针的原始 stdout; 未认证的运行也留, 按 UTC 命名 |
| ⑬ | `trace/` | 可再生中间件 | 219 | 68.0 MiB | 全量逐周期退休轨迹 (.txt.gz); 证据的压缩形态, 复跑即再生 |
| ⑭ | `cov/` | 现行证据 | 1 | 0.0 MiB | 覆盖率臂的 line/toggle 汇总 (本轮新增) |
| ⑮ | `rand_trace/` | 现行证据 | 2 | 0.0 MiB | 约束随机指令流与黄金 ISS 的逐条比对 (本轮新增) |
| ⑯ | `asserts/` | 现行证据 | 1 | 0.0 MiB | 断言注入臂 (发数由 tools/defect_count_gate.py 与 flow/assert_selfcheck.py 的判决行现算, 本索引不抄) 的判决存档, 带它当时的逐文件 rtl 指纹; 正向臂那一步会核这份存档属不属于当前这一代 |
| ⑰ | `demo/` | 现行证据 | 25 | 6.0 MiB | 成片与逐段产物 |
| ⑱ | `wave/` | 现行证据 | 4 | 1.0 MiB | VCD 与压缩件 + 收条 |
| ⑲ | `backup/` | 历史对照 | 356 | 1.2 MiB | 各轮换代前的整份镜像; 配对对照的旧侧数据源, 不是垃圾 |
| ⑳ | `iss/` | 现行证据 | 1 | 0.0 MiB | 黄金 ISS 自测的聚合判决 (assertions/failed/rc + 那份 .py 的哈希头), 由 `flow/run_all.py` 的 iss_selftest 步现写 |
| 21 | `isco_probe/` | 现行证据 | 2 | 0.0 MiB | EPV 指令级的协同仿真探针产物, 由 `flow/iss_compare_epv.py` 写 |
| 22 | `ppt/` | 现行证据 | 1 | 0.0 MiB | PPT 逐页核对的机器读数, 由 `tools/verify_ppt.py` 写 |
| 23 | `traces/` | 可再生中间件 | 1 | 0.0 MiB | 轨迹收成器自己的收条 (收成后的清单与省下字节数), 由 `flow/compact_traces.py` 写 |
| 24 | `elf_manifest.json` | 现行登记 | 1 | 0.0 MiB | build/ 里每个 ELF 指到哪个用例与哪份判决归档, 由 `tools/elf_manifest.py` 现填 |
| 25 | `elf_manifest.md` | 现行登记 | 1 | 0.0 MiB | 上一那条 json 的人读视图, 同一个生产者 |
| 26 | `word_limits.json` | 现行证据 | 1 | 0.0 MiB | 交付正文逐节字数读数, 由 `tools/check_word_limits.py` 写 |
| 27 | `word_style.json` | 现行证据 | 1 | 0.0 MiB | 字数与文体的第二口径读数, 由 `tools/word_style_count.py` 写 |
| 28 | `soc_td_run.log` | 未认领 | 1 | 0.0 MiB | **盘上有、但本包没有任何生产者点名写过它** —— 一条手工/一次性运行留下的 stdout, 既不在链条步骤表里也没有 writer; 本轮只登记这个事实, 不替它编角色 |

## 没被上面点到的条目（自动发现，条数由本表现算）

（无 —— 本目录每一项都在上面有归属；这一句本身是判据：出现新目录而本表没更新时，本节会先列出它，然后下面那条守恒式的未登记计数就非零）

守恒式：本目录共 1649 个文件 / 140.9 MiB；上面登记了 28 项，未登记 0 项；逐条目相加 1649 个文件（与前者不等即判红）。登记表里指向盘上不存在对象的行有 0 条：（无）。
