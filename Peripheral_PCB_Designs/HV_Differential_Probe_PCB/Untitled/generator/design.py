"""Single source of truth for the HV differential probe: parts, nets, placement.

Coordinates:
  sch = schematic position in mm (A3 sheet)
  pcb = (x, y, rotation_deg) in board mm, board origin = top-left corner of the outline.
"""
from dataclasses import dataclass, field

PROJECT = "hvprobe"


@dataclass
class Part:
    ref: str
    lib_id: str            # schematic symbol "Lib:Name"
    value: str
    footprint: str         # "Lib:Name"
    pins: dict             # pin number -> net name (None = no connect)
    sch: tuple
    pcb: tuple
    mpn: str = ""
    dnp: bool = False
    note: str = ""
    fields: dict = field(default_factory=dict)


FP = {
    "R0603": "Resistor_SMD:R_0603_1608Metric",
    "R0805": "Resistor_SMD:R_0805_2012Metric",
    "R2512": "Resistor_SMD:R_2512_6332Metric",
    "C0603": "Capacitor_SMD:C_0603_1608Metric",
    "C0805": "Capacitor_SMD:C_0805_2012Metric",
    "C1206": "Capacitor_SMD:C_1206_3216Metric",
    "FB0805": "Inductor_SMD:L_0805_2012Metric",
    "LED0805": "LED_SMD:LED_0805_2012Metric",
    "SMA": "Diode_SMD:D_SMA",
    "SOD123": "Diode_SMD:D_SOD-123",
    "SOT23": "Package_TO_SOT_SMD:SOT-23",
    "SOIC8": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
    "MSOP8EP": "Package_SO:MSOP-8-1EP_3x3mm_P0.65mm_EP1.68x1.88mm",
    "POT3296W": "Potentiometer_THT:Potentiometer_Bourns_3296W_Vertical",
    "TRIMCAP": "Capacitor_SMD:C_Trimmer_Murata_TZB4-A",
    "SW": "Button_Switch_SMD:SW_SPDT_PCM12",
    "USBC": "Connector_USB:USB_C_Receptacle_GCT_USB4125-xx-x_6P_TopMnt_Horizontal",
    "JST2": "Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical",
    "HOLE": "MountingHole:MountingHole_3.2mm_M3",
    "HVPAD": "TestPoint:TestPoint_THTPad_D4.0mm_Drill2.0mm",
    "TMR": "hvprobe:Traco_TMR3_SIP8",
    "GDT": "hvprobe:GDT_Bourns_2051",
    "COAX": "hvprobe:Coax_Solder_Pads",
}

PARTS: list = []


def add(*a, **k):
    PARTS.append(Part(*a, **k))


