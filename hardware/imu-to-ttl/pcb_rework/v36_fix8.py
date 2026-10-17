#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3.6 修复第八轮:回滚 fix7 的事故并正确重做 U2/U3 接地。

fix7 事故:①旧 V3V3 环没删成(容差 0.002mm 太严) → 新旧叠加短路 6 处
          ②过孔钻 0.25 < 规则最小 0.3
本脚本:删干净 fix7 加的所有东西 → 正确删旧环(容差 0.05) →
       0.6/0.3 过孔重做。"""
import pcbnew

MM = 1000000
PCB = "imu_to_ttl.kicad_pcb"
b = pcbnew.LoadBoard(PCB)
assert b


def v(x, y):
    return pcbnew.VECTOR2I(int(round(x * MM)), int(round(y * MM)))


def near(a, b, tol=0.05):
    return abs(a - b) < tol * MM


def find_via(net, x, y):
    for t in b.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA) and t.GetNetname() == net:
            q = t.GetPosition()
            if near(q.x, x * MM) and near(q.y, y * MM):
                return t
    return None


def find_seg(net, x0, y0, x1, y1):
    for t in b.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA) or t.GetNetname() != net:
            continue
        s, e = t.GetStart(), t.GetEnd()
        if ((near(s.x, x0 * MM) and near(s.y, y0 * MM) and near(e.x, x1 * MM) and near(e.y, y1 * MM))
                or (near(s.x, x1 * MM) and near(s.y, y1 * MM) and near(e.x, x0 * MM) and near(e.y, y0 * MM))):
            return t
    return None


L = {}
for i in range(64):
    n = b.GetLayerName(i)
    if n:
        L[n] = i

# ---------- 1. 回滚 fix7 全部新增 ----------
removed = 0
for spec in ((("GND"), 31.3, 20.15), (("GND"), 21.0, 16.35)):
    t = find_via(spec[0], spec[1], spec[2])
    if t:
        b.RemoveNative(t)
        removed += 1
for seg in (((21.0, 16.10), (21.0, 16.35)),
            ((21.66, 15.75), (21.66, 15.45)),
            ((21.66, 15.45), (20.00, 15.45)),
            ((20.00, 15.45), (20.00, 15.635))):
    t = find_seg("GND" if seg[0][0] == 21.0 else "V3V3_IMU",
                 seg[0][0], seg[0][1], seg[1][0], seg[1][1])
    if t:
        b.RemoveNative(t)
        removed += 1
print("1) 回滚删除 fix7 物件:", removed, "/7")

# ---------- 2. 正确删除旧 V3V3 环(容差 0.05) ----------
ndel = 0
for x0, y0, x1, y1 in ((21.66, 16.03, 21.22, 16.47),
                       (21.22, 16.47, 20.16, 16.47),
                       (20.16, 16.47, 20.00, 16.31)):
    t = find_seg("V3V3_IMU", x0, y0, x1, y1)
    if t is None:
        print("  !! 仍未找到", (x0, y0, x1, y1))
        continue
    b.RemoveNative(t)
    ndel += 1
print("2) 旧 V3V3 环删除", ndel, "/3")


def add_track(net, x0, y0, x1, y1, w):
    tr = pcbnew.PCB_TRACK(b)
    tr.SetNet(b.FindNet(net))
    tr.SetLayer(L["F.Cu"])
    tr.SetWidth(int(w * MM))
    tr.SetStart(v(x0, y0))
    tr.SetEnd(v(x1, y1))
    b.Add(tr)


def add_via(net, x, y):
    via = pcbnew.PCB_VIA(b)
    via.SetPosition(v(x, y))
    via.SetNet(b.FindNet(net))
    via.SetViaType(pcbnew.VIATYPE_THROUGH)
    via.SetWidth(int(0.6 * MM))
    via.SetDrill(int(0.3 * MM))
    b.Add(via)


# ---------- 3. 重做(0.6/0.3 过孔) ----------
add_via("GND", 31.3, 20.15)                       # U3 岛
for seg in (((21.66, 15.75), (21.66, 15.45)),     # V3V3 体内新径
            ((21.66, 15.45), (20.00, 15.45)),
            ((20.00, 15.45), (20.00, 15.635))):
    add_track("V3V3_IMU", seg[0][0], seg[0][1], seg[1][0], seg[1][1], 0.2)
add_track("GND", 21.0, 16.10, 21.0, 16.30, 0.15)  # U2 逃生
add_via("GND", 21.0, 16.30)                       # 0.6Ø:下缘16.60,距VBAT_RAW缘16.78=0.18✓
print("3) 重做完成(U3 孔 + V3V3 改道 + U2 逃生+孔)")

pcbnew.ZONE_FILLER(b).Fill(b.Zones())
pcbnew.SaveBoard(PCB, b)
print("4) refill+saved")
