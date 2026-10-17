#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3.6 修复第四轮:切掉两处死区轮廓。

- VBAT_RAW_TOP:底边死走廊 (0.4,0.4)-(34.6,3.6) —— 9 条无焊盘孤岛铜带的来源
- GND_TOP/GND_BOTTOM:右上角 (38.9,27.9)-(41.2,30.2) —— 空角落的碎片
切前再查一遍区域内无焊盘/过孔/走线。
"""
import pcbnew

MM = 1000000
PCB = "imu_to_ttl.kicad_pcb"

b = pcbnew.LoadBoard(PCB)
assert b

CUTS = [
    ("VBAT_RAW_TOP", (0.4, 0.4, 34.6, 3.6)),
    ("GND_TOP", (38.9, 27.9, 41.2, 30.2)),
    ("GND_BOTTOM", (38.9, 27.9, 41.2, 30.2)),
]


def rect_poly(x0, y0, x1, y1):
    ps = pcbnew.SHAPE_POLY_SET()
    ps.NewOutline()
    for pt in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        ps.Append(int(pt[0] * MM), int(pt[1] * MM), 0, -1)
    return ps


def occupied(box):
    """box=(x0,y0,x1,y1) mm,返回 (pads, vias, tracks) 数"""
    pads = vias = trks = 0
    for f in b.GetFootprints():
        for p in f.Pads():
            q = p.GetPosition()
            if box[0] * MM <= q.x <= box[2] * MM and box[1] * MM <= q.y <= box[3] * MM:
                pads += 1
    for t in b.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            q = t.GetPosition()
            if box[0] * MM <= q.x <= box[2] * MM and box[1] * MM <= q.y <= box[3] * MM:
                vias += 1
        else:
            s, e = t.GetStart(), t.GetEnd()
            for q in (s, e, pcbnew.VECTOR2I((s.x + e.x) // 2, (s.y + e.y) // 2)):
                if box[0] * MM <= q.x <= box[2] * MM and box[1] * MM <= q.y <= box[3] * MM:
                    trks += 1
                    break
    return pads, vias, trks


for zname, box in CUTS:
    pads, vias, trks = occupied(box)
    if pads or vias or trks:
        print("!! %s 切区被占用 pads=%d vias=%d trks=%d → 跳过" % (zname, pads, vias, trks))
        continue
    for z in b.Zones():
        if z.GetZoneName() != zname:
            continue
        ol = z.Outline()
        before = ol.OutlineCount()
        ol.BooleanSubtract(rect_poly(*box))
        z.SetOutline(ol)
        z.SetNeedRefill(True)
        print("cut %s %s outlines %d → %d" % (zname, box, before, ol.OutlineCount()))

ok = pcbnew.ZONE_FILLER(b).Fill(b.Zones())
print("refill:", ok)
pcbnew.SaveBoard(PCB, b)
print("saved")