# --------------------------------------------------------------------------- divider legs
def leg(side, base, y_pcb, y_sch, mirror):
    """side 'P' or 'N'. base = 100 or 200. mirror flips y offsets for the N leg."""
    s = 1 if not mirror else -1
    hv = lambda k: f"HV_{side}{k}"
    bot, trim, bin_, buf = f"BOT_{side}", f"TRIM_{side}", f"BIN_{side}", f"BUF_{side}"
    jref = "J1" if side == "P" else "J2"
    add(jref, "Connector_Generic:Conn_01x01", f"HV_IN_{side}", FP["HVPAD"], {"1": f"HV_IN{side}"},
        (20, y_sch), (5, y_pcb, 0), note="HV lead solder pad, 10 kV silicone wire")
    add(f"R{base+1}", "Device:R", "330", FP["R2512"], {"1": f"HV_IN{side}", "2": hv(0)},
        (35, y_sch), (14, y_pcb, 0), mpn="1 W thick film 2512", note="Tip damping resistor")
    for k in range(10):
        nxt = hv(k + 1) if k < 9 else bot
        add(f"R{base+2+k}", "Device:R", "10M", FP["R2512"], {"1": hv(k), "2": nxt},
            (50 + 15 * k, y_sch), (26 + 10 * k, y_pcb, 0), mpn="Vishay CRHV2512AF10M0FKE5")
        add(f"C{base+1+k}", "Device:C", "22pF 2kV", FP["C1206"], {"1": hv(k), "2": nxt},
            (50 + 15 * k, y_sch + 22), (26 + 10 * k, y_pcb - 5.5 * s, 0), mpn="KEMET C1206C220JGGACTU")
    # bottom of divider
    yb = y_pcb
    add(f"R{base+12}", "Device:R", "97.6k 0.1%", FP["R0805"], {"1": bot, "2": trim},
        (200, y_sch - 15), (126, yb - 5.5 * s, 0), mpn="0.1 % 25 ppm thin film")
    add(f"RV{base+1}", "Device:R_Potentiometer_Trim", "5k", FP["POT3296W"], {"1": trim, "2": "GND", "3": "GND"},
        (215, y_sch - 15), (137, yb - 5.5 * s, 0), mpn="Bourns 3296W-1-502LF",
        note="Leg P: gain cal. Leg N: DC CMRR null")
    add(f"C{base+11}", "Device:C", "2.2nF C0G 1%", FP["C1206"], {"1": bot, "2": "GND"},
        (200, y_sch + 5), (124, yb + 5 * s, 90), mpn="C0G 1 % 50 V 1206")
    add(f"C{base+12}", "Device:C", "SOT 0-150pF", FP["C0805"], {"1": bot, "2": "GND"},
        (213, y_sch + 5), (128, yb + 5 * s, 90), dnp=True, note="Select on test")
    add(f"C{base+13}", "Device:C_Trim", "5-30pF", FP["TRIMCAP"], {"1": bot, "2": "GND"},
        (226, y_sch + 5), (133.5, yb + 5.5 * s, 90), mpn="Murata TZB4Z300AB10R00", note="AC flatness / CMRR trim")
    add(f"E{base+1}", "Device:GDT_2Pin", "90V GDT", FP["GDT"], {"1": bot, "3": "GND"},
        (239, y_sch + 5), (139, yb + 5 * s, 90), mpn="Bourns 2051-09-SM-RPLF")
    add(f"R{base+13}", "Device:R", "100", FP["R0603"], {"1": bot, "2": bin_},
        (252, y_sch), (144, yb, 0))
    add(f"D{base+1}", "hvprobe:BAV199", "BAV199", FP["SOT23"], {"1": "-12V", "2": "+12V", "3": bin_},
        (268, y_sch + 12), (148, yb + 4 * s, 0 if not mirror else 180), mpn="Nexperia BAV199")
    uref = "U1" if side == "P" else "U2"
    add(uref, "hvprobe:THS4631", "THS4631D", FP["SOIC8"],
        {"1": None, "2": buf, "3": bin_, "4": "-12V", "5": None, "6": buf, "7": "+12V", "8": None},
        (300, y_sch), (156, yb, 0), mpn="TI THS4631DR")
    add(f"C{base+14}", "Device:C", "100nF", FP["C0603"], {"1": "+12V", "2": "GND"},
        (318, y_sch - 15), (153, yb - 5 * s, 0))
    add(f"C{base+15}", "Device:C", "100nF", FP["C0603"], {"1": "-12V", "2": "GND"},
        (331, y_sch - 15), (159, yb - 5 * s, 0))


leg("P", 100, 10.0, 45, False)
leg("N", 200, 34.0, 115, True)

# --------------------------------------------------------------------------- difference amp + output
add("R301", "Device:R", "1.00k 0.1%", FP["R0805"], {"1": "BUF_P", "2": "AP"}, (290, 175), (164, 18.5, 90),
    mpn="0.1 % 10 ppm thin film; match all four")
add("R302", "Device:R", "1.00k 0.1%", FP["R0805"], {"1": "AP", "2": "GND"}, (302, 175), (167, 16, 0))
add("R303", "Device:R", "1.00k 0.1%", FP["R0805"], {"1": "BUF_N", "2": "AN"}, (314, 175), (164, 25.5, 90))
add("R304", "Device:R", "1.00k 0.1%", FP["R0805"], {"1": "AN", "2": "AOUT"}, (326, 175), (171, 28.5, 0))
add("R305", "Device:R", "1M", FP["R0603"], {"1": "OFFS", "2": "AP"}, (338, 175), (171, 12, 0))
add("RV301", "Device:R_Potentiometer_Trim", "10k", FP["POT3296W"], {"1": "+12V", "2": "OFFS", "3": "-12V"},
    (352, 155), (180, 6, 0), mpn="Bourns 3296W-1-103LF", note="Output offset zero")
