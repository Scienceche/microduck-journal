#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""J1-J5 引脚网络查询"""
import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
s = open('pcb_rework/net_final.net', encoding='utf-8').read()
for m in re.finditer(r'\(net\n(?:.|\n)*?\(name "([^"]+)"\)\n((?:.|\n)*?)\n\t\t\)', s):
    pins = re.findall(r'\(ref "(J[0-9])"\)\n\s*\(pin "([0-9]+)"\)', m.group(2))
    if pins:
        print('%-10s %s' % (m.group(1), ' '.join('%s.%s' % q for q in sorted(pins))))
