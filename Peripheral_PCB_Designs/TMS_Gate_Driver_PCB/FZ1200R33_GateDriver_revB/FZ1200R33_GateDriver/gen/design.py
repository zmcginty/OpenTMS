"""Single-source design model for the FZ1200R33KF2 plug-on gate driver (1SD418FI-style).

Reference domains:
  Primary   : GND_P (15 V input side, SELV)
  Secondary : COM_E = IGBT auxiliary emitter. VP15 = +15 V, VN10 = -10 V (vs COM_E), from Murata MGJ6D121510SC
              VL5   = logic rail = VN10 + 5 V (all logic is referenced to VN10)
  HV        : COLL = auxiliary collector (up to 3.3 kV vs COM_E)
"""
from libs import box_symbol, transformer_symbol

SHEETS = [
    ('pri',   'Primary_Power',      'Primary input, push-pull isolated DC/DC'),
    ('sec',   'Secondary_Power',    'Secondary rectifier and +15 V / -15 V / logic regulators'),
    ('logic', 'Fiber_Logic',        'Fiber-optic interface, fault logic, UVLO, blocking timer'),
    ('desat', 'Desat_Detection',    'VCE(sat) short-circuit detection'),
    ('gate',  'Gate_Stage',         'Pre-driver, BJT booster, gate resistors, gate protection'),
    ('clamp', 'Active_Clamp_Terms', 'Advanced active clamping and module terminals'),
]

CUSTOM_SYMBOLS = {
    'GD:UCC27531DBV': box_symbol('GD:UCC27531DBV', 'U',
        left=[('2', 'IN', 'input'), ('1', 'EN', 'input')],
        right=[('6', 'OUTH', 'output'), ('5', 'OUTL', 'output')],
        top=[('3', 'VDD', 'power_in')], bottom=[('4', 'GND', 'power_in')], width=12.7,
        desc='35 V 2.5 A/5 A single gate driver, split outputs'),
    'GD:MGJ6D121510SC': box_symbol('GD:MGJ6D121510SC', 'U',
        left=[('2', '+Vin', 'power_in'), ('1', 'Dis/Sync', 'input'), ('7', '-Vin', 'power_in'), ('8', '-Vin', 'passive')],
        right=[('3', '+15V (15Vout)', 'power_out'), ('6', '0V (15V RTN/5VA)', 'power_out'),
               ('5', '-5V (5VA RTN/5VB)', 'passive'), ('4', '-10V (5VB RTN)', 'power_out')],
        width=30.48, desc='Murata MGJ6 6 W gate-drive DC/DC, IGBT configuration +15 V / -10 V'),
    'GD:HFBR-2521Z': box_symbol('GD:HFBR-2521Z', 'U',
        left=[('1', 'VO', 'open_collector'), ('4', 'RL', 'passive')],
        right=[('5', 'MNT', 'passive'), ('8', 'MNT', 'passive')],
        top=[('3', 'VCC', 'power_in')], bottom=[('2', 'GND', 'power_in')], width=12.7,
        desc='Versatile Link fiber receiver 5 MBd (light = VO low)'),
    'GD:HFBR-1521Z': box_symbol('GD:HFBR-1521Z', 'U',
        left=[('1', 'A', 'passive'), ('2', 'K', 'passive')],
        right=[('3', 'NC', 'passive'), ('4', 'NC', 'passive'), ('5', 'MNT', 'passive'), ('8', 'MNT', 'passive')],
        width=12.7, desc='Versatile Link fiber transmitter 660 nm'),
}

