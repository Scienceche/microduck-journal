#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""02_mcu: 删掉 U1.1/U1.8 上失效的 no_connect 标记(引脚已接 RX_EN/TX_EN_N)"""
import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

p = '02_mcu.kicad_sch'
s = open(p, encoding='utf-8').read()
n = 0
for xy in ('74.93 107.95', '125.73 97.79'):
    pat = r'\n\t\(no_connect\n\t\t\(at %s\)\n\t\t\(uuid "[^"]*"\)\n\t\)' % re.escape(xy).replace(r'\ ', ' ')
    s2 = re.sub(r'\n\t\(no_connect \(at %s\) \(uuid "[0-9a-f\-]+"\)\)' % re.escape(xy), '', s, count=1)
    if s2 != s:
        n += 1
        s = s2
open(p, 'w', encoding='utf-8').write(s)
print('删掉 no_connect %d 个, 括号平衡:' % n, s.count('(') - s.count(')'))
