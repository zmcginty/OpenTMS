designs preset that compares six coils side by side:

single: the normal round coil
single_large: a 12 cm round coil
fig8: the standard figure‑8
fig8_small: a figure‑8 with 4 cm wings
fig8_large: a figure‑8 with 12 cm wings
fig8_overlap: a figure‑8 whose wings overlap

To run this script; 
    python tms_coil_3d.py --configs fig8 fig8_overlap --overlap 3

    python tms_coil_3d.py               # 3-D comparison of all six designs
    python tms_coil_fields.py --configs designs             # same designs as flat 2-D maps
    python tms_coil_3d.py --configs fig8 fig8_overlap --overlap 3   # try different overlaps
    python tms_coil_fields.py           # the wiring comparison, same as before
