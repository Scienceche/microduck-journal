#!/usr/bin/env python3
"""J1: XY308 screw terminal -> bare wire solder pads + local reroute.

From a clean v3 backup:
1. swap J1 to imu_to_ttl:BattPad_1x02_P3.60mm_Drill1.2mm at (4.4, 26.5) rot0
   -> pad1 VBAT_RAW @(6.2,26.5) lands on the existing VBAT stub chain,
      pad2 GND       @(2.6,26.5) lands on the two GND stubs.
2. the Q1_GATE / NRST traces used to wrap AROUND the old terminal body and
   now clip the fat pads; nudge them out (Q1_GATE east corner via x7.6 +
   top run y27.95 + west diagonal moved down to (0.79,25.6); NRST top run
   up to y28.35).
3. refill zones, save.
"""
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = os.path.join(PROJ, "imu_to_ttl.kicad_pcb")
BAK = os.path.join(HERE, "imu_to_ttl.v3_xy308J1.bak")
LIB = os.path.join(PROJ, "imu_to_ttl.pretty")
FPNAME = "BattPad_1x02_P3.60mm_Drill1.2mm"

MM = 1000000


def close(a, b, tol=0.06):
    return abs(a - b) < tol * MM


def find_seg(board, tracks, netname, x1, y1, x2, y2):
    for t in tracks:
        if t.GetClass() != "PCB_TRACK":
            continue
        if board.FindNet(t.GetNetCode()).GetNetname() != netname:
            continue
        s, e = t.GetStart(), t.GetEnd()
        if ((close(s.x, x1 * MM) and close(s.y, y1 * MM) and close(e.x, x2 * MM) and close(e.y, y2 * MM)) or
                (close(s.x, x2 * MM) and close(s.y, y2 * MM) and close(e.x, x1 * MM) and close(e.y, y1 * MM))):
            return t
    return None


def add_seg(board, net, layer, x1, y1, x2, y2, w):
    t = pcbnew.PCB_TRACK(board)
    t.SetStart(pcbnew.VECTOR2I(int(x1 * MM), int(y1 * MM)))
    t.SetEnd(pcbnew.VECTOR2I(int(x2 * MM), int(y2 * MM)))
    t.SetWidth(int(w * MM))
    t.SetLayer(layer)
    t.SetNet(net)
    board.Add(t)
    return t


def main():
    # start clean from the pre-swap backup
    import shutil
    shutil.copyfile(BAK, PCB)

    board = pcbnew.LoadBoard(PCB)
    # snapshot everything BEFORE any Remove(): SWIG collections corrupt after Remove
    tracks = [t for t in board.GetTracks()]
    fps = [board.GetFootprints()[i] for i in range(len(board.GetFootprints()))]
    j1 = None
    for fp in fps:
        if fp.GetReference() == "J1":
            j1 = fp
    if j1 is None:
        raise SystemExit("J1 not found")

    # ---- track surgery FIRST (operate on the snapshot, board still intact) ----
    QG = "Q1_GATE"
    NR = "NRST"
    fcu, bcu = pcbnew.F_Cu, pcbnew.B_Cu
    qg_net = board.FindNet(QG)
    nr_net = board.FindNet(NR)

    width = {}
    ref = find_seg(board, tracks, QG, 14.92, 26.05, 7.38, 26.05)
    width[QG] = ref.GetWidth() / MM if ref else 0.25
    ref2 = find_seg(board, tracks, NR, 9.24, 26.25, 7.73, 27.76)
    width[NR] = ref2.GetWidth() / MM if ref2 else 0.25

    # deletions (old wrap around the terminal body)
    for seg in [
        (QG, 7.38, 26.05, 5.62, 27.80), (QG, 5.62, 27.80, 1.57, 27.80),
        (QG, 1.57, 27.80, 0.79, 27.03), (QG, 0.79, 27.03, 0.79, 25.99),
        (QG, 0.79, 25.99, 2.39, 24.40),
        (NR, 7.73, 27.76, 1.46, 27.76), (NR, 1.46, 27.76, 0.81, 27.10),
    ]:
        t = find_seg(board, tracks, *seg)
        if t is None:
            print("WARN: no seg", seg)
        else:
            board.Remove(t)

    # east Q1_GATE run shortened to x7.6, then a new clear jog over the pads
    t = find_seg(board, tracks, QG, 14.92, 26.05, 7.38, 26.05)
    if t:
        s, e = t.GetStart(), t.GetEnd()
        if close(s.x, 7.38):
            t.SetEnd(pcbnew.VECTOR2I(int(7.6 * MM), int(26.05 * MM)))
        else:
            t.SetStart(pcbnew.VECTOR2I(int(7.6 * MM), int(26.05 * MM)))
    for seg in [
        (fcu, 7.60, 26.05, 7.60, 27.95),
        (fcu, 7.60, 27.95, 1.57, 27.95),
        (fcu, 1.57, 27.95, 0.79, 27.03),
        (fcu, 0.79, 27.03, 0.79, 25.60),
        (fcu, 0.79, 25.60, 2.39, 24.40),
    ]:
        add_seg(board, qg_net, seg[0], *seg[1:], width[QG])

    # NRST: drop its east end lower and run the top wire above the pads
    t = find_seg(board, tracks, NR, 9.24, 26.25, 7.73, 27.76)
    if t:
        s, e = t.GetStart(), t.GetEnd()
        if close(s.x, 7.73 * MM):
            t.SetStart(pcbnew.VECTOR2I(int(7.73 * MM), int(28.35 * MM)))
        else:
            t.SetEnd(pcbnew.VECTOR2I(int(7.73 * MM), int(28.35 * MM)))
    for seg in [
        (bcu, 7.73, 28.35, 1.20, 28.35),
        (bcu, 1.20, 28.35, 0.81, 27.10),
    ]:
        add_seg(board, nr_net, seg[0], *seg[1:], width[NR])

    # ---- now swap J1 footprint (board collections no longer needed) ----
    board.Remove(j1)
    new = pcbnew.FootprintLoad(LIB, FPNAME)
    if new is None:
        raise SystemExit("cannot load %s" % FPNAME)
    new.SetReference("J1")
    new.SetValue("BattPad_1x02_P3.6")
    new.SetPosition(pcbnew.VECTOR2I(int(4.4 * MM), int(26.5 * MM)))
    new.SetOrientationDegrees(0)
    netmap = {"1": "VBAT_RAW", "2": "GND"}
    for pad in new.Pads():
        nm = netmap.get(pad.GetNumber())
        if nm:
            pad.SetNet(board.FindNet(nm))
    try:
        new.SetLocalClearance(pcbnew.FromMM(0))
    except Exception:
        pass
    board.Add(new)

    filler = pcbnew.ZONE_FILLER(board)
    print("refill:", filler.Fill(board.Zones()))
    board.Save(PCB)
    print("done: J1 bare pads + reroute")


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
