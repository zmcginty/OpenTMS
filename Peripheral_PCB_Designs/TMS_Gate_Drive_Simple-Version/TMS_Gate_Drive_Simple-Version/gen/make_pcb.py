"""Build the PCB with KiCad 7 pcbnew API. Run with /usr/bin/python3."""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
import pcbnew
from pcbnew import VECTOR2I, FromMM as MM
import design

PROJ = 'FZ1200R33_GateDriver'
OUT = '/home/claude/out/' + PROJ
FPLIB = '/usr/share/kicad/footprints'
OX, OY = 40.0, 40.0          # board origin on the sheet
BW, BH = 197.10, 43.94        # board size (mm), 1SP0635 envelope

# ---------------- MODULE TERMINAL POSITIONS (board coords, mm) -----------------
# !!! Placeholders: set these to the FZ1200R33KF2 auxiliary-terminal positions from the
# !!! Infineon package drawing before ordering boards (G, E-aux, C-aux, M4 screws).
# From CONCEPT 1SP0635 manual Fig. 5 (datum = left edge / baseline 4.0 mm above bottom edge):
#   C: x=20.04, 27.83 above baseline;  E: x=58.19, +5.20;  G: x=79.19, same height as E;  support hole x=193.59 on baseline.
BH_ = 43.94
TERM = {'X3': (20.04, BH_ - (4.0 + 27.83)), 'X2': (58.19, BH_ - (4.0 + 27.83 + 5.20)), 'X1': (79.19, BH_ - (4.0 + 27.83 + 5.20))}
SUPPORT = (193.59, BH_ - 4.0)

def P(x, y):
    return VECTOR2I(MM(OX + x), MM(OY + y))

board = pcbnew.BOARD()
board.SetCopperLayerCount(4)
ds = board.GetDesignSettings()
ds.SetBoardThickness(MM(1.6))

# ---------------- nets
netmap = {}
def net(name):
    if name is None:
        return None
    if name not in netmap:
        ni = pcbnew.NETINFO_ITEM(board, '/' + name)
        board.Add(ni)
        netmap[name] = ni
    return netmap[name]

# ---------------- custom footprints
def mkpad(fp, num, x, y, shape, size, drill=None, layers='tht', net_=None):
    p = pcbnew.PAD(fp)
    p.SetNumber(num)
    p.SetShape(shape)
    p.SetSize(VECTOR2I(MM(size[0]), MM(size[1])))
    if drill:
        p.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
        p.SetDrillSize(VECTOR2I(MM(drill), MM(drill)))
        p.SetLayerSet(pcbnew.PAD.PTHMask())
    else:
        p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        p.SetLayerSet(pcbnew.PAD.SMDMask())
    p.SetPos0(VECTOR2I(MM(x), MM(y))); p.SetPosition(VECTOR2I(MM(x), MM(y)))
    fp.Add(p)
    return p

def rect(fp, layer, x0, y0, x1, y1, w=0.12):
    for (a, b) in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        s = pcbnew.FP_SHAPE(fp)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetLayer(layer)
        s.SetWidth(MM(w))
        s.SetStart0(VECTOR2I(MM(a[0]), MM(a[1])))
        s.SetEnd0(VECTOR2I(MM(b[0]), MM(b[1])))
        fp.Add(s)
        s.SetDrawCoord()

def fptext(fp, txt, x, y, layer=pcbnew.F_SilkS, size=1.0):
    t = pcbnew.FP_TEXT(fp)
    t.SetText(txt)
    t.SetLayer(layer)
    t.SetTextSize(VECTOR2I(MM(size), MM(size)))
    t.SetTextThickness(MM(size * 0.15))
    t.SetPos0(VECTOR2I(MM(x), MM(y)))
    fp.Add(t)
    t.SetDrawCoord()

