#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""microduck 2S 软包电池组 保护壳 v2(底部出线版,三件:壳体/盖子/支架)。

电池:裸软包 2S 7.4V 2000mAh,实测 35(宽)×38(长)×70(高) mm,**含保护板**;
保护板 + XT60 输出口 + JST-XH 3pin 平衡口全在**底面**。

v2 关键改动(相对 v1):
  1. 电池不再坐底 —— 内腔两侧各一条支撑凸轨(贴长边),底部留 6mm 走线通道,
     保护板/接头悬空不受压,线从通道走到出线端
  2. 出线豁口从"端墙上部"改到"端墙底部":26×12,XT60 整个能穿出壳外
     (接头留在壳外,充电/接板不用开壳)
  3. 支架的出线端墙同步开底部豁口,插装时出线端对准
软包要点不变:全包裹、每边 0.5 间隙、顶部 3.6mm 膨胀余量、
锁带只勒壳不碰电芯、盖裙带对齐锁带槽、免支撑。

装配:壳内凸轨朝下放电池(保护板朝下、出线端对豁口)→ 线穿出豁口 →
扣盖 → 扎带①穿壳顶槽(同时锁壳+盖)→ 插入支架(豁口端对豁口)→
扎带②穿支架中部槽 → 固定耳 M2×6 锁进鸭身。
"""
import struct

# ---------------- 参数(mm) ----------------
BAT_W = 35.0    # 电池宽(X)
BAT_L = 38.0    # 电池长(Y)
BAT_H = 70.0    # 电池高(Z)
CLEAR = 0.5     # 每边间隙
TOP_GAP = 3.6   # 顶部膨胀余量
WALL = 2.4      # 壁厚
BASE = 2.4      # 底厚
PAD_H = 6.0     # 支撑轨高(底部走线通道)
RAIL_W = 4.0    # 支撑轨宽
RAIL_TRIM = 8.0  # 轨两端让位(避开底面端部的保护板边缘)
SKIRT = 8.0     # 盖沿高度
GAP_FIT = 0.35  # 盖子配合间隙
TIE_W = 7.0     # 锁带槽宽
TIE_H = 4.0     # 锁带槽高
WIRE_W = 26.0   # 底部出线豁口宽(XT60 21mm + 线摆动)
WIRE_H = 12.0   # 出线豁口高

IN_W = BAT_W + 2 * CLEAR
IN_L = BAT_L + 2 * CLEAR
BODY_H = BASE + PAD_H + BAT_H + TOP_GAP
OUT_W = IN_W + 2 * WALL
OUT_L = IN_L + 2 * WALL
TIE_Z0 = BODY_H - 5.0
TIE_Z1 = BODY_H - 1.0

tris = []


def box(x0, y0, z0, x1, y1, z1):
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    f = [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6),
         (0, 4, 1), (1, 4, 5), (2, 6, 3), (3, 6, 7),
         (0, 3, 4), (3, 7, 4), (1, 5, 2), (2, 5, 6)]
    for a, b, c in f:
        tris.append((v[a], v[b], v[c]))


# ============ 壳体 ============
body_n0 = len(tris)
box(-OUT_W / 2, -OUT_L / 2, 0, OUT_W / 2, OUT_L / 2, BASE)              # 底板
# 两侧支撑轨(电池坐轨,底面中部悬空走线)
for rx0, rx1 in ((-IN_W / 2, -IN_W / 2 + RAIL_W), (IN_W / 2 - RAIL_W, IN_W / 2)):
    box(rx0, -IN_L / 2 + RAIL_TRIM, BASE, rx1, IN_L / 2 - RAIL_TRIM, BASE + PAD_H)
# 长墙 + 顶部锁带槽
tie_slots = [(-OUT_L / 4 - TIE_W / 2, -OUT_L / 4 + TIE_W / 2),
             (OUT_L / 4 - TIE_W / 2, OUT_L / 4 + TIE_W / 2)]
for cx0, cx1 in ((-OUT_W / 2, -IN_W / 2), (IN_W / 2, OUT_W / 2)):
    e = [-OUT_L / 2] + [s for sl in tie_slots for s in sl] + [OUT_L / 2]
    box(cx0, e[0], 0, cx1, e[-1], TIE_Z0)
    box(cx0, e[0], TIE_Z1, cx1, e[-1], BODY_H)
    for i in range(0, len(e), 2):
        if e[i + 1] > e[i]:
            box(cx0, e[i], TIE_Z0, cx1, e[i + 1], TIE_Z1)
box(-IN_W / 2, -OUT_L / 2, 0, IN_W / 2, -IN_L / 2, BODY_H)              # -Y 端墙(实)
# +Y 端墙:底部出线豁口(WIRE_W × WIRE_H)
box(-IN_W / 2, IN_L / 2, 0, -WIRE_W / 2, OUT_L / 2, BODY_H)
box(WIRE_W / 2, IN_L / 2, 0, IN_W / 2, OUT_L / 2, BODY_H)
box(-IN_W / 2, IN_L / 2, WIRE_H, IN_W / 2, OUT_L / 2, BODY_H)
BODY_TRIS = len(tris) - body_n0

# ============ 盖子(反扣杯状) ============
cap_n0 = len(tris)
SK_IN_W = OUT_W + 2 * GAP_FIT
SK_IN_L = OUT_L + 2 * GAP_FIT
SK_OUT_W = SK_IN_W + 2 * WALL
SK_OUT_L = SK_IN_L + 2 * WALL
CAP_H = SKIRT + WALL
CAP_TIE_Z0, CAP_TIE_Z1 = 3.0, 7.0      # 装配后对应壳体 TIE_Z0..Z1
box(-SK_OUT_W / 2, -SK_OUT_L / 2, SKIRT, SK_OUT_W / 2, SK_OUT_L / 2, CAP_H)  # 顶板
for cx0, cx1 in ((-SK_OUT_W / 2, -SK_IN_W / 2), (SK_IN_W / 2, SK_OUT_W / 2)):
    e = [-SK_OUT_L / 2] + [s for sl in tie_slots for s in sl] + [SK_OUT_L / 2]
    box(cx0, e[0], 0, cx1, e[-1], CAP_TIE_Z0)
    box(cx0, e[0], CAP_TIE_Z1, cx1, e[-1], SKIRT)
    for i in range(0, len(e), 2):
        if e[i + 1] > e[i]:
            box(cx0, e[i], CAP_TIE_Z0, cx1, e[i + 1], CAP_TIE_Z1)
box(-SK_IN_W / 2, -SK_OUT_L / 2, 0, SK_IN_W / 2, -SK_IN_L / 2, SKIRT)  # -Y 实裙
box(-SK_IN_W / 2, SK_IN_L / 2, 0, -SK_IN_W / 2 + 6.0, SK_OUT_L / 2, SKIRT)
box(SK_IN_W / 2 - 6.0, SK_IN_L / 2, 0, SK_IN_W / 2, SK_OUT_L / 2, SKIRT)
CAP_TRIS = len(tris) - cap_n0

# ============ 支架 ============
mnt_n0 = len(tris)
MNT_CLEAR = 0.5
MNT_WALL = 2.4
MNT_IN_W = OUT_W + 2 * MNT_CLEAR
MNT_IN_L = OUT_L + 2 * MNT_CLEAR
MNT_W = MNT_IN_W + 2 * MNT_WALL
MNT_L = MNT_IN_L + 2 * MNT_WALL
MNT_H = 42.0
EAR = 7.0
M_TIE_Z0, M_TIE_Z1 = 28.0, 32.0
for cx0, cx1 in ((-MNT_W / 2, -MNT_IN_W / 2), (MNT_IN_W / 2, MNT_W / 2)):
    e = [-MNT_L / 2] + [s for sl in tie_slots for s in sl] + [MNT_L / 2]
    box(cx0, e[0], 0, cx1, e[-1], M_TIE_Z0)
    box(cx0, e[0], M_TIE_Z1, cx1, e[-1], MNT_H)
    for i in range(0, len(e), 2):
        if e[i + 1] > e[i]:
            box(cx0, e[i], M_TIE_Z0, cx1, e[i + 1], M_TIE_Z1)
box(-MNT_IN_W / 2, -MNT_L / 2, 0, MNT_IN_W / 2, -MNT_IN_L / 2, MNT_H)  # -Y 实墙
# +Y 端墙:底部出线豁口(与壳体对齐,穿 XT60)
box(-MNT_IN_W / 2, MNT_IN_L / 2, 0, -WIRE_W / 2, MNT_L / 2, MNT_H)
box(WIRE_W / 2, MNT_IN_L / 2, 0, MNT_IN_W / 2, MNT_L / 2, MNT_H)
box(-MNT_IN_W / 2, MNT_IN_L / 2, WIRE_H, MNT_IN_W / 2, MNT_L / 2, MNT_H)
# 四角底板条(开口底走线)
for cx0, cx1 in ((-MNT_W / 2, -MNT_W / 2 + 10.0), (MNT_W / 2 - 10.0, MNT_W / 2)):
    box(cx0, -MNT_L / 2, 0, cx1, MNT_L / 2, BASE)
for cy0, cy1 in ((-MNT_L / 2, -MNT_L / 2 + 10.0), (MNT_L / 2 - 10.0, MNT_L / 2)):
    box(-MNT_W / 2 + 10.0, cy0, 0, MNT_W / 2 - 10.0, cy1, BASE)
# 四角固定耳
for ex, ey in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
    x0 = -MNT_W / 2 - EAR if ex < 0 else MNT_W / 2
    y0 = ey * (MNT_L / 2) - (EAR if ey < 0 else 0)
    box(x0, y0, 0, x0 + EAR, y0 + EAR, BASE)
MNT_TRIS = len(tris) - mnt_n0


def normal(a, b, c):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    n = (nx * nx + ny * ny + nz * nz) ** 0.5
    return (nx / n, ny / n, nz / n) if n else (0, 0, 0)


def write_stl(fname, lo, cnt, label):
    with open(fname, "wb") as f:
        f.write(("microduck %s 2S pouch bottom-exit 35x38x70" % label).encode().ljust(80, b"\0"))
        f.write(struct.pack("<I", cnt))
        for i in range(lo, lo + cnt):
            a, b, c = tris[i]
            f.write(struct.pack("<3f", *normal(a, b, c)))
            for p in (a, b, c):
                f.write(struct.pack("<3f", *p))
            f.write(b"\0\0")


write_stl("battery_case_body.stl", body_n0, BODY_TRIS, "case BODY v2")
write_stl("battery_case_cap.stl", cap_n0, CAP_TRIS, "case CAP v2")
write_stl("battery_mount.stl", mnt_n0, MNT_TRIS, "case MOUNT v2")
print("壳体: %d tris | 轨高%g 通道 | 内腔 %.1f×%.1f×%.1f 外形 %.1f×%.1f×%.1f" %
      (BODY_TRIS, PAD_H, IN_W, IN_L, BODY_H - BASE, OUT_W, OUT_L, BODY_H))
print("盖子: %d tris | %.1f×%.1f×%.1f | 支架: %d tris | 内 %.1f×%.1f×%.1f 含耳 %.1f×%.1f" %
      (CAP_TRIS, SK_OUT_W, SK_OUT_L, CAP_H, MNT_TRIS, MNT_IN_W, MNT_IN_L, MNT_H,
       MNT_W + 2 * EAR, MNT_L + 2 * EAR))
print("出线豁口 %g×%g @壳底+支架底 对齐 | 锁带槽 壳z%.0f~%.0f" %
      (WIRE_W, WIRE_H, TIE_Z0, TIE_Z1))
