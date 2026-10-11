#!/usr/bin/env python3
"""Close the last few connections with exact geometry.

STATUS (2026-09-16): the connected-component analysis and the
foreign-copper-only clearance model work, but the routing step does
not close the remaining nets yet - it reports pairs 12-15 mm apart
for nets whose pads are much closer, so the component/anchor
extraction still has a bug. patch_routes.py is the tool that is in
the build chain; this one is the starting point for the mop-up work.

Why this exists: after the autorouter and patch_routes have had their turn, a
handful of nets are still split into several disconnected pieces of copper. The
earlier hand-written mop-up failed because it paired the DRC's "unconnected
items" entries two by two - a net often has *more* than two pieces, so the pairs
were wrong and one net (+3V3) came out more fragmented than before.

This tool instead:
  * groups every net's copper (pads, track segments, vias, any layer) into
    connected components by actual contact,
  * repeatedly joins the two closest components (a minimum spanning tree),
  * and searches each join with exact clearance geometry against *foreign*
    copper only - own-net copper is the destination, not an obstacle.

Layers are explicit: a path runs on one layer, or escapes through vias to the
back layer when the front is blocked.

Usage:  python3 tools/route_leftovers.py [board.kicad_pcb] [--dry-run]
"""

import math
import os
import re
import sys
import uuid

import pcbnew

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import patch_routes as pr

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = os.path.join(PROJ, "imu_to_ttl.kicad_pcb")
DRY = "--dry-run" in sys.argv
for a in sys.argv[1:]:
    if a.endswith(".kicad_pcb"):
        PCB = a

CLEARANCE = 0.20          # the project's netclass clearance
TRACK_W = 0.20            # the Default class minimum (the leftovers are signals)
VIA_D, VIA_DRILL = 0.60, 0.30
BOARD = (0.60, 0.60, 40.40, 29.40)
EPS = 0.002               # contact tolerance
MAX_PAIRS = 60            # anchor pairs examined per component join
OFFSETS = [i * 0.25 for i in range(-40, 41)]   # long detours included
V_RING = [(dx * 0.4, dy * 0.4) for dx in range(-5, 6) for dy in range(-5, 6)
          if 0.30 < math.hypot(dx * 0.4, dy * 0.4) < 2.2]


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


def rect_gap(a, b):
    return math.hypot(max(b[0] - a[2], a[0] - b[2], 0.0),
                      max(b[1] - a[3], a[1] - b[3], 0.0))


def rect_seg(r, c, d):
    x0, y0, x1, y1 = r
    return min(seg_seg((x0, y0), (x1, y0), c, d), seg_seg((x1, y0), (x1, y1), c, d),
               seg_seg((x1, y1), (x0, y1), c, d), seg_seg((x0, y1), (x0, y0), c, d))


class Pad(object):
    def __init__(self, net, box, on_f, on_b):
        self.net, self.box, self.on_f, self.on_b = net, box, on_f, on_b
        self.cx, self.cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0

    def layers(self):
        return [l for l, on in (("F", self.on_f), ("B", self.on_b)) if on]


class Seg(object):
    def __init__(self, net, layer, x1, y1, x2, y2, hw):
        self.net, self.layer = net, layer
        self.x1, self.y1, self.x2, self.y2, self.hw = x1, y1, x2, y2, hw

    def layers(self):
        return ["F" if self.layer == "F.Cu" else "B"]


class Via(object):
    def __init__(self, net, x, y, r):
        self.net, self.x, self.y, self.r = net, x, y, r

    def layers(self):
        return ["F", "B"]


