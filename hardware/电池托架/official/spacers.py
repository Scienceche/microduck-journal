#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""官方背板(power_support)拓展垫块。

原理:官方电池 NP-F970 厚 20.6mm,你的电池厚 38mm(+保护壳 43.8mm)——
电池舱深度不够,背板装不回去。垫块垫在背板与躯干之间(4 颗原 M2 螺丝位),
把背板整体后移 T mm,舱深从 28.3mm 增加 T。

一版打 3 种厚度(8/12/16),试装看哪种正好(壳装得下且背不鼓出太多),
选定的那种 ×4 颗 + M2×(T+8) 螺丝替换原 M2×6。

垫块:外径 Ø5,内孔 Ø2.2(M2 穿过),36 棱柱逼近。
"""
import struct

SEG = 36
R_OUT = 2.5
R_IN = 1.1
SIZES = (8.0, 12.0, 16.0)

tris = []


def ring(T):
    import math
    o = [(R_OUT * math.cos(2 * math.pi * i / SEG), R_OUT * math.sin(2 * math.pi * i / SEG))
         for i in range(SEG)]
    ii = [(R_IN * math.cos(2 * math.pi * i / SEG), R_IN * math.sin(2 * math.pi * i / SEG))
          for i in range(SEG)]
    for i in range(SEG):
        j = (i + 1) % SEG
        # 外壁(法线朝外)
        tris.append(((o[i][0], o[i][1], 0), (o[j][0], o[j][1], 0), (o[j][0], o[j][1], T)))
        tris.append(((o[i][0], o[i][1], 0), (o[j][0], o[j][1], T), (o[i][0], o[i][1], T)))
        # 内壁(法线朝内)
        tris.append(((ii[j][0], ii[j][1], 0), (ii[i][0], ii[i][1], 0), (ii[i][0], ii[i][1], T)))
        tris.append(((ii[j][0], ii[j][1], 0), (ii[i][0], ii[i][1], T), (ii[j][0], ii[j][1], T)))
        # 顶面(扇形环)
        tris.append(((o[i][0], o[i][1], T), (o[j][0], o[j][1], T), (ii[j][0], ii[j][1], T)))
        tris.append(((o[i][0], o[i][1], T), (ii[j][0], ii[j][1], T), (ii[i][0], ii[i][1], T)))
        # 底面
        tris.append(((ii[i][0], ii[i][1], 0), (ii[j][0], ii[j][1], 0), (o[j][0], o[j][1], 0)))
        tris.append(((ii[i][0], ii[i][1], 0), (o[j][0], o[j][1], 0), (o[i][0], o[i][1], 0)))


def normal(a, b, c):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    L = (nx * nx + ny * ny + nz * nz) ** 0.5
    return (nx / L, ny / L, nz / L) if L else (0, 0, 0)


for T in SIZES:
    n0 = len(tris)
    ring(T)
    fn = "spacer_T%d.stl" % int(T)
    with open(fn, "wb") as f:
        f.write(("power_support spacer T=%gmm M2" % T).encode().ljust(80, b"\0"))
        cnt = len(tris) - n0
        f.write(struct.pack("<I", cnt))
        for i in range(n0, len(tris)):
            a, b, c = tris[i]
            f.write(struct.pack("<3f", *normal(a, b, c)))
            for p in (a, b, c):
                f.write(struct.pack("<3f", *p))
            f.write(b"\0\0")
    print(fn, cnt, "tris — 1 颗;打印 4 颗(切片软件里复制 4 份)")
