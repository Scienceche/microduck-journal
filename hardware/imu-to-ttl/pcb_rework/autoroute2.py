#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Finisher for the last few nets: parameterised polyline search with numeric
clearance verification against the live board.

Each route is a family of axis-aligned polylines with 2-3 free parameters
(detour x, band y, leg x). For every candidate the script checks every segment
(and via) against all foreign copper on the same layer using a 1 mm spatial
grid, and commits the first family member that clears 0.155 mm everywhere.
Prints progress unbuffered.

Usage: KiCad-python pcb_rework/autoroute2.py
"""
import math
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = os.path.join(PROJ, "imu_to_ttl.kicad_pcb")

CLEAR = 0.155
W = 0.2
VIA_R = 0.3


def M(v):
    return pcbnew.ToMM(v)


def pt_seg(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def seg_seg(a, b):
    x1, y1, x2, y2 = a
    x3, y3, x4, y4 = b
    return min(pt_seg(x3, y3, x1, y1, x2, y2), pt_seg(x4, y4, x1, y1, x2, y2),
               pt_seg(x1, y1, x3, y3, x4, y4), pt_seg(x2, y2, x3, y3, x4, y4))


class Grid:
    """1 mm buckets of foreign copper: (net, kind, geom)."""

    def __init__(self, board):
        self.cells = {}
        for t in board.GetTracks():
            nm = t.GetNetname()
            if t.Type() == pcbnew.PCB_VIA_T:
                p = t.GetPosition()
                # KiCad 10 asserts on via GetWidth() without a layer arg; all
                # vias here are 0.6-0.7 dia, so use a conservative radius
                self._add(t.GetLayer(), nm, 'circ', (M(p.x), M(p.y), 0.36))
            elif t.Type() == pcbnew.PCB_TRACE_T:
                s, e = t.GetStart(), t.GetEnd()
                w = M(t.GetWidth())
                self._add(t.GetLayer(), nm, 'seg', (M(s.x), M(s.y), M(e.x), M(e.y), w))
        for i in range(len(board.GetFootprints())):
            fp = board.GetFootprints()[i]
            for p in fp.Pads():
                c = p.GetPosition()
                sx, sy = M(p.GetSize().x), M(p.GetSize().y)
                try:
                    ang = abs(p.GetOrientation().AsDegrees()) % 180
                except AttributeError:
                    ang = 0.0
                if sx >= sy:
                    # long thin pad (SMD): model as a segment along its axis
                    if abs(ang) < 45 or abs(ang) > 135:
                        g = (M(c.x) - sx / 2, M(c.y), M(c.x) + sx / 2, M(c.y), sy)
                    else:
                        g = (M(c.x), M(c.y) - sx / 2, M(c.x), M(c.y) + sx / 2, sy)
                    self._add(fp.GetLayer(), p.GetNetname(), 'seg', g)
                else:
                    self._add(fp.GetLayer(), p.GetNetname(), 'circ',
                              (M(c.x), M(c.y), sy / 2))

    def _add(self, layer, net, kind, g):
        if kind == 'circ':
            x, y, r = g
            bbox = (x - r, y - r, x + r, y + r)
        else:
            x1, y1, x2, y2, w = g
            bbox = (min(x1, x2) - w, min(y1, y2) - w, max(x1, x2) + w, max(y1, y2) + w)
        for gx in range(int(bbox[0] - 1), int(bbox[2] + 1) + 1):
            for gy in range(int(bbox[1] - 1), int(bbox[3] + 1) + 1):
                self.cells.setdefault((layer, gx, gy), []).append((net, kind, g))

    def hits(self, layer, x1, y1, x2, y2, need, skip_net):
        """Yield conflicts of the segment/circle against foreign copper."""
        bb = (min(x1, x2) - need, min(y1, y2) - need, max(x1, x2) + need, max(y1, y2) + need)
        seen = set()
        for gx in range(int(bb[0]), int(bb[2]) + 1):
            for gy in range(int(bb[1]), int(bb[3]) + 1):
                for item in self.cells.get((layer, gx, gy), ()):
                    if item in seen:
                        continue
                    seen.add(item)
                    yield item


grid = None


def seg_ok(layer, net, x1, y1, x2, y2):
    for onm, kind, g in grid.hits(layer, x1, y1, x2, y2, 0.6, net):
        if onm == net or onm == '':
            continue
        if kind == 'circ':
            cx, cy, r = g
            if pt_seg(cx, cy, x1, y1, x2, y2) < r + W / 2 + CLEAR - 0.06:
                return False
        else:
            ox1, oy1, ox2, oy2, ow = g
            if seg_seg((x1, y1, x2, y2), (ox1, oy1, ox2, oy2)) < ow / 2 + W / 2 + CLEAR:
                return False
    return True


def via_ok(layer, net, x, y):
    for onm, kind, g in grid.hits(layer, x, y, x, y, 0.8, net):
        if onm == net or onm == '':
            continue
        if kind == 'circ':
            cx, cy, r = g
            if math.hypot(cx - x, cy - y) < r + VIA_R + CLEAR - 0.06:
                return False
        else:
            ox1, oy1, ox2, oy2, ow = g
            if pt_seg(x, y, ox1, oy1, ox2, oy2) < ow / 2 + VIA_R + CLEAR:
                return False
    return True


def route_ok(net, segs):
    for s in segs:
        if s[0] == 'VIA':
            if not via_ok(pcbnew.B_Cu, net, s[1], s[2]):
                return False
        else:
            if not seg_ok(s[0], net, s[1], s[2], s[3], s[4]):
                return False
    return True


def search(name, fn, ranges):
    combos = 1
    for r in ranges:
        combos *= len(r)
    n = 0
    import itertools
    for combo in itertools.product(*ranges):
        n += 1
        if n % 2000 == 0:
            print('  %s: %d/%d' % (name, n, combos))
            sys.stdout.flush()
        segs = fn(*combo)
        if route_ok(name, segs):
            print('%s FOUND at %s' % (name, combo))
            sys.stdout.flush()
            return segs
    print('%s: no candidate passed' % name)
    sys.stdout.flush()
    return None


def main():
    global grid
    b = pcbnew.LoadBoard(PCB)
    grid = Grid(b)
    print('grid: %d cells' % len(grid.cells))
    sys.stdout.flush()

    F, B = pcbnew.F_Cu, pcbnew.B_Cu

    def p3v3(xd, yb):
        return [(F, 32.475, 20.795, xd, 20.795), (F, xd, 20.795, xd, yb),
                ('VIA', xd, yb), (B, xd, yb, 25.7, yb), (B, 25.7, yb, 25.7, yb - 0.35)]

    def pbcl(xv, yb, xl):
        return [(F, 33.595, 6.245, xv, 6.245), ('VIA', xv, 6.8),
                (B, xv, 6.8, xv, yb), (B, xv, yb, xl, yb),
                (B, xl, yb, xl, 28.2), (B, xl, 28.2, 26.18, 28.2)]

    r1 = search('+3V3', p3v3,
                [[x / 20 for x in range(676, 716, 2)], [y / 20 for y in range(440, 484, 2)]])
    r2 = search('BUS_CLAMP', pbcl,
                [[x / 20 for x in range(640, 700, 2)], [y / 20 for y in range(580, 598, 2)],
                 [x / 20 for x in range(524, 552, 2)]])

    nets = b.GetNetInfo()

    def nc(name):
        n = nets.GetNetItem(name)
        assert n, name
        return n.GetNetCode()

    def add_seg(x1, y1, x2, y2, net, layer):
        s = pcbnew.PCB_TRACK(b)
        s.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(x1), pcbnew.FromMM(y1)))
        s.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(x2), pcbnew.FromMM(y2)))
        s.SetWidth(pcbnew.FromMM(W))
        s.SetLayer(layer)
        s.SetNetCode(nc(net))
        b.Add(s)

    def add_via(x, y, net):
        v = pcbnew.PCB_VIA(b)
        v.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
        v.SetWidth(pcbnew.FromMM(0.6))
        v.SetDrill(pcbnew.FromMM(0.3))
        v.SetNetCode(nc(net))
        v.SetViaType(pcbnew.VIATYPE_THROUGH)
        b.Add(v)

    added = 0
    for name, segs in (('+3V3', r1), ('BUS_CLAMP', r2)):
        if not segs:
            continue
        for s in segs:
            if s[0] == 'VIA':
                add_via(s[1], s[2], name)
            else:
                add_seg(s[1], s[2], s[3], s[4], name, s[0])
            added += 1
    print('added %d items' % added)
    filler = pcbnew.ZONE_FILLER(b)
    filler.Fill(b.Zones())
    b.Save(PCB)
    print('saved')


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