add("U3", "hvprobe:THS4631", "THS4631D", FP["SOIC8"],
    {"1": None, "2": "AN", "3": "AP", "4": "-12V", "5": None, "6": "AOUT", "7": "+12V", "8": None},
    (352, 192), (172, 22, 0), mpn="TI THS4631DR")
add("C301", "Device:C", "100nF", FP["C0603"], {"1": "+12V", "2": "GND"}, (367, 155), (176, 17, 0))
add("C302", "Device:C", "100nF", FP["C0603"], {"1": "-12V", "2": "GND"}, (380, 155), (176, 27, 0))
add("R306", "Device:R", "49.9", FP["R0603"], {"1": "AOUT", "2": "OUT"}, (372, 192), (177.5, 24.5, 90))
add("J3", "Connector:Conn_Coaxial", "OUT", FP["COAX"], {"1": "OUT", "2": "GND"}, (390, 192), (184.5, 24, 270),
    note="Captive RG-174/RG-58 to BNC, 1000:1 into 1 MOhm")
add("D401", "Device:D_TVS", "SMAJ15CA", FP["SMA"], {"1": "+12V", "2": "GND"}, (350, 212), (167, 4, 0))
add("D402", "Device:D_TVS", "SMAJ15CA", FP["SMA"], {"1": "-12V", "2": "GND"}, (375, 212), (167, 40, 0))

# --------------------------------------------------------------------------- power
add("J4", "Connector_Generic:Conn_01x02", "BATT 4.5-9V", FP["JST2"], {"1": "VBAT", "2": "VIN_GND"},
    (25, 205), (214.5, 37.5, 0), note="2S Li-ion or 4xAA")
add("J5", "Connector:USB_C_Receptacle_PowerOnly_6P", "USB-C 5V", FP["USBC"],
    {"A9": "VBUS", "B9": "VBUS", "A12": "VIN_GND", "B12": "VIN_GND", "A5": "CC1", "B5": "CC2", "S1": "VIN_GND"},
    (30, 240), (228.5, 22, 90))
add("R401", "Device:R", "5.1k", FP["R0603"], {"1": "CC1", "2": "VIN_GND"}, (60, 238), (223.5, 15, 90))
add("R402", "Device:R", "5.1k", FP["R0603"], {"1": "CC2", "2": "VIN_GND"}, (70, 238), (225.5, 15, 90))
add("D403", "Device:D_Schottky", "B5819W", FP["SOD123"], {"1": "VIN_OR", "2": "VBAT"}, (60, 205), (220, 31.5, 0))
add("D404", "Device:D_Schottky", "B5819W", FP["SOD123"], {"1": "VIN_OR", "2": "VBUS"}, (60, 220), (220, 27, 0))
add("SW1", "Switch:SW_SPDT", "POWER", FP["SW"], {"1": "VIN_SW", "2": "VIN_OR", "3": None}, (90, 208), (222, 4, 0))
add("C401", "Device:C", "10uF 25V", FP["C1206"], {"1": "VIN_SW", "2": "VIN_GND"}, (110, 220), (214, 12, 90))
add("PS1", "hvprobe:TMR3-0523", "TMR 3-0523", FP["TMR"],
    {"1": "VIN_GND", "2": "VIN_SW", "3": None, "5": None, "6": "+15V", "7": "GND", "8": "-15V"},
    (140, 215), (218, 22, 180), mpn="Traco TMR 3-0523", note="Isolated 4.5-9 V in, +/-15 V out")
