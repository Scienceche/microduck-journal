#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PCB v2手术: U3换TSSOP-8(2G241) + R14/R15/R16(拉带) + U1焊盘网络 + 清旧走线"""
import sys, io, re, uuid as uuidlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
def u(): return str(uuidlib.uuid4())

PCB = 'imu_to_ttl.kicad_pcb'
MOD = r'C:/Users/c1349/AppData/Local/Programs/KiCad/10.0.7/share/kicad/footprints/Package_SO.pretty/TSSOP-8_3x3mm_P0.65mm.kicad_mod'
s = open(PCB, encoding='utf-8').read()
mod = open(MOD, encoding='utf-8').read()

def blk_span(s, st):
    d = 0; j = st
    while j < len(s):
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: return j + 1
        j += 1
    return -1

# ---------- 1) 新U3: TSSOP-8 ----------
m = mod
# 实例化: 加(at), 改Reference/Value
m = m.replace('(footprint "TSSOP-8_3x3mm_P0.65mm"',
              '(footprint "Package_SO:TSSOP-8_3x3mm_P0.65mm"', 1)
m = m.replace('(layer "F.Cu")', '(layer "F.Cu")\n\t(at 32.095 7.245)', 1)
m = m.replace('(property "Reference" "REF**"', '(property "Reference" "U3"', 1)
m = m.replace('(property "Value" "TSSOP-8_3x3mm_P0.65mm"', '(property "Value" "SN74LVC2G241DCUR"', 1)
# 换全部uuid
m = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', u(), m)
# pad net映射: TSSOP-8 pads 1..8 (左列1-4右列5-8)
padnet = {'1': 'TX_EN_N', '2': 'BUS_TX', '3': 'MCU_RX', '4': 'GND',
          '5': 'BUS_LOGIC', '6': 'BUS_LOGIC', '7': 'RX_EN', '8': '+3V3'}
def add_net(pm):
    pad = pm.group(1)
    body = pm.group(0)
    net = padnet.get(pad)
    if not net:
        return body
    return body.replace('(uuid ', '(net "%s")\n\t\t\t(uuid ' % net, 1)
m = re.sub(r'\(pad "(\d+)"[^\n]*\n(?:[^\n]*\n)*?\t\)', add_net, m)
new_u3 = m
print('新U3块生成: %d字节, 含net行 %d 个' % (len(m), m.count('(net "')))

# ---------- 2) R14/R15/R16: 克隆R7 ----------
i7 = s.find('(property "Reference" "R7"')
st7 = s.rfind('\n\t(footprint ', 0, i7)
e7 = blk_span(s, st7 + 1)
r7 = s[st7:e7]
# 确认R7的at行
at7 = re.search(r'\(at ([\d.\-]+) ([\d.\-]+)\)', r7[:r7.find('(property')])
def mkres(ref, x, y, n1, n2, desc):
    t = r7
    t = t.replace('(at %s %s)' % (at7.group(1), at7.group(2)), '(at %s %s 90)' % (x, y), 1)
    t = t.replace('(property "Reference" "R7"', '(property "Reference" "%s"' % ref, 1)
    t = t.replace('(property "Value" "10k"', '(property "Value" "10k"', 1)
    t = t.replace('(net "+3V3")', '(net "%s")' % n1, 1)      # pad1
    t = t.replace('(net "BUS_TX")', '(net "%s")' % n2, 1)    # pad2
    t = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', u(), t)
    if '(path ' in t:
        t = re.sub(r'\n\t\t\(path "[^"]*"\)', '', t)
    return t

res = (mkres('R14', '34.3', '9.2',  '+3V3', 'TX_EN_N', 'TX_EN_N pullup')
     + mkres('R15', '34.3', '10.4', '+3V3', 'RX_EN',  'RX_EN pullup')
     + mkres('R16', '34.3', '11.6', 'GND',  'RX_EN',  'BOOT STRAP pull-down'))
print('三颗电阻块生成 ✓')

# ---------- 3) 替换旧U3块 ----------
iu3 = s.find('(property "Reference" "U3"')
stu3 = s.rfind('\n\t(footprint ', 0, iu3)
eu3 = blk_span(s, stu3 + 1)
old_u3 = s[stu3:eu3]
old_pads = [(30.957, 6.295), (30.957, 7.245), (30.957, 8.195), (33.233, 8.195), (33.233, 6.295)]
s = s[:stu3] + '\n\t' + new_u3 + res + s[eu3:]
print('U3已替换(旧块%d字节 → 新%d+电阻%d)' % (len(old_u3), len(new_u3), len(res)))

# ---------- 4) U1焊盘网络 ----------
iu1 = s.find('(property "Reference" "U1"')
stu1 = s.rfind('\n\t(footprint ', 0, iu1)
eu1 = blk_span(s, stu1 + 1)
u1blk = s[stu1:eu1]
u1new = u1blk
for pn, net in (('1', 'RX_EN'), ('8', 'TX_EN_N'), ('10', 'MCU_RX')):
    pm = re.search(r'\(pad "%s" [^\n]*\n(?:[^\n]*\n)*?\t\t\)' % pn, u1new)
    assert pm, 'U1 pad%s缺失' % pn
    padblk = pm.group(0)
    if '(net "' in padblk:
        npb = re.sub(r'\(net "[^"]*"\)', '(net "%s")' % net, padblk, count=1)
    else:
        npb = padblk.replace('(uuid ', '(net "%s")\n\t\t\t(uuid ' % net, 1)
    u1new = u1new.replace(padblk, npb, 1)
    print('U1.pad%s → %s ✓' % (pn, net))
s = s[:stu1] + u1new + s[eu1:]

# ---------- 5) 删触旧U3焊盘的走线 ----------
def near(p, q, r=0.32):
    return abs(p[0]-q[0]) <= r and abs(p[1]-q[1]) <= r
removed = 0
out = []
pos = 0
for sm in re.finditer(r'\(segment\n\t\t\(start ([\d.\-]+) ([\d.\-]+)\)\n\t\t\(end ([\d.\-]+) ([\d.\-]+)\)\n(?:[^\n]*\n)*?\t\)', s):
    a = (float(sm.group(1)), float(sm.group(2)))
    b = (float(sm.group(3)), float(sm.group(4)))
    if any(near(a, p) or near(b, p) for p in old_pads):
        out.append((sm.start(), sm.end()))
        removed += 1
for st, e in reversed(out):
    st = s.rfind('\n', 0, st)  # 含前导换行
    s = s[:st] + s[e:]
print('删除旧U3相关走线: %d条' % removed)

open(PCB, 'w', encoding='utf-8').write(s)
bal = sum(1 if c == '(' else -1 for c in s if c in '()')
print('括号平衡:', bal)
print('PCB手术完成 ✓')
