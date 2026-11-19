"""Generate hierarchical KiCad 7 schematic from design.py."""
import uuid, os, sys, math
from sexp import Sym, dumps, find, findall
import libs, design

PROJ = 'FZ1200R33_GateDriver'
OUT = sys.argv[1] if len(sys.argv) > 1 else '/home/claude/out/' + PROJ
os.makedirs(OUT, exist_ok=True)
NS = uuid.UUID('6f1c2c55-3c6b-4d6e-9a43-1b2a0f3e8d11')
def U(*k):
    return str(uuid.uuid5(NS, '/'.join(map(str, k))))

ROOT_UUID = U('root')
for c in design.COMPS:
    if c['sym'] is None:
        c['sym'] = 'Mechanical:MountingHole'

def symdef(libid):
    if libid in design.CUSTOM_SYMBOLS:
        return design.CUSTOM_SYMBOLS[libid]
    return libs.load(libid)

SYMCACHE = {}
def getsym(libid):
    if libid not in SYMCACHE:
        SYMCACHE[libid] = symdef(libid)
    return SYMCACHE[libid]

# which nets span several sheets -> global labels
net_sheets = {}
for c in design.COMPS:
    for p, n in c['nets'].items():
        if n:
            net_sheets.setdefault(n, set()).add(c['sheet'])
GLOBAL = {n for n, s in net_sheets.items() if len(s) > 1}

EFF = lambda just=None, hide=False: [Sym('effects'), [Sym('font'), [Sym('size'), 1.27, 1.27]]] + \
    ([[Sym('justify')] + [Sym(j) for j in just.split()]] if just else []) + ([Sym('hide')] if hide else [])

STUB = 2.54
GRID = 1.27
def snap(v):
    return round(v / GRID) * GRID

NOTES = {
    'pri': ['PRIMARY SIDE (15 V nominal, 12-16 V). Same supply interface as 1SD418FI / 1SP0635: VDC on pins 1-2, GND on pins 3-4.',
            'Isolation: Murata MGJ6D121510SC (off-the-shelf). 5.7 kVDC hi-pot, 3 kVDC continuous FUNCTIONAL barrier, PD inception >3.5 kVpk,',
            '8 mm creepage, 15 pF coupling. NOT reinforced at 3.3 kV system level: safety isolation must be provided by the 15 V supply.',
            'Output config (datasheet IGBT table): pin 3 = +15 V, pin 6 = 0 V (to aux emitter), pin 4 = -10 V, pin 5 = -5 V tap (NC).'],
    'sec': ['SECONDARY SIDE at IGBT emitter potential. COM_E = auxiliary emitter. VP15 = +15 V, VN10 = -10 V (MGJ6, unregulated +/-3 %/+/-5 %).',
            'R57 preloads the +15 V output above the MGJ6 10 % minimum load (otherwise it clamps at ~17 V). The -10 V output is loaded by U4/logic.',
            'VL5 is the logic rail = VN10 + 5 V; ALL logic, comparators and fiber parts are referenced to VN10.'],
    'logic': ['Fiber RX: light ON -> RX_OUT low -> CMD high -> IGBT ON (same convention as 1SD418FI).',
              'READY (wired-AND, open drain): low on VP15-VN10 undervoltage (<20.8 V) or while the 1 s blocking timer runs.',
              'Status fiber TX: light ON = READY (no fault). Desat fault -> TLC555 blocks gate for ~1 s, retriggered while fault persists.'],
    'desat': ['VCE monitoring through 4x 1200 V ultrafast diodes with 22 Mohm sharing resistors (rated for 3.3 kV + margin).',
              'Blanking: R31/C28 from VN10 to trip point (COM_E + 13.3 V) ~5 us. Trip at VCE ~ 9 V. Q3 resets DESAT while gate is OFF.',
              'Comparator A output (nDESAT, active low) triggers the TLC555 blocking timer.'],
    'gate': ['Pre-driver UCC27531 (VDD 25 V) drives 2x complementary BJT emitter-follower pairs (MJB44H11/MJB45H11). Gate swing +15 V / -10 V.',
             'Separate turn-on / turn-off gate resistor arrays (Schottky steered). Default 4x10R per branch -> RGon = RGoff = 1.25 ohm',
             '(datasheet test value 1.2 ohm). Peak gate current ~ +15/-10 A. Optional CGE footprints C32/C33 (DS test: 220 nF).'],
    'clamp': ['ADVANCED ACTIVE CLAMPING: 5x P6SMB550A (Vbr 2.6-2.9 kV) from aux collector. Clamp current is steered to the gate (D28/R55)',
              'AND to the booster base node (D29/R56), so the turn-off transistors are throttled instead of sinking the clamp current.',
              'X1..X3 are the module M4 control terminals: board is screwed directly onto G / E(aux) / C(aux). Positions per 1SP0635 manual Fig. 5.'],
}

