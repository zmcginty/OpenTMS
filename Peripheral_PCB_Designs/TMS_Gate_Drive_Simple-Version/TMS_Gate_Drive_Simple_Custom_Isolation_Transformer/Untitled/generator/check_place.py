"""Courtyard overlap and board-bounds check on the placed board."""
import pcbnew,sys,itertools
from shapely.geometry import box
b=pcbnew.LoadBoard(sys.argv[1])
sys.path.insert(0,__import__('os').path.dirname(__file__)); from mech import OUTLINE
from shapely.geometry import Polygon; BP=Polygon(OUTLINE)
items=[]
for fp in b.GetFootprints():
    L=pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd
    bb=fp.GetCourtyard(L).BBox() if fp.GetCourtyard(L).OutlineCount() else fp.GetBoundingBox(False,False)
    x1,y1=pcbnew.ToMM(bb.GetX())-100,pcbnew.ToMM(bb.GetY())-100; x2,y2=x1+pcbnew.ToMM(bb.GetWidth()),y1+pcbnew.ToMM(bb.GetHeight())
    items.append((fp.GetReference(),box(x1,y1,x2,y2),'T' if fp.GetReference()[:1] in 'JX' else ('B' if fp.IsFlipped() else 'F')))
bad=0
for (a,ga,sa),(c,gc,sc) in itertools.combinations(items,2):
    if sa!='T' and sc!='T' and sa!=sc: continue
    if ga.intersection(gc).area>0.05: bad+=1; print('OVERLAP',a,c,round(ga.intersection(gc).area,2))
for r,g,_ in items:
    x1,y1,x2,y2=g.bounds
    if r.startswith('U3') or r.startswith('U18'): continue
    if not BP.contains(g): bad+=1; print('OUTSIDE',r,[round(v,1) for v in g.bounds])
print('issues',bad)
