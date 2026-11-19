"""Project-local symbol and footprint libraries (KiCad 7 format)."""
import os

FONT = "(effects (font (size 1.27 1.27)))"
HIDE = "(effects (font (size 1.27 1.27)) hide)"


def pin(kind, x, y, ang, length, name, num, hide=False):
    h = " hide" if hide else ""
    return (f'      (pin {kind} line (at {x} {y} {ang}) (length {length}){h}\n'
            f'        (name "{name}" {FONT})\n        (number "{num}" {FONT}))\n')


def sym(name, ref, value, fp, ds, graphics, pins, desc, ref_y=7.62, val_y=-7.62):
    return (f'  (symbol "{name}" (pin_names (offset 0.254)) (in_bom yes) (on_board yes)\n'
            f'    (property "Reference" "{ref}" (at 0 {ref_y} 0) {FONT})\n'
            f'    (property "Value" "{value}" (at 0 {val_y} 0) {FONT})\n'
            f'    (property "Footprint" "{fp}" (at 0 0 0) {HIDE})\n'
            f'    (property "Datasheet" "{ds}" (at 0 0 0) {HIDE})\n'
            f'    (property "ki_description" "{desc}" (at 0 0 0) {HIDE})\n'
            f'    (symbol "{name}_0_1"\n{graphics}    )\n'
            f'    (symbol "{name}_1_1"\n{"".join(pins)}    )\n  )\n')


def rect(x1, y1, x2, y2):
    return (f'      (rectangle (start {x1} {y1}) (end {x2} {y2}) (stroke (width 0.254) (type default)) '
            f'(fill (type background)))\n')


def poly(pts, fill="none", w=0.254):
    p = " ".join(f"(xy {x} {y})" for x, y in pts)
    return f'      (polyline (pts {p}) (stroke (width {w}) (type default)) (fill (type {fill})))\n'


