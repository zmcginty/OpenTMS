# Phase controller rev C: integrated parts, tight gate-drive loops, wide power copper

166 × 144 mm, 2-layer board, KiCad 7 (opens in 8/9). This board is for SCRs on the **120 V primary side**. Same circuit and connectors as rev B: rev C changes parts and layout only. All symbols are in the project library `phasectl.kicad_sym`.

## What changed from rev B

| Function | rev B | rev C |
|---|---|---|
| Zero-cross detect | Q1 + Q2 (2N7002) wired as a NOR, R20 pull-up | **U8 74AHC1G02**: single NOR gate, SOT-23-5, Schmitt-trigger inputs (+ C32 100 nF) |
| Logic-supply rectifier | D1–D4, 4 × 1N4007 (DO-41) | **D1 MB2S** bridge (TO-269AA) |
| Gate-drive-supply rectifier | D17–D20, 4 × 1N4007 | **D17 MB2S** bridge |
| Gate-output rectifiers | 4 × 1N5819HW per output (16 parts) | **1 × CDBHM140 Schottky bridge** per output (D23, D29, D35, D41) |
| Core-reset clamp | UF4004 + 1N4744A, through-hole, ~20 mm from the transformer | **ES1D (SMA) + BZT52C15 (SOD-123)** directly between the drive pins |
| Pulse-loop decoupling | one 1 µF (C27) shared by both channels | **2.2 µF 100 V X7R per channel** (C27, C33) at each MOSFET source |
| VGDT hub | JP5 by the regulator, J6 at the left board edge | **C26, JP5, J6 together between the two channels**, so the wide VGDT net stays short |
| Part count | 175 | **157** (removed refs are left unused so rev B and rev C compare 1:1) |

## Gate-drive layout (per channel)

- **Q5/Q6** are rotated so the drain tab sits straight below transformer pin 10. The drain path is a straight ~7 mm run, through D13's anode.
- **D13 + D14** (the core-reset clamp) sit in the 8.9 mm gap between drive pins 10 and 1. The clamp loop is about 9 × 4 mm.
- **R38/R50** stand vertically under pin 1, and **C27** connects their VGDT end straight to the MOSFET source. The whole pulse loop (C27 → resistors → winding → MOSFET → C27) fits in about 10 × 15 mm. Rev B's loop was about 25 × 20 mm.
- **R36/R37** (gate resistor and pull-down) sit right at Q5's gate and source pins.
- **Copper widths:**
  - 1.2 mm: VGDT and each channel's SUPPLY, DRAIN and CLAMP nets (net class `GateDrive`)
  - 0.8 mm: isolated gate outputs
  - 0.8 mm: other power nets
  - 1.2 mm: mains
- **Ground return:** the MOSFET sources, C27/C33 and C26 connect to the GND pour solidly, with no thermal spokes.
- **Measured isolation:** hot copper to SELV ≥ 8.8 mm; between hot groups > 6.8 mm.

## New in rev C: parts to double-check

- **CDBHM140 bridge pinout.** The footprint assumes the usual MBS layout (pins 1 and 2 = AC, 3 = +, 4 = −). Check the datasheet of the exact part you buy. The CDBHM140L-HF low-VF version is being dropped by some distributors; CDBHM140-HF (standard VF) is fine here.
- **C27/C33** must be rated 100 V if you plan to inject 48 V.

## Gate drive

```
VGDT --+--R38||R50--+-- T2 pin 1 (drive, dot)      T2 gate 1 (3-4) --TVS--CDBHM140--2R2--+-- G  (J7 "A1")
       |             |                                                     100R | 10n | 1N4148W
      C27 2u2        +--D14 (BZT52C15)--+                                       +-- K
       |                                D13 (ES1D)
      GND --- Q5 source    T2 pin 10 ---+---- Q5 drain (FQD13N10L, gate from 74HC08 DRIVE_A)
```

