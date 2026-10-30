#!/usr/bin/env python3
"""v3.8 packer: loose passives around the servo-top floorplan.

Same algorithm as v3.6 (courtyard shelf packing + pull-to-anchor), with the
v3.8 zone table: 34.5x27 body, bottom face only over the center-gap strip
(y 6..21); corrected r8.3 boss bites remove y<4.3 / y>22.7 between x 17.2..31.8.

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
    # power front-end (west pocket column x10.9, clear of J5/J6/C10/C11)
    "C1": (14.7, 28.0),  "C2": (15.5, 20.5),
    "R9": (10.9, 14.0),
    # MCU decoupling / bus straps, NE rim clear of the y>24.1 bite void
    "C12": (2.5, 9.0), "R3": (25.8, 23.3),
    "FB1": (12.0, 11.0),
    "R7": (11.0, 11.5), "R8": (7.0, 11.4), "D3": (33.3, 27.6),
    "R14": (18.0, 14.5), "R15": (9.2, 11.0), "R16": (31.0, 12.4),
    "R5": (13.5, 21.5), "R6": (9.3, 15.0),
    "R4": (16.8, 11.2),
    "R2": (15.5, 11.5), "TH1": (31.9, 7.1), "R13": (13.0, 15.2),   # clear of the J2 body shadow
    # LDO island + indicators (north-west pocket around U4/C7)
    "F2": (14.7, 28.0), "C3": (12.2, 29.0), "C4": (3.5, 5.5),
    "C8": (14.1, 22.3), "C5": (13.0, 8.5), "LED1": (11.0, 21.0), "R1": (10.0, 21.0),
    "C6": (17.6, 21.6), "C9": (16.8, 19.9), "R11": (13.6, 20.8), "R12": (22.0, 23.0),
    "TP1": (9.5, 12.5), "TP2": (33.5, 12.5), "TP3": (10.0, 14.0),
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
    # J3 plug + wire-bend reserve (top face, mid band)
    (20.2, 5.9, 25.8, 10.4),
    # screw head zones (4x M2 countersunk at (2.5,5.5) (2.5,24.5) (32.5,5.5) (32.5,24.5))
    (31.5, 3.5, 33.5, 7.5), (31.5, 22.5, 33.5, 26.5),
    (1.5, 3.5, 3.5, 7.5), (1.5, 22.5, 3.5, 26.5),
    # horn-boss bites: r8.3 lens approx (center x25; corners stay usable)
    (17.09, 0.0, 20.0, 2.6), (20.0, 0.0, 30.0, 5.8), (30.0, 0.0, 32.91, 2.6),
    (17.09, 27.4, 20.0, 30.0), (20.0, 24.2, 30.0, 30.0), (30.0, 27.4, 32.91, 30.0),
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

    # v3.8 face-aware obstacles (the board is too dense for the old
    # "everything blocks both faces" assumption):
    #  * top-face skeleton blocks top packing; bottom SMD skeleton blocks
    #    bottom packing only (SMD on the far face is invisible)
    #  * through-hole parts (J2/J6 connectors) pierce both faces, but only
    #    their PAD COLUMNS matter on the far face, not the whole courtyard
    #  * mounting holes block both faces
    boxes, back_boxes = {}, {}

    def pad_boxes(fp):
        out = []
        for pad in fp.Pads():
            px, py = pad.GetPosition().x / 1e6, pad.GetPosition().y / 1e6
            rr = max(pad.GetSizeX(), pad.GetSizeY()) / 2e6 + 0.3
            out.append([px - rr, py - rr, px + rr, py + rr])
        return out

    for ref in skeleton:
        fp = fps.get(ref)
        if fp is None:
            continue
        if ref.startswith("M"):
            cx, cy = fp.GetPosition().x / 1e6, fp.GetPosition().y / 1e6
            boxes[ref] = [cx - 2.0, cy - 2.0, cx + 2.0, cy + 2.0]
            continue
        on_back = fp.GetLayer() == pcbnew.B_Cu
        thru = any(pad.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH,
                                          pcbnew.PAD_ATTRIB_NPTH) for pad in fp.Pads())
        if not on_back:
            boxes[ref] = box_of(fp)
            if thru:                      # its pad columns pierce the bottom
                for i, pb in enumerate(pad_boxes(fp)):
                    back_boxes["%s.p%d" % (ref, i)] = pb
        elif thru:
            for i, pb in enumerate(pad_boxes(fp)):
                boxes["%s.p%d" % (ref, i)] = pb
                back_boxes["%s.p%d" % (ref, i)] = pb
        else:
            back_boxes[ref] = box_of(fp)
    for i, (x0, y0, x1, y1) in enumerate(KEEP_OUT):
        boxes["_keepout%d" % i] = [x0, y0, x1, y1]

    def free2(ref, box, gap=GAP, bface=False):
        if box[0] < MARGIN or box[1] < MARGIN or box[2] > BOARD_L - MARGIN or box[3] > BOARD_W - MARGIN:
            return False
        grown = (box[0] - gap, box[1] - gap, box[2] + gap, box[3] + gap)
        obs = boxes if not bface else back_boxes
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
            # v3.8: anchor zone saturated - sweep the other known-free zones
            for zx, zy in ((9.0, 10.0), (4.0, 21.5), (14.5, 27.5), (6.0, 9.5),
                           (23.0, 5.0), (9.0, 20.0), (30.0, 20.0), (17.0, 15.0)):
                saved = PACKED[ref]
                PACKED[ref] = (zx, zy)
                spot = scan(ref, w, h, GRID, GAP, bface) or scan(ref, w, h, GRID_FINE, GAP, bface)
                PACKED[ref] = saved
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
