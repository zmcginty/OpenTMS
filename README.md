This project is an open-source transcranial-magnetic-stimulator, including some code for VERY SIMPLE controls for pulse-firing system and hopefully charge-controller (coming later), but mainly it's for documentation of the system design, janky schematics, some pictures of circuits, some pictures of coil-designs I've tried as I remember how E&M works lol

I plan on putting both designs of pulse-generators in here and documenting each. 
Design #1; SCR type Pulse-Generator.
    This is the first design of pulse generator I built and it is great for handing rediculous energy levels at consistent frequencies/patterns such as the standard 5 & 10hz protocols used in early depression-treatment protocols. However I ran into limitations when I wanted to try more complex pulse patterns such as "Theta-Burst) which is a short burst of 50hz pulses, repeating at 4-5hz. The SCR (Silicon-Controlled-Rectum-Frier) or thyristor, is a switchable diode which are latching. So for the task of discharging a capacitor through a coil; they completely drain the capacitor, but because of this steady decrease of current, the flyback effect from the TMS coil isn't too bad.

Design #2; IGBT type Pulse-Generator.
    This is a work in progress, but I FINALLY LEARNED HOW NOT TO FRY IGBTs!!! I think... I'm currently testing with some three-phase IGBT modules, where each leg is rated at 3.3kv, 1.2kA(continuous), 2.4kA(pulse). I need about 20-30kA total at 2.5kV total, so I'm currently running two modules in parallel, and plan to run 4-5 total to reach the pulse-current requirement.
