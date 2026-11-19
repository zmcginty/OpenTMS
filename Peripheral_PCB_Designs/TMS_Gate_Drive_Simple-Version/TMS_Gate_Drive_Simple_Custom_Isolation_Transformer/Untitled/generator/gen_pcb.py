"""Build the PCB with pcbnew (KiCad 7): outline, placement, nets, plane stitching, routing, zones.
Run with /usr/bin/python3 (system python that has the pcbnew module)."""
import sys, os, json, math, heapq, re, collections
import numpy as np
import faulthandler; faulthandler.enable()
import pcbnew
sys.path.insert(0, os.path.dirname(__file__))
from circuit import parts
from mech import MOUNT, BOARD_W, BOARD_H, OUTLINE, SUPPORT_HOLE, SLOT
from potentials import dv as pot_dv, creep_uncoated, creep_route, HVSET
PRJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OX, OY = 100.0, 100.0           # board origin on the KiCad canvas
mm = pcbnew.FromMM
def P(x, y): return pcbnew.VECTOR2I(mm(OX + x), mm(OY + y))

# ---------------- placement (board-local mm, rotation deg) ----------------
POS = {}
def at(r, x, y, rot=0): POS[r] = (x, y, rot)
# Layout follows the 1SP0635 arrangement: collector terminal at the left end, emitter/gate
# terminals at the top edge, logic + fibers in the middle, isolation transformer and the
# primary (supply) side at the right end.
# --- primary side (x >= 177, right of the transformer)
at('T1', 184.0, 14.0, 180)
at('J1', 178.6, 38.6, 90); at('J2', 192.7, 33.8, 90)
BOT = set()
def atb(r, x, y, rot=0): at(r, x, y, rot); BOT.add(r)
# primary cluster is hand-placed (fixed): strip x 186..196.6 right of the transformer
at('F1', 190.0, 26.2, 0); at('D1', 190.0, 22.0, 0); atb('D2', 194.3, 9.5, 90); atb('C1', 190.0, 22.5, 0); at('C2', 194.0, 6.0, 0)
at('U1', 190.0, 18.0, 0); at('C3', 190.0, 15.0, 0); at('R1', 194.6, 19.9, 270)
at('R2', 194.4, 15.4, 90); at('C4', 196.0, 15.4, 90); at('R3', 189.4, 13.2, 0); at('R4', 192.6, 12.95, 0)
PRI_FIXED = {'F1', 'D1', 'D2', 'C1', 'C2', 'U1', 'C3', 'R1', 'R2', 'R3', 'R4', 'C4'}
# --- secondary power (x 129..158, above the cut-away)
at('D4', 152.5, 6.0, 180); at('D6', 152.5, 9.4, 0); at('D3', 152.5, 15.6, 180); at('D5', 152.5, 19.0, 0)
for i, r in enumerate(['C10', 'C11', 'C12']): at(r, 145.6, 3.4 + i * 4.0, 0)
for i, r in enumerate(['C16', 'C17', 'C18']): at(r, 140.4, 3.4 + i * 4.0, 0)
at('C22', 145.6, 15.0, 0); at('C23', 140.4, 15.0, 0)
at('U2', 133.6, 7.0, 90); at('C24', 131.0, 13.6, 90); at('C25', 133.6, 13.6, 90); at('C26', 136.2, 13.6, 90)
at('TP1', 131.0, 18.2); at('TP2', 133.6, 18.2); at('TP3', 136.2, 18.2); at('TP4', 138.8, 18.2)
# UVLO cluster on the bottom side under the logic block
atb('U20', 140.0, 10.0, 0); atb('C75', 140.0, 13.5, 0)
for i, r in enumerate(['R21', 'R22', 'R23', 'R24', 'R25', 'R26']): atb(r, 133.5 + i * 3.2, 16.8, 0)
atb('D13', 145.0, 10.0, 0); atb('R28', 145.0, 13.5, 0); atb('D14', 135.0, 10.0, 0); atb('R29', 135.0, 13.5, 0)
# --- fibers at the bottom edge (ports facing out), logic above them (x 94..128)
# Versatile Link pitch 10.16 mm = body width, so the molded L-bracket/L-slot tabs interlock;
# pin 5/8 row 2.0 mm from the board edge (Broadcom: >= 1.85 mm), ports overhang the edge.
at('U3', 110.5, 34.32, 180); at('U18', 110.5 + 10.16, 34.32, 180)
at('R10', 104.0, 23.0, 0); at('C30', 106.6, 23.0, 0); at('R11', 109.2, 23.0, 0); at('C31', 111.8, 23.0, 0)
at('R14', 122.0, 23.0, 0); at('Q1', 118.6, 23.0, 0)
COLS = [98.0, 104.0, 110.0, 116.0, 122.0]
grid = [['U4', 'U5', 'U6', 'U7', 'U9'], ['U8', 'U10', 'U11', 'U12', 'U13'], ['U14', 'U15', 'U16', 'U17', None]]
dec = {'U4': 'C60', 'U5': 'C61', 'U6': 'C62', 'U7': 'C63', 'U8': 'C64', 'U9': 'C65', 'U10': 'C66', 'U11': 'C67', 'U12': 'C68', 'U13': 'C69', 'U14': 'C70', 'U15': 'C71', 'U16': 'C72', 'U17': 'C73'}
for ri, rowl in enumerate(grid):
    for ci, r in enumerate(rowl):
        if not r: continue
        y = 3.2 + ri * 6.4
        at(r, COLS[ci], y, 0); at(dec[r], COLS[ci], y + 3.1, 0)
