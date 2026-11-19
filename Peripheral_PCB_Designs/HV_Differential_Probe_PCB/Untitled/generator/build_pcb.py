import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcbnew  # noqa: E402
import design, make_pcb  # noqa: E402

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build():
    u = json.load(open(os.path.join(PROJ, ".sch_uuids.json")))
    path = make_pcb.build(PROJ, u["sym"])
    make_pcb.write_project(PROJ)
    return path


def drc(path, out="/tmp/drc.rpt"):
    b = pcbnew.LoadBoard(path)
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    pcbnew.WriteDRCReport(b, out, pcbnew.EDA_UNITS_MILLIMETRES, True)
    txt = open(out).read()
    kinds = re.findall(r"^\[(\w+)\]", txt, re.M)
    from collections import Counter
    return txt, Counter(kinds)


def report(path):
    txt, c = drc(path)
    print("DRC:", dict(c))
    unc = re.search(r"\*\* Found (\d+) unconnected pads", txt)
    print("unconnected:", unc.group(1) if unc else "?")
    for k in ("courtyards_overlap", "clearance", "copper_edge_clearance", "hole_clearance", "silk_overlap"):
        for m in list(re.finditer(r"^\[" + k + r"\].*?(?=^\[|\Z)", txt, re.M | re.S))[:6]:
            print(m.group(0).strip().replace("\n", " | ")[:260])
    return txt, c


if __name__ == "__main__":
    report(build())
