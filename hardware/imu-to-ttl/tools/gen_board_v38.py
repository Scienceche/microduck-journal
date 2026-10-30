#!/usr/bin/env python3
"""v3.8 board frame: servo-top mount in the chest (on servos ID 20 / ID 10).

Changes vs v3.7 (2026-10-30, servo_registration_analysis.json re-derivation):
  * rear edge STRAIGHT (the two 3.5mm connector reliefs are DELETED: the
    HD1910 connector is a 5x5 window in the CENTER of the top boss face - it
    opens on the joint axis, not at the servo fore/aft end; the r8.3 boss
    bite already clears connector + mated plug, ~8mm square)
  * body stays 36x30 (a 34.5x27 cut was tried and cannot host 62 parts once
    the side-entry J4/J5 courtyards moved to the top face - +118 mm2)
  * boss bites corrected: v3.7's 3-point fit produced a shallow R~13.8 arc
    (dip 2.5mm) that did NOT clear the r8.0 boss reaching 5.5mm into the board
    (why D1 pads landed on the bite). v3.8 bites are true r8.3 arcs about the
    boss centers, dipping 4.3mm - endpoints x = 24.5 +/- sqrt(8.3^2 - 4^2).
  * corner radius 2.0 (was 1.0)

Coordinate frame (KiCad mm, x right / y down, origin at board corner):
  x 0..36 = fore-aft (world X -19..+17; x0=rear/battery, x36=front/neck)
  y 0..30 = across  (world Y -15..+15; y0=duck-right, y30=duck-left)
World mapping:  worldX = x - 19,  worldY = 15 - y.

Mechanics (STEP-verified, servo_registration_analysis.json):
  * servo axes world (6, +/-17.5); flange inner holes world
    (-16.5,+/-9.5) & (13.5,+/-9.5) -> local (2.5,5.5) (2.5,24.5) (32.5,5.5)
    (32.5,24.5)  (spacing 30.0 x 19.0; sub-um fit + dxl_hub 18.99mm x-check)
  * horn bosses r8.0 at world (6,+/-17.5) -> local (25,-2.5) & (25,32.5);
    TRUE r8.3 bite arcs dip to y=5.8 / y=24.2
  * HD1910 top connector: 5x5 window centered on the axis inside the boss
    face (vendor solid 13) - cleared by the bites; mated plug ~8x8 also clear
  * board plane = servo top flange Z (v3.7 legacy value 126.5); no-tall zones
    (neck static+sweep) carried over: front world X>+8 -> x>26.5, and
    left-rear (x<18.5 & y>18.5); bottom-face parts only center gap y 6..21.

Usage: KiCad-python tools/gen_board_v38.py
"""
import math
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
OUT = os.path.join(PROJ, "imu_to_ttl.kicad_pcb")

BOARD_L = 36.0         # x
BOARD_W = 30.0         # y
CORNER_R = 2.0

BOSS_CX = 25.0         # local x of both boss centers (world 6)
BOSS_CY = (-2.5, 32.5) # local y of boss centers (outside top/bottom edges)
BOSS_R = 8.3           # boss r8.0 + 0.3 clearance
EDGE_D = 2.5           # boss-center distance from the board edge it bites

HOLES = [(2.5, 5.5), (2.5, 24.5), (32.5, 5.5), (32.5, 24.5)]

MOUNT_LIB = os.path.join(PROJ, "imu_to_ttl.pretty")
MOUNT_FP = "MountingHole_2.1mm_M2"


def mm(v):
    return pcbnew.FromMM(v)


def vec(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def add_seg(board, a, b):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_SEGMENT)
    s.SetStart(vec(*a))
    s.SetEnd(vec(*b))
    s.SetLayer(pcbnew.Edge_Cuts)
    s.SetWidth(mm(0.1))
    board.Add(s)


def add_arc(board, start, mid, end):
    s = pcbnew.PCB_SHAPE(board)
    s.SetShape(pcbnew.SHAPE_T_ARC)
    s.SetLayer(pcbnew.Edge_Cuts)
    s.SetWidth(mm(0.1))
    s.SetArcGeometry(vec(*start), vec(*mid), vec(*end))
    board.Add(s)


