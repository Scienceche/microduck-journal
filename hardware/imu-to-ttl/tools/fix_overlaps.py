#!/usr/bin/env python3
"""Separate courtyard overlaps with minimum-translation pushes.

The packer gets placement ~95% right; the last few pairs are millimetre-scale
collisions between parts whose courtyards are polygons rather than the boxes
the packer uses. For every overlapping pair this moves the side that is not
part of the skeleton (or the smaller of the two) by the minimum translation
that separates them, then repeats until the board is clean.

Usage:  python3 tools/fix_overlaps.py [board.kicad_pcb]
"""

import os
import re
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJ, "imu_to_ttl.kicad_pcb")


def fixed_refs():
    """Everything place_board.py pins: FLOORPLAN anchors, the back-side park
    list (BACK_POS dict keys) and the four mounting holes. A previous version
    had a hardcoded {J1..J5,U1,U2} set, so Q1/F1/R10 and every BACK_POS part
    could be shoved off their anchors (U1 once landed on mounting hole B2)."""
    text = open(os.path.join(HERE, "place_board.py")).read()
    refs = set(re.findall(r'\("([A-Z]+\d+)",\s*[\d.]+,\s*[\d.]+,\s*\d+,\s*"[FB]"\)', text))
    refs |= set(re.findall(r'"([A-Z]+\d+)":\s*\(', text))
    back = re.search(r'BACK_ONLY = \[([\s\S]*?)\]', text)
    if back:
        refs |= set(re.findall(r'"([A-Z]+\d+)"', back.group(1)))
    return refs | {"M1", "M2", "M3", "M4"}


SKELETON = fixed_refs()
MARGIN = 0.6
PUSH = 0.25      # extra separation beyond the touching point


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


def overlap_area(a, b):
    dx = min(a[2], b[2]) - max(a[0], b[0])
    dy = min(a[3], b[3]) - max(a[1], b[1])
    return dx * dy if dx > 0 and dy > 0 else 0.0


def mtv(a, b):
    """Minimum translation to separate a from b, as (dx, dy), plus the depth."""
    dx1 = a[2] - b[0]      # push a right
    dx2 = b[2] - a[0]      # push a left
    dy1 = a[3] - b[1]      # push a down
    dy2 = b[3] - a[1]      # push a up
    cands = [(dx1, 0), (-dx2, 0), (0, dy1), (0, -dy2)]
    cands.sort(key=lambda c: abs(c[0]) + abs(c[1]))
    return cands[0]


def move_by(fp, dx, dy):
    pos = fp.GetPosition()
    fp.SetPosition(pcbnew.VECTOR2I(pos.x + mm(dx), pos.y + mm(dy)))


def main():
    board = pcbnew.LoadBoard(PCB)
    fps = {board.GetFootprints()[i].GetReference(): board.GetFootprints()[i]
           for i in range(len(board.GetFootprints()))}

    for sweep in range(60):
        pairs = []
        refs = list(fps)
        for i, a in enumerate(refs):
            for b in refs[i + 1:]:
                if fps[a].GetLayer() != fps[b].GetLayer():
                    continue
                if overlap_area(box_of(fps[a]), box_of(fps[b])) > 0.0001:
                    pairs.append((a, b))
        if not pairs:
            print("no courtyard overlaps after %d sweeps" % sweep)
            break
        progress = False
        for a, b in pairs:
            fa, fb = fps[a], fps[b]
            a_fixed, b_fixed = a in SKELETON, b in SKELETON
            if a_fixed and b_fixed:
                print("  both %s and %s are fixed parts - left alone" % (a, b))
                continue
            # always push the loose side; the old `a if a_fixed else ...`
            # selected the FIXED part whenever it came first in the pair
            victim, other = (b, a) if a_fixed else (a, b)
            dx, dy = mtv(box_of(fps[victim]), box_of(fps[other]))
            sgn = 1.0
            move_by(fps[victim], dx * sgn + (PUSH if dx else 0) * (1 if dx > 0 else -1 if dx < 0 else 0),
                    dy * sgn + (PUSH if dy else 0) * (1 if dy > 0 else -1 if dy < 0 else 0))
            # keep it on the board
            bx = box_of(fps[victim])
            if bx[0] < MARGIN:
                move_by(fps[victim], MARGIN - bx[0], 0)
            if bx[1] < MARGIN:
                move_by(fps[victim], 0, MARGIN - bx[1])
            if bx[2] > 36.0 - MARGIN:
                move_by(fps[victim], 36.0 - MARGIN - bx[2], 0)
            if bx[3] > 30.0 - MARGIN:
                move_by(fps[victim], 0, 30.0 - MARGIN - bx[3])
            progress = True
            break   # re-scan after every single move
        if not progress:
            break

    board.Save(PCB)
    left = 0
    refs = list(fps)
    for i, a in enumerate(refs):
        for b in refs[i + 1:]:
            if fps[a].GetLayer() != fps[b].GetLayer():
                continue
            if overlap_area(box_of(fps[a]), box_of(fps[b])) > 0.0001:
                left += 1
                print("  still overlapping: %s / %s" % (a, b))
    print("remaining overlap pairs: %d" % left)


if __name__ == "__main__":
    main()
