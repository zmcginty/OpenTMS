"""Grouped BOM from circuit.py -> CSV (and the same data as JSON for the notes)."""
import csv, os, sys, collections, re
sys.path.insert(0, os.path.dirname(__file__))
from circuit import parts
OUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def natkey(r): m = re.match(r'([A-Z]+)(\d+)', r); return (m.group(1), int(m.group(2))) if m else (r, 0)
groups = collections.OrderedDict()
for p in sorted(parts, key=lambda p: natkey(p['ref'])):
    if p['ref'].startswith('TP'): continue
    k = (p['val'], p['fp'], p['mpn'], p['mfr'], p['dnp'])
    groups.setdefault(k, []).append(p)
rows = []
for (val, fp, mpn, mfr, dnp), ps in groups.items():
    refs = sorted((p['ref'] for p in ps), key=natkey)
    descs = sorted(set(p['desc'] for p in ps if p['desc']))
    rows.append(dict(Item=0, Qty=0 if dnp else len(ps), References=' '.join(refs), Value=val, Footprint=fp.split(':')[1],
                     Manufacturer=mfr if mpn else 'Generic', MPN=mpn or '(any, see Value)', DNP='DNP' if dnp else '',
                     Notes='; '.join(descs)))
order = {'J': 0, 'X': 1, 'T': 2, 'U': 3, 'Q': 4, 'D': 5, 'F': 6, 'R': 7, 'C': 8}
rows.sort(key=lambda r: (order.get(re.match(r'[A-Z]+', r['References']).group(0)[0], 9), natkey(r['References'].split()[0])))
for i, r in enumerate(rows, 1): r['Item'] = i
tps = [p['ref'] for p in parts if p['ref'].startswith('TP')]
rows.append(dict(Item=len(rows) + 1, Qty=0, References=' '.join(sorted(tps, key=natkey)), Value='Test pad', Footprint='TestPoint_Pad_D1.5mm', Manufacturer='', MPN='(bare copper pad, no part)', DNP='', Notes='Test points'))
with open(f'{OUT}/BOM_fz1200_gate_driver.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print('lines', len(rows), 'placed parts', sum(r['Qty'] for r in rows))
