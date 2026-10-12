#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""01_power 整页重排: 主功率路径实线化(J1→F1→Q1→VBAT母线→F2→U4→+3V3)
栅极网络收拢Q1下方; TVS/去耦挂母线下方; #FLG电源标志与顶部标签原样保留"""
import io, sys, re, uuid as uuidlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

SRC = '01_power.kicad_sch'
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

# 模板与文字块
tmpl, flg_blocks, text_blocks = {}, [], []
i = 0
while True:
    st = s.find('\n\t(symbol\n', i)
    if st < 0: break
    e = blk_span(s, st + 2)
    blk = s[st:e]
    r = re.search(r'\(property "Reference" "([^"]*)"', blk)
    if r:
        a = re.search(r'\(at ([\d.\-]+) ([\d.\-]+)', blk)
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

# 顶部 #FLG 附带标签(y<50)原样保留
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
    ('J1',   40.64, 110.49, 0,   0, -8.89,  0,  8.89),
    ('F1',   58.42, 109.22, 90,  0, -6.35,  0,  6.35),
    ('Q1',  100.33, 110.49, 180, 0,-10.16,  0, 10.16),
    ('C14', 128.27, 139.70, 0, -7.62, -1.27, -7.62, 6.35),
    ('R9',  139.70, 139.70, 0, -7.62, -1.27, -7.62, 6.35),
    ('R10', 151.13, 139.70, 0, -7.62, -1.27, -7.62, 6.35),
    ('D1',  160.02, 127.00, 270,  8.90, -5.08,  8.90, 2.54),
    ('C1',  177.80, 127.00, 0,  -8.89, -1.27, -8.89, 6.35),
    ('C2',  185.42, 127.00, 0,   8.89, -1.27,  8.89, 6.35),
    ('F2',  195.58, 110.49, 90,  0, -6.35,  0,  6.35),
    ('D2',  207.01, 127.00, 270, -8.90, -1.27, -8.90, 6.35),
    ('C3',  218.44, 127.00, 0,  -8.89, -1.27, -8.89, 6.35),
    ('C4',  229.87, 127.00, 0,  -8.89, -1.27, -8.89, 6.35),
    ('U4',  250.19, 113.03, 0,   0, -8.89,  0, 10.16),
    ('R1',  273.05, 120.65, 0, -7.62, -2.54, -7.62, 2.54),
    ('LED1',273.05, 135.89, 270, -7.62, -1.27, -7.62, 1.27),
    ('C5',  285.75, 127.00, 0, -7.62, -2.54, -7.62, 2.54),
    ('C6',  285.75, 143.51, 0, -7.62, -2.54, -7.62, 2.54),
    ('TP8',  60.96, 163.83, 0,   0, -6.35,  0,  6.35),
    ('TP9',  60.96, 173.99, 0,   0, -6.35,  0,  6.35),
]

