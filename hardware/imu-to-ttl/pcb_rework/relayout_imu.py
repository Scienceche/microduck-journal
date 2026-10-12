#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""03_imu 整页重排: SPI六线实线化 + V3V3_IMU竖轨(FB1→C10/11/12→R6) + U2顶/底电源实线"""
import io, sys, re, uuid as uuidlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

SRC = '03_imu.kicad_sch'
s = open(SRC, encoding='utf-8').read()

def blk_span(s, st):
    d = 0; j = st
    while j < len(s):
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: return j + 1
        j += 1
    return -1

i_lib = s.find('(lib_symbols')
i_emb = s.rfind('(embedded_fonts')
head = s[:i_lib]
libsec = s[i_lib:blk_span(s, i_lib)]
tail = s[i_emb:]

tmpl, flg_blocks, text_blocks = {}, [], []
i = 0
while True:
    st = s.find('\n\t(symbol\n', i)
    if st < 0: break
    e = blk_span(s, st + 2)
    blk = s[st:e]
    r = re.search(r'\(property "Reference" "([^"]*)"', blk)
    if r:
        if r.group(1).startswith('#FLG'):
            flg_blocks.append(blk)
        else:
            tmpl[r.group(1)] = blk
    i = e
i = 0
while True:
    st = s.find('\n\t(text "', i)
    if st < 0: break
    e = blk_span(s, st + 2)
    text_blocks.append(s[st:e])
    i = e

flg_labels = []
for m in re.finditer(r'\(global_label "([^"]*)"\n\t\t\(shape [^)]*\)\n\t\t\(at ([\d.\-]+) ([\d.\-]+)', s):
    if float(m.group(3)) < 50:
        st = m.start()
        e = blk_span(s, st)
        flg_labels.append(s[st:e])

def fresh(blk):
    return re.sub(r'"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"',
                  lambda m: '"%s"' % uuidlib.uuid4(), blk)

SYMS = [
    # (ref, x, y, rot, ref_dx, ref_dy, val_dx, val_dy)
    ('U2',  100.33, 110.49, 0,  -8.89,-19.05, 10.16, 20.32),
    ('FB1', 160.02,  59.69, 0,   8.90, -2.54,  8.90,  2.54),
    ('C10', 160.02,  80.01, 0,  -8.89, -2.54, -8.89,  4.50),
    ('C11', 160.02,  95.25, 0,  -8.89, -2.54, -8.89,  4.50),
    ('C12', 160.02, 110.49, 0,  -8.89, -2.54, -8.89,  2.54),
    ('R3',   76.20, 130.81, 180, 7.62, -2.54,  7.62,  2.54),
    ('R4',   58.42,  97.79, 180, -7.62, -2.54, -7.62, 2.54),
    ('R5',   53.34, 100.33, 180, -7.62, -2.54, -7.62, 2.54),
    ('R6',  179.07, 134.62, 0,  -7.62, -2.54, -7.62,  2.54),
]

WIRES = [
    # SPI 左六行
    (69.85, 102.87, 85.09, 102.87),   # MISO
    (58.42, 105.41, 85.09, 105.41),   # SDx (延至R4柱)
    (53.34, 107.95, 85.09, 107.95),   # SCx (延至R5柱)
    (69.85, 113.03, 85.09, 113.03),   # MOSI
    (69.85, 115.57, 85.09, 115.57),   # SCK
    (69.85, 118.11, 85.09, 118.11),   # CS
    (58.42, 101.60, 58.42, 105.41),   # R4.1(SDx)↓
    (53.34, 104.14, 53.34, 107.95),   # R5.1(SCx)↓
    (76.20, 118.11, 76.20, 127.00),   # R3.2(SPI_CS)↓挂行下
    # U2 顶电源 → V3V3_IMU 竖轨
    (100.33, 95.25, 100.33, 88.90),
    (102.87, 95.25, 102.87, 88.90),   # U2.8 VDD ↑
    (100.33, 88.90, 170.18, 88.90),
    (170.18, 63.50, 170.18, 106.68),  # 竖轨(FB1.2→C12.1)
    (160.02, 63.50, 170.18, 63.50),   # FB1.2→轨
    (160.02, 76.20, 170.18, 76.20),   # C10.1→轨
    (160.02, 91.44, 170.18, 91.44),   # C11.1→轨
    (160.02, 106.68, 170.18, 106.68), # C12.1→轨
    # U2 右列
    (115.57, 102.87, 130.81, 102.87), # INT1
    (115.57, 105.41, 130.81, 105.41), # INT2
    (115.57, 115.57, 167.64, 115.57), # OCS_AUX
    (167.64, 115.57, 167.64, 130.81),
    (167.64, 130.81, 179.07, 130.81), # →R6.1
    # U2 底 GND
    (100.33, 125.73, 100.33, 130.81),
]

