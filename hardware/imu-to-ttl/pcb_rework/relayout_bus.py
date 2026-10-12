#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""04_bus 整页重排: 真实导线+结点的人读版式; 网表必须与重排前完全一致(外部diff验证)"""
import io, sys, re, uuid as uuidlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

SRC = '04_bus.kicad_sch'
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

# ---- 1) 取头(lib_symbols之前) / 尾(embedded_fonts起) / 原符号块模板 / 文字块 ----
i_lib = s.find('(lib_symbols')
i_emb = s.rfind('(embedded_fonts')   # 每个内嵌库符号里也有一个, 取文件级那个
head = s[:i_lib]
libsec_end = blk_span(s, i_lib)
libsec = s[i_lib:libsec_end]
tail = s[i_emb:]

tmpl = {}   # ref -> block
i = 0
while True:
    st = s.find('\n\t(symbol\n', i)
    if st < 0: break
    e = blk_span(s, st + 2)
    blk = s[st:e]
    r = re.search(r'\(property "Reference" "([^"]*)"', blk)
    if r: tmpl[r.group(1)] = blk
    i = e

texts = []  # (at, body)
for m in re.finditer(r'\n\t\(text "[^"]*"\n\t\t\(at ([\d.\-]+) ([\d.\-]+)', s):
    pass
# 文字块整体克隆后改at
text_blocks = []
i = 0
while True:
    st = s.find('\n\t(text "', i)
    if st < 0: break
    e = blk_span(s, st + 2)
    text_blocks.append(s[st:e])
    i = e

def fresh(blk):
    """刷新所有uuid"""
    return re.sub(r'"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"',
                  lambda m: '"%s"' % uuidlib.uuid4(), blk)

# ---- 2) 布局规格 ----
# (ref, x, y, rot, ref_dy, val_dy)  ref/value属性相对偏移
SYMS = [
    # (ref, x, y, rot, ref_dx, ref_dy, val_dx, val_dy)  偏移相对符号中心
    ('U3',  120.65, 110.49, 0,     0, -7.62,   0, 8.89),
    ('R14',  38.10,  83.82, 90,    0, -5.08,   0, 3.81),
    ('R7',   38.10,  95.25, 90,    0, -3.81,   0, 5.08),
    ('R15', 133.35,  88.90, 0,  -6.35, -2.54, -6.35, 1.27),   # 位号/值放竖线左侧
    ('R16', 133.35, 132.08, 0,  -6.35, -1.27, -6.35, 2.54),
    ('R8',  152.40,  93.98, 90,    0, -5.08,   0, 5.08),
    ('R11', 152.40, 101.60, 90,    0, -5.08,   0, 5.08),
    ('R12', 152.40, 109.22, 90,    0, -5.08,   0, 8.89),
    ('R13', 167.64, 114.30, 90,    0, -6.35,   0, 6.35),
    ('D3',  184.15, 128.27, 270, 6.35, -2.54,  6.35, 1.27),   # 放竖线右侧
    ('TH1', 207.01, 114.30, 0,     0, -7.62,   0, 7.62),
    ('C13', 144.78, 127.00, 0,     0, -6.35,   0, 6.35),
]

WIRES = [
    (30.48, 80.01, 30.48, 95.25),   # +3V3竖轨(左上拉组)
    (30.48, 83.82, 34.29, 83.82),   # 轨→R14.1
    (41.91, 83.82, 45.72, 83.82),   # R14.2→降线
    (45.72, 83.82, 45.72, 106.68),  # R14降线
    (30.48, 95.25, 34.29, 95.25),   # 轨→R7.1(轨端)
    (41.91, 95.25, 43.18, 95.25),   # R7.2→
    (43.18, 95.25, 43.18, 109.22),  # R7降线
    (45.72, 106.68, 110.49, 106.68),# TX_EN_N 行
    (43.18, 109.22, 110.49, 109.22),# BUS_TX 行
    (52.07, 111.76, 110.49, 111.76),# MCU_RX 行
    (110.49, 114.30, 102.87, 114.30), # U3.4 GND 短线
    (130.81, 106.68, 130.81, 101.60), # U3.8 ↑
    (130.81, 109.22, 133.35, 109.22), # U3.7 → 结点
    (133.35, 92.71, 133.35, 109.22),  # R15↓
    (133.35, 109.22, 133.35, 128.27), # ↓R16
    (130.81, 111.76, 137.16, 111.76), # U3.6 →
    (137.16, 111.76, 137.16, 114.30), # ↓ 并入总线
    (130.81, 114.30, 163.83, 114.30), # 总线 ←U3.5 →R13.1
    (171.45, 114.30, 207.01, 114.30), # 总线 R13.2 → TH1
    (207.01, 114.30, 207.01, 109.22), # ↑TH1.1
    (144.78, 88.90, 144.78, 109.22),  # +3V3竖轨(选项堆叠)
    (144.78, 93.98, 148.59, 93.98),   # 轨→R8.1
    (144.78, 101.60, 148.59, 101.60), # 轨→R11.1
    (144.78, 109.22, 148.59, 109.22), # 轨→R12.1
    (156.21, 93.98, 158.75, 93.98),   # R8.2→降线
    (156.21, 101.60, 158.75, 101.60), # R11.2→降线
    (156.21, 109.22, 158.75, 109.22), # R12.2→降线
    (158.75, 93.98, 158.75, 114.30),  # 降线→总线
    (184.15, 114.30, 184.15, 124.46), # D3↑
]

