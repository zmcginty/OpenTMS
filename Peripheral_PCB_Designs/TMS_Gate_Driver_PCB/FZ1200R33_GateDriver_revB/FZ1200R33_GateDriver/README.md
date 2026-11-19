# FZ1200R33KF2 plug-on gate driver — rev B (1SD418FI / 1SP0635-class, open design)

A single-channel IGBT driver for the Infineon FZ1200R33KF2 / KF2C (3.3 kV, 1200 A, IHV 190×140). It screws directly onto the module's M4 control terminals. **No CONCEPT / Power Integrations parts are used.**

## What changed from rev A
| | rev A | rev B |
|---|---|---|
| Isolated supply | SN6507 + custom 12 kVrms transformer | **Murata MGJ6D121510SC** off-the-shelf DC/DC module |
| Gate voltage | +15 V / −15 V | **+15 V / −10 V**, as set by the MGJ6 IGBT configuration |
| Board | 180 × 110 mm with placeholder terminals | **197.1 × 43.94 mm**, with terminal holes at the 1SP0635 positions |
| Active clamp | 8× SMBJ300A | **5× P6SMB550A**: Vbr 2.6–2.9 kV, shorter chain |

## Interfaces (same as 1SD418FI)
| Interface | Part | Notes |
|---|---|---|
| Power | J1 1×4 2.54 mm header **or** J2 JST-XH B4B (fit one) | Pins 1,2 = VDC; pins 3,4 = GND. Input 12–16 V (15 V nominal). The MGJ6 absolute maximum is 20 V. |
| Command | U5 HFBR-2521Z | Light ON = IGBT ON |
| Status | U10 HFBR-1521Z | Light ON = ready / no fault |

## Mechanical (from 1SP0635 manual, Fig. 5)
Datum: left board edge, and a baseline 4.0 mm above the bottom edge.

| Item | x [mm] | Height above baseline [mm] | KiCad position (origin = top-left of board) |
|---|---|---|---|
| C-aux (X3, M4) | 20.04 | 27.83 | (20.04, 12.11) |
| E-aux (X2, M4) | 58.19 | 33.03 | (58.19, 6.91) |
| G (X1, M4) | 79.19 | 33.03 | (79.19, 6.91) |
| Support hole Ø3.2 (H1) | 193.59 | 0 | (193.59, 39.94) |

- The outline follows the 1SP0635: 197.10 × 43.94 mm, the 2.59 mm top step at x > 162.6, and the bottom notch from x = 69.3 to 100.5.
- **Deviation 1:** the 1SP0635's diagonal cut-out under its fiber connectors (bottom-right, x ≈ 129–190) is filled in here so the MGJ6 fits. Check that this area clears the main power terminals and busbar on your stack.
- **Deviation 2: height.** The MGJ6 SIP is about 28 mm tall, versus the 1SP0635's 12 mm. Check busbar clearance above x = 150–162 mm.
- Bottom-side parts are thin SMD only (≤ 2.5 mm: 2512 resistors, 0603/0805/1206 parts, SOT-23). Make sure the gap between the board bottom and the module lid allows this.

## Isolated supply: Murata MGJ6D121510SC — read this
- 9–18 V input, +15 V at 240 mA and −10 V at 240 mA (6 W total), 15 pF coupling capacitance.
- Isolation: 5.7 kVDC hi-pot, **3 kVDC continuous (functional, non-safety) barrier**, partial-discharge inception above 3.5 kVpk, 8 mm creepage.
- That covers the emitter-potential swing of a 3.3 kV IGBT on a DC link up to about 2.2–2.5 kV. It is **not** reinforced insulation at 3.3 kV system level. The 15 V feed and the controller must therefore be isolated at system level, for example by a central reinforced-isolation 15 V supply, as the system safety concept requires.
- The only off-the-shelf supplies with a full 3.3 kV rating I found are Power Integrations ISO5125I / ISO6125R-33, which you excluded.
- **Output configuration** (datasheet "Output configurations for power switches", IGBT column):
  - pin 3 = +15 V → VP15
  - pin 6 = 0 V → COM_E (aux emitter)
  - pin 4 = −10 V → VN10
  - pin 5 = −5 V tap (not connected)
  - pin 2 = +Vin, pins 7/8 = −Vin, pin 1 = Dis/Sync (22 nF to −Vin, per the datasheet)