at('R12', 101.0, 3.2, 90); at('C32', 101.0, 6.4, 90)
at('R13', 126.5, 13.0, 90); at('C33', 126.5, 16.5, 90); at('C34', 126.5, 9.6, 90)
at('JP1', 126.0, 3.0, 0); at('R27', 126.5, 6.5, 0)
at('TP5', 114.0, 23.0); at('TP6', 116.0, 23.0)
# --- comparators / references (x 86..100, y 28..40)
# rev D: moved from under the output stage (x 80) into the bottom-side area the fibers freed
atb('U19', 110.0, 26.0, 0); atb('C74', 110.0, 29.0, 0); atb('D10', 105.0, 26.0, 0); atb('D11', 115.0, 26.0, 0)
for i, r in enumerate(['R15', 'C35', 'R16', 'R17', 'C36']): atb(r, 103.5 + i * 3.2, 22.5, 0)
for i, r in enumerate(['R18', 'R19', 'R20', 'C37']): atb(r, 103.5 + i * 3.2, 31.6, 0)
# --- HV chains along the bottom, graded from the collector (left) toward the clamp/sense end
for i, r in enumerate(['D30', 'D31', 'D32', 'D33', 'D34', 'D35']): at(r, 5.7 + i * 10.2, 30.4, 0)
at('D40', 66.4, 30.4, 0)
for i, r in enumerate(['R30', 'R31', 'R32', 'R33', 'R34', 'R35']): at(r, 6.0 + i * 8.3, 38.4, 0)
at('R36', 55.0, 41.5, 0); at('D12', 59.0, 41.5, 0); atb('R37', 119.0, 26.0, 0); atb('C38', 119.0, 29.0, 0)
# --- DA2C switch and its timer
at('Q5', 65.5, 36.0, 180); at('D41', 76.0, 33.0, 90); at('C57', 78.0, 33.0, 90); at('D42', 80.5, 33.0, 90); at('R58', 82.8, 33.0, 90)
at('D43', 76.0, 37.5, 90); at('Q6', 79.0, 37.6, 0); at('D44', 82.5, 37.6, 90); at('R59', 84.5, 37.6, 90); at('C58', 86.5, 38.6, 0)
at('U23', 90.0, 38.0, 0); at('C76', 93.5, 38.6, 0)
# --- active-clamp feed and AAC transistor
at('D36', 67.0, 25.0, 90); at('R55', 71.5, 24.0, 0); at('R56', 75.5, 28.6, 0); at('TP7', 72.5, 28.2)
at('Q4', 79.0, 27.6, 0); at('R53', 76.5, 27.0, 90); at('R54', 81.6, 27.6, 90); at('D22', 84.0, 26.6, 0)
# --- output stage around the gate / emitter terminals
# gate power stage: Q2 (complementary OptiMOS dual) with VP/VEE bulk right at its source pins,
# U21 (P-gate driver) next to it, Q7/R60/R61/D20 level shifter further left
at('Q2', 90.0, 16.5, 0); at('C50', 86.0, 12.6, 0); at('C51', 86.0, 14.9, 0)
at('U21', 73.5, 19.5, 0); at('C49', 70.4, 19.5, 90); at('R62', 77.3, 18.6, 0); at('R63', 77.3, 20.4, 0); at('R64', 77.3, 22.2, 0)
at('Q7', 60.0, 20.0, 0); at('R60', 63.4, 19.2, 90); at('R61', 65.4, 19.2, 90); at('D20', 67.6, 19.6, 90)
at('R40', 76.5, 14.2, 0); at('R41', 82.5, 17.4, 90)
at('R47', 89.0, 2.8, 0); at('R48', 89.0, 6.4, 0); at('R52', 89.0, 10.0, 0)
at('Q3', 95.5, 10.0, 0); at('R51', 95.5, 13.0, 0)
at('U22', 89.0, 23.0, 0); at('C52', 85.5, 23.0, 90); at('C53', 92.5, 24.0, 90)
at('R44', 87.0, 20.6, 0); at('R45', 89.6, 20.6, 0); at('R46', 92.2, 20.6, 0)
at('D37', 65.6, 4.2, 90); at('D38', 68.6, 4.2, 90); at('D39', 71.8, 4.8, 90)
at('C54', 65.0, 10.0, 90); at('C55', 67.4, 10.0, 90); at('C56', 69.8, 11.6, 90); at('R57', 72.0, 11.6, 90)
for r in ('X1', 'X2', 'X3'): at(r, *MOUNT[r], 0)

# ---------------- build board ----------------
board = pcbnew.BOARD()
ds = board.GetDesignSettings()
board.SetCopperLayerCount(6)
ds.m_TrackMinWidth = mm(0.2); ds.m_ViasMinSize = mm(0.6); ds.m_MinClearance = mm(0.15)
ds.m_NetSettings.m_DefaultNetClass.SetClearance(mm(0.15))
def edge(x1, y1, x2, y2):
    s = pcbnew.PCB_SHAPE(board); s.SetShape(pcbnew.SHAPE_T_SEGMENT); s.SetLayer(pcbnew.Edge_Cuts)
    s.SetStart(P(x1, y1)); s.SetEnd(P(x2, y2)); s.SetWidth(mm(0.1)); board.Add(s)
def rect_edge(x1, y1, x2, y2):
    edge(x1, y1, x2, y1); edge(x2, y1, x2, y2); edge(x2, y2, x1, y2); edge(x1, y2, x1, y1)
for (xa, ya), (xb, yb) in zip(OUTLINE, OUTLINE[1:] + OUTLINE[:1]): edge(xa, ya, xb, yb)
rect_edge(*SLOT)                            # creepage slot under T1
_h = pcbnew.PCB_SHAPE(board); _h.SetShape(pcbnew.SHAPE_T_CIRCLE); _h.SetLayer(pcbnew.Edge_Cuts)
_h.SetCenter(P(SUPPORT_HOLE[0], SUPPORT_HOLE[1])); _h.SetEnd(P(SUPPORT_HOLE[0] + SUPPORT_HOLE[2] / 2, SUPPORT_HOLE[1])); _h.SetWidth(mm(0.1)); board.Add(_h)
def text(t, x, y, layer=pcbnew.F_SilkS, size=1.0):
    tx = pcbnew.PCB_TEXT(board); tx.SetText(t); tx.SetPosition(P(x, y)); tx.SetLayer(layer)
    tx.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size))); tx.SetTextThickness(mm(0.15)); board.Add(tx)

nets = {}
def net(n):
    if n not in nets:
        ni = pcbnew.NETINFO_ITEM(board, n); board.Add(ni); nets[n] = ni
    return nets[n]
LIB = '/usr/share/kicad/footprints'
fps = {}
for p in parts:
    lib, name = p['fp'].split(':')
    path = f'{PRJ}/igbt_drv.pretty' if lib == 'igbt_drv' else f'{LIB}/{lib}.pretty'
    fp = pcbnew.FootprintLoad(path, name)
    assert fp, p['fp']
    fp.SetReference(p['ref']); fp.SetValue(p['val'])
    fp.SetFPIDAsString(p['fp'])
    x, y, rot = POS[p['ref']]
    fp.SetPosition(P(x, y)); fp.SetOrientationDegrees(rot)
    board.Add(fp); fps[p['ref']] = fp
    if p['ref'] in BOT: fp.Flip(P(x, y), False)
    for pad in fp.Pads():
        n = p['nets'].get(pad.GetNumber())
        if n: pad.SetNet(net(n))
    fp.Reference().SetLayer(pcbnew.B_Fab if fp.IsFlipped() else pcbnew.F_Fab); fp.Reference().SetTextSize(pcbnew.VECTOR2I(mm(0.6), mm(0.6))); fp.Reference().SetTextThickness(mm(0.1))
    if p.get('dnp'): fp.SetAttributes(fp.GetAttributes() | pcbnew.FP_EXCLUDE_FROM_BOM)

