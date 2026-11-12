import io,re,json,os,sys,gzip
# 波形归档可以是明文 .vcd 或压缩件 .vcd.gz（见 tools/vcd_probe.py 里同一条注释）。
# 先定位输入再开输出文件：OUT=open(...,'w') 一执行就把上一趟的 probe2.txt 截成 0 字节，
# 找不到输入时必须在那之前停手。
def resolve(p):
    if os.path.isfile(p):
        return p, False
    if os.path.isfile(p + '.gz') and os.path.isfile(p + '.gz.json'):
        return p + '.gz', True
    sys.exit("VCD_MISSING: neither %s nor %s.gz(+sidecar) exists; probe2.txt would be stale"
             % (p, p))
path=os.path.abspath(sys.argv[1]) if len(sys.argv)>1 else 'results/wave/bisect.vcd'
path, isgz = resolve(path)
open_txt = (lambda p: gzip.open(p, 'rt', encoding='utf-8', errors='replace')) if isgz \
    else (lambda p: io.open(p, encoding='utf-8', errors='replace'))
OUT=open('results/wave/probe2.txt','w',encoding='utf-8')
def w(*a): print(*a,file=OUT)
names={}; stack=[]; inhead=True
with open_txt(path) as fh:
    for raw in fh:
        line=raw.strip()
        if inhead:
            if line.startswith('$scope'):
                m=re.match(r'\$scope\s+\S+\s+(\S+)',line)
                if m: stack.append(m.group(1))
            elif line.startswith('$upscope'):
                if stack: stack.pop()
            elif line.startswith('$var'):
                m=re.match(r'\$var\s+(\S+)\s+(\d+)\s+(\S+)\s+(\S+)',line)
                if m: names[m.group(3)]='.'.join(stack+[m.group(4)])
            elif line.startswith('$enddefinitions'): inhead=False
keys=['idex_inst','i_valid','c0_lvb','c0_svb','mem_ph','active','wc','base','o_mem_addr','o_mem_wdata','o_mem_req',
      'o_mem_we','macc','o_illegal','o_done','o_busy','i_mem_rdata','i_mem_grant','vrf','o_vcfg']
want={}
for i,n in names.items():
    tail=n.split('.')[-1]
    if any(n.endswith('.'.join(suf)) for suf in [[k] for k in keys]): want[i]=n
w("watching:"); 
for i,n in sorted(want.items(),key=lambda x:x[1]): w("   ",n)
cur={}; t=0; shown=0; prev_epv=False
with open_txt(path) as fh:
    started=False
    for raw in fh:
        if not started:
            if '$enddefinitions' in raw: started=True
            continue
        line=raw.strip()
        if not line: continue
        if line[0]=='#':
            t=int(line[1:])
            e=cur.get('c0_lvb','0')=='1' or cur.get('c0_svb','0')=='1'
            if (e or prev_epv) and shown<80:
                shown+=1
                w("t=%-6d %s"%(t,json.dumps(cur,ensure_ascii=False)))
            prev_epv=e
            continue
        ident=None;val=None
        m=re.match(r'(\S+)\s+(\S+)$',line)
        if m and m.group(2) in want: ident,val=m.group(2),m.group(1)
        elif line[0] in '01xzXZ' and line[1:] in want: ident,val=line[1:],line[0]
        if ident: cur[want[ident].split('.')[-1]]=val
w("shown",shown)
w("source", path, "gz=%d" % int(isgz))
OUT.close(); print("WROTE probe2 gz=%d" % int(isgz))
