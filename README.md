This project is an open-source transcranial-magnetic-stimulator, including some code for simple control of pulse-firing system as well as a charge-controller. 

To be clear, this is NOT a legitimate medical device and should not be seen as an alternative to going to get legitimate TMS treatment. Any treatment with this janky, built-in-my-garage version of TMS isn't gauranteed to work, or do anything as I have no way to verify that it works such as an MRI. This is a dangerous device involving energy levels more than high enough to stop your heart, kill you and do alot of damage.


# TMS Coils
+ I primarily use two types of coils; single coils, and figure-8 coils. Both single and fig-8 coils can be either single-layer or multi-layer flat coils with no core (use air as core). I basically just used the same general architecture in these designs from various research papers and what little information released by tms-coil manufacturers. Below is an image of a figure-8 CloudTMS coil which influenced the coils I built.

<center><img src="./Example_Images/CloudTMS_Coil.png" alt="drawing" width="200"/></center>

+ Since I've transistioned to higher-speed pulse rates and higher pulse energies I've found it necessary to have active cooling for the coils and have tried a few different methods. One of my favorite designs is similar to the CloudTMS coil using hollow copper tube (sold for air-conditioning systems), insulating the tube with some kind of heat-shrink. PTFE heat-shrink works quite well because it's a good dielectric, thin-wall even after shrinking, withstands high-temp, and doesn't allow coil to move as much compared to other thicker heat-shrink. Note; PTFE heat-shrink requires pretty high temp to fully shrink. 
    - For the coils I've made, I buy 4mm OD, 3mm ID copper tube in 5m sections, and buy 4.5mm 4:1 PTFE heat-shrink in bulk. To get any kind of long section of heat-shrink onto such a long tube takes creativity, patience and compressed air to create an air-cushion between the copper tube and heat-shrink to easily push it on. Then I'll use a janky, custom coil-winding jig to wind a coil in either single layer or multi-layer(Note multi-layer is just winding randomly around a bobbin, not stacking multiple single layers... I'm not that fancy yet, but they seem to work well). You'll want to put something close to the ID of the copper tube to keep it from collapsing during the first few windings, and pull it out after winding's done. I find that weedwacker cable works well for this. Hold coils together with some tape while you pot them in some epoxy. 

+ Potting these coils in epoxy helps greatly with general stability of coil and noise, it keeps the coils in place when they want to fight eachother. I've tried holding coils together with hot-glue which does work, but those coils are very loud which doesn't inspire much confidence increasing power.. 

## Coil Cooling
+ These coils with hollow copper tube are meant to have cooland pumped through them to cool them. For a long time, I used mineral-oil as a coolant which is pretty thin for oil, but much thicker than water. To achieve proper cooling (maintain ~40C) I needed about 150-200psi to push enough through the small ID of the coil. When running higher energy levels, I added a water-chiller and heat-exchanger to further cool the oil before going to the coil. 
+ Recently I found that diaphram pumps are electrically insulated (to some extent) through the diaphram from the fluid they're pumping, so I found that using a diaphram pump and distilled water as coolant is much more effective and simple. Though there is a risk of the water/coolant being at high-voltage. 

## Materials used to build Coils
+ Hollow copper tube used to wind water-cooled coils; Cheap and easy to find copper tube typically used for refridgeration. https://www.amazon.com/dp/B082FDVNC5?lv=shuf&channelId=500&plpRedirect=mhFallback&th=1

+ Fiberglass resin sheet used for application-side of coil; They're sturdy, and are generally good electrical insulators, and seem to be permeable to magnetic fields (I saw in a commercial coil I took apart they used what looked like FR4 fiberglass, like in a circuit board). https://www.amazon.com/dp/B0DYT28W8W?lv=shuf&channelId=500&plpRedirect=mhFallback&th=1
    
+ Epoxy potting compound; MAX EPC. This is easy to find and relatively cheap. It's a good electrical insulator, and decent thermal conductor. If you're building a coil from hollow copper tube and running coolant through the conductor itself, you can use MAX MCR, which has lower thermal conductivity. https://www.amazon.com/dp/B07PMTMKZQ?lv=shuf&channelId=500&plpRedirect=mhFallback

# IGBT switches: 
My current switch of choice is the Infineon/Eupec FZ1200R33KF2, why? because they're cheap and they can handle alot of power. I've found them on ebay ranging from $100 to $400 each, and I've put 8kA pulse through just one. Just make sure you have enough snubber capacitors/circuitry to keep the voltage accross them less than 3.3kV. 

## Gate-Drivers
With this IGBT, I've used two gate drivers from Power Integrations; 
+ 2SC0535T2G0-33; This did its job but doesn't have features like active clamping to protect the IGBT from over-voltage during turn-off spikes. You also have to build up a PCB with capacitors and gate turn-on and turn-off resistors.
+ 1SD418F2-FZ1200R33KF2; This is made specifically for the FZ1200R33KF2 IGBT, so it just bolts onto the pads. You just have to feed the gate-driver with 15VDC, I'm using a 1A supply. These gate drivers have two fiber-optic ports; one input signal, one output/status signal. So I made some PCBs with the correct HFBR fiber-optic transmitter(AFBR-1624Z) and receiver(AFBR-1624Z) modules which is driven by the Teensy 4.1 (through 3.3V->5V level shifter).


# Measurement System
### Differential Voltage Measurement
When running IGBT based Pulse-Generators it's important to monitor the voltages accross each IGBT's collector and emitter pins (Vce) so you don't exceed its rating. To be safe, it's best to keep some safety margin here; I try to stay below 2.5kV for my 3.3kV rated IGBTs. To measure this I use a basic oscilliscope and, since these IGBTs are switching high-side voltage, you'll have to use a high-voltage differential probe. The HV Diff Probes I use are Micsig DP20003 High Voltage Differential Probe 5600V, 100MHz. I measure voltage of the main capacitor bank using a similar HV Diff Probe, but lower voltage (1300V).
### Current Measurement
To measure current I've tried a few types of measurement, initially tried a shunt resistor, but never got a signal I trusted.I found the two below to be the best;
1. Rogowski-Coil; Great for measuring really high current, there's no iron core to saturate, they can measure AC or DC current. But you can't really find calibrated probes used on Ebay, and to get a useful signal on a scope, you need a calibrated integrator circuit. So I ended up spending alot on a calibrarted rogowski coil from https://powertekuk.com. I got one like this; https://powertekuk.com/cwtmini
2. Current-Transformer (CT); I use a Tektronix A621 AC Current Probe, which says it can measure up to 2kA. These can be found pretty cheap on Ebay ($100 - $500). And though it says it can only measure 2kA, I've seen it agree with the Powertek Rogowski coil up to about 8kA.


# Charging Circuit 
I've used the same general janky, open-loop charging circuit for a while; this consists of mains power going into a variable-transformer which goes to a microwave-oven-transformer to step it up to higher voltage, which is then rectified with a full-bridge rectum-fryer. This rectified DC then goes to the large discharhe capacitor which is discharged through the coil by a high-side switch (either latching-SCR or non-latching-IGBT).

## Microwave-Oven Transformers
For the current two-switch flyback pulse-generator I'm using two relatively-matched Microwave-Oven-Transformers (MOTs). I do this by checking the voltage and phase of both transformers are pretty close to eachother. (TODO: insert measurements, or resource)
With the charge circuit drawing about 15A at 120V, I've found that two matched transformers only need air-cooling to keep them below about 60C.
Past designs have used a single MOT with low-pressure mineral-oil cooling, which worked well up to about 14A. But mineral-oil cooling systems and pumps are complicated and leak everywhere.

# Pulse-Generator Designs
### Design #1; SCR type Pulse-Generator.
This is the first design of pulse generator I built and it is great for handing rediculous energy levels at consistent frequencies/patterns such as the standard 5 & 10hz protocols used in early depression-treatment protocols. However I ran into limitations when I wanted to try more complex pulse patterns such as "Theta-Burst) which is a short burst of 50hz pulses, repeating at 4-5hz. The SCR (Silicon-Controlled-Rectum-Frier) or thyristor, is a switchable diode which are latching. So for the task of discharging a capacitor through a coil; they completely drain the capacitor, but because of this steady decrease of current, the flyback effect from the TMS coil isn't too bad.

### Design #2; Single IGBT type Pulse-Generator.
The IGBT based pulse generator allows much more control of energy through the treatment coil. And since IGBT switches aren't latching like the SCR, you can turn them on then back off in short duration pulses, only partially discharging energy storage capacitors. This allows doing pulse patterns such as "Theta-Burst" as mentioned above. However, since you're shutting off the switch as current is flowing through the TMS-coil you're going to have a great deal of flyback to deal with so as not to fry IGBTs with this potentially high reverse voltage spike (This was how I fried many IGBTs). For flyback you'll need one hell of a flyback diode accross the TMS coil, as well as snubber capacitors accross the IGBT(s) to handle the spikes caused by parasitic inductance of wires/cables/bussbars and such. You'll want to get some high-voltage differential oscilliscope probes to measure important voltages, such as voltage accross the IGBT. So that as you're testing and tuning the system, you can stay within the limits of the switch you're using. 

**NOTE:** this design used a flyback diode accross the coil, so the coil current waveform has a sharp rising edge, but a slow falling edge as the flyback current is dissipated through the coil+resistance of the wires and the diode. The single IGBT setup seemed to work well and was relatively simple compared to the two-switch flyback, but due to not recycling the flyback energy in the coil, I found myself limited by the 120V 15A circuit charging the capacitor bank.

### Design #3; Two-Switch Flyback IGBT Pulse Generator.
Compared to the single IGBT pulse gen, the two switch design recycles more of the pulse energy than the single IGBT. The two-switch setup also has a sharp rising and falling edge of current to the coil, which increases the di-dt which results in a stronger but shorter magnetic field. This setup seems to increase efficiency so my charging circuit current draw decreased significantly. This setup seems to be more effective at stimulating atleast muscles/nerves than the single switch IGBT running at higher peak pulse current. I believe this is because the faster edge rates cause a faster change in magnetic field which (I think) induces greater currents in muscle/tissue. 

### Design #4; H-Bridge Switch topology IGBT Pulse Generator (Not Built yet)
This design should double the coil pulse current compared to the two-switch flyback topology, because of the ability to reverse the current flow. Though this setup feels quite complicated in terms of how to position 4 IGBT switches and needed snubber capacitors so that the added parasitic inductance doesn't decrease the energy capability of the system overall. 

## Useful Resources
+ Coil placement and the 10-20 system/beam protocol; 
https://youtu.be/CKCvAkgdJuY?si=f4i1zZF6m_ImrpPf


### Update: I'm building a charge-control circuit!!!
Starting the the same design seen in the Kicad schematics; the charge control circuit is a safety critical circuit which controls charging the main pulse capacitors. This circuit is an improved version of past control circuits using arduino and an ADC which wasn't able to sample (divided) capacitor voltage consistently. To be more fail-safe than the last version, this version uses a DAC outputting an analog voltage to a comparator which compares the DAC output voltage to the (voltage-divided) capacitor voltage. This way the arduino/teensy microcontroller doesn't need to sample quickly or at consistent times, and if the microcontroller glitches or dies, I'm going to have a weak pull-down on the DAC to be sure the DAC voltage falls to 0 making the charge circuit fail safely.

This circuit has been tested though my voltage-divider is picking up quite a bit of noise.... which is everywhere since this is basically a small EMP generator. So I'll try some shielded cable and filtering to reduce this noise while maintaining good measurements. I've confirmed it triggers the comparator output as expected but have not connected the comparator's output to drive a switch to disconnect the charging supply.

I'm also trying some ADCs since I'd like the main controller (a teensy 4.1) to be able to know the capacitor's voltage without having to worry about having the teensy doing anything super safety critical. 

### TODO:

1. Build a charge-control circuit. I don't trust a uC to do this job. Going to use a comparator, compare to a DAC output from uC, so if uC hangs DAC falls to 0 and turns off charge circuit.
2. Build phase-control recitifier to replace HV charge circuit rectifier, this will also get rid of variac and be tied into charge system. Might need some fancy PID... thingy to control phase/firing angle to let more/less current through to charge caps. Or say fuck it and just bang-bang control it.
3. Process feedback signal from Gate-Drivers (fiber-optic RX) to tell if we're missing pulses. What would we do if we miss pulses? I dunno yet..... shut off another redundant switch to shut off charge circuit? Also maybe "crowbar" the capacitor-bank? 