# ---- legalize: nudge small parts off overlaps / out of HV creepage zones
from shapely.geometry import box as sbox, Point
FIXED = {'X1', 'X2', 'X3', 'T1', 'U3', 'U18', 'J1', 'J2'} | PRI_FIXED | {f'D{i}' for i in range(30, 36)} | {'D40'} | {f'R{i}' for i in range(30, 36)}
HVP = {'CAUX': 3300}
for i in range(1, 7): HVP[f'CH{i}'] = 3300 * (6 - i) / 6 + (100 if i == 6 else 0)
for i in range(1, 6): HVP[f'HVS{i}'] = 3300 * (6 - i) / 6
def creep(v):
    tab = [(50, .6), (100, .71), (160, 1.1), (250, 1.8), (400, 2.8), (630, 4.5), (1000, 7.1), (1600, 11), (2000, 14), (2500, 18), (3200, 22), (4000, 28)]
    for (v1, d1), (v2, d2) in zip(tab, tab[1:]):
        if v <= v2: return math.exp(math.log(d1) + (math.log(d2) - math.log(d1)) * (math.log(v) - math.log(v1)) / (math.log(v2) - math.log(v1)))
    return 28
THT = {'J1', 'J2', 'JP1', 'X1', 'X2', 'X3'}
def side(r): return 'T' if r in THT else ('B' if fps[r].IsFlipped() else 'F')
def cy(fp):
    c = fp.GetCourtyard(pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd)
    bb = c.BBox() if c.OutlineCount() else fp.GetBoundingBox(False, False)
    x1, y1 = pcbnew.ToMM(bb.GetX()) - OX, pcbnew.ToMM(bb.GetY()) - OY
    np_ = len(fp.Pads()); m_ = 0.0 if fp.GetReference() in FIXED else (0.2 if np_ >= 5 else 0.0)
    return sbox(x1 - m_, y1 - m_, x1 + pcbnew.ToMM(bb.GetWidth()) + m_, y1 + pcbnew.ToMM(bb.GetHeight()) + m_)
hvzones = []
for ref, fp in fps.items():
    for pad in fp.Pads():
        if pad.GetNetname() in HVP:
            bb = pad.GetBoundingBox(); x1, y1 = pcbnew.ToMM(bb.GetX()) - OX, pcbnew.ToMM(bb.GetY()) - OY
            hvzones.append((ref, sbox(x1, y1, x1 + pcbnew.ToMM(bb.GetWidth()), y1 + pcbnew.ToMM(bb.GetHeight())).buffer(creep(HVP[pad.GetNetname()]) - 0.5)))
def is_hv_part(ref): return any(n in HVP for n in (pd.GetNetname() for pd in fps[ref].Pads()))
from shapely.geometry import Polygon as _Poly
PRI_POLY_L = [(185.9, 3.0), (196.5, 3.0), (196.5, 43.3), (191.0, 43.3), (191.0, 40.3), (177.7, 40.3), (177.7, 33.0), (185.9, 33.0)]
BOARDPOLY = _Poly(OUTLINE).buffer(-0.3)
KEEPOUT = sbox(SLOT[0] - 1.0, SLOT[1] - 1.0, SLOT[2] + 1.0, SLOT[3] + 1.0).union(Point(SUPPORT_HOLE[0], SUPPORT_HOLE[1]).buffer(SUPPORT_HOLE[2] / 2 + 1.2))
PRIMARY_NETS = {'VIN', 'VIN_R', 'PGND', 'SW1', 'SW2', 'FCLK', 'SSILIM', 'PEN'}
_t1 = fps['T1']
def _padbox(pd):
    bb = pd.GetBoundingBox(); x1, y1 = pcbnew.ToMM(bb.GetX()) - OX, pcbnew.ToMM(bb.GetY()) - OY
    return sbox(x1, y1, x1 + pcbnew.ToMM(bb.GetWidth()), y1 + pcbnew.ToMM(bb.GetHeight()))
SEC_PINS = [_padbox(pd) for pd in _t1.Pads() if pd.GetNumber() in ('3', '4', '5')]
PRI_PINS = [_padbox(pd) for pd in _t1.Pads() if pd.GetNumber() in ('1', '2')]
PRI_KEEP = sbox(0, 0, 0, 0).union(SEC_PINS[0]).union(SEC_PINS[1]).union(SEC_PINS[2]).buffer(21.0)
def is_primary(ref): return all((pd.GetNetname() in PRIMARY_NETS) or not pd.GetNetname() for pd in fps[ref].Pads())
THT_HOLES = None
for _r in ('U3', 'U18', 'T1', 'JP1'):
    for _pd in fps[_r].Pads():
        if _pd.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
            _b = _padbox(_pd).buffer(0.5); THT_HOLES = _b if THT_HOLES is None else THT_HOLES.union(_b)
def bad_at(ref, g, others):
    if not BOARDPOLY.contains(g): return True
    if side(ref) == 'B' and g.intersects(THT_HOLES): return True
    if ref != 'T1':
        if is_primary(ref):
            if g.intersects(PRI_KEEP): return True
        elif g.distance(_Poly(PRI_POLY_L)) < 20.5 or any(g.distance(q) < 21.0 for q in PRI_PINS): return True
    if ref != 'T1' and g.intersects(KEEPOUT): return True
    sr = side(ref)
    for r, o in others:
        so = side(r)
        if sr != 'T' and so != 'T' and so != sr: continue
        if r != ref and g.intersection(o).area > 0.0: return True
    if not is_hv_part(ref):
        for r, z in hvzones:
            if g.intersects(z): return True
    return False
