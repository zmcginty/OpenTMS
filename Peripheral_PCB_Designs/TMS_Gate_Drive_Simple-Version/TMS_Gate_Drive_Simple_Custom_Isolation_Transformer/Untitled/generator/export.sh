#!/bin/sh
# Renders + fab outputs into ./out
set -e
D=$(dirname "$0")/..; cd "$D"; mkdir -p out/gerbers
kicad-cli sch export pdf -o out/schematic.pdf fz1200_gate_driver.kicad_sch
kicad-cli pcb export gerbers -o out/gerbers/ --layers F.Cu,In1.Cu,In2.Cu,In3.Cu,In4.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts fz1200_gate_driver.kicad_pcb
kicad-cli pcb export drill -o out/gerbers/ fz1200_gate_driver.kicad_pcb
kicad-cli pcb export pos --format csv --units mm -o out/positions.csv fz1200_gate_driver.kicad_pcb
kicad-cli pcb export pdf --layers F.Cu,F.SilkS,Edge.Cuts -o out/pcb_top.pdf fz1200_gate_driver.kicad_pcb
kicad-cli pcb export pdf --layers B.Cu,B.SilkS,Edge.Cuts --mirror -o out/pcb_bottom.pdf fz1200_gate_driver.kicad_pcb
kicad-cli pcb export pdf --layers In1.Cu,Edge.Cuts -o out/pcb_in1.pdf fz1200_gate_driver.kicad_pcb
kicad-cli pcb export pdf --layers In2.Cu,Edge.Cuts -o out/pcb_in2.pdf fz1200_gate_driver.kicad_pcb
kicad-cli pcb export pdf --layers In3.Cu,Edge.Cuts -o out/pcb_in3.pdf fz1200_gate_driver.kicad_pcb
kicad-cli pcb export pdf --layers In4.Cu,Edge.Cuts -o out/pcb_in4.pdf fz1200_gate_driver.kicad_pcb
