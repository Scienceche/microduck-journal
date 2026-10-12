#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""两个netlist的连通性diff: {net: sorted(pins)} 完全一致才通过"""
import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def parse(path):
    s = open(path, encoding='utf-8').read()
    d = {}
    for m in re.finditer(r'\(net\n(?:.|\n)*?\(name "([^"]+)"\)\n((?:.|\n)*?)\n\t\t\)', s):
        pins = sorted('%s.%s' % (r, p) for r, p in
                      re.findall(r'\(ref "([^"]+)"\)\n\t*\(pin "([^"]+)"\)', m.group(2)))
        d[m.group(1)] = pins
    return d

a = parse('pcb_rework/net_before_relayout.net')
b = parse('pcb_rework/net_after_relayout.net')
ok = True
for n in sorted(set(a) | set(b)):
    pa, pb = a.get(n), b.get(n)
    if pa != pb:
        ok = False
        print('DIFF %-14s' % n)
        if pa: print('   前:', ' '.join(pa))
        if pb: print('   后:', ' '.join(pb))
print('网表完全一致 ✓✓' if ok else '!!! 有差异 !!!')
sys.exit(0 if ok else 1)
