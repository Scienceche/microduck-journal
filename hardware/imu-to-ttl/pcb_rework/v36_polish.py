#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3.6 打磨:清掉 DRC 全部非阻塞项(层表自定义,按层名解析;子图形均为绝对坐标)。"""
import os
import shutil
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = os.path.join(PROJ, "imu_to_ttl.kicad_pcb")
BAK = os.path.join(HERE, "01.pre_v36.bak")
MM = 1000000
SEG, RECT = 0, 1

if "--no-bak" not in sys.argv:
    shutil.copy(PCB, BAK)
b = pcbnew.LoadBoard(PCB)
print("loaded, footprints:", len(b.GetFootprints()), "tracks:", len(b.GetTracks()))

L = {}
for i in range(64):
    n = b.GetLayerName(i)
    if n:
        L[n] = i
LY_FAB, LY_CRT, LY_SILK = L["F.Fab"], L["F.Courtyard"], L["F.Silkscreen"]

fps = {f.GetReference(): f for f in b.GetFootprints()}


def v(x, y):
    return pcbnew.VECTOR2I(int(round(x)), int(round(y)))


def shapes(fp):
    for g in fp.GraphicalItems():
        s = pcbnew.Cast_to_PCB_SHAPE(g)
        if s is not None:
            yield s


def bbox_of(fp, layer):
    """并集 bounding box(nm),用 GetBoundingBox 兼容 POLY/RECT。"""
    x0 = y0 = None
    for g in shapes(fp):
        if g.GetLayer() != layer:
            continue
        bb = g.GetBoundingBox()
        bx0, by0 = bb.GetX(), bb.GetY()
        bx1, by1 = bx0 + bb.GetWidth(), by0 + bb.GetHeight()
        if x0 is None:
            x0, y0, x1, y1 = bx0, by0, bx1, by1
        else:
            x0, y0 = min(x0, bx0), min(y0, by0)
            x1, y1 = max(x1, bx1), max(y1, by1)
    return None if x0 is None else (x0, y0, x1, y1)


# ---------- 1. 删残根 ----------
removed = 0
for tr in list(b.GetTracks()):
    if not isinstance(tr, pcbnew.PCB_TRACK) or isinstance(tr, pcbnew.PCB_VIA):
        continue
    s, e = tr.GetStart(), tr.GetEnd()
    if (abs(s.x - 25.0 * MM) < 0.05 * MM and abs(s.y - 21.825 * MM) < 0.05 * MM
            and abs(e.x - 25.0 * MM) < 0.05 * MM and abs(e.y - 22.1 * MM) < 0.05 * MM
            and abs(tr.GetWidth() - 0.2 * MM) < 0.02 * MM):
        b.RemoveNative(tr)
        removed += 1
print("1) 残根删除:", removed)

# ---------- 2. courtyard 重建 fab+0.10 ----------
for ref in ("F1", "Q1", "U1"):
    fp = fps[ref]
    bb = bbox_of(fp, LY_FAB)
    if bb is None:
        print("  !", ref, "无 fab,跳过")
        continue
    m = int(0.10 * MM)
    for g in list(shapes(fp)):
        if g.GetLayer() == LY_CRT:
            fp.RemoveNative(g)
    sh = pcbnew.PCB_SHAPE(fp)
    sh.SetShape(RECT)
    sh.SetLayer(LY_CRT)
    sh.SetStart(v(bb[0] - m, bb[1] - m))
    sh.SetEnd(v(bb[2] + m, bb[3] + m))
    sh.SetWidth(10000)
    fp.Add(sh)
    print("2)", ref, "courtyard→fab+0.10: x[%.2f,%.2f] y[%.2f,%.2f]"
          % ((bb[0] - m) / MM, (bb[2] + m) / MM, (bb[1] - m) / MM, (bb[3] + m) / MM))

# ---------- 3. zone 孤岛清除 ----------
for z in b.Zones():
    z.SetIslandRemovalMode(1)              # AREA
    z.SetMinIslandArea(1000 * MM * MM)     # 1000mm²:所有孤岛一律裁掉
print("3) zone 孤岛策略 → AREA/1000mm²:", len(b.Zones()), "个 zone")

# ---------- 4. J2~J5 越边丝印裁剪 ----------
bb = b.GetBoardEdgesBoundingBox()
bx0, by0 = bb.GetX(), bb.GetY()
bx1, by1 = bx0 + bb.GetWidth(), by0 + bb.GetHeight()
m = int(0.05 * MM)


def clamp(pt):
    return v(max(bx0 + m, min(bx1 - m, pt.x)), max(by0 + m, min(by1 - m, pt.y)))


clip_cnt = del_cnt = 0
for ref in ("J2", "J3", "J4", "J5"):
    fp = fps[ref]
    for g in list(shapes(fp)):
        if g.GetLayer() != LY_SILK or g.GetShape() not in (SEG, RECT):
            continue
        s, e = g.GetStart(), g.GetEnd()
        ns, ne = clamp(s), clamp(e)
        if g.GetShape() == SEG:
            if ns == ne:
                fp.RemoveNative(g)
                del_cnt += 1
            elif ns != s or ne != e:
                g.SetStart(ns)
                g.SetEnd(ne)
                clip_cnt += 1
        else:  # RECT:规范化对角
            x0n, x1n = sorted((ns.x, ne.x))
            y0n, y1n = sorted((ns.y, ne.y))
            if x1n - x0n < 0.05 * MM or y1n - y0n < 0.05 * MM:
                fp.RemoveNative(g)
                del_cnt += 1
            elif ns != s or ne != e:
                g.SetStart(v(x0n, y0n))
                g.SetEnd(v(x1n, y1n))
                clip_cnt += 1
print("4) J2~J5 丝印: 裁剪", clip_cnt, "删除", del_cnt)

# ---------- 5. J1 极性标记避让焊盘(用线段中点判断) ----------
j1 = fps["J1"]
pads = [(p.GetPosition().x, p.GetPosition().y,
         max(p.GetSize().x, p.GetSize().y)) for p in j1.Pads()]
moved = 0
for g in list(shapes(j1)):
    if g.GetLayer() != LY_SILK or g.GetShape() not in (SEG, RECT):
        continue
    s, e = g.GetStart(), g.GetEnd()
    c = ((s.x + e.x) // 2, (s.y + e.y) // 2)
    for px, py, psize in pads:
        dx, dy = c[0] - px, c[1] - py
        r = (dx * dx + dy * dy) ** 0.5
        keep = psize / 2 + 0.35 * MM
        if r < keep:
            if r < 0.01 * MM:
                dx, dy, r = -1.0 * MM, 0.0, 1.0 * MM
            push = int(keep + 0.2 * MM - r)
            g.Move(v(int(dx / r * push), int(dy / r * push)))
            moved += 1
            break
print("5) J1 极性标记外移:", moved, "个")

# ---------- 重灌铺铜 + 保存 ----------
ok = pcbnew.ZONE_FILLER(b).Fill(b.Zones())
print("zone refill:", ok)
pcbnew.SaveBoard(PCB, b)
print("saved", PCB)