JUNCS = [
    (30.48, 83.82), (133.35, 109.22), (137.16, 114.30), (158.75, 114.30),
    (184.15, 114.30), (144.78, 93.98), (144.78, 101.60), (158.75, 101.60),
    (158.75, 109.22),
]

# (text, x, y, rot)
LABELS = [
    ('TX_EN_N',   52.07, 106.68, 0),
    ('BUS_TX',    52.07, 109.22, 0),
    ('MCU_RX',    52.07, 111.76, 0),
    ('GND',      102.87, 114.30, 180),
    ('+3V3',      30.48,  80.01, 90),
    ('+3V3',     130.81, 101.60, 90),
    ('+3V3',     133.35,  85.09, 90),
    ('RX_EN',    133.35, 109.22, 0),
    ('GND',      133.35, 135.89, 0),
    ('+3V3',     144.78,  88.90, 90),
    ('BUS_LOGIC', 130.81, 114.30, 0),
    ('BUS_CLAMP', 177.80, 114.30, 0),
    ('GND',      184.15, 132.08, 0),
    ('BUS_DATA', 207.01, 119.38, 0),
    ('+3V3',     144.78, 123.19, 0),
    ('GND',      144.78, 130.81, 0),
]

# ---- 3) 生成 ----
out = [head, libsec]

for ref, x, y, rot, rdx, rdy, vdx, vdy in SYMS:
    blk = tmpl[ref]
    blk = fresh(blk)
    blk = re.sub(r'\n\t\t\(at [\d.\- ]+\)\n\t\t\(unit', '\n\t\t(at %.2f %.2f %d)\n\t\t(unit' % (x, y, rot), blk, count=1)
    # Reference/Value 属性位置
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

# 分压配对说明(放在R16右侧空白区)
out.append('\n\t(text "R15+R16 = RX_EN boot strap: PB7 floats at boot, divider keeps 2OE low (RX buffer off)"\n\t\t(at 125.73 139.70 0)\n\t\t(effects (font (size 1 1)) (justify left bottom))\n\t\t(uuid "%s")\n\t)' % uuidlib.uuid4())

# 注释文字: 4块挪到底部左侧竖排 + 上拉选项说明
ty = 163.0
for tb in text_blocks:
    tb = re.sub(r'\(at [\d.\- ]+\)', '(at 25.40 %.1f 0)' % ty, tb, count=1)
    tb = fresh(tb)
    out.append(tb)
    ty += 6.5
out.append('\n\t(text "R8/R11/R12 = BUS_LOGIC pull-up options, fit ONE (2.2k default / 4.7k / 10k for trunk length)"\n\t\t(at 25.40 %.1f 0)\n\t\t(effects (font (size 1 1)) (justify left bottom))\n\t\t(uuid "%s")\n\t)' % (ty, uuidlib.uuid4()))

out.append(tail if tail.startswith('\n') else '\n' + tail)
new = ''.join(out)
open(SRC, 'w', encoding='utf-8').write(new)
print('重排完成: 符号%d 导线%d 结点%d 标签%d 文字%d' % (len(SYMS), len(WIRES), len(JUNCS), len(LABELS), len(text_blocks)))
print('括号平衡:', new.count('(') - new.count(')'))
