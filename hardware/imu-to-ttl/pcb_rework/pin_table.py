#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自动提取某页每个符号每个引脚的偏移: 引脚网net -> 就近同网标签锚点"""
import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sch = sys.argv[1]
s = open(sch, encoding='utf-8').read()
ns = open('pcb_rework/net_final.net', encoding='utf-8').read()

def blk_span(s, st):
    d = 0; j = st
    while j < len(s):
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: return j + 1
        j += 1
    return -1

netmap = {}
for m in re.finditer(r'\(net\n(?:.|\n)*?\(name "([^"]+)"\)\n((?:.|\n)*?)\n\t\t\)', ns):
    for r, p in re.findall(r'\(ref "([^"]+)"\)\n\t*\(pin "([^"]+)"\)', m.group(2)):
        netmap[(r, p)] = m.group(1)

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
        syms.append((r.group(1), float(a.group(1)), float(a.group(2)), a.group(3) or '0'))
    i = e

labs = []
for m in re.finditer(r'\(global_label "([^"]*)"\n\t\t\(shape [^)]*\)\n\t\t\(at ([\d.\-]+) ([\d.\-]+)(?: ([\d.]+))?\)', s):
    labs.append((m.group(1), float(m.group(2)), float(m.group(3)), m.group(4) or '0'))

for ref, x, y, rot in syms:
    pins = sorted([(p, n) for p, n in ((p, netmap[(ref, p)]) for p in set(p for r, p in netmap if r == ref)) if not n.startswith('unconnected')])
    print('%-6s @(%7.2f,%7.2f) rot=%s' % (ref, x, y, rot))
    for p, net in pins:
        cands = [(abs(lx-x)+abs(ly-y), lx, ly, lr) for t, lx, ly, lr in labs if t == net]
        cands.sort()
        d, lx, ly, lr = cands[0]
        print('   pin%-2s %-12s 偏(%+6.2f,%+6.2f) [标签rot=%s 距%.1f]' % (p, net, lx-x, ly-y, lr, d))