FP = {
    '0603': 'Resistor_SMD:R_0603_1608Metric', '0805': 'Resistor_SMD:R_0805_2012Metric',
    '1206': 'Resistor_SMD:R_1206_3216Metric', '2512': 'Resistor_SMD:R_2512_6332Metric',
    'C0603': 'Capacitor_SMD:C_0603_1608Metric', 'C0805': 'Capacitor_SMD:C_0805_2012Metric',
    'C1206': 'Capacitor_SMD:C_1206_3216Metric', 'C1210': 'Capacitor_SMD:C_1210_3225Metric',
    'SMA': 'Diode_SMD:D_SMA', 'SMB': 'Diode_SMD:D_SMB', 'SMC': 'Diode_SMD:D_SMC',
    'SOD123': 'Diode_SMD:D_SOD-123', 'LED0603': 'LED_SMD:LED_0603_1608Metric',
    'SOT23': 'Package_TO_SOT_SMD:SOT-23', 'SOT23-5': 'Package_TO_SOT_SMD:SOT-23-5',
    'SOT23-6': 'Package_TO_SOT_SMD:SOT-23-6', 'SOIC8': 'Package_SO:SOIC-8_3.9x4.9mm_P1.27mm',
    'SOT223': 'Package_TO_SOT_SMD:SOT-223-3_TabPin2', 'D2PAK': 'Package_TO_SOT_SMD:TO-263-2',
        'F1206': 'Fuse:Fuse_1206_3216Metric', 'SRN6045': 'Inductor_SMD:L_Bourns_SRN6045TA',
    'CP6.3': 'Capacitor_SMD:CP_Elec_6.3x7.7', 'CP8': 'Capacitor_SMD:CP_Elec_8x10',
    'HDR4': 'Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical',
    'XH4': 'Connector_JST:JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical',
    'HFBR': 'GD:Broadcom_VersatileLink_Horizontal', 'MGJ6': 'GD:Murata_MGJ6_SIP8',
    'M4LUG': 'GD:IHV_AuxTerminal_M4', 'MH3': 'MountingHole:MountingHole_3.2mm_M3',
}

COMPS = []

def add(ref, sym, value, fp, nets, sheet, mpn='', mfr='', desc='', dnp=False):
    COMPS.append(dict(ref=ref, sym=sym, value=value, fp=FP.get(fp, fp), nets={str(k): v for k, v in nets.items()},
                      sheet=sheet, mpn=mpn, mfr=mfr, desc=desc, dnp=dnp))

def R(ref, val, a, b, sheet, size='0603', mpn='', mfr='', desc='', dnp=False):
    gen = {'0603': ('Yageo', 'RC0603FR-07%sL'), '0805': ('Yageo', 'RC0805FR-07%sL'),
           '1206': ('Yageo', 'RC1206FR-07%sL'), '2512': ('Vishay', 'CRCW2512%sFKEG')}
    if not mpn:
        mfr, pat = gen[size]
        mpn = pat % val.replace('.', 'R')
    add(ref, 'Device:R', val, size, {1: a, 2: b}, sheet, mpn, mfr, desc or f'Resistor {val} {size}', dnp)

def C(ref, val, a, b, sheet, size='C0603', mpn='', mfr='', desc='', dnp=False):
    add(ref, 'Device:C', val, size, {1: a, 2: b}, sheet, mpn, mfr, desc or f'Capacitor {val}', dnp)

def D(ref, sym, val, k, a, sheet, fp, mpn, mfr, desc, dnp=False):
    add(ref, sym, val, fp, {1: k, 2: a}, sheet, mpn, mfr, desc, dnp)

C10U = dict(size='C1210', mpn='GRM32ER71H106KA12L', mfr='Murata', desc='10uF 50V X7R 1210')
C100N = dict(size='C0603', mpn='GCM188R71H104KA57D', mfr='Murata', desc='100nF 50V X7R 0603')
C1U50 = dict(size='C0805', mpn='GRM21BR71H105KA12L', mfr='Murata', desc='1uF 50V X7R 0805')

# ---------------------------------------------------------------- PRIMARY + ISOLATED DC/DC
s = 'pri'
add('J1', 'Connector_Generic:Conn_01x04', 'PWR 2.54mm', 'HDR4',
    {1: 'VIN_RAW', 2: 'VIN_RAW', 3: 'GND_P', 4: 'GND_P'}, s, '61300411121', 'Wurth', 'Power header 1x4 2.54 mm (VDC,VDC,GND,GND)')
