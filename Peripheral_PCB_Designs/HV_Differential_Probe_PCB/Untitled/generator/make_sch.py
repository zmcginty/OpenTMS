"""Write hvprobe.kicad_sch (KiCad 7 format) from design.PARTS.

Every pin gets a 2.54 mm stub wire ending in a local net label (or a no-connect flag),
so connectivity is defined purely by net names and can be checked with kicad-cli.
"""
import math
import os
import re
import uuid as _uuid

from kiutils.symbol import SymbolLib

import design

KLIB = "/usr/share/kicad/symbols"
OFF = (10.0, 10.0)
STUB = 2.54


def U():
    return str(_uuid.uuid4())


def snap(v):
    return round(round(v / 1.27) * 1.27, 4)


def extract(text, name):
    """Return the s-expression text of top-level (symbol "name" ...) in a .kicad_sym file."""
    m = re.search(r'\(symbol "' + re.escape(name) + r'"[\s\n]', text)
    if not m:
        raise KeyError(name)
    i, depth, instr = m.start(), 0, False
    j = i
    while True:
        c = text[j]
        if instr:
            if c == "\\":
                j += 1
            elif c == '"':
                instr = False
        elif c == '"':
            instr = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
        j += 1


def lib_path(lib, proj_dir):
    return os.path.join(proj_dir, "hvprobe.kicad_sym") if lib == "hvprobe" else os.path.join(KLIB, lib + ".kicad_sym")


