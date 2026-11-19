"""Close the last unconnected pairs that KiCad's DRC reports, with short tracks (outer layer)
or via + inner-layer (In2/In3) + via jumps. Every candidate keeps >= 0.25 mm to all other-net
copper on the layers it touches. Usage: finish.py board.kicad_pcb drc_report.rpt"""
import pcbnew, sys, re, math, itertools
from shapely.geometry import box, LineString, Point
from shapely.ops import unary_union
b = pcbnew.LoadBoard(sys.argv[1]); O = 100.0; CL = 0.21; TW = 0.2
mm = pcbnew.FromMM; mv = lambda v: (pcbnew.ToMM(v.x) - O, pcbnew.ToMM(v.y) - O)
L = {'F.Cu': pcbnew.F_Cu, 'B.Cu': pcbnew.B_Cu, 'In2.Cu': pcbnew.In2_Cu, 'In3.Cu': pcbnew.In3_Cu}
OUTLINE = None
sys.path.insert(0, __import__('os').path.dirname(__file__))
from mech import OUTLINE as _OL
from shapely.geometry import Polygon
BOARD = Polygon(_OL).buffer(-0.6)
def obstacles(net, layer):
    g = []
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname() == net and p.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH: continue
            if p.IsOnLayer(layer) or p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
                bb = p.GetBoundingBox(); x1, y1 = mv(bb.GetOrigin()); x2, y2 = mv(bb.GetEnd()); g.append(box(x1, y1, x2, y2))
    for t in b.GetTracks():
        if t.GetNetname() == net: continue
        w = pcbnew.ToMM(t.GetWidth()) / 2
        if t.GetClass() == 'PCB_VIA': g.append(Point(*mv(t.GetPosition())).buffer(w))
        elif t.GetLayer() == layer: g.append(LineString([mv(t.GetStart()), mv(t.GetEnd())]).buffer(w))
    return unary_union(g)
ALL_LAYERS = [pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.In3_Cu, pcbnew.In4_Cu, pcbnew.B_Cu]
def islands(net):
    """list of (layer, geometry, comp_id) for all copper of a net, grouped into connected islands"""
    items = []
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname() != net: continue
            bb = p.GetBoundingBox(); x1, y1 = mv(bb.GetOrigin()); x2, y2 = mv(bb.GetEnd()); g = box(x1, y1, x2, y2)
            items.append(([Ly for Ly in ALL_LAYERS if p.IsOnLayer(Ly)] if p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD else ALL_LAYERS, g))
    for t in b.GetTracks():
        if t.GetNetname() != net: continue
        w = pcbnew.ToMM(t.GetWidth()) / 2
        if t.GetClass() == 'PCB_VIA': items.append((ALL_LAYERS, Point(*mv(t.GetPosition())).buffer(w)))
        else: items.append(([t.GetLayer()], LineString([mv(t.GetStart()), mv(t.GetEnd())]).buffer(w)))
    for z in b.Zones():
        if z.GetNetname() != net: continue
        for Ly in ALL_LAYERS:
            if not z.IsOnLayer(Ly): continue
            ps = z.GetFilledPolysList(Ly)
            for i in range(ps.OutlineCount()):
                o = ps.Outline(i); pts = [mv(o.CPoint(k)) for k in range(o.PointCount())]
                if len(pts) >= 3: items.append(([Ly], Polygon(pts).buffer(0)))
    par = list(range(len(items)))
    def f(i):
        while par[i] != i: par[i] = par[par[i]]; i = par[i]
        return i
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if set(items[i][0]) & set(items[j][0]) and items[i][1].distance(items[j][1]) < 1e-3: par[f(i)] = f(j)
    return [(items[i][0], items[i][1], f(i)) for i in range(len(items))]
def comp_at(isl, pt, layer):
    for ls, g, c in isl:
        if layer in ls and g.distance(Point(pt)) < 0.05: return c
