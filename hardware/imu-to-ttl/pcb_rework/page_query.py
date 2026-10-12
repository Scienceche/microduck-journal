#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指定页元件的连通性 + 符号位置/引脚几何(标签锚点反推)"""
import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sch = sys.argv[1]
want = set(sys.argv[2].split(','))

def blk_span(s, st):
    d = 0; j = st
    while j < len(s):
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: return j + 1
        j += 1
    return -1

# 连通性
ns = open('pcb_rework/net_final.net', encoding='utf-8').read()
print('=== 连通性 ===')
for m in re.finditer(r'\(net\n(?:.|\n)*?\(name "([^"]+)"\)\n((?:.|\n)*?)\n\t\t\)', ns):
    pins = ['%s.%s' % (r, p) for r, p in re.findall(r'\(ref "([^"]+)"\)\n\t*\(pin "([^"]+)"\)', m.group(2))]
    mine = [p for p in pins if p.split('.')[0] in want]
    if mine:
        others = [p for p in pins if p.split('.')[0] not in want]
        print('%-14s %-34s | 外部: %s' % (m.group(1), ' '.join(mine), ' '.join(others)))

# 符号与标签几何
s = open(sch, encoding='utf-8').read()
print()
print('=== 符号(位置/旋转/值) ===')
i = 0
while True:
    st = s.find('\n\t(symbol\n', i)
    if st < 0: break
    e = blk_span(s, st + 2)
    blk = s[st:e]
    r = re.search(r'\(property "Reference" "([^"]*)"', blk)
    if r and r.group(1) in want:
        a = re.search(r'\(at ([\d.\-]+) ([\d.\-]+)(?: ([\d.]+))?\)', blk)
        v = re.search(r'\(property "Value" "([^"]*)"', blk)
        dnp = ' DNP' if '(dnp yes)' in blk else ''
        print('%-6s @(%7.2f,%7.2f) rot=%-4s %-16s%s' % (r.group(1), float(a.group(1)), float(a.group(2)), a.group(3) or 0, (v.group(1) if v else '')[:16], dnp))
    i = e
print()
print('=== 标签 → 引脚(锚点即引脚位置) ===')
syms = []
i = 0
while True:
    st = s.find('\n\t(symbol\n', i)
    if st < 0: break
    e = blk_span(s, st + 2)
    blk = s[st:e]
    r = re.search(r'\(property "Reference" "([^"]*)"', blk)
    if r and r.group(1) in want:
        a = re.search(r'\(at ([\d.\-]+) ([\d.\-]+)(?: ([\d.]+))?\)', blk)
        syms.append((r.group(1), float(a.group(1)), float(a.group(2))))
    i = e
netmap = {}
for m in re.finditer(r'\(net\n(?:.|\n)*?\(name "([^"]+)"\)\n((?:.|\n)*?)\n\t\t\)', ns):
    for r, p in re.findall(r'\(ref "([^"]+)"\)\n\t*\(pin "([^"]+)"\)', m.group(2)):
        netmap[(r, p)] = m.group(1)
for m in re.finditer(r'\(global_label "([^"]*)"\n\t\t\(shape [^)]*\)\n\t\t\(at ([\d.\-]+) ([\d.\-]+)(?: ([\d.]+))?\)', s):
    t, x, y, rot = m.group(1), float(m.group(2)), float(m.group(3)), m.group(4) or '0'
    for ref, sx, sy in syms:
        if abs(x - sx) < 40 and abs(y - sy) < 30:
            for (r2, p2), net in netmap.items():
                if r2 == ref and net == t:
                    print('%-6s.%s %-14s 锚(%7.2f,%7.2f) rot=%s 偏(%+.2f,%+.2f)' % (ref, p2, t, x, y, rot, x - sx, y - sy))
