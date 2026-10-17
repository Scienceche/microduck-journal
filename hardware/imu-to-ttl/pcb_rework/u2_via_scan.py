#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""U2 接地过孔位置穷举扫描。

在 U2 周边网格上逐点判定 0.6/0.3 过孔是否与两个层的所有铜件保持 0.15mm 间距,
输出全部合法位置。两种模式:
  strict: 现状(环在)
  no-ring: 假设删除 V3V3_IMU 南环三段+pad8 东侧残段
"""
import math
import pcbnew

MM = 1000000
CLR = 0.15
VIA_R = 0.3
PCB = "imu_to_ttl.kicad_pcb"

b = pcbnew.LoadBoard(PCB)
L = {}
for i in range(64):
    n = b.GetLayerName(i)
    if n:
        L[n] = i

RING = [(21.66, 16.03, 21.22, 16.47), (21.22, 16.47, 20.16, 16.47),
        (20.16, 16.47, 20.00, 16.31), (21.66, 15.75, 21.66, 16.03)]

tracks = []   # (net, x0,y0,x1,y1, halfw, layer)
for t in b.GetTracks():
    if isinstance(t, pcbnew.PCB_VIA):
        q = t.GetPosition()
        tracks.append(("__via__" + t.GetNetname(), q.x / MM, q.y / MM,
                       q.x / MM, q.y / MM, 0.3, "BOTH"))
        continue
    s, e = t.GetStart(), t.GetEnd()
    tracks.append((t.GetNetname(), s.x / MM, s.y / MM, e.x / MM, e.y / MM,
                   t.GetWidth() / 2 / MM, b.GetLayerName(t.GetLayer())))

pads = []
for f in b.GetFootprints():
    for p in f.Pads():
        q = p.GetPosition()
        pads.append((f.GetReference() + "." + p.GetPadName(), p.GetNetname(),
                     q.x / MM, q.y / MM,
                     p.GetSize().x / 2 / MM, p.GetSize().y / 2 / MM,
                     p.GetOrientation() / 10))


def seg_dist(px, py, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    l2 = dx * dx + dy * dy
    if l2 == 0:
        return math.hypot(px - x0, py - y0)
    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / l2))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def pad_dist(px, py, cx, cy, hx, hy, rot):
    if abs(rot % 180 - 90) < 1e-6:
        hx, hy = hy, hx
    dx = max(abs(px - cx) - hx, 0.0)
    dy = max(abs(py - cy) - hy, 0.0)
    return math.hypot(dx, dy)


def ok(cx, cy, no_ring):
    need = VIA_R + CLR
    for net, x0, y0, x1, y1, hw, lyr in tracks:
        if net.endswith("__GND") or net == "GND":
            continue
        if no_ring and any(near_seg(x0, y0, x1, y1, *r) for r in RING):
            continue
        if lyr == "BOTH" or lyr == "F.Cu" or lyr == "B.Cu":
            if seg_dist(cx, cy, x0, y0, x1, y1) < need + hw:
                return False, net
    for name, net, px, py, hx, hy, rot in pads:
        if net == "GND":
            continue
        if pad_dist(cx, cy, px, py, hx, hy, rot) < need:
            return False, name
    return True, ""


def near_seg(x0, y0, x1, y1, rx0, ry0, rx1, ry1, tol=0.01):
    return (abs(x0 - rx0) < tol and abs(y0 - ry0) < tol
            and abs(x1 - rx1) < tol and abs(y1 - ry1) < tol)


for mode, no_ring in (("strict", False), ("no-ring", True)):
    good = []
    x = 18.6
    while x <= 22.6:
        y = 13.6
        while y <= 17.9:
            o, why = ok(x, y, no_ring)
            if o:
                good.append((round(x, 2), round(y, 2)))
            y += 0.05
        x += 0.05
    # 聚类:相邻 0.05 视为同一区域
    print(mode, "合法过孔位:", len(good), good[:14])
