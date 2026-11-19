"""Fails if any net connects parts on the primary (15 V input) side with parts on the secondary side.
T1 is the only part allowed to touch both."""
import sys, os, collections
sys.path.insert(0, os.path.dirname(__file__))
from circuit import parts
PRI_REFS = {'J1', 'J2', 'F1', 'D1', 'D2', 'C1', 'C2', 'C3', 'U1', 'R1', 'R2', 'R3', 'R4', 'C4'}
use = collections.defaultdict(set)
for p in parts:
    for pin, n in p['nets'].items():
        if n and p['ref'] != 'T1': use[n].add(p['ref'])
bad = [(n, sorted(r)) for n, r in use.items() if r & PRI_REFS and r - PRI_REFS]
for b in bad: print('ISOLATION BREACH: net', b[0], 'joins', b[1])
print('side check:', 'FAIL' if bad else 'pass', '(%d nets)' % len(use)); sys.exit(1 if bad else 0)
