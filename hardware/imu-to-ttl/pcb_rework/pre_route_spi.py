#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pre-route the U2->U1 SPI escape pair (MISO/SCK) on the freshly packed board.

Freerouting strands these two nets on many rolls: it weaves the IMU's aux
nets (SDx/SCx/OCS/INT) through the south-row escape lanes first and fences
the remaining SPI pads in. Both endpoints are FLOORPLAN-anchored (U2 at
(20.5,15), U1 at (28.6,16)), so these two routes are fully deterministic.
Run between pack_loose/fix_overlaps/rules and `route.py --continue`.

Usage:  KiCad-python pcb_rework/pre_route_spi.py
"""
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = os.path.join(PROJ, "imu_to_ttl.kicad_pcb")

F, B = pcbnew.F_Cu, pcbnew.B_Cu

# west of U2 the escape frame reserves x17.2-18.8; drop through it on B,
# run the B corridor y16.9-18.3 (empty between the keep-out frames), rise
# under U1's east pad row.
# U2 LGA-14 west column (x19.34): pin1 MISO@14.25, SDx@14.75, SCx@15.25,
# INT1@15.75; east column (x21.66): OCS@14.75, INT2@15.25, V3V3@15.75.
# Escapes must run OUTSIDE those columns: MISO west lane x18.35, SCK drops
# south then jogs east to x21.2 (between the pad columns' clearance).
ROUTES = {
    "SPI_MISO": [
        (F, 19.3375, 14.25, 18.35, 14.25), (F, 18.35, 14.25, 18.35, 17.625),
        ("VIA", 18.35, 17.625),
        (B, 18.35, 17.625, 30.8, 17.625), ("VIA", 30.8, 17.625),
        (F, 30.8, 17.625, 31.4625, 17.625),
    ],
}


def main():
    b = pcbnew.LoadBoard(PCB)
    nets = b.GetNetInfo()

    def nc(name):
        n = nets.GetNetItem(name)
        assert n, name
        return n.GetNetCode()

    for name, path in ROUTES.items():
        for s in path:
            if s[0] == "VIA":
                v = pcbnew.PCB_VIA(b)
                v.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(s[1]), pcbnew.FromMM(s[2])))
                v.SetWidth(pcbnew.FromMM(0.6))
                v.SetDrill(pcbnew.FromMM(0.3))
                v.SetNetCode(nc(name))
                v.SetViaType(pcbnew.VIATYPE_THROUGH)
                b.Add(v)
            else:
                t = pcbnew.PCB_TRACK(b)
                t.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(s[1]), pcbnew.FromMM(s[2])))
                t.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(s[3]), pcbnew.FromMM(s[4])))
                t.SetWidth(pcbnew.FromMM(0.2))
                t.SetLayer(s[0])
                t.SetNetCode(nc(name))
                b.Add(t)
    b.Save(PCB)
    print("pre-routed: %s" % ", ".join(ROUTES))  # SCK: 0.5mm-pitch south row has no legal 0.155 escape - leave to the router


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
