#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3.6 修复第三轮:规则区对脚本填充无效 → 直接从 zone 轮廓上切掉角落方块。

1. 删掉上一轮加的两个规则区(无效,免得留垃圾)
2. 所有轮廓伸入 (0,0)-(2.2,2.2) 的 zone:BooleanSubtract 切掉该方块
3. 重灌铺铜,保存
"""
import pcbnew

MM = 1000000
PCB = "imu_to_ttl.kicad_pcb"
CUT = 2.2 * MM

b = pcbnew.LoadBoard(PCB)
assert b

# ---------- 1. 删规则区 ----------
removed = 0
for z in list(b.Zones()):
    if z.GetIsRuleArea():
        b.RemoveNative(z)
        removed += 1
print("1) 规则区删除:", removed)

# ---------- 2. 切角落 ----------
square = pcbnew.SHAPE_POLY_SET()
square.NewOutline()
for pt in ((0, 0), (CUT, 0), (CUT, CUT), (0, CUT)):
    square.Append(int(pt[0]), int(pt[1]), 0, -1)

cut_zones = 0
for z in b.Zones():
    if z.GetIsRuleArea():
        continue
    ol = z.Outline()
    near = False
    for i in range(ol.OutlineCount()):
        o = ol.COutline(i)
        for j in range(o.PointCount()):
            p = o.CPoint(j)
            if p.x <= CUT and p.y <= CUT:
                near = True
                break
        if near:
            break
    if not near:
        continue
    before = ol.OutlineCount()
    ol.BooleanSubtract(square)
    if hasattr(z, "SetOutline"):
        z.SetOutline(ol)
    z.SetNeedRefill(True)
    cut_zones += 1
    print("2) zone", z.GetZoneName(), b.GetLayerName(z.GetLayer()),
          "outlines", before, "→", ol.OutlineCount())
print("   共切", cut_zones, "个 zone")

# ---------- 3. 重灌 + 保存 ----------
ok = pcbnew.ZONE_FILLER(b).Fill(b.Zones())
print("3) refill:", ok)
pcbnew.SaveBoard(PCB, b)
print("saved")
