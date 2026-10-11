#!/usr/bin/env python3
"""Close the last unrouted connections with a small geometric router.

The autorouter converges with a handful of connections left in the dense
MCU/IMU/debug cluster. This script reads them from a DRC report, builds a
geometric model of the board straight from the .kicad_pcb (pads, tracks, vias -
the SWIG collection accessors are unusable on this Python), and tries a short
list of candidate paths per connection: straight, two L-shapes, then the same
on the other layer, finally a path with one via. The first candidate that keeps
clearance to every foreign object wins.

Zones are deliberately not obstacles: KiCad clears them around the new copper
when they are refilled afterwards.

Usage:  python3 tools/patch_routes.py [board.kicad_pcb]
"""

import json
import math
import os
import re
import subprocess
import sys
import uuid

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
PCB = _args[0] if _args else os.path.join(PROJ, "imu_to_ttl.kicad_pcb")
DRC_JSON = "/tmp/imu_patch_drc.json"

# must match the project rule (0.20 mm): routing at 0.15 produced 243 clearance
# violations in one pass - the DRC enforces the netclass clearance, not our default
CLEARANCE = float(os.environ.get("PATCH_CLEARANCE", "0.20"))
EDGE_CLEARANCE = 0.4   # mm, track to board edge
BOARD_W, BOARD_H = 41.0, 30.0
VIA_SIZE, VIA_DRILL = 0.6, 0.3
WIDTH_DEFAULT = float(os.environ.get("PATCH_WIDTH", "0.2"))
WIDTH_BY_NET = {"VBAT": 0.4, "VBAT_RAW": 0.4, "VBAT_FUSED": 0.4, "VBAT_LOGIC": 0.4,
                "GND": 0.4, "+3V3": 0.4, "V3V3_IMU": 0.3,
                "BUS_DATA": 0.3, "BUS_LOGIC": 0.3, "BUS_TX": 0.3, "BUS_CLAMP": 0.3}


# --------------------------------------------------------------- sexp reader