def custom_fp(name):
    fp = pcbnew.FOOTPRINT(board)
    fp.SetFPID(pcbnew.LIB_ID('GD', name))
    if name == 'Broadcom_VersatileLink_Horizontal':
        # Pins 4..1 on 2.54 mm pitch (top view, left to right), mounting pins 5/8 7.62 mm toward PCB edge.
        # VERIFY against Broadcom "Versatile Link printed board layout dimensions".
        for i, num in enumerate(('4', '3', '2', '1')):
            mkpad(fp, num, i * 2.54, 0, pcbnew.PAD_SHAPE_CIRCLE if num != '1' else pcbnew.PAD_SHAPE_RECT, (1.7, 1.7), 1.0)
        mkpad(fp, '5', 0, 7.62, pcbnew.PAD_SHAPE_CIRCLE, (2.6, 2.6), 1.8)
        mkpad(fp, '8', 7.62, 7.62, pcbnew.PAD_SHAPE_CIRCLE, (2.6, 2.6), 1.8)
        rect(fp, pcbnew.F_CrtYd, -2.6, -4.0, 10.2, 9.6, 0.05)
        rect(fp, pcbnew.F_SilkS, -2.4, -3.8, 10.0, 9.4)
        fptext(fp, 'FIBER >', 3.81, 11.0, size=1.0)
    elif name == 'Murata_MGJ6_SIP8':
        # SIP-8 on a 2.54 mm grid spanning 35.56 mm (14 pitches). Pin positions INFERRED from the 8 mm
        # creepage spec: 1,2 | gap | 3,4,5,6 | gap | 7,8.  VERIFY against Murata KDC_MGJ6C-SCDC drawing.
        posn = {'1': 0, '2': 1, '3': 5, '4': 6, '5': 7, '6': 8, '7': 13, '8': 14}
        for num, k in posn.items():
            mkpad(fp, num, 0, k * 2.54, pcbnew.PAD_SHAPE_RECT if num == '1' else pcbnew.PAD_SHAPE_CIRCLE, (2.0, 2.0), 1.2)
        rect(fp, pcbnew.F_CrtYd, -6.2, -1.9, 6.2, 37.5, 0.05)
        rect(fp, pcbnew.F_SilkS, -5.9, -1.6, 5.9, 37.2)
        rect(fp, pcbnew.F_Fab, -5.45, -1.67, 5.45, 37.22, 0.1)
        fptext(fp, 'MGJ6', 3.2, 18.0, size=1.0)
    elif name == 'IHV_AuxTerminal_M4':
        mkpad(fp, '1', 0, 0, pcbnew.PAD_SHAPE_CIRCLE, (10.0, 10.0), 4.3)
        c = pcbnew.FP_SHAPE(fp); c.SetShape(pcbnew.SHAPE_T_CIRCLE); c.SetLayer(pcbnew.F_CrtYd); c.SetWidth(MM(0.05))
        c.SetStart0(VECTOR2I(0, 0)); c.SetEnd0(VECTOR2I(MM(5.6), 0)); fp.Add(c); c.SetDrawCoord()
        c = pcbnew.FP_SHAPE(fp); c.SetShape(pcbnew.SHAPE_T_CIRCLE); c.SetLayer(pcbnew.F_SilkS); c.SetWidth(MM(0.15))
        c.SetStart0(VECTOR2I(0, 0)); c.SetEnd0(VECTOR2I(MM(5.4), 0)); fp.Add(c); c.SetDrawCoord()
    else:
        raise KeyError(name)
    return fp

def load_fp(fpid):
    lib, name = fpid.split(':')
    if lib == 'GD':
        return custom_fp(name)
    fp = pcbnew.FootprintLoad(os.path.join(FPLIB, lib + '.pretty'), name)
    if fp is None:
        raise RuntimeError('footprint not found ' + fpid)
    return fp

FPS = {}
for c in design.COMPS:
    fp = load_fp(c['fp'])
    fp.SetReference(c['ref'])
    fp.SetValue(c['value'])
    for k in ('MPN', 'Manufacturer'):
        if c[k.lower() if k == 'MPN' else 'mfr']:
            fp.SetProperty(k, c['mpn'] if k == 'MPN' else c['mfr'])
    if c['dnp']:
        fp.SetAttributes(fp.GetAttributes() | pcbnew.FP_EXCLUDE_FROM_BOM | pcbnew.FP_EXCLUDE_FROM_POS_FILES)
    for pad in fp.Pads():
        n = c['nets'].get(pad.GetNumber())
        if n:
            pad.SetNet(net(n))
    board.Add(fp)
    FPS[c['ref']] = fp

def place(ref, x, y, rot=0, bottom=False):
    fp = FPS[ref]
    fp.SetPosition(P(x, y))
    if rot:
        fp.SetOrientationDegrees(rot)
    if bottom:
        fp.Flip(fp.GetPosition(), False)
    PLACED.add(ref)