def symbols():
    out = []
    # THS4631: op amp triangle
    g = poly([(-5.08, 5.08), (5.08, 0), (-5.08, -5.08), (-5.08, 5.08)], "background")
    pins = [pin("input", -7.62, 2.54, 0, 2.54, "+", 3), pin("input", -7.62, -2.54, 0, 2.54, "-", 2),
            pin("output", 7.62, 0, 180, 2.54, "~", 6),
            pin("power_in", -2.54, 7.62, 270, 3.81, "V+", 7), pin("power_in", -2.54, -7.62, 90, 3.81, "V-", 4),
            pin("no_connect", 0, 0, 0, 0, "NC", 1, True), pin("no_connect", 0, 0, 0, 0, "NC", 5, True),
            pin("no_connect", 0, 0, 0, 0, "NC", 8, True)]
    out.append(sym("THS4631", "U", "THS4631", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                   "https://www.ti.com/lit/gpn/ths4631", g, pins,
                   "210 MHz GBW FET-input op amp, +/-15 V, SOIC-8", 8.89, -8.89))
    # LDOs
    for name, desc, vin in (("TPS7A4901", "36 V 150 mA ultralow-noise positive LDO, MSOP-8 PowerPAD", "IN"),
                            ("TPS7A3001", "-36 V 200 mA ultralow-noise negative LDO, MSOP-8 PowerPAD", "IN")):
        g = rect(-7.62, 7.62, 7.62, -7.62)
        pins = [pin("power_in", -10.16, 5.08, 0, 2.54, "IN", 8), pin("input", -10.16, 0, 0, 2.54, "EN", 5),
                pin("passive", -10.16, -5.08, 0, 2.54, "NR/SS", 6),
                pin("power_out", 10.16, 5.08, 180, 2.54, "OUT", 1), pin("input", 10.16, 0, 180, 2.54, "FB", 2),
                pin("power_in", 0, -10.16, 90, 2.54, "GND", 4), pin("passive", 2.54, -10.16, 90, 2.54, "EP", 9),
                pin("no_connect", -2.54, 10.16, 270, 2.54, "NC", 3), pin("no_connect", 2.54, 10.16, 270, 2.54, "DNC", 7)]
        out.append(sym(name, "U", name, "Package_SO:MSOP-8-1EP_3x3mm_P0.65mm_EP1.68x1.88mm",
                       f"https://www.ti.com/lit/gpn/{name.lower()[:6]}", g, pins, desc, 8.89, -13.97))
    # TMR 3-0523 isolated module
    g = rect(-7.62, 7.62, 7.62, -7.62) + poly([(0, 7.62), (0, -7.62)], w=0.152)
    pins = [pin("power_in", -10.16, 5.08, 0, 2.54, "+Vin", 2), pin("power_in", -10.16, -5.08, 0, 2.54, "-Vin", 1),
            pin("input", -10.16, 0, 0, 2.54, "Remote", 3),
            pin("power_out", 10.16, 5.08, 180, 2.54, "+Vout", 6), pin("power_out", 10.16, 0, 180, 2.54, "COM", 7),
            pin("power_out", 10.16, -5.08, 180, 2.54, "-Vout", 8),
            pin("no_connect", 0, 10.16, 270, 2.54, "NC", 5, True)]
    out.append(sym("TMR3-0523", "PS", "TMR 3-0523", "hvprobe:Traco_TMR3_SIP8",
                   "https://www.tracopower.com/products/tmr3.pdf", g, pins,
                   "Traco TMR 3 isolated DC-DC, 4.5-9 V in, +/-15 V 100 mA out", 8.89, -8.89))
    # BAV199 series pair: pin1 A1, pin2 K2, pin3 K1/A2
    g = (poly([(-5.08, 0), (5.08, 0)], w=0.152) +
         poly([(-3.302, 1.27), (-3.302, -1.27), (-1.27, 0), (-3.302, 1.27)], "outline") +
         poly([(-1.27, 1.27), (-1.27, -1.27)]) +
         poly([(1.27, 1.27), (1.27, -1.27), (3.302, 0), (1.27, 1.27)], "outline") +
         poly([(3.302, 1.27), (3.302, -1.27)]))
    pins = [pin("passive", -7.62, 0, 0, 2.54, "A1", 1), pin("passive", 7.62, 0, 180, 2.54, "K2", 2),
            pin("passive", 0, -5.08, 90, 5.08, "K1A2", 3)]
    out.append(sym("BAV199", "D", "BAV199", "Package_TO_SOT_SMD:SOT-23", "https://www.nexperia.com/product/BAV199",
                   g, pins, "Low-leakage series diode pair, SOT-23", 3.81, 3.81 + 2.54))
    return ("(kicad_symbol_lib (version 20220914) (generator hvprobe_gen)\n" + "".join(out) + ")\n")


def fp_header(name, descr, smd=False):
    return (f'(footprint "{name}" (version 20221018) (generator hvprobe_gen)\n  (layer "F.Cu")\n'
            f'  (descr "{descr}")\n  (attr {"smd" if smd else "through_hole"})\n'
            f'  (fp_text reference "REF**" (at 0 -4) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))\n'
            f'  (fp_text value "{name}" (at 0 4) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))\n')


def rect_lines(x1, y1, x2, y2, layer, w):
    return "".join(f'  (fp_line (start {a} {b}) (end {c} {d}) (stroke (width {w}) (type solid)) (layer "{layer}"))\n'
                   for a, b, c, d in ((x1, y1, x2, y1), (x2, y1, x2, y2), (x2, y2, x1, y2), (x1, y2, x1, y1)))


