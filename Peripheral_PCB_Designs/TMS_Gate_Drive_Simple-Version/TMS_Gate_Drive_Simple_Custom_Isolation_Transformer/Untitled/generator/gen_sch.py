import uuid, math, os, sys, collections
sys.path.insert(0,os.path.dirname(__file__))
from circuit import parts, RAILS
OUT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJ='fz1200_gate_driver'
U=lambda: str(uuid.uuid4())
ROOT=U(); SHEETS={'power':(U(),2,'Power and isolation'),'control':(U(),3,'Fiber, protection logic'),'output':(U(),4,'Output stage, AAC, Vce sense')}
# ---------- symbols
F='(font (size 1.27 1.27))'
def poly(pts,w=0.254,fill='none'):
    return '(polyline (pts '+' '.join(f'(xy {x} {y})' for x,y in pts)+f') (stroke (width {w}) (type default)) (fill (type {fill})))'
def rect(x1,y1,x2,y2,fill='background'):
    return f'(rectangle (start {x1} {y1}) (end {x2} {y2}) (stroke (width 0.254) (type default)) (fill (type {fill})))'
def pin(num,name,x,y,rot,typ='passive',ln=2.54,hide=False):
    return dict(num=str(num),name=name,x=x,y=y,rot=rot,typ=typ,ln=ln,hide=hide)
SYMS={}
def sym(name,pins,gfx,prefix,names=False,power=False):
    SYMS[name]=dict(pins=pins,gfx=gfx,prefix=prefix,names=names,power=power)
sym('R',[pin(1,'~',0,3.81,270,ln=1.27),pin(2,'~',0,-3.81,90,ln=1.27)],[rect(-1.016,2.54,1.016,-2.54,'none')],'R')
sym('PTC',[pin(1,'~',0,3.81,270,ln=1.27),pin(2,'~',0,-3.81,90,ln=1.27)],[rect(-1.016,2.54,1.016,-2.54,'none'),poly([(-1.8,-2.2),(-1.2,-2.2),(1.8,2.2)])],'F')
sym('C',[pin(1,'~',0,3.81,270,ln=2.794),pin(2,'~',0,-3.81,90,ln=2.794)],[poly([(-2.032,0.762),(2.032,0.762)],0.508),poly([(-2.032,-0.762),(2.032,-0.762)],0.508)],'C')
tri=poly([(-1.27,1.27),(-1.27,-1.27),(1.27,0),(-1.27,1.27)],0.254)
sym('D_S',[pin(1,'K',3.81,0,180),pin(2,'A',-3.81,0,0)],[tri,poly([(1.778,1.016),(1.778,1.27),(1.27,1.27),(1.27,-1.27),(0.762,-1.27),(0.762,-1.016)])],'D')
sym('D_REF',[pin(1,'K',3.81,0,180),pin(2,'A',-3.81,0,0)],[tri,poly([(1.27,1.27),(1.27,-1.27)])],'D')
sym('D_SOT',[pin(3,'K',3.81,0,180),pin(1,'A',-3.81,0,0)],[tri,poly([(1.27,1.27),(1.27,-1.27)])],'D')
sym('D_TVS',[pin(1,'K',3.81,0,180),pin(2,'A',-3.81,0,0)],[tri,poly([(0.762,1.778),(1.27,1.27),(1.27,-1.27),(1.778,-1.778)])],'D')
sym('D_TVSBI',[pin(1,'A1',-5.08,0,0),pin(2,'A2',5.08,0,180)],[poly([(-2.54,1.27),(-2.54,-1.27),(0,0),(-2.54,1.27)]),poly([(2.54,1.27),(2.54,-1.27),(0,0),(2.54,1.27)]),poly([(-0.5,1.778),(0,1.27),(0,-1.27),(0.5,-1.778)])],'D')
sym('NPN',[pin(1,'B',-5.08,0,0),pin(3,'C',2.54,5.08,270),pin(2,'E',2.54,-5.08,90)],[poly([(0,1.905),(0,-1.905)],0.508),poly([(0,0.635),(2.54,2.54)]),poly([(0,-0.635),(2.54,-2.54)]),poly([(1.2,-2.2),(2.1,-2.1),(1.7,-1.3)]),'(circle (center 1.27 0) (radius 2.8) (stroke (width 0.254) (type default)) (fill (type none)))'],'Q')
mosgfx=[poly([(-0.508,1.905),(-0.508,-1.905)],0.254),poly([(0.254,2.2),(0.254,0.9)],0.508),poly([(0.254,0.65),(0.254,-0.65)],0.508),poly([(0.254,-0.9),(0.254,-2.2)],0.508),poly([(0.254,1.5),(2.54,1.5),(2.54,2.54)]),poly([(0.254,-1.5),(2.54,-1.5),(2.54,-2.54)]),poly([(0.254,0),(2.54,0),(2.54,-1.5)]),poly([(0.6,0),(1.4,0.5),(1.4,-0.5),(0.6,0)]),'(circle (center 1.27 0) (radius 2.8) (stroke (width 0.254) (type default)) (fill (type none)))']
sym('NMOS_SOT',[pin(1,'G',-5.08,0,0),pin(3,'D',2.54,5.08,270),pin(2,'S',2.54,-5.08,90)],mosgfx,'Q')
sym('NMOS_SOT223',[pin(1,'G',-5.08,0,0),pin(2,'D',2.54,5.08,270),pin(3,'S',2.54,-5.08,90)],mosgfx,'Q')
sym('NMOS_DPAK',[pin(1,'G',-5.08,0,0),pin(2,'D',2.54,5.08,270),pin(3,'S',2.54,-5.08,90)],mosgfx,'Q')
def box(name,left,right,prefix='U',w=12.7,title=None):
    h=max(len(left),len(right))*2.54+2.54
    top=h/2; ps=[]; 
    for side,lst,x,rot in (('L',left,-(w/2+2.54),0),('R',right,w/2+2.54,180)):
        y=round((len(lst)-1)*1.27/1.27)*1.27
        for i,(n,nm,t) in enumerate(lst):
            if n is None: continue
            ps.append(pin(n,nm,x,round(y-i*2.54,3),rot,t))
    g=round(max(len(left),len(right))*1.27+1.27,2)
    gg=[rect(-w/2,g,w/2,-g)]
    sym(name,ps,gg,prefix,names=True); SYMS[name]['h']=g
