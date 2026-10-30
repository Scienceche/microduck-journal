#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3.8: grid-scan GND stitching.

The fixed STITCH_CANDIDATES spots are mostly covered by routing. This scans
the whole board on a 0.5 mm grid, keeps points clear of all copper/holes,
and drops GND vias spread across board thirds until the planes merge.
"""
import math
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJ, "imu_to_ttl.kicad_pcb")

SAFE = 0.55
STEP = 0.5
MAX_VIAS = 14


def mm(v):
    return pcbnew.FromMM(v)


def main():
    board = pcbnew.LoadBoard(PCB)
    nets = board.GetNetInfo()
    gnd = nets.GetNetItem("GND")
    if gnd is None:
        sys.exit("no GND")

    holes = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                p = pad.GetPosition()
                holes.append((p.x / 1e6, p.y / 1e6))

    tracks = []
    ts = board.Tracks()
    for i in range(len(ts)):
        tr = ts[i]
        if isinstance(tr, pcbnew.PCB_VIA):
            v = tr.GetPosition()
            tracks.append(("v", v.x / 1e6, v.y / 1e6, 0.35))   # vias: fixed half-width (0.6-0.7 dia)
        else:
            a, b = tr.GetStart(), tr.GetEnd()
            tracks.append(("s", a.x / 1e6, a.y / 1e6, b.x / 1e6, b.y / 1e6, tr.GetWidth() / 2e6))

    pads = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            p = pad.GetPosition()
            rr = max(pad.GetSizeX(), pad.GetSizeY()) / 2e6
            pads.append((p.x / 1e6, p.y / 1e6, rr))

    def clear(x, y, is_gnd_zone_fill=True):
        for hx, hy in holes:
            if math.hypot(x - hx, y - hy) < 1.05 + SAFE - 0.25:
                return False
        for px, py, rr in pads:
            if math.hypot(x - px, y - py) < rr + SAFE:
                return False
        for t in tracks:
            if t[0] == "v":
                if math.hypot(x - t[1], y - t[2]) < t[3] + SAFE:
                    return False
            else:
                x1, y1, x2, y2, hw = t[1], t[2], t[3], t[4], t[5]
                dx, dy = x2 - x1, y2 - y1
                L2 = dx * dx + dy * dy or 1e-12
                tt = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / L2))
                if math.hypot(x - (x1 + tt * dx), y - (y1 + tt * dy)) < hw + SAFE:
                    return False
        return True

    # 6 zones (2 cols x 3 rows), up to 3 vias each, min 4mm apart
    zones = [(cx, cy) for cy in (5.0, 15.0, 25.0) for cx in (9.0, 27.0)]
    placed = []
    for zx, zy in zones:
        got = 0
        for r in (0.0, 1.5, 3.0, 4.5):
            for k in range(12):
                ang = math.radians(30.0 * k)
                x = round((zx + r * math.cos(ang)) / STEP) * STEP
                y = round((zy + r * math.sin(ang)) / STEP) * STEP
                if not (0.9 < x < 35.1 and 0.9 < y < 29.1):
                    continue
                if any(math.hypot(x - px, y - py) < 4.0 for px, py in placed):
                    continue
                if clear(x, y):
                    via = pcbnew.PCB_VIA(board)
                    via.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
                    via.SetWidth(mm(0.6))
                    via.SetDrill(mm(0.3))
                    via.SetNetCode(gnd.GetNetCode())
                    via.SetViaType(pcbnew.VIATYPE_THROUGH)
                    board.Add(via)
                    placed.append((x, y))
                    got += 1
                    break
            if got >= 2:
                break

    board.Save(PCB)
    print("grid-stitched %d GND vias at %s" % (len(placed),
          " ".join("(%.1f,%.1f)" % p for p in placed)))


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
