# FZ1200R33KF2 bolt-on gate driver (rev D, prototype)

Single-channel plug-and-play style gate driver for the Infineon/eupec FZ1200R33KF2
(3300 V / 1200 A IHM single switch). Same external interface as the CONCEPT 1SD418F2:
15 V supply on a 5-pin header (or JST-XH), fiber-optic command in, fiber-optic status out,
2-level / 3-level mode jumper. Built only from catalog parts, with no custom-wound magnetics and no
CONCEPT / Power Integrations ASICs, cores or DC/DC modules. Dynamic advanced active clamping follows the idea used in the
1SP0635 (SCALE-2 plug-and-play).

**Status: design draft, not built or tested. Hazardous voltages. Read "Open items" before ordering anything.**

## Files

| File | What it is |
|---|---|
| `fz1200_gate_driver.kicad_pro/.kicad_sch/.kicad_pcb` | KiCad 7 project (opens in KiCad 8/9; they upgrade it) |
| `power.kicad_sch`, `control.kicad_sch`, `output.kicad_sch` | Sub-sheets: isolation supply, protection logic, output stage + HV |
| `igbt_drv.kicad_sym`, `igbt_drv.pretty/` | Project symbol library and custom footprints (incl. the provisional XS000027 land pattern) |
| `BOM_fz1200_gate_driver.csv` | Grouped BOM (Qty 0 = DNP) |
| `out/` | Gerbers + drill (6 layers), pick-and-place CSV, schematic PDF, per-layer PCB PDFs |
| `generator/` | Python that generates schematic, PCB, routing and BOM from `circuit.py`; `mech.py` holds the mount coordinates |

The schematic and board are generated, so changes are easiest made in `generator/circuit.py`
(parts and nets) and `generator/gen_pcb.py` (placement), then re-run
`python3 generator/gen_sch.py`, `/usr/bin/python3 generator/gen_pcb.py`,
`/usr/bin/python3 generator/patch_fix.py fz1200_gate_driver.kicad_pcb` (CH6 neck-downs and primary ground stitching),
`/usr/bin/python3 generator/fill_zones.py ...`, `/usr/bin/python3 generator/drc.py fz1200_gate_driver.kicad_pcb drc.rpt`,
`/usr/bin/python3 generator/finish.py fz1200_gate_driver.kicad_pcb drc.rpt` (closes the few connections the router leaves open),
`/usr/bin/python3 generator/manual_fix.py fz1200_gate_driver.kicad_pcb drc.rpt` (via-bridges anything still open),
`/usr/bin/python3 generator/cleanup.py fz1200_gate_driver.kicad_pcb`, `/usr/bin/python3 generator/fill_zones.py fz1200_gate_driver.kicad_pcb`,
`python3 generator/gen_bom.py`, `sh generator/export.sh`. If an edit leaves a few opens that finish/manual_fix can't close, `generator/maze_fix.py board drc.rpt` is a fine-grid maze router for them. `python3 generator/check_sides.py` confirms no net crosses the isolation barrier.
You can also just edit the KiCad files by hand from here on.

## Interface (matches 1SD418F2)

| Item | This design | 1SD418F2 |
|---|---|---|
| Supply | J1 1x5 2.54 mm: pin 3 = +15 V, pins 1, 2, 4, 5 = GND (or J2 JST-XH: 1 = +15 V, 2 = GND) | same 5-pin pinout |
| Command | HFBR-2522Z, light = IGBT on | HFBR-2522 |
| Status | HFBR-1522Z, light on = healthy, 0.9 us dark ack on every command edge, about 1.1 s dark on fault | same protocol (1 s) |
| Mode | JP1 1-2 = 2-level (driver turns off on short circuit), 2-3 = 3-level (report only, host turns off). Open = 2-level | J1 jumper, same meaning |
| Gate | +15 / -10 V (set by the catalog transformer), Rg(on) 1.8 ohm, Rg(off) 3.4 ohm, Cge 222 nF | +-15 V, 1.8 / 3.4 ohm, 220 nF |
| Mounting | Screws onto the module G, aux E, aux C terminals (M4), plus a 3.2 mm support hole; terminal positions and outline as on the 1SP0635 (see Board) | same principle |

## Architecture

