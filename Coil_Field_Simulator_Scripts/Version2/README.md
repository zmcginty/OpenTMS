To run this script;
    python tms_coil_3d.py --large-single
    
The --large-single option adds a large round coil (12 cm across) next to the figure‑8, so the depth‑vs‑focus trade‑off is visible. Two pages open in your browser, and both are also saved to tms_output/:

coil_3d.html: one 3‑D view per coil, which you can drag to rotate and scroll to zoom. Each view shows the coil windings, shells where the field reaches 25%, 50%, 75% and 100% of the threshold, and a blue plane at the target depth.
coil_depth_focality.html: on the left, how quickly each coil’s field fades with depth. On the right, each coil plotted by depth reached (d½) against how spread out the field is (S½).

Other options:

--style volume draws a soft, cloud‑like heat map instead of distinct shells.
--configs all includes every figure‑8 wiring option.
--normalize none drives every coil at the same dI/dt instead of matching them to the threshold.
--depth 2.5 --threshold 120 changes the target depth and threshold.
--res3d 71 makes the 3‑D shapes smoother, but runs slower and makes the HTML file bigger.
