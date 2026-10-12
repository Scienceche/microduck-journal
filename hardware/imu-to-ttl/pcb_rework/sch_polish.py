#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三页原理图可读性修复(纯图形层, 不动连通性):
01_power: 位号/值文字统一放符号正上/正下(避开引脚标签)
05_connectors: J3/J4/J5 加引出短线把1.27mm间距的三行标签扇开到±2.54
03_imu: 删U2重复GND标签; V3V3_IMU第二个标签加短线引上
"""
import io, sys, re, uuid as uuidlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def load(f):
    return open(f, encoding='utf-8').read()

def save(f, s):
    open(f, 'w', encoding='utf-8').write(s)
    print('%-20s 括号平衡: %d' % (f, s.count('(') - s.count(')')))

def blk_span(s, st):
    d = 0; j = st
    while j < len(s):
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: return j + 1
        j += 1
    return -1

def sym_blocks(s):
    out = []
    i = 0
    while True:
        st = s.find('\n\t(symbol\n', i)
        if st < 0: break
        e = blk_span(s, st + 2)
        blk = s[st:e]
        r = re.search(r'\(property "Reference" "([^"]*)"', blk)
        if r:
            a = re.search(r'\(at ([\d.\-]+) ([\d.\-]+)(?: ([\d.]+))?\)', blk)
            out.append((r.group(1), float(a.group(1)), float(a.group(2)), st, e))
        i = e
    return out

# ================= A) 01_power: 属性文字归位 =================
f = '01_power.kicad_sch'
s = load(f)
BIG = {'U4', 'Q1', 'J1'}
nfix = 0
for ref, x, y, st, e in sym_blocks(s):
    off = 10.16 if ref in BIG else 7.62
    blk = s[st:e]
    nb = re.sub(r'(\(property "Reference" "[^"]+"\n\t\t\(at )[^\)]+\)',
                r'\g<1>%.2f %.2f 0)' % (x, y - off), blk, count=1)
    nb = re.sub(r'(\(property "Value" "[^"]+"\n\t\t\(at )[^\)]+\)',
                r'\g<1>%.2f %.2f 0)' % (x, y + off), nb, count=1)
    if nb != blk:
        s = s[:st] + nb + s[e:]
        nfix += 1
print('01_power: 属性归位 %d 个符号' % nfix)
save(f, s)

# ================= B) 05_connectors: J3/J4/J5 扇出 =================
f = '05_connectors.kicad_sch'
s = load(f)
WIRE = '\n\t(wire\n\t\t(pts (xy %.2f %.2f) (xy %.2f %.2f))\n\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n\t\t(uuid "%s")\n\t)'
wires = []
nmov = 0
for ref, x, y, st, e in sym_blocks(s):
    if ref not in ('J3', 'J4', 'J5'):
        continue
    px = x - 7.62            # 引脚x(标签锚点)
    sx = x - 12.70           # 扇出竖线x
    for pin_dy, fan_dy, text in ((-1.27, -2.54, 'BUS_DATA'), (1.27, 2.54, 'GND')):
        py, fy = y + pin_dy, y + fan_dy
        # 旧标签(锚在引脚上) → 新锚(扇出线端)
        old = r'\(global_label "%s"\n\t\t\(shape [^)]*\)\n\t\t\(at %s %s 180\)' % (text, '%.2f' % px, '%.2f' % py)
        new = '(global_label "%s"\n\t\t(shape bidirectional)\n\t\t(at %.2f %.2f 180)' % (text, sx, fy)
        s2 = re.sub(old, new, s, count=1)
        assert s2 != s, '%s %s 标签没找到 @%.2f,%.2f' % (ref, text, px, py)
        s = s2
        nmov += 1
        wires.append((px, py, sx, py))
        wires.append((sx, py, sx, fy))
i_emb = s.rfind('(embedded_fonts')
ins = ''.join(WIRE % (x1, y1, x2, y2, uuidlib.uuid4()) for x1, y1, x2, y2 in wires)
s = s[:i_emb] + ins + '\n' + s[i_emb:]
print('05_connectors: 标签扇开 %d 个, 加线 %d 条' % (nmov, len(wires)))
save(f, s)

# ================= C) 03_imu: U2 标签去重/引开 =================
f = '03_imu.kicad_sch'
s = load(f)
# C1: 删第二个锚在(100.33,125.73)的GND
pat = r'\n\t\(global_label "GND"\n\t\t\(shape [^)]*\)\n\t\t\(at 100\.33 125\.73 0\)(?:.|\n)*?\n\t\)'
ms = list(re.finditer(pat, s))
assert len(ms) == 2, 'GND重复标签应2个, 实际%d' % len(ms)
s = s[:ms[1].start()] + s[ms[1].end():]
print('03_imu: 删重复GND标签 1 个')
# C2: (102.87,95.25)的V3V3_IMU 加线引上到92.71
old = r'\(global_label "V3V3_IMU"\n\t\t\(shape [^)]*\)\n\t\t\(at 102\.87 95\.25 0\)'
new = '(global_label "V3V3_IMU"\n\t\t(shape bidirectional)\n\t\t(at 102.87 92.71 90)'
s2 = re.sub(old, new, s, count=1)
assert s2 != s, 'V3V3_IMU(102.87,95.25)没找到'
s = s2
w = WIRE % (102.87, 95.25, 102.87, 92.71, uuidlib.uuid4())
i_emb = s.rfind('(embedded_fonts')
s = s[:i_emb] + w + '\n' + s[i_emb:]
print('03_imu: V3V3_IMU第二标签引上 92.71')
save(f, s)
