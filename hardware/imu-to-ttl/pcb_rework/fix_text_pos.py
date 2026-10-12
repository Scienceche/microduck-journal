#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按扫描结果修正 02_mcu/03_imu 的位号/值文字位置(纯属性, 不动连通性)
规则: x160元件列(FB1/C10/C11/C12/R6)的位号/值统一放左列 x-8.89;
      02_mcu 四个Value放符号左侧; R3.Value同."""
import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

def blk_span(s, st):
    d = 0; j = st
    while j < len(s):
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: return j + 1
        j += 1
    return -1

def sym_block(s, ref):
    i = s.find('(property "Reference" "%s"' % ref)
    assert i > 0, ref
    st = s.rfind('\n\t(symbol\n', 0, i)
    e = blk_span(s, st + 2)
    return st, e, s[st:e]

def move(s, ref, prop, nx, ny):
    st, e, blk = sym_block(s, ref)
    pat = r'(\(property "%s" "[^"]*"\n\t\t\(at )[^\)]+\)' % prop
    nb = re.sub(pat, r'\g<1>%.2f %.2f 0)' % (nx, ny), blk, count=1)
    assert nb != blk, '%s.%s 没找到' % (ref, prop)
    return s[:st] + nb + s[e:]

def origin(s, ref):
    st, e, blk = sym_block(s, ref)
    a = re.search(r'\(at ([\d.\-]+) ([\d.\-]+)', blk)
    return float(a.group(1)), float(a.group(2))

# ---- 03_imu: x160列 位号/值 → 左侧一列 ----
f = '03_imu.kicad_sch'
s = open(f, encoding='utf-8').read()
for ref in ('FB1', 'C10', 'C11', 'C12', 'R6'):
    x, y = origin(s, ref)
    s = move(s, ref, 'Reference', x - 8.89, y - 2.54)
    s = move(s, ref, 'Value', x - 8.89, y + 2.54)
    print('%s 文字 → (%.2f, %.2f/%.2f)' % (ref, x - 8.89, y - 2.54, y + 2.54))
s = move(s, 'R3', 'Value', 46.99, 83.82)
print('R3.Value → (46.99, 83.82)')
open(f, 'w', encoding='utf-8').write(s)
print('%s 平衡: %d' % (f, s.count('(') - s.count(')')))

# ---- 02_mcu: 四个Value → 符号左侧 ----
f = '02_mcu.kicad_sch'
s = open(f, encoding='utf-8').read()
for ref, ny_off in (('C7', 4.5), ('C8', 4.5), ('R2', 4.5), ('C9', 4.5)):
    x, y = origin(s, ref)
    s = move(s, ref, 'Value', x - 7.62, y + ny_off)
    print('%s.Value → (%.2f, %.2f)' % (ref, x - 7.62, y + ny_off))
open(f, 'w', encoding='utf-8').write(s)
print('%s 平衡: %d' % (f, s.count('(') - s.count(')')))
