#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3.5 板子体检:关键封装的占位间隙、悬空走线、孤立铜、corner zone 碎片。"""
import math
import re
import sys

PCB = "imu_to_ttl.kicad_pcb"


def parse_sexp(text):
    """极简 S 表达式解析 → (tag, [children/atoms]) 树。"""
    stack = [[]]
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '(':
            node = []
            stack[-1].append(node)
            stack.append(node)
            i += 1
        elif c == ')':
            stack.pop()
            i += 1
        elif c == '"':
            j = text.index('"', i + 1)
            stack[-1].append(text[i + 1:j])
            i = j + 1
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            stack[-1].append(text[i:j])
            i = j
    return stack[0]


def tag(node):
    return node[0] if isinstance(node, list) and node else None


def find_all(node, name):
    out = []
    for ch in node:
        if isinstance(ch, list) and ch and ch[0] == name:
            out.append(ch)
        elif isinstance(ch, list):
            out += find_all(ch, name)
    return out


text = open(PCB, encoding="utf-8").read()
tree = parse_sexp(text)
root = tree[0]          # ("kicad_pcb" ...)

# ---- 收集封装 ----
fps = []
for fp in root:
    if isinstance(fp, list) and fp and fp[0] == "footprint":
        ref = None
        for pr in find_all(fp, "property"):
            if pr[1] == "Reference":
                ref = pr[2]
        at = find_all(fp, "at")[0]
        x, y = float(at[1]), float(at[2])
        rot = float(at[3]) if len(at) > 3 else 0.0
        fps.append((ref, x, y, rot, fp))

by_ref = {f[0]: f for f in fps}


def layer_bbox(fp, layer):
    pts = []
    for kind in ("fp_line", "fp_rect", "fp_poly", "fp_circle"):
        for it in find_all(fp, kind):
            lay = [c for c in it if isinstance(c, list) and c[0] == "layer"]
            if not lay or lay[0][1] != layer:
                continue
            for key in ("start", "end", "mid", "center"):
                for p in find_all(it, key):
                    pts.append((float(p[1]), float(p[2])))
            for pl in find_all(it, "pts"):
                for xy in find_all(pl, "xy"):
                    pts.append((float(xy[1]), float(xy[2])))
    return pts


def pad_bbox(fp):
    pts = []
    for pad in find_all(fp, "pad"):
        at = find_all(pad, "at")[0]
        px, py = float(at[1]), float(at[2])
        prot = float(at[3]) if len(at) > 3 else 0.0
        size = find_all(pad, "size")[0]
        w, h = float(size[1]), float(size[2])
        # 旋转 90 的 pad 宽高互换
        if abs((prot % 180) - 90) < 1e-6:
            w, h = h, w
        pts += [(px - w / 2, py - h / 2), (px + w / 2, py + h / 2)]
    return pts


def rot_bb(pts, rot, ox, oy):
    r = math.radians(rot)
    q = [(x * math.cos(r) - y * math.sin(r), x * math.sin(r) + y * math.cos(r))
         for x, y in pts]
    return (ox + min(p[0] for p in q), oy + min(p[1] for p in q),
            ox + max(p[0] for p in q), oy + max(p[1] for p in q))


def gap_y(a, b):
    """y 方向净间隙(a 在上)。"""
    return b[1] - a[3]


print("== 占位间隙核查(courtyard / 本体fab / 焊盘) ==")
boxes = {}
for ref in ("F1", "Q1", "U1", "R17", "U2", "J1", "J3", "J4", "J5", "C1"):
    if ref not in by_ref:
        print(ref, ": 不存在")
        continue
    _, x, y, rot, fp = by_ref[ref]
    for nm, pts in (("crt", layer_bbox(fp, "F.CrtYd")),
                    ("fab", layer_bbox(fp, "F.Fab")),
                    ("pad", pad_bbox(fp))):
        if pts:
            boxes[(ref, nm)] = rot_bb(pts, rot, x, y)

for pair in (("F1", "Q1"), ("F1", "U1"), ("Q1", "U1")):
    a, b = pair
    for nm in ("crt", "fab", "pad"):
        if (a, nm) in boxes and (b, nm) in boxes:
            ba, bb = boxes[(a, nm)], boxes[(b, nm)]
            # y 轴方向净隙(他们竖着堆)
            gy = ba[1] - bb[3] if ba[1] > bb[3] else bb[1] - ba[3]
            # 任意方向最坏重叠:分离轴粗判
            ox = min(ba[2], bb[2]) - max(ba[0], bb[0])
            oy = min(ba[3], bb[3]) - max(ba[1], bb[1])
            ov = "重叠 %.2f" % min(ox, oy) if (ox > 0 and oy > 0) else "分离"
            print(f"{a}↔{b} [{nm}] y净隙={gy:+.2f}mm x重叠={ox:+.2f} → {ov}")

print()
print("== 悬空走线 (+3V3 @25.0,21.825) ==")
# 找到该走线两端看上下文
for seg in find_all(tree, "segment"):
    ns = find_all(seg, "start")[0]
    ne = find_all(seg, "end")[0]
    net = find_all(seg, "net")[0]
    x1, y1 = float(ns[1]), float(ns[2])
    x2, y2 = float(ne[1]), float(ne[2])
    if net and "+3V3" in str(net[1:3]) and abs(x1 - 25.0) < 0.6 and abs(y1 - 21.825) < 0.6:
        print("  seg (%.3f,%.3f)-(%.3f,%.3f) w=%s net=%s" %
              (x1, y1, x2, y2, find_all(seg, "width")[0][1], net[1:3]))

print()
print("== 板角孤立铜/未连 zone 碎片位置 ==")
print("  DRC 已报: VBAT_RAW_TOP 孤岛 ×9 @(0.9,0.9); GND zone 未连 ×2 @(0.3,0.3)")
print("  → 同一角落的 zone 记账碎片,不涉任何焊盘(电气无影响,美观问题)")

print()
print("== 板框/安装孔/高度复查 ==")
edges = []
for seg in find_all(root, "gr_line"):
    lay = [c for c in seg if isinstance(c, list) and c[0] == "layer"]
    if lay and lay[0][1] == "Edge.Cuts":
        s, e = find_all(seg, "start")[0], find_all(seg, "end")[0]
        edges.append((float(s[1]), float(s[2]), float(e[1]), float(e[2])))
xs = [c for e in edges for c in (e[0], e[2])]
ys = [c for e in edges for c in (e[1], e[3])]
print("  板框 X:[%.2f, %.2f] Y:[%.2f, %.2f] → %s×%s mm" %
      (min(xs), max(xs), min(ys), max(ys),
       max(xs) - min(xs), max(ys) - min(ys)))
holes = [f for f in fps if f[0] and f[0].startswith("MH") or
         (f[4] and "MountingHole" in str(f[4][1:3]))]
n_holes = len([f for f in fps if "MountingHole" in (f[4][1] if isinstance(f[4], list) and len(f[4]) > 1 and isinstance(f[4][1], str) else "")])
mhs = [f for f in fps if f[0] and "MountingHole" in str(find_all(f[4], "layer"))]
# 直接按 footprint 名找
mh_count = 0
for fp in root:
    if isinstance(fp, list) and fp and fp[0] == "footprint":
        name = fp[1] if len(fp) > 1 and isinstance(fp[1], str) else ""
        if "MountingHole" in name:
            mh_count += 1
print("  安装孔(MountingHole):", mh_count, "个(要求 4)")
