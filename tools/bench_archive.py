#!/usr/bin/env python3
"""把 nn_bench 的 UART 读数 + 运行归档 汇成 results/bench/nn_bench.json。

正文里出现的加速比只能来自这份文件; 字段全部现算自本次运行, 不从报告反抄。
工具指纹(仿真器/编译器/RTL 哈希)一并记下, 让「哪次运行产出的哪个数」可追。
"""
import io,json,os,re,hashlib,subprocess,sys

ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,os.path.join(ROOT,"flow"))
from atomic_json import write_json_atomic   # 归档落盘的唯一实现
os.chdir(ROOT)
SIM=r"E:\msys64\ucrt64\bin"; TC=SIM
sim=os.path.join(ROOT,"results","sim","nn_bench.json")
if not os.path.isfile(sim):
    print("NO_BENCH_RUN"); sys.exit(1)
r=json.load(io.open(sim,encoding="utf-8"))

KEYS=("MACS","REF_CYC","REF_INST","EPV_CYC","EPV_INST","SPEEDUP_X1000",
      "CYC_PER_MAC_REF_X100","CYC_PER_MAC_EPV_X100","MISMATCH")
txt=r["uart"].replace("\r\n","\n")
kv={}
for k in KEYS:
    m=re.search(r'(?m)^%s (\d+)$'%k, txt)
    if not m: print("MISSING_FIELD",k,"uart=",repr(txt[:200])); sys.exit(1)
    kv[k]=int(m.group(1))
for k in KEYS:
    if k not in kv: print("MISSING",k); sys.exit(1)

def sha(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for b in iter(lambda:f.read(65536),b''): h.update(b)
    return h.hexdigest()

rtl={}
for d in ("rtl",):
    for fn in sorted(os.listdir(d)):
        if fn.endswith(('.v','.vh')): rtl[fn]=sha(os.path.join(d,fn))

def ver(exe, *a):
    try:
        p=subprocess.run([exe]+list(a),capture_output=True,timeout=60)
        return p.stdout.decode('utf-8','replace').splitlines()[0].strip()
    except Exception as e: return "UNREADABLE: %r"%e

out={
 "benchmark":"nn_bench: 3x3x16->4 INT8 conv, NHWC, per-channel Q31 requant + zero-point",
 "run_archive":"results/sim/nn_bench.json",
 "macs":kv["MACS"],
 "ref_cycles":kv["REF_CYC"], "ref_instret":kv["REF_INST"],
 "epv_cycles":kv["EPV_CYC"],  "epv_instret":kv["EPV_INST"],
 "speedup_x1000":kv["SPEEDUP_X1000"],
 "speedup":round(kv["SPEEDUP_X1000"]/1000.0,3),
 "inst_ratio_x1000":round(kv["REF_INST"]*1000/kv["EPV_INST"]),
 "cyc_per_mac_ref":kv["REF_CYC"]/kv["MACS"],
 "cyc_per_mac_epv":kv["EPV_CYC"]/kv["MACS"],
 "epv_cpi":round(kv["EPV_CYC"]/kv["EPV_INST"],3),
 "ref_cpi":round(kv["REF_CYC"]/kv["REF_INST"],3),
 "bit_exact_mismatches":kv["MISMATCH"],
 "bit_exact": kv["MISMATCH"]==0,
 "epv_exit_code":r["commit_val"], "verdict":r.get("verdict"),
 "total_cycles":r["cycles"], "total_retired":r["retired"],
 "toolchain":{
   "simulator":ver(os.path.join(TC,"verilator_bin.exe"),"--version"),
   "cc":ver(os.path.join(TC,"riscv64-unknown-elf-gcc.exe"),"--version"),
   "yosys":ver(r"D:\yosys_build\bucrt\yosys.exe","--version"),
   "march":"rv32im_zicsr_zifencei","mabi":"ilp32","opt":"-O2 -nostdlib -lgcc",
 },
 "rtl_sha256":rtl,
 "caveats":[
   "存储为单周期理想模型: 无 cache, 权重每个输出像素重新 LVB 一次",
   "基线是 -O2 的普通 C 循环, 不是手工优化的标量汇编",
   "无实物板; 未做 FPGA 上电验证",
 ],
}
os.makedirs("results/bench",exist_ok=True)
write_json_atomic("results/bench/nn_bench.json",out)
print("WROTE results/bench/nn_bench.json")
print("speedup=%s bit_exact=%s cyc/mac %s -> %s"%(
  out["speedup"],out["bit_exact"],round(out["cyc_per_mac_ref"],2),
  round(out["cyc_per_mac_epv"],3)))