add('J2', 'Connector_Generic:Conn_01x04', 'PWR JST-XH', 'XH4',
    {1: 'VIN_RAW', 2: 'VIN_RAW', 3: 'GND_P', 4: 'GND_P'}, s, 'B4B-XH-A(LF)(SN)', 'JST', 'Alternative power connector JST-XH 4p (fit J1 or J2)')
add('F1', 'Device:Fuse', '2A', 'F1206', {1: 'VIN_RAW', 2: 'VIN_F'}, s, '0466002.NR', 'Littelfuse', 'Input fuse 2 A (Murata MGJ6 12 V-type recommendation)')
D('D1', 'Device:D_Schottky', 'B240A', 'VIN_P', 'VIN_F', s, 'SMA', 'B240A-13-F', 'Diodes Inc', 'Reverse-polarity Schottky 40 V 2 A')
D('D2', 'Device:D_Zener', 'SMBJ16A', 'VIN_P', 'GND_P', s, 'SMB', 'SMBJ16A', 'Littelfuse', 'Input TVS 16 V standoff (MGJ6 abs. max 20 V)')
C('C2', '10u', 'VIN_P', 'GND_P', s, **C10U)
add('L1', 'Device:L', '10u', 'SRN6045', {1: 'VIN_P', 2: 'VIN_M'}, s, 'SRN6045TA-100M', 'Bourns', 'Input EMC filter inductor 10 uH (per MGJ6 EMC filter)')
C('C3', '10u', 'VIN_M', 'GND_P', s, **C10U)
add('C1', 'Device:C_Polarized', '47u/35V', 'CP6.3', {1: 'VIN_M', 2: 'GND_P'}, s, 'EEE-FK1V470P', 'Panasonic', 'Input bulk / filter damping 47 uF 35 V')
C('C5', '22n', 'SYNC_P', 'GND_P', s, mpn='GCM188R71H223KA37D', mfr='Murata', desc='Dis/Sync noise cap (datasheet recommendation, 22nF)')
add('U1', 'GD:MGJ6D121510SC', 'MGJ6D121510SC', 'MGJ6',
    {2: 'VIN_M', 7: 'GND_P', 8: 'GND_P', 1: 'SYNC_P', 3: 'VP15', 6: 'COM_E', 4: 'VN10', 5: None}, s,
    'MGJ6D121510SC', 'Murata', 'Isolated DC/DC 6 W, 9-18 V in, +15 V/-10 V out, 5.7 kVDC hi-pot, 3 kVDC continuous (functional)')
R('R5', '4.7k', 'VIN_P', 'LED_P', s, desc='Power LED resistor')
D('LED1', 'Device:LED', 'GREEN', 'LED_P', 'GND_P', s, 'LED0603', '150060GS75000', 'Wurth', 'Primary power-on LED')

# ---------------------------------------------------------------- SECONDARY POWER
s = 'sec'
C('C11', '10u', 'VP15', 'COM_E', s, **C10U)
C('C12', '10u', 'VP15', 'COM_E', s, **C10U)
add('C13', 'Device:C_Polarized', '100u/35V', 'CP8', {1: 'VP15', 2: 'COM_E'}, s, 'EEE-FK1V101P', 'Panasonic', 'VP15 bulk 100 uF 35 V (MGJ6 max 220 uF)')
C('C14', '10u', 'COM_E', 'VN10', s, **C10U)
C('C15', '10u', 'COM_E', 'VN10', s, **C10U)
add('C16', 'Device:C_Polarized', '100u/35V', 'CP8', {1: 'COM_E', 2: 'VN10'}, s, 'EEE-FK1V101P', 'Panasonic', 'VN10 bulk 100 uF 35 V (MGJ6 max 220 uF)')
R('R57', '680', 'VP15', 'COM_E', s, size='2512', mpn='CRCW2512680RFKEG', mfr='Vishay',
  desc='MGJ6 minimum-load preload on +15 V (~22 mA, 0.33 W) - keeps VP15 from rising to 17 V')
