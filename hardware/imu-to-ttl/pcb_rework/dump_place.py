#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dump 当前布局(全部器件坐标/面)"""
import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
s = open('imu_to_ttl.kicad_pcb', encoding='utf-8').read()
i = 0
out = []
while True:
    st = s.find('\n\t(footprint ', i)
    if st < 0: break
    d = 0; j = st + 1
    while True:
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: break
        j += 1
    blk = s[st:j]
    r = re.search(r'\(property "Reference" "([^"]+)"', blk)
    if r:
        at = re.search(r'\n\t+\(at ([\d.\-]+) ([\d.\-]+)(?: ([\d.\-]+))?\)', blk)
        lay = re.search(r'\n\t+\(layer "(B\.Cu)"', blk)
        out.append((r.group(1), float(at.group(1)), float(at.group(2)), at.group(3) or '0', '底' if lay else '顶'))
    i = j
for ref, x, y, rot, side in sorted(out):
    print('%-6s %s (%6.2f,%6.2f) rot%s' % (ref, side, x, y, rot))