moved = []
for it in range(3):
    geo = {r: cy(f) for r, f in fps.items()}
    order_refs = sorted(fps, key=lambda r: -geo[r].area)
    seen = set(FIXED)
    for ref in order_refs:
        if ref in FIXED: continue
        others = [(r, geo[r]) for r in fps if r != ref and (it > 0 or r in seen)]
        seen.add(ref)
        if not bad_at(ref, geo[ref], others): continue  # noqa
        fp = fps[ref]; x0, y0, rot = POS[ref]; done = False
        for rad in [k * 0.5 for k in range(1, 36)]:
            for ang in range(0, 360, 15):
                for rr in (rot, (rot + 90) % 360):
                    nx, ny = x0 + rad * math.cos(math.radians(ang)), y0 + rad * math.sin(math.radians(ang))
                    fp.SetPosition(P(nx, ny)); fp.SetOrientationDegrees(rr); g = cy(fp)
                    if not bad_at(ref, g, others): POS[ref] = (nx, ny, rr); geo[ref] = g; done = True; break
                if done: break
            if done: break
        if not done and it == 2 and len(fp.Pads()) <= 8 and ref not in THT and ref not in ('U1',):
            fp.SetPosition(P(x0, y0)); fp.SetOrientationDegrees(rot); fp.Flip(P(x0, y0), False)
            for rad in [0] + [k * 0.5 for k in range(1, 30)]:
                for ang in range(0, 360, 20):
                    nx, ny = x0 + rad * math.cos(math.radians(ang)), y0 + rad * math.sin(math.radians(ang))
                    fp.SetPosition(P(nx, ny)); g = cy(fp)
                    if not bad_at(ref, g, others): POS[ref] = (nx, ny, rot); geo[ref] = g; done = True; break
                if done: break
            if done: moved.append(ref + '(B)')
        if not done:
            if fp.IsFlipped() and ref not in BOT: fp.Flip(fp.GetPosition(), False)
            fp.SetPosition(P(x0, y0)); fp.SetOrientationDegrees(rot)
        elif not moved or moved[-1] != ref + '(B)': moved.append(ref)
unplaced = []
geo = {r: cy(f) for r, f in fps.items()}
for ref in fps:
    if ref not in FIXED and bad_at(ref, geo[ref], list(geo.items())): unplaced.append(ref)
print('legalize moved', len(moved), 'bottom', [m for m in moved if m.endswith('(B)')], 'still bad', unplaced)

text('FZ1200R33KF2 GATE DRIVER rev D - PROTOTYPE', 30.0, 42.6, size=0.8)
text('PRIMARY', 192.0, 35.5, size=0.8); text('ISOLATION', 172.0, 31.5, size=0.7)
text('DANGER 3300 V', 22.0, 22.0, size=1.2)

def save(path=f'{PRJ}/fz1200_gate_driver.kicad_pcb'):
    pcbnew.SaveBoard(path, board)

if __name__ == '__main__' and 'place' in sys.argv:
    save(); print('placed only'); sys.exit(0)

# ---------------- routing ----------------
from scipy.ndimage import binary_dilation, distance_transform_edt
import random
G = 0.2
NX, NY = int(BOARD_W / G) + 1, int(BOARD_H / G) + 1
# stack-up: F | In1 VEE plane | In2 signal | In3 signal | In4 VE plane | B
LAYERS = [pcbnew.F_Cu, pcbnew.B_Cu, pcbnew.In2_Cu, pcbnew.In3_Cu, pcbnew.In1_Cu, pcbnew.In4_Cu]
NL = 6
CLR = 0.25
HW = 0.125                                            # half of the default track width
WIDTH = collections.defaultdict(lambda: 0.25)
for n in ['GATE', 'OUT_ON', 'ROFF_D']: WIDTH[n] = 0.8
for n in ['RSSD_D', 'CLK', 'CL_A', 'VP', 'VIN', 'SEC_A', 'SEC_B']: WIDTH[n] = 0.6
WIDTH['SW1'] = WIDTH['SW2'] = 0.5
for n in ['CAUX', 'CH6'] + [f'CH{i}' for i in range(1, 6)] + [f'HVS{i}' for i in range(1, 6)]: WIDTH[n] = 0.5
for n in ['GQ', 'GQL', 'GP', 'GPL', 'GPH', 'SG', 'LED_A', 'LED_K']: WIDTH[n] = 0.4
PLANE = {'VEE': 1, 'VE': 2, 'PGND': 1, 'V5': 3}
VPOT = {'CAUX': 3300, 'CH6': 250}
for i in range(1, 6): VPOT[f'CH{i}'] = 3300 * (6 - i) / 6; VPOT[f'HVS{i}'] = 3300 * (6 - i) / 6
HVNETS = set(VPOT) | {'CL_A', 'SNS', 'DG', 'DGR'}
def creep_mm(v): return creep(max(v, 51))
netid = {n: i + 1 for i, n in enumerate(sorted(set(v for p in parts for v in p['nets'].values() if v)))}
def loc(v): return (pcbnew.ToMM(v.x) - OX, pcbnew.ToMM(v.y) - OY)
def cells_rect(x1, y1, x2, y2):
    i1, i2 = max(0, int(math.floor(x1 / G))), min(NX - 1, int(math.ceil(x2 / G)))
    j1, j2 = max(0, int(math.floor(y1 / G))), min(NY - 1, int(math.ceil(y2 / G)))
    return i1, i2, j1, j2
_SEC = [(47, 0.8), (161.8, 0.8), (161.8, 19.0), (128.2, 19.0), (128.2, 43.4), (100.7, 43.4), (100.7, 40.3), (68.7, 40.3), (68.7, 43.4), (52.0, 43.4), (52.0, 34.5), (60, 34.5), (60, 26.5), (47, 26.5)]
PRI_POLY = [(185.9, 3.0), (196.5, 3.0), (196.5, 43.3), (191.0, 43.3), (191.0, 40.3), (177.7, 40.3), (177.7, 33.0), (185.9, 33.0)]
V5_POLY = [(70, 13), (84, 13), (84, 0.8), (161.5, 0.8), (161.5, 19.0), (128.2, 19.0), (128.2, 26.0), (100.7, 26.0), (100.7, 40.3), (70, 40.3)]
ZONE_POLY = {'VEE': _SEC, 'VE': _SEC, 'PGND': PRI_POLY, 'V5': V5_POLY}
from shapely.geometry import Polygon
import shapely
GX, GY = np.meshgrid(np.arange(NX) * G, np.arange(NY) * G, indexing='ij')
ZSHAPE = {k: Polygon(v).buffer(-1.0) for k, v in ZONE_POLY.items()}
PADS = collections.defaultdict(list)
PADINFO = []
for ref, fp in fps.items():
    for pad in fp.Pads():
        bb = pad.GetBoundingBox(); x1, y1 = loc(bb.GetOrigin()); x2, y2 = loc(bb.GetEnd())
        cx, cy_ = loc(pad.GetPosition()); n = pad.GetNetname()
        att = pad.GetAttribute()
        if att == pcbnew.PAD_ATTRIB_PTH: ls = list(range(6))
        elif att == pcbnew.PAD_ATTRIB_NPTH: ls = list(range(6))
        else: ls = [0] if pad.IsOnLayer(pcbnew.F_Cu) else [1]
        d = dict(x=cx, y=cy_, layers=ls, bb=(x1, y1, x2, y2), ref=ref, net=n, npth=att == pcbnew.PAD_ATTRIB_NPTH, minsz=min(x2 - x1, y2 - y1), fpc=loc(fp.GetPosition()))
        PADINFO.append(d)
        if n: PADS[n].append(d)
