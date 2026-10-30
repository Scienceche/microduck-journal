#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3.8: stitch connector GND pads to the bottom GND plane.

J3/J4/J5 sit (by design) inside the VBAT_TOP pour band, so their pin-3 GND
pads can never reach the top GND zone. This drops one via per such pad
(0.9 mm off-pad) plus a short tie trace; the bottom GND plane picks the via
up on the next pours.py fill. Any other GND pad whose only copper neighbor
is inside the VBAT band gets the same treatment.
"""
import os
import re
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJ, "imu_to_ttl.kicad_pcb")

TARGETS = ("J3", "J4", "J5", "J6")     # bus connectors with pin3 = GND
VIA_OFF = 0.9                            # pad-center to via-center
VIA_D, VIA_DRILL = 0.6, 0.3


def mm(v):
    return pcbnew.FromMM(v)


def main():
    board = pcbnew.LoadBoard(PCB)
    nets = board.GetNetInfo()
    gnd = nets.GetNetItem("GND")
    if gnd is None:
        sys.exit("no GND net")

    # drop previously added tie vias + tie tracks (idempotence)
    import math as _m
    pads = []
    for ref in TARGETS:
        fp = next((f for f in board.GetFootprints() if f.GetReference() == ref), None)
        if fp:
            pads += [(p.GetPosition().x / 1e6, p.GetPosition().y / 1e6)
                     for p in fp.Pads() if p.GetNetname() == "GND"]

    def near_pad(x, y, r=3.0):
        return any(_m.hypot(x - px, y - py) < r for px, py in pads)

    removed = 0
    trs = list(board.Tracks())
    doomed = []
    for i in range(len(trs)):
        tr = trs[i]
        if tr.GetNetname() != "GND":
            continue
        if isinstance(tr, pcbnew.PCB_VIA):
            v = tr.GetPosition()
            if near_pad(v.x / 1e6, v.y / 1e6, 2.2):
                doomed.append(tr)
        else:
            a, b = tr.GetStart(), tr.GetEnd()
            if near_pad(a.x / 1e6, a.y / 1e6, 2.2) and near_pad(b.x / 1e6, b.y / 1e6, 2.2):
                doomed.append(tr)
    for tr in doomed:
        board.Remove(tr)
        removed += 1
    board.Save(PCB)
    print("removed %d previous tie items" % removed)

    def copper_clear(x, y, clr=0.55):
        """True if no foreign copper (tracks/vias/pads) is within clr mm."""
        for tr in board.GetTracks():
            if tr.GetNetname() == "GND":
                continue
            if isinstance(tr, pcbnew.PCB_VIA):
                v = tr.GetPosition()
                if _m.hypot(x - v.x / 1e6, y - v.y / 1e6) < clr + 0.3:
                    return False
                continue
            a, b = tr.GetStart(), tr.GetEnd()
            x1, y1 = a.x / 1e6, a.y / 1e6
            x2, y2 = b.x / 1e6, b.y / 1e6
            dx, dy = x2 - x1, y2 - y1
            L2 = dx * dx + dy * dy or 1e-12
            t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / L2))
            if _m.hypot(x - (x1 + t * dx), y - (y1 + t * dy)) < clr + tr.GetWidth() / 2e6:
                return False
        for f in board.GetFootprints():
            for pad in f.Pads():
                if pad.GetNetname() == "GND":
                    continue
                pp = pad.GetPosition()
                rr = max(pad.GetSizeX(), pad.GetSizeY()) / 2e6
                if _m.hypot(x - pp.x / 1e6, y - pp.y / 1e6) < clr + rr:
                    return False
        return True

    added = 0
    for ref in TARGETS:
        fp = next((f for f in board.GetFootprints() if f.GetReference() == ref), None)
        if fp is None:
            continue
        for pad in fp.Pads():
            if pad.GetNetname() != "GND":
                continue
            p = pad.GetPosition()
            px, py = p.x / 1e6, p.y / 1e6
            # scan 8 directions x 3 radii for a spot clear of foreign copper
            vx = vy = None
            import math as _m2
            for dist in (1.5, 1.9, 2.3, 2.8, 3.4, 4.0):
                for k in range(8):
                    ang = _m2.radians(45.0 * k + 22.5)
                    cx = px + dist * _m2.cos(ang)
                    cy = py + dist * _m2.sin(ang)
                    if 0.8 < cx < 35.2 and 0.8 < cy < 29.2 and copper_clear(cx, cy):
                        # the TIE TRACE must be clear too - sample along the line
                        okline = True
                        for k in range(1, 4):
                            tx = px + (cx - px) * k / 4.0
                            ty = py + (cy - py) * k / 4.0
                            if not copper_clear(tx, ty, 0.35):
                                okline = False
                                break
                        if okline:
                            vx, vy = cx, cy
                            break
                if vx is not None:
                    break
            if vx is None:
                print("%s GND pad (%.2f,%.2f): NO clear via spot found" % (ref, px, py))
                continue
            via = pcbnew.PCB_VIA(board)
            via.SetPosition(pcbnew.VECTOR2I(mm(vx), mm(vy)))
            via.SetWidth(mm(VIA_D))
            via.SetDrill(mm(VIA_DRILL))
            via.SetNetCode(gnd.GetNetCode())
            via.SetViaType(pcbnew.VIATYPE_THROUGH)
            board.Add(via)
            # tie trace pad -> via (same net, F.Cu)
            tr = pcbnew.PCB_TRACK(board)
            tr.SetStart(p)
            tr.SetEnd(pcbnew.VECTOR2I(mm(vx), mm(vy)))
            tr.SetWidth(mm(0.3))
            tr.SetLayer(pcbnew.F_Cu)
            tr.SetNetCode(gnd.GetNetCode())
            board.Add(tr)
            added += 1
            print("%s GND pad (%.2f,%.2f) -> via (%.2f,%.2f)" % (ref, px, py, vx, vy))

    board.Save(PCB)
    print("added %d tie vias (rerun pours.py to connect)" % added)


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
