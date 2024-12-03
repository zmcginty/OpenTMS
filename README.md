This project is an open-source transcranial-magnetic-stimulator, including some code for VERY SIMPLE controls for pulse-firing system and hopefully charge-controller (coming later), but mainly it's for documentation of the system design, janky schematics, some pictures of circuits, some pictures of coil-designs and possibly much more.

To be clear, this is NOT a legitimate medical device and should not be seen as an alternative to going to get legitimate TMS treatment. Any treatment with this janky, built-in-my-garage version of TMS isn't gauranteed to work, or do anything as I have no way to verify that it works such as an MRI. This is a dangerous device involving energy levels more than high enough to stop your heart, kill you and do alot of damage.

I plan on putting both designs of pulse-generators in here and documenting each. 
Design #1; SCR type Pulse-Generator.
    This is the first design of pulse generator I built and it is great for handing rediculous energy levels at consistent frequencies/patterns such as the standard 5 & 10hz protocols used in early depression-treatment protocols. However I ran into limitations when I wanted to try more complex pulse patterns such as "Theta-Burst) which is a short burst of 50hz pulses, repeating at 4-5hz. The SCR (Silicon-Controlled-Rectum-Frier) or thyristor, is a switchable diode which are latching. So for the task of discharging a capacitor through a coil; they completely drain the capacitor, but because of this steady decrease of current, the flyback effect from the TMS coil isn't too bad.

Design #2; IGBT type Pulse-Generator.
    The IGBT based pulse generator allows much more control of energy through the treatment coil. And since IGBT switches aren't latching like the SCR, you can turn them on then back off in short duration pulses, only partially discharging energy storage capacitors. This allows doing pulse patterns such as "Theta-Burst" as mentioned above. However, since you're shutting off the switch as current is flowing through the TMS-coil you're going to have a great deal of flyback to deal with so as not to fry IGBTs with this potentially high reverse voltage spike (This was how I fried many IGBTs). For flyback you'll need one hell of a flyback diode accross the TMS coil, as well as snubber capacitors accross the IGBT(s) to handle the spikes caused by parasitic inductance of wires/cables/bussbars and such. You'll want to get some high-voltage differential oscilliscope probes to measure important voltages, such as voltage accross the IGBT. So that as you're testing and tuning the system, you can stay within the limits of the switch you're using. 