- **Isolation supply:** TI SN6507 push-pull driver (36 V, two 0.5 A switches, 150 kHz set by R1,
  soft-start and current limit on SS/ILIM) driving a **Poweronics XS000027**, a catalog gate-drive
  transformer for IGBTs up to 3.3 kV: 6 kV isolation test, partial-discharge routine test at 3.545 kV,
  18 kV impulse, 15-0-10 V output, 10 W from a 15 V supply. Rails: VP = +15 V, VE = emitter,
  VEE = -10 V, unregulated (the 1SD418F2 is unregulated too). D3 rectifies the 15 V end and D6 the 10 V end;
  D4/D5 are DNP positions in case Poweronics' reference rectifier uses all four diodes.
- **Logic** is referenced to VEE (the -10 V rail) with a 5 V LDO, so the fiber parts, comparators,
  gates and both gate-driver ICs share one ground with no level shifting.
- **Gate power stage (rev D): Q2 = Infineon ISA170230C04LMDS**, a complementary 40 V OptiMOS dual in
  one SO-8 (N 17 mohm / 38 A pulsed, P 23 mohm / -35 A pulsed, VGS +-20 V), the same family as the
  ISA250250N04LMDS dual-N. Q2 is the only part that carries gate current:
  - **Turn-on:** Q2 P-channel, source at VP (+15 V), drain -> R40 || R41 (2 x 3.6 ohm) -> GATE.
    Its gate is driven between VP and VE (0 / -15 V VGS) by U21 (UCC27511, supplied VP-VE).
    The logic is VEE-referenced, so DRV_ON reaches U21 through a small level shifter:
    Q7 (IRLML0060) pulls LSIN down through R60, D20 (BAT54) clamps it at VE, R61 pulls it up to VP.
    If anything loses power, R61/R64 hold the P-FET off.
  - **Turn-off:** Q2 N-channel, source at VEE (-10 V), drain -> R47 || R48 (2 x 6.8 ohm) -> GATE,
    driven between VE and VEE (10 V VGS) by U22 (UCC27511).
  - Peak currents with these resistors: about 11 A on, 7 A off (25 V swing), so the 35/38 A pulse
    rating is margin. Lower Rg to get more current, the stage is not the limit.
  - C50 (10 uF) and C51 sit right at Q2's two source pins, so the high-current loop is VP-C50-VEE.
  - Why not two ISA250250N04LMDS N-channels: an N-FET high side needs its gate above VP, i.e. a
    bootstrap or charge pump, and a bootstrap can't hold the IGBT on indefinitely (DC on, or long
    on-times at low frequency). The complementary sibling avoids that with the same package and pinout.
  **Soft shutdown** after a fault: Q3 (IRLML0060) through 10 ohm (normal off path held off).
- **Short-circuit (Vce) detection:** 6 x 3.3 Mohm high-voltage chain plus 309 kohm divider into a
  TLV3202 comparator. Trip at Vce = 55.1 V (1SD418F2: 50-60 V), blanking 6.1 us from the on command.
  The fault latches a 1.1 s TLC555 lockout and darkens the status fiber.
- **UVLO:** both rails watched (each comparator channel has its own LM4040 reference, D13/D14):
  VP-VEE trips at 21.5 V rising / 20.3 V falling (nominal 25 V), VE-VEE at 8.5 / 8.1 V (nominal 10 V).
  That's about 86 % of nominal, the same ratio as the 1SD418F2's 13 V threshold on its 15 V supply.
- **Advanced active clamping:** 6 x 1.5SMC440A TVS from collector to gate through SS34 + 10 ohm.
  When clamp current flows, Q4 pulls the gates of the turn-off switches (Q2, Q3) low, so the TVS
  current goes into the gate instead of the off-resistor. This is the "advanced" part.
- **Dynamic AAC (DA2C, as on 1SP0635):** an extra SMBJ188A is in series. Q5 (600 V) shorts it while
  the IGBT is on and for about 17 us after turn-off, then releases it. So the clamp acts at
  2508-2772 V during switching, but the module can sit at a higher DC link when it's off. Q5's gate is
  bootstrapped (C57) so it stays on while the clamp node rises during clamping.

## Key numbers (calculated; the clamp was also checked in a behavioural ngspice model)