add('U4', 'Regulator_Linear:LM317_SOT-223', 'LM317DCYR', 'SOT223', {3: 'COM_E', 2: 'VL5', 1: 'ADJ_L'}, s,
    'LM317DCYR', 'Texas Instruments', 'Logic rail VL5 = VN10 + 5 V (fed from COM_E; also loads the -10 V output)')
R('R10', '240', 'VL5', 'ADJ_L', s)
R('R11', '715', 'ADJ_L', 'VN10', s, desc='VL5 = 1.25*(1+715/240) = 4.97 V above VN10')
C('C17', '10u', 'VL5', 'VN10', s, **C10U)
C('C18', '100n', 'VL5', 'VN10', s, **C100N)

# ---------------------------------------------------------------- FIBER & LOGIC
s = 'logic'
add('U5', 'GD:HFBR-2521Z', 'HFBR-2521Z', 'HFBR', {1: 'RX_OUT', 4: 'RX_OUT', 3: 'VL5', 2: 'VN10', 5: None, 8: None}, s,
    'HFBR-2521Z', 'Broadcom', 'Command input fiber receiver (light = IGBT ON)')
C('C19', '100n', 'VL5', 'VN10', s, **C100N)
R('R12', '1k', 'RX_OUT', 'RX_F', s, desc='Input glitch filter')
C('C20', '220p', 'RX_F', 'VN10', s, mpn='GRM1885C1H221JA01D', mfr='Murata', desc='220pF C0G (~200 ns filter)')
add('U6', '74xGxx:74LVC1G14', 'SN74LVC1G14DBVR', 'SOT23-5', {1: None, 2: 'RX_F', 3: 'VN10', 4: 'CMD', 5: 'VL5'}, s,
    'SN74LVC1G14DBVR', 'Texas Instruments', 'Schmitt inverter: CMD = light present')
add('U7', '74xGxx:74LVC1G08', 'SN74LVC1G08DBVR', 'SOT23-5', {1: 'CMD', 2: 'READY', 3: 'VN10', 4: 'GATE_EN', 5: 'VL5'}, s,
    'SN74LVC1G08DBVR', 'Texas Instruments', 'GATE_EN = CMD AND READY')
add('U8', '74xGxx:74LVC1G14', 'SN74LVC1G14DBVR', 'SOT23-5', {1: None, 2: 'GATE_EN', 3: 'VN10', 4: 'nGATE', 5: 'VL5'}, s,
    'SN74LVC1G14DBVR', 'Texas Instruments', 'nGATE for desat blanking reset')
C('C21', '100n', 'VL5', 'VN10', s, **C100N)
C('C22', '100n', 'VL5', 'VN10', s, **C100N)
add('U9', 'Timer:TLC555xD', 'TLC555IDR', 'SOIC8', {8: 'VL5', 1: 'VN10', 2: 'nDESAT', 3: 'BLOCK', 4: 'VL5', 5: 'CONT', 6: 'TMR', 7: 'TMR'}, s,
    'TLC555IDR', 'Texas Instruments', 'Fault blocking timer (~1 s, retriggerable while fault persists)')
