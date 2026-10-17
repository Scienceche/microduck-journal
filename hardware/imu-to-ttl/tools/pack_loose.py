#!/usr/bin/env python3
"""Place the loose passives around the anchored skeleton.

`place_board.py` fixes the mechanical and electrical anchors (connectors, ICs,
power front-end, bus clamp network, debug header) and parks the bottom-side
test points. This script places everything that is left - the small passives -
using each footprint's real courtyard box, with:

  * shelf packing (rows filled to the full width, tallest parts first) so the
    front side stays routable, and
  * a pull-to-anchor pass that walks every part towards the IC it belongs to
    (decoupling caps to their pin, straps to the IMU, gate network to the FET)
    as far as free space allows.

Both passes only ever move a part into verified-free space, so the result has
no courtyard overlaps by construction; `score_placement` verifies it.

Usage:  python3 tools/pack_loose.py [board.kicad_pcb]
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
    # v3.4: J1 -> left edge (1.9,20.4/24.0) and J3 -> (19.2,25.5).
    # The old table's J1-relative spots (F1/R9/R10/C14 at x4-12) are now
    # stale - those parts were re-anchored in place_board.py or left loose
    # for first-fit, which reads the real courtyard boxes. Only parts whose
    # spot still matches the NEW floorplan stay pinned.
    "C1":  (26.0, 11.5),     # 10 uF bulk on VBAT
    "D1":  (24.5, 13.5),      # SMBJ12A bulk TVS
    "C2":  (25.0, 11.0),      # 100 nF on VBAT
    "R17": (25.0, 9.3),       # v3.5: 0R bridge VBAT <-> VBAT_C2 (schematic netlist part)
    "D3": (33.2, 5.4), "R8": (33.5, 15.5),
    "R7":  (29.5, 18.5), "U3": (31.5, 21.5),
    "FB1": (15.0, 13.2),
    "C12": (33.0, 13.0), "R3": (33.0, 17.0),
    "R5": (18.0, 20.0), "R6": (21.0, 20.0),   # moved off J3's new pad row
    # v2 bus straps (2G241 direction control) - real parts, restored 2026-10-15
    "R14": (28.0, 24.0), "R15": (30.0, 24.0), "R16": (32.0, 24.0),
    "C7": (26.5, 22.5),
    # v3.6: R9 移入 Q1 栅极区(R10 在 place_board 骨架表里同步移动)
    "R4": (12.5, 18.5), "R9": (24.6, 11.6),
}

BOARD_W, BOARD_H = 41.0, 30.0
MARGIN = 0.6

# Routing channels the packer must leave empty. The MCU <-> IMU SPI/interrupt
# bundle has nowhere else to go, and blocking it is what left those nets
# unrouted in the first pass.
KEEP_OUT = [
    # top 3 mm: chassis rib, no components (mechanical drawing)
    (0.0, 0.0, BOARD_W, 3.2),
    # IMU: a square escape frame around the part. The 0.5 mm-pitch LGA pads can
    # only be escaped perpendicular to their row, so the ring must stay clear.
    (17.2, 10.5, 18.8, 19.5),   # west (v3.6: 西墙内收, 给 C10/C11 让位)
    (22.2, 10.5, 24.4, 19.5),   # east: channel to the MCU
    (18.8, 10.5, 22.2, 12.0),   # north
    (18.8, 18.0, 22.2, 19.5),   # south
    # J3's through-hole pad row along the bottom edge (v3)
    (12.0, 24.0, 21.5, 29.4),
    # J2's on-board body strip (v3.5: pad column x4.6, body sits x0.45-3.21,
    # pin tails continue off-board) - nothing may live under the connector
    (0.4, 3.2, 3.4, 21.5),
]
GAP = 0.3            # routing channel reserved between courtyards
GRID = 0.5
GRID_FINE = 0.1      # second pass for parts the coarse scan cannot place
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
    """Everything place_board.py anchors (read from its FLOORPLAN table)."""
    text = open(os.path.join(HERE, "place_board.py")).read()
    return re.findall(r'\("([A-Z]+\d+)",\s*[\d.]+,\s*[\d.]+,\s*\d+\)', text)


def main():
    board = pcbnew.LoadBoard(PCB)
    fps = {board.GetFootprints()[i].GetReference(): board.GetFootprints()[i]
           for i in range(len(board.GetFootprints()))}

    skeleton = set(skeleton_refs()) | {"B1", "B2", "B3", "B4"}
    back = [r for r, fp in fps.items() if fp.GetLayer() == pcbnew.B_Cu]

    # --- obstacles for front-side packing: the anchored parts only. Bottom-side
    # parts sit on the other face and block nothing (their own keep-out is
    # handled by place_board.py's fixed positions).
    boxes = {}
    for ref in skeleton:
        fp = fps.get(ref)
        # Every skeleton ref and hole blocks the packer on BOTH sides - a part
        # packed on top of a back-side anchor is still a real collision.
        if fp is None:
            continue
        if ref in ("B1", "B2", "B3", "B4"):
            # The MountingHole footprint's own bbox is just the 2.2 mm pad, so
            # parts packed right up against it. Reserve the full hole area
            # (screw head + courtyard, dia 5.5) explicitly.
            cx, cy = fp.GetPosition().x / 1e6, fp.GetPosition().y / 1e6
            boxes[ref] = [cx - 2.75, cy - 2.75, cx + 2.75, cy + 2.75]
        else:
            boxes[ref] = box_of(fp)
    for i, (x0, y0, x1, y1) in enumerate(KEEP_OUT):
        boxes["_keepout%d" % i] = [x0, y0, x1, y1]

    def free2(ref, box, gap=GAP):
        if box[0] < MARGIN or box[1] < MARGIN or box[2] > BOARD_W - MARGIN or box[3] > BOARD_H - MARGIN:
            return False
        # Every part needs a routing channel beside it. Packing courtyard
        # against courtyard (the old behaviour, GAP existed but was unused) left
        # the router no room and a few nets came out unrouted every rebuild.
        grown = (box[0] - gap, box[1] - gap, box[2] + gap, box[3] + gap)
        for oid, ob in boxes.items():
            if oid != ref and overlaps(grown, ob):
                return False
        return True

    # --- first fit: biggest parts first, scanning the whole board for the
    # first free cell (a shelf cursor gets stuck behind the big connectors)
    parts = sorted((r for r in PACKED if r not in skeleton), key=lambda r: -size_of(box_of(fps[r]))[0] * size_of(box_of(fps[r]))[1])  # v3: 骨架件不重排
    placed, unplaced = [], []

    def scan(ref, w, h, step, gap=GAP):
        # Walk outwards from the part's preferred anchor in square rings
        # instead of taking the first free cell from the top-left corner. The
        # old scan dropped e.g. the bus series resistor 17 mm away from the
        # connector it belongs to, and no router can close that.
        ax, ay = PACKED[ref]
        n = 0
        while n * step <= max(BOARD_W, BOARD_H):
            for i in range(-n, n + 1):
                for jx, jy in ((i, -n), (i, n), (-n, i), (n, i)):
                    x = round((ax + jx * step - w / 2.0) / step) * step
                    y = round((ay + jy * step - h / 2.0) / step) * step
                    if (x < MARGIN or y < MARGIN
                            or x + w > BOARD_W - MARGIN or y + h > BOARD_H - MARGIN):
                        continue
                    if free2(ref, (x, y, x + w, y + h), gap):
                        return (x, y)
            n += 1
        return None

    for ref in parts:
        fp = fps[ref]
        if fp.GetLayer() != pcbnew.F_Cu:      # never leave a packed part on the back
            fp.SetLayerAndFlip(pcbnew.F_Cu)
        w, h = size_of(box_of(fp))
        # Coarse first pass, then a fine one: a 0.5 mm step walks straight past
        # legal slots once the free space is fragmented, and an unplaced part
        # used to stay at its schematic-centroid spot (on top of a hole).
        spot = scan(ref, w, h, GRID) or scan(ref, w, h, GRID_FINE)
        if spot is None:
            # Board full: give up routing room before giving up on the part.
            # Leaving it unplaced is not an option - it would sit at its
            # schematic-centroid spot, right on top of whatever is there.
            for g in (GAP * 0.5, 0.15, 0.0):
                spot = scan(ref, w, h, GRID_FINE, g)
                if spot is not None:
                    break
        if spot is None:
            unplaced.append(ref)
            continue
        snap_to(fp, box_of(fp), spot[0], spot[1])
        boxes[ref] = box_of(fp)
        placed.append(ref)

    # --- pull-to-anchor: walk each part towards the IC it serves
    for _sweep in range(40):
        moved = 0
        for ref in placed:
            fp = fps[ref]
            ax, ay = PACKED[ref]
            for _ in range(120):
                box = box_of(fp)
                cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
                dx = 0.0 if abs(ax - cx) < PULL_STEP else (PULL_STEP if ax > cx else -PULL_STEP)
                dy = 0.0 if abs(ay - cy) < PULL_STEP else (PULL_STEP if ay > cy else -PULL_STEP)
                if dx == 0.0 and dy == 0.0:
                    break
                cand = [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy]
                if free2(ref, cand):
                    move_by(fp, dx, dy)
                    boxes[ref] = box_of(fp)
                    moved += 1
                else:
                    # try one axis at a time before giving up
                    if dx:
                        one = [box[0] + dx, box[1], box[2] + dx, box[3]]
                        if free2(ref, one):
                            move_by(fp, dx, 0)
                            boxes[ref] = box_of(fp)
                            moved += 1
                            continue
                    if dy:
                        one = [box[0], box[1] + dy, box[2], box[3] + dy]
                        if free2(ref, one):
                            move_by(fp, 0, dy)
                            boxes[ref] = box_of(fp)
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
    os._exit(0)   # _pcbnew segfaults during interpreter teardown; skip it
