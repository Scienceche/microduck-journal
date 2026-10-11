#!/usr/bin/env python3
"""Apply the board floorplan: the anchored skeleton plus bottom-side test points.

Anchored parts are the mechanical and electrical fixtures - connectors on the
board edges, the IMU between the mounting holes, the MCU beside it, the power
front-end in the left column, the bus buffer and clamp network in the right
column, and the debug header along the bottom edge. The small passives are
placed afterwards by `pack_loose.py`, which reads the FLOORPLAN table below to
know what must not move.

Coordinates are KiCad millimetres in the file's Y-down frame, origin at the
board's upper-left corner; the board is 41 x 30 mm with the B-group mounting
holes at (9.90, 2.15), (31.10, 2.15), (9.90, 27.85), (31.10, 27.85) - keep the
floorplan clear of those four spots, they are drilled through the finished
board.

Usage:  python3 tools/place_board.py [board.kicad_pcb]
"""

import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJ, "imu_to_ttl.kicad_pcb")

# ref, x, y, rotation (deg) -- every one of these parts is locked in place
FLOORPLAN = [
    # Only true mechanical anchors live here; everything else is packed around
    # them by pack_loose.py using its real courtyard boxes.
    ("J1", 4.6, 15.0, 180),      # battery input, left edge, wires exit left
    ("J3", 13.5, 8.7, 180),      # servo bus: neck - high enough that the B1
                                 # hole courtyard (y <= 4.65) clears its own
    ("J4", 22.5, 8.7, 180),      # servo bus: left leg, same row as J3
    ("J5", 38.2, 15.0, 270),     # servo bus: right leg, right edge
    # Power front-end is anchored too: the VBAT / VBAT_RAW pours are fixed
    # polygons, so these parts have to sit where the pour reaches them. Left to
    # the packer they drift, and the battery net comes out unconnected.
    ("Q1", 28.84, 7.9, 0),       # reverse-polarity FET, clear of B2's courtyard
    ("F1", 33.67, 10.78, 0),     # 5 A fuse, inside the VBAT_RAW pour
    # D1 (SMBJ12A) used to sit at its schematic-com position, which my J3
    # move put it on top of. Anchor it clear of U3 and J3; it only carries
    # VBAT/GND (both pour-connected), so the spot is routing-free.
    ("D1", 10.5, 14.5, 0),
    ("R10", 9.62, 24.4, 0),      # gate divider, clear of B4's courtyard
    ("U2", 20.5, 15.0, 0),       # IMU: at the mounting-hole centre
    ("U1", 28.6, 15.5, 0),       # MCU beside it - the pair moved together so
                                 # the routing channel between them stays clear
    ("J2", 16.0, 23.0, 90),      # debug header, one row up: the bottom row
                                 # now belongs to the B3/B4 mounting holes
]

# test points stay on the bottom side (probed during bring-up, out of the robot);
# the two HAT power pads are DNP options
BACK_ONLY = [
    # test points (probed during bring-up, out of the robot) plus the whole
    # logic rail - the LDO, its caps and the DNP divider options - which does
    # not fit on the front side next to the servo-bus copper
    "TP1", "TP2", "TP3", "TP4", "TP5",
    "F2", "D2", "U4", "C3", "C4", "C5", "C8", "R1", "LED1", "R11", "R12",
]
BACK_POS = {
    # Bottom side. Spacing follows the parts: the LDO is 5.2 mm wide and the
    # 1206 fuse/clamp pair 4.7/4.5 mm, so those need ~5.5 mm pitch.
    "TP1": (12.0, 11.0), "TP2": (12.0, 13.6), "TP3": (12.0, 16.2),
    "TP4": (12.0, 18.8), "TP5": (12.0, 21.4),
    "F2": (16.0, 11.0), "D2": (21.5, 11.0), "C4": (25.5, 11.0),
    "R11": (29.0, 11.0), "R12": (32.5, 11.0),
    "C3": (36.0, 25.5), "C6": (25.5, 6.0), "C8": (29.0, 6.0), "C5": (32.5, 6.0),
    "U4": (37.0, 9.5),
    "R1": (36.5, 21.0), "LED1": (33.0, 21.0),
}


def mm(v):
    return pcbnew.FromMM(v)


def vec(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def set_locked(fp, locked):
    for method in ("SetLocked", "SetLockedFlag"):
        fn = getattr(fp, method, None)
        if fn is not None:
            fn(locked)
            return True
    return False


DROP = ["TP6", "TP7", "TP8", "TP9"]   # dropped: no room / redundant with TP1-TP5


def main():
    board = pcbnew.LoadBoard(PCB)
    fps = {board.GetFootprints()[i].GetReference(): board.GetFootprints()[i]
           for i in range(len(board.GetFootprints()))}

    for ref in DROP:
        fp = fps.get(ref)
        if fp is not None:
            board.Remove(fp)
            del fps[ref]

    for ref in BACK_ONLY:
        fp = fps.get(ref)
        if fp is None:
            continue
        if fp.GetLayer() != pcbnew.B_Cu:
            fp.SetLayerAndFlip(pcbnew.B_Cu)
        fp.SetPosition(vec(BACK_POS[ref][0], BACK_POS[ref][1]))
        fp.SetOrientationDegrees(0)

    missing = []
    for ref, x, y, rot in FLOORPLAN:
        fp = fps.get(ref)
        if fp is None:
            missing.append(ref)
            continue
        fp.SetPosition(vec(x, y))
        fp.SetOrientationDegrees(rot)
        set_locked(fp, True)

    # Silkscreen is handled by tools/silk_refs.py, which runs after packing:
    # it resizes the references, hides the values and moves every label to a
    # spot that clears pads, courtyards and the board edge. (The earlier note
    # here - that the silk warnings were cosmetic - was wrong: silk over pads
    # and overlapping labels are a real assembly annoyance.)
    board.Save(PCB)
    print("anchored %d parts, parked %d on the bottom side"
          % (len(FLOORPLAN), len(BACK_ONLY)))
    if missing:
        print("missing refs: %s" % " ".join(missing))


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)   # _pcbnew segfaults during interpreter teardown; skip it
