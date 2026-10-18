#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""microduck 电池托架 参数化生成器(纯 Python 输出二进制 STL,无需 CAD 软件)。

电池实测:宽 35 × 长 38 × 高 70 mm(立式 2S 组)。
设计:
  - 托住 35×38 底面,四周挡墙 30mm 高,电池 70 高露上半段(壳体舱内由后盖板约束)
  - 两侧长墙各开 2 个竖向扎带槽(7×22):扎带从左墙槽上去、越过电池顶、
    右墙槽下来,竖向包住电池——高电池防倒靠这个,不是靠底部横勒
  - 一端墙底部开 10×6 走线豁口
  - 平底、全竖直壁,0.4 喷嘴/0.2 层高免支撑直接打

打印后试装:紧了调 CLEAR=0.3,松了调 0.5;材料建议 PETG。
扎带建议 4×300 尼龙(或魔术贴),槽口处垫醋酸胶带护电池包膜。
"""
import struct

# ---------------- 参数(mm) ----------------
BAT_W = 35.0    # 电池宽(X)
BAT_L = 38.0    # 电池长(Y)
BAT_H = 70.0    # 电池高(Z,仅参考,托架不包顶)
CLEAR = 0.4     # 每边间隙
WALL = 2.4      # 壁厚
BASE = 2.4      # 底厚
WALL_H = 30.0   # 挡墙高
NSLOT = 2       # 每面墙扎带槽数
SLOT_W = 7.0    # 扎带槽宽(Y 向)
SLOT_Z0 = 4.0   # 槽底高
SLOT_Z1 = 26.0  # 槽顶高
WIRE_W = 10.0   # 走线豁口宽
WIRE_H = 6.0    # 走线豁口高

IN_W = BAT_W + 2 * CLEAR
IN_L = BAT_L + 2 * CLEAR
OUT_W = IN_W + 2 * WALL
OUT_L = IN_L + 2 * WALL

tris = []


def box(x0, y0, z0, x1, y1, z1):
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    f = [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6),
         (0, 4, 1), (1, 4, 5), (2, 6, 3), (3, 6, 7),
         (0, 3, 4), (3, 7, 4), (1, 5, 2), (2, 5, 6)]
    for a, b, c in f:
        tris.append((v[a], v[b], v[c]))


# 1) 底板
box(-OUT_W / 2, -OUT_L / 2, 0, OUT_W / 2, OUT_L / 2, BASE)

# 2) 长挡墙(X 两侧):底条 + 顶条 + 槽间/槽外立柱,竖槽 2 个
slot_ys = [-OUT_L / 4, OUT_L / 4] if NSLOT == 2 else [0.0]
for wx0, wx1 in ((-OUT_W / 2, -IN_W / 2), (IN_W / 2, OUT_W / 2)):
    box(wx0, -OUT_L / 2, 0, wx1, OUT_L / 2, SLOT_Z0)              # 底条
    box(wx0, -OUT_L / 2, SLOT_Z1, wx1, OUT_L / 2, WALL_H)         # 顶条
    edges = [-OUT_L / 2] + slot_ys + [OUT_L / 2]
    for i in range(len(edges) - 1):
        y0 = edges[i] + (SLOT_W / 2 if i > 0 else 0)
        y1 = edges[i + 1] - (SLOT_W / 2 if i < len(edges) - 2 else 0)
        if y1 > y0:
            box(wx0, y0, SLOT_Z0, wx1, y1, SLOT_Z1)               # 立柱

# 3) -Y 端墙(定位端,无豁口)
box(-IN_W / 2, -OUT_L / 2, 0, IN_W / 2, -IN_L / 2, WALL_H)

# 4) +Y 端墙:底部中间留走线豁口
box(-IN_W / 2, IN_L / 2, 0, -WIRE_W / 2, OUT_L / 2, WALL_H)
box(WIRE_W / 2, IN_L / 2, 0, IN_W / 2, OUT_L / 2, WALL_H)
box(-IN_W / 2, IN_L / 2, WIRE_H, IN_W / 2, OUT_L / 2, WALL_H)


def normal(a, b, c):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    n = (nx * nx + ny * ny + nz * nz) ** 0.5
    return (nx / n, ny / n, nz / n) if n else (0, 0, 0)


out = "battery_tray_35x38x70.stl"
with open(out, "wb") as f:
    f.write(b"microduck battery tray W35 L38 H70 vertical-strap".ljust(80, b"\0"))
    f.write(struct.pack("<I", len(tris)))
    for a, b, c in tris:
        f.write(struct.pack("<3f", *normal(a, b, c)))
        for p in (a, b, c):
            f.write(struct.pack("<3f", *p))
        f.write(b"\0\0")
print("%s: %d 三角形" % (out, len(tris)))
print("外形 %.1f×%.1f×%.1f mm | 内腔 %.1f×%.1f | 竖槽 %d个 %g×%g @z%g~%g | 走线口 %g×%g" %
      (OUT_W, OUT_L, WALL_H, IN_W, IN_L, NSLOT * 2, SLOT_W, SLOT_Z1 - SLOT_Z0,
       SLOT_Z0, SLOT_Z1, WIRE_W, WIRE_H))
