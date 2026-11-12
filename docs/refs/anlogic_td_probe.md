# Anlogic TD 6.2.168.116 命令行/脚本化探查记录 (probe log)

- 目标安装: `E:\Anlogic\TD_6.2.1_Engineer_6.2.168.116`
- 版本自报: `Tang Dynasty, V6.2.168116` / `Built at = 16:32:07 Jun 12 2025`
- 探查日期: 2026-09-24，主机 `LAPTOP-ROVJN0UC`，用户 `lenovo`
- 本次为**只读探查**：未修改安装目录任何文件；所有实验写在
  `C:\Users\lenovo\AppData\Local\Temp\td_probe_scratch\`（scratch 目录，见文末）
- 内嵌脚本引擎: Tcl 8.6 (`tcl_patchLevel = 8.6.15`)，`OS = Windows NT 11.0`

> 重要环境坑（已实测）：TD 输出与本项目文档中的中文一旦进 stdout 会触发
> `'gbk' codec can't encode character`。本文件所有结论均来自**先写 UTF-8 文件再读回**，
> 不依赖终端回显。实验过程中确实炸过一次（`UnicodeEncodeError: 'gbk' ... '\uf0d8'`）。

---

## 1. 批量 / 非交互模式：真实可用的调用方式

### 1.1 结论（先给答案）

| 调用形式 | 是否执行脚本 | 是否打印 `%` 提示符 | 退出码 |
|---|---|---|---|
| `run.exe -b script.tcl` | **是** | 否 | 0 / 失败时 1 |
| `run.exe script.tcl`（裸位置参数） | **是**（等价） | 否 | 0 / 失败时 1 |
| `echo "cmd" \| run.exe`（stdin） | **是** | 否 | 0 |
| `run.exe < script.tcl`（stdin 重定向） | 是（同 stdin） | 否 | 0 |
| `run.exe -f t.tcl` | **否**（未知 flag 被忽略，退回交互） | 是 `% ` | 0 |
| `run.exe -s / --tcl / -batch / -no_gui / -c / -e / -q / -n / -h / --help` | **否** | 是 `% ` | 0 |

**判据不是退出码，而是"有没有打印 `% `"。** 上面所有未知 flag 都返回 `rc=0`，
所以只看 `$?` 会误判为成功（这是本次最容易踩的假绿）。真正区分方式是：
batch 模式跑完不打印交互提示符 `% `；未知 flag 会打印 `% `。

注意 `--help` **并不会**打印用法，它只是让程序进交互壳然后 EOF 退出。
`-b` 也**没有**在随附 PDF 里被文档化（见 §1.4）。

### 1.2 实测命令与逐字输出

Git-Bash 里直接跑（`bin` 已在 PATH 上）：

```sh
$ printf 'puts "BARE-DIR-TEST-9L"\n' > t9.tcl
$ timeout 60 run.exe -b t9.tcl < /dev/null ; echo $?
   ...banner...
============================================================
BARE-DIR-TEST-9L
0
```

```sh
$ echo 'puts "HELLO-FROM-STDIN-4T"; exit' | timeout 60 run.exe
   ...banner...
============================================================
HELLO-FROM-STDIN-4T
$ echo $?
0
```

缺失文件（证明 `-b` 之后那个参数真的被当脚本读取，而不是被忽略）：

```sh
$ timeout 60 run.exe -b nosuch.tcl < /dev/null ; echo $?
   ...banner...
============================================================
couldn't read file "nosuch.tcl": no such file or directory
1
```

Tcl 运行期错误（stderr 带栈，且 **rc=1**，可用于 Makefile/CI 判失败）：

脚本 `s_err.tcl`：`puts "BEFORE-ERR"` / `error "BOOM-9h3"` / `puts "AFTER-ERR"`

```
$ run.exe -b s_err.tcl
RC = 1
stdout: BEFORE-ERR
stderr: BOOM-9h3
            while executing
        "error "BOOM-9h3""
            (file "s_err.tcl" line 2)
```

语法错误同样 `rc=1`：`missing "` / `(file "s_badsyntax.tcl" line 1)`。

### 1.3 语义细节（实测）

- `run.exe -b a.tcl b.tcl` → **只跑第一个** `a.tcl`（`AAA-RAN`，`BBB-RAN` 不出现）。
- `run.exe a.tcl -b` → 也跑 `a.tcl`。即"第一个非 flag 参数被 source"。
- `run.exe -b`（无参数）→ 退回交互，打印 `% `。
- **`run.exe` 不会因为 stdin 而永久挂起**：stdin 为 `/dev/null` 时立即 EOF 退出（实测 0.1–0.3 s）。
  所以只有"交互式无参数且 stdin 是 tty"才会卡住；脚本里加 `timeout 60` 仍然稳妥。
- `run.exe` 与 `td_commands_prompt.exe` 大小完全相同（76,680,976 B）但 SHA256 不同
  （`fa66196c…` vs `e2e04ada…`）；`bw.exe` / `bw_commands_prompt.exe` 同理。
  即 `run.exe` 就是 TD 的命令行 Tcl 壳（GUI 是另一个 `td.exe`，114,448,144 B）。

### 1.4 官方文档怎么说（与实测对照）

`doc\SWUG\SWUG105_TCL_Command_User_Guide.pdf` 第 2.2 节 "TD 命令模式下运行"：

> 1. 在 Windows 下启动 TD CMD 窗口：Start → All Programs → **td_commands_prompt**
> 2. 运行 Tcl 命令：在此 CMD 窗口中可直接运行单独的 Tcl 命令；
> 3. 运行 Tcl 脚本：进入 Tcl 脚本所在目录，执行命令 **`source demo.tcl`**

`doc\TD_User_Guide_V6.2.pdf` 第 8 页（Linux 侧）：

> 界面模式：`/td_install_dir/td -gui`
> 命令模式：`/td_install_dir/td`

即：Windows 上"命令模式"= `run.exe` / `td_commands_prompt.exe`。
**`-b` 在全部 PDF 里 0 次出现**（全文检索 `-b` / `run.exe` / `batch` 均为 0 命中）；
官方推荐的脚本化路径是"进壳再 `source`"。但实测 `-b <file>` 与裸 `<file>` 等价且更好用。

### 1.5 推荐给脚本用的包装

```sh
run_one() {                      # $1 = tcl script, cwd 已 cd 到工程目录
  timeout "${TMO:-1800}" run.exe -b "$1" >"$1.out" 2>"$1.err"
  rc=$?
  if [ $rc -ne 0 ]; then echo "TD FAILED rc=$rc see $1.err"; tail -20 "$1.err"; return $rc; fi
  grep -q 'ERROR' "$1.out" && { echo "TD printed ERROR"; return 1; }   # 见 §4.3：ERROR 字样不一定代表失败
  return 0
}
```

---

## 2. 从 Verilog 源码到 bitstream 的完整脚本流程

### 2.1 两种模式

TD 有两套脚本流程，都在 `SWUG105` 第 3 章：

- **3.1 工程模式 (project mode)**：`import_device` → `create_project` → `open_project` →
  `top_module` → `add_files` → `create_run` → `set_param -run` → `launch_runs` → `wait_run` → …
  会生成 `.al` 工程与 run 目录，GUI 用的就是这套。
