#!/usr/bin/env python3
"""v3.7 packer: loose passives around the servo-top floorplan.

Same algorithm as v3.6 (courtyard shelf packing + pull-to-anchor), with the
v3.7 zone table: the bottom face only exists over the center-gap strip
(y 7.5..22.5) - everywhere else the board clamps flush on the servo flanges.

Usage:  KiCad-python tools/pack_loose.py [board.kicad_pcb]
"""

import os
import re
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJ, "imu_to_ttl.kicad_pcb")

# ref -> (anchor x, anchor y): the spot the part wants to be, electrically
PACKED = {
    # power front-end (rear column, next to Q1/F1/J1 skeleton)
    "C1": (8.0, 22.0),  "C2": (10.5, 21.5),
    "R9": (27.5, 4.3),
    # MCU decoupling / bus straps near U3
    "C12": (29.5, 21.0), "R3": (28.5, 23.5),
    # IMU rails
    "FB1": (13.2, 11.0),
    # bus network near U3 (below/right of U1)
    "R7": (29.0, 16.5), "R8": (31.0, 16.5), "D3": (31.5, 25.5),
    "R14": (29.0, 19.0), "R15": (31.0, 19.0), "R16": (33.0, 16.5),
    "R5": (14.8, 18.2), "R6": (21.3, 11.5),     # IMU straps, off pad rows
    "R4": (14.8, 15.0),
    # v3.7: LDO island + indicators on the TOP face (rear-right low zone)
    "F2": (5.5, 28.3), "C3": (8.5, 28.3), "C4": (11.5, 28.3),
    "C8": (13.5, 22.5), "LED1": (7.5, 21.5), "R1": (6.0, 19.0),
    "C6": (18.5, 22.5), "C9": (24.0, 12.0), "R11": (19.0, 23.5), "R12": (19.0, 26.3),
    # v3.7 final: R2/TH1 back on TOP near the bus clamp; TPs clear of J2 courtyard
    "R2": (33.8, 21.2), "TH1": (33.0, 28.8), "R13": (13.5, 15.8),
    "TP1": (9.0, 9.3), "TP2": (33.5, 19.0), "TP3": (10.0, 15.5),
}

BOARD_L, BOARD_W = 36.0, 30.0      # x = fore-aft 0..36, y = across 0..30
MARGIN = 0.6

# zones the packer must keep FREE of parts (routing reserves + mechanics)
KEEP_OUT = [
    # U2 IMU escape frame (0.5mm-pitch LGA needs perpendicular escapes)
    (13.4, 12.0, 15.2, 18.0),   # west
    (18.8, 12.0, 20.6, 18.0),   # east: SPI channel to U1
    (15.2, 12.0, 18.8, 13.4),   # north
    (15.2, 16.6, 18.8, 18.0),   # south
    # J3 plug + wire-bend reserve (top face, tall band)
    (20.5, 3.2, 26.0, 8.8),
    # screw head zones (4x M2 countersunk)
    (31.5, 4.0, 33.5, 7.0), (31.5, 23.0, 33.5, 26.0),
    (1.5, 4.0, 3.5, 7.0), (1.5, 23.0, 3.5, 26.0),
    # horn-boss bite arcs on the y=0 / y=30 edges (approximated as rects)
    (17.0, 0.0, 33.0, 3.2),
    (17.0, 26.8, 33.0, 30.0),
]
GAP = 0.3            # routing channel between courtyards
GRID = 0.5
GRID_FINE = 0.1
PULL_STEP = 0.25


def mm(v):
    return pcbnew.FromMM(v)


def to_mm(v):
    return pcbnew.ToMM(v)


def box_of(fp):
    for layer in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
        cy = fp.GetCourtyard(layer)
        if cy.OutlineCount() > 0:
            b = cy.BBox()
            return [to_mm(b.GetX()), to_mm(b.GetY()), to_mm(b.GetRight()), to_mm(b.GetBottom())]
    bb = fp.GetBoundingBox(True, False)
    return [to_mm(bb.GetX()), to_mm(bb.GetY()), to_mm(bb.GetRight()), to_mm(bb.GetBottom())]


