#!/usr/bin/env python3
"""v3.8 floorplan: IMU+HUB merged board, servo-top mount in the chest.

Frame (x 0..36 fore-aft, y 0..30 across; x0=rear/battery, x36=front/neck,
y0=duck-right, y30=duck-left; worldX = x-19, worldY = 15-y).

Height zones (neck static + sweep, carried from v3.7 neck_sweep.py):
  * no-tall (>2.5 mm): front x>27 (world X>+8), left-rear (x<19 and
    y>20), edge ring 2mm
  * bottom-face parts ONLY in the center-gap strip y 7.5..22.5 (the board clamps
    flush onto the servo top flanges everywhere else)
  * tall parts (JST verticals ~7mm, horizontal ~4.5mm) only mid band
    x 18.5..26.5 / y 6..21, plus the y0-side edge (no sweep zone there)

v3.8 placement changes (vs v3.7):
  * J4/J5 are SIDE-ENTRY JST-PH (S3B-PH-K-S): J5 right-leg on the y0 edge
    (wires exit toward duck-right), J4 left-leg horizontal mid-band west of
    U1 (wires exit +y over the board to the duck-left side)
  * SW1 power slide (MSK12C02) rear low zone - reachable from battery side
  * D1 TVS moved to (30,12) - v3.7 had it ON the top boss bite (bug), the
    corrected r8.3 bites remove y>22.7 / y<4.3 between x 17.2..31.8
  * all anchors shifted (-0.5,-1.5) for the 34.5x27 body

Usage:  KiCad-python tools/place_board.py
"""

import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJ, "imu_to_ttl.kicad_pcb")

# ref, x, y, rotation, layer -- locked skeleton (KiCad frame, mm)
FLOORPLAN = [
    # ---- branch ports ----
    ("J3", 23.0, 7.6, 0, "F"),      # neck/HAT vertical (up from y6: true bite dips to 5.8)
    ("J4", 24.7, 12.1, 0, "F"),     # left leg SIDE-ENTRY mid band, wires exit +y over the board
    ("J5", 12.9, 6.3, 180, "F"),    # right leg SIDE-ENTRY bottom edge, wires exit -y
    ("J6", 20.3, 15.5, 90, "B"),     # vertical, threading the U2-U1 pad gap      # head/spare vertical BOTTOM over the center gap
    ("J2", 2.0, 18.2, 270, "B"),      # debug 1x7 lying along rear-mid (old x19.6 row is J4 courtyard now)
    # ---- battery + power front end ----
    ("J1", 2.3, 14.2, 90, "F"),     # bare battery pads, straight rear edge
    ("SW1", 8.2, 24.8, 0, "F"),     # v3.8 power slide (Q1 gate leg), rear-north
    ("Q1", 6.8, 3.0, 90, "F"),      # reverse FET, rear-south corner (clear of J5 + hole M1)
    ("F1", 5.0, 12.5, 90, "F"),     # 5A fuse, rear chain
    ("R10", 18.0, 7.5, 90, "F"),    # gate bleed between J5/J3 (was y3.5 - inside the true bite)
    ("D2", 3.2, 9.0, 0, "F"),      # VBAT_LOGIC TVS locked rear-west (packer whiffed here twice)
    # ---- digital core ----
    ("U2", 17.0, 15.0, 0, "F"),     # IMU: board center over the 15mm gap
    ("U1", 25.5, 19.0, 0, "F"),     # MCU east of U2, SPI channel
    ("U3", 31.0, 9.8, 180, "F"),    # 2G241 front band (2mm tall, low zone OK)
    # ---- decouplers locked at their ICs ----
    ("C10", 13.6, 13.4, 0, "F"),    # U2 100nF, west escape frame
    ("C11", 13.6, 16.6, 0, "F"),    # U2 100nF
    ("C13", 21.5, 20.6, 90, "B"),   # U1 100nF BOTTOM (top spots all eaten by J4 courtyard)
    # ---- servo surge bulk ----
    ("C15", 10.0, 16.0, 0, "F"),    # 22uF bulk top, mid-west
    ("D1", 33.2, 18.0, 90, "F"),    # VBAT TVS vertical on the east rim (clear of U1 east pads)
    ("C15", 26.0, 18.5, 0, "B"),    # 22uF bulk BOTTOM, back-to-back with U1
    ("C16", 28.8, 7.0, 0, "B"),     # 22uF bulk BOTTOM
    # ---- LDO island ----
    ("U4", 15.6, 25.3, 0, "F"),     # HT7533 SOT-89 north of SW1 (its v3.7 spot is SW1 now)
    ("C7", 17.0, 22.8, 0, "F"),     # LDO OUT 100nF
]

# bottom-face parts: CENTER-GAP STRIP ONLY (y 6..21)
BACK_ONLY = [
    "TP1", "TP2", "TP3",
    "R13", "C14",
    # v3.8: top face saturated at courtyard level - these SMD parts live on
    # the bottom strip (plenty of room there, see the free-space map)
    "FB1", "R4", "R15", "R6", "C4", "C5", "C6", "C12", "R7", "R2", "R14",
]
BACK_POS = {
    "C5": (9.0, 19.5), "C8": (9.0, 7.0), "F2": (9.0, 17.5),
    "D2": (9.0, 15.0), "C3": (11.0, 7.5), "C4": (11.0, 19.0),
    "R13": (28.0, 12.5), "TH1": (28.0, 16.0), "R2": (20.0, 19.0),
    "C6": (20.0, 7.5), "C9": (24.0, 7.5), "C14": (5.5, 18.5),
    "R1": (28.0, 9.5), "LED1": (28.0, 6.0),
    "R11": (18.0, 7.5), "R12": (18.0, 19.5),
}

DROP = ["TP4", "TP5", "TP6", "TP7", "TP8", "TP9"]  # strip scarce, 3 TPs enough


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


def main():
    board = pcbnew.LoadBoard(PCB)
    fps = {board.GetFootprints()[i].GetReference(): board.GetFootprints()[i]
           for i in range(len(board.GetFootprints()))}

    for ref in DROP:
        fp = fps.get(ref)
        if fp is not None:
            board.Remove(fp)
            del fps[ref]

    # anything not in BACK_ONLY belongs on the top face
    for r, fp in fps.items():
        if r not in BACK_ONLY and r not in DROP and fp.GetLayer() == pcbnew.B_Cu:
            fp.SetLayerAndFlip(pcbnew.F_Cu)
    for ref in BACK_ONLY:
        fp = fps.get(ref)
        if fp is None:
            continue
        if fp.GetLayer() != pcbnew.B_Cu:
            fp.SetLayerAndFlip(pcbnew.B_Cu)
        i = BACK_ONLY.index(ref)
        fp.SetPosition(vec(9.0 + (i % 2) * 12.0, 9.5 + (i // 2) * 2.6))
        fp.SetOrientationDegrees(0)

    missing = []
    for ref, x, y, rot, side in FLOORPLAN:
        fp = fps.get(ref)
        if fp is None:
            missing.append(ref)
            continue
        if side == "B" and fp.GetLayer() != pcbnew.B_Cu:
            fp.SetLayerAndFlip(pcbnew.B_Cu)
        if side == "F" and fp.GetLayer() != pcbnew.F_Cu:
            fp.SetLayerAndFlip(pcbnew.F_Cu)
        fp.SetPosition(vec(x, y))
        fp.SetOrientationDegrees(rot)
        set_locked(fp, True)

    board.Save(PCB)
    print("anchored %d, back %d, missing: %s" % (
        len(FLOORPLAN), len(BACK_ONLY), " ".join(missing) or "none"))


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
