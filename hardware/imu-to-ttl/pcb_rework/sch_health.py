#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""所有原理图页面健康自检: 符号数/标签数/导线数/文字重叠粗查"""
import io, sys, re, glob
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

for f in ['imu_to_ttl.kicad_sch', '01_power.kicad_sch', '02_mcu.kicad_sch',
          '03_imu.kicad_sch', '04_bus.kicad_sch', '05_connectors.kicad_sch']:
    s = open(f, encoding='utf-8').read()
    nsym = len(re.findall(r'\n\t\(symbol\n\t\t\(lib_id', s))
    nwire = len(re.findall(r'\n\t\(wire\b', s))
    njunc = len(re.findall(r'\n\t\(junction\b', s))
    nglabel = len(re.findall(r'\n\t\(global_label\b', s))
    nlabel = len(re.findall(r'\n\t\(label\b', s))
    npage = len(re.findall(r'\n\t\(text "', s))
    print('%-24s 符号%3d 导线%3d 结点%3d 全局标签%3d 局部标签%3d 文字%2d' %
          (f, nsym, nwire, njunc, nglabel, nlabel, npage))