def place_sheet(key, title, desc):
    comps = [c for c in design.COMPS if c['sheet'] == key]
    items = []
    lib_used = set()
    labels, wires, nocs = [], [], []
    x0, y0 = 30.0, 60.0
    xmax = 395.0
    cx, cy, rowh = x0, y0, 0
    sheet_uuid = U('sheet', key)
    for c in comps:
        sd = getsym(c['sym'])
        lib_used.add(c['sym'])
        allpins = libs.pins(sd)
        us = libs.units(sd)
        for unit in us:
            pins = [p for p in allpins if p['unit'] in (unit, 0)]
            if not pins:
                continue
            xs = [p['x'] for p in pins]; ys = [p['y'] for p in pins]
            def lab_len(p):
                n = c['nets'].get(p['number'])
                return (len(n) * 1.1 + 4) if n else 2
            left = max([lab_len(p) for p in pins if p['angle'] == 0] + [0])
            right = max([lab_len(p) for p in pins if p['angle'] == 180] + [0])
            top = max([lab_len(p) for p in pins if p['angle'] == 270] + [0])
            bot = max([lab_len(p) for p in pins if p['angle'] == 90] + [0])
            w = (max(xs) - min(xs)) + left + right + 2 * STUB + 14
            h = (max(ys) - min(ys)) + top + bot + 2 * STUB + 12
            if cx + w > xmax:
                cx = x0; cy += rowh; rowh = 0
            X = snap(cx + left + STUB + 4 - min(xs))
            Y = snap(cy + top + STUB + 4 + max(ys))
            cx += w; rowh = max(rowh, h)
            inst = [Sym('symbol'), [Sym('lib_id'), c['sym']], [Sym('at'), X, Y, 0], [Sym('unit'), unit],
                    [Sym('in_bom'), Sym('yes')], [Sym('on_board'), Sym('yes')], [Sym('dnp'), Sym('yes' if c['dnp'] else 'no')],
                    [Sym('uuid'), U('sym', c['ref'], unit)]]
            props = {p[1]: p for p in findall(sd, 'property')}
            def prop(name, val, hide=False, just=None):
                lp = props.get(name)
                if lp is not None:
                    at = find(lp, 'at'); px, py = float(at[1]), float(at[2]); ang = float(at[3]) if len(at) > 3 else 0
                else:
                    px, py, ang = 0, 0, 0
                return [Sym('property'), name, val, [Sym('at'), X + px, Y - py, ang], EFF(just, hide)]
            inst.append(prop('Reference', c['ref']))
            inst.append(prop('Value', c['value']))
            inst.append(prop('Footprint', c['fp'], True))
            inst.append(prop('Datasheet', '~', True))
            for k, v in (('MPN', c['mpn']), ('Manufacturer', c['mfr']), ('Description', c['desc'])):
                inst.append([Sym('property'), k, v, [Sym('at'), X, Y, 0], EFF(None, True)])
            for p in allpins:
                if p['unit'] in (unit, 0):
                    inst.append([Sym('pin'), p['number'], [Sym('uuid'), U('pin', c['ref'], unit, p['number'])]])
            inst.append([Sym('instances'), [Sym('project'), PROJ, [Sym('path'), f'/{ROOT_UUID}/{sheet_uuid}',
                         [Sym('reference'), c['ref']], [Sym('unit'), unit]]]])
            items.append(inst)
            # connections
            seen = set()
            for p in pins:
                px, py = X + p['x'], Y - p['y']
                if (round(px, 2), round(py, 2)) in seen:
                    continue
                seen.add((round(px, 2), round(py, 2)))
                net = c['nets'].get(p['number'])
                if c['sym'] == 'Mechanical:MountingHole':
                    continue
                if net is None:
                    nocs.append([Sym('no_connect'), [Sym('at'), px, py], [Sym('uuid'), U('nc', c['ref'], p['number'])]])
                    continue
                a = math.radians(p['angle'])
                dx, dy = -math.cos(a), math.sin(a)
                ex, ey = round(px + STUB * dx, 2), round(py + STUB * dy, 2)
                wires.append([Sym('wire'), [Sym('pts'), [Sym('xy'), px, py], [Sym('xy'), ex, ey]],
                              [Sym('stroke'), [Sym('width'), 0], [Sym('type'), Sym('default')]],
                              [Sym('uuid'), U('w', c['ref'], p['number'])]])
                if abs(dx) > 0.5:
                    ang, just = (0, 'left') if dx > 0 else (180, 'right')
                else:
                    ang, just = (90, 'left') if dy < 0 else (270, 'right')
                if net in GLOBAL:
                    labels.append([Sym('global_label'), net, [Sym('shape'), Sym('bidirectional')], [Sym('at'), ex, ey, ang],
                                   [Sym('fields_autoplaced')], EFF(just), [Sym('uuid'), U('gl', c['ref'], p['number'])],
                                   [Sym('property'), 'Intersheetrefs', '${INTERSHEET_REFS}', [Sym('at'), ex, ey, 0], EFF(None, True)]])
                else:
                    labels.append([Sym('label'), net, [Sym('at'), ex, ey, ang], [Sym('fields_autoplaced')],
                                   EFF(just + ' bottom'), [Sym('uuid'), U('lb', c['ref'], p['number'])]])
    # PWR_FLAGs for nets with no driver
    flags = PWR_FLAGS.get(key, [])
    if flags:
        lib_used.add('power:PWR_FLAG')
    fx = 30.0
    fy = snap(cy + rowh + 20)
    for i, net in enumerate(flags):
        X, Y = snap(fx + i * 30), fy
        ref = f'#FLG{key}{i}'
        items.append([Sym('symbol'), [Sym('lib_id'), 'power:PWR_FLAG'], [Sym('at'), X, Y, 0], [Sym('unit'), 1],
                      [Sym('in_bom'), Sym('yes')], [Sym('on_board'), Sym('yes')], [Sym('dnp'), Sym('no')],
                      [Sym('uuid'), U('flag', key, net)],
                      [Sym('property'), 'Reference', ref, [Sym('at'), X, Y - 3.81, 0], EFF(None, True)],
                      [Sym('property'), 'Value', 'PWR_FLAG', [Sym('at'), X, Y - 3.81, 0], EFF()],
                      [Sym('property'), 'Footprint', '', [Sym('at'), X, Y, 0], EFF(None, True)],
                      [Sym('property'), 'Datasheet', '~', [Sym('at'), X, Y, 0], EFF(None, True)],
                      [Sym('pin'), '1', [Sym('uuid'), U('flagpin', key, net)]],
                      [Sym('instances'), [Sym('project'), PROJ, [Sym('path'), f'/{ROOT_UUID}/{sheet_uuid}',
                                                                  [Sym('reference'), ref], [Sym('unit'), 1]]]]])
        wires.append([Sym('wire'), [Sym('pts'), [Sym('xy'), X, Y], [Sym('xy'), X, Y + 2.54]],
                      [Sym('stroke'), [Sym('width'), 0], [Sym('type'), Sym('default')]], [Sym('uuid'), U('fw', key, net)]])
        if net in GLOBAL:
            labels.append([Sym('global_label'), net, [Sym('shape'), Sym('bidirectional')], [Sym('at'), X, Y + 2.54, 270],
                           [Sym('fields_autoplaced')], EFF('right'), [Sym('uuid'), U('fgl', key, net)],
                           [Sym('property'), 'Intersheetrefs', '${INTERSHEET_REFS}', [Sym('at'), X, Y, 0], EFF(None, True)]])
        else:
            labels.append([Sym('label'), net, [Sym('at'), X, Y + 2.54, 270], [Sym('fields_autoplaced')], EFF('right bottom'),
                           [Sym('uuid'), U('flb', key, net)]])
    # notes
    texts = [[Sym('text'), title.replace('_', ' ') + ' - ' + desc, [Sym('at'), 30, 25, 0],
              [Sym('effects'), [Sym('font'), [Sym('size'), 3.5, 3.5], Sym('bold')], [Sym('justify'), Sym('left'), Sym('bottom')]],
              [Sym('uuid'), U('title', key)]]]
    for i, line in enumerate(NOTES.get(key, [])):
        texts.append([Sym('text'), line, [Sym('at'), 30, 33 + i * 5, 0],
                      [Sym('effects'), [Sym('font'), [Sym('size'), 2, 2]], [Sym('justify'), Sym('left'), Sym('bottom')]],
                      [Sym('uuid'), U('note', key, i)]])
    paper = 'A3' if cy + rowh < 255 else 'A2'
    libsyms = [Sym('lib_symbols')] + [getsym(l) if l != 'power:PWR_FLAG' else libs.load('power:PWR_FLAG') for l in sorted(lib_used)]
    doc = [Sym('kicad_sch'), [Sym('version'), 20230121], [Sym('generator'), Sym('eeschema')],
           [Sym('uuid'), U('file', key)], [Sym('paper'), paper],
           [Sym('title_block'), [Sym('title'), 'FZ1200R33KF2 plug-on gate driver - ' + title.replace('_', ' ')],
            [Sym('date'), '2026-11-09'], [Sym('rev'), 'B'], [Sym('company'), 'Open design (non-proprietary 1SD418FI-class)']],
           libsyms] + items + wires + labels + nocs + texts + [[Sym('sheet_instances'), [Sym('path'), '/', [Sym('page'), '1']]]]
    with open(f'{OUT}/{title}.kicad_sch', 'w') as f:
        f.write(dumps(doc) + '\n')
    return sheet_uuid

