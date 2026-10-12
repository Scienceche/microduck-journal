#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用现有页面的标签位置反推各符号引脚绝对坐标(标签锚点=引脚连接点)"""
import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

s = open('04_bus.kicad_sch', encoding='utf-8').read()

def blk_span(s, st):
    d = 0; j = st
    while j < len(s):
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: return j + 1
        j += 1
    return -1

syms = []   # (ref, x, y, rot, blk_span)
i = 0
while True:
    st = s.find('\n\t(symbol\n', i)
    if st < 0: break
    e = blk_span(s, st + 2)
    blk = s[st:e]
    r = re.search(r'\(property "Reference" "([^"]*)"', blk)
    if r:
        a = re.search(r'\(at ([\d.\-]+) ([\d.\-]+)(?: ([\d.]+))?\)', blk)
        syms.append((r.group(1), float(a.group(1)), float(a.group(2)), a.group(3) or '0', (st, e)))
    i = e

labels = []  # (text, x, y, rot, mirror)
for m in re.finditer(r'\(global_label "([^"]*)"\n\t\t\(shape [^)]*\)\n\t\t\(at ([\d.\-]+) ([\d.\-]+)(?: ([\d.]+))?\)((?:.|\n)*?)\n\t\)', s):
    blk = m.group(0)
    labels.append((m.group(1), float(m.group(2)), float(m.group(3)), m.group(4) or '0', '(mirror' in blk))

print('符号:')
for r, x, y, rot, _ in syms:
    print('  %-5s @(%7.2f,%7.2f) rot%s' % (r, x, y, rot))
print('标签(%d):' % len(labels))
for t, x, y, rot, mir in labels:
    print('  %-12s @(%7.2f,%7.2f) rot=%s %s' % (t, x, y, rot, 'mirror' if mir else ''))

# 网表: net -> pins
ns = open('pcb_rework/net_before_relayout.net', encoding='utf-8').read()
netmap = {}   # (ref,pin) -> net
for m in re.finditer(r'\(net\n(?:.|\n)*?\(name "([^"]+)"\)\n((?:.|\n)*?)\n\t\t\)', ns):
    for r, p in re.findall(r'\(ref "([^"]+)"\)\n\t*\(pin "([^"]+)"\)', m.group(2)):
        netmap[(r, p)] = m.group(1)

print()
print('标签↔引脚对应(距离<2.54):')
for t, x, y, rot, mir in labels:
    for r, p in netmap:
        if netmap[(r, p)] != t:
            continue
        sx = next(z[1] for z in syms if z[0] == r)
        sy = next(z[2] for z in syms if z[0] == r)
        if abs(x - sx) < 30 and abs(y - sy) < 25:
            dx, dy = x - sx, y - sy
            print('  %-12s @(%7.2f,%7.2f) ← %s.%s  偏移(%+.2f,%+.2f)' % (t, x, y, r, p, dx, dy))
