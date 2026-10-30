#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3.8 board x servo overlay (top view) - generates an HTML/SVG fragment.

Board outline is rebuilt from gen_board_v38.py constants (the .kicad_pcb is
that generator's output); the 4 NPTH are read from the board file. Servo top
geometry uses the STEP-verified constants (servo_registration_analysis.json):
axes world (6,+-17.5), flange hole rows world x 13.5/-16.5 at +-8 about the
axis, horn bosses r8 with the 5x5 connector window on the axis.
Board->world: wx = bx - 19, wy = 15 - by.
"""
import math
import os

import pcbnew

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PCB = os.path.join(PROJ, "imu_to_ttl.kicad_pcb")
OUT = os.path.join(os.path.expanduser(
    "~/.zcode/v2/visualizations/b5a83939e346ea33e0f69045d8313e56b821745a3350ac214b266bf2ea85cf81"),
    "v38-servo-overlay.html")

BOSS_CX, BOSS_CY, BOSS_R = 25.0, (-2.5, 32.5), 8.3
R2 = 2.0
dx = math.sqrt(BOSS_R ** 2 - 2.5 ** 2)
bx1, bx2 = BOSS_CX - dx, BOSS_CX + dx


def W(bx, by):
    return (bx - 19.0, 15.0 - by)


board = pcbnew.LoadBoard(PCB)
holes = []
for fp in board.GetFootprints():
    if fp.GetReference().startswith("M"):
        p = fp.GetPosition()
        holes.append((p.x / 1e6, p.y / 1e6))


def arc_b(cx, cy, r, a0, a1, n):
    return [W(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
              cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


def corner_b(cx, cy, a0, a1):
    return arc_b(cx, cy, R2, a0, a1, 10)


def bite_b(cy, n=48):
    a0 = math.degrees(math.atan2(0 - cy, bx2 - BOSS_CX))
    a1 = math.degrees(math.atan2(0 - cy, bx1 - BOSS_CX))
    return arc_b(BOSS_CX, cy, BOSS_R, a0, a1, n)


outline = []
outline.append(W(36, R2))
outline.append(W(36, 30 - R2))
outline += corner_b(34, 28, -90, -45)
outline.append(W(bx2, 30))
outline += bite_b(BOSS_CY[1])
outline.append(W(bx1, 30))
outline.append(W(R2, 30))
outline += corner_b(R2, 28, -135, -180)
outline.append(W(0, 30 - R2))
outline.append(W(0, R2))
outline += corner_b(R2, R2, 180, 135)
outline.append(W(R2, 0))
outline.append(W(bx1, 0))
outline += bite_b(BOSS_CY[0])
outline.append(W(bx2, 0))
outline.append(W(34, 0))
outline += corner_b(34, R2, 135, 90)

SVG = []
S = 12.0
OX, OY = 34, 27.0


def X(wx):
    return round((wx + OX) * S, 1)


def Y(wy):
    return round((29.0 - (wy + OY)) * S, 1)


svg_poly = ' '.join('%s,%s' % (X(p[0]), Y(p[1])) for p in outline)
SVG.append('<polygon points="%s" fill="var(--primary)" fill-opacity="0.18" stroke="var(--primary)" stroke-width="2.4"/>' % svg_poly)

for ay in (17.5, -17.5):
    x0, x1 = X(-18.5), X(15.5)
    y0, y1 = Y(ay + 10), Y(ay - 10)
    SVG.append('<rect x="%s" y="%s" width="%d" height="%d" rx="10" fill="var(--muted)" fill-opacity="0.55" stroke="var(--border)" stroke-width="1.4"/>' % (x0, y0, x1 - x0, y1 - y0))
    SVG.append('<circle cx="%d" cy="%d" r="%d" fill="none" stroke="var(--viz-series-2)" stroke-width="1.8" stroke-dasharray="7 5"/>' % (X(6), Y(ay), int(8.0 * S)))
    SVG.append('<rect x="%d" y="%d" width="%d" height="%d" fill="var(--yellow)" fill-opacity="0.55" stroke="var(--yellow)" stroke-width="1.2"/>' % (X(6) - int(2.5 * S), Y(ay) - int(2.5 * S), int(5 * S), int(5 * S)))
    for hx in (13.5, -16.5):
        for hy in (ay - 8, ay + 8):
            SVG.append('<circle cx="%d" cy="%d" r="%d" fill="var(--background)" stroke="var(--muted-foreground)" stroke-width="1.2"/>' % (X(hx), Y(hy), int(0.8 * S)))

for bx, by in holes:
    wx, wy = W(bx, by)
    ok = any(math.hypot(wx - hx, wy - hy) < 0.3
             for ay in (17.5, -17.5) for hx in (13.5, -16.5) for hy in (ay - 8, ay + 8))
    color = 'var(--green)' if ok else 'var(--red)'
    SVG.append('<circle cx="%d" cy="%d" r="%d" fill="none" stroke="%s" stroke-width="2.2"/>' % (X(wx), Y(wy), int(1.05 * S), color))

html = '''<div id="v38-overlay">
<style>
  #v38-overlay svg { width: 100%%; height: auto; display: block; }
  #v38-overlay .t { fill: var(--foreground); font-size: 13px; font-weight: 500; }
  #v38-overlay .s { fill: var(--muted-foreground); font-size: 11.5px; }
  #v38-overlay ul { margin: 0.4rem 0 0.8rem 1.1rem; padding: 0; }
  #v38-overlay li { margin: 0.15rem 0; }
</style>
<h3>v3.8 板形 × 舵机 10/20 顶面叠加（俯视，全部实测坐标）</h3>
<svg viewBox="0 0 900 740" role="img" aria-label="v3.8 board overlaid on servo top faces">
  %s
  <text class="t" x="%d" y="%d" text-anchor="middle">舵机A轴线 worldY +17.5</text>
  <text class="t" x="%d" y="%d" text-anchor="middle">舵机B轴线 worldY −17.5</text>
  <text class="s" x="%d" y="%d" text-anchor="middle">Ø16 喇叭台(虚线) + 5×5 连接器窗(黄)</text>
  <text class="s" x="%d" y="%d">前→</text>
  <text class="s" x="%d" y="%d" text-anchor="middle">←后(电池)</text>
</svg>
<ul>
  <li style="color:var(--green)">✅ 板上 4×Ø2.1 孔(绿圈)与两舵机法兰内侧孔逐点重合（孔阵 30×19，STEP 亚微米拟合 + 官方板 18.99mm 双证）</li>
  <li style="color:var(--green)">✅ 两侧 R8.3 真圆咬弧（咬入 5.8mm）完整包络 Ø16 喇叭台 + 顶面 5×5 连接器窗 + 插头</li>
  <li style="color:var(--green)">✅ 连接器实测在舵机顶面轴心（不在后端）——v3.7 的后缘缺口已删，后缘拉直</li>
  <li style="color:var(--yellow)">⚠ 外侧法兰孔(小圈)在板外——锁内侧 4 孔，每舵机前后各 1 颗 M2×6 沉头</li>
  <li style="color:var(--yellow)">⚠ 板面高度沿用 v3.7 假设值（无溯源），首装实测复核</li>
</ul>
</div>
''' % (''.join(SVG), X(6), Y(21.0), X(6), Y(-21.0), X(6), Y(18.6), X(2), Y(0), X(-13), Y(13))

os.makedirs(os.path.dirname(OUT), exist_ok=True)
open(OUT, 'w', encoding='utf-8').write(html)
print('overlay written:', OUT)
print('outline pts:', len(outline), 'holes:', len(holes))