- **Footprint `GD:Murata_MGJ6_SIP8`:** the pin positions are **inferred** (positions 0, 1, 5, 6, 7, 8, 13 and 14 on a 2.54 mm grid, 35.56 mm span). They follow from the 8 mm creepage spec and the 35.56 mm dimension. **Verify against the Murata drawing (KDC_MGJ6C-SCDC) before fab.**
- **Minimum load:** below 10 % load the outputs rise to 17 V / 12 V. R57 (680 Ω, 2512) preloads +15 V. The −10 V output is loaded by the logic regulator U4 and the fiber LED (~40 mA).
- Two milled slots under the module between the pin groups lengthen the creepage path. The DRC rule `PRI_to_SEC_MGJ6_functional` enforces ≥ 8 mm everywhere else.

## Key design numbers
| Item | Value |
|---|---|
| Gate voltage | +15 V / −10 V vs. aux emitter (MGJ6 ±3 % / ±5 %, unregulated) |
| RGon / RGoff | 1.25 Ω each by default (4× 10 Ω 2512 per branch, 2 branches, Schottky-steered; bottom side R38–R53). Datasheet test value is 1.2 Ω. |
| Peak gate current | ≈ +15 A / −10 A (2 parallel MJB44H11/MJB45H11 pairs) |
| Gate power | QG ≈ 22 µC over ±15 V, ≈ 18 µC over +15/−10 V. P ≈ 0.45 mJ × f, so ~1.4 W at 3 kHz. The MGJ6 gives 3.6 W (+15 V) + 2.4 W (−10 V). |
| Supply UVLO | READY drops when VP15 − VN10 < 20.8 V (nominal 25 V) |
| Desat | Blanking ≈ 5 µs (R31 = 15 k, C28 = 120 pF). Trip at DESAT > COM_E + 13.3 V, i.e. VCE ≈ 9 V. Response = blanking + ~1.5 µs. |
| Active clamp | 5× P6SMB550A, Vbr 2.6–2.9 kV. At a 2.2 kV DC link each TVS sees 440 V, below its 470 V standoff. Clamp current is steered to the gate (D28/R55) **and** the booster base (D29/R56). |
| Fault blocking | 1.0 s, retriggered while the fault persists (TLC555) |

## PCB status
- 4 layers. In1 = COM_E plane and In2 = VN10 plane (secondary); GND_P on In1/bottom (primary end).
- **Done and verified:**
  - all parts placed;
  - KiCad's exported netlist and the PCB pad nets both match the design model exactly;
  - no clearance, isolation-rule or courtyard violations in DRC;
  - HV collector chains routed: desat 4× STTH112A + 22 MΩ sharing resistors, and the TVS clamp chain.
- **DRC rules** (`.kicad_dru`):
  - PRI ↔ SEC ≥ 8 mm (MGJ6-limited);
  - COLL ↔ LV secondary ≥ 16 mm;
  - HV chain nodes ↔ LV ≥ 8 mm;
  - HV chain internal ≥ 1.5 mm.
- **To do:** route the remaining LV nets (223 connections). Route the gate loop by hand: VP15/VN10 caps → BJTs → Rg arrays on the bottom side → X1 (G), returning through the COM_E plane to X2 (E). Keep it wide and short. Do not autoroute through the HV area at the left end, or between the MGJ6 pin groups.

## Verify before fab
1. MGJ6 footprint pin positions (see above).
2. HFBR-x521Z footprint (mounting-pin positions and location relative to the board edge).
3. MJB44H11 / MJB45H11 TO-263 pinout (B, C-tab, E).
4. Busbar / main-terminal clearance in the filled-in bottom-right area and above the 28 mm-tall MGJ6.
5. Bench validation: double-pulse test, RG tuning, desat blanking time (C28), clamp level, dv/dt immunity.
6. Run ERC once in the KiCad GUI. KiCad 7's CLI has no ERC, so connectivity was checked by script.

Some resistor and capacitor part numbers follow the manufacturer's numbering pattern rather than a catalog lookup. Check them with your distributor.

## Regenerating
Scripts are in `gen/`. Run `make_sch.py`, then `make_pcb.py`, then `make_bom.py`, using the system Python with KiCad 7's `pcbnew` module. Re-running `make_pcb.py` overwrites any manual PCB edits.
