"""Hand-finish connections the autorouter left open: tries short track/via candidates
and keeps the first one with >= 0.25 mm clearance to all other-net copper (outer layers)."""
import pcbnew, sys, math
from shapely.geometry import box, LineString, Point
from shapely.ops import unary_union
b = pcbnew.LoadBoard(sys.argv[1]); O = 100.0
mm = pcbnew.FromMM; mv = lambda v: (pcbnew.ToMM(v.x) - O, pcbnew.ToMM(v.y) - O)
def copper(exclude, side):
    g = []
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname() == exclude: continue
            onF = p.IsOnLayer(pcbnew.F_Cu); onB = p.IsOnLayer(pcbnew.B_Cu)
            if (side == 'F' and onF) or (side == 'B' and onB):
                bb = p.GetBoundingBox(); x1, y1 = mv(bb.GetOrigin()); x2, y2 = mv(bb.GetEnd()); g.append(box(x1, y1, x2, y2))
            if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                bb = p.GetBoundingBox(); x1, y1 = mv(bb.GetOrigin()); x2, y2 = mv(bb.GetEnd()); g.append(box(x1, y1, x2, y2))
    for t in b.GetTracks():
        if t.GetNetname() == exclude: continue
        w = pcbnew.ToMM(t.GetWidth()) / 2
        if t.GetClass() == 'PCB_VIA': g.append(Point(*mv(t.GetPosition())).buffer(w))
        elif t.GetLayer() == (pcbnew.F_Cu if side == 'F' else pcbnew.B_Cu): g.append(LineString([mv(t.GetStart()), mv(t.GetEnd())]).buffer(w))
    return unary_union(g)
def pad(ref, num):
    for fp in b.GetFootprints():
        if fp.GetReference() == ref:
            for p in fp.Pads():
                if p.GetNumber() == num: return p
def net(n): return b.FindNet(n)
def add_track(n, pts, layer=pcbnew.F_Cu, w=0.25):
    for a, c in zip(pts, pts[1:]):
        t = pcbnew.PCB_TRACK(b); t.SetStart(pcbnew.VECTOR2I(mm(a[0] + O), mm(a[1] + O))); t.SetEnd(pcbnew.VECTOR2I(mm(c[0] + O), mm(c[1] + O)))
        t.SetLayer(layer); t.SetWidth(mm(w)); t.SetNet(net(n)); b.Add(t)
def add_via(n, x, y):
    v = pcbnew.PCB_VIA(b); v.SetPosition(pcbnew.VECTOR2I(mm(x + O), mm(y + O))); v.SetWidth(mm(0.7)); v.SetDrill(mm(0.35)); v.SetNet(net(n)); b.Add(v)
CL = 0.25
# 1) CMD_RC: R12.2 -> U5.2 on F.Cu
a = mv(pad('R12', '2').GetPosition()); c = mv(pad('U5', '2').GetPosition())
obsF = copper('CMD_RC', 'F'); done = len(sys.argv) > 2 and 'CMD_RC' not in sys.argv[2]
for mx in ([] if done else [a[0] + k * 0.1 for k in range(-8, 20)]):
    for path in ([a, (mx, a[1]), (mx, c[1]), c], [a, (a[0], c[1]), c], [a, (mx, c[1]), c]):
        g = unary_union([LineString([p, q]).buffer(0.125) for p, q in zip(path, path[1:])])
        if g.distance(obsF) >= CL: add_track('CMD_RC', path); done = True; print('CMD_RC fixed', [tuple(round(v, 2) for v in p) for p in path]); break
    if done: break
if not done: print('CMD_RC NOT fixed')
# 2) VEE at R36.2: short track + via into In1 VEE plane
p = pad('R36', '2'); a = mv(p.GetPosition())
for side in ('F',):
    obsF = copper('VEE', 'F'); obsB = copper('VEE', 'B'); done = len(sys.argv) > 2 and 'R36' not in sys.argv[2]
    for d in ([] if done else [0.9 + 0.1 * k for k in range(40)]):
        for ang in range(0, 360, 10):
            v = (a[0] + d * math.cos(math.radians(ang)), a[1] + d * math.sin(math.radians(ang)))
            if not (113 + 1 < v[0] < 131 - 1 and 14 + 1 < v[1]): continue
            via = Point(*v).buffer(0.35); trk = LineString([a, v]).buffer(0.2)
            if via.distance(obsF) >= CL and via.distance(obsB) >= CL and trk.distance(obsF) >= CL:
                add_track('VEE', [a, v], w=0.4); add_via('VEE', *v); done = True; print('VEE R36 fixed via at', [round(x, 2) for x in v]); break
        if done: break
    if not done: print('VEE R36 NOT fixed')
