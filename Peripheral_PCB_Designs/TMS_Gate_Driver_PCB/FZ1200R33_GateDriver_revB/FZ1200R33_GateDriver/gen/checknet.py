import sys
from sexp import parse, find, findall
import design
t = parse(open(sys.argv[1]).read())[0]
nets = find(t, 'nets')
got = {}
for n in findall(nets, 'net'):
    name = find(n, 'name')[1].split('/')[-1]
    nodes = frozenset((find(x,'ref')[1], find(x,'pin')[1]) for x in findall(n,'node') if not find(x,'ref')[1].startswith('#'))
    if nodes: got[name] = nodes
exp = {k: frozenset(v) for k, v in design.nets().items()}
comps = find(t,'components'); print('components in netlist:', len(findall(comps,'comp')))
bad = 0
for k,v in exp.items():
    if got.get(k) != v:
        bad += 1; print('MISMATCH', k, 'exp', sorted(v - got.get(k,set())), 'extra', sorted(got.get(k,set())-v))
for k in got:
    if k not in exp and len(got[k])>1: bad+=1; print('UNEXPECTED NET', k, sorted(got[k]))
print('netlist check:', 'OK' if not bad else f'{bad} problems', len(got), 'nets')