PLACED = set()

def bbox(ref):
    fp = FPS[ref]
    b = fp.GetBoundingBox(False, False)
    return pcbnew.ToMM(b.GetWidth()), pcbnew.ToMM(b.GetHeight()), \
        pcbnew.ToMM(fp.GetPosition().x - b.GetX()), pcbnew.ToMM(fp.GetPosition().y - b.GetY())

def pack(refs, x0, y0, x1, y1, gap=1.0, rot=0, bottom=False):
    """Shelf-pack footprints (in given order) into a rectangle."""
    cx, cy, rowh = x0, y0, 0
    for ref in refs:
        if rot:
            FPS[ref].SetOrientationDegrees(rot)
        FPS[ref].SetPosition(P(0, 0))
        if bottom and not FPS[ref].IsFlipped():
            FPS[ref].Flip(FPS[ref].GetPosition(), False)
        w, h, ox, oy = bbox(ref)
        if cx + w > x1:
            cx, cy, rowh = x0, cy + rowh + gap, 0
        if cy + h > y1:
            raise RuntimeError(f'region full at {ref} ({x0},{y0},{x1},{y1})')
        FPS[ref].SetPosition(P(cx + ox, cy + oy))
        PLACED.add(ref)
        cx += w + gap
        rowh = max(rowh, h)
    return cy + rowh

# ======================= PLACEMENT (rev B: 1SP0635 envelope) =======================
class Region:
    def __init__(self, name, x0, y0, x1, y1, bottom=False):
        self.name, self.x0, self.y0, self.x1, self.y1, self.bottom = name, x0, y0, x1, y1, bottom
        self.cx, self.cy, self.rowh = x0, y0, 0.0

def try_place(ref, reg, gap, rot):
    fp = FPS[ref]
    fp.SetOrientationDegrees(rot)
    fp.SetPosition(P(0, 0))
    if reg.bottom != fp.IsFlipped():
        fp.Flip(fp.GetPosition(), False)
    w, h, ox, oy = bbox(ref)
    cx, cy, rowh = reg.cx, reg.cy, reg.rowh
    if cx + w > reg.x1:
        cx, cy, rowh = reg.x0, cy + rowh + gap, 0.0
    if cy + h > reg.y1 or cx + w > reg.x1:
        return False
    fp.SetPosition(P(cx + ox, cy + oy))
    reg.cx, reg.cy, reg.rowh = cx + w + gap, cy, max(rowh, h)
    PLACED.add(ref)
    return True

def pack_multi(refs, regions, gap=0.8, rot=0):
    for ref in refs:
        if not any(try_place(ref, r, gap, rot) for r in regions):
            raise RuntimeError(f'no room for {ref} in {[r.name for r in regions]}')

# --- fixed items
for r, (x, y) in TERM.items():
    place(r, x, y)
place('H1', *SUPPORT)
MGJ6_X, MGJ6_Y = 156.0, 4.0
place('U1', MGJ6_X, MGJ6_Y)
place('J1', 195.2, 6.0)
place('U5', 106.0, 34.2)              # fiber RX (pins row), mounting pins toward bottom edge
place('U10', 120.0, 34.2)             # fiber TX

# --- HV chains (left end, around the aux-collector terminal)
for i in range(5):                    # D24 (COLL) ... D20 (CLMP)
    place(f'D{24 - i}', 7.5 + i * 7.4, 37.0)
for i in range(4):                    # D10 (COLL) ... D7 (DS_R), sharing resistors above
    place(f'D{10 - i}', 10.0 + i * 8.0, 28.5)
    place(f'R{26 + i}', 10.0 + i * 8.0, 24.0)
place('D28', 51.5, 35.5, 180)
place('D29', 51.5, 40.5, 180)
place('R30', 50.5, 28.5)