| Item | Value |
|---|---|
| Clamp onset, switching (6 x VBR) | 2508-2772 V at 25 C |
| Clamp onset, off-state (DA2C released) | 2717-3003 V |
| TVS stand-off: switching / off-state | 2256 V / 2444 V (in the off state D40 sees at most its 231 V VBR max), so max DC link 2200 V (same as 1SD418F2) |
| Simulated Vce peak, 4 kA turn-off, 100 nH, BV +6 % hot | 2.96 kV (< 3.3 kV) |
| Desat threshold / blanking | 55.1 V / 6.08 us; gate starts falling about 6.3 us, well inside the 10 us SC rating |
| Gate energy per cycle (QG about 19 uC at -10/+15 V, estimated from the 22 uC at +-15 V, + Cge, 25 V swing) | about 0.61 mJ |
| Quiescent load | about 0.5 W (the status LED's 30 mA dominates) |
| Available output | about 6 W, set by the SN6507's 0.5 A switches at 15 V in (the XS000027 is rated 10 W) |
| Max switching frequency | **about 8 kHz** (1SD418F2: 4 kHz) |
| Status ack / fault block | 0.90 us / 1.1 s |

## Board

- 197.1 x 43.94 mm, outline and terminal positions taken from the 1SP0635V mechanical
  drawing (Description & Application Manual, Fig. 5), which bolts onto the same 3.3 kV IHM housing:
  - C (aux collector, X3): 20.04 mm from the left edge, 12.16 mm below the top edge
  - E (aux emitter, X2): C + 38.15 mm, 6.96 mm below the top edge
  - G (gate, X1): E + 21.00 mm, same height
  - 3.2 mm support hole: G + 114.4 mm, 3.95 mm above the bottom edge
  - The notches in the bottom edge and the cut-away under the right end follow the 1SP0635 outline.
    Corners without a dimension were scaled from the drawing (about 0.3 mm) and kept on or inside it.
  The layout follows the 1SP0635's arrangement too: HV chains at the collector (left) end, output
  stage around E/G, logic and fibers in the middle (fiber ports overhang the bottom edge), transformer and
  15 V input at the right end. Rev D: the fibers sit at 10.16 mm pitch so they interlock, and the
  comparator cluster (U19) moved to the bottom side above them, freeing the area under the gate power stage.
- 6 layers: F.Cu parts + signals | In1 VEE plane (PGND under the primary) | In2 signals |
  In3 signals + 5 V logic plane under the logic block | In4 VE (emitter) plane | B.Cu signals + parts.
  At this outline (about 30 % less usable area than rev A) four layers did not route. The comparator
  and UVLO clusters sit on the bottom side; the comparators are TLV3202 in SOIC-8 for easier escape.
- Primary-to-secondary: >= 20 mm copper-to-copper (measured 21.6 mm) with a 2 mm milled slot under T1 (1SD418F2: 20 mm).
- HV creepage is graded along both chains. Low-voltage copper keeps the IEC 60664-1 PD2 /
  material group II creepage for 3300 V (22 mm) from the collector chain. Between HV nets the
  spacing follows each pair's worst-case DC voltage at a 2200 V link, in both DA2C states
  (`generator/potentials.py`). Pairs under 400 V (only inside the Q5/D40 DA2C cluster, where the
  SOT-223 pin pitch alone is below the uncoated value) are held to the coated (IEC 60664-3) value.
  **So conformal coating is mandatory**, and so is FR4 with CTI >= 400.
  `generator/check_creepage.py` reports both the coated and uncoated results.
- Minimum copper clearance rule 0.15 mm (needed by the 0.5 mm-pitch SN6507 land pattern); everything the router placed keeps 0.25 mm or more.
- Routed by a custom router in `generator/gen_pcb.py`; the last few connections are closed by `finish.py`.
- Verification state (rev D as delivered):
  - KiCad DRC: 0 unconnected pads, 0 clearance / hole / edge / short errors. Remaining: 171
    "library not in this configuration" warnings (footprints are embedded in the board; they clear once
    KiCad's standard libraries are in your global table), 13 cosmetic silkscreen warnings (some are the fiber outlines overhanging the edge, intended) and one short
    dead copper stub (harmless; delete it in the editor if you like).
  - Schematic netlist matches the intended circuit net-for-net (85 nets); `check_sides.py` confirms no net crosses the barrier, and the PCB pad nets match the schematic.
  - Terminal positions on the board: C (20.04, 12.16), E (58.19, 6.96), G (79.19, 6.96), support hole
    (193.59, 39.99), i.e. C to E 38.15 mm and E to G 21.00 mm, as in the 1SP0635 drawing.
  - Creepage: primary-secondary 21.7 mm. All HV pairs pass the coated (PD1) limits, and everything at or
    above 400 V passes uncoated PD2. The 3 uncoated shortfalls are 231 V pairs inside the DA2C switch cluster.
  - Courtyards: no overlaps, everything inside the outline.
  - KiCad 7's command line has no schematic ERC, so run ERC once in the GUI.

## Open items before fabrication (in priority order)

1. **Check the fit on a real module.** Terminal positions now come from the 1SP0635 drawing (which
   fits this IHM housing). Before ordering, print the board 1:1 (`out/pcb_top.pdf`) or measure a
   module and confirm C/E/G and the support hole, and that the cut-away clears the main terminals.
2. **Get the XS000027 drawing and reference design from Poweronics** (hello@poweronics.co.uk; they
   offer a reference design, Altium footprint and schematic). Then:
   - Replace the provisional land pattern `XS000027_PROVISIONAL` (primary row and secondary row 24 mm
     apart, so the board keeps 20 mm+ isolation). Keep the 2 mm slot between the rows.
   - Confirm the pin mapping (primary: SW1 / centre tap = +15 V in / SW2; secondary: 15 V end, 0 V = emitter,
     10 V end) and the rectifier: D3 + D6 as drawn, or fit D4/D5 if their reference uses a 4-diode bridge.
   - Confirm the switching frequency range (R1 = 82.5 kohm gives about 150 kHz) and that the
     SN6507's 48 % default duty with 70 ns dead time suits the core.
3. **Optional input UVLO:** EN is tied to VCC through R3 (0 ohm), so the SN6507 starts as soon as VCC
   comes up. Fit R4 and change R3 to a divider if you want a primary-side UVLO as well.
4. **Confirm Q2's channel-to-pin mapping and land pattern** on the full Infineon datasheet. The design
   assumes channel 1 (pins 1 S, 2 G, 7-8 D) = N and channel 2 (pins 3 S, 4 G, 5-6 D) = P, which is
   how the datasheet orders the parameters, and a standard SO-8 (3.9 x 4.9 mm, 1.27 mm) land pattern.
   If it's the other way round, swap the nets in `circuit.py` (Q2) and regenerate.
5. **Fiber connectors (rev D):** footprint `HFBR_Versatile` now follows Broadcom AV02-1501EN: all
   holes 1.01 mm, pins 5 and 8 plated, courtyard = 10.16 mm body width, so U3 and U18 sit at 10.16 mm
   pitch and their molded L-bracket / L-slot tabs interlock. Pin 5/8 row is 2.0 mm from the board
   edge (Broadcom: >= 1.85 mm) and the ports overhang the edge. Pins 5/8 are left unconnected
   (mechanical); tie them to VEE if you want them grounded. Check the order of the two modules: the
   interlock only engages with the L-bracket side of one against the L-slot side of the other.
6. **Validation:** double-pulse at 1800 V and 2200 V (turn-off overshoot with and without DA2C),
   short-circuit test (type I) at 2200 V, blanking margin at full current (the 3.3 kV Vce tail
   must fall below 55 V within about 6 us; increase R20 if not), hipot, partial discharge, thermal
   check of the R(on)/R(off) resistors at the target switching frequency.

## Not included / changed from the original

- Turn-off rail is -10 V instead of -15 V, because that's what the catalog transformer provides. Expect slightly
  slower turn-off and less margin against dv/dt-induced turn-on; verify both in the double-pulse test.
- Isolation transformer: Coilcraft TX2-ZE242x was evaluated and rejected for this module. It is a
  5 kVrms / 10 mm creepage part with no partial-discharge rating, while a 3.3 kV IHM driver needs
  about 6 kVrms test, partial-discharge extinction >= 2.6 kV and >= 20 mm pri-sec spacing (1SD418F2 / IEC 61800-5-1).
  It suits 1200 V modules (1700 V only after your own PD test). The XS000027 stays.
- No overcurrent measurement (the 1SD418F2 has none either); short-circuit detection only.
- References and pin assignments were checked against manufacturer datasheets for the ICs;
  passives are generic (value, voltage, dielectric, package) so you can source them freely.