RT_ = 0.25 + 0.125                              # track centreline keep-off from other copper
RV_ = 0.25 + 0.35                               # via centre keep-off from other copper
def paint(arr, L, kind, geo, r, val):
    if kind == 'rect':
        x1, y1, x2, y2 = geo
        i1, i2, j1, j2 = cells_rect(x1 - r, y1 - r, x2 + r, y2 + r)
        xs = np.arange(i1, i2 + 1)[:, None] * G; ys = np.arange(j1, j2 + 1)[None, :] * G
        dx = np.maximum(0, np.maximum(x1 - xs, xs - x2)); dy = np.maximum(0, np.maximum(y1 - ys, ys - y2))
        mk = dx * dx + dy * dy <= r * r + 1e-9
    else:  # segment with half width hw: geo = (ax, ay, bx, by, hw)
        ax, ay, bx, by, hw = geo; rr = r + hw
        i1, i2, j1, j2 = cells_rect(min(ax, bx) - rr, min(ay, by) - rr, max(ax, bx) + rr, max(ay, by) + rr)
        xs = np.arange(i1, i2 + 1)[:, None] * G; ys = np.arange(j1, j2 + 1)[None, :] * G
        vx, vy = bx - ax, by - ay; ll = vx * vx + vy * vy
        t = np.clip(((xs - ax) * vx + (ys - ay) * vy) / ll, 0, 1) if ll > 0 else np.zeros_like(xs + ys)
        px, py = ax + t * vx - xs, ay + t * vy - ys
        mk = px * px + py * py <= rr * rr + 1e-9
    sub = arr[L, i1:i2 + 1, j1:j2 + 1]
    if val < 0: sub[mk] = -1; return
    sub[mk & (sub == 0)] = val; sub[mk & (sub > 0) & (sub != val)] = -1
STUBS = []
for d in PADINFO:
    if not d['net'] or d['npth'] or len(d['layers']) != 1 or d['minsz'] > 0.55: continue
    if d['ref'] == 'U1' and d['net'] == 'PGND':
        ep = [q for q in PADINFO if q['ref'] == 'U1' and q['minsz'] >= 1.3][0]
        d['eplink'] = True
        STUBS.append(('PGND', (d['x'], d['y']), (ep['x'], d['y']), 0)); continue
    near = [q for q in PADINFO if q is not d and q['ref'] == d['ref'] and abs(q['x'] - d['x']) + abs(q['y'] - d['y']) < 0.9]
    if not near: continue
    x1, y1, x2, y2 = d['bb']
    if (x2 - x1) >= (y2 - y1): ax, hl = (1 if d['x'] >= d['fpc'][0] else -1, 0), (x2 - x1) / 2
    else: ax, hl = (0, 1 if d['y'] >= d['fpc'][1] else -1), (y2 - y1) / 2
    ex, ey = d['x'] + ax[0] * (hl + 0.45), d['y'] + ax[1] * (hl + 0.45)
    gx, gy = round(ex / G) * G, round(ey / G) * G
    d['esc'] = (gx, gy)
    STUBS.append((d['net'], (d['x'], d['y']), (ex, ey), d['layers'][0])); STUBS.append((d['net'], (ex, ey), (gx, gy), d['layers'][0]))
def base_grid():
    ot = np.zeros((NL, NX, NY), dtype=np.int32); ov = np.zeros((NL, NX, NY), dtype=np.int32)
    hv = np.zeros((NX, NY), dtype=bool); smd = np.zeros((NX, NY), dtype=bool)
    for arr, r in ((ot, 0.125 + 0.1), (ov, 0.35 + 0.1)):
        for L in range(NL):
            paint(arr, L, 'rect', SLOT, r + 0.2, -1)
            paint(arr, L, 'seg', (SUPPORT_HOLE[0], SUPPORT_HOLE[1], SUPPORT_HOLE[0], SUPPORT_HOLE[1], SUPPORT_HOLE[2] / 2), r + 0.4, -1)
            inside = shapely.contains_xy(Polygon(OUTLINE).buffer(-(r + 0.42)), GX, GY)
            arr[L][~inside] = -1
    global INNER_NO; INNER_NO = ~shapely.contains_xy(Polygon(_SEC).buffer(-1.0), GX, GY)
    for d in PADINFO:
        v = -1 if (d['npth'] or not d['net']) else netid[d['net']]
        for L in d['layers']:
            paint(ot, L, 'rect', d['bb'], RT_, v); paint(ov, L, 'rect', d['bb'], RV_, v)
        if (len(d['layers']) == 1 and d['minsz'] < 1.3) or len(d['layers']) > 1:
            i1, i2, j1, j2 = cells_rect(*d['bb']); smd[i1:i2 + 1, j1:j2 + 1] = True
    own_cells = []
    for d in PADINFO:
        if d['net'] and not d['npth']:
            x1, y1, x2, y2 = d['bb']; i1, i2, j1, j2 = cells_rect(x1 + 0.02, y1 + 0.02, x2 - 0.02, y2 - 0.02)
            for L in d['layers']: own_cells.append((L, i1, i2, j1, j2, netid[d['net']]))
        if d['net'] in VPOT:
            v_ = VPOT[d['net']]
            rr = (creep_mm(v_) if v_ > 1100 else creep_route(pot_dv(d['net'], 'VEE'))) + 0.42; x1, y1, x2, y2 = d['bb']
            i1, i2, j1, j2 = cells_rect(x1 - rr, y1 - rr, x2 + rr, y2 + rr)
            xs = np.arange(i1, i2 + 1)[:, None] * G; ys = np.arange(j1, j2 + 1)[None, :] * G
            dx = np.maximum(0, np.maximum(x1 - xs, xs - x2)); dy = np.maximum(0, np.maximum(y1 - ys, ys - y2))
            hv[i1:i2 + 1, j1:j2 + 1] |= (dx * dx + dy * dy) <= rr * rr
    for n, a, b, L in STUBS:
        paint(ot, L, 'seg', (a[0], a[1], b[0], b[1], 0.125), RT_, netid[n]); paint(ov, L, 'seg', (a[0], a[1], b[0], b[1], 0.125), RV_, netid[n])
    return ot, ov, hv, smd, own_cells
BASE_OT, BASE_OV, HV, SMD, OWN_CELLS = base_grid()
V5_AREA = shapely.contains_xy(Polygon(V5_POLY).buffer(0.5), GX, GY)
_secpin = np.ones((NX, NY), dtype=bool); _pripin = np.ones((NX, NY), dtype=bool)
for d in PADINFO:
    if d['ref'] == 'T1':
        i1, i2, j1, j2 = cells_rect(*d['bb'])
        (_secpin if d['net'] not in PRIMARY_NETS else _pripin)[i1:i2 + 1, j1:j2 + 1] = False
