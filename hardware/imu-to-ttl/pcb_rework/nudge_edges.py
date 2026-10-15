#!/usr/bin/env python3
"""Nudge copper that sits too close to the board edge (copper_edge_clearance).

The autorouter occasionally lays a bus trace along the very top/bottom band
(inside the 3 mm component keep-out the router is allowed to use), leaving
0.16-0.22 mm of copper-to-edge instead of the project's 0.25 mm. This script
moves the *nodes* (track endpoints / via centres) that belong to the violating
items inward by exactly the missing amount, so every segment that shares the
node moves with it and connectivity is preserved. Zones are refilled at the end.

Usage:  KiCad-python pcb_rework/nudge_edges.py [board.kicad_pcb]
"""

import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJ, "imu_to_ttl.kicad_pcb")

CLEAR = 250000          # 0.25 mm in nm
SLACK = 5000            # nudge a hair beyond the rule
BOARD_W = 41000000
BOARD_H = 30000000


def key(p):
    return (round(p.x / 1000), round(p.y / 1000))   # 1 um grid


def edge_short(bb):
    """How much (dx, dy) the item must move to clear every edge, and whether
    it violates at all."""
    dx = dy = 0
    need = False
    if bb.GetX() < CLEAR:
        dy = dy  # left edge -> +x
        dx = max(dx, CLEAR - bb.GetX() + SLACK)
        need = True
    if bb.GetRight() > BOARD_W - CLEAR:
        dx = min(dx, (BOARD_W - CLEAR) - bb.GetRight() - SLACK)
        need = True
    if bb.GetY() < CLEAR:
        dy = max(dy, CLEAR - bb.GetY() + SLACK)
        need = True
    if bb.GetBottom() > BOARD_H - CLEAR:
        dy = min(dy, (BOARD_H - CLEAR) - bb.GetBottom() - SLACK)
        need = True
    return need, dx, dy


def main():
    board = pcbnew.LoadBoard(PCB)

    for sweep in range(8):
        moves = {}          # node key -> (dx, dy)
        for t in board.GetTracks():
            if t.Type() not in (pcbnew.PCB_TRACE_T, pcbnew.PCB_VIA_T):
                continue
            need, dx, dy = edge_short(t.GetBoundingBox())
            if not need:
                continue
            if t.Type() == pcbnew.PCB_VIA_T:
                k = key(t.GetPosition())
                ox, oy = moves.get(k, (0, 0))
                moves[k] = (max(ox, dx) if dx > 0 else min(ox, dx),
                            max(oy, dy) if dy > 0 else min(oy, dy))
            else:
                for p in (t.GetStart(), t.GetEnd()):
                    k = key(p)
                    ox, oy = moves.get(k, (0, 0))
                    moves[k] = (max(ox, dx) if dx > 0 else min(ox, dx),
                                max(oy, dy) if dy > 0 else min(oy, dy))
        if not moves:
            break
        moved = 0
        for t in board.GetTracks():
            if t.Type() == pcbnew.PCB_VIA_T:
                k = key(t.GetPosition())
                if k in moves:
                    dx, dy = moves[k]
                    t.Move(pcbnew.VECTOR2I(dx, dy))
                    moved += 1
            elif t.Type() == pcbnew.PCB_TRACE_T:
                hit = False
                s, e = t.GetStart(), t.GetEnd()
                ks, ke = key(s), key(e)
                if ks in moves:
                    dx, dy = moves[ks]
                    t.SetStart(pcbnew.VECTOR2I(s.x + dx, s.y + dy))
                    hit = True
                if ke in moves:
                    dx, dy = moves[ke]
                    t.SetEnd(pcbnew.VECTOR2I(e.x + dx, e.y + dy))
                    hit = True
                if hit:
                    moved += 1
        print("sweep %d: %d nodes, %d items moved" % (sweep, len(moves), moved))

    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    board.Save(PCB)
    print("refilled zones and saved")


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