pcbnew.SaveBoard(sys.argv[1], b)
# 3) fallback: connect R36.2 to the nearest VEE copper on F.Cu with an L or straight track
if not done:
    targets = []
    for t in b.GetTracks():
        if t.GetNetname() != 'VEE': continue
        if t.GetClass() == 'PCB_VIA': targets.append(mv(t.GetPosition()))
        elif t.GetLayer() == pcbnew.F_Cu: targets += [mv(t.GetStart()), mv(t.GetEnd())]
    for fp in b.GetFootprints():
        for q in fp.Pads():
            if q.GetNetname() == 'VEE' and q.IsOnLayer(pcbnew.F_Cu) and fp.GetReference() != 'R36': targets.append(mv(q.GetPosition()))
    targets.sort(key=lambda t: (t[0] - a[0]) ** 2 + (t[1] - a[1]) ** 2)
    for tg in targets[:15]:
        for path in ([a, tg], [a, (a[0], tg[1]), tg], [a, (tg[0], a[1]), tg]):
            g = unary_union([LineString([p_, q_]).buffer(0.15) for p_, q_ in zip(path, path[1:])])
            if g.distance(obsF) >= CL:
                add_track('VEE', path, w=0.3); done = True; print('VEE R36 linked to', [round(v, 2) for v in tg]); break
        if done: break
    if not done: print('VEE R36 still open')
    pcbnew.SaveBoard(sys.argv[1], b)
# 4b) neck any track that comes within the coated creepage (0.49 mm at 231 V) of CH6 copper
ch6 = []
for f_ in b.GetFootprints():
    for p_ in f_.Pads():
        if p_.GetNetname() == 'CH6' and p_.IsOnLayer(pcbnew.F_Cu):
            bb_ = p_.GetBoundingBox(); a1, b1 = mv(bb_.GetOrigin()); a2, b2 = mv(bb_.GetEnd()); ch6.append(box(a1, b1, a2, b2))
for t in b.GetTracks():
    if t.GetNetname() == 'CH6' and t.GetClass() != 'PCB_VIA' and t.GetLayer() == pcbnew.F_Cu:
        ch6.append(LineString([mv(t.GetStart()), mv(t.GetEnd())]).buffer(pcbnew.ToMM(t.GetWidth()) / 2))
ch6 = unary_union(ch6); n4b = 0
for t in b.GetTracks():
    if t.GetNetname() in ('CH6', '') or t.GetClass() == 'PCB_VIA' or t.GetLayer() != pcbnew.F_Cu: continue
    w_ = pcbnew.ToMM(t.GetWidth())
    if w_ > 0.2 and LineString([mv(t.GetStart()), mv(t.GetEnd())]).buffer(w_ / 2).distance(ch6) < 0.52:
        t.SetWidth(mm(0.2)); n4b += 1
print('tracks necked near CH6', n4b)
# 4) neck DGOFF down to 0.2 mm where it passes D40's CH6 pad (coated creepage 0.49 mm at 231 V)
d40 = [p_ for f_ in b.GetFootprints() if f_.GetReference() == 'D40' for p_ in f_.Pads() if p_.GetNetname() == 'CH6'][0]
bb = d40.GetBoundingBox(); x1, y1 = mv(bb.GetOrigin()); x2, y2 = mv(bb.GetEnd()); pg = box(x1, y1, x2, y2); n4 = 0
for t in b.GetTracks():
    if t.GetNetname() == 'DGOFF' and t.GetClass() != 'PCB_VIA' and t.GetLayer() == pcbnew.F_Cu:
        if LineString([mv(t.GetStart()), mv(t.GetEnd())]).distance(pg) < 0.49 + pcbnew.ToMM(t.GetWidth()) / 2:
            t.SetWidth(mm(0.2)); n4 += 1
print('DGOFF segments necked', n4)
pcbnew.SaveBoard(sys.argv[1], b)
# 5) stitch the primary PGND pours (F, B, In1) together with a via grid
from shapely.geometry import Polygon
PRI = Polygon([(185.9, 3.0), (196.5, 3.0), (196.5, 43.3), (191.0, 43.3), (191.0, 40.3), (177.7, 40.3), (177.7, 33.0), (185.9, 33.0)]).buffer(-0.8)
oF = copper('PGND', 'F'); oB = copper('PGND', 'B'); nv = 0
for f_ in b.GetFootprints():
    for p_ in f_.Pads():
        if p_.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
            hb = Point(*mv(p_.GetPosition())).buffer(max(pcbnew.ToMM(p_.GetSize().x), pcbnew.ToMM(p_.GetSize().y)) / 2 + 0.3)
            oF = oF.union(hb); oB = oB.union(hb)
for t in b.GetTracks():
    if t.GetClass() == 'PCB_VIA':
        hb = Point(*mv(t.GetPosition())).buffer(0.9); oF = oF.union(hb); oB = oB.union(hb)
hole = Point(193.59, 39.99).buffer(1.6 + 0.6)
import numpy as _np
for x in _np.arange(178.5, 196.5, 1.6):
    for y in _np.arange(3.8, 43.0, 1.6):
        v = Point(x, y)
        if not PRI.contains(v) or v.distance(hole) < 0.4: continue
        c = v.buffer(0.35)
        if c.distance(oF) >= 0.35 and c.distance(oB) >= 0.35:
            add_via('PGND', float(x), float(y)); nv += 1; oF = oF.union(v.buffer(0.35 + 0.8)); oB = oB.union(v.buffer(0.35 + 0.8))
print('PGND stitching vias', nv)
pcbnew.SaveBoard(sys.argv[1], b)