_pripin &= ~shapely.contains_xy(Polygon(PRI_POLY_L), GX, GY)
PRI_MASK = distance_transform_edt(_secpin) * G < 20.6        # primary copper must stay out
SEC_MASK = distance_transform_edt(_pripin) * G < 20.6        # secondary copper must stay out
_hvm = {}
def hvmask(n):
    if n in _hvm: return _hvm[n]
    m = np.zeros((NX, NY), dtype=bool)
    for d in PADINFO:
        if not d['net'] or d['net'] == n or (d['net'] not in HVSET and n not in HVSET): continue
        rr = creep_route(pot_dv(n, d['net'])) + 0.275 + max(0, WIDTH[n] / 2 - 0.125)
        if rr < 0.45: continue
        x1, y1, x2, y2 = d['bb']; i1, i2, j1, j2 = cells_rect(x1 - rr, y1 - rr, x2 + rr, y2 + rr)
        xs = np.arange(i1, i2 + 1)[:, None] * G; ys = np.arange(j1, j2 + 1)[None, :] * G
        dx = np.maximum(0, np.maximum(x1 - xs, xs - x2)); dy = np.maximum(0, np.maximum(y1 - ys, ys - y2))
        m[i1:i2 + 1, j1:j2 + 1] |= (dx * dx + dy * dy) <= rr * rr
    _hvm[n] = m; return m
VIA_D, VIA_DR = 0.7, 0.35
DIRS = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0), (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414)]
LPEN = [0.0, 0.1, 0.2, 0.2, 1.5, 1.5]
_dk = {}
def disk(r):
    if r not in _dk:
        k = int(math.ceil(r / G)); y, x = np.ogrid[-k:k + 1, -k:k + 1]; _dk[r] = (x * x + y * y) * G * G <= r * r + 1e-9
    return _dk[r]
