import io,re,json,os,sys,gzip
# 波形的归档形态有两种：明文 .vcd 与压缩件 .vcd.gz。大于 10 MB 的明文产物随包只留
# 压缩件（flow/compress_artifact.py：解压回读后 sha256 与行数与原件逐位相同才删原件，
# 并留 <name>.gz.json 收条），所以这里两个形态都认。一个都没有就必须停手 ——
# 因为 results/wave/probe.txt 是覆盖写的，继续跑只会把上一趟的残留再拷进 results/demo/。
def resolve(p):
    if os.path.isfile(p):
        return p, False
    if os.path.isfile(p + '.gz') and os.path.isfile(p + '.gz.json'):
        return p + '.gz', True
    sys.exit("VCD_MISSING: neither %s nor %s.gz(+sidecar) exists; probe.txt would be stale"
             % (p, p))

path=os.path.abspath(sys.argv[1]) if len(sys.argv)>1 else 'results/wave/bisect.vcd'
path, isgz = resolve(path)
OUT=open('results/wave/probe.txt','w',encoding='utf-8')
def w(*a): print(*a,file=OUT)
open_txt = (lambda p: gzip.open(p, 'rt', encoding='utf-8', errors='replace')) if isgz \
    else (lambda p: io.open(p, encoding='utf-8', errors='replace'))
names={}; stack=[]; width={}; inhead=True; nc=0
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
                if m:
                    kind,wd,ident,nm=m.groups()
                    width[ident]=int(wd); names[ident]='.'.join(stack+[nm])
            elif line.startswith('$enddefinitions'):
                inhead=False
            continue
        nc+=1
want_re=r'(u_epv\.i_valid|u_epv\.o_illegal|u_epv\.mem_ph|u_epv\.c0_lvb|u_epv\.c0_svb|u_epv\.active|u_epv\.o_done|u_epv\.o_busy|idex_is_epv|idex_valid|o_dmem_en|idex_opcode|epv_mreq|epv_grant)$'
interest={i:n for i,n in names.items() if re.search(want_re,n)}
w("vars_total",len(names),"deltalines",nc,"interest",len(interest))
for i,f in sorted(interest.items(),key=lambda x:x[1]): w("   %-14s %s w=%d"%(i,f,width[i]))
cur={}; samples=[]; t=0
pick=set(interest)
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
            samples.append((t,dict(cur)))
            continue
        if len(line)<2: continue
        ident=line[-1] if line[-1] in names else None
        m=re.match(r'(\S+)\s+(\S+)$',line)
        if m and m.group(2) in names:
            ident,val=m.group(2),m.group(1)
        elif line[0] in '01xzXZ' and line[1:] in names:
            ident,val=line[1:],line[0]
        if ident in pick: cur[interest[ident]]=val
# 只在信号真的变过的时候取样, 输出每个关注信号出现过哪些值
seen={}
for t,s in samples:
    for k,v in s.items(): seen.setdefault(k,set()).add(v)
w("--- distinct values observed per signal ---")
for k in sorted(seen): w("   %-46s %s"%(k,sorted(seen[k])[:8]))
w("--- last 6 samples ---")
for t,s in samples[-6:]: w("  t=%d %s"%(t,json.dumps({k.split('.')[-1]:v for k,v in s.items()},ensure_ascii=False)[:300]))
w("--- source ---")
w("   %s gz=%d" % (path, int(isgz)))
OUT.close(); print("WROTE results/wave/probe.txt gz=%d" % int(isgz))
