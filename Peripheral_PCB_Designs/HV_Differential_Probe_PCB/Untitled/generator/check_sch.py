"""Export the schematic netlist with kicad-cli and compare connectivity against design.PARTS."""
import re
import subprocess
import sys

import design


def sch_nets(sch, out="/tmp/hvprobe_net.net"):
    r = subprocess.run(["kicad-cli", "sch", "export", "netlist", "--format", "kicadsexpr", "-o", out, sch],
                       capture_output=True, text=True)
    if r.returncode:
        print(r.stdout, r.stderr)
        sys.exit(1)
    txt = open(out).read()
    nets = {}
    for m in re.finditer(r'\(net \(code "?\d+"?\) \(name "([^"]*)"\)(.*?)\)\s*(?=\(net |\)\s*\)\s*$)', txt, re.S):
        name, body = m.group(1), m.group(2)
        nodes = frozenset(re.findall(r'\(node \(ref "([^"]+)"\) \(pin "([^"]+)"\)', body))
        nets[name] = nodes
    return nets


def compare(sch):
    got = sch_nets(sch)
    want = {k: frozenset(v) for k, v in design.nets().items()}
    got_sets = {v for v in got.values() if len(v) > 1 or not any(r.startswith("#") for r, _ in v)}
    got_sets = {frozenset((r, p) for r, p in s if not r.startswith("#")) for s in got_sets}
    got_sets = {s for s in got_sets if s}
    missing = [k for k, v in want.items() if v not in got_sets]
    extra = [s for s in got_sets if s not in set(want.values()) and len(s) > 1]
    print(f"schematic nets: {len(got)}  design nets: {len(want)}  mismatched: {len(missing)}  extra: {len(extra)}")
    for k in missing[:10]:
        print("  MISSING", k, sorted(want[k])[:6])
    for s in extra[:10]:
        print("  EXTRA", sorted(s)[:6])
    return not missing and not extra