def add_track(n, pts, layer, w=0.25):
    for a, c in zip(pts, pts[1:]):
        if a == c: continue
        t = pcbnew.PCB_TRACK(b); t.SetStart(pcbnew.VECTOR2I(mm(a[0] + O), mm(a[1] + O))); t.SetEnd(pcbnew.VECTOR2I(mm(c[0] + O), mm(c[1] + O)))
        t.SetLayer(layer); t.SetWidth(mm(w)); t.SetNet(b.FindNet(n)); b.Add(t)
def add_via(n, x, y):
    v = pcbnew.PCB_VIA(b); v.SetPosition(pcbnew.VECTOR2I(mm(x + O), mm(y + O))); v.SetWidth(mm(0.7)); v.SetDrill(mm(0.35)); v.SetNet(b.FindNet(n)); b.Add(v)
def paths(a, c):
    out = [[a, c], [a, (a[0], c[1]), c], [a, (c[0], a[1]), c]]
    for k in [x * 0.4 for x in range(-12, 13)]:
        out.append([a, (a[0] + k, a[1]), (a[0] + k, c[1]), c]); out.append([a, (a[0], a[1] + k), (c[0], a[1] + k), c])
    return out
def esc(p):
    yield p
    for e in (0.7, 1.0, 1.4, 2.0):
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)): yield (p[0] + dx * e, p[1] + dy * e)
def full_paths(a, c):
    for a2 in esc(a):
        for c2 in esc(c):
            for mid in paths(a2, c2):
                yield [a] + mid + [c]
