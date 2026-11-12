# tools/ 目录说明：哪些是长期工具，哪些是一次性改稿脚本

<!-- BEGIN make_navigation:TOOLS-LAYERING (生成件, 勿手改本块) -->

| 层 | 数量 | 是什么 | 要不要跑 |
|---|---:|---|---|
| `tools/` 根 | 44 个 `.py` | 现役工具（门禁、归档汇综、生成器） | 要，`flow/run_all.py` 会跑到其中的门禁 |
| 一次性改稿笔 | 0 个还留在包里 | 历史上一次性用过的搬表/改措辞/打补丁脚本 | **不要**，照着重跑会改坏现役交付件；本轮起这类脚本不随包 |

两个数都由本块生成时现数目录得到。第二行是**政策回退计数器**：包内一次性改稿脚本的数量应当为 0，不为 0 就说明有历史脚本被放回了包里，而不是「目录内容变了」这么简单。

### 内部里程碑代号对照

| 代号 | 这一轮在做什么 | 名字出现在 |
|---|---|---|
| M6 | 硬件非对齐访存（跨字 load/store 的两相位状态机） | `tools/ab_m6_m7.py`、`results/pnr/run2s_*` 一类臂名 |
| M7 | 与 M6 的配对对照（改前/改后同一用例的逐位比对） | 同上 |
| M8 | 可写计数器与 PMP 的 WARL 语义（`rv32mi` 那批用例） | `tools/m9_paired_control.py` 的注释与 `docs/附件A` |
| M9 | 计数器写语义逐项探针与门禁收口轮 | `sw/bench/micro_bisect.c`、`docs/附件A` 的 M9 改动台账 |

代号是本包内部按里程碑顺序起的名字，对包外读者不可解 —— 所以这里给出对照，而不是把文件名改成中文（文件名被归档与门禁按名字点名）。
<!-- END make_navigation:TOOLS-LAYERING -->

## 这个目录是怎么分的

`tools/` 根下现役脚本的**数量与清单**以上方那块由 `tools/make_navigation.py` 现数的 TOOLS-LAYERING 表为准，这里不另记一份数（另记一份就会漂）。本文件由 `python tools/organize_legacy.py --apply` 生成：它只列目录里**现在真的在**的文件，所以过期了重跑一次就重新对齐，不靠人工回忆。

## 一次性改稿脚本（不随包）

这类脚本的作用是**把上一版文档改成当时那一版**：它们的锚点取自当时那份文档的字节，照着重跑会改坏现役交付件（会非零退出，这是设计如此）。本包现在的政策是把它们**整目录移出包**，而不是收进一个包内子目录，所以这里不再列它们的名字 —— 列了就是一份指向不存在之物的清单。包内如果又留着一支这样的脚本，上方那块现数的表里的「政策回退计数器」就不会是 0。


## 名字留在这里的脚本（留下的判据是「包内有文件按名字点过它」）

这条判据管的是**提及**而不是**依赖**：一支脚本被台账或某句复跑命令按名字提到，它就还有一份指向它的文字，搬走会让那句话指不到东西；但这不等于它每趟都会被跑到。哪一支真的在链条上被跑，看 `flow/README.md` 里那张由同一个生成器现数的步骤表，不看这一节。

- `ab_m6_m7.py`
- `ascii_probe_uart.py`
- `bench_archive.py`
- `bram_arms_build.py`
- `bram_arms_check.py`
- `bram_arms_make.py`
- `bram_arms_read.py`
- `check_archive_clobber.py`
- `check_encoding_consistency.py`
- `check_word_limits.py`
- `cifar_archive.py`
- `close_svb_debt_docs.py`
- `coverage_report.py`
- `defect_count_gate.py`
- `dump_official_template.py`
- `dump_ppt.py`
- `dump_ppt_md.py`
- `elf_manifest.py`
- `fixture_claims.py`
- `historical_archives.py`
- `instret_witness.py`
- `list_cjk_literals.py`
- `m9_paired_control.py`
- `make_navigation.py`
- `make_wrappers.py`
- `mem_sensitivity.py`
- `net_archive.py`
- `official_coverage.py`
- `organize_legacy.py`
- `peek_trace.py`
- `pnr_archive.py`
- `record_epv_trace_attempt.py`
- `record_soc_attempt.py`
- `refresh_gz_receipts.py`
- `shrink_trace.py`
- `snapshot_pre.py`
- `synth_archive.py`
- `third_party_licenses.py`
- `train_cifar_int8.py`
- `vcd_probe.py`
- `vcd_probe2.py`
- `verify_deliverables.py`
- `verify_ppt.py`
- `word_style_count.py`

## 为什么这么分

赛题 4.1.7② 的原文里有「可维护性和可拓展性」。一堆 `trim_s5c.py`混在门禁脚本旁边会让人以为整个工程是靠打补丁堆出来的 —— 实际上不是：证据链在 `flow/run_all.py` 的步表里。收纳的判据也不是名字：一支脚本只有在全包的引用面上**一次都没有出现**（外部组件与上游材料里的同名提及不算引用）才会被移进来，被引用到的一律留在原地并在 dry-run 里点名。收纳之后两件事同时成立：长期工具一眼可辨，历史动作仍可复现。
