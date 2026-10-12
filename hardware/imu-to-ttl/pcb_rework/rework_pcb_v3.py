#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PCB清理v3: 删新U3区域旧走线 + U1三焊盘旧走线 + 电阻重新定位到真实空位"""
import sys, io, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
PCB = 'imu_to_ttl.kicad_pcb'
s = open(PCB, encoding='utf-8').read()

def seg_bbox(m):
    x1, y1, x2, y2 = [float(g) for g in m.groups()[:4]]
    return (min(x1,x2), min(y1,y2), max(x1,x2), max(y1,y2))

def blk_span(s, st):
    d = 0; j = st
    while j < len(s):
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: return j + 1
        j += 1
    return -1

# ---- 1) 收集所有几何占用 ----
segs = []   # (start,end,bbox,span)
for m in re.finditer(r'\(segment\n\t\t\(start ([\d.\-]+) ([\d.\-]+)\)\n\t\t\(end ([\d.\-]+) ([\d.\-]+)\)\n\t\t\(width ([\d.]+)\)', s):
    b = seg_bbox(m)
    segs.append((m.start(), m.end(), b, float(m.group(5))))
vias = []
for m in re.finditer(r'\(via\n(?:[^\n]*\n)*?\t\t\(at ([\d.\-]+) ([\d.\-]+)\)\n(?:[^\n]*\n)*?\t\t\(size ([\d.]+)\)', s):
    x, y, sz = float(m.group(1)), float(m.group(2)), float(m.group(3))
    vias.append((m.start(), m.end(), (x-sz/2, y-sz/2, x+sz/2, y+sz/2)))
print(f'走线{len(segs)}条 过孔{len(vias)}个')

# ---- 2) 删与U3新区域相交的走线/过孔 ----
U3BOX = (29.2, 5.3, 35.0, 9.1)  # 新TSSOP-8+间隙
def hit(a, b, margin=0.15):
    return not (a[2]+margin < b[0] or b[2]+margin < a[0] or a[3]+margin < b[1] or b[3]+margin < a[2]-0+0 and b[3]+margin < a[1])
def inter(a, b, margin=0.15):
    return not (a[2] < b[0]-margin or b[2] < a[0]-margin or a[3] < b[1]-margin or b[3] < a[1]-margin)

kill = []
for st, e, b, w in segs:
    if inter(b, U3BOX, 0.2 + w/2):
        kill.append((st, blk_span(s, st)))
for st, e, b in vias:
    if inter(b, U3BOX, 0.2):
        kill.append((st, blk_span(s, st)))
# U1 pad 1/8/10 位置(从DRC得知pad10 @(25.7375,18.425)); U1@(28.6,15.5) TSSOP-20: 计算三焊盘
u1pads = {}  # 由文件实际读取
iu1 = s.find('(property "Reference" "U1"')
stu1 = s.rfind('\n\t(footprint ', 0, iu1)
d=0; j=stu1+1
while True:
    if s[j]=='(': d+=1
    elif s[j]==')':
        d-=1
        if d==0: break
    j+=1
u1blk = s[stu1:j+1]
at1 = re.search(r'\n\t\t\(at ([\d.\-]+) ([\d.\-]+)', u1blk)
u1x, u1y = float(at1.group(1)), float(at1.group(2))
for pm in re.finditer(r'\(pad "(\d+)"[^\n]*\n\t\t\t\(at ([\d.\-]+) ([\d.\-]+)\)\n\t\t\t\(size ([\d.]+) ([\d.]+)\)', u1blk):
    u1pads[pm.group(1)] = (u1x+float(pm.group(2)), u1y+float(pm.group(3)), max(float(pm.group(4)), float(pm.group(5)))/2)
for pn in ('1', '8', '10'):
    px, py, pr = u1pads[pn]
    box = (px-pr-0.1, py-pr-0.1, px+pr+0.1, py+pr+0.1)
    for st, e, b, w in segs:
        if inter(b, box, 0.1 + w/2):
            kill.append((st, blk_span(s, st)))
    for st, e, b in vias:
        if inter(b, box, 0.1):
            kill.append((st, blk_span(s, st)))
# 去重+倒序删除
kill = sorted(set(kill), reverse=True)
for st, e in kill:
    st = s.rfind('\n', 0, st)
    s = s[:st] + s[e:]
print(f'删除走线/过孔 {len(kill)} 处')

