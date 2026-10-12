#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PCB接插件盘点: J*/TH* 封装/位置/旋转/层 + 全板最高元件估计"""
import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

s = open('imu_to_ttl.kicad_pcb', encoding='utf-8').read()

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
    st = s.find('\n\t(footprint ', i)
    if st < 0: break
    e = blk_span(s, st + 1)
    blk = s[st:e]
    r = re.search(r'\(property "Reference" "([^"]*)"', blk)
    if r:
        fp = re.search(r'\(footprint "([^"]+)"', blk)
        at = re.search(r'\n\t+\(at ([\d.\-]+) ([\d.\-]+)(?: ([\d.\-]+))?\)', blk)
        lay = re.search(r'\n\t+\(layer "([^"]+)"\)', blk)
        npad = len(re.findall(r'\(pad ', blk))
        pads = re.findall(r'\(pad "([^"]+)" (smd|thru_hole)', blk)
        syms.append((r.group(1), fp.group(1) if fp else '?', float(at.group(1)), float(at.group(2)),
                     at.group(3) or '0', lay.group(1) if lay else '?', npad, pads))
    i = e

print('%-6s %-58s %9s %9s %5s %6s %s' % ('REF', 'FOOTPRINT', 'X', 'Y', 'ROT', 'LAYER', 'PADS'))
for ref, fp, x, y, rot, lay, npad, pads in sorted(syms):
    tag = ''
    if re.match(r'^(J\d|TH\d|TP\d)', ref):
        tag = ' ◀ 接插件'
        print('%-6s %-58s %9.2f %9.2f %5s %6s %2d%s' % (ref, fp[:58], x, y, rot, lay, npad, tag))

print()
print('=== 板框 === 41 x 30 (0..41, 0..30), 安装孔 B1(9.9,27.85) B2(31.1,27.85) B3(31.1,2.15) B4(9.9,2.15)')
print()
print('=== 各接插件焊盘绝对坐标(判断线从哪边出) ===')
for ref, fp, x, y, rot, lay, npad, pads in sorted(syms):
    if not re.match(r'^(J\d|TH\d)', ref):
        continue
    i2 = s.find('(property "Reference" "%s"' % ref)
    st = s.rfind('\n\t(footprint ', 0, i2)
    e = blk_span(s, st + 1)
    blk = s[st:e]
    print('%s (%s @%.1f,%.1f rot%s %s):' % (ref, fp.split(':')[-1][:34], x, y, rot, lay))
    for pm in re.finditer(r'\(pad "([^"]+)"[^\n]*\n\t+\(at ([\d.\-]+) ([\d.\-]+)', blk):
        print('   pad%-3s 偏(%+6.2f,%+6.2f) → 绝对(%6.2f,%6.2f)' % (pm.group(1), float(pm.group(2)), float(pm.group(3)),
              x + float(pm.group(2)), y + float(pm.group(3))))
