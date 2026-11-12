# `build/elf/` 清单：ELF ↔ 用例 ↔ 判决归档 ↔ 复跑入口

本表由 `tools/elf_manifest.py` 从盘上现算，不手写；每一列的存在性都由那一步
自己核对（归档不在盘上、生产者脚本不在盘上，都会判红而不是留空）。

| ELF | 用例 | 家族 | 判决归档 | 复跑入口 |
|---|---|---|---|---|
| `build\elf/chainab__00.elf` | `chainab__00` | probe | `results\sim\chainab__00_isco.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/epv_bisect.elf` | `epv_bisect` | bench | `results\sim\epv_bisect.json` | `flow\run_all.py` |
| `build\elf/epv_selftest.elf` | `epv_selftest` | bench | `results\sim\epv_selftest.json` | `flow\run_all.py` |
| `build\elf/micro_bisect.elf` | `micro_bisect` | bench | `results\sim\micro_bisect.json` | `flow\run_all.py` |
| `build\elf/minimal__repro.elf` | `minimal__repro` | probe | `results\sim\minimal__repro_isco.json` | `flow\run_all.py` |
| `build\elf/misalign_bisect.elf` | `misalign_bisect` | bench | `results\sim\misalign_bisect.json` | `flow\run_all.py` |
| `build\elf/nn_bench.elf` | `nn_bench` | bench | `results\sim\nn_bench.json` | `flow\run_all.py` |
| `build\elf/nn_cifar.elf` | `nn_cifar` | bench | `results\sim\nn_cifar.json` | `flow\run_all.py` |
| `build\elf/nn_net.elf` | `nn_net` | bench | `results\sim\nn_net.json` | `flow\run_all.py` |
| `build\elf/rand__p00.elf` | `rand__p00` | probe | `results\sim\rand__p00_isco.json` | `flow\run_all.py` |
| `build\elf/rand__p01.elf` | `rand__p01` | probe | `results\sim\rand__p01_isco.json` | `flow\run_all.py` |
| `build\elf/rand__p02.elf` | `rand__p02` | probe | `results\sim\rand__p02_isco.json` | `flow\run_all.py` |
| `build\elf/rem2__c00.elf` | `rem2__c00` | bench | `results\sim\rem2__c00.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/rem2__c01.elf` | `rem2__c01` | bench | `results\sim\rem2__c01.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/rem2__c02.elf` | `rem2__c02` | bench | `results\sim\rem2__c02.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/rem2__c03.elf` | `rem2__c03` | bench | `results\sim\rem2__c03.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/rem2__c04.elf` | `rem2__c04` | bench | `results\sim\rem2__c04.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/rem2__c05.elf` | `rem2__c05` | bench | `results\sim\rem2__c05.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/rem2__c06.elf` | `rem2__c06` | bench | `results\sim\rem2__c06.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/rem2__c07.elf` | `rem2__c07` | bench | `results\sim\rem2__c07.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/rem2__c08.elf` | `rem2__c08` | bench | `results\sim\rem2__c08.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/rem2__c09.elf` | `rem2__c09` | bench | `results\sim\rem2__c09.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/rem2__c10.elf` | `rem2__c10` | bench | `results\sim\rem2__c10.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/rem2__c11.elf` | `rem2__c11` | bench | `results\sim\rem2__c11.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/rem2__c12.elf` | `rem2__c12` | bench | `results\sim\rem2__c12.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/rv32mi__breakpoint.elf` | `rv32mi / breakpoint` | compliance | `results\compliance\rv32mi.json` 与 `results\sim\rv32mi__breakpoint.json` | `flow\rt.py` |
| `build\elf/rv32mi__csr.elf` | `rv32mi / csr` | compliance | `results\compliance\rv32mi.json` 与 `results\sim\rv32mi__csr.json` | `flow\rt.py` |
| `build\elf/rv32mi__illegal.elf` | `rv32mi / illegal` | compliance | `results\compliance\rv32mi.json` 与 `results\sim\rv32mi__illegal.json` | `flow\rt.py` |
| `build\elf/rv32mi__instret_overflow.elf` | `rv32mi / instret_overflow` | compliance | `results\compliance\rv32mi.json` 与 `results\sim\rv32mi__instret_overflow.json` | `flow\rt.py` |
| `build\elf/rv32mi__lh-misaligned.elf` | `rv32mi__lh-misaligned` | bench | `results\sim\rv32mi__lh-misaligned.json` 与 `results\sim\rv32mi__lh-misaligned_isco.json` | `flow\run_all.py` |
| `build\elf/rv32mi__lw-misaligned.elf` | `rv32mi__lw-misaligned` | bench | `results\sim\rv32mi__lw-misaligned.json` 与 `results\sim\rv32mi__lw-misaligned_isco.json` | `flow\run_all.py` |
| `build\elf/rv32mi__ma_addr.elf` | `rv32mi / ma_addr` | compliance | `results\compliance\rv32mi.json` 与 `results\sim\rv32mi__ma_addr.json` | `flow\rt.py` |
| `build\elf/rv32mi__ma_fetch.elf` | `rv32mi / ma_fetch` | compliance | `results\compliance\rv32mi.json` 与 `results\sim\rv32mi__ma_fetch.json` | `flow\rt.py` |
| `build\elf/rv32mi__mcsr.elf` | `rv32mi / mcsr` | compliance | `results\compliance\rv32mi.json` 与 `results\sim\rv32mi__mcsr.json` | `flow\rt.py` |
| `build\elf/rv32mi__pmpaddr.elf` | `rv32mi / pmpaddr` | compliance | `results\compliance\rv32mi.json` 与 `results\sim\rv32mi__pmpaddr.json` | `flow\rt.py` |
| `build\elf/rv32mi__sbreak.elf` | `rv32mi / sbreak` | compliance | `results\compliance\rv32mi.json` 与 `results\sim\rv32mi__sbreak.json` | `flow\rt.py` |
| `build\elf/rv32mi__scall.elf` | `rv32mi / scall` | compliance | `results\compliance\rv32mi.json` 与 `results\sim\rv32mi__scall.json` | `flow\rt.py` |
| `build\elf/rv32mi__sh-misaligned.elf` | `rv32mi__sh-misaligned` | bench | `results\sim\rv32mi__sh-misaligned.json` 与 `results\sim\rv32mi__sh-misaligned_isco.json` | `flow\run_all.py` |
| `build\elf/rv32mi__shamt.elf` | `rv32mi / shamt` | compliance | `results\compliance\rv32mi.json` 与 `results\sim\rv32mi__shamt.json` | `flow\rt.py` |
| `build\elf/rv32mi__sw-misaligned.elf` | `rv32mi__sw-misaligned` | bench | `results\sim\rv32mi__sw-misaligned.json` 与 `results\sim\rv32mi__sw-misaligned_isco.json` | `flow\run_all.py` |
| `build\elf/rv32mi__zicntr.elf` | `rv32mi / zicntr` | compliance | `results\compliance\rv32mi.json` 与 `results\sim\rv32mi__zicntr.json` | `flow\rt.py` |
| `build\elf/rv32ui__add.elf` | `rv32ui / add` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__add.json` | `flow\rt.py` |
| `build\elf/rv32ui__addi.elf` | `rv32ui / addi` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__addi.json` | `flow\rt.py` |
| `build\elf/rv32ui__and.elf` | `rv32ui / and` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__and.json` | `flow\rt.py` |
| `build\elf/rv32ui__andi.elf` | `rv32ui / andi` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__andi.json` | `flow\rt.py` |
| `build\elf/rv32ui__auipc.elf` | `rv32ui / auipc` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__auipc.json` | `flow\rt.py` |
| `build\elf/rv32ui__beq.elf` | `rv32ui / beq` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__beq.json` | `flow\rt.py` |
| `build\elf/rv32ui__bge.elf` | `rv32ui / bge` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__bge.json` | `flow\rt.py` |
| `build\elf/rv32ui__bgeu.elf` | `rv32ui / bgeu` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__bgeu.json` | `flow\rt.py` |
| `build\elf/rv32ui__blt.elf` | `rv32ui / blt` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__blt.json` | `flow\rt.py` |
| `build\elf/rv32ui__bltu.elf` | `rv32ui / bltu` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__bltu.json` | `flow\rt.py` |
| `build\elf/rv32ui__bne.elf` | `rv32ui / bne` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__bne.json` | `flow\rt.py` |
| `build\elf/rv32ui__fence_i.elf` | `rv32ui / fence_i` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__fence_i.json` | `flow\rt.py` |
| `build\elf/rv32ui__jal.elf` | `rv32ui / jal` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__jal.json` | `flow\rt.py` |
| `build\elf/rv32ui__jalr.elf` | `rv32ui / jalr` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__jalr.json` | `flow\rt.py` |
| `build\elf/rv32ui__lb.elf` | `rv32ui / lb` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__lb.json` | `flow\rt.py` |
| `build\elf/rv32ui__lbu.elf` | `rv32ui / lbu` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__lbu.json` | `flow\rt.py` |
| `build\elf/rv32ui__ld_st.elf` | `rv32ui / ld_st` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__ld_st.json` | `flow\rt.py` |
| `build\elf/rv32ui__lh.elf` | `rv32ui / lh` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__lh.json` | `flow\rt.py` |
| `build\elf/rv32ui__lhu.elf` | `rv32ui / lhu` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__lhu.json` | `flow\rt.py` |
| `build\elf/rv32ui__lui.elf` | `rv32ui / lui` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__lui.json` | `flow\rt.py` |
| `build\elf/rv32ui__lw.elf` | `rv32ui / lw` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__lw.json` | `flow\rt.py` |
| `build\elf/rv32ui__ma_data.elf` | `rv32ui / ma_data` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__ma_data.json` | `flow\rt.py` |
| `build\elf/rv32ui__or.elf` | `rv32ui / or` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__or.json` | `flow\rt.py` |
| `build\elf/rv32ui__ori.elf` | `rv32ui / ori` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__ori.json` | `flow\rt.py` |
| `build\elf/rv32ui__sb.elf` | `rv32ui / sb` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__sb.json` | `flow\rt.py` |
| `build\elf/rv32ui__sh.elf` | `rv32ui / sh` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__sh.json` | `flow\rt.py` |
| `build\elf/rv32ui__simple.elf` | `rv32ui / simple` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__simple.json` | `flow\rt.py` |
| `build\elf/rv32ui__sll.elf` | `rv32ui / sll` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__sll.json` | `flow\rt.py` |
| `build\elf/rv32ui__slli.elf` | `rv32ui / slli` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__slli.json` | `flow\rt.py` |
| `build\elf/rv32ui__slt.elf` | `rv32ui / slt` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__slt.json` | `flow\rt.py` |
| `build\elf/rv32ui__slti.elf` | `rv32ui / slti` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__slti.json` | `flow\rt.py` |
| `build\elf/rv32ui__sltiu.elf` | `rv32ui / sltiu` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__sltiu.json` | `flow\rt.py` |
| `build\elf/rv32ui__sltu.elf` | `rv32ui / sltu` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__sltu.json` | `flow\rt.py` |
| `build\elf/rv32ui__sra.elf` | `rv32ui / sra` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__sra.json` | `flow\rt.py` |
| `build\elf/rv32ui__srai.elf` | `rv32ui / srai` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__srai.json` | `flow\rt.py` |
| `build\elf/rv32ui__srl.elf` | `rv32ui / srl` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__srl.json` | `flow\rt.py` |
| `build\elf/rv32ui__srli.elf` | `rv32ui / srli` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__srli.json` | `flow\rt.py` |
| `build\elf/rv32ui__st_ld.elf` | `rv32ui / st_ld` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__st_ld.json` | `flow\rt.py` |
| `build\elf/rv32ui__sub.elf` | `rv32ui / sub` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__sub.json` | `flow\rt.py` |
| `build\elf/rv32ui__sw.elf` | `rv32ui / sw` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__sw.json` | `flow\rt.py` |
| `build\elf/rv32ui__xor.elf` | `rv32ui / xor` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__xor.json` | `flow\rt.py` |
| `build\elf/rv32ui__xori.elf` | `rv32ui / xori` | compliance | `results\compliance\rv32ui.json` 与 `results\sim\rv32ui__xori.json` | `flow\rt.py` |
| `build\elf/rv32um__div.elf` | `rv32um / div` | compliance | `results\compliance\rv32um.json` 与 `results\sim\rv32um__div.json` | `flow\rt.py` |
| `build\elf/rv32um__divu.elf` | `rv32um / divu` | compliance | `results\compliance\rv32um.json` 与 `results\sim\rv32um__divu.json` | `flow\rt.py` |
| `build\elf/rv32um__mul.elf` | `rv32um / mul` | compliance | `results\compliance\rv32um.json` 与 `results\sim\rv32um__mul.json` | `flow\rt.py` |
| `build\elf/rv32um__mulh.elf` | `rv32um / mulh` | compliance | `results\compliance\rv32um.json` 与 `results\sim\rv32um__mulh.json` | `flow\rt.py` |
| `build\elf/rv32um__mulhsu.elf` | `rv32um / mulhsu` | compliance | `results\compliance\rv32um.json` 与 `results\sim\rv32um__mulhsu.json` | `flow\rt.py` |
| `build\elf/rv32um__mulhu.elf` | `rv32um / mulhu` | compliance | `results\compliance\rv32um.json` 与 `results\sim\rv32um__mulhu.json` | `flow\rt.py` |
| `build\elf/rv32um__rem.elf` | `rv32um / rem` | compliance | `results\compliance\rv32um.json` 与 `results\sim\rv32um__rem.json` | `flow\rt.py` |
| `build\elf/rv32um__remu.elf` | `rv32um / remu` | compliance | `results\compliance\rv32um.json` 与 `results\sim\rv32um__remu.json` | `flow\rt.py` |
| `build\elf/shrink__0001.elf` | `shrink__0001` | probe | `results\sim\shrink__0001_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0002.elf` | `shrink__0002` | probe | `results\sim\shrink__0002_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0003.elf` | `shrink__0003` | probe | `results\sim\shrink__0003_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0004.elf` | `shrink__0004` | probe | `results\sim\shrink__0004_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0005.elf` | `shrink__0005` | probe | `results\sim\shrink__0005_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0006.elf` | `shrink__0006` | probe | `results\sim\shrink__0006_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0007.elf` | `shrink__0007` | probe | `results\sim\shrink__0007_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0008.elf` | `shrink__0008` | probe | `results\sim\shrink__0008_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0009.elf` | `shrink__0009` | probe | `results\sim\shrink__0009_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0010.elf` | `shrink__0010` | probe | `results\sim\shrink__0010_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0011.elf` | `shrink__0011` | probe | `results\sim\shrink__0011_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0012.elf` | `shrink__0012` | probe | `results\sim\shrink__0012_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0013.elf` | `shrink__0013` | probe | `results\sim\shrink__0013_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0014.elf` | `shrink__0014` | probe | `results\sim\shrink__0014_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0015.elf` | `shrink__0015` | probe | `results\sim\shrink__0015_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0016.elf` | `shrink__0016` | probe | `results\sim\shrink__0016_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0017.elf` | `shrink__0017` | probe | `results\sim\shrink__0017_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0018.elf` | `shrink__0018` | probe | `results\sim\shrink__0018_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0019.elf` | `shrink__0019` | probe | `results\sim\shrink__0019_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0020.elf` | `shrink__0020` | probe | `results\sim\shrink__0020_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0021.elf` | `shrink__0021` | probe | `results\sim\shrink__0021_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0022.elf` | `shrink__0022` | probe | `results\sim\shrink__0022_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0023.elf` | `shrink__0023` | probe | `results\sim\shrink__0023_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0024.elf` | `shrink__0024` | probe | `results\sim\shrink__0024_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0025.elf` | `shrink__0025` | probe | `results\sim\shrink__0025_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0026.elf` | `shrink__0026` | probe | `results\sim\shrink__0026_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0027.elf` | `shrink__0027` | probe | `results\sim\shrink__0027_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0028.elf` | `shrink__0028` | probe | `results\sim\shrink__0028_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0029.elf` | `shrink__0029` | probe | `results\sim\shrink__0029_isco.json` | `flow\run_all.py` |
| `build\elf/shrink__0030.elf` | `shrink__0030` | probe | `results\sim\shrink__0030_isco.json` | `flow\run_all.py` |
| `build\elf/signab__00.elf` | `signab__00` | probe | `results\sim\signab__00_isco.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/signab__01.elf` | `signab__01` | probe | `results\sim\signab__01_isco.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/signab__02.elf` | `signab__02` | probe | `results\sim\signab__02_isco.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/signab__03.elf` | `signab__03` | probe | `results\sim\signab__03_isco.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/signab__04.elf` | `signab__04` | probe | `results\sim\signab__04_isco.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/signab__05.elf` | `signab__05` | probe | `results\sim\signab__05_isco.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/signab__06.elf` | `signab__06` | probe | `results\sim\signab__06_isco.json` | `NO_ACTIVE_PRODUCER` |
| `build\elf/signab__07.elf` | `signab__07` | probe | `results\sim\signab__07_isco.json` | `NO_ACTIVE_PRODUCER` |

合计：ELF `129`，映射 `129`，未映射 `0`，无现役生产者 `22`（逐条名单在 `results/elf_manifest.json` 的 `no_active_producer` 键里）。
