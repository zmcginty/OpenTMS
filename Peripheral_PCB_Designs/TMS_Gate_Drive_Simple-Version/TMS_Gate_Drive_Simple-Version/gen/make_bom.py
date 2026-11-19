import csv, re, design
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
OUT = '/home/claude/out/FZ1200R33_GateDriver'
def nat(r): m = re.match(r'([A-Z]+)(\d+)', r); return (m.group(1), int(m.group(2)))
groups = {}
for c in design.COMPS:
    if c['sym'] in (None, 'Mechanical:MountingHole'): continue
    k = (c['mpn'], c['value'], c['fp'], c['dnp'])
    groups.setdefault(k, []).append(c)
rows = []
for (mpn, val, fp, dnp), cs in groups.items():
    refs = sorted((c['ref'] for c in cs), key=nat)
    rows.append(dict(Item=0, Qty=len(cs), References=', '.join(refs), Value=val, Description=(' / '.join(dict.fromkeys(c['desc'] for c in cs)) if len(set(c['desc'] for c in cs))<=2 else cs[0]['desc'].split(' GON_A')[0].replace('Gate resistor array','Gate resistor arrays (RGon/RGoff)')),
                     Manufacturer=cs[0]['mfr'], MPN=mpn, Footprint=fp.split(':')[1], DNP='DNP' if dnp else '',
                     Sheet=cs[0]['sheet']))
order = {'U':0,'T':1,'Q':2,'D':3,'LED':4,'R':5,'C':6,'L':7,'F':8,'J':9,'X':10}
rows.sort(key=lambda r: (r['DNP'] != '', order.get(nat(r['References'].split(',')[0])[0], 99), nat(r['References'].split(',')[0])))
for i, r in enumerate(rows, 1): r['Item'] = i
cols = ['Item','Qty','References','Value','Description','Manufacturer','MPN','Footprint','DNP']
with open(f'{OUT}/BOM.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, cols, extrasaction='ignore'); w.writeheader(); w.writerows(rows)
wb = Workbook(); ws = wb.active; ws.title = 'BOM'
ws.append(['FZ1200R33KF2 plug-on gate driver rev A - Bill of Materials (per board)']); ws['A1'].font = Font(bold=True, size=13)
ws.append(['Placed parts: %d   Line items: %d   DNP lines are optional / not fitted by default. T1 is build-to-print (see README spec).' %
           (sum(r['Qty'] for r in rows if not r['DNP']), len(rows))])
ws.append([])
ws.append(cols)
hdr = 4
for cell in ws[hdr]:
    cell.font = Font(bold=True, color='FFFFFF'); cell.fill = PatternFill('solid', fgColor='1F3864')
for r in rows:
    ws.append([r[c] for c in cols])
    if r['DNP']:
        for cell in ws[ws.max_row]: cell.font = Font(color='808080', italic=True)
    if 'CUSTOM' in r['MPN']:
        for cell in ws[ws.max_row]: cell.fill = PatternFill('solid', fgColor='FFF2CC')
widths = [6, 5, 34, 18, 60, 18, 22, 34, 6]
for i, wd in enumerate(widths, 1): ws.column_dimensions[get_column_letter(i)].width = wd
for row in ws.iter_rows(min_row=hdr+1):
    for cell in row: cell.alignment = Alignment(vertical='top', wrap_text=cell.column in (3, 5))
ws.freeze_panes = 'A5'; ws.auto_filter.ref = f'A{hdr}:I{ws.max_row}'
wb.save(f'{OUT}/BOM.xlsx')
print(len(rows), 'lines', sum(r['Qty'] for r in rows), 'parts')
