#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""看某页符号与标签的实际几何: 找出'标签文字伸进符号本体'的(引脚在符号左侧但标签rot0 / 右侧但rot180)"""
import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

f = sys.argv[1]
s = open(f, encoding='utf-8').read()

def blk_span(s, st):
    d = 0; j = st
    while j < len(s):
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: return j + 1
        j += 1
    return -1

syms = []
i = 0
while True:
    st = s.find('\n\t(symbol\n', i)
    if st < 0: break
    e = blk_span(s, st + 2)
    blk = s[st:e]
    r = re.search(r'\(property "Reference" "([^"]*)"', blk)
    if r:
        a = re.search(r'\(at ([\d.\-]+) ([\d.\-]+)(?: ([\d.]+))?\)', blk)
        syms.append((r.group(1), float(a.group(1)), float(a.group(2)), st, e))
    i = e

labels = []
for m in re.finditer(r'\(global_label "([^"]*)"\n\t\t\(shape [^)]*\)\n\t\t\(at ([\d.\-]+) ([\d.\-]+)(?: ([\d.]+))?\)', s):
    labels.append((m.group(1), float(m.group(2)), float(m.group(3)), m.group(4) or '0'))

for ref, x, y, st, e in syms:
    near = [(t, lx, ly, lr) for t, lx, ly, lr in labels if abs(lx - x) < 26 and abs(ly - y) < 26]
    if near:
        print('%-6s @(%7.2f,%7.2f)' % (ref, x, y))
        for t, lx, ly, lr in near:
            dx, dy = lx - x, ly - y
            side = '左' if dx < -1 else ('右' if dx > 1 else '上/下' if abs(dx) <= 1 else '?')
            bad = (side == '左' and lr == '0') or (side == '右' and lr == '180')
            print('   %-12s 偏(%+6.2f,%+6.2f) rot=%-4s [%s端]%s' % (t, dx, dy, lr, side, ' ←文字伸进符号!' if bad else ''))
