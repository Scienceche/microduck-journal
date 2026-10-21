#!/usr/bin/env python3
"""v3.7 board frame: servo-top mount in the chest (on servos ID 20 / ID 10).

Coordinate frame (KiCad mm, x right / y down, origin at board corner):
  x 0..36  = fore-aft (world X -19..+17; x0=rear/battery side, x36=front/neck)
  y 0..30  = across   (world Y -15..+15; y0=duck-right, y30=duck-left)
World mapping:  worldX = x - 19,  worldY = 15 - y  (y-down flip).

Mechanics (from dxl_hub measurements + HD1910 STEP + robot_walk.xml):
  * 4x NPTH M2.1 at the servo flange inner holes, 19.0mm apart:
      local (2.5, 5.5) (2.5, 24.5) (32.5, 5.5) (32.5, 24.5)
  * side-edge arc bites clear the two output-shaft horn bosses
    (world (6, +/-17.5) r8; local (25, -2.5) and (25, 32.5) -> edge bites
    to depth 2.5mm between x 17.05..32.95)
  * board plane = servo top flange Z=126.5; front zone x>27 and left-rear
    zone (x<19, y>20) are no-tall (>2.5mm) zones (neck static + sweep);
    bottom-face parts only in the center gap strip y 7.5..22.5.

Usage: KiCad-python tools/gen_board_v37.py
"""
import math
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
OUT = os.path.join(PROJ, "imu_to_ttl.kicad_pcb")

BOARD_L = 36.0          # x
BOARD_W = 30.0          # y
CORNER_R = 1.0

BOSS_CX = 25.0          # local x of both horn-boss centers
BOSS_CY = (-2.5, 32.5)  # local y of right/left boss centers (outside edges)
BOSS_R = 8.3            # boss 8.0 + 0.3 clearance

HOLES = [(2.5, 5.5), (2.5, 24.5), (32.5, 5.5), (32.5, 24.5)]
RELIEF_D = 3.5   # servo connector relief depth (rear edge)

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

    L, W = BOARD_L, BOARD_W
    R = CORNER_R
    K = R * (1.0 - math.sqrt(0.5))
    dx = math.sqrt(BOSS_R ** 2 - 2.5 ** 2)
    bx1, bx2 = BOSS_CX - dx, BOSS_CX + dx          # 17.05 .. 32.95

    # right (front) edge
    add_seg(board, (L, R), (L, W - R))
    add_arc(board, (L, W - R), (L - K, W - K), (L - R, W))   # tr corner
    # top edge (y=30, duck-left) with left-boss bite
    add_seg(board, (L - R, W), (bx2, W))
    add_arc(board, (bx2, W), (BOSS_CX, W - 2.5), (bx1, W))   # bite into y=27.5
    add_seg(board, (bx1, W), (R, W))
    add_arc(board, (R, W), (K, W - K), (0, W - R))           # tl corner
    # rear (x=0) edge with two SERVO CONNECTOR reliefs (detour topology):
    # material x<RELIEF_D is removed at y 22.5..29 and y 1..7.5
    add_seg(board, (0, 29.0), (RELIEF_D, 29.0))       # into left-servo relief (tl arc ends at (0,29))
    add_seg(board, (RELIEF_D, 29.0), (RELIEF_D, 22.5))
    add_seg(board, (RELIEF_D, 22.5), (0, 22.5))        # back to edge
    add_seg(board, (0, 22.5), (0, 7.5))                # gap zone: solid edge
    add_seg(board, (0, 7.5), (RELIEF_D, 7.5))          # into right-servo relief
    add_seg(board, (RELIEF_D, 7.5), (RELIEF_D, 1.0))
    add_seg(board, (RELIEF_D, 1.0), (0, 1.0))
    add_arc(board, (0, R), (K, K), (R, 0))                    # bl corner
    # bottom edge (y=0, duck-right) with right-boss bite + br corner
    add_seg(board, (R, 0), (bx1, 0))
    add_arc(board, (bx1, 0), (BOSS_CX, 2.5), (bx2, 0))        # bite into y=2.5
    add_seg(board, (bx2, 0), (L - R, 0))
    add_arc(board, (L - R, 0), (L - K, K), (L, R))             # br corner

    # mounting holes
    for hx, hy in HOLES:
        fp = pcbnew.FootprintLoad(MOUNT_LIB, MOUNT_FP)
        if fp is None:
            raise SystemExit("cannot load %s" % MOUNT_FP)
        fp.SetPosition(vec(hx, hy))
        fp.SetReference("M%d" % (HOLES.index((hx, hy)) + 1))
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


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