PRIMARY_FIRST = {'SW1', 'SW2', 'VIN', 'FCLK', 'SSILIM', 'PEN'}
def run_pass(order, verbose=False):
    ot = BASE_OT.copy(); ov = BASE_OV.copy(); trk = [(n, a, b, L, 0.25) for n, a, b, L in STUBS if a != b]; via = []; viaset = set(); failed = []; stf = []
    hvcu = {}; hvdist = {}
    def rec_hv(n, geo):
        if n not in HVSET: return
        g = hvcu.setdefault(n, np.zeros((NX, NY), dtype=np.int8)); tmp = np.zeros((1, NX, NY), dtype=np.int32)
        paint(tmp, 0, 'seg', geo, 0.0, 1); g[tmp[0] > 0] = 1; hvdist.pop(n, None)
    def copper_mask(n, extra):
        m = np.zeros((NX, NY), dtype=bool)
        for h, g in hvcu.items():
            if h == n: continue
            r = creep_route(pot_dv(n, h))
            if r < 0.3: continue
            if h not in hvdist: hvdist[h] = distance_transform_edt(g == 0) * G
            m |= hvdist[h] < r + extra + 0.15
        return m
    def put_track(n, a, b, L, w):
        trk.append((n, a, b, L, w)); nid = netid[n]
        if L in (0, 1): rec_hv(n, (a[0], a[1], b[0], b[1], w / 2))
        paint(ot, L, 'seg', (a[0], a[1], b[0], b[1], w / 2), RT_, nid)
        paint(ov, L, 'seg', (a[0], a[1], b[0], b[1], w / 2), RV_, nid)
    def put_via(n, x, y, la=None, lb=None):
        key = (round(x, 3), round(y, 3))
        if key in viaset: return
        for n2, x2, y2 in via:
            if n2 == n and (x2 - x) ** 2 + (y2 - y) ** 2 < 0.75 ** 2:
                # reuse the existing via: bridge to it on both layers instead of drilling a second hole
                for L_ in {la, lb} - {None}: put_track(n, (x, y), (x2, y2), L_, 0.25)
                return
        viaset.add(key); via.append((n, x, y)); nid = netid[n]; rec_hv(n, (x, y, x, y, VIA_D / 2))
        for L in range(NL):
            paint(ot, L, 'seg', (x, y, x, y, VIA_D / 2), RT_, nid); paint(ov, L, 'seg', (x, y, x, y, VIA_D / 2), RV_, nid)
    def track_block(n, w, lv):
        nid = netid[n]; b = (ot != 0) & (ot != nid)
        extra = w / 2 - 0.125
        if extra > 0.01: b = np.stack([binary_dilation(b[L], structure=disk(round(extra + 0.16, 2))) for L in range(NL)])
        if lv: b |= HV[None]
        else: b |= hvmask(n)[None]
        b[:2] |= copper_mask(n, w / 2 + 0.1)[None]
        b |= (PRI_MASK if n in PRIMARY_NETS else SEC_MASK)[None]
        b[2:] |= INNER_NO[None]
        b[3] |= V5_AREA
        return b
    def via_block(n, lv):
        nid = netid[n]; b = ((ov != 0) & (ov != nid)).any(axis=0) | SMD
        if lv: b |= HV
        else: b |= hvmask(n)
        b |= copper_mask(n, VIA_D / 2 + 0.1)
        b |= (PRI_MASK if n in PRIMARY_NETS else SEC_MASK)
        return b
    def stitch():
        for n, pl_layer in PLANE.items():
            nid = netid[n]
            for pd in PADS[n]:
                if len(pd['layers']) > 1 or pd.get('eplink'): continue
                vb = via_block(n, True)
                L0 = pd['layers'][0]; cx, cy_ = pd.get('esc', (pd['x'], pd['y'])); best = None
                if pd['minsz'] >= 1.3 and ZSHAPE[n].contains(Point(cx, cy_)) and not vb[int(round(cx / G)), int(round(cy_ / G))]: best = (cx, cy_)
                tb = None
                for dd in ([] if best else [0.8 + 0.2 * k for k in range(22)]):
                    for ang in range(0, 360, 10):
                        vx, vy = cx + dd * math.cos(math.radians(ang)), cy_ + dd * math.sin(math.radians(ang))
                        if not ZSHAPE[n].contains(Point(vx, vy)): continue
                        i, j = int(round(vx / G)), int(round(vy / G))
                        if not (0 <= i < NX and 0 <= j < NY) or vb[i, j]: continue
                        if tb is None: tb = track_block(n, 0.4, True)
                        steps = int(dd / (G / 2)) + 1; ok = True
                        for k in range(steps + 1):
                            ii = int(round((cx + (vx - cx) * k / steps) / G)); jj = int(round((cy_ + (vy - cy_) * k / steps) / G))
                            if tb[L0, ii, jj]: ok = False; break
                        if ok: best = (vx, vy); break
                    if best: break
                if best:
                    if best != (cx, cy_): put_track(n, (cx, cy_), best, L0, 0.4)
                    put_via(n, *best)
                else: stf.append((n, pd['ref'], pd))
    def route_nets(nets, override=None):
        for n in nets:
            pl = override if override else PADS[n]; nid = netid[n]; w = WIDTH[n]; lv = n not in HVNETS
            if any('esc' in q for q in PADS[n]): w = min(w, 0.3)
            xs = [p['x'] for p in pl]; ys = [p['y'] for p in pl]
            bx1, bx2 = int(max(0, (min(xs) - 9) / G)), int(min(NX - 1, (max(xs) + 9) / G))
            by1, by2 = int(max(0, (min(ys) - 9) / G)), int(min(NY - 1, (max(ys) + 9) / G))
            def cellsof(pd):
                x1, y1, x2, y2 = pd['bb']; out = []
                if 'esc' in pd: out.append((pd['layers'][0], int(round(pd['esc'][0] / G)), int(round(pd['esc'][1] / G))))
                for L in pd['layers']:
                    if L >= 2 and not lv: continue
                    ins = 0.3 * min(x2 - x1, y2 - y1)
                    for i in range(int(math.ceil((x1 + ins) / G)), int(math.floor((x2 - ins) / G)) + 1):
                        for j in range(int(math.ceil((y1 + ins) / G)), int(math.floor((y2 - ins) / G)) + 1):
                            out.append((L, i, j))
                    if not out: out.append((L, int(round(pd['x'] / G)), int(round(pd['y'] / G))))
                return out
            rest = sorted(pl, key=lambda p: (p['x'] - pl[0]['x']) ** 2 + (p['y'] - pl[0]['y']) ** 2)
            tree = cellsof(rest[0])
            for pd in rest[1:]:
                bm = track_block(n, w, lv); vb = via_block(n, lv) if w <= 1.0 else None
                tg = set(cellsof(pd))
                # inside own pads the track may overlap the pad itself
                for c in list(tg) + tree: bm[c[0], c[1], c[2]] = False
                tl = list(tg); cx_ = np.mean([t[1] for t in tl]); cy2 = np.mean([t[2] for t in tl])
                def h(i, j): return max(abs(cx_ - i), abs(cy2 - j)) * 0.95
                openl = []; g = {}; came = {}; found = None; nexp = 0
                for s0 in tree:
                    if s0[0] >= 2 and not lv: continue
                    g[s0] = 0.0; heapq.heappush(openl, (h(s0[1], s0[2]), 0.0, s0))
                while openl:
                    f, gc, s0 = heapq.heappop(openl)
                    if gc > g.get(s0, 1e9): continue
                    if s0 in tg: found = s0; break
                    nexp += 1
                    if nexp > 300000: break
                    L, i, j = s0
                    for di, dj, c in DIRS:
                        ii, jj = i + di, j + dj
                        if ii < bx1 or ii > bx2 or jj < by1 or jj > by2 or bm[L, ii, jj]: continue
                        if di and dj and (bm[L, ii, j] or bm[L, i, jj]): continue
                        t = (L, ii, jj); ng = gc + c + LPEN[L]
                        if ng < g.get(t, 1e9): g[t] = ng; came[t] = s0; heapq.heappush(openl, (ng + h(ii, jj), ng, t))
                    if vb is not None and not vb[i, j]:
                        for L2 in range(NL):
                            if L2 == L or (L2 >= 2 and not lv) or bm[L2, i, j]: continue
                            t = (L2, i, j); ng = gc + 7.0
                            if ng < g.get(t, 1e9): g[t] = ng; came[t] = s0; heapq.heappush(openl, (ng + h(i, j), ng, t))
                if not found:
                    failed.append((n, pd['ref']))
                    if os.environ.get('DBG') == n:
                        src = tree[0]; tt = list(tg)[0]; print('DBG src', src, 'tgt', tt, 'exp', nexp)
                        for L in range(NL):
                            print('layer', L)
                            for jj in range(min(src[2], tt[2]) - 8, max(src[2], tt[2]) + 9):
                                print(''.join('S' if (L, ii, jj) in tree else ('T' if (L, ii, jj) in tg else ('#' if bm[L, ii, jj] else ('v' if vb is not None and vb[ii, jj] else '.'))) for ii in range(min(src[1], tt[1]) - 15, max(src[1], tt[1]) + 16)))
                    continue
                path = [found]
                while path[-1] in came: path.append(came[path[-1]])
                path = path[::-1]
                seg = path[0]; prev = path[0]; pdir = None
                for c in path[1:]:
                    if c[0] != prev[0]:
                        if prev[1:] != seg[1:]: put_track(n, (seg[1] * G, seg[2] * G), (prev[1] * G, prev[2] * G), prev[0], w)
                        put_via(n, prev[1] * G, prev[2] * G, prev[0], c[0]); seg = c; prev = c; pdir = None; continue
                    dd = (c[1] - prev[1], c[2] - prev[2])
                    if pdir is not None and dd != pdir:
                        put_track(n, (seg[1] * G, seg[2] * G), (prev[1] * G, prev[2] * G), prev[0], w); seg = prev
                    pdir = dd; prev = c
                if prev[1:] != seg[1:]: put_track(n, (seg[1] * G, seg[2] * G), (prev[1] * G, prev[2] * G), prev[0], w)
                tree += path
    early = [n for n in order if n in PRIMARY_FIRST]
    hvn = [n for n in order if n in HVSET and n not in PRIMARY_FIRST]
    route_nets(early); route_nets(hvn); stitch(); route_nets([n for n in order if n not in PRIMARY_FIRST and n not in HVSET])
    for n in PLANE:
        for pd in PADS[n]:
            if len(pd['layers']) > 1 and not ZSHAPE[n].buffer(0.8).contains(Point(pd['x'], pd['y'])):
                stf.append((n, pd['ref'], pd))
    left = []
    for n, ref, pd in stf:
        others = [q for q in PADS[n] if q is not pd]
        q = min(others, key=lambda q: (q['x'] - pd['x']) ** 2 + (q['y'] - pd['y']) ** 2)
        nf = len(failed); route_nets([n], [q, pd])
        if len(failed) > nf: failed.pop(); left.append((n, ref))
    stf[:] = left
    # ---- local rip-up and repair
    stubset = set((n, a, b, L) for n, a, b, L in STUBS)
    def is_fixed(t): return t[0] in PLANE or (t[0], t[1], t[2], t[3]) in stubset
    def rebuild():
        ot[...] = BASE_OT; ov[...] = BASE_OV; hvcu.clear(); hvdist.clear()
        for n_, a, b, L, w in trk:
            if (n_, a, b, L) in stubset: continue
            nid = netid[n_]; paint(ot, L, 'seg', (a[0], a[1], b[0], b[1], w / 2), RT_, nid); paint(ov, L, 'seg', (a[0], a[1], b[0], b[1], w / 2), RV_, nid)
            if L in (0, 1): rec_hv(n_, (a[0], a[1], b[0], b[1], w / 2))
        for n_, x, y in via:
            rec_hv(n_, (x, y, x, y, VIA_D / 2))
            for L in range(NL):
                paint(ot, L, 'seg', (x, y, x, y, VIA_D / 2), RT_, netid[n_]); paint(ov, L, 'seg', (x, y, x, y, VIA_D / 2), RV_, netid[n_])
    tries = 0; skip = collections.Counter()
    while failed and tries < int(os.environ.get('REPAIR', '40')):
        tries += 1
        cand = [f for f in failed if skip[f] < 6]
        if not cand: break
        fn_, fref = cand[0]
        pl = PADS[fn_]; xs = [p['x'] for p in pl]; ys = [p['y'] for p in pl]
        rx1, rx2, ry1, ry2 = min(xs) - 2.5, max(xs) + 2.5, min(ys) - 2.5, max(ys) + 2.5
        vic = collections.Counter()
        for t in trk:
            if is_fixed(t) or t[0] == fn_ or t[0] in HVNETS: continue
            for (x, y) in (t[1], t[2], ((t[1][0] + t[2][0]) / 2, (t[1][1] + t[2][1]) / 2)):
                if rx1 <= x <= rx2 and ry1 <= y <= ry2: vic[t[0]] += 1
        victims = [v for v, _ in vic.most_common(5 + skip[(fn_, fref)] * 3)]
        snap = (list(trk), list(via), list(failed), set(viaset))
        rm = set(victims) | {fn_}
        trk[:] = [t for t in trk if is_fixed(t) or t[0] not in rm]
        via[:] = [v for v in via if v[0] in PLANE or v[0] not in rm]
        viaset.clear(); viaset.update((round(v[1], 3), round(v[2], 3)) for v in via)
        failed[:] = [f for f in failed if f[0] not in rm]
        rebuild()
        route_nets([fn_] + victims)
        if len(failed) < len(snap[2]):
            print(f'  repair {fn_}/{fref}: {len(snap[2])} -> {len(failed)}', flush=True)
        else:
            trk[:], via[:], failed[:] = snap[0], snap[1], snap[2]; viaset.clear(); viaset.update(snap[3]); rebuild()
            skip[(fn_, fref)] += 1
            failed.sort(key=lambda f: skip[f])
    return trk, via, failed, stf