# --- regions
T_mid_pads = Region('T_mid_pads', 64.2, 0.6, 73.6, 12.5)
T_top = Region('T_top', 85.0, 0.6, 146.0, 12.5)
T_gateQ = Region('T_gateQ', 56.0, 13.0, 106.5, 30.0)
T_gateD = Region('T_gateD', 56.0, 30.5, 102.5, 40.5)
T_left = Region('T_left', 47.0, 13.0, 55.8, 26.5)
T_mid = Region('T_mid', 107.0, 13.0, 149.0, 30.0)
T_lowr = Region('T_lowright', 131.5, 31.0, 146.0, 43.4)
T_pri2 = Region('T_pri_low', 165.0, 35.0, 189.0, 43.4)
T_pri = Region('T_pri', 165.0, 3.2, 192.8, 35.0)
B_gate = Region('B_gate', 47.0, 13.0, 102.5, 40.4, bottom=True)
B_logic = Region('B_logic', 103.0, 0.6, 146.0, 29.5, bottom=True)
B_lowr = Region('B_lowright', 131.5, 31.0, 146.0, 43.4, bottom=True)
B_pri = Region('B_pri', 165.0, 3.2, 192.8, 35.0, bottom=True)

pack_multi(['C34', 'C35', 'C36', 'C37'], [T_mid_pads], gap=0.8, rot=90)
pack_multi(['C13', 'C16', 'C38', 'C39', 'C40', 'C41', 'U12', 'C30', 'C31'], [T_top], gap=0.8)
pack_multi(['Q4', 'Q5', 'Q6', 'Q7'], [T_gateQ], gap=1.4, rot=90)
pack_multi(['D13', 'D14', 'D15', 'D16', 'D17', 'D18'], [T_gateD], gap=0.9, rot=90)
pack_multi(['D19'], [T_left], gap=0.8, rot=90)
pack_multi(['R56', 'C29'], [T_left], gap=0.8)
pack_multi(['U4', 'R57', 'C11', 'C12', 'C14', 'C15', 'C17', 'C18', 'U11', 'C27', 'U9', 'C25',
            'U6', 'U7', 'U8', 'C21', 'C22', 'C19', 'Q1', 'Q2', 'Q3', 'LED2', 'D11', 'D12', 'C28', 'R31',
            'R22', 'R12', 'C20', 'C23', 'C24', 'C26'], [T_mid, T_lowr, T_top], gap=0.8)
pack_multi(['J2'], [T_pri2], gap=0.8)
pack_multi(['L1', 'C1', 'D2', 'D1', 'F1', 'C2', 'C3', 'C5', 'R5', 'LED1'], [T_pri, B_pri], gap=1.0)
pack_multi(['R%d' % i for i in range(38, 54)] + ['R55', 'R54', 'R36', 'R37', 'C32', 'C33'], [B_gate], gap=0.6)
rest = [c['ref'] for c in design.COMPS if c['ref'] not in PLACED]
pack_multi(rest, [B_logic, B_lowr, B_gate], gap=0.6)

missing = [c['ref'] for c in design.COMPS if c['ref'] not in PLACED]
assert not missing, missing

# ======================= BOARD OUTLINE / SLOTS =======================
def edge_line(x0, y0, x1, y1):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetLayer(pcbnew.Edge_Cuts)
    s.SetWidth(MM(0.1))
    s.SetStart(P(x0, y0)); s.SetEnd(P(x1, y1))
    board.Add(s)

def edge_poly(pts):
    for a, b in zip(pts, pts[1:] + pts[:1]):
        edge_line(a[0], a[1], b[0], b[1])

# 1SP0635 outline (Fig. 5): top step at x=162.6 (2.59 mm), bottom notch x=69.3..100.5 (3.3 mm).
# DEVIATION: the 1SP0635 diagonal cut-out under its fiber connectors (x~129..177) is filled in here to fit the MGJ6.
edge_poly([(0, 0), (162.6, 0), (162.6, 2.59), (BW, 2.59), (BW, BH), (100.5, BH), (100.5, BH - 3.3),
           (69.3, BH - 3.3), (69.3, BH), (0, BH)])
# creepage-extension slots under the MGJ6 between primary and secondary pin groups
for ys in (MGJ6_Y + 3 * 2.54 + 0.0, MGJ6_Y + 10.5 * 2.54):
    edge_poly([(149.0, ys - 0.75), (163.0, ys - 0.75), (163.0, ys + 0.75), (149.0, ys + 0.75)])

# ======================= ZONES =======================
def zone(netname, layer, pts, prio=0, clearance=0.5, name=None):
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    if netname:
        z.SetNet(net(netname))
    ol = z.Outline()
    ol.NewOutline()
    for (x, y) in pts:
        ol.Append(MM(OX + x), MM(OY + y))
    z.SetLocalClearance(MM(clearance))
    z.SetMinThickness(MM(0.25))
    z.SetAssignedPriority(prio)
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    if name:
        z.SetZoneName(name)
    board.Add(z)
    return z

