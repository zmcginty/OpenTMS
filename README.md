# Open-Source Transcranial Magnetic Stimulator

An open-source transcranial magnetic stimulator (TMS) project, including coil designs, pulse-generator hardware, and control/charging circuitry.

> **Safety Notice**
> This is **not** a legitimate medical device and is not a substitute for real TMS treatment. This is an experiment I built in my garage, and I cannot verify that it does what it's supposed to (if anyone wants to build an open-source MRI.... let me know...) This device is dangerous, it operates at energy levels more than enough to stop your heart, kill you, blow up and do lots of damage. If you're going to play with high energy pulsed power systems; be careful.

## Table of Contents
- [TMS Coils](#tms-coils)
- [Coil Cooling](#coil-cooling)
- [Coil Materials](#coil-materials)
- [IGBT Switches](#igbt-switches)
- [Gate Drivers](#gate-drivers)
- [Measurement System](#measurement-system)
- [Charging Circuit](#charging-circuit)
- [Pulse-Generator Designs](#pulse-generator-designs)
- [Charge-Control Circuit](#charge-control-circuit)
- [Useful Resources](#useful-resources)
- [TODO](#todo)

---

## TMS Coils

I primarily build two coil geometries — single coils and figure-8 coils. What I've focused on are air-core windings in either single-layer or multi-layer form. The general architecture is adapted from research papers and the limited information published by commercial TMS coil manufacturers. The figure-8 CloudTMS coil (below) was a major influence on my designs in terms of hollow copper conductor carrying coolant.

<p align="center"><img src="Example Images/CloudTMS_Coil_Slice.png" alt="CloudTMS coil" width="200"/></p>

As I moved to higher pulse rates and energies, active cooling became necessary. My preferred approach, similar to the CloudTMS design, uses hollow copper tubing (sold for HVAC/refrigeration) insulated with PTFE heat-shrink. PTFE works well because it's a good dielectric, stays thin-walled after shrinking, tolerates high temperatures, and holds the coil shape better than thicker heat-shrink alternatives — though it does require fairly high heat to shrink fully.

**Build process:**
- Source 4mm OD / 3mm ID copper tube (5m sections) and 4.5mm, 4:1 PTFE heat-shrink in bulk.
- Feeding heat-shrink onto a long tube run takes patience — compressed air, used to create an air cushion between tube and shrink-wrap, makes this much easier.
- Wind the coil on a custom jig, either single-layer or multi-layer (multi-layer here just means winding randomly around a bobbin rather than stacking discrete single-layer coils).
- Insert something into the copper tube close to the tube's ID (weed-wacker cable works well) during the first few windings to prevent the tube from collapsing, then remove it once winding is complete.
- Hold the coil together with tape ahead of potting.

<p align="center">
  <img src="./TMS Coils/figure_8_multilayer_coil_naked_4.jpg" alt="Coil before potting" width="200"/>
  <img src="./TMS Coils/figure_8_multilayer_coil_naked_2.jpg" alt="Coil before potting" width="200"/>
</p>
<p align="center"><em>Example coil before potting</em></p>

Potting the coil in epoxy significantly improves mechanical stability and reduces noise by keeping the windings from moving against each other under pulse forces. Hot glue works as an alternative but produces coils that are noticeably louder in operation — not confidence-inspiring at higher power levels.

<p align="center"><img src="./TMS Coils/figure_8_multilayer_coil_potted_4.jpg" alt="Coil after potting" width="200"/></p>
<p align="center"><em>Example coil after potting</em></p>

## Coil Cooling

Coils built from hollow copper tube are cooled by pumping coolant through the winding itself.

- **Mineral oil:** Used for a long time — thinner than typical oils but thicker than water. Maintaining ~40°C required roughly 150–200 psi to push enough flow through the coil's small ID. At higher energy levels, I added a water chiller and heat exchanger to pre-cool the oil.
- **Distilled water:** More recently, I've switched to distilled water paired with a diaphragm pump, since the diaphragm provides a degree of electrical isolation between the pump mechanism and the fluid. This is simpler and more effective overall, though there is still a risk of the coolant loop reaching high voltage.

## Coil Materials

| Material | Purpose | Notes |
|---|---|---|
| [Hollow copper tube](https://www.amazon.com/dp/B082FDVNC5) | Water-cooled coil winding | Standard refrigeration-grade tube; cheap and widely available |
| [Fiberglass resin sheet](https://www.amazon.com/dp/B0DYT28W8W) | Application-side coil face | Sturdy, good electrical insulator, and appears permeable to magnetic fields — a disassembled commercial coil used what looked like FR4 fiberglass (PCB-grade) |
| [MAX EPC epoxy potting compound](https://www.amazon.com/dp/B07PMTMKZQ) | Coil potting | Inexpensive, good electrical insulator, decent thermal conductivity. For tube-cooled coils, MAX MCR (lower thermal conductivity) is also an option |

---

## IGBT Switches

My current switch of choice is the **Infineon/Eupec FZ1200R33KF2** — inexpensive and capable of handling substantial power. Prices on eBay range from ~$100–$400, and I've pushed 8kA pulses through a single unit. Adequate snubber capacitance/circuitry is essential to keep the voltage across the device under its 3.3kV rating (I target a safety margin below that).

## Gate Drivers

I've used two Power Integrations gate drivers with this IGBT:

- **2SC0535T2G0-33** — Functional, but lacks features like active clamping to protect against turn-off voltage spikes. Requires a custom PCB with gate turn-on/turn-off resistors and capacitors.
- **1SD418F2-FZ1200R33KF2** — Purpose-built for the FZ1200R33KF2; bolts directly onto the device pads. Requires only a 15VDC supply (I use a 1A source). It communicates over two fiber-optic channels (input signal and output/status), which I drive with custom PCBs using AFBR-1624Z HFBR transmitter/receiver modules, controlled by a Teensy 4.1 through a 3.3V→5V level shifter.

---

## Measurement System

### Differential Voltage Measurement
Monitoring Vce (collector-emitter voltage) across each IGBT is critical to staying within its rating — I keep a safety margin and stay below 2.5kV on 3.3kV-rated devices. Since these switches operate on the high side, measurement requires a high-voltage differential probe: I use a **Micsig DP20003** (5600V, 100MHz). A similar, lower-range HV differential probe (1300V) monitors the main capacitor bank voltage.

### Current Measurement
A shunt resistor was my first attempt but never produced a trustworthy signal. Two methods have worked well since:

1. **Rogowski coil** — Excellent for high current measurement (AC or DC, no iron core to saturate), but calibrated probes are hard to find secondhand, and a calibrated integrator circuit is needed to get a usable scope signal. I use a calibrated coil from [PowertekUK](https://powertekuk.com) — specifically the [CWTMini](https://powertekuk.com/cwtmini).
2. **Current transformer (CT)** — I use a Tektronix A621 AC current probe (rated to 2kA, available on eBay for $100–$500). In practice, it agrees with the Powertek Rogowski coil up to roughly 8kA despite the rated limit.

---

## Charging Circuit

The charging circuit is a simple, open-loop design: mains power feeds a variable transformer (variac), which drives a microwave oven transformer (MOT) to step up voltage. The stepped-up AC is rectified by a full-bridge rectifier and used to charge the main discharge capacitor bank, which is then discharged through the coil via a high-side switch (latching SCR or non-latching IGBT).

### Microwave Oven Transformers
The current two-switch flyback pulse generator uses two matched MOTs (matched by checking voltage and phase agreement between units — *measurements/resources TODO*). Drawing ~15A at 120V, two matched transformers stay below ~60°C with air cooling alone.

Earlier designs used a single MOT with low-pressure mineral-oil cooling, which worked up to ~14A — but oil cooling systems and pumps added complexity and were prone to leaks.

---

## Pulse-Generator Designs

### Design #1 — SCR-Based Pulse Generator
<p align="center"><img src="./Design 1 SCR-Type Pulse-Generator/SCR-Type_TMS_1.jpg" alt="SCR switch bottom-right" width="200"/></p>
<p align="center"><em>SCR-switch pulse generator (SCRs are bottom-right. Main capacitor front left. Rectifier behind capacitor. and mineral-oil cooled microwave transformer behid rectifier. </em></p>

The first design I built. It handles very high energy levels well at consistent frequencies/patterns (e.g., the standard 5Hz/10Hz protocols used in early depression-treatment research). Its limitation is pattern flexibility: an SCR/thyristor is a latching switchable diode, so once triggered it fully discharges the capacitor. This steady current decay is gentle on flyback effects but rules out more complex patterns like Theta-Burst (50Hz bursts repeating at 4–5Hz).

### Design #2 — Single-IGBT Pulse Generator
<p align="center"><img src="./Design 2 Single IGBT-Type Pulse-Generator/Gate-Drive_to_IGBTs.jpg" alt="Single-IGBT (Two IGBTs in parallel)" width="200"/></p>
<p align="center"><em>Single IGBT topology (note; two IGBTs in parallel), you can also see gate driver I used before using ones built specifically for the IGBT module</em></p>

Since IGBTs aren't latching, they can be switched on and back off within a short pulse, allowing partial capacitor discharge and enabling patterns like Theta-Burst. The tradeoff is significant flyback voltage when interrupting current through the coil — this destroyed several IGBTs before I addressed it with a robust flyback diode across the coil and snubber capacitors across the IGBT(s) to absorb spikes from parasitic wiring/busbar inductance. HV differential probes are essential here to stay within the switch's voltage rating while tuning.

This design uses a flyback diode across the coil, producing a sharp current rise but a slow fall (as flyback energy dissipates through coil/wire resistance and the diode). It worked well and was relatively simple compared to the two-switch flyback design, but since flyback energy isn't recycled, it's ultimately limited by the 120V/15A charging circuit.

### Design #3 — Two-Switch Flyback IGBT Pulse Generator
<p align="center"><img src="./Design 3 IGBT Two-Switch-Flyback/Two-Switch_Flyback_TMS_Construction.jpg" alt="Construction of Two-Switch Flyback" width="200"/></p>
<p align="center"><em>Two-Switch flyback topology being built. You can see the diodes hanging off to the sides. And gate drives are now on the left, bolted to IGBT modules behind bus-bars.</em></p>

This design recycles more pulse energy than the single-IGBT version and produces both a sharp rising *and* falling current edge, increasing di/dt for a stronger but shorter magnetic field pulse. It noticeably reduced charging-circuit current draw and appears more effective at stimulating muscle/nerve tissue than the single-switch design, even at higher peak current — likely because faster field transitions induce greater current in tissue.
+ For this design it's important to thermally bond both IGBT/switch modules so that they're the same temperature and have the same switching characteristics. For this I bolt them each to opposite sides of an aluminum plate which I attach water-cooling blocks to.
I've been realizing the importance of keeping switching loops tight, and have shortened cabling from capacitor to IGBTs, and shortening cables from IGBTs to diodes.
<p align="center"><img src="Charge-Control And Pulse-Driver Upgrade Photos/03_System_Partial_Photo_01.jpg" alt="System Photo" width="200"/></p>
<p align="center"><em>Photo of the system, from left-to-right; charge-control board, multimeter, main pulse-IGBTs (with many red snubber caps), charge-control IGBT (with single red snubber in front), charge-circuit-rectifier, and main pulse capacitor bank in back </em></p>

### Design #4 — H-Bridge IGBT Pulse Generator (Not Yet Built)
Should double coil pulse current relative to the two-switch flyback topology by enabling current reversal. The complexity of positioning four large IGBT switches and the associated snubbers and diodes close to eachother, with cooling is going to be difficult without adding parasitic inductance that would offset the gains.

---

## Charge-Control Circuit
<p align="center"><img src="Charge-Control And Pulse-Driver Upgrade Photos/08_Charge_Controller_Board_01.jpg" alt="Newly-Built Charge-Controller Board" width="200"/></p>
<p align="center"><em>Newly Built Charge-Control Board with (from bottom to top) Teensy 4.0, ADS8699 high-speed ADC, AD5693 DAC, LM393P Comparator, ADS1115 4ch slow ADC, 5V LDO, and 3.3V buck-converter</em></p>

The new charge-control system seems to be working well enough for calling it version 1!!! After many failed attempts to measure a voltage divider, and my ground plane shifting and making either the teensy and/or ADC shit itself, even after adding digital SPI isolators, I realized.... that's what HV diff probes are for! So ripped out some HV SPI/I2C isolators and I'm now just measuring the output of a Micsig 1300v diff probe. No glithes, no isolators causing signal delays, no fried chips and salsa. Perfect.

The new approach uses a DAC to output a reference voltage into a comparator, which compares it against the output of the Micsig 1300v differential probe. I have probe scale-factors in the teensy code as well as linear calibrations for both ADCs and DAC as well. I have the fast ADC on here as well incase I want to control the charge-circuit via the teensy reading ADC instead of from the comparator + DAC, but for now the comparator + DAC method is working great, so we have some extra/redundant ADCs. A weak pull-down on the DAC output ensures that if the microcontroller glitches or crashes, the DAC voltage falls to zero and the charge circuit fails safely.

<p align="center"><img src="Teensy Charge Control Telemetry Viewer/Interface_Screen_Shots/TMS_Charge_Control_Telem_With_Setpoint_Control.png" alt="Python Telemetry Graph/Display" width="300"/></p>
<p align="center"><em>Screenshot of a python script which displays a realtime graph and telemetry from the serial output of the teensy charge-control. Also added the ability to adjust the charge-setpoint in the python window. You can see the pulse trains as the capacitor discharges, and the charge controller working as it limits the capacitor voltage at the setpoint of (close to) 100V!</em></p>

Above is a screenshot of a python script which listens to voltage values sent over serial from the teensy charge controller and will display a live graph of the capacitor voltage, which is really useful for debugging as it's basically an autoscaling slow oscilliscope. The graph only shows the fast ADC's (ADS8699) calibrated capacitor voltage. It also displays other, slow-speed telemetry as numbers such as the slow ADC's calibrated capacitor voltage, slow ADC's measurement of DAC's output in both raw voltage, and the scaled/calibrated set-voltage it corresponds to (ie. I set the DAC to output a voltage which corresponds to 200V from the Micsig, I can verify that the ADC's measurement reads close enough, like 199.2V) I also added control to adjust the voltage setpoint from this python window which is useful for testing system starting from low voltage to be safe. I think there may be something with the comparator which is causing the cutoff to be slightly below the setpoint; in the screenshot you can see it cutting at ~95V instead of 100V. More calibrations....

## Upgrades to Pulse-Driver system
<p align="center"><img src="Charge-Control And Pulse-Driver Upgrade Photos/09_Pulse_Driver_Board_01.jpg" alt="Upgraded Pulse-Driver Board" width="200"/></p>
<p align="center"><em>Pulse Driver Board with added inputs for RX signals from fiber-optic interface board, level-shifter and fault LED</em></p>

I've made some additions to the pulse-driver control circuit as well; for a while I was just sending pulses from the teensy to the fiber-optic transmitters to fire the IGBT switches, and not reading the signal received from the other fiber line which acknowledges the driver received each pulse, and can indicate faults like a short-circuit. I wired up the fiber RX lines, ran them through a level shifter to 3.3v for the teensy to read and the teensy is now checking that each pulse was ack'd by each gate-driver, and can turn on the big-red fault LED if something's wrong. NOTE: Big-Red Fault LED will blink if the teensy detects a warning; like a an ack pulse was slightly off, or short/long. The LED will latch on if it detects a fault such as a short-circuit. It also prints this data + occasional status out over serial. The teensy code has the ability to control a safety relay to shut off main power to the system if a fault is detected, but I don't have that safety mechanical relay wired in yet, so for now, it just keeps going.

---

## Safety Resources
- [TMS Safety with respect to seizures](https://pmc.ncbi.nlm.nih.gov/articles/PMC7732158/pdf/ndt-16-2989.pdf)
- [General article discussion factors which can decrease seizure-threshold (make it more likely you'll have a seizure)](https://www.wmchealth.org/living-well/7-everyday-factors-that-can-increase-seizure-risk)
- [Concise overview from clinic of seizure risk factors](https://www.midcitytms.com/transcranial-magnetic-stimulation-safety-with-respect-to-seizure-ndt/)


## Useful Resources

- [Online calculator used for Beam-Protocol/10-20 system to calculate position of DLPFC](https://clinicalresearcher.org/F3/calculate.php)
- [Same online calculator, where you enter your measurements](https://clinicalresearcher.org/F3/)
- [Clinical Researcher Software Tools (where I got the two links above) Contains other tools that could be useful for motor-threshold stuff](https://clinicalresearcher.org/software.htm)

## Useful Videos
- [Coil placement and the 10-20 system/beam protocol (video)](https://youtu.be/CKCvAkgdJuY?si=6TRw3_EgRK5eAh5G)
- [Same video as above with added instructions to enter measurements into clinicalresearcher calculator (link above)](https://youtu.be/akp4V5PFb6A?si=kvKB5cpgHWPCvdm8)
- [Good introductory explanation of general TMS](https://www.youtube.com/watch?v=NQHVfF_5rtc)
- [Funny video of the TMS researcher interrupting his speach with TMS](https://www.youtube.com/watch?v=85QKdt8boMA)
- [Researcher showing TMS stimulating muscles directly and muscles through the motor-cortex](https://www.youtube.com/watch?v=JA0q4fVqrFQ)
- [Good Long-Form explanation of someone who runs a TMS clinic showing how they get a patient setup, and perform treatment](https://www.youtube.com/watch?v=WxCunEG2oi0)




## TODO
1. **UPDATE THE SCHEMATICS**
2. **Refine charge-control switch** The charge controller is working well now. But I'm using a rediculously big IGBT to switch just a few tens of amps. Need to find a smaller IGBT/switch which can withstand ~2-3kV.
3. **Build a phase-control rectifier** to replace the HV charge circuit's current rectifier and eliminate the variac, integrating it into the charge system. May need PID-style control of phase/firing angle — or a simpler bang-bang control scheme.
4. **Process gate-driver fault feedback** Reading status/feedback from gate-drivers is working now and has latching/blinking LEDs/indicators to indicate warnings and faults. There is functionality to turn off a safety relay, disconnecting power from a subsystem or whole system which I'll get wired up later.
5. **Add functionality to Pulse-Driver** To run different patterns/protocols, maybe add input (with damn good shielding) so user can press a button to fire a single pulse.
6. **Finally build a tool to map the magnetic field strength & shape of a coil.** My idea is basically put a precise analog hall-effect sensor on some 3d gantry machine like a 3d printer, and have it scan over many 3d points around the coil and create a 3d heat-map of magnetic field strength. Anyone know how to use 3d-printers? and know how to make them scan some end-effector to create a map like this???? I'd love some help!