JUNCS = [
    (76.20, 118.11), (170.18, 76.20), (102.87, 88.90),
    (170.18, 88.90), (170.18, 91.44),
]

LABELS = [
    ('SPI_MISO', 69.85, 102.87, 0),
    ('SDx',      62.23, 105.41, 0),
    ('SCx',      57.15, 107.95, 0),
    ('SPI_MOSI', 69.85, 113.03, 0),
    ('SPI_SCK',  69.85, 115.57, 0),
    ('SPI_CS',   69.85, 118.11, 0),
    ('GND',      58.42,  93.98, 0),
    ('GND',      53.34,  96.52, 0),
    ('+3V3',     76.20, 134.62, 0),
    ('V3V3_IMU', 119.38, 88.90, 0),
    ('+3V3',    160.02,  55.88, 0),
    ('GND',     160.02,  83.82, 0),
    ('GND',     160.02,  99.06, 0),
    ('GND',     160.02, 114.30, 0),
    ('IMU_INT1', 130.81, 102.87, 0),
    ('IMU_INT2', 130.81, 105.41, 0),
    ('OCS_AUX',  121.92, 115.57, 0),
    ('GND',     100.33, 130.81, 0),
    ('+3V3',    179.07, 138.43, 0),
]

out = [head, libsec]
for blk in flg_blocks + flg_labels:
    out.append(blk)

for ref, x, y, rot, rdx, rdy, vdx, vdy in SYMS:
    blk = fresh(tmpl[ref])
    blk = re.sub(r'\n\t\t\(at [\d.\- ]+\)\n\t\t\(unit', '\n\t\t(at %.2f %.2f %d)\n\t\t(unit' % (x, y, rot), blk, count=1)
    blk = re.sub(r'(\(property "Reference" "[^"]+"\n\t\t\(at )[^\)]+\)',
                 r'\g<1>%.2f %.2f 0)' % (x + rdx, y + rdy), blk, count=1)
    blk = re.sub(r'(\(property "Value" "[^"]+"\n\t\t\(at )[^\)]+\)',
                 r'\g<1>%.2f %.2f 0)' % (x + vdx, y + vdy), blk, count=1)
    out.append(blk)

for x1, y1, x2, y2 in WIRES:
    out.append('\n\t(wire\n\t\t(pts (xy %.2f %.2f) (xy %.2f %.2f))\n\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n\t\t(uuid "%s")\n\t)'
               % (x1, y1, x2, y2, uuidlib.uuid4()))
for x, y in JUNCS:
    out.append('\n\t(junction (at %.2f %.2f) (diameter 0) (color 0 0 0 0)\n\t\t(uuid "%s")\n\t)' % (x, y, uuidlib.uuid4()))
for t, x, y, rot in LABELS:
    out.append('\n\t(global_label "%s"\n\t\t(shape bidirectional)\n\t\t(at %.2f %.2f %d)\n\t\t(fields_autoplaced yes)\n\t\t(effects\n\t\t\t(font (size 1.27 1.27)) (justify left)\n\t\t)\n\t\t(uuid "%s")\n\t)'
               % (t, x, y, rot, uuidlib.uuid4()))

# U2.11 SDO_Aux 设计不用: 保留不连接旗标(格式与原版一致)
out.append('\n\t(no_connect (at 115.57 113.03) (uuid "%s"))\n' % uuidlib.uuid4())

ty = 185.0
for tb in text_blocks:
    tb = re.sub(r'\(at [\d.\- ]+\)', '(at 25.40 %.1f 0)' % ty, tb, count=1)
    out.append(fresh(tb))
    ty += 6.5
out.append('\n\t(text "SDx/SCx pull-downs (R4/R5) terminate the aux I3C bus; OCS_AUX pull-up R6 per datasheet"\n\t\t(at 25.40 %.1f 0)\n\t\t(effects (font (size 1 1)) (justify left bottom))\n\t\t(uuid "%s")\n\t)' % (ty, uuidlib.uuid4()))

out.append(tail if tail.startswith('\n') else '\n' + tail)
new = ''.join(out)
open(SRC, 'w', encoding='utf-8').write(new)
print('重排完成: 符号%d(+FLG%d) 导线%d 结点%d 标签%d(+FLG%d) 文字%d' %
      (len(SYMS), len(flg_blocks), len(WIRES), len(JUNCS), len(LABELS), len(flg_labels), len(text_blocks) + 1))
print('括号平衡:', new.count('(') - new.count(')'))