def build(proj_dir):
    root = U()
    libs_needed = sorted({p.lib_id for p in design.PARTS} | {"power:GND", "power:+12V", "power:-12V", "power:PWR_FLAG"})
    lib_text, pins_of = [], {}
    for lid in libs_needed:
        lib, name = lid.split(":")
        path = lib_path(lib, proj_dir)
        txt = open(path).read()
        s = extract(txt, name)
        s = s.replace(f'(symbol "{name}"', f'(symbol "{lid}"', 1)
        lib_text.append(s)
        sl = SymbolLib.from_file(path)
        sy = next(x for x in sl.symbols if x.entryName == name)
        pins_of[lid] = {p.number: (p.position.X, p.position.Y, p.position.angle or 0, p.hide)
                        for u in sy.units for p in u.pins}

    items = []
    labels_used = set()

    def wire(x1, y1, x2, y2):
        items.append(f'  (wire (pts (xy {x1} {y1}) (xy {x2} {y2})) (stroke (width 0) (type default)) (uuid {U()}))')

    def label(net, x, y, outward):
        ang, just = {(-1, 0): (180, "right"), (1, 0): (0, "left"), (0, -1): (90, "left"), (0, 1): (270, "right")}[outward]
        items.append(f'  (label "{net}" (at {x} {y} {ang}) (fields_autoplaced)\n'
                     f'    (effects (font (size 1.27 1.27)) (justify {just} bottom)) (uuid {U()}))')
        labels_used.add(net)

    def place_symbol(lid, ref, value, fp, x, y, extra=None, dnp=False, pin_numbers=()):
        props = [("Reference", ref, (x + 2.54, y - 6.35), False), ("Value", value, (x + 2.54, y + 6.35), False),
                 ("Footprint", fp, (x, y), True), ("Datasheet", "~", (x, y), True)]
        for k, v in (extra or {}).items():
            props.append((k, v, (x, y), True))
        ptxt = "\n".join(
            f'    (property "{k}" "{v}" (at {px} {py} 0) (effects (font (size 1.27 1.27)){" (justify left)" if not h else ""}{" hide" if h else ""}))'
            for k, v, (px, py), h in props)
        pins = "\n".join(f'    (pin "{n}" (uuid {U()}))' for n in pin_numbers)
        items.append(f'  (symbol (lib_id "{lid}") (at {x} {y} 0) (unit 1)\n'
                     f'    (in_bom {"no" if lid.startswith("power:") else "yes"}) (on_board {"no" if lid.startswith("power:") else "yes"}) (dnp {"yes" if dnp else "no"})\n'
                     f'    (uuid {U()})\n{ptxt}\n{pins}\n'
                     f'    (instances (project "{design.PROJECT}" (path "/{root}" (reference "{ref}") (unit 1))))\n  )')
        return items[-1]

    sym_uuid = {}
    for p in design.PARTS:
        x, y = snap(p.sch[0] + OFF[0]), snap(p.sch[1] + OFF[1])
        extra = {}
        if p.mpn:
            extra["MPN"] = p.mpn
        if p.note:
            extra["Note"] = p.note
        txt = place_symbol(p.lib_id, p.ref, p.value, p.footprint, x, y, extra, p.dnp, list(pins_of[p.lib_id].keys()))
        sym_uuid[p.ref] = re.search(r"\(uuid ([0-9a-f-]+)\)", txt).group(1)
        for num, (px, py, ang, hidden) in pins_of[p.lib_id].items():
            net = p.pins.get(num, None) if p.pins else None
            cx, cy = round(x + px, 4), round(y - py, 4)
            if net is None:
                if not hidden:
                    items.append(f'  (no_connect (at {cx} {cy}) (uuid {U()}))')
                continue
            if hidden:
                # hidden pins (e.g. stacked) share a location with a visible pin of the same net
                continue
            dx, dy = -round(math.cos(math.radians(ang))), round(math.sin(math.radians(ang)))
            ex, ey = round(cx + dx * STUB, 4), round(cy + dy * STUB, 4)
            wire(cx, cy, ex, ey)
            label(net, ex, ey, (dx, dy))

    # power ports: tie labels to global power symbols so the nets carry KiCad power names
    px0, py0 = snap(130 + OFF[0]), snap(172 + OFF[1])
    for i, net in enumerate(["GND", "+12V", "-12V"]):
        x = px0 + 20.32 * i
        wire(x, py0, x, py0 + 5.08)
        label(net, x, py0, (0, -1))
        place_symbol(f"power:{net}", f"#PWR0{i+1}", net, "", x, py0 + 5.08, pin_numbers=["1"])
        place_symbol("power:PWR_FLAG", f"#FLG0{i+1}", "PWR_FLAG", "", x + 2.54, py0 + 5.08, pin_numbers=["1"])
        wire(x, py0 + 5.08, x + 2.54, py0 + 5.08)
    # VIN_GND flag (isolated input side)
    x = px0 + 20.32 * 3
    wire(x, py0, x, py0 + 5.08)
    label("VIN_GND", x, py0, (0, -1))
    place_symbol("power:PWR_FLAG", "#FLG04", "PWR_FLAG", "", x, py0 + 5.08, pin_numbers=["1"])

    notes = [
        ((15, 22), "LEG P: 1000:1 RC-compensated divider (100 Mohm || 2.2 pF top, 100.1 kohm || 2.2 nF bottom)"),
        ((15, 92), "LEG N: identical to leg P, mirrored on the PCB across a milled slot"),
        ((255, 140), "DIFFERENCE AMP + OUTPUT: unity gain, 49.9 ohm back-terminated, 1000:1 into 1 Mohm"),
        ((15, 190), "POWER: battery (J4) or USB-C 5 V (J5) -> isolated TMR 3-0523 -> +/-12 V low-noise LDOs"),
        ((60, 275), "HV PROBE v1 - generated from the design doc. All HV_ nets: netclass HV (1.5 mm clearance)."),
    ]
    for (nx, ny), t in notes:
        items.append(f'  (text "{t}" (at {nx + OFF[0]} {ny + OFF[1]} 0)\n'
                     f'    (effects (font (size 2 2) (thickness 0.3) bold) (justify left bottom)) (uuid {U()}))')

    sch = (f'(kicad_sch (version 20230121) (generator eeschema)\n  (uuid {root})\n  (paper "A3")\n'
           f'  (title_block (title "7 kV / 50 MHz HV differential probe") (date "2026-11-14") (rev "v1")\n'
           f'    (comment 1 "Generated design - review before fabrication"))\n'
           f'  (lib_symbols\n' + "\n".join(lib_text) + '\n  )\n' + "\n".join(items) +
           f'\n  (sheet_instances (path "/" (page "1")))\n)\n')
    open(os.path.join(proj_dir, f"{design.PROJECT}.kicad_sch"), "w").write(sch)
    return root, sym_uuid