- **3.2 非工程模式 (non-project mode)**：**推荐用于命令行/CI**。不需要 `.al`、不需要
  `settings.cfg`，一条直线把综合到出流走完。

随装的 GUI 流程脚本在 `doc\scripts\`：`DefaultFlow.tcl`（249 行，主流程）、
`MultiSeed.tcl`、`PRFlow.tcl`（部分重配）、`SynIpFlow.tcl`。
它们 `source ./settings.cfg`，而 **`template/template.cfg` 是 AES128 加密的**
（`` `pragma protect encrypt_agent = "Anlogic" `` + RSA key block，185,068 B 密文），
不可读也不可自造 —— 这就是**不要照抄 `DefaultFlow.tcl`、而要用 §2.2 非工程模式**的原因。

`DefaultFlow.tcl` 里的 step 列表（原文第 46 行）：

```tcl
set stepList {read_design opt_rtl opt_gate opt_place opt_route bitgen}
```

其 "Editable zone" 内的真实命令序列：
`import_device` → `open_project`/`import_db` → `elaborate -top` → `read_adc`/`read_sdc`
→ `insert_debugger` → `optimize_rtl` → `report_area` → `optimize_gate` → `legalize_phy_inst`
→ `update_timing` → `report_timing_status` / `report_timing_summary` → `place`
→ `route` → `bitgen -bit $prj_name.bit`。

### 2.2 我实测跑通的完整流程（非工程模式，端到端出 bit）

工作目录 `...\td_probe_scratch\flow1\`，输入取自随板例程
`board_designs\ANLOGIC\AP102\LED_AP102\`（`LED_Example_Top.v` + `LED.adc` + `LED.sdc`）。
器件 = `PH1A180SFG676`（`board_designs\boardinfor.xml` 指明 AP102 = family `ph1`,
device `PH1A180SFG676`, speed `2`, devicedb `ph1_180.db`）。

`flow.tcl`：

```tcl
import_device ph1_180.db -package PH1A180SFG676 -speed 2
read_verilog -file {./LED_Example_Top.v} -top LED_Example_Top
read_adc ./LED.adc
optimize_rtl
report_area -file rtl.area
read_sdc ./LED.sdc
optimize_gate
legalize_phy_inst
read_sdc ./LED.sdc
export_db ./gate.db
report_qor -step gate -file gate.qor
report_area -file gate.area
place
report_qor -step place -file place.qor
update_timing -mode manhattan
report_timing_summary -file place_timing.rpt
route
export_db ./route.db
report_qor -step route -file route.qor
report_area -file route.area
update_timing -mode final
report_timing_summary -file route_timing.rpt
report_timing_status -file route.ts
bitgen -bit ./LED.bit
```

实测：`run.exe -b flow.tcl` → **rc = 0，stderr 空，耗时 45 s**，产出：

```
MARK-01-import_device-OK … MARK-09-bitgen-OK, MARK-10-FLOW-DONE-ZX8
BITSTREAM-SIZE=9424375
文件: LED.bit(9,424,375) gate.db(259,908) route.db(322,158) rtl.area gate.area route.area
      gate.qor place.qor route.qor place_timing.rpt route_timing.rpt final_timing.rpt route.ts
```

（`read_hdl` 与 `read_verilog` 两条命令都在；文档 §4.3.1 的"命令示例"行本身就写的是
`read_verilog -file {...} -top top`，与命令名 `read_hdl` 不一致 —— 实测两个都存在，可互换。）

### 2.3 约束文件格式（随装例程原文）

引脚约束 `.adc`（`source_code\constraints\LED.adc`）：

```tcl
set_pin_assignment  { I_sysclk_p } { LOCATION = G22; IOSTANDARD = LVCMOS33;}
set_pin_assignment  { I_rstn     } { LOCATION = H9;  IOSTANDARD = LVCMOS18; }
set_pin_assignment  { O_led[0]   } { LOCATION = H23; IOSTANDARD = LVCMOS33; }
```

时序约束 `.sdc`（`LED.sdc`，全文只有一行）：

```tcl
create_clock -name sysclk -period 40.000 [get_ports {I_sysclk_p}]
```

注意 `.adc` 里的 `set_pin_assignment` 本身就是 Tcl 命令：`read_adc` 会逐行 dispatch 它
（日志里能看到 10 条 `start command "set_pin_assignment"`）。
本 install 的约束扩展名是 **`.adc` + `.sdc`**，没有 `.fdc`（全树 36 个 `.adc`、29 个 `.sdc`、**0 个 `.fdc`、0 个 `.tdf`、0 个 `.prj` 实体文件**）。

`prj` 目录里的工程文件其实是 **`files.list`**（FileSet 语法），例：

```
FileSet "DESIGN_DEFAULT" { Verilog { ../../source_code/rtl/LED_Example_Top.v } }
FileSet "constraint_1"   { ADC_FILE { ../../source_code/constraints/LED.adc }
                           SDC_FILE { ../../source_code/constraints/LED.sdc } }
