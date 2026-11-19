"""Compare the netlist KiCad extracts from the schematic with the intended circuit."""
import re,json,sys,subprocess,collections
net=sys.argv[1]; want=json.load(open(sys.argv[2]))
s=open(net).read()
nets={}
for m in re.finditer(r'\(net \(code "?\d+"?\) \(name "([^"]*)"\)(.*?)(?=\n\s*\(net \(code|\Z)',s,re.S):
    name=m.group(1).split('/')[-1]; pins=re.findall(r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"',m.group(2))
    pins=[f'{a}.{b}' for a,b in pins if not a.startswith('#')]
    if name.startswith('unconnected') or not pins: continue
    nets[name]=sorted(pins)
want={k:sorted(v) for k,v in want.items()}
bad=0
got=collections.Counter(tuple(v) for v in nets.values()); exp=collections.Counter(tuple(v) for v in want.values())
for v in (exp-got): bad+=1; print('MISSING',v)
for v in (got-exp): bad+=1; print('UNEXPECTED',v)
print('nets intended',len(want),'extracted',len(nets),'mismatches',bad)
sys.exit(1 if bad else 0)
