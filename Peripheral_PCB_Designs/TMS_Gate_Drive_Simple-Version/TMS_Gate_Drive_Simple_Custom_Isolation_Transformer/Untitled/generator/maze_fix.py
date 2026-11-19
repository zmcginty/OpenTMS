"""Grid maze router for the last unconnected pairs in a KiCad DRC report.
Fine grid (0.05 mm), 6 layers (plane layers In1/In4 allowed at a high cost), 8-connected moves,
vias only where clear on every layer. Keeps CL to other-net copper and the creepage distance
from HV nets (potentials.creep_route). Usage: maze_fix.py board.kicad_pcb drc.rpt"""
import pcbnew, sys, re, math, heapq, os
import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import box, LineString, Point, Polygon
sys.path.insert(0, os.path.dirname(__file__))
from mech import OUTLINE
from potentials import HVSET, dv, creep_route
PCB = sys.argv[1]; b = pcbnew.LoadBoard(PCB); O = 100.0
mm = pcbnew.FromMM; mv = lambda v: (pcbnew.ToMM(v.x) - O, pcbnew.ToMM(v.y) - O)
R = 0.05; TW = 0.2; CL = 0.25; VD = 0.6; VDR = 0.3
LAY = [pcbnew.F_Cu, pcbnew.In2_Cu, pcbnew.In3_Cu, pcbnew.B_Cu, pcbnew.In1_Cu, pcbnew.In4_Cu]
LCOST = [1.0, 1.3, 1.3, 1.0, 8.0, 8.0]
VCOST = 30.0
BOARD = Polygon(OUTLINE).buffer(-0.5)
NL = len(LAY)

def pad_poly(p):
    try:
        o = p.GetEffectivePolygon().Outline(0)
        return Polygon([mv(o.CPoint(k)) for k in range(o.PointCount())]).buffer(0)
    except Exception:
        bb = p.GetBoundingBox(); x1, y1 = mv(bb.GetOrigin()); x2, y2 = mv(bb.GetEnd()); return box(x1, y1, x2, y2)

def copper_items():
    """list of (net, set of layer indices, geometry, is_pad)"""
    out = []
    for fp in b.GetFootprints():
        for p in fp.Pads():
            th = p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH)
            g = pad_poly(p)
            if th:
                dr = pcbnew.ToMM(max(p.GetDrillSize().x, p.GetDrillSize().y)) / 2
                g = g.union(Point(*mv(p.GetPosition())).buffer(dr))
                ls = set(range(NL))
            else:
                ls = {i for i, L in enumerate(LAY) if p.IsOnLayer(L)}
            out.append((p.GetNetname(), ls, g, True))
    for t in b.GetTracks():
        w = pcbnew.ToMM(t.GetWidth()) / 2
        if t.GetClass() == 'PCB_VIA':
            out.append((t.GetNetname(), set(range(NL)), Point(*mv(t.GetPosition())).buffer(w), False))
        elif t.GetLayer() in LAY:
            out.append((t.GetNetname(), {LAY.index(t.GetLayer())}, LineString([mv(t.GetStart()), mv(t.GetEnd())]).buffer(w), False))
    return out

