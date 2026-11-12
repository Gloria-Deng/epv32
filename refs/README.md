# refs/ 说明（生成件，勿手改）

> 由 `python tools/make_navigation.py --write` 生成。`refs/riscv-tests/` 是上游
> 仓库的完整拷贝（文件数见下面那一行，由本件现走树数出来），本包真正读到的只有
> 其中一小部分；哪些被点名不靠人记，靠下面在 `flow/` 与 `tools/` 源码里搜出来的
> 路径片段 —— 但**只有解析到盘上真实对象的那几条才会被写进来**。

## 本目录顶层

- `README.md` — 文件数 1，体积 0.0 MiB
- `THIRD_PARTY_LICENSES` — 文件数 12，体积 0.2 MiB
- `riscv-tests` — 文件数 1416，体积 3.1 MiB
- `rt.tar.gz` — 文件数 1，体积 0.5 MiB

`rt.tar.gz` 与 `riscv-tests/` 的关系：前者是来源包，后者是它解出的结果。
两份都在是因为交付时要能证明解出来的树与归档对得上（`verify-unpacked-tree`那一类判据），不是因为内容重复。

## 被 flow/ 与 tools/ 源码点名的 refs/ 子路径

这一节只列**解析得到**的片段：以前是把搜到的片段直接拼在 `refs/` 后面写出来，
于是盘上没有的东西（含一条纯垃圾片段）也进了索引而无人发现。现在片段按包内路径
解析，目录要 `isdir`、文件要 `isfile`，并且逐段验名（`refs/...` 这种占位片段在
Windows 上会被折叠成 `refs` 目录本身，所以形状守卫先把它挡掉）。

- `refs/README.md` — 已核为盘上文件，← flow/check_package_hygiene.py, flow/make_submission_package.py
- `refs/THIRD_PARTY_LICENSES` — 已核为盘上目录，← flow/make_review_bundle.py, tools/third_party_licenses.py
- `refs/riscv-tests` — 已核为盘上目录，← flow/check_package_hygiene.py, flow/make_review_bundle.py
- `refs/rt.tar.gz` — 已核为盘上文件，← flow/check_package_hygiene.py, flow/make_review_bundle.py

listed=4 resolved=4 unmatched=0 rejected=4 found=8

`unmatched` 是「印出来了但解析不到」的条数，它非零本件就非零退出；`rejected` 是
语料里点到但盘上没有、因而**没有**被写进本节的片段数（散文里的举例、占位串、
以及挂在 `docs/refs/` 下却被写成 `refs/` 的片段都落在这一档）。


## 第三方许可全文

`refs/THIRD_PARTY_LICENSES/` 下 6 个子目录：binutils, gcc, mingw-crt, riscv-tests, verilator, yosys。
收编与「拿不到的必须显式声明」的判据在 `tools/third_party_licenses.py`（`LICENSE_GATE_OK`）。
