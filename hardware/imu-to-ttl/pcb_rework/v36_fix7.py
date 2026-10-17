#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3.6 修复第七轮(真电气修复):U2/U3 浮地岛接回主地网。

问题:U2.6/7(IMU GND)与 U3.4(缓冲 GND)只靠与主地隔断的铺铜岛供电——
制成板上这两颗芯片没有地!

修法:
  U3: poly3 岛内空位 (31.3,20.15) 打 GND 过孔 → 直连 B.Cu 地
  U2: V3V3_IMU 底部环绕(3 段)改道芯片体内下方 → 腾出焊盘南侧通道;
      U2.7 焊盘向下引出走线 + GND 过孔 (21.0,16.35) → B.Cu 地
最后重灌+收敛+保存。
"""
import pcbnew

MM = 1000000
PCB = "imu_to_ttl.kicad_pcb"

b = pcbnew.LoadBoard(PCB)
assert b


def v(x, y):
    return pcbnew.VECTOR2I(int(round(x * MM)), int(round(y * MM)))


def seg_at(net, x0, y0, x1, y1, layer=None):
    for t in b.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA) or t.GetNetname() != net:
            continue
        s, e = t.GetStart(), t.GetEnd()
        if (abs(s.x - x0 * MM) < 2000 and abs(s.y - y0 * MM) < 2000
                and abs(e.x - x1 * MM) < 2000 and abs(e.y - y1 * MM) < 2000):
            return t
        if (abs(s.x - x1 * MM) < 2000 and abs(s.y - y1 * MM) < 2000
                and abs(e.x - x0 * MM) < 2000 and abs(e.y - y0 * MM) < 2000):
            return t
    return None


def add_track(net, x0, y0, x1, y1, w, layer):
    tr = pcbnew.PCB_TRACK(b)
    tr.SetNet(b.FindNet(net))
    tr.SetLayer(layer)
    tr.SetWidth(int(w * MM))
    tr.SetStart(v(x0, y0))
    tr.SetEnd(v(x1, y1))
    b.Add(tr)
    return tr


L = {}
for i in range(64):
    n = b.GetLayerName(i)
    if n:
        L[n] = i

# ---------- 1. U3 岛接地孔 ----------
via = pcbnew.PCB_VIA(b)
via.SetPosition(v(31.3, 20.15))
via.SetNet(b.FindNet("GND"))
via.SetViaType(pcbnew.VIATYPE_THROUGH)
via.SetWidth(int(0.55 * MM))
via.SetDrill(int(0.25 * MM))
b.Add(via)
print("1) U3 岛 GND 过孔 @(31.3,20.15)")

# ---------- 2. U2:V3V3 环绕改道体内 ----------
DELS = [
    (21.66, 16.03, 21.22, 16.47),
    (21.22, 16.47, 20.16, 16.47),
    (20.16, 16.47, 20.00, 16.31),
]
ndel = 0
for x0, y0, x1, y1 in DELS:
    t = seg_at("V3V3_IMU", x0, y0, x1, y1)
    if t is None:
        print("  !! 未找到待删段", (x0, y0, x1, y1))
        continue
    b.RemoveNative(t)
    ndel += 1
print("2) V3V3 环绕删除 %d/3 段" % ndel)
# 体内新径:U2.8 pad(21.66,15.75) → 北上入体 → 西行 → 南下汇 U2.5 pad(20.0,15.91)
for seg in (((21.66, 15.75), (21.66, 15.45)),
            ((21.66, 15.45), (20.00, 15.45)),
            ((20.00, 15.45), (20.00, 15.635))):
    add_track("V3V3_IMU", seg[0][0], seg[0][1], seg[1][0], seg[1][1], 0.2, L["F.Cu"])
print("   V3V3 体内新径 3 段")

# ---------- 3. U2 地焊盘逃生 ----------
add_track("GND", 21.0, 16.10, 21.0, 16.35, 0.15, L["F.Cu"])
via2 = pcbnew.PCB_VIA(b)
via2.SetPosition(v(21.0, 16.35))
via2.SetNet(b.FindNet("GND"))
via2.SetViaType(pcbnew.VIATYPE_THROUGH)
via2.SetWidth(int(0.55 * MM))
via2.SetDrill(int(0.25 * MM))
b.Add(via2)
print("3) U2 地: 逃生走线 + GND 过孔 @(21.0,16.35)")

# ---------- 4. 重灌+保存 ----------
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
pcbnew.SaveBoard(PCB, b)
print("4) refill+saved")