def geom(path, w=TW): return unary_union([LineString([p, q]).buffer(w / 2) for p, q in zip(path, path[1:]) if p != q])
def ok(g, obs): return BOARD.contains(g) and g.distance(obs) >= CL
rep = open(sys.argv[2]).read()
pairs = re.findall(r'\[unconnected_items\].*?\n.*?\n\s+@\(([\d.]+) mm, ([\d.]+) mm\): (\w[^\[]*)\[([^\]]+)\][^\n]* on (\w\.Cu)[^\n]*\n\s+@\(([\d.]+) mm, ([\d.]+) mm\): (\w[^\[]*)\[([^\]]+)\][^\n]* on (\w\.Cu)', rep)
done = 0
for x1, y1, k1, n1, l1, x2, y2, k2, n2, l2 in pairs:
    if n1 != n2 or k1.startswith('Zone'): continue
    n = n1; a0 = (float(x1) - O, float(y1) - O); c0 = (float(x2) - O, float(y2) - O); fixed = False
    def ends(pt):
        out = [pt]
        for t in b.GetTracks():
            if t.GetNetname() == n and t.GetClass() != 'PCB_VIA':
                s_, e_ = mv(t.GetStart()), mv(t.GetEnd())
                if LineString([s_, e_]).distance(Point(pt)) < 0.05: out += [s_, e_]
        return out
    if l1 != l2 and k1.startswith('Track') and k2.startswith('Track') and math.dist(a0, c0) < 0.8:
        # a layer change that lost its via: drop one at the end that is clear on every layer
        obsAll = {k: obstacles(n, v) for k, v in L.items()}
        for cand in ends(a0) + ends(c0):
            vg = Point(cand).buffer(0.35)
            if all(vg.distance(o) >= CL for o in obsAll.values()):
                add_via(n, *cand); fixed = True; break
    if not fixed:
        # plane net: a via beside either pad drops it onto its plane
        planes = [z for z in b.Zones() if z.GetNetname() == n and z.GetLayer() not in (pcbnew.F_Cu, pcbnew.B_Cu)]
        if planes:
            obsAll = {k: obstacles(n, v) for k, v in L.items()}
            nfix = 0
            for pt, lay in ((a0, l1), (c0, l2)):
                fixed = False
                for d in [0.8 + 0.2 * i for i in range(14)]:
                    for ang in range(0, 360, 15):
                        v = (pt[0] + d * math.cos(math.radians(ang)), pt[1] + d * math.sin(math.radians(ang)))
                        vg = Point(v).buffer(0.35)
                        if any(Point(v).distance(Point(*mv(t.GetPosition()))) < 0.75 for t in b.GetTracks() if t.GetClass() == 'PCB_VIA'): continue
                        if not any(z.HitTestFilledArea(z.GetLayer(), pcbnew.VECTOR2I(mm(v[0] + O), mm(v[1] + O))) for z in planes): continue
                        if all(vg.distance(o) >= CL for o in obsAll.values()) and ok(geom([pt, v], 0.3), obsAll[lay]):
                            add_track(n, [pt, v], L[lay], 0.3); add_via(n, *v); fixed = True; break
                    if fixed: break
                nfix += fixed
            fixed = nfix == 2
    if not fixed and l1 == l2:
        obs = obstacles(n, L[l1])
        for a in ends(a0):
            for c in ends(c0):
                for pth in full_paths(a, c):
                    if ok(geom(pth), obs): add_track(n, pth, L[l1], TW); fixed = True; break
                if fixed: break
            if fixed: break
    a, c = a0, c0
    if not fixed:
        # via jump: pad -> via (own layer), via -> via on In2/In3, via -> pad
        obsAll = {k: obstacles(n, v) for k, v in L.items()}
        def via_sites(p, lay):
            for d in [0.8 + 0.2 * i for i in range(18)]:
                for ang in range(0, 360, 15):
                    v = (p[0] + d * math.cos(math.radians(ang)), p[1] + d * math.sin(math.radians(ang)))
                    vg = Point(v).buffer(0.35)
                    if any(Point(v).distance(Point(*mv(t.GetPosition()))) < 0.75 for t in b.GetTracks() if t.GetClass() == 'PCB_VIA'): continue
                    if all(vg.distance(o) >= CL for o in obsAll.values()) and BOARD.contains(vg):
                        for e2 in esc(p):
                            for pp in ([p, e2, v], [p, e2, (e2[0], v[1]), v], [p, e2, (v[0], e2[1]), v]):
                                if ok(geom(pp), obsAll[lay]): yield (v, pp[1:-1]); break
                            else: continue
                            break
                if d == 0: continue
        for va, ea in itertools.islice(via_sites(a, l1), 25):
            for vc, ec in itertools.islice(via_sites(c, l2), 25):
                for inner in ('In2.Cu', 'In3.Cu'):
                    for pth in paths(va, vc)[:30]:
                        if ok(geom(pth), obsAll[inner]) and geom(pth).distance(obsAll[inner]) >= CL:
                            add_track(n, [a] + list(ea) + [va], L[l1], TW); add_via(n, *va); add_track(n, pth, L[inner], TW); add_via(n, *vc); add_track(n, [vc] + list(ec)[::-1] + [c], L[l2], TW)
                            fixed = True; break
                    if fixed: break
                if fixed: break
            if fixed: break
    if not fixed:
        # last resort: tie either end to any nearby copper of the same net on its own layer
        for pt, lay in ((c0, l2), (a0, l1)):
            obs = obstacles(n, L[lay]); tg = []
            for fp in b.GetFootprints():
                for p_ in fp.Pads():
                    if p_.GetNetname() == n and p_.IsOnLayer(L[lay]): tg.append(mv(p_.GetPosition()))
            for t in b.GetTracks():
                if t.GetNetname() != n: continue
                if t.GetClass() == 'PCB_VIA': tg.append(mv(t.GetPosition()))
                elif t.GetLayer() == L[lay]: tg += [mv(t.GetStart()), mv(t.GetEnd())]
            isl = islands(n); me = comp_at(isl, pt, L[lay])
            tg = sorted({q for q in tg if 0.3 < math.dist(q, pt) < 8.0 and comp_at(isl, q, L[lay]) not in (None, me)}, key=lambda q: math.dist(q, pt))
            for q in tg:
                for pth in full_paths(pt, q):
                    if ok(geom(pth), obs): add_track(n, pth, L[lay], TW); fixed = True; break
                if fixed: break
            if fixed: break
    print(('fixed ' if fixed else 'OPEN  ') + n, a, c); done += fixed
pcbnew.SaveBoard(sys.argv[1], b); print('closed', done, 'of', len([p for p in pairs if p[3] == p[8] and not p[2].startswith('Zone')]))
