"""Remove exact-duplicate track segments and the dangling stubs DRC reports."""
import pcbnew, sys, re
b = pcbnew.LoadBoard(sys.argv[1]); seen = {}; kill = []
for t in b.GetTracks():
    if t.GetClass() == 'PCB_VIA': continue
    a, c = (t.GetStart().x, t.GetStart().y), (t.GetEnd().x, t.GetEnd().y)
    k = (t.GetNetname(), t.GetLayer(), min(a, c), max(a, c))
    if k in seen:
        o = seen[k]
        if o.GetWidth() >= t.GetWidth(): kill.append(t)
        else: kill.append(o); seen[k] = t
    else: seen[k] = t
dang = [tuple(map(float, m)) for m in re.findall(r'\[track_dangling\].*?\n.*?\n\s+@\(([\d.]+) mm, ([\d.]+) mm\)', open(sys.argv[2]).read())] if len(sys.argv) > 2 else []
for t in b.GetTracks():
    if t.GetClass() == 'PCB_VIA' or t in kill: continue
    for x, y in dang:
        for p in (t.GetStart(), t.GetEnd()):
            if abs(pcbnew.ToMM(p.x) - x) < 0.01 and abs(pcbnew.ToMM(p.y) - y) < 0.01 and pcbnew.ToMM(t.GetLength()) < 1.5: kill.append(t)
for t in kill: b.Delete(t)
pcbnew.SaveBoard(sys.argv[1], b); print('removed', len(kill))