R('R13', '910k', 'VL5', 'TMR', s, desc='Blocking time t = 1.1*R*C = 1.0 s')
C('C23', '1u', 'TMR', 'VN10', s, size='C0805', mpn='GRM21BR71H105KA12L', mfr='Murata', desc='1uF X7R timing')
C('C24', '10n', 'CONT', 'VN10', s, mpn='GCM188R71H103KA37D', mfr='Murata', desc='10nF 50V X7R')
C('C25', '100n', 'VL5', 'VN10', s, **C100N)
R('R14', '10k', 'VL5', 'nDESAT', s, desc='Desat comparator pull-up')
add('Q1', 'Device:Q_NMOS_GSD', 'BSS138', 'SOT23', {1: 'BLOCK', 2: 'VN10', 3: 'READY'}, s, 'BSS138', 'onsemi', 'Pulls READY low while blocked')
R('R15', '10k', 'VL5', 'READY', s, desc='READY pull-up (wired-AND: UVLO + BLOCK)')
R('R16', '1k', 'BLOCK', 'LED_F', s, desc='Fault LED resistor')
D('LED2', 'Device:LED', 'RED', 'VN10', 'LED_F', s, 'LED0603', '150060RS75000', 'Wurth', 'Fault / blocked LED')
# UVLO comparator (LM2903 unit B)
R('R17', '220k', 'VP15', 'UVS', s, desc='UVLO sense: trips at VP15-VN10 < 20.8 V (nominal 25 V)')
R('R18', '30k', 'UVS', 'VN10', s)
R('R19', '1M', 'READY', 'UVS', s, desc='UVLO hysteresis (~1.3 V)')
R('R20', '10k', 'VL5', 'REF_UV', s)
R('R21', '10k', 'REF_UV', 'VN10', s)
C('C26', '10n', 'REF_UV', 'VN10', s, mpn='GCM188R71H103KA37D', mfr='Murata', desc='10nF 50V X7R')
# Status fiber transmitter
add('U10', 'GD:HFBR-1521Z', 'HFBR-1521Z', 'HFBR', {1: 'TX_A', 2: 'TX_K', 3: None, 4: None, 5: None, 8: None}, s,
    'HFBR-1521Z', 'Broadcom', 'Status fiber transmitter (light = READY / no fault)')
R('R22', '120', 'VL5', 'TX_A', s, size='0805', desc='LED current ~25 mA')
add('Q2', 'Device:Q_NMOS_GSD', 'BSS138', 'SOT23', {1: 'READY', 2: 'VN10', 3: 'TX_K'}, s, 'BSS138', 'onsemi', 'Status LED switch')

# ---------------------------------------------------------------- COMPARATOR (shared IC) - placed on desat sheet
s = 'desat'
add('U11', 'Comparator:LM2903', 'LM2903BIDR', 'SOIC8',
    {1: 'nDESAT', 2: 'DSD', 3: 'DREF', 5: 'UVS', 6: 'REF_UV', 7: 'READY', 4: 'VN10', 8: 'VP15'}, s,
    'LM2903BIDR', 'Texas Instruments', 'Dual comparator 36 V: A = desat, B = UVLO')
C('C27', '100n', 'VP15', 'VN10', s, **C100N)
# HV diode chain from aux collector
chain = ['COLL', 'DS3', 'DS2', 'DS1', 'DS_R']
for i in range(4):
    D(f'D{10 - i}', 'Device:D', 'STTH112A', chain[i], chain[i + 1], s, 'SMA', 'STTH112A', 'STMicroelectronics',
      'Desat HV diode 1200 V ultrafast (4 in series)')
    R(f'R{26 + i}', '22M', chain[i], chain[i + 1], s, size='2512', mpn='HVC2512T2205FET', mfr='Ohmite',
      desc='HV voltage-sharing resistor 22M 2512 (>=1.5 kV rated)')
