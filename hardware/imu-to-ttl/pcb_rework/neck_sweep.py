#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""颈部全行程扫掠包络 vs. v3.7 板区干涉计算。

【2026-10-21 结论】名义关节全行程（neck_pitch -90..+60 × head_pitch ±90 × head_yaw ±170）
的扫掠可名义到达板区任意位置（最低 Z≈67）——但该扫掠**必须穿过躯干外壳**才能成立。
脖子链从壳体前上方槽口伸出，头/连杆被壳体挡在体外；唯一通向板区的路径是前部槽口。
工程处置（已入 v3.7 设计规则）：
  1. 前部 X>+8(世界) 禁高区 —— 覆盖槽口方向的扫掠
  2. 固件 neck_pitch 后仰软限位 -45°（必装，写进装配文档）
  3. 左后象限保守禁高 + 首装实测脖子后仰极限复核
本脚本的数值输出仅在"无壳体阻挡"假设下成立，用作最坏界参考。


运动学链取自 robot_walk.xml（零位世界系，mm）：
  neck 关节     (26, 14.5, 152.44)，neck_pitch −90°..+60°（绕世界 −Y 轴）
  neck_pitch 体 关节上方 50mm（连杆，本体 20×34×50 盒）
  yaw_roll 体   再上 18.7mm（head_yaw ±170°，头壳伺机群 20×34 盒）
  bottom_head   再上 ~15mm（头壳，半径 ~40 球，含嘴尖 −77mm）
head_pitch ±90° 在 neck_pitch 体上。

输出：板区网格（世界 X −19..+17 × Y −15..+15）上扫掠体最低到达高度，
与禁高阈值（板面 126.5 + 允许件高）比对，打印禁高多边形边界。
"""
import math

NECK = (26.0, 14.5, 152.44)
LINK_LEN = 50.0
YAW_ROLL_OFF = 18.7
HEAD_OFF = 15.0
HEAD_R = 40.0
MOUTH = 77.0  # 嘴尖到 yaw_roll 中心的距离（head 局部 -Z）

# 实体包络（挂在链上）：(沿链偏移 mm, 包络半径 mm) —— 连杆伺机群 + 头
SOLIDS = [
    (10.0, 17.0),   # 颈连杆下部（舵机本体半宽 17）
    (30.0, 17.0),   # 颈连杆上部
    (50.0, 17.0),   # head_pitch 舵机
    (68.7, 17.0),   # head_yaw 舵机群
    (83.7, HEAD_R),  # 头壳（球近似）
    (68.7 + MOUTH, 8.0),  # 嘴尖延伸（head_pitch 折叠时可达）
]

BOARD_Z = 126.5      # 板面（舵机顶面）
TALL_TOP = 134.5     # 高件顶（JST 立式含插头 ~8mm）
LOW_TOP = 129.0      # 矮件顶（~2.5mm 件）


def chain_points(neck_deg, head_deg):
    """返回扫掠实体圆心世界坐标列表。链在 neck 关节上方沿世界 +Z，绕过关节
    平行世界 Y 的轴旋转（前倾为 +X 方向）。"""
    th = math.radians(neck_deg)
    # 连杆方向：零位 (0,0,1)，绕 +Y 轴旋转（前倾 → +X）
    # R_y(th): z 轴 → (sin th, 0, cos th)
    d = (math.sin(th), 0.0, math.cos(th))
    out = []
    for off, r in SOLIDS[:5]:
        out.append((NECK[0] + off * d[0], NECK[1], NECK[2] + off * d[2], r))
    # 头 + 嘴尖：head_pitch 叠加旋转（绕连杆局部 Y 轴再转 head_deg）
    ph = th + math.radians(head_deg)
    for off, r in ((HEAD_OFF, HEAD_R), (MOUTH, 8.0)):
        # head 段从 yaw_roll 点（= 关节沿链 50+18.7=68.7mm）继续转 ph
        base = LINK_LEN + YAW_ROLL_OFF
        e = (math.sin(ph), 0.0, math.cos(ph))
        out.append((NECK[0] + (YAW_ROLL_OFF * d[0] + off * e[0]),
                    NECK[1],
                    NECK[2] + (YAW_ROLL_OFF * d[2] + off * e[2]), r))
    # head_yaw ±170° 使头部绕链轴横摆：头部实体横向再采样 ±25mm
    expanded = []
    for cx, cy, cz, r in out:
        expanded.append((cx, cy, cz, r))
        if r >= HEAD_R:
            expanded.append((cx, cy - 25, cz, r))
            expanded.append((cx, cy + 25, cz, r))
    return expanded


def min_reach(x, y):
    """该板上方 (x,y) 列被扫掠体到达的最低 Z；无扫掠返回 None"""
    best = None
    for nd in range(-90, 61, 5):
        for hd in (-90, 0, 90, -45, 45):
            for cx, cy, cz, r in chain_points(nd, hd):
                # 只关心落在板 X 范围附近的实体（Y 方向脖子在 14.5，头_yaw 摆动会横向展开）
                # head_yaw ±170° 把头甩向左右：横向可达 |Y-14.5| <= 链长
                dy = abs(y - cy)
                # 纵向投影距离
                dx = x - cx
                horiz = math.hypot(dx, 0)
                if dy > r + 60:   # 头_yaw 横摆预留 60mm
                    continue
                if horiz > r + 5:
                    continue
                z = cz - math.sqrt(max(r * r - dx * dx, 0))
                if best is None or z < best:
                    best = z
    return best


print('板区扫掠最低到达高度（Z，板面=126.5）—— X: 前缘+17..后缘-19')
print('%6s' % 'X\\Y', ''.join('%6.0f' % y for y in range(-15, 16, 3)))
for x in range(17, -20, -2):
    row = []
    for y in range(-15, 16, 3):
        z = min_reach(x, y)
        row.append('  ---' if z is None else '%6.1f' % z)
    print('%6.0f' % x, ''.join(row))

# 禁高判定：扫掠最低 Z < 件顶 → 该点禁高件
print('\n禁高区判定（T=高件禁区 @<%.1f, L=矮件禁区 @<%.1f, .=自由）' % (TALL_TOP, LOW_TOP))
for x in range(17, -20, -1):
    row = []
    for y in range(-15, 16, 2):
        z = min_reach(x, y)
        if z is None or z >= TALL_TOP:
            row.append('.')
        elif z >= LOW_TOP:
            row.append('l')
        else:
            row.append('T')
    print('%4d  %s' % (x, ''.join(row)))
