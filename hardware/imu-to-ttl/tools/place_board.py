#!/usr/bin/env python3
"""v3.7 floorplan: IMU+HUB merged board, servo-top mount in the chest.

Frame (x 0..36 fore-aft, y 0..30 across; x0=rear/battery, x36=front/neck,
y0=duck-right, y30=duck-left; worldX = x-19, worldY = 15-y).

Height zones (neck static + sweep + battery, see gen_board_v37.py header):
  * no-tall (>2.5 mm): front x>27, left-rear (x<19 and y>20), edge ring 2mm
  * bottom-face parts ONLY in the center-gap strip y 7.5..22.5 (the board
    clamps flush onto the servo top flanges everywhere else)
  * JST-PH verticals (top face J3 / bottom face J4-J6) are the only tall parts

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
    # ---- branch ports (JST-PH vertical, direct-plug for HD-1910) ----
    ("J3", 23.0, 6.0, 0, "F"),      # neck/HAT, TOP face, tall band x19-27
    ("J4", 19.0, 9.0, 0, "B"),      # left leg BOTTOM (clear of Q1 pads + U2 frame)
    ("J5", 14.0, 20.5, 0, "B"),     # right leg, BOTTOM over center gap
    ("J6", 7.5, 18.5, 0, "B"),      # head/spare, BOTTOM (clear of J1 pads)
    ("J2", 19.6, 13.8, 270, "B"),  # TH column x19.3-34.5 mid-strip (only TH-free lane)    # horizontal along the bottom strip, plug drops through gap      # debug 1x7 VERTICAL bottom, clear of U1 east pads
    # ---- battery + power front end (rear edge, all low parts) ----
    ("J1", 2.3, 16.0, 90, "F"),   # rear center (reliefs at y<7.5/y>22.5)     # bare pads, rear edge (pin1 VBAT_RAW y-)
    ("Q1", 10.5, 6.5, 0, "F"),      # reverse FET, rear-north clear of F1/J4
    ("F1", 5.0, 12.5, 90, "F"),      # 5A fuse, horizontal rear chain J1->F1->Q1
    ("R10", 17.0, 3.5, 0, "F"),     # gate bleed by Q1 (v3.6 lesson)
    # ---- digital core ----
    ("U2", 17.0, 15.0, 0, "F"),     # IMU: board center over the 15mm gap
    ("U1", 25.5, 19.0, 0, "F"),     # MCU east of U2, SPI channel x18.7-22.3
    ("U3", 31.0, 9.8, 180, "F"),    # v3.7-B: 2G241 re-homed to front band (2mm tall, low zone OK)
    # ---- decouplers locked at their ICs (v3.6 audit) ----
    ("C10", 13.6, 13.4, 0, "F"),    # U2 100nF, west escape frame
    ("C11", 13.6, 16.6, 0, "F"),    # U2 100nF
    ("C13", 22.5, 10.8, 90, "F"),   # U1 100nF, west of U1 pad column
    # ---- servo surge bulk caps (tall band, hug the port cluster) ----
    ("C15", 10.0, 16.0, 0, "F"),    # 22uF bulk, mid-west (VBAT pour covers)    # 22uF 16V 1210
    # ---- LDO island (top face now; bottom strip is reserved) ----
    ("U4", 10.5, 25.5, 0, "F"),     # HT7533 SOT-89 (left-rear low zone)
    ("C7", 16.5, 23.5, 0, "F"),     # LDO OUT 100nF (v3.6: 1.4mm)
    ("D1", 24.5, 27.6, 0, "F"),   # clear of U1 east pads (x<=28.4-0.99)    # VBAT TVS (old strap strip, verified clear)
    ("C15", 26.0, 18.5, 0, "B"),    # 22uF bulk BOTTOM (back-to-back with TH1/U1 SMD)
    ("C16", 28.0, 8.5, 0, "B"),     # 22uF bulk BOTTOM
]

# bottom-face parts: CENTER-GAP STRIP ONLY (y 7.5..22.5) - everything else
# on the bottom would be crushed against the servo top flanges
BACK_ONLY = [
    # v3.7: bottom strip is scarce - only the probe row and four small parts
    # stay on the back; the LDO island and indicators moved to the top face
    "TP1", "TP2", "TP3",
    "R13", "C14",
]
BACK_POS = {
    # TP row along the front of the strip
    # LDO island passives west of the JST cluster
    "C5": (9.5, 21.0), "C8": (9.5, 8.5), "F2": (9.5, 19.0),
    "D2": (9.5, 16.5), "C3": (11.5, 9.0), "C4": (11.5, 20.5),
    # bus + misc
    "R13": (28.5, 14.0), "TH1": (28.5, 17.5), "R2": (20.5, 20.5),
    "C6": (20.5, 9.0), "C9": (24.5, 9.0), "C14": (6.0, 20.0),
    "R1": (28.5, 11.0), "LED1": (28.5, 8.0),
    "R11": (18.5, 9.0), "R12": (18.5, 21.0),
}

DROP = ["TP4", "TP5", "TP6", "TP7", "TP8", "TP9"]  # v3.7: strip scarce, 3 TPs enough


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

    # anything not in BACK_ONLY belongs on the top face (a previous run may
    # have left it on the back - the strip is reserved for the list below)
    for r, fp in fps.items():
        if r not in BACK_ONLY and r not in DROP and fp.GetLayer() == pcbnew.B_Cu:
            fp.SetLayerAndFlip(pcbnew.F_Cu)
    for ref in BACK_ONLY:
        fp = fps.get(ref)
        if fp is None:
            continue
        if fp.GetLayer() != pcbnew.B_Cu:
            fp.SetLayerAndFlip(pcbnew.B_Cu)
        # rough start position inside the strip; pack_loose relocates legally
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
