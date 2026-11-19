"""Build hvprobe.kicad_pcb with the KiCad 7 pcbnew API from design.PARTS."""
import json
import os

import pcbnew

import design

KFP = "/usr/share/kicad/footprints"
OX, OY = 40.0, 60.0          # board origin on the page (mm)
mm = pcbnew.FromMM


def P(x, y):
    return pcbnew.VECTOR2I(mm(OX + x), mm(OY + y))


def fp_path(lib, proj):
    return os.path.join(proj, "hvprobe.pretty") if lib == "hvprobe" else os.path.join(KFP, lib + ".pretty")


def seg(board, a, b, layer, width=0.12):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetStart(a); s.SetEnd(b); s.SetLayer(layer); s.SetWidth(mm(width))
    board.Add(s)


def rect_edge(board, x1, y1, x2, y2):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_RECT)
    s.SetStart(P(x1, y1)); s.SetEnd(P(x2, y2)); s.SetLayer(pcbnew.Edge_Cuts); s.SetWidth(mm(0.1))
    board.Add(s)


def text(board, t, x, y, size=1.2, layer=pcbnew.F_SilkS, bold=False):
    tx = pcbnew.PCB_TEXT(board)
    tx.SetText(t); tx.SetPosition(P(x, y)); tx.SetLayer(layer)
    tx.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size))); tx.SetTextThickness(mm(size * (0.2 if bold else 0.15)))
    board.Add(tx)


def track(board, a, b, net, width, layer=pcbnew.F_Cu):
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(a); t.SetEnd(b); t.SetWidth(mm(width)); t.SetLayer(layer); t.SetNet(net); t.SetLocked(True)
    board.Add(t)


def zone(board, net, layer, pts, prio=0, clearance=0.2):
    z = pcbnew.ZONE(board)
    z.SetLayer(layer); z.SetNet(net)
    ol = z.Outline(); ol.NewOutline()
    for x, y in pts:
        ol.Append(mm(OX + x), mm(OY + y))
    z.SetLocalClearance(mm(clearance)); z.SetMinThickness(mm(0.25))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THT_THERMAL)
    z.SetThermalReliefGap(mm(0.3)); z.SetThermalReliefSpokeWidth(mm(0.4))
    z.SetAssignedPriority(prio)
    z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    board.Add(z)