PWR_FLAGS = {'pri': ['GND_P', 'VIN_M']}

sheet_syms = []
for i, (key, title, desc) in enumerate(design.SHEETS):
    su = place_sheet(key, title, desc)
    x, y = 30 + (i % 3) * 120, 50 + (i // 3) * 70
    sheet_syms.append([Sym('sheet'), [Sym('at'), x, y], [Sym('size'), 90, 40], [Sym('fields_autoplaced')],
                       [Sym('stroke'), [Sym('width'), 0.1524], [Sym('type'), Sym('solid')]],
                       [Sym('fill'), [Sym('color'), 0, 0, 0, 0.0]], [Sym('uuid'), su],
                       [Sym('property'), 'Sheetname', title, [Sym('at'), x, y - 0.7, 0], EFF('left bottom')],
                       [Sym('property'), 'Sheetfile', title + '.kicad_sch', [Sym('at'), x, y + 40.6, 0], EFF('left top')],
                       [Sym('instances'), [Sym('project'), PROJ, [Sym('path'), f'/{ROOT_UUID}', [Sym('page'), str(i + 2)]]]]])

root_text = [
    'FZ1200R33KF2 (3.3 kV / 1200 A, IHV 190x140) PLUG-ON GATE DRIVER rev B - 1SD418FI/1SP0635-class, no Power Integrations/CONCEPT parts',
    'Interfaces: 15 V power on 1x4 2.54 mm header (J1) or JST-XH (J2); fiber command input HFBR-2521Z; fiber status output HFBR-1521Z.',
    'Features: +15/-10 V gate drive, ~+/-15 A peak, separate RGon/RGoff, advanced active clamping (~2.7 kV), VCE(sat) short-circuit',
    'detection with ~5 us blanking, supply UVLO, 1 s fault blocking time, status feedback. Isolated supply: Murata MGJ6D121510SC (COTS).',
    'Mounting: C/E/G M4 terminal holes and support hole at the 1SP0635 positions. MGJ6 barrier is functional (3 kVDC) - see README.',
]
texts = [[Sym('text'), t, [Sym('at'), 30, 170 + i * 6, 0],
          [Sym('effects'), [Sym('font'), [Sym('size'), 2.2, 2.2]], [Sym('justify'), Sym('left'), Sym('bottom')]],
          [Sym('uuid'), U('rootnote', i)]] for i, t in enumerate(root_text)]
root = [Sym('kicad_sch'), [Sym('version'), 20230121], [Sym('generator'), Sym('eeschema')], [Sym('uuid'), ROOT_UUID],
        [Sym('paper'), 'A3'],
        [Sym('title_block'), [Sym('title'), 'FZ1200R33KF2 plug-on gate driver rev B (open design)'],
         [Sym('date'), '2026-11-09'], [Sym('rev'), 'B']],
        [Sym('lib_symbols')]] + sheet_syms + texts + [[Sym('sheet_instances'), [Sym('path'), '/', [Sym('page'), '1']]]]
with open(f'{OUT}/{PROJ}.kicad_sch', 'w') as f:
    f.write(dumps(root) + '\n')

import json
pro = {"meta": {"filename": PROJ + ".kicad_pro", "version": 1},
       "sheets": [[ROOT_UUID, "Root"]] + [[U('sheet', k), t] for k, t, _ in design.SHEETS],
       "boards": [], "libraries": {"pinned_footprint_libs": [], "pinned_symbol_libs": []},
       "net_settings": {"classes": [
           {"name": "Default", "clearance": 0.2, "track_width": 0.25, "via_diameter": 0.8, "via_drill": 0.4,
            "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25, "microvia_diameter": 0.3,
            "microvia_drill": 0.1, "bus_width": 12, "line_style": 0, "pcb_color": "rgba(0, 0, 0, 0.000)",
            "schematic_color": "rgba(0, 0, 0, 0.000)", "wire_width": 6}], "meta": {"version": 3},
           "net_colors": None, "netclass_assignments": None, "netclass_patterns": []},
       "schematic": {"legacy_lib_dir": "", "legacy_lib_list": []},
       "text_variables": {}}
if not os.path.exists(f'{OUT}/{PROJ}.kicad_pro'):
    with open(f'{OUT}/{PROJ}.kicad_pro', 'w') as f:
        json.dump(pro, f, indent=2)
print('schematic written to', OUT, 'globals:', len(GLOBAL))