def sexp(text):
    toks, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c in "()":
            toks.append(c)
            i += 1
        elif c == '"':
            j, buf = i + 1, []
            while j < n and text[j] != '"':
                if text[j] == "\\":
                    j += 1
                buf.append(text[j])
                j += 1
            toks.append("".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in " \t\r\n()":
                j += 1
            toks.append(text[i:j])
            i = j
    stack = [[]]
    for t in toks:
        if t == "(":
            new = []
            stack[-1].append(new)
            stack.append(new)
        elif t == ")":
            stack.pop()
        else:
            stack[-1].append(t)
    return stack[0]


def kids(node, tag):
    return [c for c in node if isinstance(c, list) and c and c[0] == tag]


def first(node, tag, default=None):
    k = kids(node, tag)
    return k[0] if k else default


def net_name_of(node):
    """KiCad 10 names the net on the copper item instead of numbering it."""
    n = first(node, "net")
    return n[1] if n and len(n) > 1 else ""


def fnum(x):
    try:
        return float(x)
    except Exception:
        return 0.0


# ------------------------------------------------------------ board geometry

class Obj(object):
    """Obstacle with a net code: `box` is the broad phase, `geom` the exact shape.

    The exact geometry matters: a diagonal track's axis-aligned box covers a
    large rectangle, and treating that as solid bricked up whole pockets of the
    board - which is why the router kept giving up on nets that are in fact
    routable. geom is ("seg", x1, y1, x2, y2) / ("circle", x, y, r) /
    ("rect", x0, y0, x1, y1); box still guards the cheap reject."""

    def __init__(self, x0, y0, x1, y1, net, kind, geom=None):
        self.box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        self.net = net
        self.kind = kind
        self.geom = geom if geom else ("rect",) + self.box


def _pt_seg(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    tt = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    return math.hypot(px - (x1 + tt * dx), py - (y1 + tt * dy))


def geom_dist_pt(g, px, py):
    """Distance from a point to an obstacle's exact shape."""
    if g[0] == "seg":
        return _pt_seg(px, py, g[1], g[2], g[3], g[4])
    if g[0] == "circle":
        return max(0.0, math.hypot(px - g[1], py - g[2]) - g[3])
    x0, y0, x1, y1 = g[1], g[2], g[3], g[4]
    dx = max(x0 - px, px - x1, 0.0)
    dy = max(y0 - py, py - y1, 0.0)
    return math.hypot(dx, dy)


def load_model(path):
    board = sexp(open(path, encoding="utf-8").read())[0]   # root node
    obstacles = []

    track_ends = []
    for seg in kids(board, "segment"):
        s = first(seg, "start")
        e = first(seg, "end")
        w = fnum(first(seg, "width")[1]) / 2.0
        net = net_name_of(seg)
        x1, y1, x2, y2 = fnum(s[1]), fnum(s[2]), fnum(e[1]), fnum(e[2])
        obstacles.append(Obj(min(x1, x2) - w, min(y1, y2) - w,
                             max(x1, x2) + w, max(y1, y2) + w, net, "track",
                             ("seg", x1, y1, x2, y2)))
        track_ends.append((x1, y1, net))
        track_ends.append((x2, y2, net))

    for via in kids(board, "via"):
        at = first(via, "at")
        r = fnum(first(via, "size")[1]) / 2.0
        net = net_name_of(via)
        x, y = fnum(at[1]), fnum(at[2])
        obstacles.append(Obj(x - r, y - r, x + r, y + r, net, "via",
                             ("circle", x, y, r)))

    pads = []
    for fp in kids(board, "footprint"):
        at = first(fp, "at")
        fx, fy = fnum(at[1]), fnum(at[2])
        rot = fnum(at[3]) if len(at) > 3 else 0.0
        ref = ""
        for prop in kids(fp, "property"):
            if len(prop) > 2 and prop[1] == "Reference":
                ref = prop[2]
        if not ref:
            for t in kids(fp, "fp_text"):
                if len(t) > 2 and t[1] == "reference":
                    ref = t[2]
        fp_pads = kids(fp, "pad")
        # footprint centre from its pad field (good enough for an escape direction)
        pxs = [fnum(first(pp, "at")[1]) for pp in fp_pads if first(pp, "at")]
        pys = [fnum(first(pp, "at")[2]) for pp in fp_pads if first(pp, "at")]
        fcx = fx + (sum(pxs) / len(pxs) if pxs else 0.0)
        fcy = fy + (sum(pys) / len(pys) if pys else 0.0)
        for pad in fp_pads:
            pat = first(pad, "at")
            size = first(pad, "size")
            net_node = first(pad, "net")
            if pat is None or size is None:
                continue
            px, py = fnum(pat[1]), fnum(pat[2])
            prot = fnum(pat[3]) if len(pat) > 3 else 0.0
            w, h = fnum(size[1]), fnum(size[2])
            ang = math.radians(rot + prot)
            # pad centre in board coordinates
            cx = fx + px * math.cos(math.radians(rot)) + py * math.sin(math.radians(rot))
            cy = fy - px * math.sin(math.radians(rot)) + py * math.cos(math.radians(rot))
            # rotate the pad rectangle
            hw = abs(w * math.cos(ang)) + abs(h * math.sin(ang))
            hh = abs(w * math.sin(ang)) + abs(h * math.cos(ang))
            net = net_name_of(pad)
            name = pad[1] if len(pad) > 1 else ""
            pads.append({"ref": ref, "pad": name, "x": cx, "y": cy,
                         "net": net, "fcx": fcx, "fcy": fcy,
                         "box": (cx - hw / 2, cy - hh / 2,
                                 cx + hw / 2, cy + hh / 2)})
            obstacles.append(Obj(cx - hw / 2, cy - hh / 2, cx + hw / 2, cy + hh / 2,
                                 net, "pad"))
    return board, obstacles, pads, track_ends


def geom_dist_box(g, box):
    """Distance between an obstacle's exact shape and an axis-aligned box."""
    x0, y0, x1, y1 = box
    if g[0] == "rect":
        dx = max(g[1] - x1, x0 - g[3], 0.0)
        dy = max(g[2] - y1, y0 - g[4], 0.0)
        return math.hypot(dx, dy)
    if g[0] == "circle":
        dx = max(x0 - g[1], g[1] - x1, 0.0)
        dy = max(y0 - g[2], g[2] - y1, 0.0)
        return max(0.0, math.hypot(dx, dy) - g[3])
    # segment: sample it, take the smallest point-to-box distance
    L = math.hypot(g[3] - g[1], g[4] - g[2])
    n = max(4, int(L / 0.05))
    best = 1e9
    for s in range(n + 1):
        px = g[1] + (g[3] - g[1]) * s / n
        py = g[2] + (g[4] - g[2]) * s / n
        dx = max(x0 - px, px - x1, 0.0)
        dy = max(y0 - py, py - y1, 0.0)
        best = min(best, math.hypot(dx, dy))
    return best


def box_hits(box, obstacles, net, margin):
    for ob in obstacles:
        if ob.net == net:
            continue          # same net: joining its copper is the point
        bx0, by0, bx1, by1 = ob.box
        if not (box[2] + margin > bx0 and box[0] - margin < bx1
                and box[3] + margin > by0 and box[1] - margin < by1):
            continue          # broad phase
        if geom_dist_box(ob.geom, box) < margin:
            return True
    return False


def seg_box(x1, y1, x2, y2, half_width):
    return (min(x1, x2) - half_width, min(y1, y2) - half_width,
            max(x1, x2) + half_width, max(y1, y2) + half_width)


def path_ok(pts, half_width, obstacles, net, margin):
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        if box_hits(seg_box(ax, ay, bx, by, half_width), obstacles, net, margin):
            return False
    for x, y in pts:
        if (x < EDGE_CLEARANCE or y < EDGE_CLEARANCE
                or x > BOARD_W - EDGE_CLEARANCE or y > BOARD_H - EDGE_CLEARANCE):
            return False
    return True


import heapq

GRID = 0.1             # mm per maze cell
VIA_COST = 40          # cells, discourages layer hopping
DIAG_COST = 1.41


def open_escape(grids, hard, cols, rows, x, y, away=None):
    """Clear the pad's own cells plus an escape corridor to the nearest free cell
    in each direction. A 0.5 mm-pitch LGA pad field fuses into one blocked blob
    under any clearance inflation, and the only way out of such a pad is
    perpendicular to its row - so punch those lanes through.

    Only cells that are free in `hard` (no foreign copper) may be cleared: this
    is what keeps the escape from routing across a neighbouring pad."""
    c0 = int(round(x / GRID))
    r0 = int(round(y / GRID))
    # the pad's own footprint in cells (roughly 0.4 x 0.6 mm)
    for dr in range(-3, 4):
        for dc in range(-2, 3):
            c, r = c0 + dc, r0 + dr
            if 0 <= c < cols and 0 <= r < rows and not hard[0][r][c]:
                grids[0][r][c] = 0
                grids[1][r][c] = 0
    # escape lanes: away from the part first (that is how an LGA/QFN pad is
    # actually escaped), then the four axis directions as a fallback
    dirs = []
    if away is not None:
        dx, dy = x - away[0], y - away[1]
        mag = math.hypot(dx, dy) or 1.0
        dirs.append((dx / mag, dy / mag))
    dirs += [(0.0, -1.0), (0.0, 1.0), (-1.0, 0.0), (1.0, 0.0)]
    for ux, uy in dirs:
        lane = []
        ok = True
        for step in range(1, int(8.0 / GRID)):
            c = int(round((x + ux * step * GRID) / GRID))
            r = int(round((y + uy * step * GRID) / GRID))
            if c < 0 or r < 0 or c >= cols or r >= rows:
                ok = False
                break
            inside_pad = abs(c - c0) <= 2 and abs(r - r0) <= 3
            if not inside_pad and hard[0][r][c]:
                ok = False           # foreign copper in the corridor: give up
                break
            lane.append((c, r))
            if not inside_pad and not grids[0][r][c]:
                break                # reached real open space beyond the pad
        if ok and lane:
            for lc, lr in lane:
                if not hard[0][lr][lc]:
                    grids[0][lr][lc] = 0


def build_grid(obstacles, net, half_width):
    """Occupancy grids: `grids` blocks within CLEARANCE of a foreign object, and
    `hard` blocks only the foreign copper itself (plus half the track width).

    The distinction matters for `open_escape`: an LGA/QFN pad field fuses into one
    blocked blob under the clearance inflation, and the pad's start cell has to be
    freed - but freeing a cell that holds foreign *copper* would route a track
    straight across it. So an escape lane may only clear clearance-only cells."""
    cols = int(BOARD_W / GRID) + 1
    rows = int(BOARD_H / GRID) + 1
    grids = [[bytearray(cols) for _ in range(rows)] for _ in range(2)]
    hard = [[bytearray(cols) for _ in range(rows)] for _ in range(2)]
    pad = CLEARANCE + half_width
    for ob in obstacles:
        if ob.net == net:
            continue
        x0, y0, x1, y1 = ob.box
        for grid, inflate in ((grids, pad), (hard, half_width)):
            c0 = max(0, int((x0 - inflate) / GRID))
            c1 = min(cols - 1, int((x1 + inflate) / GRID) + 1)
            r0 = max(0, int((y0 - inflate) / GRID))
            r1 = min(rows - 1, int((y1 + inflate) / GRID) + 1)
            g = ob.geom
            for r in range(r0, r1 + 1):
                row = grid[0][r]
                rowb = grid[1][r]
                py = r * GRID
                for c in range(c0, c1 + 1):
                    if geom_dist_pt(g, c * GRID, py) < inflate:
                        row[c] = 1
                        rowb[c] = 1
    # board edge
    ec = int(EDGE_CLEARANCE / GRID) + 1
    for r in range(rows):
        for c in list(range(ec)) + list(range(max(0, cols - ec), cols)):
            grids[0][r][c] = 1
            grids[1][r][c] = 1
            hard[0][r][c] = 1
            hard[1][r][c] = 1
    for r in list(range(ec)) + list(range(max(0, rows - ec), rows)):
        for c in range(cols):
            grids[0][r][c] = 1
            grids[1][r][c] = 1
            hard[0][r][c] = 1
            hard[1][r][c] = 1
    return grids, hard, cols, rows


def free_cell(grids, cols, rows, x, y, layer):
    c = int(round(x / GRID))
    r = int(round(y / GRID))
    if c < 0 or r < 0 or c >= cols or r >= rows:
        return None
    if grids[layer][r][c]:
        return None
    return (c, r)


def astar(grids, cols, rows, src, dst):
    """src/dst are (c, r). Returns a list of (c, r, layer) or None."""
    start_state = (src[0], src[1], 0)
    goal = (dst[0], dst[1])
    openq = [(0.0, start_state)]
    gscore = {start_state: 0.0}
    came = {}
    moves = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
             (1, 1, DIAG_COST), (1, -1, DIAG_COST), (-1, 1, DIAG_COST), (-1, -1, DIAG_COST)]
    seen = set()
    while openq:
        _, state = heapq.heappop(openq)
        if state in seen:
            continue
        seen.add(state)
        c, r, l = state
        if (c, r) == goal:
            path = [state]
            while state in came:
                state = came[state]
                path.append(state)
            return list(reversed(path))
        for dc, dr, cost in moves:
            nc, nr = c + dc, r + dr
            if nc < 0 or nr < 0 or nc >= cols or nr >= rows:
                continue
            if grids[l][nr][nc]:
                continue
            ns = (nc, nr, l)
            ng = gscore[state] + cost
            if ng < gscore.get(ns, 1e18):
                gscore[ns] = ng
                came[ns] = state
                h = abs(nc - goal[0]) + abs(nr - goal[1])
                heapq.heappush(openq, (ng + h, ns))
        # layer change through a via
        nl = 1 - l
        if not grids[nl][r][c]:
            ns = (c, r, nl)
            ng = gscore[state] + VIA_COST
            if ng < gscore.get(ns, 1e18):
                gscore[ns] = ng
                came[ns] = state
                h = abs(c - goal[0]) + abs(r - goal[1])
                heapq.heappush(openq, (ng + h, ns))
    return None


def simplify(pts):
    """Drop the middle points of straight runs, keeping the corners."""
    if len(pts) < 3:
        return pts
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        ax, ay = out[-1]
        bx, by = pts[i]
        cx, cy = pts[i + 1]
        if (bx - ax) * (cy - by) != (by - ay) * (cx - bx):   # direction changed
            out.append(pts[i])
    out.append(pts[-1])
    return out


def path_to_copper(path):
    """Collapse the cell path into (layer, [points]) runs, with vias between."""
    runs = []
    cur_layer = path[0][2]
    pts = [(path[0][0] * GRID, path[0][1] * GRID)]
    vias = []
    for (c, r, l) in path[1:]:
        if l != cur_layer:
            runs.append((cur_layer, simplify(pts + [(c * GRID, r * GRID)])))
            vias.append((c * GRID, r * GRID))
            cur_layer = l
            pts = [(c * GRID, r * GRID)]
        else:
            pts.append((c * GRID, r * GRID))
    runs.append((cur_layer, simplify(pts)))
    return runs, vias


# --------------------------------------------------------------------- main

def run_drc():
    subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json",
                    "--output", DRC_JSON, "--severity-error", PCB],
                   capture_output=True, timeout=600)
    return json.load(open(DRC_JSON))