SEC_POLY = [(46.0, 0.3), (150.0, 0.3), (150.0, 43.6), (46.0, 43.6)]
zone('COM_E', pcbnew.In1_Cu, SEC_POLY, name='COM_E_plane')
zone('VN10', pcbnew.In2_Cu, SEC_POLY, name='VN10_plane')
PRI_POLY = [(158.0, 0.3), (196.9, 0.3), (196.9, 43.7), (158.0, 43.7)]
zone('GND_P', pcbnew.In1_Cu, PRI_POLY, name='GND_P_in1')
zone('GND_P', pcbnew.B_Cu, PRI_POLY, name='GND_P_bottom')

# ======================= HV ROUTING (scripted) =======================
def pad_pos(ref, num):
    for p in FPS[ref].Pads():
        if p.GetNumber() == num:
            return p.GetPosition()
    raise KeyError((ref, num))

def track(a, b, netname, w=0.6, layer=pcbnew.F_Cu):
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(a); t.SetEnd(b); t.SetWidth(MM(w)); t.SetLayer(layer); t.SetNet(net(netname))
    board.Add(t)

def route(points, netname, w=0.6):
    for a, b in zip(points, points[1:]):
        track(a, b, netname, w)

def XY(x, y):
    return P(x, y)

def ymm(v):
    return pcbnew.ToMM(v.y) - OY

def xmm(v):
    return pcbnew.ToMM(v.x) - OX

x3 = pad_pos('X3', '1')
TRUNK = 2.6
route([x3, XY(TRUNK, ymm(x3)), XY(TRUNK, ymm(pad_pos('D24', '1')))], 'COLL', 0.8)
k24 = pad_pos('D24', '1'); route([XY(TRUNK, ymm(k24)), k24], 'COLL', 0.8)
k10 = pad_pos('D10', '1'); route([XY(TRUNK, ymm(k10)), k10], 'COLL', 0.6)
r26 = pad_pos('R26', '1'); route([XY(TRUNK, ymm(r26)), r26], 'COLL', 0.6)
tv = ['ACK4', 'ACK3', 'ACK2', 'ACK1']
for i in range(4):
    route([pad_pos(f'D{24 - i}', '2'), pad_pos(f'D{23 - i}', '1')], tv[i], 0.6)
dn = ['DS3', 'DS2', 'DS1', 'DS_R']
for i in range(4):
    route([pad_pos(f'D{10 - i}', '2'), pad_pos(f'R{26 + i}', '2')], dn[i], 0.5)
    if i < 3:
        route([pad_pos(f'D{10 - i}', '2'), pad_pos(f'D{9 - i}', '1')], dn[i], 0.5)
        route([pad_pos(f'R{26 + i}', '2'), pad_pos(f'R{27 + i}', '1')], dn[i], 0.5)
a7 = pad_pos('D7', '2'); r30 = pad_pos('R30', '1')
route([a7, XY(xmm(r30), ymm(a7)), r30], 'DS_R', 0.4)
a20 = pad_pos('D20', '2'); a28 = pad_pos('D28', '2'); a29 = pad_pos('D29', '2')
jx = 45.0
route([a20, XY(jx, ymm(a20)), XY(jx, ymm(a28)), a28], 'CLMP', 0.6)
route([XY(jx, ymm(a20)), XY(jx, ymm(a29)), a29], 'CLMP', 0.6)

# ======================= SILKSCREEN NOTES =======================
def text(s, x, y, size=1.5, layer=pcbnew.F_SilkS, bold=False):
    t = pcbnew.PCB_TEXT(board)
    t.SetText(s); t.SetPosition(P(x, y)); t.SetLayer(layer)
    t.SetTextSize(VECTOR2I(MM(size), MM(size))); t.SetTextThickness(MM(size * (0.2 if bold else 0.15)))
    t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_LEFT)
    board.Add(t)

