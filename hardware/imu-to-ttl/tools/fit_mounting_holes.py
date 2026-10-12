#!/usr/bin/env python3
"""Fit the four mounting holes into the routed board.

The board's layout predates the hole requirement, and four parts sit inside the
4.99 mm hole courtyards: J2 (its pin-7 pad was 0.14 mm from B3 - the drill would
have cut the pad), J3 and Q1 (an M2 head reaches y=4.05, their bodies start at
y~3.3) and R10. This moves them to the nearest position that clears:

  * the board edges and the 3 mm component-free band along the top,
  * the four hole courtyards,
  * every other footprint's courtyard,
  * and every piece of foreign copper, by exact geometry (segment-segment
    distance for tracks, rectangle distance for pads, circle for vias) - the
    earlier attempt used axis-aligned boxes and put parts on top of tracks,
    which shorted 49 nets.

Their own-net tracks then dangle, clean_tracks.py prunes them and
patch_routes.py re-routes the stubs; the pours are re-filled afterwards.

Usage:  python3 tools/fit_mounting_holes.py [board.kicad_pcb]
"""

import math
import os
import re
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJ, "imu_to_ttl.kicad_pcb")

HOLES = [("B1", 9.90, 2.15), ("B2", 31.10, 2.15),
         ("B3", 31.10, 27.85), ("B4", 9.90, 27.85)]
import os as _o
MOUNT_LIB = _o.path.join(next(_p for _p in [_o.environ.get("KICAD_FOOTPRINTS", ""),
    "C:/Users/c1349/AppData/Local/Programs/KiCad/10.0.7/share/kicad/footprints", "/usr/share/kicad/footprints"]
    if _p and _o.path.isdir(_p)), "MountingHole.pretty")
MOUNT_FP = "MountingHole_2.2mm_M2"

# parts that sit in a hole courtyard (J4 is clear and stays put) and how far to
# look for a new home, in 0.25 mm steps
MOVES = {"J2": (16.0, 27.8), "J3": (13.5, 6.4), "Q1": (28.84, 6.34), "R10": (9.62, 24.88)}
SEARCH_R = 14.0
STEP = 0.25
EDGE = 0.30
TOP_BAND = 3.05
COPPER_MARGIN = 0.22


def V(x, y):
    return pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))


def box_of(item):
    b = item.GetBoundingBox()
    return (b.GetX() / 1e6, b.GetY() / 1e6, b.GetRight() / 1e6, b.GetBottom() / 1e6)


def courtyard(fp):
    for layer in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
        ps = fp.GetCourtyard(layer)
        if ps.OutlineCount():
            b = ps.BBox()          # SHAPE_POLY_SET, not a BOARD_ITEM
            return (b.GetX() / 1e6, b.GetY() / 1e6, b.GetRight() / 1e6, b.GetBottom() / 1e6)
    return None


