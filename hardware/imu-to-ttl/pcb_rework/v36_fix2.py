#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3.6 修复第二轮:
- J1/J2~J5 丝印: 从 v3.5 参考板恢复原始几何,然后:
    * 完全在板外的丝印 → 删(制造不出来,只存在于 EDA 视图)
    * 跨板边的丝印 → 裁到边内 0.3mm
- J1 极性标记: 删旧粗标记(压焊盘),新增纤细 +/− 标记于焊盘左侧空带
- zone 孤岛模式恢复 0(ALWAYS)
- 板角(0,0)-(1.5,1.5) 加"禁铺铜"规则区(F.Cu+B.Cu),清掉圆角处铜碎片
"""
import pcbnew

MM = 1000000
SEG, RECT = 0, 1
REF = "pcb_rework/v35_ref.kicad_pcb"
PCB = "imu_to_ttl.kicad_pcb"
INSET = 0.3 * MM

b = pcbnew.LoadBoard(PCB)
ref = pcbnew.LoadBoard(REF)
assert b and ref

L = {}
for i in range(64):
    n = b.GetLayerName(i)
    if n:
        L[n] = i
LY_SILK = L["F.Silkscreen"]
fps = {f.GetReference(): f for f in b.GetFootprints()}
rfps = {f.GetReference(): f for f in ref.GetFootprints()}

bb = b.GetBoardEdgesBoundingBox()
BX0, BY0 = bb.GetX(), bb.GetY()
BX1, BY1 = BX0 + bb.GetWidth(), BY0 + bb.GetHeight()
print("board bbox: [%.2f,%.2f]-[%.2f,%.2f]" % (BX0 / MM, BY0 / MM, BX1 / MM, BY1 / MM))


def v(x, y):
    return pcbnew.VECTOR2I(int(round(x)), int(round(y)))


def silks(fp):
    out = []
    for g in fp.GraphicalItems():
        s = pcbnew.Cast_to_PCB_SHAPE(g)
        if s is not None and s.GetLayer() == LY_SILK:
            out.append(s)
    return out


def bbox_of(s):
    bbx = s.GetBoundingBox()
    return (bbx.GetX(), bbx.GetY(), bbx.GetX() + bbx.GetWidth(),
            bbx.GetY() + bbx.GetHeight())


# ---------- A. J1/J2~J5 丝印恢复 + 正确处理 ----------
for refid in ("J1", "J2", "J3", "J4", "J5"):
    fp = fps[refid]
    # 删当前全部丝印 shape
    for s in list(silks(fp)):
        fp.RemoveNative(s)
    # 从参考板拷回原始丝印
    restored = 0
    for g in rfps[refid].GraphicalItems():
        s = pcbnew.Cast_to_PCB_SHAPE(g)
        if s is None or s.GetLayer() != LY_SILK or s.GetShape() not in (SEG, RECT):
            continue
        ns = pcbnew.PCB_SHAPE(fp)
        ns.SetShape(s.GetShape())
        ns.SetLayer(LY_SILK)
        ns.SetStart(s.GetStart())
        ns.SetEnd(s.GetEnd())
        ns.SetWidth(s.GetWidth())
        fp.Add(ns)
        restored += 1
    # 处理:板外删 / 跨界裁
    deleted = clipped = 0
    for s in list(silks(fp)):
        x0, y0, x1, y1 = bbox_of(s)
        if x1 <= BX0 or x0 >= BX1 or y1 <= BY0 or y0 >= BY1:
            fp.RemoveNative(s)          # 完全在板外
            deleted += 1
            continue
        if x0 >= BX0 and x1 <= BX1 and y0 >= BY0 and y1 <= BY1:
            continue                     # 完全在板内
        a, c = s.GetStart(), s.GetEnd()


        def cl(p):
            return v(max(BX0 + INSET, min(BX1 - INSET, p.x)),
                     max(BY0 + INSET, min(BY1 - INSET, p.y)))


        na, nc = cl(a), cl(c)
        if s.GetShape() == SEG:
            if na == nc:
                fp.RemoveNative(s)
                deleted += 1
            else:
                s.SetStart(na)
                s.SetEnd(nc)
                clipped += 1
        else:
            xa, xb = sorted((na.x, nc.x))
            ya, yb = sorted((na.y, nc.y))
            if xb - xa < 0.05 * MM or yb - ya < 0.05 * MM:
                fp.RemoveNative(s)
                deleted += 1
            else:
                s.SetStart(v(xa, ya))
                s.SetEnd(v(xb, yb))
                clipped += 1
    print("A)", refid, "恢复", restored, "| 板外删", deleted, "| 裁边", clipped)

# ---------- B. J1 极性标记重建 ----------
j1 = fps["J1"]
pads = {p.GetPadName(): p for p in j1.Pads()}
p1 = pads.get("1")
p2 = pads.get("2")
# 删掉恢复回来的旧粗标记(pad1 上的 fat+ 和 pad2 tick):
# 判据:丝印 bbox 与任一焊盘 bbox 相交
for s in list(silks(j1)):
    sx0, sy0, sx1, sy1 = bbox_of(s)
    for p in (p1, p2):
        bbp = p.GetBoundingBox()
        px0, py0 = bbp.GetX(), bbp.GetY()
        px1, py1 = px0 + bbp.GetWidth(), py0 + bbp.GetHeight()
        if sx0 < px1 and sx1 > px0 and sy0 < py1 and sy1 > py0:
            j1.RemoveNative(s)
            break
# 新标记:焊盘左侧空带 x∈[0.35,0.8],腿 0.3mm,线宽 0.12
marks = []


def add_leg(x0, y0, x1, y1):
    s = pcbnew.PCB_SHAPE(j1)
    s.SetShape(SEG)
    s.SetLayer(LY_SILK)
    s.SetStart(v(x0 * MM, y0 * MM))
    s.SetEnd(v(x1 * MM, y1 * MM))
    s.SetWidth(12000)
    j1.Add(s)
    marks.append((x0, y0, x1, y1))


if p1 is not None:
    cx, cy = p1.GetPosition().x / MM, p1.GetPosition().y / MM
    x0 = cx - 1.35                       # 0.55
    add_leg(x0 - 0.15, cy, x0 + 0.15, cy)   # + 横腿
    add_leg(x0, cy - 0.15, x0, cy + 0.15)   # + 竖腿
if p2 is not None:
    cx, cy = p2.GetPosition().x / MM, p2.GetPosition().y / MM
    x0 = cx - 1.35
    add_leg(x0 - 0.15, cy, x0 + 0.15, cy)   # − 单横腿
print("B) J1 新极性标记:", marks)

# ---------- C. zone 孤岛模式恢复 ALWAYS ----------
for z in b.Zones():
    z.SetIslandRemovalMode(0)
print("C) zone island → ALWAYS(0)")

# ---------- D. 板角禁铺铜规则区 ----------
made = 0
for lname in ("F.Cu", "B.Cu"):
    ra = pcbnew.ZONE(b)
    ra.SetIsRuleArea(True)
    ra.SetLayer(L[lname])
    ra.SetDoNotAllowZoneFills(True)
    # 其余 keepout 类型一律关
    for setter in ("SetDoNotAllowVias", "SetDoNotAllowTracks", "SetDoNotAllowPads",
                   "SetDoNotAllowFootprints", "SetDoNotAllowLabels"):
        if hasattr(ra, setter):
            getattr(ra, setter)(False)
    for pt in ((0, 0), (1.5, 0), (1.5, 1.5), (0, 1.5)):
        ra.AppendCorner(v(pt[0] * MM, pt[1] * MM), -1)
    ra.SetLocalClearance(0)
    b.Add(ra)
    made += 1
print("D) 板角禁铺铜规则区:", made, "个(F.Cu+B.Cu)")

# ---------- 重灌 + 保存 ----------
ok = pcbnew.ZONE_FILLER(b).Fill(b.Zones())
print("zone refill:", ok)
pcbnew.SaveBoard(PCB, b)
print("saved")
