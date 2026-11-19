"""Mechanical interface, taken from the 1SP0635V mechanical drawing (Description & Application
Manual, Fig. 5), which fits the 130x140 / 190x140 mm 3.3 kV IHM module family incl. FZ1200R33KF2.

Coordinates: mm, origin = left board edge / top board edge, y pointing down, viewed from the
component (top) side.  Dimensioned values are exact; outline corners without a dimension were
scaled from the drawing (about +-0.3 mm) and the outline is kept on or inside the 1SP0635 outline.

Drawing values (datum = horizontal line through the support hole, 3.95 mm above the bottom edge):
  C : 20.04 mm from the left edge, 27.83 mm above datum
  E : C + 38.15 mm, 27.83 + 5.20 mm above datum (6.96 mm below the top edge)
  G : E + 21.00 mm, same height as E
  support hole 3.2 mm: G + 114.4 mm, on the datum;  board 197.10 x 43.94 mm
"""
BOARD_W, BOARD_H = 197.10, 43.94
_DATUM_Y = 43.94 - 3.95                      # datum line, measured from the top edge
_C_X = 20.04; _E_X = _C_X + 38.15; _G_X = _E_X + 21.00
MOUNT = {'X1': (_G_X, 6.96),                    # gate          (M4)
         'X2': (_E_X, 6.96),                    # aux emitter   (M4)
         'X3': (_C_X, 6.96 + 5.20)}             # aux collector (M4)
SUPPORT_HOLE = (_G_X + 114.4, _DATUM_Y, 3.2)    # x, y, diameter (non-plated, on the primary side)
# Board outline (closed polygon).  Notches at the bottom edge and the cut-away region under the
# right end follow the 1SP0635 outline (they clear features of the module / its busbars).
OUTLINE = [(0.0, 0.0), (162.7, 0.0), (162.7, 2.36), (BOARD_W, 2.36), (BOARD_W, BOARD_H),
           (190.4, BOARD_H), (190.4, 40.86), (177.1, 40.86), (167.0, 29.04), (161.9, 26.05),
           (134.2, 20.7), (128.7, 20.7), (128.7, BOARD_H), (100.2, BOARD_H), (100.2, 40.86),
           (69.2, 40.86), (69.2, BOARD_H), (0.0, BOARD_H)]
# Milled isolation slot under the transformer (primary | secondary boundary)
SLOT = (171.0, 4.0, 173.0, 28.0)