def rects_overlap(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def rect_gap(a, b):
    return math.hypot(max(b[0] - a[2], a[0] - b[2], 0.0),
                      max(b[1] - a[3], a[1] - b[3], 0.0))


def pt_seg(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def seg_seg(a, b, c, d):
    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    if (cross(a, b, c) * cross(a, b, d) < 0) and (cross(c, d, a) * cross(c, d, b) < 0):
        return 0.0
    return min(pt_seg(*c, *a, *b), pt_seg(*d, *a, *b), pt_seg(*a, *c, *d), pt_seg(*b, *c, *d))


def rect_seg(r, c, d):
    x0, y0, x1, y1 = r
    return min(seg_seg((x0, y0), (x1, y0), c, d), seg_seg((x1, y0), (x1, y1), c, d),
               seg_seg((x1, y1), (x0, y1), c, d), seg_seg((x0, y1), (x0, y0), c, d))


def main():
    board = pcbnew.LoadBoard(PCB)

    # --- 1. the four holes
    have = {str(board.GetFootprints()[i].GetReference()) for i in range(len(board.GetFootprints()))}
    added = 0
    for ref, x, y in HOLES:
        if ref in have:
            continue
        fp = pcbnew.FootprintLoad(MOUNT_LIB, MOUNT_FP)
        if fp is None:
            sys.exit("cannot load %s" % MOUNT_FP)
        fp.SetPosition(V(x, y))
        fp.SetReference(ref)
        fp.SetValue("M2")
        fp.Reference().SetVisible(False)
        fp.Value().SetVisible(False)
        board.Add(fp)
        added += 1
    print("mounting holes: %d added" % added)

    # --- 2. obstacle sets (exact geometry; zone fills are not obstacles, they
    #        recede on the next refill)
    text = open(PCB, encoding="utf-8").read()
    track_segs = []
    for m in re.finditer(r"\(segment\s+\(start ([\d.-]+) ([\d.-]+)\)\s+\(end ([\d.-]+) ([\d.-]+)\)"
                         r"\s+\(width ([\d.-]+)\)\s+\(layer \"[^\"]*\"\)\s+\(net \"([^\"]*)\"\)", text):
        x1, y1, x2, y2, w, net = m.groups()
        track_segs.append((net, float(x1), float(y1), float(x2), float(y2), float(w) / 2))
    via_list = []
    for m in re.finditer(r"\(via\s+\(at ([\d.-]+) ([\d.-]+)\)\s+\(size ([\d.-]+)\)"
                         r"[\s\S]{0,140}?\(net \"([^\"]*)\"\)", text):
        x, y, s, net = m.groups()
        via_list.append((net, float(x), float(y), float(s) / 2))
    all_pads = []
    for i in range(len(board.GetFootprints())):
        for pad in board.GetFootprints()[i].Pads():
            all_pads.append((pad.GetNetname(),) + box_of(pad))
    print("obstacles: %d track segments, %d vias, %d pads" % (len(track_segs), len(via_list), len(all_pads)))

    hole_boxes = []
    for ref, x, y in HOLES:
        for i in range(len(board.GetFootprints())):
            fp = board.GetFootprints()[i]
            if str(fp.GetReference()) == ref:
                box = courtyard(fp)
                if box:
                    hole_boxes.append(box)

    refs_of = {i: str(board.GetFootprints()[i].GetReference()) for i in range(len(board.GetFootprints()))}

    def position_ok(fp, ref):
        own = {p.GetNetname() for p in fp.Pads()}
        cb = courtyard(fp)
        if cb is None:
            return False, "no courtyard"
        if (cb[0] < EDGE or cb[1] < EDGE or cb[2] > 41.0 - EDGE or cb[3] > 30.0 - EDGE):
            return False, "off board"
        if cb[1] < TOP_BAND:
            return False, "in top band"
        for hb in hole_boxes:
            if rects_overlap(cb, hb):
                return False, "hole courtyard"
        for i in range(len(board.GetFootprints())):
            other = refs_of[i]
            if other == ref:
                continue
            ob = courtyard(board.GetFootprints()[i])
            if ob and rects_overlap(cb, ob):
                return False, "courtyard of %s" % other
        for pad in fp.Pads():
            pb = box_of(pad)
            for net, x1, y1, x2, y2, hw in track_segs:
                if net in own:
                    continue
                if rect_seg(pb, (x1, y1), (x2, y2)) < hw + COPPER_MARGIN:
                    return False, "track %s" % net
            for net, vx, vy, r in via_list:
                if net in own:
                    continue
                if rect_gap(pb, (vx - r, vy - r, vx + r, vy + r)) < COPPER_MARGIN:
                    return False, "via %s" % net
            for net, x0, y0, x1, y1 in all_pads:
                if net in own or net == "":
                    continue
                if rect_gap(pb, (x0, y0, x1, y1)) < COPPER_MARGIN:
                    return False, "pad %s" % net
        return True, ""

    # --- 3. search, nearest candidate first
    cands = []
    n = int(SEARCH_R / STEP)
    for dx in range(-n, n + 1):
        for dy in range(-n, n + 1):
            cands.append((dx * STEP, dy * STEP))
    cands.sort(key=lambda o: o[0] * o[0] + o[1] * o[1])

    for ref, (bx, by) in MOVES.items():
        fp = board.FindFootprintByReference(ref)
        if fp is None:
            print("  %s not on the board" % ref)
            continue
        got = None
        last = ""
        for dx, dy in cands:
            fp.SetPosition(V(bx + dx, by + dy))
            ok, why = position_ok(fp, ref)
            if ok:
                got = (bx + dx, by + dy)
                break
            last = why
        if got:
            fp.SetPosition(V(*got))
            print("  %-4s (%.2f,%.2f) -> (%.2f,%.2f)   move %.2f mm" %
                  (ref, bx, by, got[0], got[1], math.hypot(got[0] - bx, got[1] - by)))
        else:
            fp.SetPosition(V(bx, by))
            print("  %-4s no free spot (last: %s)" % (ref, last))

    board.Save(PCB)
    print("saved %s" % PCB)


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
