"""Hand fixes for the last connections, checked with the same clearance test as finish.py."""
import sys; sys.path.insert(0, __import__('os').path.dirname(__file__))
REPORT = sys.argv[2]
sys.argv = [sys.argv[0], sys.argv[1], '/dev/null']
exec(open(__import__('os').path.join(__import__('os').path.dirname(__file__), 'finish.py')).read().split("rep = open")[0])
def try_path(n, pts, layer, w=0.2):
    g = geom(pts, w); obs = obstacles(n, layer); d = g.distance(obs)
    good = d >= CL and BOARD.contains(g)
    print(n, 'path', [tuple(round(v, 2) for v in p) for p in pts], 'clear', round(d, 3), 'OK' if good else 'BLOCKED')
    if good: add_track(n, pts, layer, w)
    return good
def blockers(n, pts, layer, w=0.2):
    g = geom(pts, w)
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname() == n: continue
            if p.IsOnLayer(layer) or p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                bb = p.GetBoundingBox(); x1, y1 = mv(bb.GetOrigin()); x2, y2 = mv(bb.GetEnd())
                d = g.distance(box(x1, y1, x2, y2))
                if d < CL: print('   pad', fp.GetReference(), p.GetNumber(), p.GetNetname(), round(d, 2))
    for t in b.GetTracks():
        if t.GetNetname() == n: continue
        if t.GetClass() == 'PCB_VIA': gg = Point(*mv(t.GetPosition())).buffer(pcbnew.ToMM(t.GetWidth()) / 2)
        elif t.GetLayer() == layer: gg = LineString([mv(t.GetStart()), mv(t.GetEnd())]).buffer(pcbnew.ToMM(t.GetWidth()) / 2)
        else: continue
        d = g.distance(gg)
        if d < CL: print('   ', t.GetClass(), t.GetNetname(), [round(v, 2) for v in mv(t.GetStart()) + mv(t.GetEnd())], round(d, 2))
def via_ok(n, v):
    vg = Point(v).buffer(0.35)
    ds = {k: round(vg.distance(obstacles(n, Ly)), 3) for k, Ly in L.items()}
    near = min([Point(v).distance(Point(*mv(t.GetPosition()))) for t in b.GetTracks() if t.GetClass() == 'PCB_VIA'] + [9])
    print('  via', v, ds, 'nearest via', round(near, 2)); return min(ds.values()) >= CL and near >= 0.75
def quiet_via_ok(n, v, obsAll, vias):
    vg = Point(v).buffer(0.35)
    return all(vg.distance(o) >= CL for o in obsAll.values()) and min([math.dist(v, q) for q in vias] + [9]) >= 0.75 and BOARD.contains(vg)
def via_bridge(n, pa, pb, pad_layer, mids=('B.Cu', 'In2.Cu')):
    obsAll = {k: obstacles(n, Ly) for k, Ly in L.items()}
    vias = [mv(t.GetPosition()) for t in b.GetTracks() if t.GetClass() == 'PCB_VIA']
    def sites(p):
        out = []
        for dx in [x * 0.2 for x in range(-12, 13)]:
            for dy in [y * 0.2 for y in range(-12, 13)]:
                v = (p[0] + dx, p[1] + dy)
                if 0.7 <= math.hypot(dx, dy) <= 2.4 and quiet_via_ok(n, v, obsAll, vias):
                    for pth in ([p, v], [p, (v[0], p[1]), v], [p, (p[0], v[1]), v]):
                        if ok(geom(pth), obsAll[pad_layer]): out.append((math.hypot(dx, dy), v, pth)); break
        return sorted(out)[:20]
    for _, va, pa_path in sites(pa):
        for _, vb, pb_path in sites(pb):
            if math.dist(va, vb) < 0.75: continue
            for mid in mids:
                for pth in paths(va, vb):
                    if ok(geom(pth), obsAll[mid]):
                        add_track(n, pa_path, L[pad_layer], TW); add_via(n, *va); add_track(n, pth, L[mid], TW); add_via(n, *vb); add_track(n, pb_path, L[pad_layer], TW)
                        print(n, 'bridged via', [round(x, 2) for x in va], [round(x, 2) for x in vb], 'on', mid); return True
    print(n, 'NOT bridged'); return False
import re as _re
rep_ = open(sys.argv[-1] if False else REPORT).read()
for m in _re.finditer(r'\[unconnected_items\].*?\n.*?\n\s+@\(([\d.]+) mm, ([\d.]+) mm\): (\w[^\[]*)\[([^\]]+)\][^\n]* on (\w\.Cu)[^\n]*\n\s+@\(([\d.]+) mm, ([\d.]+) mm\): (\w[^\[]*)\[([^\]]+)\][^\n]* on (\w\.Cu)', rep_):
    x1, y1, k1, n1, l1, x2, y2, k2, n2, l2 = m.groups()
    if n1 != n2 or k1.startswith('Zone') or l1 != l2: continue
    via_bridge(n1, (float(x1) - O, float(y1) - O), (float(x2) - O, float(y2) - O), l1, ('B.Cu', 'F.Cu', 'In2.Cu', 'In3.Cu'))
pcbnew.SaveBoard(sys.argv[1], b)
