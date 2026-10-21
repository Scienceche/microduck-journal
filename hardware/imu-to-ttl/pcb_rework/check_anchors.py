#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pad-level collision check for the anchored skeleton (v3.7).

place_board/pack_loose work at courtyard level; TH pads stick out of their
courtyard boxes (J1 bare pads, JST pins, screw holes), so the anchors
themselves need a pad-vs-pad + pad-vs-hole check before routing.
Reports any two different-net pads (or NPTH holes) closer than the sum of
their radii + 0.25mm.

Usage: KiCad-python pcb_rework/check_anchors.py
"""
import math
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = os.path.join(PROJ, "imu_to_ttl.kicad_pcb")


def main():
    b = pcbnew.LoadBoard(PCB)
    pads = []          # (net, x, y, r, ref.pin, footprint)
    for i in range(len(b.GetFootprints())):
        fp = b.GetFootprints()[i]
        through = fp.GetAttribute() != pcbnew.FP_SMD if hasattr(fp, "GetAttribute") else True
        for p in fp.Pads():
            c = p.GetPosition()
            r = max(pcbnew.ToMM(p.GetSize().x), pcbnew.ToMM(p.GetSize().y)) / 2
            net = p.GetNetname() or "NPTH"
            # a pad matters across faces only if it is a through hole
            smd = p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD
            layer = fp.GetLayer()
            pads.append((net, pcbnew.ToMM(c.x), pcbnew.ToMM(c.y), r,
                         "%s.%s" % (fp.GetReference(), p.GetPadName()), fp.GetReference(),
                         smd, layer))
    bad = 0
    for i in range(len(pads)):
        for j in range(i + 1, len(pads)):
            n1, x1, y1, r1, t1, f1, smd1, l1 = pads[i]
            n2, x2, y2, r2, t2, f2, smd2, l2 = pads[j]
            if n1 == n2 or f1 == f2:
                continue
            # SMD pads on opposite faces coexist (back-to-back)
            if smd1 and smd2 and l1 != l2:
                continue
            d = math.hypot(x1 - x2, y1 - y2)
            need = r1 + r2 + 0.25
            if d < need:
                bad += 1
                print("COLLIDE %-12s %-12s d=%.2f need=%.2f  @(%.1f,%.1f)" % (t1, t2, d, need, x1, y1))
    print("%d pad collisions" % bad)


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