# Only the short connections inside the MCU/IMU cluster go here: those are the
# ones the autorouter keeps failing on, because it never finds the perpendicular
# escape out of a 0.5 mm-pitch pad row. The long debug hauls it handles fine.
BUNDLE = ["SPI_CS", "SPI_SCK", "SPI_MISO", "SPI_MOSI", "IMU_INT1", "IMU_INT2",
          "V3V3_IMU", "SCx", "OCS_AUX", "LED_A", "+3V3", "BUS_TX", "BUS_LOGIC"]


def bundle_main():
    """Route the signal bundle pad-to-pad while the board is still empty.

    These are the connections the autorouter keeps failing on: they all start
    at the 0.5 mm-pitch IMU or the MCU and run out through crowded channels.
    Doing them first, with the whole board free, is what makes them possible.
    """
    board, obstacles, pads, track_ends = load_model(PCB)
    pcboard = pcbnew.LoadBoard(PCB)
    added, failed = 0, []
    for net in BUNDLE:
        pts = [(p["x"], p["y"], (p["fcx"], p["fcy"])) for p in pads if p["net"] == net]
        if len(pts) < 2:
            continue
        width = WIDTH_BY_NET.get(net, WIDTH_DEFAULT)
        half = width / 2.0
        code = pcboard.GetNetInfo().GetNetItem(net)
        if code is None:
            continue
        code = code.GetNetCode()
        # chain the pads: nearest-neighbour order, then maze-route consecutive
        chain = [pts.pop(0)]
        while pts:
            last = chain[-1]
            pts.sort(key=lambda q: (q[0] - last[0]) ** 2 + (q[1] - last[1]) ** 2)
            chain.append(pts.pop(0))
        ok = True
        for a, b in zip(chain, chain[1:]):
            grids, hard, cols, rows = build_grid(obstacles, net, half)
            for pt in (a, b):
                open_escape(grids, hard, cols, rows, pt[0], pt[1], pt[2])
            src = (max(0, min(cols - 1, int(round(a[0] / GRID)))),
                   max(0, min(rows - 1, int(round(a[1] / GRID)))))
            dst = (max(0, min(cols - 1, int(round(b[0] / GRID)))),
                   max(0, min(rows - 1, int(round(b[1] / GRID)))))
            grid_path = astar(grids, cols, rows, src, dst)
            if grid_path is None:
                ok = False
                break
            runs, vias = path_to_copper(grid_path)
            for layer, part in runs:
                for (x1, y1), (x2, y2) in zip(part, part[1:]):
                    if abs(x1 - x2) < 1e-6 and abs(y1 - y2) < 1e-6:
                        continue
                    tr = pcbnew.PCB_TRACK(pcboard)
                    tr.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(x1), pcbnew.FromMM(y1)))
                    tr.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(x2), pcbnew.FromMM(y2)))
                    tr.SetWidth(pcbnew.FromMM(width))
                    tr.SetLayer(layer)
                    tr.SetNetCode(code)
                    pcboard.Add(tr)
                    obstacles.append(Obj(min(x1, x2) - half, min(y1, y2) - half,
                                         max(x1, x2) + half, max(y1, y2) + half, net, "track"))
            for vx, vy in vias:
                via = pcbnew.PCB_VIA(pcboard)
                via.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(vx), pcbnew.FromMM(vy)))
                via.SetWidth(pcbnew.FromMM(VIA_SIZE))
                via.SetDrill(pcbnew.FromMM(VIA_DRILL))
                via.SetNetCode(code)
                via.SetViaType(pcbnew.VIATYPE_THROUGH)
                pcboard.Add(via)
                obstacles.append(Obj(vx - 0.3, vy - 0.3, vx + 0.3, vy + 0.3, net, "via"))
        if ok:
            added += 1
        else:
            failed.append(net)
    pcboard.Save(PCB)
    print("bundle: routed %d nets, failed %s" % (added, ", ".join(failed) or "none"))
    sys.stdout.flush()
    os._exit(0)


