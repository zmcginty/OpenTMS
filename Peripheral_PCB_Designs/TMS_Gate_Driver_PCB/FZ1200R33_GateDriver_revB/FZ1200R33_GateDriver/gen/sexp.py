"""Minimal S-expression parser / serializer for KiCad files."""
import re

class Sym(str):
    """Unquoted atom."""
    pass

_tok = re.compile(r'\s*(\(|\)|"(?:[^"\\]|\\.)*"|[^\s()"]+)')

def parse(text):
    pos = 0
    stack = [[]]
    n = len(text)
    while True:
        m = _tok.match(text, pos)
        if not m:
            break
        t = m.group(1)
        pos = m.end()
        if t == '(':
            stack.append([])
        elif t == ')':
            e = stack.pop()
            stack[-1].append(e)
        elif t.startswith('"'):
            stack[-1].append(t[1:-1].replace('\\"', '"').replace('\\\\', '\\'))
        else:
            stack[-1].append(Sym(t))
        if pos >= n:
            break
    return stack[0]

def q(s):
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'

def dumps(e, indent=0):
    sp = '  ' * indent
    if isinstance(e, list):
        if not e:
            return '()'
        simple = all(not isinstance(x, list) for x in e)
        if simple:
            return '(' + ' '.join(atom(x) for x in e) + ')'
        parts = [atom(e[0])] if not isinstance(e[0], list) else []
        rest = e[1:] if not isinstance(e[0], list) else e
        s = '(' + ' '.join(parts)
        inline = []
        for x in rest:
            if isinstance(x, list):
                break
            inline.append(atom(x))
        if inline:
            s += ' ' + ' '.join(inline)
        for x in rest[len(inline):]:
            s += '\n' + sp + '  ' + (dumps(x, indent + 1) if isinstance(x, list) else atom(x))
        s += '\n' + sp + ')'
        return s
    return atom(e)

def atom(x):
    if isinstance(x, Sym):
        return str(x)
    if isinstance(x, bool):
        return 'yes' if x else 'no'
    if isinstance(x, (int,)):
        return str(x)
    if isinstance(x, float):
        s = ('%.4f' % x).rstrip('0').rstrip('.')
        return s if s not in ('-0', '') else '0'
    return q(x)

def find(e, key):
    for x in e:
        if isinstance(x, list) and x and x[0] == key:
            return x
    return None

def findall(e, key):
    return [x for x in e if isinstance(x, list) and x and x[0] == key]
