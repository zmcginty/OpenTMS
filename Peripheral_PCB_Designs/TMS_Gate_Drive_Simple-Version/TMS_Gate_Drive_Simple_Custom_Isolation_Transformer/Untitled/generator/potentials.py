"""Worst-case DC working potentials (V, relative to emitter/VEE side) of the HV nets.
Working voltage = max DC link 2200 V. Two DA2C states: A = Q5 on (6 TVS share the voltage),
B = Q5 off (7 TVS share it). Sense chain is resistive, so its nodes are exact."""
import math
VDC = 2200.0
def pots(cfg):
    p = {'CAUX': VDC}
    for k in range(1, 6): p[f'HVS{k}'] = VDC * (6 - k) / 6
    if cfg == 'A':
        for k in range(1, 6): p[f'CH{k}'] = VDC * (6 - k) / 6
        p['CH6'] = 0.0
    else:  # Q5 off: D40 (SMBJ188A) holds at most its VBR(max) 231 V, the rest is shared by the 6 x 440 V parts
        rest = VDC - 231.0
        for k in range(1, 6): p[f'CH{k}'] = 231.0 + rest * (6 - k) / 6
        p['CH6'] = 231.0
    return p
CFG = [pots('A'), pots('B')]
def dv(a, b): return max(abs(c.get(a, 0.0) - c.get(b, 0.0)) for c in CFG)
def _interp(tab, v):
    if v <= tab[0][0]: return tab[0][1]
    for (v1, d1), (v2, d2) in zip(tab, tab[1:]):
        if v <= v2: return math.exp(math.log(d1) + (math.log(d2) - math.log(d1)) * (math.log(v) - math.log(v1)) / (math.log(v2) - math.log(v1)))
    return tab[-1][1] * v / tab[-1][0]
# IEC 60664-1 Table F.5: PD2 material group II (uncoated) and PD1 (conformal coated per IEC 60664-3)
PD2 = [(50, .6), (100, .71), (160, 1.1), (250, 1.8), (400, 2.8), (630, 4.5), (1000, 7.1), (1600, 11), (2000, 14), (2500, 18), (3200, 22), (4000, 28)]
PD1 = [(50, .04), (100, .1), (160, .25), (250, .56), (320, .75), (400, 1.0), (500, 1.3), (630, 1.8), (800, 2.4), (1000, 3.2), (1250, 4.2), (1600, 5.6), (2000, 7.5), (2500, 10), (3200, 12.5)]
def creep_uncoated(v): return 0.0 if v < 50 else _interp(PD2, v)
def creep_coated(v): return 0.0 if v < 50 else _interp(PD1, v)
HVSET = set(CFG[0]) | set(CFG[1])

def creep_route(v):
    """What the router enforces: full uncoated PD2 above 400 V, coated (PD1) below it.
    Below 400 V the only such spacings are inside the DA2C switch cluster, where the
    SOT-223 pin pitch itself cannot meet the uncoated value, so the board must be coated."""
    return creep_uncoated(v) if v >= 400 else creep_coated(v)