def footprints(d):
    os.makedirs(d, exist_ok=True)
    # Traco TMR 3 SIP-8 (pins 1,2,3,5,6,7,8; pin 4 absent), 2.54 pitch, body 21.8 x 4.0
    s = fp_header("Traco_TMR3_SIP8", "Traco TMR 3 SIP-8, pin 4 omitted, 2.54 mm pitch")
    for n in (1, 2, 3, 5, 6, 7, 8):
        x = (n - 1) * 2.54
        shape = "rect" if n == 1 else "circle"
        s += f'  (pad "{n}" thru_hole {shape} (at {x:.2f} 0) (size 1.8 1.8) (drill 1.0) (layers "*.Cu" "*.Mask"))\n'
    x0, x1 = -2.44, 17.78 + 2.24
    s += rect_lines(x0, -2.0, x1, 2.0, "F.Fab", 0.1) + rect_lines(x0 - 0.12, -2.12, x1 + 0.12, 2.12, "F.SilkS", 0.12)
    s += rect_lines(x0 - 0.5, -2.5, x1 + 0.5, 2.5, "F.CrtYd", 0.05) + ")\n"
    open(os.path.join(d, "Traco_TMR3_SIP8.kicad_mod"), "w").write(s)
    # Bourns 2051 GDT, 4.5 x 3.2 mm body, end terminations. VERIFY pads against Bourns land pattern.
    s = fp_header("GDT_Bourns_2051", "Bourns 2051 SMD GDT 4.5x3.2 mm. Pads estimated - verify against datasheet", True)
    for n, x in (("1", -2.1), ("3", 2.1)):
        s += f'  (pad "{n}" smd roundrect (at {x} 0) (size 1.4 3.4) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.15))\n'
    s += rect_lines(-2.25, -1.6, 2.25, 1.6, "F.Fab", 0.1) + rect_lines(-3.1, -2.0, 3.1, 2.0, "F.CrtYd", 0.05)
    s += '  (fp_line (start -1.2 -1.85) (end 1.2 -1.85) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))\n'
    s += '  (fp_line (start -1.2 1.85) (end 1.2 1.85) (stroke (width 0.12) (type solid)) (layer "F.SilkS"))\n)\n'
    open(os.path.join(d, "GDT_Bourns_2051.kicad_mod"), "w").write(s)
    # Coax solder pads for captive RG-174 / RG-58 output cable, with cable-tie holes
    s = fp_header("Coax_Solder_Pads", "Solder pads for captive coax: centre pad 1, braid pad 2, two tie-wrap holes")
    s += '  (pad "1" thru_hole circle (at 0 0) (size 2.2 2.2) (drill 1.1) (layers "*.Cu" "*.Mask"))\n'
    s += '  (pad "2" thru_hole oval (at 4.5 0) (size 3.2 4.5) (drill oval 1.6 2.8) (layers "*.Cu" "*.Mask"))\n'
    for y in (-3.5, 3.5):
        s += f'  (pad "" np_thru_hole circle (at 9.5 {y}) (size 2.2 2.2) (drill 2.2) (layers "*.Cu" "*.Mask"))\n'
    s += rect_lines(-1.6, -5.0, 11.2, 5.0, "F.CrtYd", 0.05)
    s += '  (fp_text user "OUT" (at 2.2 -3.4) (layer "F.SilkS") (effects (font (size 0.8 0.8) (thickness 0.12))))\n)\n'
    open(os.path.join(d, "Coax_Solder_Pads.kicad_mod"), "w").write(s)


def write(proj_dir):
    open(os.path.join(proj_dir, "hvprobe.kicad_sym"), "w").write(symbols())
    footprints(os.path.join(proj_dir, "hvprobe.pretty"))
    open(os.path.join(proj_dir, "sym-lib-table"), "w").write(
        '(sym_lib_table\n  (lib (name "hvprobe")(type "KiCad")(uri "${KIPRJMOD}/hvprobe.kicad_sym")(options "")(descr "HV probe parts"))\n)\n')
    open(os.path.join(proj_dir, "fp-lib-table"), "w").write(
        '(fp_lib_table\n  (lib (name "hvprobe")(type "KiCad")(uri "${KIPRJMOD}/hvprobe.pretty")(options "")(descr "HV probe footprints"))\n)\n')
