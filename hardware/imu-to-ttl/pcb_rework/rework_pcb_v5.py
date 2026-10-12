#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PCB v5: 接插件全部改卧式弯排针贴板边(电池/躯干夹层约束)
J2 1x7卧式→左缘(体朝板内,针探出左边) J3 1x3→底缘 J4/J5 1x3→右缘
J1 电池端子挪左下角; 删旧接插件焊盘的引出走线"""
import io, sys, re, uuid as uuidlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PCB = 'imu_to_ttl.kicad_pcb'
KC = 'C:/Users/c1349/AppData/Local/Programs/KiCad/10.0.7/share/kicad/footprints/Connector_PinHeader_2.54mm.pretty/'
s = open(PCB, encoding='utf-8').read()

def blk_span(s, st):
    d = 0; j = st
    while j < len(s):
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: return j + 1
        j += 1
    return -1

def fu():
    return '"%s"' % uuidlib.uuid4()

# ---- 1) 删除旧 J2/J3/J4/J5 块, 记录旧焊盘位置 ----
old_pads = []
kill_ref = []
i = 0
while True:
    st = s.find('\n\t(footprint ', i)
    if st < 0: break
    e = blk_span(s, st + 1)
    blk = s[st:e]
    r = re.search(r'\(property "Reference" "(J[2-5])"', blk)
    if r:
        kill_ref.append((st, e, r.group(1)))
        at = re.search(r'\n\t+\(at ([\d.\-]+) ([\d.\-]+)(?: ([\d.\-]+))?\)', blk)
        fx, fy, frot = float(at.group(1)), float(at.group(2)), float(at.group(3) or 0)
        def rot_pt(x, y, r):
            r = int(r) % 360
            return {0: (x, y), 90: (-y, x), 180: (-x, -y), 270: (y, -x)}[r]
        for pm in re.finditer(r'\(pad "[^"]+"[^\n]*\n\t+\(at ([\d.\-]+) ([\d.\-]+)', blk):
            px, py = rot_pt(float(pm.group(1)), float(pm.group(2)), frot)
            old_pads.append((fx + px, fy + py))
    i = e

# J1 旧焊盘(要挪位置, 走线也得删)
i1 = s.find('(property "Reference" "J1"')
st1 = s.rfind('\n\t(footprint ', 0, i1)
e1 = blk_span(s, st1 + 1)
blk1 = s[st1:e1]
at1 = re.search(r'\n\t+\(at ([\d.\-]+) ([\d.\-]+)(?: ([\d.\-]+))?\)', blk1)
fx1, fy1, fr1 = float(at1.group(1)), float(at1.group(2)), float(at1.group(3) or 0)
for pm in re.finditer(r'\(pad "[^"]+"[^\n]*\n\t+\(at ([\d.\-]+) ([\d.\-]+)', blk1):
    px, py = {0: lambda x, y: (x, y), 180: lambda x, y: (-x, -y)}[int(fr1) % 360](float(pm.group(1)), float(pm.group(2)))
    old_pads.append((fx1 + px, fy1 + py))

# 倒序删旧块
for st, e, ref in sorted(kill_ref, reverse=True):
    s = s[:st] + s[e:]
    print('删旧', ref)

# ---- 2) J1 挪到 (4.6, 26.5) ----
i1 = s.find('(property "Reference" "J1"')
st1 = s.rfind('\n\t(footprint ', 0, i1)
e1 = blk_span(s, st1 + 1)
blk1 = s[st1:e1]
nb1 = re.sub(r'\n\t+\(at 4\.6 15(?:\.0+)? 180\)', '\n\t\t(at 4.6 26.5 180)', blk1, count=1)
assert nb1 != blk1, 'J1 at没替换到'
s = s[:st1] + nb1 + s[e1:]
print('J1 → (4.6,26.5) ✓')

# ---- 3) 删触及旧焊盘的走线/过孔 ----
segs = []
for m in re.finditer(r'\(segment\n\t\t\(start ([\d.\-]+) ([\d.\-]+)\)\n\t\t\(end ([\d.\-]+) ([\d.\-]+)\)\n\t\t\(width ([\d.]+)\)', s):
    x1, y1, x2, y2 = [float(g) for g in m.groups()[:4]]
    segs.append((m.start(), (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)), float(m.group(5))))
vias = []
for m in re.finditer(r'\(via\b', s):
    e = blk_span(s, m.start())
    vb = s[m.start():e]
    am = re.search(r'\(at ([\d.\-]+) ([\d.\-]+)\)', vb)
    sm = re.search(r'\(size ([\d.]+)\)', vb)
    x, y, sz = float(am.group(1)), float(am.group(2)), float(sm.group(1))
    vias.append((m.start(), (x - sz/2, y - sz/2, x + sz/2, y + sz/2)))

def near(box, pad, r):
    return not (box[2] < pad[0] - r or box[0] > pad[0] + r or box[3] < pad[1] - r or box[1] > pad[1] + r)

kill = []
for st, b, w in segs:
    if any(near(b, p, 0.9 + w/2) for p in old_pads):
        kill.append((st, blk_span(s, st)))
for st, b in vias:
    if any(near(b, p, 0.9) for p in old_pads):
        kill.append((st, blk_span(s, st)))
kill = sorted(set(kill), reverse=True)
for st, e in kill:
    st = s.rfind('\n', 0, st)
    s = s[:st] + s[e:]
print('删旧焊盘引出走线/过孔 %d 处 (旧焊盘%d个)' % (len(kill), len(old_pads)))

# ---- 4) 生成新卧式封装块 ----
def build(ref, value, mod_file, x, y, rot, nets):
    mod = open(mod_file, encoding='utf-8').read()
    mod = re.sub(r'"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"',
                 lambda m: fu(), mod)
    mod = mod.replace('\t\t(at 4.385 -2.27 0)', '\t\t(at 4.385 -2.27 0)')  # noop
    # Reference/Value属性
    mod = re.sub(r'\(property "Reference" "REF\*\*"\n\t\t\(at [^\)]+\)\n\t\t\(layer "F.SilkS"\)\n(?:[^\n]*\n)*?\t\)\n',
                 '(property "Reference" "%s"\n\t\t(at 4.385 -2.27 0)\n\t\t(layer "F.SilkS")\n\t\t(uuid %s)\n\t\t(effects (font (size 1 1) (thickness 0.15)))\n\t)\n'
                 % (ref, fu()), mod, count=1)
    mod = re.sub(r'\(property "Value" "[^"]*"\n\t\t\(at [^\)]+\)\n\t\t\(layer "F.Fab"\)\n(?:[^\n]*\n)*?\t\)\n',
                 '(property "Value" "%s"\n\t\t(at 4.385 17.51 0)\n\t\t(layer "F.Fab")\n\t\t(hide yes)\n\t\t(uuid %s)\n\t\t(effects (font (size 1 1) (thickness 0.15)))\n\t)\n'
                 % (value, fu()), mod, count=1)
    # 实例at (在footprint行的下一行插入)
    mod = re.sub(r'(\(footprint "[^"]+"\n)(?:\t\(version[^\n]*\)\n)?(?:\t\(generator[^\n]*\)\n)?(?:\t\(generator_version[^\n]*\)\n)?',
                 r'\1\t(version 20260206)\n\t(generator "pcbnew")\n\t(generator_version "10.0")\n\t(layer "F.Cu")\n\t(at %.3f %.3f %d)\n'
                 % (x, y, rot), mod, count=1)
    # 去掉库里多余的重复layer行(原mod自带 (layer "F.Cu") 在footprint行后)
    mod = mod.replace('\t(layer "F.Cu")\n\t(layer "F.Cu")\n', '\t(layer "F.Cu")\n')
    # pads: 加 net + uuid
    def pad_sub(m):
        blk = m.group(0)
        pn = re.search(r'\(pad "([^"]+)"', blk).group(1)
        net = nets.get(pn)
        if not net:
            return blk
        if '(net ' not in blk:
            blk = blk.rstrip()[:-1] + '\t\t(net "%s")\n\t\t(uuid %s)\n\t)\n' % (net, fu())
        return blk
    mod = re.sub(r'\(pad "[^"]+"[\s\S]*?\n\t\)', pad_sub, mod)
    return '\n' + mod.rstrip() + '\n'

NETS_J2 = {'1': 'GND', '2': 'SWCLK', '3': 'SWDIO', '4': 'UART1_TX', '5': 'UART1_RX', '6': '+3V3', '7': 'NRST'}
NETS_S = {'1': 'BUS_DATA', '2': 'VBAT', '3': 'GND'}
PH7 = KC + 'PinHeader_1x07_P2.54mm_Horizontal.kicad_mod'
PH3 = KC + 'PinHeader_1x03_P2.54mm_Horizontal.kicad_mod'

new_blocks = [
    ('J2', build('J2', 'SWD/UART 1x7', PH7, 9.5, 20.5, 180, NETS_J2)),
    ('J3', build('J3', 'NECK 1x3',     PH3, 21.35, 25.5, 90, NETS_S)),
    ('J4', build('J4', 'LEG_L 1x3',    PH3, 35.7, 19.0, 0, NETS_S)),
    ('J5', build('J5', 'LEG_R 1x3',    PH3, 35.7, 11.0, 0, NETS_S)),
]
i_ins = s.rfind('(embedded_fonts') if '(embedded_fonts' in s else len(s)
# 插到最后一个footprint块之后: 找最后一个footprint结束
last = 0
i = 0
while True:
    st = s.find('\n\t(footprint ', i)
    if st < 0: break
    last = blk_span(s, st + 1)
    i = last
insert = ''.join(blk for _, blk in new_blocks)
s = s[:last] + insert + s[last:]
for ref, _ in new_blocks:
    print(ref, '卧式已插入 ✓')

open(PCB, 'w', encoding='utf-8').write(s)
print('v5完成, 括号平衡:', s.count('(') - s.count(')'))