def overlaps(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def size_of(box):
    return (box[2] - box[0], box[3] - box[1])


def move_by(fp, dx, dy):
    pos = fp.GetPosition()
    fp.SetPosition(pcbnew.VECTOR2I(pos.x + mm(dx), pos.y + mm(dy)))


def snap_to(fp, box, x, y):
    move_by(fp, x - box[0], y - box[1])


def skeleton_refs():
    """Everything place_board.py pins (FLOORPLAN refs + mounting holes)."""
    text = open(os.path.join(HERE, "place_board.py")).read()
    refs = set(re.findall(r'\("([A-Z]+\d+)",\s*[\d.]+,\s*[\d.]+,\s*\d+,\s*"[FB]"\)', text))
    return refs | {"M1", "M2", "M3", "M4"}


def main():
    board = pcbnew.LoadBoard(PCB)
    fps = {board.GetFootprints()[i].GetReference(): board.GetFootprints()[i]
           for i in range(len(board.GetFootprints()))}

    skeleton = skeleton_refs()
    back = [r for r, fp in fps.items() if fp.GetLayer() == pcbnew.B_Cu]

    # front obstacles: skeleton + holes block on BOTH faces; KEEP_OUT applies
    # to front packing only (the escape frame is a top-face routing reserve)
    boxes = {}
    for ref in skeleton:
        fp = fps.get(ref)
        if fp is None:
            continue
        if ref.startswith("M"):
            cx, cy = fp.GetPosition().x / 1e6, fp.GetPosition().y / 1e6
            boxes[ref] = [cx - 2.0, cy - 2.0, cx + 2.0, cy + 2.0]
        else:
            boxes[ref] = box_of(fp)
    for i, (x0, y0, x1, y1) in enumerate(KEEP_OUT):
        boxes["_keepout%d" % i] = [x0, y0, x1, y1]

    back_boxes = {}
    for ref in back:
        fp = fps.get(ref)
        if fp is None or ref in skeleton:
            continue
        back_boxes[ref] = box_of(fp)

    def free2(ref, box, gap=GAP, bface=False):
        if box[0] < MARGIN or box[1] < MARGIN or box[2] > BOARD_L - MARGIN or box[3] > BOARD_W - MARGIN:
            return False
        grown = (box[0] - gap, box[1] - gap, box[2] + gap, box[3] + gap)
        obs = boxes if not bface else dict(boxes, **back_boxes)
        for oid, ob in obs.items():
            if oid != ref and overlaps(grown, ob):
                return False
        if bface and (box[1] < 7.6 or box[3] > 22.4):
            return False       # bottom face lives only over the center gap
        return True

    parts = [r for r in fps if r not in skeleton and not r.startswith("M")]
    for r in parts:
        if r not in PACKED:
            bx = box_of(fps[r])
            PACKED[r] = ((bx[0] + bx[2]) / 2.0, (bx[1] + bx[3]) / 2.0)
    parts = sorted(parts, key=lambda r: -size_of(box_of(fps[r]))[0] * size_of(box_of(fps[r]))[1])
    placed, unplaced = [], []

    def scan(ref, w, h, step, gap=GAP, bface=False):
        ax, ay = PACKED[ref]
        n = 0
        while n * step <= max(BOARD_L, BOARD_W):
            for i in range(-n, n + 1):
                for jx, jy in ((i, -n), (i, n), (-n, i), (n, i)):
                    x = round((ax + jx * step - w / 2.0) / step) * step
                    y = round((ay + jy * step - h / 2.0) / step) * step
                    if (x < MARGIN or y < MARGIN
                            or x + w > BOARD_L - MARGIN or y + h > BOARD_W - MARGIN):
                        continue
                    if free2(ref, (x, y, x + w, y + h), gap, bface):
                        return (x, y)
            n += 1
        return None

    for ref in parts:
        fp = fps[ref]
        bface = fp.GetLayer() == pcbnew.B_Cu
        if not bface:
            fp.SetLayerAndFlip(pcbnew.F_Cu)
        w, h = size_of(box_of(fp))
        spot = scan(ref, w, h, GRID, GAP, bface) or scan(ref, w, h, GRID_FINE, GAP, bface)
        if spot is None:
            for g in (GAP * 0.5, 0.15, 0.0):
                spot = scan(ref, w, h, GRID_FINE, g, bface)
                if spot is not None:
                    break
        if spot is None:
            unplaced.append(ref)
            continue
        snap_to(fp, box_of(fp), spot[0], spot[1])
        (back_boxes if bface else boxes)[ref] = box_of(fp)
        placed.append(ref)

    # pull-to-anchor
    for _sweep in range(40):
        moved = 0
        for ref in placed:
            fp = fps[ref]
            bface = fp.GetLayer() == pcbnew.B_Cu
            ax, ay = PACKED[ref]
            for _ in range(120):
                box = box_of(fp)
                cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
                dx = 0.0 if abs(ax - cx) < PULL_STEP else (PULL_STEP if ax > cx else -PULL_STEP)
                dy = 0.0 if abs(ay - cy) < PULL_STEP else (PULL_STEP if ay > cy else -PULL_STEP)
                if dx == 0.0 and dy == 0.0:
                    break
                cand = [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy]
                if free2(ref, cand, GAP, bface):
                    move_by(fp, dx, dy)
                    (back_boxes if bface else boxes)[ref] = box_of(fp)
                    moved += 1
                else:
                    if dx:
                        one = [box[0] + dx, box[1], box[2] + dx, box[3]]
                        if free2(ref, one, GAP, bface):
                            move_by(fp, dx, 0)
                            (back_boxes if bface else boxes)[ref] = box_of(fp)
                            moved += 1
                            continue
                    if dy:
                        one = [box[0], box[1] + dy, box[2], box[3] + dy]
                        if free2(ref, one, GAP, bface):
                            move_by(fp, 0, dy)
                            (back_boxes if bface else boxes)[ref] = box_of(fp)
                            moved += 1
                            continue
                    break
        if moved == 0:
            break

    board.Save(PCB)
    print("anchored: %d, packed: %d, unplaced: %s" % (len(skeleton), len(placed), unplaced or "none"))


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