I='input';O='output';PI='power_in';PO='power_out';PS='passive';NC='no_connect'
box('UCC25800',[(8,'VCC',PI),(2,'DIS/FLT',I),(1,'SYNC',I),(5,'RT',PS),(4,'OC/DT',I)],[(7,'SW',O),(3,'VREG',PO),(6,'GND',PI),(9,'EP',PI)],w=17.78)
box('XFMR',[(1,'P1',PS),(2,'P2',PS)],[(3,'S1',PS),(4,'CT',PS),(5,'S2',PS)],'T',w=12.7)
box('XFMR_PP',[(1,'PA',PS),(2,'PCT',PS),(3,'PB',PS)],[(4,'S15',PS),(5,'S0',PS),(6,'S10',PS)],'T',w=12.7)
box('SN6507',[(3,'VCC',PI),(4,'EN/UVLO',I),(5,'DC',I),(6,'SR',I),(7,'CLK',I),(8,'SS/ILIM',PS)],[(1,'SW1',O),(10,'SW2',O),(2,'GND',PI),(9,'GND',PI),(11,'EP',PI)],w=17.78)
box('LDO',[(1,'IN',PI),(2,'GND',PI)],[(3,'OUT',PO)],w=12.7)
box('G2',[(1,'A',I),(2,'B',I),(5,'VCC',PI),(3,'GND',PI)],[(4,'Y',O)],w=10.16)
box('G3',[(1,'A',I),(3,'B',I),(6,'C',I),(5,'VCC',PI),(2,'GND',PI)],[(4,'Y',O)],w=10.16)
box('BUF',[(2,'A',I),(5,'VCC',PI),(3,'GND',PI)],[(4,'Y',O)],w=10.16)
box('BUF_N',[(2,'A',I),(5,'VCC',PI),(3,'GND',PI)],[(4,'Y',O)],w=10.16)
box('G74',[(1,'CLK',I),(2,'D',I),(6,'~{CLR}',I),(7,'~{PRE}',I),(8,'VCC',PI),(4,'GND',PI)],[(5,'Q',O),(3,'~{Q}',O)],w=12.7)
box('TLC555',[(2,'TRIG',I),(4,'RESET',I),(5,'CONT',PS),(8,'VCC',PI),(1,'GND',PI)],[(3,'OUT',O),(6,'THR',I),(7,'DIS',PS)],w=12.7)
box('CMP2',[(3,'IN1+',I),(2,'IN1-',I),(5,'IN2+',I),(6,'IN2-',I),(8,'VCC',PI),(4,'GND',PI)],[(1,'OUT1',O),(7,'OUT2',O)],w=12.7)
box('HFBR_RX',[(3,'VCC',PI),(2,'GND',PI)],[(1,'VO',O),(4,'RL',PS)],'U',w=12.7)
box('HFBR_TX',[(1,'A',PS),(2,'K',PS)],[],'U',w=12.7)
box('IXDN630',[(1,'VCC',PI),(2,'IN',I),(3,'GND',PI)],[(4,'OUT',O),(5,'NC',NC)],w=12.7)
box('DUAL_NP',[(2,'G1 (N)',I),(1,'S1 (N)',PS),(4,'G2 (P)',I),(3,'S2 (P)',PS)],[(7,'D1',PS),(8,'D1',PS),(5,'D2',PS),(6,'D2',PS)],'Q',w=15.24)
box('UCC27511',[(1,'VDD',PI),(6,'IN+',I),(5,'IN-',I),(4,'GND',PI)],[(2,'OUTH',O),(3,'OUTL',O)],w=12.7)
box('BAT54S',[(1,'A',PS),(2,'K',PS)],[(3,'COM',PS)],'D',w=7.62)
box('BAT54C',[(1,'A1',PS),(2,'A2',PS)],[(3,'K',PS)],'D',w=7.62)
box('CONN5',[(i,str(i),PS) for i in range(1,6)],[],'J',w=5.08)
box('CONN2',[(i,str(i),PS) for i in range(1,3)],[],'J',w=5.08)
box('JUMPER3',[(i,str(i),PS) for i in range(1,4)],[],'JP',w=5.08)
box('TP',[(1,'',PS)],[],'TP',w=2.54)
box('TERM',[(1,'M4',PS)],[],'X',w=5.08)
for p in SYMS: pass
for rail in RAILS:
    low=rail in('PGND','VEE')
    g=[poly([(0,0),(0,1.27 if not low else -1.27)]),poly([(-1.27,1.27),(1.27,1.27)],0.3)] if not low else [poly([(-1.27,-1.27),(1.27,-1.27),(0,-2.54),(-1.27,-1.27)],0.3)]
    sym('P_'+rail,[pin(1,rail,0,0,90 if low else 270,PI,0,True)],g,'#PWR',power=True)