def build(proj, sym_uuid):
    path = os.path.join(proj, "hvprobe.kicad_pcb")
    board = pcbnew.NewBoard(path)
    ds = board.GetDesignSettings()
    ds.SetCopperLayerCount(2)
    ds.m_TrackMinWidth = mm(0.2); ds.m_MinClearance = mm(0.2); ds.m_HoleToHoleMin = mm(0.25)
    ds.m_CopperEdgeClearance = mm(0.5)

    netmap = {}
    for name in sorted(design.nets()):
        ni = pcbnew.NETINFO_ITEM(board, name)
        board.Add(ni)
        netmap[name] = ni

    fps = {}
    for p in design.PARTS:
        lib, name = p.footprint.split(":")
        fp = pcbnew.FootprintLoad(fp_path(lib, proj), name)
        if fp is None:
            raise RuntimeError(p.footprint)
        fp.SetFPID(pcbnew.LIB_ID(lib, name))
        fp.SetReference(p.ref); fp.SetValue(p.value)
        x, y, rot = p.pcb
        fp.SetPosition(P(x, y)); fp.SetOrientationDegrees(rot)
        if p.ref in sym_uuid:
            fp.SetPath(pcbnew.KIID_PATH("/" + sym_uuid[p.ref]))
        for pad in fp.Pads():
            net = p.pins.get(pad.GetNumber()) if pad.GetNumber() else None
            if net:
                pad.SetNet(netmap[net])
        fp.Reference().SetTextSize(pcbnew.VECTOR2I(mm(0.8), mm(0.8)))
        fp.Reference().SetTextThickness(mm(0.12))
        board.Add(fp)
        fps[p.ref] = fp

    B = design.BOARD
    rect_edge(board, 0, 0, B["w"], B["h"])
    sx1, sx2, sy1, sy2 = B["slot"]
    rect_edge(board, sx1, sy1, sx2, sy2)

    def pad(ref, num):
        return next(pd for pd in fps[ref].Pads() if pd.GetNumber() == num).GetPosition()

    # ---- HV ladders, routed by hand and locked
    for side, base in (("P", 100), ("N", 200)):
        jref = "J1" if side == "P" else "J2"
        W = 0.6
        track(board, pad(jref, "1"), pad(f"R{base+1}", "1"), netmap[f"HV_IN{side}"], W)
        R = [f"R{base+2+k}" for k in range(10)]
        C = [f"C{base+1+k}" for k in range(10)]
        # node 0
        n0 = netmap[f"HV_{side}0"]
        a, b = pad(f"R{base+1}", "2"), pad(R[0], "1")
        track(board, a, b, n0, W)
        c0 = pad(C[0], "1")
        corner = pcbnew.VECTOR2I(b.x, c0.y)
        track(board, b, corner, n0, W); track(board, corner, c0, n0, W)
        for j in range(1, 10):          # BOT node (j=10) is low-voltage: left to the autorouter
            net = netmap[f"HV_{side}{j}"]
            r_prev, c_prev = pad(R[j - 1], "2"), pad(C[j - 1], "2")
            corner = pcbnew.VECTOR2I(r_prev.x, c_prev.y)
            track(board, r_prev, corner, net, W); track(board, c_prev, corner, net, W)
            track(board, r_prev, pad(R[j], "1"), net, W)
            track(board, c_prev, pad(C[j], "1"), net, W)

    # ---- zones (LV section only; nothing under the HV strings)
    gnd, vgnd = netmap["GND"], netmap["VIN_GND"]
    split_x = 210.4                      # between TMR pin 3 and pin 5
    gnd_poly = [(121.5, 0.3), (split_x - 1.0, 0.3), (split_x - 1.0, 43.7), (121.5, 43.7)]
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        zone(board, gnd, layer, gnd_poly)
    zone(board, vgnd, pcbnew.B_Cu, [(split_x + 1.0, 0.3), (231.7, 0.3), (231.7, 43.7), (split_x + 1.0, 43.7)])

    # ---- GND stitching vias on a grid, avoiding footprints
    boxes = []
    for f in board.GetFootprints():
        bb = f.GetBoundingBox(False, False)
        boxes.append((pcbnew.ToMM(bb.GetLeft()) - OX - 1.0, pcbnew.ToMM(bb.GetTop()) - OY - 1.0,
                      pcbnew.ToMM(bb.GetRight()) - OX + 1.0, pcbnew.ToMM(bb.GetBottom()) - OY + 1.0))
    nvia = 0
    for gx in [123 + 4 * i for i in range(22)]:
        for gy in [2.0 + 4 * j for j in range(11)]:
            if gx > split_x - 2.5:
                continue
            if any(x1 <= gx <= x2 and y1 <= gy <= y2 for x1, y1, x2, y2 in boxes):
                continue
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(P(gx, gy)); v.SetWidth(mm(0.6)); v.SetDrill(mm(0.3))
            v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu); v.SetNet(gnd); v.SetLocked(True)
            board.Add(v); nvia += 1
    print("stitching vias:", nvia)

    # ---- silkscreen
    text(board, "HV IN +", 5, 4.2, 1.2, bold=True)
    text(board, "HV IN -", 5, 39.8, 1.2, bold=True)
    text(board, "DANGER 7 kV  -  HV SECTION", 62, 22.0 - 3.6, 1.6, bold=True)
    text(board, "keep coated, no metal within 20 mm", 62, 22.0 + 3.6, 1.0)
    text(board, "GAIN", 137, 1.2, 0.8)
    text(board, "CMRR DC", 137, 42.8, 0.8)
    text(board, "FLAT +", 133.5, 18.6, 0.8)
    text(board, "CMRR AC", 133.5, 25.6, 0.8)
    text(board, "OFFSET", 180, 1.2, 0.8)
    text(board, "ISO", 210.4, 26.5, 0.8)
    text(board, "HV diff probe v1  1000:1", 160, 43.0, 0.8)

    pcbnew.SaveBoard(path, board)
    return path


def write_project(proj):
    pro = os.path.join(proj, "hvprobe.kicad_pro")
    d = json.load(open(pro)) if os.path.exists(pro) else {}
    d.setdefault("meta", {"filename": "hvprobe.kicad_pro", "version": 1})
    ns = d.setdefault("net_settings", {})
    base = {"bus_width": 12, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25, "diff_pair_width": 0.2,
            "line_style": 0, "microvia_diameter": 0.3, "microvia_drill": 0.1, "pcb_color": "rgba(0, 0, 0, 0.000)",
            "schematic_color": "rgba(0, 0, 0, 0.000)", "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6}
    ns["classes"] = [dict(base, name="Default", clearance=0.2, track_width=0.25),
                     dict(base, name="HV", clearance=1.5, track_width=0.6),
                     dict(base, name="Power", clearance=0.2, track_width=0.3, via_diameter=0.6, via_drill=0.3)]
    ns["meta"] = {"version": 3}
    ns["net_colors"] = None
    ns["netclass_assignments"] = None
    ns["netclass_patterns"] = ([{"netclass": "HV", "pattern": "HV_*"}] +
                               [{"netclass": "Power", "pattern": n} for n in design.POWER_NETS])
    d.setdefault("pcbnew", {}).setdefault("last_paths", {})
    d.setdefault("schematic", {}).setdefault("legacy_lib_dir", "")
    d.setdefault("sheets", [])
    json.dump(d, open(pro, "w"), indent=2)