text('FZ1200R33KF2 GATE DRIVER rev B', 2.0, 42.3, 0.9, layer=pcbnew.B_SilkS)
text('!! HV 3.3 kV !!', 1.5, 20.0, 1.0, bold=True)
text('C', TERM['X3'][0] - 8.0, TERM['X3'][1] - 1.0, 1.4, bold=True)
text('E', TERM['X2'][0] - 8.0, TERM['X2'][1] - 1.0, 1.4, bold=True)
text('G', TERM['X1'][0] + 6.0, TERM['X1'][1] - 1.0, 1.4, bold=True)
text('IN', 104.0, 31.2, 0.9)
text('STATUS', 118.0, 31.2, 0.9)
text('15V: 1,2=VDC 3,4=GND', 166.0, 1.0 + 2.59, 0.8)

# ======================= NET CLASSES / RULES =======================
PRI = ['GND_P', 'VIN_RAW', 'VIN_F', 'VIN_P', 'VIN_M', 'SYNC_P', 'LED_P']
HV = ['COLL', 'ACK2', 'ACK3', 'ACK4', 'DS2', 'DS3']
HVLOW = ['ACK1', 'CLMP', 'DS1', 'DS_R']
POWER = ['VP15', 'VN10', 'COM_E', 'GATE', 'OUT_A', 'OUT_B', 'GON_A', 'GON_B', 'GOFF_A', 'GOFF_B', 'BASE']
def nc(name, clr, tw):
    return {"name": name, "clearance": clr, "track_width": tw, "via_diameter": 0.8, "via_drill": 0.4,
            "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25, "microvia_diameter": 0.3,
            "microvia_drill": 0.1, "bus_width": 12, "line_style": 0, "pcb_color": "rgba(0, 0, 0, 0.000)",
            "schematic_color": "rgba(0, 0, 0, 0.000)", "wire_width": 6}
pf = f'{OUT}/{PROJ}.kicad_pro'
pro = json.load(open(pf))
pro['net_settings'] = {"classes": [nc('Default', 0.2, 0.25), nc('PRI', 0.3, 0.4), nc('HV', 1.5, 0.6),
                                   nc('HVLOW', 0.5, 0.5), nc('POWER', 0.25, 1.0)],
                       "meta": {"version": 3}, "net_colors": None, "netclass_assignments": None,
                       "netclass_patterns": [{"netclass": c, "pattern": '/' + n} for c, ns in
                                             (('PRI', PRI), ('HV', HV), ('HVLOW', HVLOW), ('POWER', POWER)) for n in ns]}
pro['board'] = {"design_settings": {"rules": {"min_clearance": 0.15, "min_track_width": 0.15, "min_hole_clearance": 0.25,
                "min_copper_edge_clearance": 0.4, "min_through_hole_diameter": 0.3, "min_via_diameter": 0.5,
                "min_via_annular_width": 0.1, "min_hole_to_hole": 0.25}}}
json.dump(pro, open(pf, 'w'), indent=2)
def anyof(side, nets):
    return '(' + ' || '.join(f"{side}.NetName == '/{n}'" for n in nets) + ')'
ALLN = sorted(design.nets().keys())
SECLV = [n for n in ALLN if n not in PRI + HV + HVLOW]
dru = """(version 1)
# Isolation coordination (IEC 61800-5-1 / IEC 60664-1, PD2, CTI>=600). Verify for your system voltage.
(rule "PRI_to_SEC_MGJ6_functional"
  (constraint clearance (min 8mm))
  (condition "%s && %s"))
(rule "COLL_to_LV_secondary"
  (constraint clearance (min 16mm))
  (condition "A.NetName == '/COLL' && %s"))
(rule "HV_to_LV_secondary"
  (constraint clearance (min 8mm))
  (condition "%s && %s"))
(rule "HV_chain_internal"
  (constraint clearance (min 1.5mm))
  (condition "%s && %s"))
""" % (anyof('A', PRI), anyof('B', SECLV + HV + HVLOW), anyof('B', SECLV), anyof('A', HV), anyof('B', SECLV),
       anyof('A', HV), anyof('B', HV + HVLOW))
open(f'{OUT}/{PROJ}.kicad_dru', 'w').write(dru)

# ======================= SAVE =======================
pcbfile = f'{OUT}/{PROJ}.kicad_pcb'
board.Save(pcbfile)
board = pcbnew.LoadBoard(pcbfile)
board.BuildConnectivity()
filler = pcbnew.ZONE_FILLER(board)
filler.Fill(board.Zones())
board.Save(pcbfile)
print('saved', pcbfile, 'footprints', len(FPS), 'nets', len(netmap))
