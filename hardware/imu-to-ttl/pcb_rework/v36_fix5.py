#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3.6 修复第五轮(收敛):把铺铜死岛从 zone 轮廓里减掉。

死岛定义:该 zone 填充里的一个多边形,内部不含本网络的任何
焊盘/过孔/走线采样点 → 纯死铜。
迭代:减掉 → 全部重灌 → 再检查,直到没有新死岛(上限 6 轮)。
"""
import pcbnew

MM = 1000000
PCB = "imu_to_ttl.kicad_pcb"
MAX_ROUNDS = 6

b = pcbnew.LoadBoard(PCB)
assert b

# 网络条目索引: netname -> {'pads':[(x,y)], 'vias':[(x,y)], 'trk':[(x,y)...采样]}
items = {}


def sample_seg(s, e, n=9):
    return [((s.x * (n - 1 - i) + e.x * i) / (n - 1),
             (s.y * (n - 1 - i) + e.y * i) / (n - 1)) for i in range(n)]


def build_index():
    items.clear()
    for f in b.GetFootprints():
        for p in f.Pads():
            q = p.GetPosition()
            items.setdefault(p.GetNetname(), {"pads": [], "vias": [], "trk": []})
            items[p.GetNetname()]["pads"].append((q.x, q.y))
    for t in b.GetTracks():
        net = t.GetNetname()
        d = items.setdefault(net, {"pads": [], "vias": [], "trk": []})
        if isinstance(t, pcbnew.PCB_VIA):
            q = t.GetPosition()
            d["vias"].append((q.x, q.y))
        else:
            d["trk"] += sample_seg(t.GetStart(), t.GetEnd())


def poly_has_item(poly_sps, idx):
    """poly_sps: 只含一个轮廓的 SHAPE_POLY_SET"""
    has = False

    def pt_in(x, y):
        return poly_sps.Contains(pcbnew.VECTOR2I(int(x), int(y)))

    for x, y in idx["pads"]:
        if pt_in(x, y):
            return True
    for x, y in idx["vias"]:
        if pt_in(x, y):
            return True
    step = max(1, len(idx["trk"]) // 4000)
    for x, y in idx["trk"][::step]:
        if pt_in(x, y):
            return True
    return has


total_cut = 0
for rnd in range(MAX_ROUNDS):
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    build_index()
    cut_this = 0
    for z in b.Zones():
        if z.GetIsRuleArea():
            continue
        net = z.GetNetname()
        if not net or net not in items:
            continue
        fps = z.GetFilledPolysList(z.GetLayer())
        dead = pcbnew.SHAPE_POLY_SET()
        for i in range(fps.OutlineCount()):
            single = pcbnew.SHAPE_POLY_SET()
            single.NewOutline()
            ol = fps.COutline(i)
            for j in range(ol.PointCount()):
                p = ol.CPoint(j)
                single.Append(p.x, p.y, 0, -1)
            if not poly_has_item(single, items[net]):
                dead.BooleanAdd(single)
        if dead.OutlineCount():
            newol = z.Outline()
            newol.BooleanSubtract(dead)
            z.SetOutline(newol)
            z.SetNeedRefill(True)
            cut_this += dead.OutlineCount()
    if not cut_this:
        print("round %d: 收敛,无新死岛" % rnd)
        break
    total_cut += cut_this
    print("round %d: 减掉 %d 个死岛多边形" % (rnd, cut_this))

pcbnew.ZONE_FILLER(b).Fill(b.Zones())
pcbnew.SaveBoard(PCB, b)
print("共减掉", total_cut, "个死岛轮廓;saved")