```

### 2.4 例程清单

`board_designs\ANLOGIC\` 下有 3 块板 × 4 个例程 = 12 套，每套结构一致
（`source_code/rtl/*.v` + `source_code/constraints/*.{adc,sdc}` + `sim/`）：
`AP102/{DNA,LED,SEU,UART}_AP102`、`AP104/*`、`AP106/*`。
IP 例化模板 1,031 个 `.v` + 92 个 `.vhd` 在 `ip\` 与 `ip\depot\`。
**没有** Makefile / `.sh` / `.bat` / 顶层 tutorial 目录 —— 自动化要自己写。

### 2.5 后续下载/烧写

`bitgen` 出 `.bit` 之后：`bw.exe`（BitWizard, `SWUG110`）、`mpack.exe`（Module Packager, `SWUG507`）、
`hwserver.exe`、`alps.exe`、`svf2aje.exe`。

- `mpack.exe`（无参/带任意 flag）：`mpack V2025.06 (APP main) … Please enter the correct app path`，`rc=0`。
- `svf2aje.exe --help`：`rc=4294967276`，输出正规 usage：
  `svf2aje [ -help | -svffile <svf> [ -ajefile <aje> ] [ -full ] [ -comment ] [ -header <s> ] ]`，
  版本 `Svf2Aje Version 6.4.0.38014`。
- **`bw.exe` 会挂死（连 `--help` 和 `-h` 都不返回，25 s 超时被 kill，两次都这样）**。
  它自报 `BitWriter, V1.2.168116`（GUI 烧写器；文档标题写作 "BitWizard"）。
  副作用：它会把 banner 日志写到 **exe 自己旁边的 `bin\log\`**，跟你的 cwd 无关 ——
  所以哪怕是"只是问它要 usage"也会往安装目录落文件（本次落了 4 个 400 B 的
  `bw_*.log`/`.logw`，纯 banner；详见 §8）。其他 GUI 件同理，一律 `timeout`。
- **`image2aje.exe` 不存在**（全树 `*aje*` 只有 `bin\svf2aje.exe` 和 `lib\tcl8.6\tzdata\Europe\Sarajevo`）。
- 其他 GUI/工具：`IpTool.exe`、`hpfTool.exe`、`design_integrator.exe`、`alps.exe`、
  `design_integrator.exe` 对应 `SWUG501`，`hpfTool` 对应 `SWUG503`。

---

## 3. 本 install 支持哪些器件族，怎么选

### 3.1 权威来源：`run.exe` 自带的 `list_devices` 命令

这是**唯一完整且与二进制一致的清单**（PDF 附录一只有 PH1/PH2 两族，已过期，见 §5.3）。

```tcl
list_devices
```

输出（节选，完整 74 行见 §3.2）：

```
RUN-1001 : Device Table
   Family  |   Device    |     Package      |  Speed
   dr1    |   dr1_90    |   DR1M90GEG400   |   2/3
   ef2    |    ef2_1    |   EF2L15BG256B   |   NA
   eg4    |  eagle_15   |   EG4A15BG256    |   NA
   eg4    |  eagle_s20  |   EG4S20BG256    |   NA
   ph1    |   ph1_180   |  PH1A180SFG676   |   2/3
   ph2    |   ph2_106   |  PH2A106FLG676   |  2/3/1
   sf1    |    sf1_6    |   SF1N60VG81C    |   NA
```

**10 个 family**：`dr1, ef2, ef3, ef4, eg4, ph1, ph2, sf1, sf2` （+ 隐含的
`eagle` = `eg4` 的 db 前缀名）。共 **74 个 package/part**，**28 个 db**。

### 3.2 选择器件的命令

```tcl
import_device <db_name> -package <PART> [-speed <grade>] [-basic] [-ext <path>]
```

`import_device -help` 的运行时原文：

```
import_device                     db_name -package <string> -speed <string> -basic -ext
[db_name]                         Imported device name
[-package]                        To set the pkg type of current device.
[-speed]                          User speed
[-basic]                          To skip dr info import.
[-ext]                            User specified db path.
```

- `db_name` 写 `ph1_180.db` 或 `ph1_180` 都可以（`list_devices` 的 Device 列就是去掉了 `.db`）。
- `-ext` 允许指定别处的 db 目录。
- 工程模式另用 `create_project <name> <dir> -family ph1 -device PH1A100SFG676 -speed 2`。
- 默认器件（GUI 新建工程时）：文档 `TD_User_Guide` p.9 写 "默认为 DR1 和 DR1M90GEG400"。

db 文件位置与体量（`arch\*.db`，共 29 个含 `system.db` 21,340,160 B）：
最小 `ef2_1.db` 380 KB，最大 `ph2_106.db` **66,978,146 B**，`dr1_90.db` 10,073,628 B。

### 3.3 完整器件表（`list_devices` × `arch\device_resource\device_resource.csv` 合并，74 行全部匹配上 LUT 数）

LUT/DFF/IO/DSP 取自 `arch\device_resource\device_resource.csv`（75 行含表头，列头
`Family,Device,User IOs,LUTs,DFFs,DSPs,PLLs,ERAMs(Kbits),DRAMs(Kbits),URAMs(Kbits),…`）。
交叉校验：`route.area` 报 `#lut6 52 out of 116800`，与 CSV 里 `PH1A180SFG676 = 116800` 一致 ——
**CSV 的 LUTs 列就是 TD P&R 实际使用的分母**。

| family | import_device db | package / part | speed | LUTs | DFFs | IOs | DSPs |
|---|---|---|---|---|---|---|---|
| dr1 | dr1_90.db | DR1M90GEG400 | 2/3 | 52480 | 104960 | 126 | 240 |
| dr1 | dr1_90.db | DR1M90GEG484 | 2/3 | 52480 | 104960 | 201 | 240 |
| dr1 | dr1_90.db | DR1M90GEG484A | 3 | 52480 | 104960 | 201 | 240 |
| dr1 | dr1_90.db | DR1M90MEG484 | 2/3 | 52480 | 104960 | 175 | 240 |
| dr1 | dr1_90.db | DR1V90GEG400 | 2/3 | 52480 | 104960 | 126 | 240 |
| dr1 | dr1_90.db | DR1V90GEG484 | 2/3 | 52480 | 104960 | 201 | 240 |
| dr1 | dr1_90.db | DR1V90MEG484 | 2/3 | 52480 | 104960 | 175 | 240 |
| ef2 | ef2_1.db | EF2L15BG256B | NA | 1520 | 1520 | 202 | 8 |
| ef2 | ef2_1.db | EF2L15LG100B | NA | 1520 | 1520 | 76 | 8 |
| ef2 | ef2_1.db | EF2L15LG144B | NA | 1520 | 1520 | 109 | 8 |
| ef2 | ef2_2.db | EF2L25BG256B | NA | 2520 | 2520 | 202 | 12 |
| ef2 | ef2_2.db | EF2L25XG42B | NA | 2520 | 2520 | 25 | 12 |
| ef2 | ef2_4.db | EF2L45BG256B | NA | 4480 | 4480 | 202 | 15 |
| ef2 | ef2_4.db | EF2L45LG144B | NA | 4480 | 4480 | 109 | 15 |
| ef2 | ef2_4.db | EF2L45UG132B | NA | 4480 | 4480 | 100 | 15 |
| ef2 | ef2_4.db | EF2M45LG100B | NA | 4480 | 4480 | 76 | 15 |
| ef2 | ef2_4.db | EF2M45LG144B | NA | 4480 | 4480 | 109 | 15 |
| ef2 | ef2_4.db | EF2M45LG48B | NA | 4480 | 4480 | 31 | 15 |
| ef2 | ef2_s4.db | EF2M45VG81C | NA | 4480 | 4480 | 53 | 15 |
| ef2 | ef2_s4.db | EF2S45VG81C | NA | 4480 | 4480 | 53 | 15 |
| ef3 | ef3_1.db | EF3L15CG256B | NA | 1520 | 1520 | 202 | 8 |
| ef3 | ef3_10.db | EF3LA0CG484B | NA | 11776 | 11776 | 379 | 0 |
| ef3 | ef3_10.db | EF3LA0CG642B | NA | 11776 | 11776 | 471 | 0 |
| ef3 | ef3_4.db | EF3L40CG324B | NA | 4800 | 4800 | 275 | 8 |
| ef3 | ef3_4.db | EF3L40CG332B | NA | 4800 | 4800 | 275 | 8 |
| ef3 | ef3_45.db | EF3L45CG256B | NA | 4480 | 4480 | 202 | 15 |
| ef3 | ef3_45.db | EF3L45CG256BH | NA | 4480 | 4480 | 202 | 15 |
| ef3 | ef3_5.db | EF3L50CG256B | NA | 5304 | 5304 | 202 | 0 |
| ef3 | ef3_7.db | EF3L70CG256B | NA | 7952 | 7952 | 202 | 0 |
| ef3 | ef3_9.db | EF3L90CG324B | NA | 9280 | 9280 | 275 | 16 |
| ef3 | ef3_9.db | EF3L90CG400B | NA | 9280 | 9280 | 331 | 16 |
| ef4 | ef4_9.db | EF4L90CG324A | NA | 9280 | 9280 | 275 | 16 |
| eg4 (Eagle) | eagle_15.db | EG4A15BG256 | NA | 14720 | 14720 | 191 | 24 |
| eg4 (Eagle) | eagle_15.db | EG4X15BG256 | NA | 14720 | 14720 | 188 | 24 |
| eg4 (Eagle) | eagle_20.db | EG4A20BG256 | NA | 19600 | 19600 | 191 | 29 |
| eg4 (Eagle) | eagle_20.db | EG4A20NG88 | NA | 19600 | 19600 | 66 | 29 |
| eg4 (Eagle) | eagle_20.db | EG4X20BG256 | NA | 19600 | 19600 | 188 | 29 |
| eg4 (Eagle) | eagle_20.db | EG4X20LG144 | NA | 19600 | 19600 | 102 | 29 |
| eg4 (Eagle) | eagle_s20.db | EG4D20EG176 | NA | 19600 | 19600 | 130 | 29 |
| **eg4 (Eagle)** | **eagle_s20.db** | **EG4S20BG256** | NA | **19600** | 19600 | 188 | 29 |
| eg4 (Eagle) | eagle_s20.db | EG4S20CG324 | NA | 19600 | 19600 | 210 | 29 |
| eg4 (Eagle) | eagle_s20.db | EG4S20NG88 | NA | 19600 | 19600 | 66 | 29 |
| ph1 | ph1_100.db | PH1A100GCG324 | 2/3 | 71040 | 142080 | 214 | 212 |
| ph1 | ph1_100.db | PH1A100GSG324 | 2/3 | 71040 | 142080 | 214 | 212 |
| ph1 | ph1_100.db | PH1A100SFG676 | 2/3 | 71040 | 142080 | 316 | 212 |
| ph1 | ph1_100.db | PH1A100SFG676X | 2/3 | 71040 | 142080 | 316 | 212 |
| ph1 | ph1_100p.db | PH1P100GEG324 | 3 | 71680 | 143360 | 211 | 220 |
| ph1 | ph1_100p.db | PH1P100SBG484 | 3 | 71680 | 143360 | 286 | 220 |
| ph1 | ph1_100p.db | PH1P100SBG676 | 3 | 71680 | 143360 | 301 | 220 |
| ph1 | ph1_100p.db | PH1P100SBG677 | 3 | 71680 | 143360 | 301 | 220 |
| **ph1** | **ph1_180.db** | **PH1A180SFG676** | **2/3** | **116800** | 233600 | 376 | 600 |
| ph1 | ph1_35p.db | PH1P35MBG484 | 3 | 21216 | 42432 | 291 | 40 |
| ph1 | ph1_35p.db | PH1P35MDG324 | 3 | 21216 | 42432 | 160 | 40 |
| ph1 | ph1_35p.db | PH1P35MEG324 | 3 | 21216 | 42432 | 197 | 40 |
| ph1 | ph1_35p.db | PH1P35MEG324A | A | 21216 | 42432 | 197 | 40 |
| ph1 | ph1_35p.db | PH1P35MHG324 | 3 | 21216 | 42432 | 160 | 40 |
| ph1 | ph1_400.db | PH1A400SFG676 | 2/3 | 231680 | 463360 | 401 | 840 |
| ph1 | ph1_400.db | PH1A400SFG900 | 2/3 | 231680 | 463360 | 501 | 840 |
| ph1 | ph1_400.db | PH1A400SFX900 | 2/3 | 231680 | 463360 | 501 | 840 |
| ph1 | ph1_50p.db | PH1P50GEG324 | 3 | 34240 | 68480 | 211 | 120 |
| ph1 | ph1_50p.db | PH1P50MEG324A | A | 34240 | 68480 | 189 | 120 |
| ph1 | ph1_50p.db | PH1P50SBG484 | 3 | 34240 | 68480 | 251 | 120 |
| ph1 | ph1_60.db | PH1A60GEG324 | 3 | 39360 | 78720 | 211 | 120 |
| ph1 | ph1_60c.db | PH1A60GEG324C | 3 | 39360 | 78720 | 211 | 120 |
| ph1 | ph1_90.db | PH1A90SBG484 | 2/3 | 64320 | 128640 | 260 | 240 |
| ph1 | ph1_90.db | PH1A90SEG324 | 2/3 | 64320 | 128640 | 148 | 240 |
| ph1 | ph1_90.db | PH1A90SEG325 | 2/3 | 64320 | 128640 | 160 | 240 |
| ph2 | ph2_106.db | PH2A106FLG676 | 2/3/1 | 246720 | 493440 | 286 | 1800 |
| ph2 | ph2_106.db | PH2A106FLG900 | 2/3/1 | 246720 | 493440 | 386 | 1800 |
| sf1 | sf1_6.db | SF1N60VG81C | NA | 5824 | 5824 | 27 | 10 |
| sf1 | sf1_6.db | SF1S60CG121I | NA | 5824 | 5824 | 55 | 10 |
| sf1 | sf1_6.db | SF1S60VG81C | NA | 5824 | 5824 | 27 | 10 |
| sf1 | sf1_6.db | SF1S60VG81E | NA | 5824 | 5824 | 27 | 10 |
| sf2 | sf2_1.db | SF2S50VG90C | NA | 3120 | 6240 | 31 | 8 |

（CSV 未给 `ef2_*/ph1_*p` 之外的 speed 语义；`NA` 表示该器件无速度等级选项。
74 行 part 全部能在 CSV 里匹配到 LUT 数，无一行匹配不上。）

规模跨度：最小 `SF2S50VG90C` **3,120 LUT** → 最大 `PH2A106FLG*` **246,720 LUT**。
`EG4S20*` 各封装均 **19,600 LUT**（Eagle/EG4 家族里 `eagle_s20.db` 就是 S20 系列）。
注：本表无 `LOGOS` / `PFAB` / `SATURN` 命名的族 —— 实测 `arch\device_resource\device_resource.csv`
与 `list_devices` 输出里这三个词（不区分大小写）**各 0 命中**；`arch\` 目录下的 29 个 db 文件名
也只含 `dr1_ / eagle_ / ef2_ / ef3_ / ef4_ / ph1_ / ph2_ / sf1_ / sf2_` 这些前缀。
（`eagle` 这个词在 CSV 里也 0 命中 —— CSV 的 Family 列写 `eg4`，`eagle_*` 只出现在 db 文件名与
`list_devices` 的 Device 列里。）另有 `import_pfb_connection` 这类命令名，但它与器件族无关。

---

## 4. TD 能不能自己做综合？能读哪些网表格式？

### 4.1 TD 自带综合：是

`elaborate` / `optimize_rtl` / `optimize_gate` 三步就是完整的 HDL 综合，
不是"只能从门级网表做起"。文档 `SWUG105` §4.5 "分步运行命令"：
`4.5.1 elaborate`、`4.5.2 optimize_rtl`、`4.5.3 optimize_gate`、`4.5.4 map_macro`、
`4.5.5 map`、`4.5.6 pack`、`4.5.7 legalize_phy_inst`、`4.5.8 place`、`4.5.9 route`。
另有整包命令 `launch_runs`（一步跑到某个 step）。
`board_designs` 例程只给 `.v` 源码、不给任何网表，也印证综合在 TD 内部。

**意外的强证据**：`run.exe` 二进制里嵌着 Yosys 与 ABC 的调用串（我直接对 76.7 MB 的
exe 做了 ASCII 上下文扫描）：

```
... _temp__.rtlil | attribute \src | yosys.exe | Cannot execute "%s". | .rtl | flatten; |
    -top | -sv | %s -qp "read_verilog %s %s%s; hierarchy %s%s; %sproc; write_rtlil %s" |
    Dumped the design into file "%s". | _temp_.aig | techmap; setundef -zero; | -auto-top | read_rtlil
