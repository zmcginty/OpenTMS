import os, sys, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json, design, make_libs, make_sch, check_sch, build_pcb, route
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(PROJ, exist_ok=True)
make_libs.write(PROJ)
root, sym_uuid = make_sch.build(PROJ)
json.dump({"root": root, "sym": sym_uuid}, open(os.path.join(PROJ, ".sch_uuids.json"), "w"))
assert check_sch.compare(os.path.join(PROJ, "hvprobe.kicad_sch"))
path = build_pcb.build()
import subprocess
subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "route.py"), os.environ.get("PASSES", "60")], check=True)
subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, %r); import build_pcb; build_pcb.report(%r)" % (os.path.dirname(os.path.abspath(__file__)), path)], check=True)