nets_to_route = [n for n, pl in PADS.items() if n not in PLANE and len(pl) >= 2]
def keyf(n):
    pl = PADS[n]; xs = [p['x'] for p in pl]; ys = [p['y'] for p in pl]
    return (0 if WIDTH[n] >= 0.6 else 1, (max(xs) - min(xs)) + (max(ys) - min(ys)))
order = sorted(nets_to_route, key=keyf)
best = None
for ps in range(int(os.environ.get('PASSES', '4'))):
    trk, via, failed, stf = run_pass(order)
    print(f'pass {ps}: tracks {len(trk)} vias {len(via)} failed {len(failed)} stitch_fail {len(stf)}', failed[:12], flush=True)
    if best is None or len(failed) + len(stf) < len(best[2]) + len(best[3]): best = (trk, via, failed, stf)
    if not failed and not stf: break
    fn = list(dict.fromkeys(f[0] for f in failed))
    rnd = random.Random(ps)
    rest_ = [n for n in order if n not in fn]
    if ps % 2: rnd.shuffle(rest_); rest_.sort(key=lambda n: 0 if WIDTH[n] >= 0.6 else 1)
    order = fn + rest_
trk, via, failed, stf = best
_pts = collections.defaultdict(set)
_stubkeys = set((n, a, b, L) for n, a, b, L in STUBS)
for n, a, b, L, w in trk:
    if (n, a, b, L) in _stubkeys: continue
    _pts[n].add((round(a[0], 2), round(a[1], 2))); _pts[n].add((round(b[0], 2), round(b[1], 2)))
for n, x, y in via: _pts[n].add((round(x, 2), round(y, 2)))
_drop = set()
for d in PADINFO:
    if 'esc' in d:
        e = (round(d['esc'][0], 2), round(d['esc'][1], 2))
        if e not in _pts[d['net']]:
            for st in STUBS:
                if st[0] == d['net'] and (st[1] == (d['x'], d['y']) or (round(st[2][0], 2), round(st[2][1], 2)) == e): _drop.add(st)
trk = [t for t in trk if (t[0], t[1], t[2], t[3]) not in _drop]
print('dropped unused escape stubs', len(_drop))
for n, a, b, L, w in trk:
    t = pcbnew.PCB_TRACK(board); t.SetStart(P(*a)); t.SetEnd(P(*b)); t.SetLayer(LAYERS[L]); t.SetWidth(mm(w)); t.SetNet(net(n)); board.Add(t)
for n, x, y in via:
    v = pcbnew.PCB_VIA(board); v.SetPosition(P(x, y)); v.SetWidth(mm(VIA_D)); v.SetDrill(mm(VIA_DR)); v.SetNet(net(n)); board.Add(v)
# ---------------- zones ----------------
def zone(n, layer, pts, prio=0, clearance=0.3, full=False):
    z = pcbnew.ZONE(board); z.SetLayer(layer); z.SetNet(net(n)); z.SetAssignedPriority(prio)
    z.SetLocalClearance(mm(clearance)); z.SetMinThickness(mm(0.25))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL); z.SetThermalReliefGap(mm(0.3)); z.SetThermalReliefSpokeWidth(mm(0.5))
    z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    if full: z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
    ol = z.Outline(); ol.NewOutline()
    for x, y in pts: ol.Append(mm(OX + x), mm(OY + y))
    board.Add(z)
zone('VEE', pcbnew.In1_Cu, ZONE_POLY['VEE']); zone('VE', pcbnew.In4_Cu, ZONE_POLY['VE']); zone('V5', pcbnew.In3_Cu, V5_POLY); zone('PGND', pcbnew.In1_Cu, ZONE_POLY['PGND'])
zone('PGND', pcbnew.B_Cu, PRI_POLY, full=True)

save()
json.dump(dict(failed=failed, stitch_fail=stf, tracks=len(trk), vias=len(via)), open(f'{PRJ}/generator/route_report.json', 'w'), indent=1)
print('FINAL tracks', len(trk), 'vias', len(via), 'failed', len(failed), failed, 'stitch_fail', stf, flush=True)
