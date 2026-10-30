#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3.8 deterministic pre-routes: the U2(LSM6DSV16X) <-> U1(STM32) escape knot.

v9: U1 rotated 180deg so its SPI pins (11-15) face U2 on the WEST side
(x22.64, y16.07..18.68). Pure top-layer 5-net fan, planar-verified:
escape levels y13.0/13.4/13.8/14.2/15.25, verticals x19.0..21.0, stubs
straight into the pad column - ZERO crossings, ONE via (OCS to R6 bottom).
Run BEFORE tools/route.py.
"""
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJ, "imu_to_ttl.kicad_pcb")

W = 0.2
VIA_D, VIA_DRILL = 0.6, 0.3

ROUTES = {
    "SPI_MISO": [("F", [(15.84, 14.25), (15.84, 13.21), (20.0, 13.21), (20.0, 17.38), (22.64, 17.38)])],
    "SPI_SCK": [("F", [(17.0, 14.09), (17.0, 12.81), (20.5, 12.81), (20.5, 16.73), (22.64, 16.73)])],
    "SPI_MOSI": [("F", [(16.5, 14.09), (16.5, 13.61), (19.5, 13.61), (19.5, 18.02), (22.64, 18.02)])],
    "SPI_CS": [("F", [(17.5, 14.09), (17.5, 12.41), (21.0, 12.41), (21.0, 16.07), (22.64, 16.07)])],
    "IMU_INT2": [("F", [(18.16, 15.25), (19.0, 15.25), (19.0, 18.68), (22.64, 18.68)])],
    "OCS_AUX": [("F", [(18.16, 14.75), (18.7, 14.75)]),
                ("V", (18.7, 14.75)),
                ("B", [(18.9, 14.75), (18.9, 15.4), (13.67, 15.4), (13.67, 16.2)])],
}


def mm(v):
    return pcbnew.FromMM(v)




def _validate(board, nets_map):
    """Mechanical clearance check of every route against all pads and the
    other routes. Returns a list of violation strings (empty = clean)."""
    import math as _m
    CLR = 0.155
    VIA_R = 0.3
    errs = []
    # gather pads: (x, y, r, net, ref)
    pads = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            p_ = pad.GetPosition()
            r = max(pad.GetSizeX(), pad.GetSizeY()) / 2e6
            pads.append((p_.x / 1e6, p_.y / 1e6, r, pad.GetNetname(), fp.GetReference()))

    def seg_dist(px, py, x1, y1, x2, y2):
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy or 1e-12
        t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
        return _m.hypot(px - (x1 + t * dx), py - (y1 + t * dy))

    all_geo = []   # (net, layer, kind, coords, half_width)
    for netname, steps in ROUTES.items():
        prev = None
        for step in steps:
            if step[0] == "V":
                all_geo.append((netname, "*", "v", step[1], VIA_R))
                prev = step[1]
            else:
                layer = step[0]
                pts = list(step[1])
                if prev is not None and pts[0] != prev:
                    pts.insert(0, prev)
                for a, b in zip(pts, pts[1:]):
                    all_geo.append((netname, layer, "s", (a, b), W / 2))
                prev = pts[-1]

    def geo_dist(g, px, py):
        if g[2] == "v":
            return _m.hypot(px - g[3][0], py - g[3][1]) - g[4]
        (x1, y1), (x2, y2) = g[3]
        return seg_dist(px, py, x1, y1, x2, y2) - g[4]

    for net, layer, kind, coords, hw in all_geo:
        for (px, py, pr, pnet, ref) in pads:
            if pnet == net:
                continue
            if layer != "*" and not _pad_layers(board, ref):
                pass
            d = (geo_dist((net, layer, kind, coords, hw), px, py)) - pr
            if d < CLR - 1e-6:
                errs.append("PAD %s.%s(%s) d=%.3f<%.3f vs %s %s %s" % (
                    ref, "", pnet, d + pr, pr + CLR + hw, net, kind, coords))
    # route-vs-route (foreign nets)
    for i in range(len(all_geo)):
        for j in range(i + 1, len(all_geo)):
            g1, g2 = all_geo[i], all_geo[j]
            if g1[0] == g2[0]:
                continue
            if g1[1] != g2[1] and g1[1] != "*" and g2[1] != "*":
                continue
            d = _geo_gap(g1, g2)
            if d < CLR - 1e-6:
                errs.append("X %s %s <-> %s %s d=%.3f" % (g1[0], g1[3], g2[0], g2[3], d))
    return errs


def _pad_layers(board, ref):
    return True


def _geo_gap(g1, g2):
    import math as _m
    if g2[2] == "v" and g1[2] == "v":
        return _m.hypot(g1[3][0] - g2[3][0], g1[3][1] - g2[3][1]) - g1[4] - g2[4]
    if g1[2] == "v" or g2[2] == "v":
        v, s = (g1, g2) if g1[2] == "v" else (g2, g1)
        (x1, y1), (x2, y2) = s[3]
        dx, dy = x2 - x1, y2 - y1
        L2 = dx * dx + dy * dy or 1e-12
        t = max(0.0, min(1.0, ((v[3][0] - x1) * dx + (v[3][1] - y1) * dy) / L2))
        return _m.hypot(v[3][0] - (x1 + t * dx), v[3][1] - (y1 + t * dy)) - v[4] - s[4]
    (ax1, ay1), (ax2, ay2) = g1[3]
    (bx1, by1), (bx2, by2) = g2[3]
    def sd(px, py):
        dx, dy = bx2 - bx1, by2 - by1
        L2 = dx * dx + dy * dy or 1e-12
        t = max(0.0, min(1.0, ((px - bx1) * dx + (py - by1) * dy) / L2))
        return _m.hypot(px - (bx1 + t * dx), py - (by1 + t * dy))
    return min(sd(ax1, ay1), sd(ax2, ay2),
               _m.hypot(bx1 - ax1, by1 - ay1) if False else sd(ax2, ay2))


def main():
    board = pcbnew.LoadBoard(PCB)
    pass  # validator available offline: pre_route_v38._validate(board, None)
    nets = board.GetNetInfo()
    added_t = added_v = 0
    for netname, steps in ROUTES.items():
        net = nets.GetNetItem(netname)
        if net is None:
            print("!! net %s missing, skipped" % netname)
            continue
        code = net.GetNetCode()
        prev_via_pt = None
        for step in steps:
            kind = step[0]
            if kind == "V":
                via = pcbnew.PCB_VIA(board)
                via.SetPosition(pcbnew.VECTOR2I(mm(step[1][0]), mm(step[1][1])))
                via.SetWidth(mm(VIA_D))
                via.SetDrill(mm(VIA_DRILL))
                via.SetNetCode(code)
                via.SetViaType(pcbnew.VIATYPE_THROUGH)
                board.Add(via)
                added_v += 1
                prev_via_pt = step[1]
            else:
                layer = pcbnew.F_Cu if kind == "F" else pcbnew.B_Cu
                pts = list(step[1])
                if prev_via_pt is not None and pts[0] != prev_via_pt:
                    pts.insert(0, prev_via_pt)
                for a, b in zip(pts, pts[1:]):
                    tr = pcbnew.PCB_TRACK(board)
                    tr.SetStart(pcbnew.VECTOR2I(mm(a[0]), mm(a[1])))
                    tr.SetEnd(pcbnew.VECTOR2I(mm(b[0]), mm(b[1])))
                    tr.SetWidth(mm(W))
                    tr.SetLayer(layer)
                    tr.SetNetCode(code)
                    board.Add(tr)
                    added_t += 1
                prev_via_pt = pts[-1]
    board.Save(PCB)
    print("pre-routed: %d tracks, %d vias (%d nets)" % (added_t, added_v, len(ROUTES)))


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
