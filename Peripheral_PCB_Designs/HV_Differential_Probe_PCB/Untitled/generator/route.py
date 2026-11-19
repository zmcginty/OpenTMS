"""Autoroute with Freerouting and import the session (SES) into the board via the pcbnew API."""
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcbnew  # noqa: E402

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PCB = os.path.join(PROJ, "hvprobe.kicad_pcb")


def tokenize(s):
    return re.findall(r'\(|\)|"[^"]*"|[^\s()]+', s)


def parse(tokens):
    stack, cur = [], []
    for t in tokens:
        if t == "(":
            stack.append(cur); cur = []
        elif t == ")":
            done = cur; cur = stack.pop(); cur.append(done)
        else:
            cur.append(t.strip('"'))
    return cur[0]


def find(node, key):
    for c in node:
        if isinstance(c, list) and c and c[0] == key:
            yield c


def import_ses(board, ses_path):
    tree = parse(tokenize(open(ses_path).read()))
    routes = next(find(tree, "routes"))
    res = next(find(routes, "resolution"))
    scale = {"um": 1e-3, "mil": 0.0254, "mm": 1.0}[res[1]] / float(res[2])   # -> mm
    netout = next(find(routes, "network_out"))
    ses_nets = {n[1] for n in find(netout, "net")}
    removed = 0
    for t in list(board.GetTracks()):
        if t.GetNetname() in ses_nets and not t.IsLocked():
            board.Remove(t); removed += 1
    print(f"SES nets: {len(ses_nets)}; replaced {removed} existing tracks/vias on those nets")
    nt, nv = 0, 0
    for net in find(netout, "net"):
        name = net[1]
        ni = board.FindNet(name)
        if ni is None:
            print("unknown net", name); continue
        for w in find(net, "wire"):
            path = next(find(w, "path"))
            layer = board.GetLayerID(path[1])
            width = float(path[2]) * scale
            xy = [float(v) * scale for v in path[3:]]
            pts = [(xy[i], -xy[i + 1]) for i in range(0, len(xy), 2)]
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                t = pcbnew.PCB_TRACK(board)
                t.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(x1), pcbnew.FromMM(y1)))
                t.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(x2), pcbnew.FromMM(y2)))
                t.SetWidth(pcbnew.FromMM(width)); t.SetLayer(layer); t.SetNet(ni)
                board.Add(t); nt += 1
        for v in find(net, "via"):
            m = re.search(r"_(\d+):(\d+)_um", v[1])
            dia, drill = (int(m.group(1)) / 1000, int(m.group(2)) / 1000) if m else (0.6, 0.3)
            x, y = float(v[2]) * scale, -float(v[3]) * scale
            via = pcbnew.PCB_VIA(board)
            via.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
            via.SetWidth(pcbnew.FromMM(dia)); via.SetDrill(pcbnew.FromMM(drill))
            via.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu); via.SetNet(ni)
            board.Add(via); nv += 1
    return nt, nv


def run(passes=60):
    b = pcbnew.LoadBoard(PCB)
    pcbnew.ExportSpecctraDSN(b, "/tmp/hvprobe.dsn")
    if os.path.exists("/tmp/hvprobe.ses"):
        os.remove("/tmp/hvprobe.ses")
    subprocess.run(["xvfb-run", "-a", "java", "-jar", "/opt/fr/freerouting.jar", "-de", "/tmp/hvprobe.dsn",
                    "-do", "/tmp/hvprobe.ses", "-mp", str(passes)], stdout=open("/tmp/fr.log", "w"),
                   stderr=subprocess.STDOUT, timeout=1200)
    b = pcbnew.LoadBoard(PCB)
    nt, nv = import_ses(b, "/tmp/hvprobe.ses")
    for t in list(b.GetTracks()):
        if t.GetClass() == "PCB_TRACK" and t.GetLength() < pcbnew.FromMM(0.01):
            b.Remove(t)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.SaveBoard(PCB, b)
    print(f"imported {nt} track segments, {nv} vias")


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 60)


def stitch_islands(path=PCB, rounds=3):
    """Add a GND via inside every F.Cu/B.Cu GND fill island that has no via, where a 0.6 mm via fits in both fills."""
    added = 0
    for _ in range(rounds):
        b = pcbnew.LoadBoard(path)
        filler = pcbnew.ZONE_FILLER(b); filler.Fill(b.Zones())
        gnd_vias = [v.GetPosition() for v in b.GetTracks() if v.GetClass() == "PCB_VIA" and v.GetNetname() == "GND"]
        gnd_pads_th = [p.GetPosition() for p in b.GetPads() if p.GetNetname() == "GND" and p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH]
        anchors = gnd_vias + gnd_pads_th
        zones = {z.GetLayer(): z for z in b.Zones() if z.GetNetname() == "GND"}
        fpolys = {l: z.GetFilledPolysList(l) for l, z in zones.items()}
        new = 0
        for layer, other in ((pcbnew.F_Cu, pcbnew.B_Cu), (pcbnew.B_Cu, pcbnew.F_Cu)):
            ps = fpolys[layer]
            for i in range(ps.OutlineCount()):
                sub = pcbnew.SHAPE_POLY_SET(); sub.AddOutline(ps.Outline(i))
                if any(sub.Contains(a) for a in anchors):
                    continue
                bb = ps.Outline(i).BBox()
                step = pcbnew.FromMM(0.25); r = pcbnew.FromMM(0.55)
                found = None
                y = bb.GetTop()
                while y <= bb.GetBottom() and not found:
                    x = bb.GetLeft()
                    while x <= bb.GetRight():
                        ring = [pcbnew.VECTOR2I(x + int(r * dx), y + int(r * dy)) for dx, dy in
                                ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1), (.7, .7), (-.7, .7), (.7, -.7), (-.7, -.7))]
                        if all(sub.Contains(p) for p in ring) and all(fpolys[other].Contains(p) for p in ring):
                            found = pcbnew.VECTOR2I(x, y); break
                        x += step
                    y += step
                if found:
                    v = pcbnew.PCB_VIA(b); v.SetPosition(found)
                    v.SetWidth(pcbnew.FromMM(0.6)); v.SetDrill(pcbnew.FromMM(0.3))
                    v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu); v.SetNet(b.FindNet("GND"))
                    b.Add(v); anchors.append(found); new += 1
        if not new:
            break
        pcbnew.ZONE_FILLER(b).Fill(b.Zones())
        pcbnew.SaveBoard(path, b)
        added += new
    print("island stitching vias added:", added)