WIRES = [
    # VBAT_RAW: J1.1 → F1.1
    (33.02, 109.22, 54.61, 109.22),
    # VBAT_FUSED: F1.2 → Q1左列竖轨(4脚)
    (62.23, 109.22, 85.09, 109.22),
    (85.09, 107.95, 85.09, 115.57),
    (85.09, 107.95, 90.17, 107.95),
    (85.09, 110.49, 90.17, 110.49),
    (85.09, 113.03, 90.17, 113.03),
    (85.09, 115.57, 90.17, 115.57),
    # VBAT: Q1右列竖轨 → 主母线 → F2.1
    (110.49, 110.49, 115.57, 110.49),
    (115.57, 110.49, 115.57, 115.57),
    (115.57, 113.03, 110.49, 113.03),
    (115.57, 115.57, 110.49, 115.57),
    (115.57, 110.49, 191.77, 110.49),
    # GATE: Q1.4 → 下降 → 栅极横轨
    (110.49, 105.41, 120.65, 105.41),
    (120.65, 105.41, 120.65, 130.81),
    (120.65, 130.81, 151.13, 130.81),
    (128.27, 130.81, 128.27, 135.89),
    (139.70, 130.81, 139.70, 135.89),
    (151.13, 130.81, 151.13, 135.89),
    # R10.2 → VBAT (绕行右上升至母线)
    (151.13, 143.51, 151.13, 153.67),
    (151.13, 153.67, 189.23, 153.67),
    (189.23, 153.67, 189.23, 110.49),
    # VBAT TVS/去耦柱
    (160.02, 110.49, 160.02, 123.19),
    (177.80, 110.49, 177.80, 123.19),
    (185.42, 110.49, 185.42, 123.19),
    # VBAT_LOGIC: F2.2 → U4.2(IN)
    (199.39, 110.49, 242.57, 110.49),
    (207.01, 110.49, 207.01, 123.19),
    (218.44, 110.49, 218.44, 123.19),
    (229.87, 110.49, 229.87, 123.19),
    # +3V3: U4.3(OUT) → R1 → LED1
    (257.81, 110.49, 273.05, 110.49),
    (273.05, 110.49, 273.05, 116.84),
    (273.05, 124.46, 273.05, 132.08),
]

JUNCS = [
    (85.09, 109.22), (85.09, 110.49), (85.09, 113.03),
    (115.57, 113.03),
    (160.02, 110.49), (177.80, 110.49), (185.42, 110.49), (189.23, 110.49),
    (128.27, 130.81), (139.70, 130.81),
    (207.01, 110.49), (218.44, 110.49), (229.87, 110.49),
]

LABELS = [
    ('VBAT_RAW',   41.91, 109.22, 0),
    ('GND',        33.02, 111.76, 180),
    ('VBAT_FUSED', 71.12, 109.22, 0),
    ('Q1_GATE',   120.65, 121.92, 90),
    ('VBAT',      154.94, 110.49, 0),
    ('GND',       128.27, 143.51, 0),
    ('GND',       139.70, 143.51, 0),
    ('GND',       160.02, 130.81, 0),
    ('GND',       177.80, 130.81, 0),
    ('GND',       185.42, 130.81, 0),
    ('VBAT_LOGIC', 233.68, 110.49, 0),
    ('GND',       207.01, 130.81, 0),
    ('GND',       218.44, 130.81, 0),
    ('GND',       229.87, 130.81, 0),
    ('+3V3',      262.89, 110.49, 0),
    ('GND',       250.19, 118.11, 0),
    ('LED_A',     273.05, 127.00, 90),
    ('GND',       273.05, 139.70, 0),
    ('+3V3',      285.75, 123.19, 0),
    ('GND',       285.75, 130.81, 0),
    ('+3V3',      285.75, 139.70, 0),
    ('GND',       285.75, 147.32, 0),
    ('VBAT',       60.96, 163.83, 0),
    ('GND',        60.96, 173.99, 0),
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

# 注释挪到底部
ty = 190.0
for tb in text_blocks:
    tb = re.sub(r'\(at [\d.\- ]+\)', '(at 25.40 %.1f 0)' % ty, tb, count=1)
    out.append(fresh(tb))
    ty += 6.5
out.append('\n\t(text "R10 1M + R9 100k + C14 100n on Q1 gate: reverse protection with ~10ms soft start"\n\t\t(at 25.40 %.1f 0)\n\t\t(effects (font (size 1 1)) (justify left bottom))\n\t\t(uuid "%s")\n\t)' % (ty, uuidlib.uuid4()))

out.append(tail if tail.startswith('\n') else '\n' + tail)
new = ''.join(out)
open(SRC, 'w', encoding='utf-8').write(new)
print('重排完成: 符号%d(+FLG%d) 导线%d 结点%d 标签%d(+FLG标签%d) 文字%d' %
      (len(SYMS), len(flg_blocks), len(WIRES), len(JUNCS), len(LABELS), len(flg_labels), len(text_blocks) + 1))
print('括号平衡:', new.count('(') - new.count(')'))