- **Current drive.** The series resistors set the pulse current, so the transformer winding only sees the gate load voltage (~3–5 V). It doesn't see the 24 V or 48 V supply. That keeps the volt-seconds the same whatever VGDT you use, and an unconnected output can't over-drive anything.
- **Flux check** (Pulse's formula for unipolar drive: B = 33 × ET, limit 2100 G): the first pulse is 10.3 µs × 3.3 V = 34 V·µs, or about **1120 G**. With a high-voltage gate (5 V), it's 51 V·µs, or about **1700 G**.
- **Polarity-proof outputs.** Each gate winding feeds a CDBHM140 Schottky bridge (one TO-269AA part), so a reversed winding or mirrored footprint still fires correctly. The SMAJ15CA limits transients, and the 100 Ω / 10 nF / 1N4148W at G–K hold the gate off against dv/dt.
- **Paired outputs.** A1 and A2 come from the same transformer, so they always fire together; the same goes for B1 and B2. Use one output per SCR. A primary-side anti-parallel pair uses A1 and B1. A2 and B2 are for a second SCR in the same half-cycle, such as paralleled SCRs or the 2-SCR half-controlled bridge on 120 V.

**Choosing R38||R50 and R41||R51** (two 2512 1 W resistors per channel, in parallel):

| VGDT | Resistors per channel | Gate pulse | Each 2512 | Rail current while firing |
|---|---|---|---|---|
| 24 V on-board (default) | 2 × 120 Ω | ~0.34 A | 0.18 W | ~36 mA (the Zettler limit) |
| 48 V injected | 2 × 220 Ω | ~0.41 A | 0.47 W | ~42 mA |
| 48 V injected | 2 × 120 Ω | ~0.74 A | 0.86 W (max) | ~77 mA |

0.34 A is about 2.3 × the SKKT 57's maximum gate trigger current. The on-board 24 V uses nearly the whole 2.8 VA budget at continuous full power. If the Zettler runs hot, fit 2 × 150 Ω, or inject an external supply.

**To inject:** cut JP5 (the bridge between its two pads) **before** connecting J6. Otherwise the external supply back-feeds the LM317. VGDT + 16 V must stay below the MOSFETs' 100 V rating, and C26 is rated 63 V.

## Isolation (custom DRC rules in `phase_controller_revC.kicad_dru`)

- **"Hot" copper** means anything at mains or SCR-cathode potential: the mains input, plus all four gate-output groups. It sits above the silkscreen line.
- **SELV** means everything below the line, including the Teensy header.

| Rule | Required | Actual minimum on this layout |
|---|---|---|
| Hot to SELV (reinforced) | ≥ 8 mm | **8.8 mm** (gate pin to GND pour) |
| Between different hot groups (different SCR cathodes, or mains) | ≥ 3 mm | > 6.8 mm |
| L to N | ≥ 2 mm | |
| PE copper to mains | ≥ 2.5 mm | |

The P0584ANL itself bridges the line, with 12.7 mm from its drive pins to its gate pins. DRC passes with 0 errors and 0 warnings. The netlist matches the design (81 nets, 157 parts), and all 157 footprints are linked to their schematic symbols.

## Bring-up (additions to the rev A procedure)

1. **Supplies.** Power with nothing connected to the gate outputs. Check:
   - VRAW_LOGIC 33–58 V and VRAW_GDT similar
   - +12 V, +5 V, and VGDT ≈ 23.8 V (TP11)
2. **RV2** sets the ramp to 4.8 V, the same as rev A.
3. **Pulses.** Put a 22 Ω resistor across output A1 G–K as a dummy gate. Set ENABLE_IN high and CTRL_IN to 2.5 V. With a scope (**isolated or differential: the output is floating**), check:
   - pulses about 7 µs wide, 8–9 V across the 22 Ω
   - present only in the L-positive half-cycle
   - B1 behaves the same in the other half
4. **SCRs.** Connect the SCR gates with short twisted G/K pairs, then do the lamp test from the rev A README before connecting the MOTs.

## Parts to double-check before ordering

- **P0584ANL footprint:** drawn from Pulse P515.C ("suggested PCB hole pattern", 0.700 × 0.350 in, 0.040 in holes).
- **Zettler footprint:** drawn from the Zettler EI30-18 2.8 VA drawing (rows 20 mm; 6–7 = 5 mm, 7–9 = 10 mm, 9–10 = 5 mm).
- **Mirror check:** both transformers are mirror-safe. The Zettler's secondary phasing stays correct if mirrored, and the gate bridges make polarity irrelevant. Still, hold a real part against a 1:1 printout of the board before ordering.
- **LM317HV headroom:** its 60 V input-to-output rating is only approached with no load at high mains (~59 V). The always-on divider load keeps it below that in practice. Clip-on TO-220 heatsinks on U1 and U7 are cheap insurance.