def main():
    if "--bundle" in sys.argv:
        bundle_main()
        return
    report = run_drc()
    missing = report.get("unconnected_items", [])
    if not missing:
        print("nothing unconnected")
        return

    board, obstacles, pads, track_ends = load_model(PCB)
    print("board model: %d pads, %d obstacles" % (len(pads), len(obstacles)))

    pcboard = pcbnew.LoadBoard(PCB)
    added, failed = 0, []
    for item in missing:
        items = item.get("items", [])
        if len(items) < 2:
            continue
        net_name = None
        for i in items:
            m = re.search(r"\[([^\]]+)\]", i.get("description", ""))
            if m:
                net_name = m.group(1)
                break
        if not net_name or net_name == "GND":
            # GND connectivity is a pour/stitching matter: routing it as traces
            # was tried and cost 20 clearance violations (the 0.1 mm maze grid
            # quantises clearance down to ~0.10 mm, under the 0.15 mm rule).
            continue
        net_code = net_name

        ends = []
        for i in items:
            px, py = i["pos"]["x"], i["pos"]["y"]
            # snap to the nearest pad or track end of the same net. The pad's
            # footprint centre travels along as the escape hint: open_escape
            # walks its lane *away* from the part, which is the only direction
            # an LGA/QFN pad can actually leave. The hint was never passed
            # before (ends were 2-tuples), so escapes were guesswork.
            best, bestd = None, 9e9
            for p in pads:
                if p["net"] != net_code:
                    continue
                d = (p["x"] - px) ** 2 + (p["y"] - py) ** 2
                if d < bestd:
                    best, bestd = (p["x"], p["y"], (p["fcx"], p["fcy"])), d
            for tx, ty, tnet in track_ends:
                if tnet != net_code:
                    continue
                d = (tx - px) ** 2 + (ty - py) ** 2
                if d < bestd:
                    best, bestd = (tx, ty, None), d
            ends.append(best if best else (px, py, None))

        width = WIDTH_BY_NET.get(net_name, WIDTH_DEFAULT)
        # Retry thinner before giving up: a 0.4 mm power-class track cannot get
        # through a dense pocket, but the same route at 0.2 mm is legal (the
        # board minimum is 0.15 mm) and often the only way in.
        # NOTE: a thinner retry (0.15/0.20 mm) was tried and removed - the DRC
        # enforces the netclass width as a *minimum*, so a thinned track is a
        # track_width error (243 of them in one go). The real fix for "cannot
        # route" was the exact obstacle geometry below.
        grid_path = None
        for try_width in (width,):
            half = try_width / 2.0
            grids, hard, cols, rows = build_grid(obstacles, net_code, half)
            src = free_cell(grids, cols, rows, ends[0][0], ends[0][1], 0)
            dst = free_cell(grids, cols, rows, ends[1][0], ends[1][1], 0)
            src = src or (int(round(ends[0][0] / GRID)), int(round(ends[0][1] / GRID)))
            dst = dst or (int(round(ends[1][0] / GRID)), int(round(ends[1][1] / GRID)))
            src = (max(0, min(cols - 1, src[0])), max(0, min(rows - 1, src[1])))
            dst = (max(0, min(cols - 1, dst[0])), max(0, min(rows - 1, dst[1])))
            # A route starts and ends inside its own pad: clear the pad's own
            # rectangle in the grid (the 0.5 mm LGA pitch leaves no free cell there)
            for pt in (ends[0], ends[1]):
                open_escape(grids, hard, cols, rows, pt[0], pt[1],
                            pt[2] if len(pt) > 2 else None)
            grid_path = astar(grids, cols, rows, src, dst)
            if grid_path is not None:
                width = try_width
                break
        if grid_path is None:
            failed.append(net_name)
            continue
        runs, vias = path_to_copper(grid_path)

        code = pcboard.GetNetInfo().GetNetItem(net_code).GetNetCode()

        def emit(layer, pts):
            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                if abs(x1 - x2) < 1e-6 and abs(y1 - y2) < 1e-6:
                    continue
                tr = pcbnew.PCB_TRACK(pcboard)
                tr.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(x1), pcbnew.FromMM(y1)))
                tr.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(x2), pcbnew.FromMM(y2)))
                tr.SetWidth(pcbnew.FromMM(width))
                tr.SetLayer(layer)
                tr.SetNetCode(code)
                pcboard.Add(tr)
                obstacles.append(Obj(min(x1, x2) - half, min(y1, y2) - half,
                                     max(x1, x2) + half, max(y1, y2) + half,
                                     net_code, "track"))

        for layer, pts in runs:
            emit(layer, pts)
        for vx, vy in vias:
            via = pcbnew.PCB_VIA(pcboard)
            via.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(vx), pcbnew.FromMM(vy)))
            via.SetWidth(pcbnew.FromMM(VIA_SIZE))
            via.SetDrill(pcbnew.FromMM(VIA_DRILL))
            via.SetNetCode(code)
            via.SetViaType(pcbnew.VIATYPE_THROUGH)
            pcboard.Add(via)
            obstacles.append(Obj(vx - VIA_SIZE / 2, vy - VIA_SIZE / 2,
                                 vx + VIA_SIZE / 2, vy + VIA_SIZE / 2, net_code, "via"))
        added += 1
        print("  patched %-12s %d runs, %d vias, %d cells"
              % (net_name, len(runs), len(vias), len(grid_path)))

    if added:
        filler = pcbnew.ZONE_FILLER(pcboard)
        filler.Fill(pcboard.Zones())
        pcboard.Save(PCB)
    print("patched %d connections, could not route %d%s"
          % (added, len(failed), (": " + ", ".join(sorted(set(failed)))) if failed else ""))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