```
```
usage: read_verilog [-mcbh] <file>          reads the network in Verilog (IWLS 2002/2005 subset)
usage: read_status [-ch] <file>            reads verification log file
usage: &read_gig [-h] <file>               reads design in GIG format
usage: read_json [-h] <file>               reads file in JSON format
usage: read_sf  [-h] <file>                reads file in SF format
write_genlib %s | write_liberty %s
```

也就是：**TD 的 RTL 前端本身就走 Yosys（`read_verilog; hierarchy; proc; write_rtlil`），
门级优化走内嵌 ABC**。上面那串 `IWLS 2002/2005 subset` / `GIG` / `SF` / `read_liberty`
是 **ABC 的内部命令**，不是 TD 的 Tcl 命令 —— 别把它们当成 TD 能读的格式。

### 4.2 能读的格式（运行时实测，非文档推断）

在 `run.exe` 里问 `info commands`：

```
TCLVER=8.6  PATCH=8.6.15
HAS_edif=0                     ;# [info commands *edif*] 长度为 0
EXIST read_edif  = 0
EXIST import_edif= 0
EXIST read_json  = 0
EXIST read_blif  = 0
EXIST read_netlist= 0
EXIST read_vhdl  = 0
EXIST read_verilog= 1
EXIST read_hdl   = 1
```

对 76.7 MB 的 `run.exe` 做字节级计数：`read_edif` **0**、`import_edif` **0**、`.edif` **0**，
而 `edif` 只有 2 次、`EDIF` 2 次 —— 那两处都在 **ABC/Yosys 的内部路径字符串**里
（一处是 `edif / synlib / upf` 这种目录类别列表，紧挨 `...DesignObject.cpp`；另一处在 SQLite 的
字符串池里），**不是** TD 的 Tcl 命令。运行时 `[info commands *edif*]` 长度为 0（上面已证），
740 条命令全集里 `*edif*` **0 命中**。

`read_hdl`（= `read_verilog`）文档 §4.3.1 的原文范围：
"支持 `.v, .sv 和 .vhd`，TD 会根据文件后缀自动选择对应的语言标准"，
带 `sv::` / `vlib::lib::` 前缀可强制语言/库；`-define` 宏、`-global_include`、`-dir`、`-top`。

**所以：不能喂 EDIF，不能喂 Yosys 的 JSON。** 可用的入口只有三个：

| 入口 | 命令 | 实测 |
|---|---|---|
| HDL 源码 | `read_verilog -file {…} -top T` | ✅ §2.2 全流程 |
| **TD 自家二进制设计库** | `import_db <f.db>` / `export_db <f.db>` | ✅ §4.4 A |
| 结构化 Verilog 网表（`al_lmacro.v` 单元） | `read_verilog -file {./al_lmacro.v ./netlist.v} -top T` | ✅ §4.4 B |

### 4.3 两条网表路径的实测细节（重要，决定 Yosys 怎么接）

**(A) `import_db`：已跑通。** 用 §2.2 产出的 `gate.db` 直接续跑 P&R：

```tcl
import_device ph1_180.db -package PH1A180SFG676 -speed 2
import_db ./gate.db
read_adc ./LED.adc
read_sdc ./LED.sdc
legalize_phy_inst
place
route
bitgen -bit ./from_netlist.bit
```
→ `rc=0`，41 s，`from_netlist.bit` = 9,424,375 B（与 RTL 流程同尺寸）。

**(B) 结构化 Verilog 网表回灌：分阶段，结论不一样 —— 这是本探查最实用的一条。**

- `optimize_gate` **之后** `write_verilog` 出来的网表（45,107 B）**不能**回灌：
  `rc=1`，7 个黑盒 —— `PH1_PHY_PAD`（×2）、`PH1_PHY_LSLICE`（×2）、`AL_MAP_SEQ_GSR`（×3）
  报 `HDL-8007 ERROR: … is a black box`。把 `arch/ph1_macro.v`（74 个 module）和
  `arch/al_lmacro.v` 一起加进 `read_verilog` 也**仍然 7 个黑盒**，因为这三个 cell
  在两个 macro 库里**都没有定义**：

  ```
  PH1_PHY_PAD  in ph1_macro.v: 0      PH1_PHY_LSLICE in ph1_macro.v: 0
  AL_MAP_SEQ_GSR in ph1_macro.v: 0    AL_MAP_SEQ_GSR in al_lmacro.v: 0
  ```
  （TD 自己会隐式加载 `arch/al_lmacro.v` 解析 `AL_MAP_LUT4/5`，日志里
  `HDL-1007 : elaborate module AL_MAP_LUT4(...) in E:\…\arch/al_lmacro.v(24)` 可证。）

- `optimize_rtl` **之后**（即物理映射之前）`write_verilog` 的网表（25,941 B）**可以**回灌并出 bit：

  ```tcl
  import_device ph1_180.db -package PH1A180SFG676 -speed 2
  read_verilog -file {./al_lmacro.v ./rtl_out.v} -top LED_Example_Top   ;# 0 黑盒
  read_adc ./LED.adc
  read_sdc ./LED.sdc
  optimize_gate
  legalize_phy_inst
  place
  route
  bitgen -bit ./from_rtlnetlist.bit
  ```
  → `rc=0`，37 s，`RSIZE=9424375`，`black box` 计数 **0**。

- **可注入的单元库 = `arch\al_lmacro.v`（30 个 module，通用跨族）**：
  `AL_MAP_LUT1  AL_MAP_LUT2  AL_MAP_LUT3  AL_MAP_LUT4  AL_MAP_LUT5  AL_MAP_LUT6
   AL_MAP_ADDER  AL_MAP_F7MUX  AL_MAP_F8MUX  AL_MAP_MUX4  AL_MAP_ALU2B
   AL_MAP_SEQ  AL_MAP_BLE_MULT18X18  AL_MAP_BLE_MULT9X9  AL_FADD  AL_MUX
   AL_DFF  AL_LATCH  AL_DFF_W  AL_DFF_0  AL_DFF_1  AL_DFF_X
   AL_LATCH_W  AL_LATCH_0  AL_LATCH_1  AL_LATCH_X  PH1_MAP_SEQ  AL_LOGIC_DEBUGHUB`
  LUT 用 `INIT` + `EQN` 参数（实测形如
  `AL_MAP_LUT4(INIT=16'b0100000000000,EQN="(D*C*~B*A)")`）。
  各族另有 `arch\<fam>_macro.v` / `.vhd`（`eagle_macro.v` 50,064 B、`ph1_macro.v` 435,651 B 等）
  提供 `*_LOGIC_*` 硬宏（RAM/DSP/FIFO/…）。

> **给 Yosys 的具体建议**：不要试图让 Yosys 出 EDIF/JSON。让 Yosys
> `synth -run fine` + 自定义 `techmap` 映射到 `AL_MAP_LUT4/5/6` + `AL_MAP_SEQ`，
> `write_verilog -noattr`，然后 TD 端按 §4.3(B) 那 8 行跑
> （`read_verilog {al_lmacro.v, netlist.v}` → `optimize_gate` → `place` → `route` → `bitgen`）。
> 黑盒风险点在 IO：`PH1_PHY_PAD` 无定义，所以 **IO buffer 不要自己例化**，
> 留给 TD 从 `read_adc` 的 `set_pin_assignment` 生成。

### 4.4 一条防假绿的提醒

TD 日志里 `ERROR` 字样**不一定**代表运行失败。上面 §4.3(B) 成功那次
（`rc=0`、bit 正常产出）stdout 里出现 **5,313 行**含 `ERROR` 的
`SYN-8490 ERROR: The model 'eq_w33' has more than one Upinst:` + `SYN-8002 ERROR: eq0 in ./rtl_out.v(28)`。
`TD_User_Guide_V6.2.pdf` p.245 的消息表说明 `SYN-8002` 只是 "`SYN-8490` 的补充信息"
（独占资源被多实例化）。所以判成败要用 **退出码 + 产物文件是否存在 + 你自己的 MARK 行**，
不能 `grep ERROR`，也不能只看 `rc`（未知 flag 那次 `rc` 也是 0）。

---

## 5. 文档：Tcl 命令清单位在哪

### 5.1 有，但是 PDF；没有 CHM/HTML help

`doc\` 下共 **39 个 PDF**。没有任何 `.chm`；仅有 6 个 `.html`，且都是 IP 打包器的
`soc_plat.html`（3 个）和 `lib\tcllib2.0\doctools\mpformats\*.html`（3 个），与 Tcl 命令无关。
GUI 的 Help 菜单靠 `doc\gui\helpmenu.json` 指向这些 PDF（`"file_path": "../doc/SWUG/SWUG105_TCL_Command_User_Guide.pdf"` 等）。

| 你要的东西 | 文件 | 位置 |
|---|---|---|
| **Tcl 命令总表/用法（最关键）** | `doc\SWUG\SWUG105_TCL_Command_User_Guide.pdf` | 90 页，2024.11，v1.0 |
| 综合编码指南 | `doc\SWUG\SWUG106_HDL_Coding_Synthesis_User_Guide.pdf` | |
| 时序约束 | `doc\SWUG\SWUG200_Timing_Constraints_User_Guide.pdf` | |
| 时序工具 | `doc\SWUG\SWUG201_Timing_Tools_User_Guide.pdf` | |
| 主手册 | `doc\TD_User_Guide_V6.2.pdf` | **289 页 / 191,586 字符**（含全部错误码表） |
| 仿真 | `doc\SWUG\SWUG107_Design_Simulation_User_Guide.pdf` | |
| 下载/烧写 BitWizard | `doc\SWUG\SWUG110_BitWizard_User_Guide.pdf` | 对 `bw.exe` |
| 模块打包 | `doc\SWUG\SWUG507_Module_Packager_User_Guide.pdf` | 对 `mpack.exe` |
| 其余 SWUG | `SWUG111_Debug_Tools`, `SWUG113_Encrypt_Tool`, `SWUG501_Design_Integrator`, `SWUG503_HPF_Tool` | |
| 器件库指南（HDL 宏） | `UG1110_PH2A_*`, `UG1211_DR1_*`, `UG1310_PH1P_*`, `UG710_PH1A100_*`, `UG910_PH1A_*` | 5 份 |
| IP 手册 | `doc\IPUG\` 15 份 | |
| 随装流程脚本 | `doc\scripts\{DefaultFlow,MultiSeed,PRFlow,SynIpFlow}.tcl` | **不是文档，是能抄的脚本** |

**PDF 附录一（"Device db 文件名"，p.83）不完整**：只列 PH1、PH2 两族。
**用 `list_devices` 代替**（§3.1）。

### 5.2 更好的命令参考：问二进制自己

`doc\cmd\` 那 4 个 JSON **不能当命令表用**：
`help_comment_opt.json`(4,792 B, 5 条)、`help_comment_phy.json`(9,276 B, 19 条)、
**`help_comment_syn.json` = 0 字节、`help_comment_tool.json` = 0 字节**。
这直接解释了为什么 `read_hdl -help`、`bitgen -help`、`report_area -help`、`elaborate -help`
等跑了**什么都不打印**（`RC-OK:` 空），而 `place -help`、`route -help`、`phys_opt -help`、
`import_device -help`、`report_cells -help`、`report_congestion -help` 有完整表格输出。

所以：

1. **全量命令名** = 在 `run.exe` 里跑 `info commands` → **740 条**（含 Tcl 内建）。
   已 dump 到 scratch 的 `real_commands.txt`。
   （内置 `help` 只列 **385** 条，因为它是按上面那几个 JSON 建的索引，syn/tool 是空的。
   例如 `report_qor` / `report_timing_status` / `flow_status` / `report_clock_utilization`
   在 `help` 里查不到，但 `info commands` 证明它们存在，且 `DefaultFlow.tcl` 在用。）
2. **单条命令的参数** = `<cmd> -help`（只有部分命令有内容，见上）。
3. **没有内容的那些** = 回落到 `SWUG105` PDF 正文对应小节。

### 5.3 你五类需求的对应命令（全部经 §2.2/§4.3 实跑，或 `info commands`/`-help` 存在性核对）

**(a) 读 Verilog**
```
read_verilog / read_hdl   -file {a.v b.sv c.vhd} | -dir <d>  -top <module>
                          [-define "NAME NAME2=V2"] [-global_include f] [-included_dir d]
elaborate -top <module>              ;# 工程模式用（read_design step 内部）
add_files [-list x.list|x.slist] [files]   ;# 工程模式
add_dir -r <dir>        ;# .v/.vhd/.vhdl/.sv/.ipc，不含 .adc/.sdc
add_include_dir <dir>
```

**(b) 约束（引脚 / 时钟）**
```
read_adc <f.adc>     ;# 内部逐行 dispatch set_pin_assignment {net} {LOCATION=..; IOSTANDARD=..;}
read_ip_adc -ip <ip> -file <f.adc>
read_sdc [-ip <ip>] <f.sdc>
clear_adc / clear_sdc / clear_user_sdc
create_clock -name sysclk -period 40.000 [get_ports {I_sysclk_p}]
create_generated_clock  derive_clocks  rename_clock
set_input_delay set_output_delay set_clock_latency set_clock_uncertainty
set_max_delay set_min_delay set_multicycle_path set_false_path
set_clock_groups set_clock_sense set_clock_route set_clock_route_enforce
set_cross_domain_timing set_timing_derate
对象获取: all_clocks all_inputs all_outputs all_registers
          get_ports get_cells get_nets get_regs get_pins get_clocks
```

**(c) 跑综合 + P&R**
```
optimize_rtl        ;# RTL 优化/映射到 AL_MAP_*      (插 debugger 在这之前: insert_debugger)
optimize_gate       ;# 技术映射到物理 LUT/slice
map_macro  map  pack  phys_opt  fix_hold
legalize_phy_inst   ;# 必须再 place 之前
place   [-eco] [-keep_pad] [-export_adc]
route   [-eco] [-incremental] [-verbose]
update_timing [-mode basic|manhattan|final]
set_param <step> <param> <value>     ;# 例: set_param place seed 5 / set_param route fix_hold on
commit_param -step <design|rtl|gate|place|route|bitgen>
;# 工程模式一步到位: launch_runs [-step design|rtl|gate|place|route|bitgen] [-jobs 1..6] {run...}
;#               wait_run <run> [-quiet] / open_run <run> / reset_runs <run>
```
`place/route/phys_opt` 的调优参数在 `doc\strategies\official_strategy_ph1.json`、
`official_strategy_ph2.json`；`set_param` 的 step/param/value 全集在
`SWUG105` §4.2 表 4-1 / 4-2（Phy 侧节选实测引用：`place effort medium/high`、
`place pr_strategy PH1: 1~11 / PH2: 1~5,12~17`、`route fix_hold off/on/High(High only for PH2)`、
`route post_route_opt auto/medium/high/ultra/off`、`flow enable_seed off/on`）。

**(d) 生成 bitstream**
```
bitgen -bit <out.bit> [-g {param:value …}]
;# 文档 p.82 原文示例:
;#   bitgen -bit top.bit -g {security:1}
;#   bitgen -bit top.bit -g {mclk_freq:5MHz boot_mode:mspix4}
export_bitgen_param -file .bitgen_param.f   ;# GUI 流程在 bitgen 后回存参数
save_best_bits      ;# multi-seed 选最优
;# 之后: bw.exe (BitWizard, GUI!) / mpack.exe / svf2aje.exe -svffile x.svf -ajefile x.aje
```

**(e) 资源使用报告 & 时序报告**

资源：
```
report_area -file <f>          ;# 也可 -io_info
report_qor   -step gate|place|route -file <f>
report_design_cell_num  report_cells  report_nets  report_logic_level
report_ram_utilization  report_imux_utilization  report_clock_utilization [-dramhardcon] [-file f]
report_congestion -step place|route [-th n] [-area x1,y1-x2,y2]
report_route_status [-drc] [-fanout_stat] [-file f]
report_io  report_hierarchy_stats  report_analysis  report_hfn  report_noise
```
时序：
```
report_timing_summary -file <f>       ;# 主 STA 报告
report_timing_status  -file <f>       ;# 两行 WNS/TNS 摘要
report_timing_path  report_timing_exception  report_timing_ignored
report_clock_summary  report_clock_intersection  report_clock_buffers
report_clock_polarity  report_path_margin  report_path_skew  report_hyper_paths
check_timing  report_annotated_check  report_abnormal_timing_target
```

实测产物长相（`flow1\`，可直接当解析目标）：

`route.area` 头部：
```
***Report Model: LED_Example_Top Device: PH1A180SFG676***
Design Statistics
#IO                        10   out of    376    2.66%
#lut6                      52   out of 116800    0.04%
#reg                       41
  #slice reg               41   out of 233600    0.02%
Utilization Statistics
#slice                     57   out of 116800    0.05%
#eram                       0   out of    646    0.00%
#dsp                        0   out of    600    0.00%
#pll                        0   out of     16    0.00%
```
`route.qor`（定宽两列表格，最适合机器读；以下为**逐字**原样）：
```
-----------------------------------------------------------
| QoR metrics               |         value               |
-----------------------------------------------------------
| Timing                    |                             |
|     Setup WNS             |        35488                |
|     Setup TNS             |            0                |
|     Num of violated S-EP  |            0                |
|     Hold WNS              |          107                |
|     Hold TNS              |            0                |
|     Num of violated H-EP  |            0                |
-----------------------------------------------------------
| Routing Resource          |                             |
|     Wire length           |        21344                |
-----------------------------------------------------------
| Utilization               |                             |
|     #slices               |           57                |
|     slices percentage     |        0.05%                |
|     #RAMs                 |            0                |
|     #DSPs                 |            0                |
|     #DRAMHARDCON          |            0                |
-----------------------------------------------------------
Report route stage QoR done.
```

`route.ts`（`report_timing_status` 全文，只有 2 行）：
```
SWNS: 35.488ns, STNS: 0.000ns.
HWNS: 0.107ns, HTNS: 0.000ns.
```
`route_timing.rpt`（`report_timing_summary`）头部字段：
```
Created by : Tang Dynasty v6.2.168116
Top Model  : LED_Example_Top    Device : PH1A180SFG676    Speed : 2
STA coverage : 89.56%
Constraint File : .../LED.sdc;
Confidence : Routed        Check Corner : Both (Slow and Fast)      CRPR Enabled : Yes
Timing Status:  SWNS: 35.488ns, STNS: 0.000ns / HWNS: 0.107ns, HTNS: 0.000ns
Clock Summary: clk0  25.000MHz  Fanout 32  Clock-Name sysclk
```
`report_timing_summary` 还会额外自动写一份 `final_timing.rpt`（119,743 B，我没请求它）。

---

## 6. License：需要，但不需要服务器

### 6.1 事实

- **license 文件存在**（只报告路径，**未复制、未编辑**）：
  `E:\Anlogic\TD_6.2.1_Engineer_6.2.168.116\license\Anlogic.lic`（258 B，2025-07-06 修改）
  ```
  # NODELOCKED license for TD, Shanghai Anlogic Info Tech
  # Generated on Tue Aug 13 13:41:00 2024
  FEATURE  TD  Anlogic  2029-08-17  a3c61cf2f71eeea0b2d0e503deccfea6c7159c1a5e88193479b1c532…
  HOST_ID = ANY
  ```
  → 到期 **2029-08-17**，`HOST_ID = ANY`（不锁网卡）。是**自校验签名串**，不是 flexlm。
- **环境变量名 = `TD_LICENSE`**（`run.exe` 内 1 处命中，紧邻
  `Invalid License! #2` / `License expired!` / `License validation success!` /
  `Can not find license file path` / `.lic` / `license|-feature`）。
  `ANLOGIC_LICENSE`、`LIC_FILES`、`LM_LICENSE`、`LICENSE_FILE`、`flexlm`、`lmgrd`、`.l3c`
  在二进制里 **0 命中** → 没有 license server 概念。
- 签名校验公钥在 `pubkey\`：`anlogic-rsa-008.pubkey`、`anlogic-rsa-009.pubkey`、
  `CDS_RSA_KEY_VER_1.pubkey`、`SNPS-VCS-RSA-*.pubkey`、`UV-ENC-RSA-1.pubkey`（后几个是给
  加密 IP / 仿真用的，不是 TD license）。
- **本机环境里 `TD_LICENSE` 根本没有设**：进程 env 只有 Cadence 的
  `CDS_LIC_FILE=5280@localhost` / `CDS_LIC_ONLY=1`；HKCU\Environment 与
  HKLM Session Manager\Environment 里没有任何 `*LIC*ANLOGIC*TD_*` 项（注册表已实查）。
  → 工具靠 **exe 同目录相对路径** 自动找到 `../license/Anlogic.lic`。

### 6.2 实测证明 license 真的被用、且真的会拦

**(1) 默认情况：自动打开，全流程通过。** `flow1\flow_stdout.txt` 里 4 次：
```
RUN-1001 : Open license file E:/Anlogic/TD_6.2.1_Engineer_6.2.168.116/license/Anlogic.lic
```
按日志顺序，第一次打开正好在 `optimize_rtl` 之后一行：
```
line  20 START:import_device
line  44 START:read_verilog
line  56 START:read_adc   (+10 条 set_pin_assignment)
line  80 START:optimize_rtl
line  82 LICENSE-OPEN          <-- 第一个被 license 拦的 step
line 240 START:optimize_gate
line 242 LICENSE-OPEN
```
（`import_device` / `read_verilog` / `read_adc` **不检查** license，从这些 step 失败前跑完可以推知；
但"不检查"这句是从 step 顺序推断，未单独最小化验证。）

**(2) 把 `TD_LICENSE` 指到一个不存在的文件 —— 立刻被拦住，rc=1：**
```
TD_LICENSE=''                     RC=0  … LIC-FLOW-OK-P4        (marker 打印了)
TD_LICENSE='C:/nope/bogus.lic'    RC=1  marker 未打印
   RUN-1001 : Open license file C:/nope/bogus.lic
   RUN-8008 ERROR: Cannot find license file C:/nope/bogus.lic
   stderr: while executing  "optimize_rtl"  (file "lic.tcl" line 4)
```
→ `TD_LICENSE` 会**覆盖**默认路径；覆盖成坏值就把综合停掉。错误码
`RUN-8008 = 找不到 license 文件`，`RUN-9992 = license 格式非法`
（后者见 `TD_User_Guide_V6.2.pdf` p.244 错误码表："RUN-9992 license 格式非法 / 重新获取合法的 license 文件"）。

### 6.3 结论

**要 license，但不要 license server。** 现状（`Engineer` 版、NODELOCKED、`HOST_ID=ANY`、2029 到期）
下 `run.exe -b` 可直接跑综合+P&R+bitgen，无需任何 env 设置。**不要去动 `license\Anlogic.lic`**；
如果要搬机器/进 CI，最省事的是整目录带上（license 靠相对路径被找到），
或显式 `export TD_LICENSE=<绝对路径>/Anlogic.lic`（实测该变量确实被读）。
唯一"无 license 也能跑"的部分是 `run.exe` 的 banner、Tcl 解释器、`list_devices`、
`import_device`、`read_verilog` —— 你之前看到的"banner 印得很正常"正是因为这个，
它不代表 license 已通过。

---

## 7. 一页速查

```tcl
# ---- build.tcl : 非工程模式，Verilog -> bit （实测 45 s / rc=0 / 9,424,375 B .bit）----
import_device ph1_180.db -package PH1A180SFG676 -speed 2   ;# 换 EG4S20BG256 用 eagle_s20.db
read_verilog -file {./top.v ./rtl/*.v} -top top            ;# 或 read_hdl；不支持 .edif/.json
read_adc  ./pins.adc        ;# set_pin_assignment {…} { LOCATION=…; IOSTANDARD=…; }
optimize_rtl
report_area -file rtl.area
read_sdc  ./top.sdc         ;# create_clock -name c -period 8.0 [get_ports {clk}]
optimize_gate
legalize_phy_inst
place
report_qor      -step place -file place.qor
update_timing   -mode manhattan
report_timing_summary -file place_timing.rpt
route
report_area     -file route.area
update_timing   -mode final
report_timing_status  -file route.ts
report_timing_summary -file route_timing.rpt
export_db       ./route.db           ;# 只重跑 P&R 就 import_db 这个
bitgen -bit ./out.bit                ;# 加密/boot 选项: -g {security:1 mclk_freq:5MHz boot_mode:mspix4}
```
```sh
cd <dir-with-build.tcl> && timeout 1800 run.exe -b build.tcl >build.out 2>build.err; rc=$?
# 判成败: $rc==0 且 [ -f out.bit ] 且 build.out 里有你自打的 MARK 行。不要 grep ERROR。
```

**其它必记事实**
- `run.exe` = Tcl 8.6.15 壳；`td.exe` = GUI；`td_commands_prompt.exe` = 官方名字叫法（同一个东西）。
- `%` 出现 = 你掉回交互模式了 = flag 写错了。
- `list_devices` 是唯一完整的器件表（74 part / 10 family / 28 db），PDF 附录一只有 2 族。
- 无 EDIF / 无 JSON 输入；网表入口 = `import_db`(.db) 或 `read_verilog {al_lmacro.v, netlist.v}`。
- `optimize_gate` 之后的 `write_verilog` 回灌不了（`PH1_PHY_PAD`/`PH1_PHY_LSLICE`/`AL_MAP_SEQ_GSR` 黑盒）。
- TD 内部前端 = Yosys + ABC。
- **`bw.exe` 会挂死（连 `--help` 都不返回）**，其他 GUI 件同理，一律 `timeout`。
- `image2aje.exe` 不存在；只有 `svf2aje.exe`（正规 CLI，有 usage）。
- `template/template.cfg` 与 `settings.cfg`、`ip/**/*.enc*` 都是 Anlogic AES128 加密件，读不了也别猜。

---

## 8. scratch 目录（本次唯一写入处，可安全删除）

`C:\Users\lenovo\AppData\Local\Temp\td_probe_scratch\`

- `probe_cli.py` / `probe_cli2.py` / `probe_cmds.py` / `probe_cmds4.py` — 探针脚本
- `probe_out\` `probe_out2\` `probe_out3\` `probe_out4\` — §1 的 flag 矩阵原始输出；
  `probe_out3\real_commands.txt` = 740 条运行时命令全集；`probe_out4\clean_devices.txt` = `list_devices` 全表
- `flow1\` — §2.2 端到端（`LED.bit` 等全部产物）；`flow2\` — §4.3(A) `import_db`；
  `flow3\` — §4.3(B) 网表回灌（含 `b_reimport_out.txt` 黑盒证据、`e_reimp3_out.txt` 成功证据）
- `SWUG105.txt` / `TD_UG.txt` / `ug_*.txt` — 两份 PDF 的全文抽取 + 关键词上下文
- `device_table.md` — §3.3 的合并表；`lic_strings.txt` — license 字符串上下文

安装目录 `E:\Anlogic\TD_6.2.1_Engineer_6.2.168.116\`：我**没有主动写任何文件**，
但有**一处副作用必须交代** ——

```
bin\log\bw_2026-09-24_14-09-14.log   (+ .logw)
bin\log\bw_2026-09-24_14-09-41.log   (+ .logw)
```

这 4 个 400 B 文件（和 `bin\log\` 这个目录本身）是我探测 `bw.exe --help` / `-h`（两者都挂死被
timeout 杀掉）时，**由 bw.exe 自己**写在它 exe 旁边的（与我的 cwd 无关）。
内容纯粹是那 12 行启动 banner，**不含任何设计数据或 license 内容**。
因为用户要求"不得修改/删除安装目录"，我**没有**把它们删掉；
要恢复原状就手动执行：`rm -rf "E:/Anlogic/TD_6.2.1_Engineer_6.2.168.116/bin/log"`
（该目录本次新建、只含这 4 个 banner 日志）。

其余：`bin\td_20260924_135216.log` 那两个 404 B 文件是本次探查**之前**（13:52，你自己跑
`--help` 时）由工具自动写的，不是我写的。我的 `run.exe` 运行都把 `td_*.log` 写在了各自 cwd
（scratch）里 —— 已核对：`find . -newermt "2026-09-24 13:53"` 在安装目录内只返回上面那 4 个
`bin/log/bw_*` 文件。license 文件仅被读取，未复制/未修改/未移动。

**顺带一条命令名纠正**：`bw.exe` 自报 **`BitWriter, V1.2.168116`**，
不是 `SWUG110_BitWizard_User_Guide.pdf` 标题里的 "BitWizard"；它是**下载/烧写器**，
而且它是 GUI 程序（`--help` 和 `-h` 都不返回，25 s 超时被 kill）。
