#!/usr/bin/env python3
"""Generate the IMU-to-TTL board frame (mechanical interface).

45 x 22 mm, R2 corners, 2 x M2 non-plated mounting holes - the MicroDuck board
footprint used by the original "banana" IMU carrier:

  * outline    45.0 x 22.0 mm, corner radius 2.0 mm (Edge.Cuts)
  * holes      MH1 and MH2 in board coordinates with the origin at the
               lower-left corner and Y up: (5.5, 3.5) and (39.5, 18.5) ->
               34.0 mm apart in X, 15.0 mm in Y, 37.16 mm diagonal
  * hole size  NPTH 2.2 mm (M2 clearance), non-plated

KiCad's Y axis points down, so those become MH1 (5.5, 18.5), MH2 (39.5, 3.5).

The hole spacing is the one open mechanical question: the official board was
never published, so 34 mm follows the MicroDuck locker boss spacing the
replica project measured. Measure the actual holder before ordering and adjust
MH1/MH2 here if it differs - the corners keep the holes clear of every
connector and of the copper pours, so a small move costs no re-route.

Usage:  python3 tools/gen_board.py [output.kicad_pcb]
"""

import math
import os
import sys

import pcbnew

BOARD_W = 41.0
BOARD_H = 30.0
CORNER_R = 1.0
NPTH_DIA = 2.2

# Mounting holes per the supplied mechanical drawing: 41.00 x 30.00 mm board,
# R1 corners, 8 x Ø2.20 NPTH in two interleaved groups; this build uses the
# B group (21.20 x 25.70 mm bolt pattern, centred on the board -> 9.90 mm from
# the left/right edges and 2.15 mm from the top/bottom edges).
# Coordinates below are board coordinates (origin lower-left, Y up); KiCad's Y
# axis points down, so the mapping flips Y.
HOLES = [
    ("B1", 9.90, 2.15),          # drawing: top-left        (KiCad Y is down)
    ("B2", 31.10, 2.15),         # top-right
    ("B3", 31.10, BOARD_H - 2.15),   # bottom-right
    ("B4", 9.90, BOARD_H - 2.15),    # bottom-left
]

# the drawing reserves the top 3 mm for the chassis rib: no components there
TOP_KEEPOUT = 3.0

import os as _o
MOUNT_LIB = _o.path.join(next(_p for _p in [_o.environ.get("KICAD_FOOTPRINTS", ""),
    "C:/Users/c1349/AppData/Local/Programs/KiCad/10.0.7/share/kicad/footprints", "/usr/share/kicad/footprints"]
    if _p and _o.path.isdir(_p)), "MountingHole.pretty")
MOUNT_FP = "MountingHole_2.2mm_M2"

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "imu_to_ttl.kicad_pcb")

W = BOARD_W
H = BOARD_H
R = CORNER_R
K = R * (1.0 - math.sqrt(0.5))   # 0.586: corner-arc inset from the corner

EDGES = [
    ((R, 0.0), (W - R, 0.0)),        # bottom
    ((W, R), (W, H - R)),            # right
    ((W - R, H), (R, H)),            # top
    ((0.0, H - R), (0.0, R)),        # left
]

ARCS = [  # (start, mid, end), one 90 degree fillet per corner
    ((0.0, R), (K, K), (R, 0.0)),                    # lower-left
    ((W - R, 0.0), (W - K, K), (W, R)),              # lower-right
    ((W, H - R), (W - K, H - K), (W - R, H)),        # upper-right
    ((R, H), (K, H - K), (0.0, H - R)),              # upper-left
]


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

    for a, b in EDGES:
        add_seg(board, a, b)
    for start, mid, end in ARCS:
        add_arc(board, start, mid, end)

    for ref, x, y in HOLES:
        fp = pcbnew.FootprintLoad(MOUNT_LIB, MOUNT_FP)
        if fp is None:
            raise SystemExit("cannot load %s:%s" % (MOUNT_LIB, MOUNT_FP))
        fp.SetPosition(vec(x, y))
        fp.SetReference(ref)
        fp.SetValue("M2")
        fp.Reference().SetVisible(False)
        fp.Value().SetVisible(False)
        board.Add(fp)

    out = os.path.abspath(OUT)
    board.Save(out)

    # verify what we just wrote (index-based: KiCad 10's SWIG iterators break on Python 3.14)
    check = pcbnew.LoadBoard(out)
    bbox = check.GetBoardEdgesBoundingBox()
    fps = []
    for i in range(len(check.GetFootprints())):
        f = check.GetFootprints()[i]
        fps.append((f.GetReference(), f.GetPosition(), f.GetValue()))
    print("wrote %s" % out)
    print("bbox: %.3f x %.3f mm (includes the 0.1 mm stroke)" % (
        pcbnew.ToMM(bbox.GetWidth()), pcbnew.ToMM(bbox.GetHeight())))
    for ref, pos, val in fps:
        print("  %s at (%.2f, %.2f) %s" % (ref, pcbnew.ToMM(pos.x), pcbnew.ToMM(pos.y), val))
    if abs(pcbnew.ToMM(bbox.GetWidth()) - (BOARD_W + 0.1)) > 0.05 or \
       abs(pcbnew.ToMM(bbox.GetHeight()) - (BOARD_H + 0.1)) > 0.05:
        raise SystemExit("outline size mismatch")
    if len(fps) != len(HOLES):
        raise SystemExit("mounting hole count mismatch")


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)   # _pcbnew segfaults during interpreter teardown; skip it
