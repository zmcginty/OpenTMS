"""Load KiCad library symbols (resolving 'extends'), extract pins, build custom box symbols."""
import copy, os
from sexp import parse, Sym, find, findall

LIBDIR = '/usr/share/kicad/symbols'
_cache = {}

def _lib(libname):
    if libname not in _cache:
        with open(os.path.join(LIBDIR, libname + '.kicad_sym')) as f:
            tree = parse(f.read())[0]
        _cache[libname] = {s[1]: s for s in findall(tree, 'symbol')}
    return _cache[libname]

def _unhide_pins(e):
    if isinstance(e, list):
        if e and e[0] == 'pin':
            while Sym('hide') in e:
                e.remove(Sym('hide'))
        for x in e:
            _unhide_pins(x)

def load(libid):
    """Return embedded symbol s-expr named libid (e.g. 'Device:R')."""
    lib, name = libid.split(':')
    syms = _lib(lib)
    s = copy.deepcopy(syms[name])
    ext = find(s, 'extends')
    if ext:
        parent = copy.deepcopy(syms[ext[1]])
        # take parent's body, child's properties
        props = {p[1]: p for p in findall(s, 'property')}
        new = [Sym('symbol'), name]
        for x in parent[2:]:
            if isinstance(x, list) and x[0] == 'property' and x[1] in props:
                new.append(props.pop(x[1]))
            elif isinstance(x, list) and x[0] == 'symbol':
                x[1] = x[1].replace(ext[1], name, 1)
                new.append(x)
            else:
                new.append(x)
        for p in props.values():
            new.insert(2, p)
        s = new
    s[1] = libid
    _unhide_pins(s)
    return s

def pins(symdef):
    """List of pins: dict(number,name,x,y,angle,unit,type)."""
    out = []
    for sub in findall(symdef, 'symbol'):
        parts = sub[1].rsplit('_', 2)
        unit = int(parts[-2])
        for p in findall(sub, 'pin'):
            at = find(p, 'at')
            out.append(dict(number=str(find(p, 'number')[1]), name=str(find(p, 'name')[1]),
                            x=float(at[1]), y=float(at[2]), angle=float(at[3]) if len(at) > 3 else 0.0,
                            unit=unit, type=str(p[1]), length=float(find(p, 'length')[1])))
    return out

def units(symdef):
    us = set(p['unit'] for p in pins(symdef))
    return sorted(u for u in us if u != 0) or [1]

def F(name, val, x=0, y=0, hide=False, angle=0):
    e = [Sym('property'), name, val, [Sym('at'), x, y, angle],
         [Sym('effects'), [Sym('font'), [Sym('size'), 1.27, 1.27]]] + ([Sym('hide')] if hide else [])]
    return e

def box_symbol(libid, refprefix, left, right, top=(), bottom=(), width=15.24, desc=''):
    """Custom rectangular IC symbol. Pins given as (number, name, type)."""
    name = libid.split(':')[1]
    n = max(len(left), len(right), 1)
    h = (n + 1) * 2.54
    hh = round(h / 2 / 2.54) * 2.54 + 2.54
    ww = width / 2
    body = [Sym('symbol'), name + '_0_1',
            [Sym('rectangle'), [Sym('start'), -ww, hh], [Sym('end'), ww, -hh],
             [Sym('stroke'), [Sym('width'), 0.254], [Sym('type'), Sym('default')]],
             [Sym('fill'), [Sym('type'), Sym('background')]]]]
    pl = [Sym('symbol'), name + '_1_1']
    def pin(num, nm, typ, x, y, ang):
        return [Sym('pin'), Sym(typ), Sym('line'), [Sym('at'), x, y, ang], [Sym('length'), 2.54],
                [Sym('name'), nm, [Sym('effects'), [Sym('font'), [Sym('size'), 1.27, 1.27]]]],
                [Sym('number'), num, [Sym('effects'), [Sym('font'), [Sym('size'), 1.27, 1.27]]]]]
    y0 = (len(left) - 1) * 2.54 / 2
    y0 = round(y0 / 2.54) * 2.54
    for i, (num, nm, typ) in enumerate(left):
        pl.append(pin(num, nm, typ, -ww - 2.54, y0 - i * 2.54, 0))
    y0 = round((len(right) - 1) * 2.54 / 2 / 2.54) * 2.54
    for i, (num, nm, typ) in enumerate(right):
        pl.append(pin(num, nm, typ, ww + 2.54, y0 - i * 2.54, 180))
    x0 = -round((len(top) - 1) * 2.54 / 2 / 2.54) * 2.54
    for i, (num, nm, typ) in enumerate(top):
        pl.append(pin(num, nm, typ, x0 + i * 2.54, hh + 2.54, 270))
    x0 = -round((len(bottom) - 1) * 2.54 / 2 / 2.54) * 2.54
    for i, (num, nm, typ) in enumerate(bottom):
        pl.append(pin(num, nm, typ, x0 + i * 2.54, -hh - 2.54, 90))
    s = [Sym('symbol'), libid, [Sym('in_bom'), Sym('yes')], [Sym('on_board'), Sym('yes')],
         F('Reference', refprefix, 0, hh + 1.27 + (2.54 if top else 0)),
         F('Value', name, 0, -hh - 1.27 - (2.54 if bottom else 0)),
         F('Footprint', '', 0, 0, True), F('Datasheet', '', 0, 0, True),
         F('Description', desc, 0, 0, True),
         body, pl]
    return s

