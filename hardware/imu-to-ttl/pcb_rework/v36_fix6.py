#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3.6 修复第六轮:
- VBAT_TOP: 减掉只包着 J3.2(VBAT,已有走线直供)的冗余铺铜岛 p0
- 加 GND 缝合过孔 @(38.5,10.0) 连接 GND_TOP 右岛与 GND_BOTTOM
然后跑 fix5 的收敛逻辑,重灌保存。
"""
import pcbnew

MM = 1000000
PCB = "imu_to_ttl.kicad_pcb"

b = pcbnew.LoadBoard(PCB)
assert b

# ---------- 1. VBAT_TOP 冗余岛切除 ----------
for z in b.Zones():
    if z.GetZoneName() != "VBAT_TOP":
        continue
    fps = z.GetFilledPolysList(z.GetLayer())
    dead = pcbnew.SHAPE_POLY_SET()
    n_dead = 0
    for i in range(fps.OutlineCount()):
        ol = fps.COutline(i)
        pts = [(ol.CPoint(j).x, ol.CPoint(j).y) for j in range(ol.PointCount())]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        # 只切 p0 那块:含 J3.2 (16.66,25.5) 的多边形
        if min(xs) <= 16.66 * MM <= max(xs) and min(ys) <= 25.5 * MM <= max(ys):
            single = pcbnew.SHAPE_POLY_SET()
            single.NewOutline()
            for x, y in pts:
                single.Append(x, y, 0, -1)
            dead.BooleanAdd(single)
            n_dead += 1
    if n_dead:
        ol2 = z.Outline()
        ol2.BooleanSubtract(dead)
        z.SetOutline(ol2)
        z.SetNeedRefill(True)
    print("1) VBAT_TOP 冗余岛切除:", n_dead)

# ---------- 2. GND 缝合孔 ----------
via = pcbnew.PCB_VIA()
via.SetPosition(pcbnew.VECTOR2I(int(38.5 * MM), int(10.0 * MM)))
via.SetNet(b.GetNetcodeFromNetname("GND") and b.FindNet("GND"))
via.SetViaType(pcbnew.VIATYPE_THROUGH)
via.SetWidth(int(0.6 * MM))
via.SetDrill(int(0.3 * MM))
b.Add(via)
print("2) GND 缝合孔 @(38.5,10.0) 0.6/0.3")

pcbnew.ZONE_FILLER(b).Fill(b.Zones())
pcbnew.SaveBoard(PCB, b)
print("refill+saved")