add("C402", "Device:C", "1uF 25V", FP["C0805"], {"1": "+15V", "2": "GND"}, (170, 200), (204, 16.5, 90))
add("C403", "Device:C", "1uF 25V", FP["C0805"], {"1": "-15V", "2": "GND"}, (170, 232), (200, 27.5, 90))
add("FB401", "Device:FerriteBead_Small", "600R@100MHz", FP["FB0805"], {"1": "+15V", "2": "+15F"}, (185, 205), (205, 3.5, 0))
add("FB402", "Device:FerriteBead_Small", "600R@100MHz", FP["FB0805"], {"1": "-15V", "2": "-15F"}, (185, 240), (205, 40.5, 0))
add("C404", "Device:C", "10uF 25V", FP["C1206"], {"1": "+15F", "2": "GND"}, (200, 205), (205, 8, 0))
add("C405", "Device:C", "10uF 25V", FP["C1206"], {"1": "-15F", "2": "GND"}, (200, 240), (205, 36, 0))
add("U4", "hvprobe:TPS7A4901", "TPS7A4901DGNR", FP["MSOP8EP"],
    {"1": "+12V", "2": "FB_POS", "3": None, "4": "GND", "5": "+15F", "6": "NR_POS", "7": None, "8": "+15F", "9": "GND"},
    (235, 205), (197, 7, 0), mpn="TI TPS7A4901DGNR")
add("R403", "Device:R", "90.9k", FP["R0603"], {"1": "+12V", "2": "FB_POS"}, (262, 198), (192.5, 4.5, 0))
add("R404", "Device:R", "10k", FP["R0603"], {"1": "FB_POS", "2": "GND"}, (275, 207), (192.5, 9.5, 0))
add("C406", "Device:C", "10nF", FP["C0603"], {"1": "+12V", "2": "FB_POS"}, (288, 198), (192.5, 2, 0))
add("C407", "Device:C", "10nF", FP["C0603"], {"1": "NR_POS", "2": "GND"}, (208, 225), (201, 12.5, 90))
add("C408", "Device:C", "10uF 25V", FP["C1206"], {"1": "+12V", "2": "GND"}, (300, 205), (188, 8, 90))
add("U5", "hvprobe:TPS7A3001", "TPS7A3001DGNR", FP["MSOP8EP"],
    {"1": "-12V", "2": "FB_NEG", "3": None, "4": "GND", "5": "-15F", "6": "NR_NEG", "7": None, "8": "-15F", "9": "GND"},
    (235, 240), (197, 37, 0), mpn="TI TPS7A3001DGNR")
add("R405", "Device:R", "90.9k", FP["R0603"], {"1": "-12V", "2": "FB_NEG"}, (262, 233), (192.5, 39.5, 0))
add("R406", "Device:R", "10k", FP["R0603"], {"1": "FB_NEG", "2": "GND"}, (275, 242), (192.5, 34.5, 0))
add("C409", "Device:C", "10nF", FP["C0603"], {"1": "-12V", "2": "FB_NEG"}, (288, 233), (192.5, 42, 0))
add("C410", "Device:C", "10nF", FP["C0603"], {"1": "NR_NEG", "2": "GND"}, (208, 262), (201, 31.5, 90))
add("C411", "Device:C", "10uF 25V", FP["C1206"], {"1": "-12V", "2": "GND"}, (250, 262), (188.5, 39, 90))
add("R407", "Device:R", "4.7k", FP["R0603"], {"1": "+12V", "2": "LED_A"}, (320, 205), (190.4, 24.5, 90))
add("D405", "Device:LED", "GRN", FP["LED0805"], {"1": "GND", "2": "LED_A"}, (335, 225), (190.4, 19.5, 90))
for i, xy in enumerate([(129, 22), (182, 40), (149, 22)]):
    add(f"H{i+1}", "Mechanical:MountingHole", "M3", FP["HOLE"], {}, (230 + 10 * i, 175), (xy[0], xy[1], 0))


# --------------------------------------------------------------------------- derived
def nets():
    n = {}
    for p in PARTS:
        for pin, net in p.pins.items():
            if net:
                n.setdefault(net, []).append((p.ref, pin))
    return n


HV_NETS = [n for n in nets() if n.startswith("HV_")]
POWER_NETS = ["GND", "+12V", "-12V", "+15V", "-15V", "+15F", "-15F", "VIN_SW", "VIN_OR", "VBAT", "VBUS", "VIN_GND"]
BOARD = dict(w=232.0, h=44.0, slot=(3.0, 120.0, 20.5, 23.5))

if __name__ == "__main__":
    nn = nets()
    print(len(PARTS), "parts,", len(nn), "nets")
    single = [k for k, v in nn.items() if len(v) < 2]
    print("single-pin nets:", single)
