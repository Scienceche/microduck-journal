#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全工程扫描: 符号位号/值文字 与 导线段、全局标签文字 的碰撞
文字bbox: 宽≈0.85*字数(1.27字号), 高1.4; 属性默认中心对齐, 标签按rot方向延伸"""
import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

FILES = ['01_power.kicad_sch', '02_mcu.kicad_sch', '03_imu.kicad_sch',
         '04_bus.kicad_sch', '05_connectors.kicad_sch']

def blk_span(s, st):
    d = 0; j = st
    while j < len(s):
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: return j + 1
        j += 1
    return -1

def text_bbox(x, y, text, mode, rot=0):
    w = 0.85 * max(len(text), 1) + 0.3
    h = 1.5
    if mode == 'center':
        return (x - w/2, y - h/2, x + w/2, y + h/2)
    # label: 从锚点沿rot方向延伸
    if rot == 0:   return (x, y - h/2, x + w, y + h/2)
    if rot == 180: return (x - w, y - h/2, x, y + h/2)
    if rot == 90:  return (x - h/2, y - w, x + h/2, y)
    return (x - h/2, y, x + h/2, y + w)

def hit(a, b, pad=0.15):
    return not (a[2]+pad < b[0] or b[2]+pad < a[0] or a[3]+pad < b[1] or b[3]+pad < a[1])

total = 0
for f in FILES:
    s = open(f, encoding='utf-8').read()
    # 导线
    wires = []
    for m in re.finditer(r'\(wire\n\t\t\(pts \(xy ([\d.\-]+) ([\d.\-]+)\) \(xy ([\d.\-]+) ([\d.\-]+)\)\)', s):
        x1, y1, x2, y2 = [float(g) for g in m.groups()]
        wires.append((min(x1,x2)-0.2, min(y1,y2)-0.2, max(x1,x2)+0.2, max(y1,y2)+0.2))
    # 标签文字bbox
    labs = []
    for m in re.finditer(r'\(global_label "([^"]*)"\n\t\t\(shape [^)]*\)\n\t\t\(at ([\d.\-]+) ([\d.\-]+)(?: ([\d.]+))?\)', s):
        labs.append((m.group(1),) + text_bbox(float(m.group(2)), float(m.group(3)), m.group(1), 'label', int(m.group(4) or 0)))
    # 符号Reference/Value
    n = 0
    i = 0
    out = []
    while True:
        st = s.find('\n\t(symbol\n', i)
        if st < 0: break
        e = blk_span(s, st + 2)
        blk = s[st:e]
        r = re.search(r'\(property "Reference" "([^"]*)"', blk)
        if r:
            ref = r.group(1)
            for prop in ('Reference', 'Value'):
                pm = re.search(r'\(property "%s" "([^"]*)"\n\t\t\(at ([\d.\-]+) ([\d.\-]+)' % prop, blk)
                if not pm: continue
                txt, x, y = pm.group(1), float(pm.group(2)), float(pm.group(3))
                if not txt: continue
                bb = text_bbox(x, y, txt, 'center')
                for wbb in wires:
                    if hit(bb, wbb):
                        out.append('  %s.%s "%s" @(%.2f,%.2f) 压导线段(%.1f,%.1f)-(%.1f,%.1f)' %
                                   (ref, prop, txt[:16], x, y, wbb[0], wbb[1], wbb[2], wbb[3])); n += 1
                for item in labs:
                    if hit(bb, item[1:5]):
                        out.append('  %s.%s "%s" @(%.2f,%.2f) 压标签%s @(%.1f,%.1f)' %
                                   (ref, prop, txt[:16], x, y, item[0], item[1], item[2])); n += 1
        i = e
    print('%s: %d 处' % (f, n))
    for line in out: print(line)
    total += n
print('---- 总计 %d 处 ----' % total)
