#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原理图 Footprint 属性换卧式: J2→1x7弯针, J3/J4/J5→1x3弯针"""
import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

CH = [
    ('02_mcu.kicad_sch', 'J2',
     'Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical',
     'Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Horizontal'),
    ('05_connectors.kicad_sch', 'J3',
     'Connector_JST:JST_PH_B3B-PH-K_1x03_P2.00mm_Vertical',
     'Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Horizontal'),
    ('05_connectors.kicad_sch', 'J4',
     'Connector_JST:JST_PH_B3B-PH-K_1x03_P2.00mm_Vertical',
     'Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Horizontal'),
    ('05_connectors.kicad_sch', 'J5',
     'Connector_JST:JST_PH_B3B-PH-K_1x03_P2.00mm_Vertical',
     'Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Horizontal'),
]

for f, ref, old, new in CH:
    s = open(f, encoding='utf-8').read()
    i = s.find('(property "Reference" "%s"' % ref)
    assert i > 0, ref
    # 找该符号块内的 Footprint 属性
    j = s.find('(property "Footprint"', i)
    k = s.find('\n\t(symbol', i + 10)  # 下一符号
    if k > 0 and k < j:
        print('%s: 没找到Footprint属性!' % ref); sys.exit(1)
    seg = s[j:s.find(')', s.find('"', s.find('"', j) + 1) + 1)]
    old_seg = s[j:j + len(seg)]
    new_seg = old_seg.replace(old, new)
    assert new_seg != old_seg, '%s Footprint替换失败: %s' % (ref, old_seg[:120])
    s = s[:j] + new_seg + s[j + len(seg):]
    open(f, 'w', encoding='utf-8').write(s)
    print('%s Footprint → %s ✓' % (ref, new))