def load(path):
    text = open(path, encoding="utf-8").read()
    pads, segs, vias = [], [], []
    board = pcbnew.LoadBoard(path)
    for i in range(len(board.GetFootprints())):
        for pad in board.GetFootprints()[i].Pads():
            b = pad.GetBoundingBox()
            pads.append(Pad(pad.GetNetname(),
                            (b.GetX() / 1e6, b.GetY() / 1e6, b.GetRight() / 1e6, b.GetBottom() / 1e6),
                            pad.IsOnLayer(pcbnew.F_Cu), pad.IsOnLayer(pcbnew.B_Cu)))
    for m in re.finditer(r"\(segment\s+\(start ([\d.-]+) ([\d.-]+)\)\s+\(end ([\d.-]+) ([\d.-]+)\)"
                         r"\s+\(width ([\d.-]+)\)\s+\(layer \"([^\"]+)\"\)\s+\(net \"([^\"]*)\"\)", text):
        x1, y1, x2, y2, w, lay, net = m.groups()
        segs.append(Seg(net, lay, float(x1), float(y1), float(x2), float(y2), float(w) / 2))
    for m in re.finditer(r"\(via\s+\(at ([\d.-]+) ([\d.-]+)\)\s+\(size ([\d.-]+)\)"
                         r"[\s\S]{0,140}?\(net \"([^\"]*)\"\)", text):
        x, y, s, net = m.groups()
        vias.append(Via(net, float(x), float(y), float(s) / 2))
    return board, pads, segs, vias


def touches(a, b):
    if isinstance(a, Pad) and isinstance(b, Pad):
        return rect_gap(a.box, b.box) <= EPS
    if isinstance(a, Pad) and isinstance(b, Seg):
        return (a.on_f if b.layer == "F.Cu" else a.on_b) and \
            rect_seg(a.box, (b.x1, b.y1), (b.x2, b.y2)) <= b.hw + EPS
    if isinstance(a, Seg) and isinstance(b, Pad):
        return touches(b, a)
    if isinstance(a, Pad) and isinstance(b, Via):
        return rect_gap(a.box, (b.x - b.r, b.y - b.r, b.x + b.r, b.y + b.r)) <= EPS
    if isinstance(a, Via) and isinstance(b, Pad):
        return touches(b, a)
    if isinstance(a, Seg) and isinstance(b, Seg):
        return a.layer == b.layer and \
            seg_seg((a.x1, a.y1), (a.x2, a.y2), (b.x1, b.y1), (b.x2, b.y2)) <= a.hw + b.hw + EPS
    if isinstance(a, Seg) and isinstance(b, Via):
        return pt_seg(b.x, b.y, a.x1, a.y1, a.x2, a.y2) <= a.hw + b.r + EPS
    if isinstance(a, Via) and isinstance(b, Seg):
        return touches(b, a)
    return math.hypot(a.x - b.x, a.y - b.y) <= a.r + b.r + EPS


def components(items):
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if touches(items[i], items[j]):
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
    groups = {}
    for i in range(len(items)):
        groups.setdefault(find(i), []).append(i)
    return [v for v in groups.values()]


def anchors(items, idxs):
    out, seen = [], set()
    for i in idxs:
        it = items[i]
        if isinstance(it, Pad):
            cs = [(it.cx, it.cy, l) for l in it.layers()]
        elif isinstance(it, Seg):
            cs = [(it.x1, it.y1, l) for l in it.layers()] + \
                 [(it.x2, it.y2, l) for l in it.layers()]
        else:
            cs = [(it.x, it.y, l) for l in it.layers()]
        for c in cs:
            k = (round(c[0], 3), round(c[1], 3), c[2])
            if k not in seen:
                seen.add(k)
                out.append(c)
    return out


