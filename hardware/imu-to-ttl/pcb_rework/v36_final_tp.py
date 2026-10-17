#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3.6 收尾:加 U2 飞线锚点 TG1(0.8mm 圆 SMD 焊盘 @ (20.3,17.73), net GND)。"""
import pcbnew

MM = 1000000
PCB = "imu_to_ttl.kicad_pcb"

b = pcbnew.LoadBoard(PCB)
assert b

if any(f.GetReference() == "TG1" for f in b.GetFootprints()):
    print("TG1 已存在,跳过")
else:
    fp = pcbnew.FOOTPRINT(b)
    fp.SetReference("TG1")
    fp.SetPosition(pcbnew.VECTOR2I(int(20.3 * MM), int(17.73 * MM)))
    fp.SetLayer(pcbnew.F_Cu)
    fp.SetAttributes(pcbnew.FP_SMD | pcbnew.FP_EXCLUDE_FROM_BOM
                     | pcbnew.FP_EXCLUDE_FROM_POS_FILES)

    pad = pcbnew.PAD(fp)
    pad.SetNumber("1")
    pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
    pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
    pad.SetSize(pcbnew.VECTOR2I(int(0.8 * MM), int(0.8 * MM)))
    lset = pcbnew.LSET()
    lset.AddLayer(pcbnew.F_Cu)
    lset.AddLayer(pcbnew.F_Mask)
    lset.AddLayer(pcbnew.F_Paste)
    pad.SetLayerSet(lset)
    pad.SetNet(b.FindNet("GND"))
    pad.SetPosition(pcbnew.VECTOR2I(0, 0))
    fp.Add(pad)

    b.Add(fp)
    print("TG1 已加 @ (20.3,17.73) net=GND")

ok = pcbnew.ZONE_FILLER(b).Fill(b.Zones())
print("refill:", ok)
pcbnew.SaveBoard(PCB, b)
print("saved")
