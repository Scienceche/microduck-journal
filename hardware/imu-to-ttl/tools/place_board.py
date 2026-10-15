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
    # v3 (2026-10-12): 板子装在电池和躯干之间, 接插件全部改卧式弯排针并贴
    # 板边(针尖探出板外, 插头从板外水平插, 线贴边引出, 在板高度约2.5mm).
    # v3.5: J2 贴左边缘(pad 列 x4.6, 针仍向左出板), J1 裸焊盘竖排左缘下半
    # (VBAT_RAW (1.9,23.1) / GND (1.9,26.7)), 两者 courtyard 留 0.5mm。
    ("J1", 1.9, 25.3, 90),       # battery wires solder direct, left edge lower
    ("J2", 4.6, 19.9, 180),      # debug 1x7: pad column x=4.6, y4.7-19.9, pins out LEFT
    ("J3", 19.2, 25.5, 270),      # 左腿 servo 1x3 lying: pins out BOTTOM (J1左移腾位)
    ("J5", 35.7, 10.5, 0),       # leg servo, right edge upper, pins out RIGHT
    ("J4", 35.7, 19.4, 0),       # leg servo, right edge lower (v3.5: +0.9 clears J5's courtyard)
    # Power front-end: the VBAT / VBAT_RAW pours are fixed polygons, so these
    # stay where the pour reaches them.
    ("Q1", 28.84, 7.9, 0),       # reverse-polarity FET, clear of B2's courtyard
    ("F1", 28.3, 11.5, 0),     # 5 A fuse, inside the VBAT_RAW pour
    ("R10", 9.62, 24.4, 0),      # gate divider, clear of B4's courtyard
    ("U2", 20.5, 15.0, 0),       # IMU: at the mounting-hole centre
    ("U1", 28.6, 16.0, 0),       # MCU beside it - the routing channel between
                                 # them stays clear
    # D1 no longer anchored: its old spot belongs to J2's pin field now; the
    # packer places it (VBAT/GND are both pour-connected, spot is free).
]

# test points stay on the bottom side (probed during bring-up, out of the robot);
# the two HAT power pads are DNP options
BACK_ONLY = [
    # test points (probed during bring-up, out of the robot) plus the whole
    # logic rail - the LDO, its caps and the DNP divider options - which does
    # not fit on the front side next to the servo-bus copper
    "TP1", "TP2", "TP3", "TP4", "TP5",
    "F2", "D2", "U4", "C3", "C4", "C5", "C8", "R1", "LED1", "R11", "R12",
    "C6", "C9", "R13", "R2", "TH1", "C14",   # v3: 边缘接插件占正面, 挪底面
]
BACK_POS = {
    # Bottom side. Spacing follows the parts: the LDO is 5.2 mm wide and the
    # 1206 fuse/clamp pair 4.7/4.5 mm, so those need ~5.5 mm pitch.
    "TP1": (12.0, 11.0), "TP2": (12.0, 13.6), "TP3": (12.0, 16.2),
    "TP4": (12.0, 18.8), "TP5": (12.0, 21.4),
    "F2": (16.0, 11.0), "D2": (21.5, 11.0), "C4": (25.5, 11.0),
    "R11": (29.0, 11.0), "R12": (32.5, 11.0),
    "C3": (38.5, 26.5), "C6": (25.5, 6.0), "C8": (29.0, 6.0), "C5": (11.5, 4.0),
    "U4": (16.5, 5.5), "C9": (21.5, 6.0), "R13": (23.5, 28.2),
    "R2": (20.5, 3.8), "TH1": (27.0, 28.2), "C14": (8.5, 21.0),
    "R1": (26.5, 26.0), "LED1": (23.2, 24.6),   # v3.5: LED1 挪开 R1 的外框和间距
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