def main():
    board = pcbnew.CreateEmptyBoard()
    board.SetFileName(os.path.abspath(OUT))

    L, W, R = BOARD_L, BOARD_W, CORNER_R
    K = R * (1.0 - math.sqrt(0.5))
    dx = math.sqrt(BOSS_R ** 2 - EDGE_D ** 2)      # 7.2786
    bx1, bx2 = BOSS_CX - dx, BOSS_CX + dx          # 17.2214 .. 31.7786
    dip_b = BOSS_CY[0] + BOSS_R                    # 4.3  (bottom bite depth)
    dip_t = BOSS_CY[1] - BOSS_R                    # 22.7 (top bite)

    # front (x=L) edge + top-right corner
    add_seg(board, (L, R), (L, W - R))
    add_arc(board, (L, W - R), (L - K, W - K), (L - R, W))
    # top edge (y=W, duck-left) with true-r8.3 boss bite
    add_seg(board, (L - R, W), (bx2, W))
    add_arc(board, (bx2, W), (BOSS_CX, dip_t), (bx1, W))
    add_seg(board, (bx1, W), (R, W))
    add_arc(board, (R, W), (K, W - K), (0, W - R))
    # rear (x=0) edge - straight, no reliefs (connector is on the boss axis)
    add_seg(board, (0, W - R), (0, R))
    add_arc(board, (0, R), (K, K), (R, 0))
    # bottom edge (y=0, duck-right) with boss bite + bottom-right corner
    add_seg(board, (R, 0), (bx1, 0))
    add_arc(board, (bx1, 0), (BOSS_CX, dip_b), (bx2, 0))
    add_seg(board, (bx2, 0), (L - R, 0))
    add_arc(board, (L - R, 0), (L - K, K), (L, R))

    # mounting holes
    for i, (hx, hy) in enumerate(HOLES):
        fp = pcbnew.FootprintLoad(MOUNT_LIB, MOUNT_FP)
        if fp is None:
            raise SystemExit("cannot load %s" % MOUNT_FP)
        fp.SetPosition(vec(hx, hy))
        fp.SetReference("M%d" % (i + 1))
        fp.SetValue("M2")
        fp.Reference().SetVisible(False)
        fp.Value().SetVisible(False)
        board.Add(fp)

    board.Save(os.path.abspath(OUT))

    # verify
    chk = pcbnew.LoadBoard(os.path.abspath(OUT))
    bbox = chk.GetBoardEdgesBoundingBox()
    print("wrote %s" % OUT)
    print("bbox %.2f x %.2f mm (expect ~%.1f x %.1f incl stroke)" % (
        pcbnew.ToMM(bbox.GetWidth()), pcbnew.ToMM(bbox.GetHeight()), L + 0.1, W + 0.1))
    n = len(chk.GetFootprints())
    print("holes placed: %d (expect 4)" % n)
    if n != 4:
        raise SystemExit("hole count mismatch")
    # hole-to-boss clearance (need r8.3 + hole r1.05 + 0.25 copper)
    for hx, hy in HOLES:
        for cy in BOSS_CY:
            d = math.hypot(hx - BOSS_CX, hy - cy)
            ok = d >= BOSS_R + 1.05 + 0.25
            print("hole (%.1f,%.1f) vs boss y=%.1f: %.2fmm %s" % (hx, hy, cy, d, "OK" if ok else "CLASH"))
            if not ok:
                raise SystemExit("hole/boss clash")
    # bite points must sit exactly on the boss-clearance circles
    for (ax, ay), cy in (((BOSS_CX, dip_b), BOSS_CY[0]), ((BOSS_CX, dip_t), BOSS_CY[1])):
        d = math.hypot(ax - BOSS_CX, ay - cy)
        assert abs(d - BOSS_R) < 1e-6, "bite dip not on clearance circle"
    print("bite dips %.1f/%.1f on r8.3 circles OK" % (dip_b, dip_t))


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
