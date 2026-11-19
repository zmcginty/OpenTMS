# HV differential probe v1 — KiCad project

7 kV / 1000:1 / ~100 MHz (simulated) differential probe, generated from the design doc
"7 kV / 50 MHz High-Voltage Differential Probe — Design v1".

Open `hvprobe.kicad_pro` in KiCad 7 or newer. Files:

| File | What |
| --- | --- |
| `hvprobe.kicad_sch` | Schematic (one A3 sheet, net-label style) |
| `hvprobe.kicad_pcb` | 2-layer board, 232 × 44 mm, fully routed |
| `hvprobe.kicad_sym`, `hvprobe.pretty/` | Project parts: THS4631, TPS7A4901, TPS7A3001, TMR 3-0523, BAV199, GDT, coax pads |
| `fab/` | Gerbers, drill file, pick-and-place CSV |
| `hvprobe-bom.csv` | Grouped BOM |
| `generator/` | Python that produced all of the above (`make_all.py`) |

## What has been checked

- Schematic netlist exported with `kicad-cli` matches the intended netlist exactly (54 nets).
- PCB DRC (KiCad 7.0.11): 0 unconnected pads, 0 clearance / short / hole errors.
  Remaining warnings: 13 silkscreen overlaps (cosmetic), plus "library not in configuration"
  notices that come from the headless build machine and disappear in a normal KiCad install.
- Footprint pinouts checked against datasheets: THS4631 (SOIC-8), TPS7A49/TPS7A30 (MSOP-8 PowerPAD),
  TMR 3 (SIP-8, pin 4 absent), BAV199 (pin 1 A1, 2 K2, 3 K1/A2).

## Not checked — do before ordering

1. **ERC** was not run (KiCad 7's command line has no ERC). Run Inspect → ERC once in the GUI.
2. **GDT footprint** (`GDT_Bourns_2051`) uses estimated pads; compare against the Bourns 2051 land pattern.
3. **HV spacing**: strings are on a 10 mm pitch, the two legs are 24 mm apart with a 3 mm milled slot, and there is no
   copper under the HV area. Confirm creepage against IEC 60664-1 for your working voltage, and conformal-coat the HV area.
4. **Netclass HV** (`HV_*`) uses 1.5 mm clearance, which is right for the 700 V-per-segment spacing; any edits near the
   strings must keep it.
5. Print the board 1:1 and check that the trimmers, pots and TMR module fit.

## Differences from the design doc

- Rtip (330 Ω) is on the board at the input pads instead of in the clip.
- The difference amp uses four discrete 0.1 % resistors instead of a matched network; the DC CMRR trim absorbs the mismatch.
- Battery/USB OR-ing uses two Schottky diodes (B5819W) instead of an ideal-diode IC; USB still gives ~4.7 V, inside the TMR's 4.5–9 V range.
- The output is a captive coax soldered to pads (with tie-wrap holes) rather than an on-board BNC.
- Two layers instead of four; the bottom layer is a near-solid ground plane under the amplifiers.
- Board is 232 mm long: about 120 mm of HV strings, plus the amplifiers and an isolated power section.

## Trims (silkscreen labels)

`GAIN` = RV101, `CMRR DC` = RV201, `FLAT +` = C113, `CMRR AC` = C213, `OFFSET` = RV301. Follow the calibration
procedure in the design doc. C112/C212 are select-on-test pads (DNP by default).

## Regenerating

`python3 generator/make_all.py` needs KiCad 7 (pcbnew Python), kiutils, Java 21 and Freerouting 1.9.
The autorouter is not deterministic, so a rerun gives a different (but DRC-checked) routing. One GND stitching via
next to C115 was added by hand after the final route.
