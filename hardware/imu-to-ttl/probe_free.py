#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分层探测: 顶面只看F.Cu走线+F.Cu SMD焊盘+通孔焊盘/过孔; 底面只看B.Cu走线+通孔"""
import io, sys, re, math
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
s = open('imu_to_ttl.kicad_pcb', encoding='utf-8').read()

def blk_span(s, st):
    d = 0; j = st
    while j < len(s):
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: return j + 1
        j += 1
    return -1

def inter(a, b):
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])

occF, occB = [], []
# 走线: 取整块看layer
for m in re.finditer(r'\(segment\n\t\t\(start ([\d.\-]+) ([\d.\-]+)\)\n\t\t\(end ([\d.\-]+) ([\d.\-]+)\)\n\t\t\(width ([\d.]+)\)\n\t\t\(layer "([FB])\.Cu"\)', s):
    x1, y1, x2, y2, w, lay = [float(g) for g in m.groups()[:5]] + [m.group(6)]
    hw = w/2 + 0.15
    bb = (min(x1,x2)-hw, min(y1,y2)-hw, max(x1,x2)+hw, max(y1,y2)+hw)
    (occF if lay == 'F' else occB).append(bb)
nsegF, nsegB = len(occF), len(occB)
for m in re.finditer(r'\(via\b', s):
    st = m.start(); e = blk_span(s, st)
    vb = s[st:e]
    am = re.search(r'\(at ([\d.\-]+) ([\d.\-]+)\)', vb)
    sm = re.search(r'\(size ([\d.]+)\)', vb)
    x, y, sz = float(am.group(1)), float(am.group(2)), float(sm.group(1))
    r = sz/2 + 0.15
    occF.append((x-r, y-r, x+r, y+r)); occB.append((x-r, y-r, x+r, y+r))
nvia = len(occF) - nsegF

def rot_pt(x, y, r):
    r = int(r) % 360
    return {(0):(x,y),(90):(-y,x),(180):(-x,-y),(270):(y,-x)}[r]

npF = npB = nth = 0
i = 0
U3BOX = (30.0, 5.15, 34.2, 9.35)   # U3身体+焊盘外扩(顶面禁放)
occF.append(U3BOX)
while True:
    st = s.find('\n\t(footprint ', i)
    if st < 0: break
    e = blk_span(s, st+1)
    blk = s[st:e]
    fat = re.search(r'\n\t+\(at ([\d.\-]+) ([\d.\-]+)(?: ([\-\d.]+))?\)', blk)
    refm = re.search(r'\(property "Reference" "([^"]+)"', blk)
    ref = refm.group(1) if refm else '?'
    if fat and ref not in ('R14', 'R15', 'R16'):   # 三颗待放电阻不算占用
        fx, fy = float(fat.group(1)), float(fat.group(2))
        frot = float(fat.group(3) or 0)
        for pm in re.finditer(r'\(pad "[^"]+"[^\n]*\n\t\t+\(at ([\d.\-]+) ([\d.\-]+)(?: ([\-\d.]+))?\)\n\t\t+\(size ([\d.]+) ([\d.]+)\)\n\t\t+\(layers ([^)]+)\)', blk):
            px, py = rot_pt(float(pm.group(1)), float(pm.group(2)), frot)
            w, h = float(pm.group(4)), float(pm.group(5))
            lays = pm.group(6)
            bb = (fx+px-w/2-0.15, fy+py-h/2-0.15, fx+px+w/2+0.15, fy+py+h/2+0.15)
            if '*.Cu' in lays: occF.append(bb); occB.append(bb); nth += 1
            else:
                if 'F.Cu' in lays: occF.append(bb); npF += 1
                if 'B.Cu' in lays: occB.append(bb); npB += 1
    i = e
print(f'F.Cu占用: {len(occF)} (走线{nsegF} 焊盘{npF}+U3禁区) | B.Cu占用: {len(occB)} (走线{nsegB} 通孔{nth})')

def free(cx, cy, hw, hh, occ, gap=0.3):
    box = (cx-hw-gap, cy-hh-gap, cx+hw+gap, cy+hh+gap)
    if box[0] < EDGE[0] or box[1] < EDGE[1] or box[2] > EDGE[2] or box[3] > EDGE[3]:
        return False
    return not any(inter(b, box) for b in occ)

# 板框: Edge.Cuts直边 0-41 x 0-30, 四角1mm圆角(探区远离角)
EDGE = [0.0, 0.0, 41.0, 30.0]

holes = [(9.9,2.15),(31.1,2.15),(9.9,27.85),(31.1,27.85)]
def scan(occ, label, xr, yr):
    U3 = (32.095, 7.245)
    cands = []
    for cyi in yr:
        cy = cyi/20
        for cxi in xr:
            cx = cxi/20
            for orient, hw, hh in (('V', 0.55, 1.1), ('H', 1.1, 0.55)):
                if free(cx, cy, hw, hh, occ):
                    if any(abs(cx-hx) < 2.6 and abs(cy-hy) < 2.6 for hx, hy in holes): continue
                    cands.append((math.hypot(cx-U3[0], cy-U3[1]), cx, cy, orient))
    cands.sort()
    seen = []
    for d, cx, cy, o in cands:
        if any(abs(cx-fx) < 3.2 and abs(cy-fy) < 2.8 for fx, fy, _ in seen): continue
        seen.append((cx, cy, o))
        print(f'  [{label}] ({cx:.2f},{cy:.2f}) {o} 距U3 {d:.1f}mm')
        if len(seen) >= 6: return

print('== 顶面 F.Cu (x24-40, y3-16) ==')
scan(occF, 'TOP', range(480, 800), range(60, 320))
print('== 底面 B.Cu (x24-40, y3-16) ==')
scan(occB, 'BOT', range(480, 800), range(60, 320))
print('== 底面 B.Cu 全板 ==')
scan(occB, 'BOT-ALL', range(50, 800), range(50, 580))