# ---- 3) 空位搜索(R14/15/16, 竖放0603: 占位x±0.6, y±1.15, 间隙0.3) ----
# 重新收集(删除后的)占用: 走线+过孔+所有焊盘绝对位置
occ = []
for m in re.finditer(r'\(segment\n\t\t\(start ([\d.\-]+) ([\d.\-]+)\)\n\t\t\(end ([\d.\-]+) ([\d.\-]+)\)\n\t\t\(width ([\d.]+)\)', s):
    occ.append(seg_bbox(m))
for m in re.finditer(r'\(via\n(?:[^\n]*\n)*?\t\t\(at ([\d.\-]+) ([\d.\-]+)\)\n(?:[^\n]*\n)*?\t\t\(size ([\d.]+)\)', s):
    x, y, sz = float(m.group(1)), float(m.group(2)), float(m.group(3))
    occ.append((x-sz/2, y-sz/2, x+sz/2, y+sz/2))
# 焊盘(按footprint原点+旋转换算绝对坐标)
import math
def rot_pt(x, y, r):
    r = r % 360
    if r == 0: return x, y
    if r == 90: return -y, x
    if r == 180: return -x, -y
    if r == 270: return y, -x
    return x, y
i = 0
npads = 0
while True:
    st = s.find('\n\t(footprint ', i)
    if st < 0: break
    d = 0; j = st+1
    while j < len(s):
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: break
        j += 1
    blk = s[st:j+1]
    fat = re.search(r'\n\t+\(at ([\d.\-]+) ([\d.\-]+)(?: ([\-\d.]+))?\)', blk)
    if fat:
        fx, fy = float(fat.group(1)), float(fat.group(2))
        frot = float(fat.group(3) or 0)
        for pm in re.finditer(r'\(pad "[^"]+"[^\n]*\n\t\t+\(at ([\d.\-]+) ([\d.\-]+)(?: ([\-\d.]+))?\)\n\t\t+\(size ([\d.]+) ([\d.]+)\)', blk):
            px, py = rot_pt(float(pm.group(1)), float(pm.group(2)), frot)
            w, h = float(pm.group(4)), float(pm.group(5))
            ax, ay = fx + px, fy + py
            occ.append((ax-w/2-0.1, ay-h/2-0.1, ax+w/2+0.1, ay+h/2+0.1))
            npads += 1
    i = j+1
occ.append(U3BOX)  # U3整个区域禁放(身体在焊盘列之间)
print(f'占用表: {len(occ)}项(含{npads}焊盘+U3禁区)')
def free(cx, cy, hw, hh):
    box = (cx-hw-0.25, cy-hh-0.25, cx+hw+0.25, cy+hh+0.25)
    for b in occ:
        if inter(b[:4], box, 0):
            return False
    return True
# 候选区: 先右半板(U3附近), 不够再全板(避开顶部3mm禁区与安装孔)
def search(x0, x1):
    found = []
    for cy in [y/20 for y in range(90, 545)]:
        for cx in [x/20 for x in range(x0, x1)]:
            if free(cx, cy, 0.55, 1.1):
                okall = all(abs(cx-fx) > 1.6 or abs(cy-fy) > 2.8 for fx, fy in found)
                for hx, hy in ((9.9,2.15),(31.1,2.15),(9.9,27.85),(31.1,27.85)):
                    if abs(cx-hx) < 2.0 and abs(cy-hy) < 2.0:
                        okall = False
                if okall:
                    found.append((cx, cy))
                    if len(found) == 3:
                        return found
    return found
print('DEBUG30 free(30.2,7.95)=', free(30.2, 7.95, 0.55, 1.1))  # DEBUG30
found = search(480, 790)   # 24.0..39.5 优先
if len(found) < 3:
    found = search(50, 790)  # 全板兜底
print('电阻新位置:', found)
assert len(found) == 3, '没找到3个空位!'
# ---- 4) 移动三颗电阻 ----
for i, ref in enumerate(('R14', 'R15', 'R16'), 0):
    ir = s.find('(property "Reference" "%s"' % ref)
    str_ = s.rfind('\n\t(footprint ', 0, ir)
    d = 0; j = str_+1
    while True:
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: break
        j += 1
    blk = s[str_:j+1]
    nx, ny = found[i]
    nblk = re.sub(r'\t\(at [\d.]+ [\d.]+ 90\)', '\t(at %.3f %.3f 90)' % (nx, ny), blk, count=1)
    assert nblk != blk, ref + ' at替换失败'
    s = s[:str_] + nblk + s[j+1:]
    print(f'{ref} → ({nx:.2f},{ny:.2f}) ✓')

open(PCB, 'w', encoding='utf-8').write(s)
print('清理与重定位完成, 括号平衡:', sum(1 if c=='(' else -1 for c in s if c in '()'))