def main():
    board, pads, segs, vias = load(PCB)
    _, pr.obstacles, _, _ = pr.load_model(PCB)
    print("copper: %d pads, %d segments, %d vias" % (len(pads), len(segs), len(vias)))

    # Only touch nets that are still split. Read the CURRENT DRC report - the
    # old code had three hardcoded stale paths, so it silently inspected the
    # wrong nets (and reported "already connected" for every net it skipped).
    rpt = sys.argv[2] if len(sys.argv) > 2 else "/tmp/drc_now.rpt"
    nets = set()
    if os.path.exists(rpt):
        txt = open(rpt, encoding="utf-8").read()
        for blk in re.split(r"\n(?=\[)", txt):
            if not blk.startswith("[unconnected_items"):
                continue
            for m in re.finditer(r"\[([A-Za-z0-9_+\-]{2,})\]", blk):
                name = m.group(1)
                if name not in ("unconnected_items", "F.Cu", "B.Cu"):
                    nets.add(name)
    print("DRC 报告: %s" % rpt)
    print("KiCad 未连接数(改前): %d" % board.GetConnectivity().GetUnconnectedCount(False))
    print("nets to inspect: %s" % (", ".join(sorted(nets)) or "-"))

    def blocker(a, b, own, layer):
        hw = TRACK_W / 2
        for s in segs:
            if s.net == own or s.layer != layer:
                continue
            if seg_seg(a, b, (s.x1, s.y1), (s.x2, s.y2)) < hw + CLEARANCE + s.hw:
                return True
        for v in vias:
            if v.net == own:
                continue
            if pt_seg(v.x, v.y, a[0], a[1], b[0], b[1]) < hw + CLEARANCE + v.r:
                return True
        box = (min(a[0], b[0]) - hw - CLEARANCE, min(a[1], b[1]) - hw - CLEARANCE,
               max(a[0], b[0]) + hw + CLEARANCE, max(a[1], b[1]) + hw + CLEARANCE)
        for p in pads:
            if p.net == own or p.net == "":
                continue
            if not (p.on_f if layer == "F.Cu" else p.on_b):
                continue
            if rect_gap(box, p.box) < 0:
                return True
        return False

    def path_clear(pts, own, layer):
        for p, q in zip(pts, pts[1:]):
            if p == q:
                continue
            if not (BOARD[0] <= p[0] <= BOARD[2] and BOARD[1] <= p[1] <= BOARD[3] and
                    BOARD[0] <= q[0] <= BOARD[2] and BOARD[1] <= q[1] <= BOARD[3]):
                return False
            if blocker(p, q, own, layer):
                return False
        return True

    def via_ok(x, y, own):
        r = VIA_D / 2
        if not (BOARD[0] < x < BOARD[2] and BOARD[1] < y < BOARD[3]):
            return False
        box = (x - r, y - r, x + r, y + r)
        for s in segs:
            if s.net == own:
                continue
            if rect_seg(box, (s.x1, s.y1), (s.x2, s.y2)) < CLEARANCE + s.hw:
                return False
        for v in vias:
            if v.net == own:
                continue
            if math.hypot(x - v.x, y - v.y) < r + CLEARANCE + v.r:
                return False
        for p in pads:
            if p.net == own or p.net == "":
                continue
            if rect_gap(box, p.box) < CLEARANCE:
                return False
        return True

    def route_pair(a, b, own):
        """A* over patch_routes' grid. The start must be on the front layer, so a
        back-layer anchor gets a via dropped right on it (a via in the own net's
        copper is normal practice)."""
        width = pr.WIDTH_BY_NET.get(own, pr.WIDTH_DEFAULT)
        half = width / 2.0
        extra = []
        for pt in (a, b):
            if pt[2] == "B":
                if not via_ok(pt[0], pt[1], own):
                    return None
                extra.append((pt[0], pt[1]))
        grids, hard, cols, rows = pr.build_grid(pr.obstacles, own, half)
        pr.open_escape(grids, hard, cols, rows, a[0], a[1], (b[0], b[1]))
        pr.open_escape(grids, hard, cols, rows, b[0], b[1], (a[0], a[1]))

        def cell(pt):
            c = pr.free_cell(grids, cols, rows, pt[0], pt[1], 0)
            if c is None:
                c = (int(round(pt[0] / pr.GRID)), int(round(pt[1] / pr.GRID)))
            return (max(0, min(cols - 1, c[0])), max(0, min(rows - 1, c[1])))

        path = pr.astar(grids, cols, rows, cell(a), cell(b))
        if not path:
            return None
        runs, vias = pr.path_to_copper(path)
        return [(("F.Cu" if li == 0 else "B.Cu"), pts) for li, pts in runs], list(vias) + extra, width

    by_net = {}
    for s in segs:
        by_net.setdefault(s.net, []).append(s)
    for p in pads:
        by_net.setdefault(p.net, []).append(p)
    for v in vias:
        by_net.setdefault(v.net, []).append(v)

    # The pour nets are carried by the zone fills, which this analysis does not
    # model; their stitching is pours.py's job. Only signals are mop-up work.
    POUR_NETS = {"GND", "VBAT", "VBAT_RAW", "VBAT_FUSED"}
    out = []
    for net in sorted(nets - POUR_NETS):
        items = by_net.get(net, [])
        if len(items) < 2:
            continue
        comps = components(items)
        if len(comps) < 2:
            print("  %-12s 已连通 ✓" % net)
            continue
        print("  %-12s %d 块铜" % (net, len(comps)))
        guard = 0
        while True:
            comps = components(items)
            if len(comps) < 2 or guard > 12:
                break
            guard += 1
            best = None
            for i in range(len(comps)):
                for j in range(i + 1, len(comps)):
                    ai, aj = anchors(items, comps[i]), anchors(items, comps[j])
                    pairs = sorted(((math.hypot(a[0] - b[0], a[1] - b[1]), a, b)
                                    for a in ai for b in aj), key=lambda t: t[0])[:MAX_PAIRS]
                    for d, a, b in pairs:
                        if best is None or d < best[0]:
                            best = (d, a, b)
            if best is None:
                break
            d, a, b = best
            got = route_pair(a, b, net)
            if not got:
                print("     最近的一对相距 %.2f mm,A* 也走不通 ✗" % d)
                break
            runs, newvias, width = got
            for layer, pts in runs:
                for p, q in zip(pts, pts[1:]):
                    if p == q:
                        continue
                    segs.append(Seg(net, layer, p[0], p[1], q[0], q[1], width / 2))
                    items.append(segs[-1])
                    pr.obstacles.append(pr.Obj(min(p[0], q[0]) - width / 2,
                                               min(p[1], q[1]) - width / 2,
                                               max(p[0], q[0]) + width / 2,
                                               max(p[1], q[1]) + width / 2,
                                               net, "track", ("seg", p[0], p[1], q[0], q[1])))
                    out.append(("\t(segment\n\t\t(start %.4f %.4f)\n\t\t(end %.4f %.4f)\n"
                                "\t\t(width %.2f)\n\t\t(layer \"%s\")\n\t\t(net \"%s\")\n\t\t(uuid \"%s\")\n\t)")
                               % (p[0], p[1], q[0], q[1], width, layer, net, uuid.uuid4()))
            for vx, vy in newvias:
                vias.append(Via(net, vx, vy, VIA_D / 2))
                items.append(vias[-1])
                pr.obstacles.append(pr.Obj(vx - VIA_D / 2, vy - VIA_D / 2,
                                           vx + VIA_D / 2, vy + VIA_D / 2,
                                           net, "via", ("circle", vx, vy, VIA_D / 2)))
                out.append(("\t(via\n\t\t(at %.4f %.4f)\n\t\t(size %.2f)\n\t\t(drill %.2f)\n"
                            "\t\t(layers \"F.Cu\" \"B.Cu\")\n\t\t(net \"%s\")\n\t\t(uuid \"%s\")\n\t)")
                           % (vx, vy, VIA_D, VIA_DRILL, net, uuid.uuid4()))
        left = len(components(items))
        print("     -> 剩 %d 块" % left)

    print("pieces added: %d" % len(out))
    if out and not DRY:
        text = open(PCB, encoding="utf-8").read()
        anchor = text.rfind("\n\t(segment\n")
        end = text.find("\n\t)", anchor) + len("\n\t)")
        open(PCB, "w", encoding="utf-8").write(text[:end] + "\n".join(out) + text[end:])
        print("written to %s" % PCB)
    # Ground truth from KiCad itself, not our own geometry tolerance.
    chk = pcbnew.LoadBoard(PCB)
    print("KiCad 未连接数(改后): %d" % chk.GetConnectivity().GetUnconnectedCount(False))


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
