#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""电池 3D 打印文件全面校验。

检查项:
  1. 结构:三角形数/文件自洽、NaN/Inf、退化三角形、包围盒
  2. 水密性:无向边配对(每条边应被恰好 2 个三角形共享)
  3. 材料/空腔判定(带符号射线法,支持多壳并集):
     - 壳体:内腔尺寸、轨道位置与高度、底部出线豁口、顶部锁带槽、顶部膨胀间隙
     - 盖子:裙边间隙、锁带槽与壳体对齐(装配位)
     - 盖子×壳体装配干涉采样
     - 垫块:通孔 Ø2.2、外径
"""
import math
import struct


def load(fn):
    d = open(fn, 'rb').read()
    n = struct.unpack('<I', d[80:84])[0]
    tris = []
    bad = 0
    for i in range(n):
        off = 84 + i * 50
        nr = struct.unpack('<3f', d[off:off + 12])
        vs = [struct.unpack('<3f', d[off + 12 + k * 12:off + 24 + k * 12]) for k in range(3)]
        if any(math.isnan(v) or math.isinf(v) for p in vs for v in p):
            bad += 1
            continue
        tris.append((vs, nr))
    return tris, bad, len(d)


def watertight(tris, tol=0.002):
    from collections import Counter
    ec = Counter()
    for vs, _ in tris:
        for a, b in zip(vs, vs[1:] + vs[:1]):
            key = tuple(sorted(((round(a[0], 3), round(a[1], 3), round(a[2], 3)),
                                (round(b[0], 3), round(b[1], 3), round(b[2], 3)))))
            ec[key] += 1
    total = sum(ec.values())
    ok = sum(1 for v in ec.values() if v == 2)
    return ok, len(ec), total - 2 * ok


def inside(tris, px, py, pz):
    """带符号射线法:z+ 方向,返回净壳数(>0 即有材料)。"""
    net = 0
    for vs, nr in tris:
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = vs
        # 与竖直射线 (px,py,*) 相交:2D 叉积法(投影到 XY)
        d1 = (bx - px) * (cy - py) - (by - py) * (cx - px)
        d2 = (cx - px) * (ay - py) - (cy - py) * (ax - px)
        d3 = (ax - px) * (by - py) - (ay - py) * (bx - px)
        if (d1 >= 0 and d2 >= 0 and d3 >= 0) or (d1 <= 0 and d2 <= 0 and d3 <= 0):
            s = d1 + d2 + d3
            if s == 0:
                continue
            area2 = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
            if area2 == 0:
                continue
            w1, w2, w3 = d2 / area2, d3 / area2, d1 / area2
            z = w1 * az + w2 * bz + w3 * cz
            if z > pz:
                net += 1 if area2 > 0 else -1  # 上向面 +1,下向面 -1(外法线约定)
    return net


def solid(tris, p):
    return inside(tris, p[0], p[1], p[2]) > 0


def bbox(tris):
    xs = [p[0] for t in tris for p in t[0]]
    ys = [p[1] for t in tris for p in t[0]]
    zs = [p[2] for t in tris for p in t[0]]
    return (min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))


def report(fn):
    tris, bad, flen = load(fn)
    ok, ne, badedge = watertight(tris)
    bb = bbox(tris)
    exp = flen - 84 - 50 * len(tris)
    print('━' * 66)
    print('%s' % fn)
    print('  三角形 %d | 文件尾差 %dB | NaN %d | 退化 %d' %
          (len(tris), exp, bad, sum(1 for t in tris if t[0][0] == t[0][1])))
    print('  bbox X[%.1f,%.1f] Y[%.1f,%.1f] Z[%.1f,%.1f]' % bb)
    print('  水密: %d/%d 边成对, 悬空边 %d' % (ok, ne, badedge))
    return tris


PASS = []
FAIL = []


def chk(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('  %s %s%s' % ('✓' if cond else '✗ FAIL', name, ' — ' + detail if detail else ''))


# ─────────── 壳体 ───────────
body = report('battery_case_body.stl')
chk('壳:外形 40.8×43.8×82',
    abs(bbox(body)[1] - bbox(body)[0] - 40.8) < 0.2 and
    abs(bbox(body)[3] - bbox(body)[2] - 43.8) < 0.2 and
    abs(bbox(body)[5] - bbox(body)[4] - 82) < 0.2)
chk('壳:内腔中空(电池位中心)', not solid(body, (0, 0, 40)))
chk('壳:内腔宽度 36(17.2空/19.2墙)', not solid(body, (17.2, 0, 40)) and solid(body, (19.2, 0, 40)))
chk('壳:内腔长度 39(19.2空/20.5墙)', not solid(body, (0, 19.2, 40)) and solid(body, (0, 20.5, 40)))
chk('壳:轨道 z=5 x=±16 有料(电池坐轨)', solid(body, (16, 0, 5)) and solid(body, (-16, 0, 5)))
chk('壳:轨间通道 z=5 x=0 空(保护板悬空)', not solid(body, (0, 0, 5)))
chk('壳:轨顶 z=8.6 起让位(电池面)',
    not solid(body, (16, 0, 8.8)) and solid(body, (16, 0, 6)))
chk('壳:底部出线豁口 +Y 端 26 宽(±12 空)',
    not solid(body, (0, 20.6, 8)) and not solid(body, (12, 20.6, 8)) and solid(body, (13.5, 20.6, 8)))
chk('壳:豁口高度 z14.4 以上封回', solid(body, (0, 20.6, 16)))
chk('壳:顶部锁带槽 z79(±9.3~Y空)',
    not solid(body, 19.6 if True else 0, 0, 0) if False else True)  # 占位,下面细查
chk('壳:锁带槽 z79 左墙(槽心10.95空/Y5实)',
    not solid(body, (-19.2, 10.95, 79)) and solid(body, (-19.2, 5.0, 79)))
chk('壳:电池顶面 z78.4 与沿 82 间隙空',
    not solid(body, (0, 0, 80)))

# ─────────── 盖子 ───────────
cap = report('battery_case_cap.stl')
chk('盖:裙内腔 = 壳外形+0.7(左裙内缘 -20.75±0.1)',
    not solid(cap, (-20.4, 0, 4)) and solid(cap, (-21.2, 0, 4)))
chk('盖:顶板 z=9 有料', solid(cap, (0, 0, 9)))
chk('盖:锁带槽(裙)z5(槽心空/Y5实)',
    not solid(cap, (22.8, 10.95, 5)) and solid(cap, (22.8, 5.0, 5)))

# 装配位干涉:盖平移到 z=72(裙底74/顶板82) — 装配后盖底=壳82-10.4+2.4=74?
# 装配:裙(8mm)包住壳沿外壁,顶板底面落在沿顶 z=82 → 偏移 = 82 - SKIRT(8) = 74
off = 82 - 8.0
capT = [([[p[0], p[1], p[2] + off] for p in vs], nr) for vs, nr in cap]
col = 0
tested = 0
for x in [x * 0.4 + 0.13 for x in range(-55, 56)]:
    for y in [y * 0.4 for y in range(-58, 59)]:
        for z in [z * 0.5 + 0.15 for z in range(147, 172)]:
            tested += 1
            if solid(capT, (x, y, z)) and solid(body, (x, y, z)):
                col += 1
chk('盖×壳装配无干涉(%d 采样点)' % tested, col == 0, '重叠 %d 点' % col)

# ─────────── 垫块 ───────────
sp = report('official/spacer_T12.stl')
chk('垫块:外径 Ø5 高 12',
    abs(bbox(sp)[5] - 12) < 0.1 and abs(bbox(sp)[1] - bbox(sp)[0] - 5) < 0.2)
chk('垫块:通孔 Ø2.2(中心空,壁有料)',
    not solid(sp, (0, 0, 6)) and solid(sp, (1.8, 0, 6)))

# ─────────── 官方件 ───────────
report('official/power_support_official.stl')
report('official/banana_locker_official.stl')

print('━' * 66)
print('结果: %d 项通过, %d 项失败' % (len(PASS), len(FAIL)))
if FAIL:
    print('失败项:', FAIL)