sym('PWR_FLAG',[pin(1,'pwr',0,0,90,PO,0,True)],[poly([(0,0),(0,1.27),(-1.016,1.905),(0,2.54),(1.016,1.905),(0,1.27)])],'#FLG',power=True)
def sym_sexp(name,prefix_lib=''):
    s=SYMS[name]
    nm=(prefix_lib+name)
    head=f'(symbol "{nm}" '+('(power) ' if s['power'] else '')
    head+='(pin_names (offset 0.5)) ' if s['names'] else '(pin_names hide) '
    head+=('(in_bom no) (on_board no) ' if s['power'] else '(in_bom yes) (on_board yes) ')
    pr=(f'(property "Reference" "{s["prefix"]}" (at 2.54 0 0) (effects {F} (justify left)))'
        f'(property "Value" "{name}" (at 2.54 -2.54 0) (effects {F} (justify left)))'
        f'(property "Footprint" "" (at 0 0 0) (effects {F} hide))(property "Datasheet" "" (at 0 0 0) (effects {F} hide))')
    g=f'(symbol "{name}_0_1" {" ".join(s["gfx"])})'
    ps=''.join(f'(pin {p["typ"]} line (at {p["x"]} {p["y"]} {p["rot"]}) (length {p["ln"]})'+(' hide' if p['hide'] else '')+f'(name "{p["name"]}" (effects {F}))(number "{p["num"]}" (effects {F})))' for p in s['pins'])
    return head+pr+g+f'(symbol "{name}_1_1" {ps})'+')'
# library file
with open(f'{OUT}/igbt_drv.kicad_sym','w') as f:
    f.write('(kicad_symbol_lib (version 20220914) (generator gen_sch)\n'+'\n'.join(sym_sexp(n) for n in SYMS)+'\n)\n')
