"""Measure outer-layer copper distances between nets at different potentials and compare
with IEC 60664-1 creepage (pollution degree 2, material group II, CTI>=400), interpolated.
Potentials are worst case with the IGBT blocking 3300 V; nets on the secondary low-voltage
side are treated as 0 V. Primary-to-secondary needs >= 20 mm (1SD418F2 spec)."""
import pcbnew, sys, math, itertools, collections
from shapely.geometry import box, LineString, Point
from shapely.ops import unary_union
b = pcbnew.LoadBoard(sys.argv[1]); OX = OY = 100.0
sys.path.insert(0, __import__('os').path.dirname(__file__))
from potentials import dv, creep_uncoated, creep_coated, HVSET
PRIMARY = {'VIN', 'VIN_R', 'PGND', 'SW1', 'SW2', 'FCLK', 'SSILIM', 'PEN'}
geo = collections.defaultdict(list)
mmv = lambda v: (pcbnew.ToMM(v.x) - OX, pcbnew.ToMM(v.y) - OY)
for fp in b.GetFootprints():
    for p in fp.Pads():
        n = p.GetNetname()
        if not n: continue
        bb = p.GetBoundingBox(); x1, y1 = mmv(bb.GetOrigin()); x2, y2 = mmv(bb.GetEnd())
        sides = ['F', 'B'] if p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH else (['B'] if fp.IsFlipped() else ['F'])
        for s in sides: geo[(n, s)].append(box(x1, y1, x2, y2))
for t in b.GetTracks():
    n = t.GetNetname(); L = t.GetLayer()
    if t.GetClass() == 'PCB_VIA':
        for s in 'FB': geo[(n, s)].append(Point(*mmv(t.GetPosition())).buffer(pcbnew.ToMM(t.GetWidth()) / 2))
    elif L in (pcbnew.F_Cu, pcbnew.B_Cu):
        geo[(n, 'F' if L == pcbnew.F_Cu else 'B')].append(LineString([mmv(t.GetStart()), mmv(t.GetEnd())]).buffer(pcbnew.ToMM(t.GetWidth()) / 2))
U = {k: unary_union(v) for k, v in geo.items()}
res = {'uncoated': [], 'coated': []}; tight = []
keys = list(U)
for (a, sa), (c, sc) in itertools.combinations(keys, 2):
    if sa != sc or a == c: continue
    pa, pc = a in PRIMARY, c in PRIMARY
    if pa != pc:
        d = U[(a, sa)].distance(U[(c, sc)])
        tight.append((d - 20, a, c, sa, round(d, 2), 20, 'pri-sec'))
        for k in res:
            if d < 20: res[k].append((a, c, sa, round(d, 2), 20, 'pri-sec'))
        continue
    if a not in HVSET and c not in HVSET: continue
    v = dv(a, c)
    if v < 50: continue
    d = U[(a, sa)].distance(U[(c, sc)])
    for k, f in (('uncoated', creep_uncoated), ('coated', creep_coated)):
        r = f(v)
        if d < r: res[k].append((a, c, sa, round(d, 2), round(r, 2), f'{v:.0f} V'))
    tight.append((d - creep_uncoated(v), a, c, sa, round(d, 2), round(creep_uncoated(v), 2), f'{v:.0f} V'))
tight.sort()
print('pri-sec min distance:', min(t[4] for t in tight if t[6] == 'pri-sec'), 'mm (need 20)')
for k, l in res.items():
    print(f'{k}: {len(l)} pairs below IEC 60664-1 creepage (working 2200 V DC)')
    for f in l: print('   ', f)
