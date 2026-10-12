#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读各封装F/B.CrtYd实际外框 + 找R15底面新家(避C5外框) + B.Cu空位复核"""
import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
s = open('imu_to_ttl.kicad_pcb', encoding='utf-8').read()

def blk_of(ref):
    i = s.find('(property "Reference" "%s"' % ref)
    assert i > 0, ref
    st = s.rfind('\n\t(footprint ', 0, i)
    d = 0; j = st + 1
    while True:
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: break
        j += 1
    return s[st:j + 1]

def rot_pt(x, y, r):
    r = int(r) % 360
    return {0: (x, y), 90: (-y, x), 180: (-x, -y), 270: (y, -x)}[r]

def crtyd(ref):
    blk = blk_of(ref)
    at = re.search(r'\n\t+\(at ([\d.\-]+) ([\d.\-]+)(?: ([\-\d.]+))?\)', blk)
    fx, fy = float(at.group(1)), float(at.group(2))
    frot = float(at.group(3) or 0)
    xs, ys = [], []
    for pm in re.finditer(r'\(fp_rect\n\t+\(start ([\d.\-]+) ([\d.\-]+)\)\n\t+\(end ([\d.\-]+) ([\d.\-]+)\)\n(?:[^\n]*\n)*?\t+\(layer "([FB])\.CrtYd"', blk):
        for x, y in ((float(pm.group(1)), float(pm.group(2))), (float(pm.group(3)), float(pm.group(4)))):
            rx, ry = rot_pt(x, y, frot)
            xs.append(fx + rx); ys.append(fy + ry)
    for pm in re.finditer(r'\(fp_line\n\t+\(start ([\d.\-]+) ([\d.\-]+)\)\n\t+\(end ([\d.\-]+) ([\d.\-]+)\)\n(?:[^\n]*\n)*?\t+\(layer "([FB])\.CrtYd"', blk):
        for x, y in ((float(pm.group(1)), float(pm.group(2))), (float(pm.group(3)), float(pm.group(4)))):
            rx, ry = rot_pt(x, y, frot)
            xs.append(fx + rx); ys.append(fy + ry)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None

for ref in ('TH1', 'U3', 'R13', 'C5', 'R14', 'R15', 'R16', 'R7'):
    c = crtyd(ref)
    print(ref, '(%.2f,%.2f)-(%.2f,%.2f)' % c if c else '无外框')

def ov(a, b):
    return (round(min(a[2], b[2]) - max(a[0], b[0]), 2), round(min(a[3], b[3]) - max(a[1], b[1]), 2))

for pa, pb in (('TH1', 'U3'), ('U3', 'R13'), ('C5', 'R15')):
    o = ov(crtyd(pa), crtyd(pb))
    print(f'{pa}↔{pb} 重叠dx,dy = {o}')

# B.Cu占用复核(走线bbox+过孔+底面焊盘)
def inter(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])
occB = []
for m in re.finditer(r'\(segment\n\t\t\(start ([\d.\-]+) ([\d.\-]+)\)\n\t\t\(end ([\d.\-]+) ([\d.\-]+)\)\n\t\t\(width ([\d.]+)\)\n\t\t\(layer "B\.Cu"\)', s):
    x1, y1, x2, y2, w = [float(g) for g in m.groups()]
    hw = w / 2 + 0.15
    occB.append((min(x1, x2) - hw, min(y1, y2) - hw, max(x1, x2) + hw, max(y1, y2) + hw))
def blk_span(s, st):
    d = 0; j = st
    while j < len(s):
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: return j + 1
        j += 1
for m in re.finditer(r'\(via\b', s):
    e = blk_span(s, m.start()); vb = s[m.start():e]
    am = re.search(r'\(at ([\d.\-]+) ([\d.\-]+)\)', vb)
    sm = re.search(r'\(size ([\d.]+)\)', vb)
    x, y, sz = float(am.group(1)), float(am.group(2)), float(sm.group(1))
    r = sz / 2 + 0.15
    occB.append((x - r, y - r, x + r, y + r))
i = 0
while True:
    st = s.find('\n\t(footprint ', i)
    if st < 0: break
    e = blk_span(s, st + 1)
    blk = s[st:e]
    refm = re.search(r'\(property "Reference" "([^"]+)"', blk)
    ref = refm.group(1) if refm else '?'
    if ref in ('R14', 'R15', 'R16'):
        i = e; continue
    fat = re.search(r'\n\t+\(at ([\d.\-]+) ([\d.\-]+)(?: ([\-\d.]+))?\)', blk)
    fx, fy = float(fat.group(1)), float(fat.group(2))
    frot = float(fat.group(3) or 0)
    for pm in re.finditer(r'\(pad "[^"]+"[^\n]*\n\t\t+\(at ([\d.\-]+) ([\d.\-]+)(?: ([\-\d.]+))?\)\n\t\t+\(size ([\d.]+) ([\d.]+)\)\n\t\t+\(layers ([^)]+)\)', blk):
        lay = pm.group(6)
        if 'B.Cu' in lay or '*.Cu' in lay:
            px, py = rot_pt(float(pm.group(1)), float(pm.group(2)), frot)
            w, h = float(pm.group(4)), float(pm.group(5))
            occB.append((fx + px - w / 2 - 0.15, fy + py - h / 2 - 0.15, fx + px + w / 2 + 0.15, fy + py + h / 2 + 0.15))
    i = e
# 底面封装外框也加入禁区(避courtyard重叠)
for ref in ('C5',):
    b = crtyd(ref)
    if b: occB.append((b[0], b[1], b[2], b[3]))
print('B.Cu占用项:', len(occB))
def free(cx, cy, hw, hh, gap=0.3):
    box = (cx - hw - gap, cy - hh - gap, cx + hw + gap, cy + hh + gap)
    return not any(inter(b, box) for b in occB)
# R15候选: x31-34, y7.6-10.5, H方向
for cy in [y / 20 for y in range(152, 212)]:
    for cx in [x / 20 for x in range(620, 690)]:
        if free(cx, cy, 1.1, 0.55):
            print('R15候选 H (%.2f,%.2f)' % (cx, cy))
