#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从netlist提取本页(04_bus)元件的连接关系 (多行块解析)"""
import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

s = open(sys.argv[1] if len(sys.argv) > 1 else 'pcb_rework/net_before_relayout.net', encoding='utf-8').read()
want = {'U3', 'R7', 'R8', 'R11', 'R12', 'R13', 'R14', 'R15', 'R16', 'D3', 'TH1', 'C13'}

for m in re.finditer(r'\(net\n(?:.|\n)*?\(name "([^"]+)"\)\n((?:.|\n)*?)\n\t\t\)', s):
    name = m.group(1)
    nodes = re.findall(r'\(ref "([^"]+)"\)\n\t*\(pin "([^"]+)"\)', m.group(2))
    pins = [f'{r}.{p}' for r, p in nodes if r in want]
    if pins:
        others = [f'{r}.{p}' for r, p in nodes if r not in want]
        print('%-14s %-46s | 板外: %s' % (name, ' '.join(pins), ' '.join(others)))