open(f'{OUT}/sym-lib-table','w').write('(sym_lib_table\n  (lib (name "igbt_drv")(type "KiCad")(uri "${KIPRJMOD}/igbt_drv.kicad_sym")(options "")(descr "Project symbols"))\n)\n')
open(f'{OUT}/fp-lib-table','w').write('(fp_lib_table\n  (lib (name "igbt_drv")(type "KiCad")(uri "${KIPRJMOD}/igbt_drv.pretty")(options "")(descr "Project footprints"))\n)\n')
# ---------- layout
LAY={}
def G(rf,x,y,rot=0): LAY[rf]=(x,y,rot)
def row(refs,x,y,dx=15.24,rot=0):
    for i,r in enumerate(refs): G(r,x+i*dx,y,rot)
def grid(refs,x,y,dx,dy,n,rot=0):
    for i,r in enumerate(refs): G(r,x+(i%n)*dx,y+(i//n)*dy,rot)
# POWER
G('J1',38.1,63.5); G('J2',38.1,91.44); G('F1',66.04,63.5,90)
row(['D1','D2'],88.9,63.5,15.24,90); row(['C1','C2','C3'],88.9,86.36,15.24)
G('U1',170.18,78.74); grid(['R1','R2','R3','R4','C4'],134.62,114.3,15.24,0,5)
row(['C5','C6'],215.9,66.04,15.24,90); G('T1',264.16,78.74)
grid(['D3','D4','D5','D6'],304.8,60.96,25.4,20.32,2,0)
grid([f'C{i}' for i in range(10,13)]+['C22'],43.18,162.56,15.24,0,7)
grid([f'C{i}' for i in range(16,19)]+['C23'],43.18,203.2,15.24,0,7)
G('U2',205.74,175.26); grid(['C24','C25','C26'],241.3,170.18,15.24,0,3)
grid(['TP1','TP2','TP3','TP4'],330.2,170.18,25.4,12.7,2)
# CONTROL
G('U3',43.18,63.5); row(['R10','C30'],78.74,50.8,15.24,0); row(['R11','C31'],78.74,86.36,15.24,0)
G('U4',134.62,55.88); G('U5',134.62,86.36); row(['R12','C32'],172.72,50.8,15.24); G('U6',215.9,60.96)
G('U9',266.7,60.96); G('U7',223.52,104.14); G('U8',276.86,104.14)
G('U10',335.28,58.42); G('U11',335.28,93.98); G('U12',383.54,93.98)
G('U13',276.86,142.24); G('U14',223.52,142.24); G('U15',335.28,139.7); G('U16',383.54,139.7)
G('U17',46,134.62); row(['R13','C33','C34'],86.36,124.46,15.24); G('Q1',171.72,132.08,0); G('R14',171.72,114.3); G('U18',175.26,152.4)
G('JP1',38,160); G('R27',66,160)
G('U19',134.62,195.58); G('U20',284.48,195.58)
grid(['D10','R15','C35','R16','R17','C36','R18','R19'],38.1,208.28,15.24,0,8)
grid(['R20','C37','D11'],180.34,187.96,15.24,0,3)
grid(['R21','R22','R23','R24','R25','R26'],325.12,187.96,13,0,6); row(['D13','R28','D14','R29'],325.12,222,20,0)
grid([f'C{i}' for i in range(60,76)],38.1,251.46,15.24,0,16)
grid(['TP5','TP6'],340,250,20,0,2)
# OUTPUT
G('U21',71.12,66.04); row(['C49','C50','C51'],22.86,48.26,15.24); row(['Q7','R60','R61'],22.86,83.82,15.24); G('D20',83.82,83.82); row(['R62','R63','R64'],96.52,76.2,12.7)
grid(['R40','R41'],137.16,53.34,15.24,0,2)
G('U22',55.88,127); row(['C52','C53'],27.94,104.14,15.24); row(['R44','R45','R46'],86.36,114.3,15.24)
G('Q2',111.76,152.4,0); grid(['R47','R48'],137.16,114.3,15.24,0,2)
G('Q3',109.22,196.5); G('R51',78.74,190.5); G('R52',137.16,172.72); 
G('Q4',198.12,144.78,0); row(['R53','R54'],177,190.5,15.24); G('D22',243.84,185.42)
G('X1',289.56,63.5); G('X2',322.58,63.5); G('X3',383.54,40.64)
row(['D37','D38'],220.98,53.34,22.86,90); row(['D39'],220,78.74,0,0); row(['R57','C54','C55','C56'],254,104.14,15.24)
row([f'D{i}' for i in range(30,36)],162.56,233.68,19.05,180)
row(['D36','R55','R56'],289.56,233.68,20.32,90); G('TP7',345,233.68)
row([f'R{i}' for i in range(30,36)],160,264,19.05,90); row(['R36','D12','R37','C38'],180,165,22,0)
row(['D40'],160,215,0,180); G('Q5',40,222); row(['D41','C57'],65,232,18,90); row(['R58','D42'],30,252,20,90); G('Q6',70,262)
row(['D43'],95,252,0,0); row(['D44','R59','C58'],95,272,17,0); G('U23',30,280); G('C76',55,280)
# ---------- instantiate
TRANS=lambda sx,sy,x,y,r:(lambda c,s:(round(sx+x*c-y*s,3),round(sy-(x*s+y*c),3)))(round(math.cos(math.radians(r))),round(math.sin(math.radians(r))))
def outward(pinrot,symrot):  # pin rot points into the body; returns unit (dx,dy) on sheet going away
    a=(pinrot+180+symrot)%360
    return {0:(1,0),90:(0,-1),180:(-1,0),270:(0,1)}[a]
inst=[]
for p in parts:
    x,y,r=LAY[p['ref']]
    p['_pos']=(x,y,r)
    inst.append(p)
pinpos={}
for p in inst:
    x,y,r=p['_pos']
    for q in SYMS[p['sym']]['pins']:
        pinpos[(p['ref'],q['num'])]=dict(pos=TRANS(x,y,q['x'],q['y'],r),out=outward(q['rot'],r),q=q)
netsheet=collections.defaultdict(set); netpins=collections.defaultdict(lambda: collections.defaultdict(list))
for p in inst:
    for pn,n in p['nets'].items():
        if n: netsheet[n].add(p['sheet']); netpins[n][p['sheet']].append((p['ref'],pn))
out={s:[] for s in SHEETS}
wires={s:[] for s in SHEETS}; ends={s:set() for s in SHEETS}
pts_by_sheet=collections.defaultdict(dict)
for k,v in pinpos.items():
    sh=[p for p in inst if p['ref']==k[0]][0]['sheet']
    pts_by_sheet[sh][v['pos']]=k
def seg_free(sheet,a,b,own):
    # segments may not pass over any pin of another part nor collinear-overlap existing wires
    for pos,k in pts_by_sheet[sheet].items():
        if k in own: continue
        if min(a[0],b[0])-0.05<=pos[0]<=max(a[0],b[0])+0.05 and min(a[1],b[1])-0.05<=pos[1]<=max(a[1],b[1])+0.05: return False
    for s,e in wires[sheet]:
        if a[0]==b[0]==s[0]==e[0] and max(min(a[1],b[1]),min(s[1],e[1]))<min(max(a[1],b[1]),max(s[1],e[1])): return False
        if a[1]==b[1]==s[1]==e[1] and max(min(a[0],b[0]),min(s[0],e[0]))<min(max(a[0],b[0]),max(s[0],e[0])): return False
    return True
def add_wire(sheet,a,b):
    if a!=b: wires[sheet].append((a,b)); out[sheet].append(f'(wire (pts (xy {a[0]} {a[1]}) (xy {b[0]} {b[1]})) (stroke (width 0) (type default)) (uuid {U()}))')
def textnote(sheet,t,x,y,size=1.8):
    out[sheet].append(f'(text "{t}" (at {x} {y} 0) (effects (font (size {size} {size})) (justify left bottom)) (uuid {U()}))')
ncnt=[0]
def label(sheet,net,pos,d,force=False):
    a={(1,0):0,(0,-1):90,(-1,0):180,(0,1):270}[d]; j='left' if d in((1,0),(0,-1)) else 'right'
    if force or len(netsheet[net])>1:
        out[sheet].append(f'(global_label "{net}" (shape bidirectional) (at {pos[0]} {pos[1]} {a}) (fields_autoplaced) (effects {F} (justify {j})) (uuid {U()}))')
    else:
        out[sheet].append(f'(label "{net}" (at {pos[0]} {pos[1]} {a}) (fields_autoplaced) (effects {F} (justify {j} bottom)) (uuid {U()}))')
def inst_sym(sheet,lib,ref,val,x,y,rot,fp='',extra=None,pins=(),hidden=False,dnp=False):
    sid=SHEETS[sheet][0]
    hh=SYMS[lib].get('h')
    if hh: rx,ry=(x-6,y-hh-3.2)
    else: rx,ry=(x+3.2,y-1.3) if rot in(0,180) else (x-4,y-4.5)
    props=(f'(property "Reference" "{ref}" (at {rx} {ry} 0) (effects {F} (justify left)'+(' hide' if (hidden or ref.startswith('#')) else '')+'))'
           f'(property "Value" "{val}" (at {rx} {ry+2.5} 0) (effects (font (size 1.1 1.1)) (justify left)))'
           f'(property "Footprint" "{fp}" (at {x} {y} 0) (effects {F} hide))(property "Datasheet" "" (at {x} {y} 0) (effects {F} hide))')
    for k,v in (extra or {}).items():
        props+=f'(property "{k}" "{v}" (at {x} {y} 0) (effects {F} hide))'
    ps=''.join(f'(pin "{n}" (uuid {U()}))' for n in pins)
    out[sheet].append(f'(symbol (lib_id "igbt_drv:{lib}") (at {x} {y} {rot}) (unit 1) (in_bom {"no" if hidden else "yes"}) (on_board {"no" if hidden else "yes"}) (dnp {"yes" if dnp else "no"}) (uuid {U()}) {props}{ps}'
                   f'(instances (project "{PROJ}" (path "/{ROOT}/{sid}" (reference "{ref}") (unit 1)))))')
pwrn=[0]
def power_at(sheet,rail,pos,d):
    pwrn[0]+=1
    low=rail in('PGND','VEE')
    rot={(0,-1):0,(0,1):180,(1,0):270,(-1,0):90}[d] if not low else {(0,1):0,(0,-1):180,(1,0):90,(-1,0):270}[d]
    inst_sym(sheet,'P_'+rail,f'#PWR{pwrn[0]:03d}',rail,pos[0],pos[1],rot,hidden=False,pins=['1'])
def stub_end(p,d,L): return (round(p[0]+d[0]*L,3),round(p[1]+d[1]*L,3))
for p in inst:
    x,y,r=p['_pos']; s=p['sheet']
    extra={'MPN':p['mpn'],'Manufacturer':p['mfr'],'Description':p['desc']}
    inst_sym(s,p['sym'],p['ref'],p['val'],x,y,r,p['fp'],extra,[q['num'] for q in SYMS[p['sym']]['pins']],dnp=p['dnp'])
stubbed=set()
for sheet in SHEETS:
    for net,bysheet in netpins.items():
        pl=bysheet.get(sheet,[])
        if not pl: continue
        if net in RAILS:
            for k in pl:
                v=pinpos[k]; e=stub_end(v['pos'],v['out'],2.54)
                add_wire(sheet,v['pos'],e)
                owner=[p for p in inst if p['ref']==k[0]][0]
                if SYMS[owner['sym']]['names']: label(sheet,net,e,v['out'],force=True)
                else: power_at(sheet,net,e,v['out'])
            continue
        done=False
        if len(pl)==2 and len(netsheet[net])==1:
            a,b=pinpos[pl[0]],pinpos[pl[1]]; A,B=a['pos'],b['pos']
            dist=abs(A[0]-B[0])+abs(A[1]-B[1])
            if dist<45:
                own={pl[0],pl[1]}
                ax,ay=A; bx,by=B
                if ax==bx or ay==by: paths=[[A,B]]
                else: paths=[[A,(bx,ay),B],[A,(ax,by),B]]
                for path in paths:
                    if all(seg_free(sheet,path[i],path[i+1],own) for i in range(len(path)-1)):
                        for i in range(len(path)-1): add_wire(sheet,path[i],path[i+1])
                        done=True; break
        if not done:
            for k in pl:
                v=pinpos[k]; e=stub_end(v['pos'],v['out'],2.54)
                add_wire(sheet,v['pos'],e); label(sheet,net,e,v['out'])
    # no-connects
    for p in inst:
        if p['sheet']!=sheet: continue
        for q in SYMS[p['sym']]['pins']:
            if not p['nets'].get(q['num']):
                pos=pinpos[(p['ref'],q['num'])]['pos']
                out[sheet].append(f'(no_connect (at {pos[0]} {pos[1]}) (uuid {U()}))')
# pwr flags
flags={'power':[('VIN',360,110),('PGND',375,110),('VP',390,110),('VE',360,130),('VEE',375,130)]}
fn=[0]
for sh,ls in flags.items():
    for rail,x,y in ls:
        fn[0]+=1
        inst_sym(sh,'PWR_FLAG',f'#FLG{fn[0]:02d}','PWR_FLAG',x,y,0,pins=['1'])
        add_wire(sh,(x,y),(x,y+2.54)); power_at(sh,rail,(x,y+2.54),(0,1))
# notes / titles
N={'power':[('Input: same 5-pin pinout as 1SD418F2, or JST-XH',28,40),('Isolation: SN6507 push-pull + Poweronics XS000027 (catalog, 3.3 kV IGBT class)',120,40),('Rails: VP = +15 V, VE = emitter, VEE = -10 V (logic ground)',28,150),('V5 = VEE+5 V logic rail. D4/D5 DNP: fit per Poweronics reference rectifier',190,150)],
 'control':[('Fiber receive and command conditioning; ack generator',28,40),('Fault blocking 1.1 s; status transmitter; 2L/3L jumper',28,116),('Enable logic',205,90),('Desaturation / blanking (6.1 us) / UVLO comparators',28,178)],
 'output':[('Gate power stage Q2 = ISA170230C04LMDS (P ch: turn-on via U21/Q7 level shift, N ch: turn-off via U22), Q3 soft shutdown',20,38),('AAC: Q4 disables Q2-N/Q3 when clamp current flows',170,126),('Gate clamps, Cge, module terminals',215,40),('Active clamp: 6 x 1.5SMC440A (2508-2772 V) + DA2C: D40 shorted by Q5 while on and ~17 us after',120,207),('Vce sense chain 19.8 Mohm, trip Vce = 55.7 V',150,254)]}
for s,l in N.items():
    for t,x,y in l: textnote(s,t,x,y)
# write
def libsyms():
    used=set(p['sym'] for p in inst)|{'P_'+r for r in RAILS}|{'PWR_FLAG'}
    return '(lib_symbols '+' '.join(sym_sexp(n,'igbt_drv:') for n in sorted(used))+')'
for name,(sid,pg,title) in SHEETS.items():
    with open(f'{OUT}/{name}.kicad_sch','w') as f:
        f.write(f'(kicad_sch (version 20230121) (generator eeschema) (uuid {sid}) (paper "A3")\n(title_block (title "FZ1200R33KF2 gate driver: {title}") (rev "D") (company "Prototype, not safety approved"))\n{libsyms()}\n'+'\n'.join(out[name])+'\n)\n')
root=f'''(kicad_sch (version 20230121) (generator eeschema) (uuid {ROOT}) (paper "A4")
(title_block (title "FZ1200R33KF2 bolt-on gate driver") (rev "D") (company "Prototype, not safety approved"))
(lib_symbols)
'''
Y=40
for name,(sid,pg,title) in SHEETS.items():
    root+=f'''(sheet (at 50 {Y}) (size 90 22) (stroke (width 0.1524) (type solid)) (fill (color 0 0 0 0)) (uuid {sid})
(property "Sheetname" "{title}" (at 50 {Y-0.7} 0) (effects (font (size 1.5 1.5)) (justify left bottom)))
(property "Sheetfile" "{name}.kicad_sch" (at 50 {Y+22.7} 0) (effects (font (size 1.2 1.2)) (justify left top)))
(instances (project "{PROJ}" (path "/{ROOT}" (page "{pg}")))))
'''
    Y+=38
root+='(text "All logic is referenced to VEE (-15 V rail). Mounting and creepage notes in README." (at 50 25 0) (effects (font (size 2 2)) (justify left bottom)) (uuid '+U()+'))\n'
root+='(sheet_instances (path "/" (page "1"))))\n'
open(f'{OUT}/{PROJ}.kicad_sch','w').write(root)
import json
intended={}
for p in inst:
    for pn,n in p['nets'].items():
        if n: intended.setdefault(n,[]).append(f"{p['ref']}.{pn}")
json.dump(intended,open(f'{OUT}/generator/intended_nets.json','w'),indent=1,sort_keys=True)
print('parts',len(inst),'nets',len(intended))