R('R30', '1k', 'DS_R', 'DESAT', s, size='1206', desc='Desat series limiter')
R('R31', '15k', 'VP15', 'DESAT', s, size='1206', desc='Desat current source (~1 mA)')
C('C28', '120p', 'DESAT', 'VN10', s, size='C0805', mpn='GRM2165C1H121JA01D', mfr='Murata', desc='Blanking cap 120pF C0G (~5 us)')
add('Q3', 'Device:Q_NMOS_GSD', 'BSS138', 'SOT23', {1: 'nGATE', 2: 'VN10', 3: 'DESAT'}, s, 'BSS138', 'onsemi', 'Desat reset while gate off')
D('D11', 'Device:D_Schottky', 'BAT46W', 'VP15', 'DESAT', s, 'SOD123', 'BAT46W-7-F', 'Diodes Inc', 'DESAT clamp to VP15')
D('D12', 'Device:D_Schottky', 'BAT46W', 'DESAT', 'VN10', s, 'SOD123', 'BAT46W-7-F', 'Diodes Inc', 'DESAT clamp to VN10')
R('R32', '1.2M', 'DESAT', 'DSD', s, desc='Desat divider (1/7)')
R('R33', '200k', 'DSD', 'VN10', s)
R('R34', '130k', 'VP15', 'DREF', s, desc='Desat ref: 3.33 V above VN10 -> trip at DESAT = VN10 + 23.3 V = COM_E + 13.3 V')
R('R35', '20k', 'DREF', 'VN10', s)
C('C29', '10p', 'DSD', 'VN10', s, mpn='GRM1885C1H100JA01D', mfr='Murata', desc='Optional noise filter', dnp=True)

# ---------------------------------------------------------------- GATE STAGE
s = 'gate'
add('U12', 'GD:UCC27531DBV', 'UCC27531DBVR', 'SOT23-6', {1: 'READY', 2: 'GATE_EN', 3: 'VP15', 4: 'VN10', 5: 'OL', 6: 'OH'}, s,
    'UCC27531DBVR', 'Texas Instruments', 'Pre-driver 35 V, VDD = VP15-VN10 = 25 V')
C('C30', '1u', 'VP15', 'VN10', s, **C1U50)
C('C31', '100n', 'VP15', 'VN10', s, **C100N)
R('R36', '10', 'OH', 'BASE', s, size='1206', desc='Booster base drive ON')
R('R37', '10', 'OL', 'BASE', s, size='1206', desc='Booster base drive OFF (also sets clamp feedback gain)')
for ref, out in (('Q4', 'OUT_A'), ('Q5', 'OUT_B')):
    add(ref, 'Device:Q_NPN_BCE', 'MJB44H11', 'D2PAK', {1: 'BASE', 2: 'VP15', 3: out}, s, 'MJB44H11T4G', 'onsemi', 'Booster NPN 80 V 10 A (20 A pk)')
for ref, out in (('Q6', 'OUT_A'), ('Q7', 'OUT_B')):
    add(ref, 'Device:Q_PNP_BCE', 'MJB45H11', 'D2PAK', {1: 'BASE', 2: 'VN10', 3: out}, s, 'MJB45H11T4G', 'onsemi', 'Booster PNP 80 V 10 A (20 A pk)')
D('D13', 'Device:D_Schottky', 'B560C', 'GON_A', 'OUT_A', s, 'SMC', 'B560C-13-F', 'Diodes Inc', 'Turn-on steering 60 V 5 A')
D('D14', 'Device:D_Schottky', 'B560C', 'GON_B', 'OUT_B', s, 'SMC', 'B560C-13-F', 'Diodes Inc', 'Turn-on steering 60 V 5 A')
D('D15', 'Device:D_Schottky', 'B560C', 'OUT_A', 'GOFF_A', s, 'SMC', 'B560C-13-F', 'Diodes Inc', 'Turn-off steering 60 V 5 A')
D('D16', 'Device:D_Schottky', 'B560C', 'OUT_B', 'GOFF_B', s, 'SMC', 'B560C-13-F', 'Diodes Inc', 'Turn-off steering 60 V 5 A')
n = 38
for grp in ('GON_A', 'GON_B', 'GOFF_A', 'GOFF_B'):
    for k in range(4):
        R(f'R{n}', '10', grp, 'GATE', s, size='2512', mpn='CRCW251210R0FKEG', mfr='Vishay',
          desc=f'Gate resistor array {grp} (4x10R // = 2.5R; two branches = 1.25R)')
        n += 1