def transformer_symbol(libid):
    """Center-tapped primary and secondary transformer: 1 P1,2 PCT,3 P2 | 4 S1,5 SCT,6 S2."""
    name = libid.split(':')[1]
    def pin(num, nm, x, y, ang):
        return [Sym('pin'), Sym('passive'), Sym('line'), [Sym('at'), x, y, ang], [Sym('length'), 2.54],
                [Sym('name'), nm, [Sym('effects'), [Sym('font'), [Sym('size'), 1.0, 1.0]]]],
                [Sym('number'), num, [Sym('effects'), [Sym('font'), [Sym('size'), 1.0, 1.0]]]]]
    g = [Sym('symbol'), name + '_0_1']
    st = [Sym('stroke'), [Sym('width'), 0.254], [Sym('type'), Sym('default')]]
    fl = [Sym('fill'), [Sym('type'), Sym('none')]]
    # coils as arcs (approximate with semicircles)
    for side, cx in ((-1, -2.54), (1, 2.54)):
        for k in range(8):
            yc = 4.445 - k * 1.27
            if side < 0:
                g.append([Sym('arc'), [Sym('start'), cx, yc + 0.635], [Sym('mid'), cx + 0.635, yc], [Sym('end'), cx, yc - 0.635], st, fl])
            else:
                g.append([Sym('arc'), [Sym('start'), cx, yc - 0.635], [Sym('mid'), cx - 0.635, yc], [Sym('end'), cx, yc + 0.635], st, fl])
    g.append([Sym('polyline'), [Sym('pts'), [Sym('xy'), -0.508, 5.08], [Sym('xy'), -0.508, -5.08]], st, fl])
    g.append([Sym('polyline'), [Sym('pts'), [Sym('xy'), 0.508, 5.08], [Sym('xy'), 0.508, -5.08]], st, fl])
    # leads
    for (x0, x1) in ((-5.08, -2.54), (2.54, 5.08)):
        for y in (5.08, 0, -5.08):
            g.append([Sym('polyline'), [Sym('pts'), [Sym('xy'), x0, y], [Sym('xy'), x1, y]], st, fl])
    pl = [Sym('symbol'), name + '_1_1',
          pin('1', 'P1', -7.62, 5.08, 0), pin('2', 'PCT', -7.62, 0, 0), pin('3', 'P2', -7.62, -5.08, 0),
          pin('4', 'S1', 7.62, 5.08, 180), pin('5', 'SCT', 7.62, 0, 180), pin('6', 'S2', 7.62, -5.08, 180)]
    return [Sym('symbol'), libid, [Sym('pin_names'), [Sym('offset'), 0.254], Sym('hide')],
            [Sym('in_bom'), Sym('yes')], [Sym('on_board'), Sym('yes')],
            F('Reference', 'T', 0, 7.62), F('Value', name, 0, -7.62),
            F('Footprint', '', 0, 0, True), F('Datasheet', '', 0, 0, True), g, pl]