def route(net, a0, c0, margin=6.0):
    items = copper_items()
    own = [(ls, g, ip) for n, ls, g, ip in items if n == net]
    par = list(range(len(own)))
    def f(i):
        while par[i] != i: par[i] = par[par[i]]; i = par[i]
        return i
    for i in range(len(own)):
        for j in range(i + 1, len(own)):
            if own[i][0] & own[j][0] and own[i][1].distance(own[j][1]) < 1e-3: par[f(i)] = f(j)
    def comp(pt):
        best = min(range(len(own)), key=lambda i: own[i][1].distance(Point(pt)))
        return f(best)
    ca, cc = comp(a0), comp(c0)
    if ca == cc: return 'already'
    x0 = min(a0[0], c0[0]) - margin; y0 = min(a0[1], c0[1]) - margin
    x1 = max(a0[0], c0[0]) + margin; y1 = max(a0[1], c0[1]) + margin
    W = int((x1 - x0) / R) + 1; H = int((y1 - y0) / R) + 1
    win = box(x0 - 4, y0 - 4, x1 + 4, y1 + 4)
    def raster(geoms):
        im = Image.new('L', (W, H), 0); d = ImageDraw.Draw(im)
        for g in geoms:
            if g.is_empty: continue
            for pg in (g.geoms if hasattr(g, 'geoms') else [g]):
                if pg.geom_type != 'Polygon': continue
                d.polygon([((x - x0) / R, (y - y0) / R) for x, y in pg.exterior.coords], fill=1)
        return np.array(im) > 0
    blk = np.zeros((NL, H, W), bool); vblk = np.zeros((H, W), bool)
    src = np.zeros((NL, H, W), bool); dst = np.zeros((NL, H, W), bool); ownpad = np.zeros((H, W), bool)
    for li in range(NL):
        ot, ov = [], []
        for n, ls, g, ip in items:
            if li not in ls or not g.intersects(win): continue
            if n == net: continue
            gap = CL
            if n in HVSET or net in HVSET: gap = max(gap, creep_route(dv(net, n)))
            ot.append(g.buffer(gap + TW / 2 + 0.04, 4)); ov.append(g.buffer(gap + VD / 2 + 0.04, 4))
        blk[li] = raster(ot); vblk |= raster(ov)
        src[li] = raster([g for k, (ls, g, ip) in enumerate(own) if li in ls and f(k) == ca])
        dst[li] = raster([g for k, (ls, g, ip) in enumerate(own) if li in ls and f(k) == cc])
    ownpad = raster([g for ls, g, ip in own if ip])
    out = ~raster([BOARD]); blk |= out[None]; vblk |= out
    free = ~blk | src | dst
    # A* from all src cells to any dst cell
    tgt = np.argwhere(dst)
    if len(tgt) == 0 or not src.any(): return 'no-geom'
    tc = tgt.mean(axis=0)
    INF = 1e18; dist = np.full((NL, H, W), INF); prev = {}
    pq = []
    for l, y, x in np.argwhere(src & free):
        dist[l, y, x] = 0; heapq.heappush(pq, (math.hypot(y - tc[1], x - tc[2]) * 0.5, 0.0, int(l), int(y), int(x)))
    moves = [(0, 1, 1), (0, -1, 1), (1, 0, 1), (-1, 0, 1), (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414)]
    end = None; it = 0
    while pq:
        h, d, l, y, x = heapq.heappop(pq)
        if d > dist[l, y, x]: continue
        if dst[l, y, x]: end = (l, y, x); break
        it += 1
        if it > 6_000_000: break
        for dy, dx, c in moves:
            ny, nx = y + dy, x + dx
            if 0 <= ny < H and 0 <= nx < W and free[l, ny, nx]:
                nd = d + c * LCOST[l]
                if nd < dist[l, ny, nx]:
                    dist[l, ny, nx] = nd; prev[(l, ny, nx)] = (l, y, x)
                    heapq.heappush(pq, (nd + 0.5 * math.hypot(ny - tc[1], nx - tc[2]), nd, l, ny, nx))
        if not vblk[y, x] and not ownpad[y, x]:
            for nl in range(NL):
                if nl != l and free[nl, y, x]:
                    nd = d + VCOST
                    if nd < dist[nl, y, x]:
                        dist[nl, y, x] = nd; prev[(nl, y, x)] = (l, y, x)
                        heapq.heappush(pq, (nd + 0.5 * math.hypot(y - tc[1], x - tc[2]), nd, nl, y, x))
    if end is None: return 'fail'
    path = [end]
    while path[-1] in prev: path.append(prev[path[-1]])
    path.reverse()
    P = lambda c: (x0 + c[2] * R, y0 + c[1] * R)
    # emit: split into per-layer runs, merge collinear steps
    runs = [[path[0]]]
    for c in path[1:]:
        if c[0] != runs[-1][-1][0]: runs.append([c])
        else: runs[-1].append(c)
    ntr = nv = 0
    for k, run in enumerate(runs):
        if k > 0:
            v = pcbnew.PCB_VIA(b); v.SetPosition(pcbnew.VECTOR2I(mm(P(run[0])[0] + O), mm(P(run[0])[1] + O)))
            v.SetWidth(mm(VD)); v.SetDrill(mm(VDR)); v.SetNet(b.FindNet(net)); b.Add(v); nv += 1
        pts = [run[0]]
        for i in range(1, len(run) - 1):
            d1 = (run[i][1] - run[i - 1][1], run[i][2] - run[i - 1][2]); d2 = (run[i + 1][1] - run[i][1], run[i + 1][2] - run[i][2])
            if d1 != d2: pts.append(run[i])          # keep corners only
        pts.append(run[-1])
        for a, c in zip(pts, pts[1:]):
            if a == c: continue
            t = pcbnew.PCB_TRACK(b); t.SetStart(pcbnew.VECTOR2I(mm(P(a)[0] + O), mm(P(a)[1] + O))); t.SetEnd(pcbnew.VECTOR2I(mm(P(c)[0] + O), mm(P(c)[1] + O)))
            t.SetLayer(LAY[run[0][0]]); t.SetWidth(mm(TW)); t.SetNet(b.FindNet(net)); b.Add(t); ntr += 1
    return f'ok tracks {ntr} vias {nv}'

if __name__ == '__main__':
    rep = open(sys.argv[2]).read()
    pairs = re.findall(r'\[unconnected_items\].*?\n.*?\n\s+@\(([\d.]+) mm, ([\d.]+) mm\): [^\[]*\[([^\]]+)\][^\n]*\n\s+@\(([\d.]+) mm, ([\d.]+) mm\): [^\[]*\[([^\]]+)\]', rep)
    for x1, y1, n1, x2, y2, n2 in pairs:
        if n1 != n2: continue
        a0 = (float(x1) - O, float(y1) - O); c0 = (float(x2) - O, float(y2) - O)
        r = route(n1, a0, c0)
        if r == 'fail': r = route(n1, a0, c0, margin=12.0)
        print(n1, a0, c0, r, flush=True)
    pcbnew.SaveBoard(PCB, b)