# n == 54
D('D17', 'Device:D_Schottky', 'B360B', 'VP15', 'GATE', s, 'SMB', 'B360B-13-F', 'Diodes Inc', 'Gate clamp to VP15')
D('D18', 'Device:D_Schottky', 'B360B', 'GATE', 'VN10', s, 'SMB', 'B360B-13-F', 'Diodes Inc', 'Gate clamp to VN10')
add('D19', 'Device:D_TVS', 'SMBJ18CA', 'SMB', {1: 'GATE', 2: 'COM_E'}, s, 'SMBJ18CA', 'Littelfuse', 'Gate-emitter TVS bidirectional 18 V')
R('R54', '10k', 'GATE', 'COM_E', s, size='1206', desc='Gate-emitter bleeder')
C('C32', '100n', 'GATE', 'COM_E', s, size='C1210', mpn='GCM32C5C1H104JA16L', mfr='Murata',
  desc='Optional CGE (DS test uses 220 nF) 100nF C0G 1210', dnp=True)
C('C33', '100n', 'GATE', 'COM_E', s, size='C1210', mpn='GCM32C5C1H104JA16L', mfr='Murata',
  desc='Optional CGE 100nF C0G 1210', dnp=True)
for i, ref in enumerate(('C34', 'C35', 'C36', 'C37')):
    C(ref, '10u', 'VP15', 'COM_E', s, **C10U)
for i, ref in enumerate(('C38', 'C39', 'C40', 'C41')):
    C(ref, '10u', 'COM_E', 'VN10', s, **C10U)

# ---------------------------------------------------------------- ACTIVE CLAMP & TERMINALS
s = 'clamp'
nodes = ['COLL', 'ACK4', 'ACK3', 'ACK2', 'ACK1', 'CLMP']
for i in range(5):
    D(f'D{24 - i}', 'Device:D_Zener', 'P6SMB550A', nodes[i], nodes[i + 1], s, 'SMB', 'P6SMB550A', 'Littelfuse',
      'Active-clamp TVS 470 V standoff, Vbr 523-578 V (5 in series, Vbr 2.6-2.9 kV)')
D('D28', 'Device:D', 'ES1J', 'CG', 'CLMP', s, 'SMA', 'ES1J-E3/61T', 'Vishay', 'Clamp steering to gate')
D('D29', 'Device:D', 'ES1J', 'CB', 'CLMP', s, 'SMA', 'ES1J-E3/61T', 'Vishay', 'Clamp steering to booster base (advanced)')
R('R55', '10', 'CG', 'GATE', s, size='2512', mpn='CRCW251210R0FKEG', mfr='Vishay', desc='Clamp feed to gate')
R('R56', '22', 'CB', 'BASE', s, size='1206', desc='Clamp feed to booster base (advanced active clamping)')
add('X1', 'Connector_Generic:Conn_01x01', 'G (M4)', 'M4LUG', {1: 'GATE'}, s, 'M4 screw', '-', 'Module gate terminal (screw-on)')
add('X2', 'Connector_Generic:Conn_01x01', 'E aux (M4)', 'M4LUG', {1: 'COM_E'}, s, 'M4 screw', '-', 'Module auxiliary emitter terminal')
add('X3', 'Connector_Generic:Conn_01x01', 'C aux (M4)', 'M4LUG', {1: 'COLL'}, s, 'M4 screw', '-', 'Module auxiliary collector terminal (HV!)')
add('H1', None, 'D3.2', 'MH3', {}, s, '', '', 'Support hole 3.2 mm (same position as 1SP0635)')

# sanity
refs = [c['ref'] for c in COMPS]
assert len(refs) == len(set(refs)), [r for r in refs if refs.count(r) > 1]

def nets():
    out = {}
    for c in COMPS:
        for p, n in c['nets'].items():
            if n:
                out.setdefault(n, []).append((c['ref'], p))
    return out

if __name__ == '__main__':
    ns = nets()
    for n, m in sorted(ns.items()):
        if len(m) < 2:
            print('SINGLE-PIN NET', n, m)
    print(len(COMPS), 'components,', len(ns), 'nets')
