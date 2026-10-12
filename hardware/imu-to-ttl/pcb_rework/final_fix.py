#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3收尾修复: LED1/F1微移 + 边缘过孔内移 + 4根顽固线手工补线 + 重灌铺铜"""
import io, sys, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import pcbnew

b = pcbnew.LoadBoard(os.path.abspath('imu_to_ttl.kicad_pcb'))
fps = {f.GetReference(): f for f in b.GetFootprints()}
assert len(fps) > 40, '板子又是空的! 中止'

def mm(v):
    return pcbnew.FromMM(v)

def pos(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))

# 1) LED1 → (24,25.5), F1 → (28.3,11.5) 并清掉触及它们旧焊盘的走线
def fp_pads_from_file(ref):
    import re as _re
    s = open('imu_to_ttl.kicad_pcb', encoding='utf-8').read()
    i = s.find('(property "Reference" "%s"' % ref)
    st = s.rfind('\n\t(footprint ', 0, i)
    d = 0; j = st + 1
    while True:
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: break
        j += 1
    blk = s[st:j]
    a = _re.search(r'\n\t+\(at ([\d.\-]+) ([\d.\-]+)(?: ([\d.\-]+))?\)', blk)
    fx, fy, fr = float(a.group(1)), float(a.group(2)), float(a.group(3) or 0)
    out = []
    for pm in _re.finditer(r'\(pad "[^"]+"[^\n]*\n\t+\(at ([\d.\-]+) ([\d.\-]+)', blk):
        px, py = float(pm.group(1)), float(pm.group(2))
        r = int(fr) % 360
        px, py = {0: (px, py), 90: (-py, px), 180: (-px, -py), 270: (py, -px)}[r]
        out.append((fx + px, fy + py))
    return out

MOVES = [('LED1', 24.0, 25.5), ('F1', 28.3, 11.5)]
oldpads = {ref: fp_pads_from_file(ref) for ref, _, _ in MOVES}
tracks = list(b.GetTracks())          # 一次性快照(SWIG集合在Remove后会坏)
kill = []
for t in tracks:
    for st in (t.GetStart(), t.GetEnd()):
        for ref, _, _ in MOVES:
            if any(abs(st.x/1e6 - px) < 0.4 and abs(st.y/1e6 - py) < 0.4 for px, py in oldpads[ref]):
                kill.append(t)
                break
        if t in kill:
            break
kill2 = []
for t in kill:
    if not any(t is k for k in kill2):
        kill2.append(t)
for t in kill2:
    b.Remove(t)
for ref, x, y in MOVES:
    fps[ref].SetPosition(pos(x, y))
    print('%s → (%.1f,%.1f) ✓ 删旧走线%d' % (ref, x, y, len(kill2)))

# 2) 边缘过孔内移 (BUS_DATA via @27.53,0.46)
for t in b.GetTracks():
    if t.GetClass() == "PCB_VIA":
        p = t.GetPosition()
        if abs(p.x/1e6 - 27.53) < 0.2 and p.y/1e6 < 0.9:
            t.SetPosition(pos(p.x/1e6, 0.85))
            print('边缘via → y0.85')

# 3) 手工补线 (端点必须在焊盘/过孔上)
def seg(x1, y1, x2, y2, layer=pcbnew.B_Cu, w=0.2, net=None):
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(pos(x1, y1))
    t.SetEnd(pos(x2, y2))
    t.SetWidth(mm(w))
    t.SetLayer(layer)
    if net:
        t.SetNetCode(net)
    b.Add(t)
    return t

def net(name):
    ni = b.GetNetInfo().GetNetItem(name) if hasattr(b.GetNetInfo(), 'GetNetItem') else None
    if ni is None:
        for code, n in b.GetNetInfo().NetsByNetcode().items():
            if n.GetNetname() == name:
                return n.GetNetCode()
        return None
    return ni.GetNetCode()

def via(x, y, netname):
    v = pcbnew.PCB_VIA(b)
    v.SetPosition(pos(x, y))
    v.SetWidth(mm(0.6), mm(0.6))
    v.SetDrill(mm(0.3))
    v.SetLayer(pcbnew.B_Cu)
    v.SetNetCode(net(netname))
    b.Add(v)
    return v

LED_A = net('LED_A'); SWCLK = net('SWCLK'); UART1_TX = net('UART1_TX'); IMU_INT2 = net('IMU_INT2')
assert all(n for n in (LED_A, SWCLK, UART1_TX, IMU_INT2)), '网络没找到'

# LED_A: LED1.1(23.2125,25.5,B) → R1.2(27.325,26,B)
for pts in (((23.2125,25.5),(23.2125,27.3)), ((23.2125,27.3),(27.325,27.3)), ((27.325,27.3),(27.325,26.0))):
    seg(pts[0][0], pts[0][1], pts[1][0], pts[1][1], pcbnew.B_Cu, 0.2, LED_A)
print('LED_A ✓')

# SWCLK: J2.2(9.5,19.05,THT) → 底走 y20.1 → via(30.8,20.1) → 顶 → U1.19(31.4625,13.725)
seg(9.5, 19.05, 10.3, 19.05, pcbnew.B_Cu, 0.2, SWCLK)
seg(10.3, 19.05, 10.3, 20.1, pcbnew.B_Cu, 0.2, SWCLK)
seg(10.3, 20.1, 30.8, 20.1, pcbnew.B_Cu, 0.2, SWCLK)
via(30.8, 20.1, 'SWCLK')
seg(30.8, 20.1, 30.8, 13.725, pcbnew.F_Cu, 0.2, SWCLK)
seg(30.8, 13.725, 31.4625, 13.725, pcbnew.F_Cu, 0.2, SWCLK)
print('SWCLK ✓')

# UART1_TX: J2.4(9.5,13.97,THT) → 底走 y14.5 → via(30.7,14.5) → 顶 → U1.16(31.4625,15.675)
seg(9.5, 13.97, 10.5, 13.97, pcbnew.B_Cu, 0.2, UART1_TX)
seg(10.5, 13.97, 10.5, 14.5, pcbnew.B_Cu, 0.2, UART1_TX)
seg(10.5, 14.5, 30.7, 14.5, pcbnew.B_Cu, 0.2, UART1_TX)
via(30.7, 14.5, 'UART1_TX')
seg(30.7, 14.5, 30.7, 15.675, pcbnew.F_Cu, 0.2, UART1_TX)
seg(30.7, 15.675, 31.4625, 15.675, pcbnew.F_Cu, 0.2, UART1_TX)
print('UART1_TX ✓')

# IMU_INT2: U2.9(21.6625,15.25,顶) → U1.15(31.4625,16.325,顶)
seg(21.6625, 15.25, 24.8, 15.25, pcbnew.F_Cu, 0.2, IMU_INT2)
seg(24.8, 15.25, 24.8, 16.325, pcbnew.F_Cu, 0.2, IMU_INT2)
seg(24.8, 16.325, 31.4625, 16.325, pcbnew.F_Cu, 0.2, IMU_INT2)
print('IMU_INT2 ✓')

# 4) 重灌铺铜
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(os.path.abspath('imu_to_ttl.kicad_pcb'))
print('完成, 封装%d' % len(b.GetFootprints()))
