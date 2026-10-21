#!/usr/bin/env python3
"""Add the current-carrying pours and fill them.

The servo rail is the whole point of this board: battery in on J1, out to the
neck and both legs. `VBAT` therefore gets a real copper plane on the top layer
along that path, and GND gets a plane on both layers everywhere else, so the
5 A fuse in front of it is never limited by a trace width.

Fill order matters: the VBAT zone (higher priority) is placed after the GND
zones and wins the overlap, so the power path is a solid plane rather than a
GND island with a hole in it.

Usage:  python3 tools/pours.py [board.kicad_pcb]
"""

import os
import re
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJ, "imu_to_ttl.kicad_pcb")

BOARD_L, BOARD_W = 36.0, 30.0   # v3.7: x=fore-aft, y=across
EDGE_CLEARANCE = 0.3
MIN_THICKNESS = 0.25

# top-side power path: battery input column plus the band the three servo
# connectors sit in (J1 -> J3/J4 on top, then down the right edge to J5)
# one L-shaped polygon: battery column -> top band -> right column down to J5
VBAT_POLY = [
    # v3.7 center band: Q1 drains + C1/C2/D1 + C15/C16 + J3.2 (neck port)
    (5.0, 3.0), (27.0, 3.0), (27.0, 27.0), (5.0, 27.0),
]

# The battery-input path (J1 -> F1) carries *everything* the board distributes,
# so it gets its own copper, not a 0.4 mm trace. Measured before: 57 mOhm over
# 46.5 mm - at 3 A that is a 171 mV drop and a trace far over 60 C.
#
# The route is boxed in: VBAT's plane above it, the bus traces below it. So this
# zone is drawn over the free 3 mm strip along the top edge (no components there,
# see the mechanical keep-out) and along the two approach runs, at a priority
# above the VBAT zone. It must never reach Q1's drain pads (VBAT_FUSED): a bridge
# there would bypass the fuse.
VBAT_RAW_POLYS = [
    # v3.7 rear column: J1 pad1 (VBAT_RAW @2.3,19.3) + F1 (3.5,21.5)
    [(0.7, 12.0), (5.2, 12.0), (5.2, 18.5), (0.7, 18.5)],
]


def mm(v):
    return pcbnew.FromMM(v)


def add_zone(board, layer, net, priority, corners, name):
    zone = pcbnew.ZONE(board)
    zone.SetLayer(layer)
    zone.SetNetCode(net.GetNetCode())
    zone.SetZoneName(name)
    zone.SetAssignedPriority(priority)
    zone.SetLocalClearance(mm(0.25))
    zone.SetMinThickness(mm(MIN_THICKNESS))
    # solid connections: a dense board has no room for thermal spokes, and
    # solid copper is better for the 5 A rail anyway
    zone.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
    # NOTE: ISLAND_REMOVAL_MODE_ALWAYS was tried and reverted - it also deletes
    # fill regions that reach the net through the *other layer's* plane, which
    # left 4 pads unconnected. The default mode keeps them.
    outline = zone.Outline()
    outline.NewOutline()
    for x, y in corners:
        outline.Append(mm(x), mm(y))
    board.Add(zone)
    return zone


def drop_redundant_power_tracks(path):
    """GND is carried by the two pours and VBAT by the top-side power plane, so
    their *front-layer* traces only eat routing channels. Bottom-layer power
    traces stay: they are the connection between the planes and the back-side
    parts."""
    text = open(path, encoding="utf-8").read()
    total = 0
    for net in ("GND", "VBAT"):
        pat = re.compile(
            r"\n\t\(segment\n"
            r"\t\t\(start [^\n]*\)\n"
            r"\t\t\(end [^\n]*\)\n"
            r"\t\t\(width [^\n]*\)\n"
            r"\t\t\(layer \"F\.Cu\"\)\n"
            r"\t\t\(net \"" + net + r"\"\)\n"
            r"\t\t\(uuid [^\n]*\)\n"
            r"\t\)", re.S)
        text, n = pat.subn("", text)
        total += n
        if n:
            print("  dropped %d F.Cu %s segments" % (n, net))
    open(path, "w", encoding="utf-8").write(text)
    return total


GND_SEGMENT = re.compile(
    r"\n\t\(segment\n"
    r"\t\t\(start [^\n]*\)\n"
    r"\t\t\(end [^\n]*\)\n"
    r"\t\t\(width [^\n]*\)\n"
    r"\t\t\(layer [^\n]*\)\n"
    r"\t\t\(net \"GND\"\)\n"
    r"\t\t\(uuid [^\n]*\)\n"
    r"\t\)", re.S)


def drop_gnd_tracks(path):
    """A GND plane design does not want GND routed as traces - they eat the
    routing channels the remaining signals need. The two pours plus the
    stitching vias carry the net instead."""
    text = open(path, encoding="utf-8").read()
    text, n = GND_SEGMENT.subn("", text)
    open(path, "w", encoding="utf-8").write(text)
    return n


# GND stitching: the two ground planes must be one net, and the servo return
# current needs a low-impedance path across the board. Candidate spots are
# hand-picked in the gaps between parts; a candidate is skipped when any
# footprint body or routed copper is within 1 mm / 0.6 mm of it.
STITCH_CANDIDATES = [
    (2.0, 2.5), (34.0, 2.5), (2.0, 27.5), (34.0, 27.5),
    (17.0, 2.5), (17.0, 27.5), (2.0, 15.0), (34.0, 15.0),
]


def drop_stitching_vias(path, candidates):
    """Remove the stitching vias a previous run placed (same net, same spot).

    Without this a rebuild stacks a second via on every candidate: harmless to
    the netlist, but it shows up in the drill file as a duplicate hit.
    """
    text = open(path, encoding="utf-8").read()
    spots = {(round(x, 3), round(y, 3)) for x, y in candidates}
    parts, n = [], 0
    for block in re.split(r"(?=\n\t\(via\n)", text):
        m = re.match(r"\n\t\(via\n\t\t\(at ([\d.-]+) ([\d.-]+)\)", block)
        if m and (round(float(m.group(1)), 3), round(float(m.group(2)), 3)) in spots \
                and '(net "GND")' in block[:400]:
            n += 1
            continue
        parts.append(block)
    if n:
        open(path, "w", encoding="utf-8").write("".join(parts))
    return n


def main():
    # idempotent: a rerun must not stack another via on the same spot, and the
    # removal has to happen on disk *before* pcbnew loads the board
    dropped = drop_stitching_vias(PCB, STITCH_CANDIDATES)
    if dropped:
        print("dropped %d stitching vias from a previous run" % dropped)

    # NOTE: dropping the autorouter's GND/VBAT *tracks* was tried and reverted:
    # without them the DRC reports the power pads as unconnected - the pours and
    # the pads' fill rules do not substitute for the copper the router laid.
    board = pcbnew.LoadBoard(PCB)
    nets = board.GetNetInfo()
    gnd = nets.GetNetItem("GND")
    vbat = nets.GetNetItem("VBAT")
    if gnd is None or vbat is None:
        sys.exit("board is missing the GND or VBAT net")

    # Idempotent by *not* recreating what is already there. Removing and
    # re-adding the zones changes the fill: a second run left four VBAT pads
    # (C2.1, D1.1, R10.2) unconnected and produced extra isolated islands. A
    # rebuild starts from gen_board's empty board, so "add the missing ones" is
    # both correct in the chain and harmless on a re-run.
    existing = {z.GetZoneName() for z in board.Zones()}
    have_zones = [z for z in board.Zones()]
    if have_zones:
        print("keeping %d existing zones, adding any missing" % len(have_zones))

    board_box = [(EDGE_CLEARANCE, EDGE_CLEARANCE),
                 (BOARD_L - EDGE_CLEARANCE, EDGE_CLEARANCE),
                 (BOARD_L - EDGE_CLEARANCE, BOARD_H - EDGE_CLEARANCE),
                 (EDGE_CLEARANCE, BOARD_H - EDGE_CLEARANCE)]

    def need(name):
        return name not in existing

    if need("GND_BOTTOM"):
        add_zone(board, pcbnew.B_Cu, gnd, 0, board_box, "GND_BOTTOM")
    if need("GND_TOP"):
        add_zone(board, pcbnew.F_Cu, gnd, 0, board_box, "GND_TOP")
    if need("VBAT_TOP"):
        add_zone(board, pcbnew.F_Cu, vbat, 9, VBAT_POLY, "VBAT_TOP")

    # v3.3: B 面 VBAT 辅助走廊 —— GND 底铜(优先级0)占满整个 B 面, VBAT 的
    # 碎片(J4.2/J5.2 右缘对, C2.1/Q1.1 顶带对)无法在正面合龙。两条走廊以
    # 优先级 5 压过 GND, 填充器自动绕开 B 面信号线把锚点连成一片。
    if need("VBAT_Bottom"):
        add_zone(board, pcbnew.B_Cu, vbat, 5,
                 [(3.5, 7.6), (29.5, 7.6), (29.5, 21.8), (3.5, 21.8)],
                 "VBAT_Bottom")  # v3.7: J4/J5/J6 pad2 cluster over the gap

    # battery-input copper: priority above VBAT so the strip along the top edge
    # (free of parts, see the mechanical keep-out) becomes battery-side copper
    vbat_raw = nets.GetNetItem("VBAT_RAW")
    if vbat_raw is None:
        sys.exit("board is missing the VBAT_RAW net")
    if need("VBAT_RAW_TOP"):
        z = add_zone(board, pcbnew.F_Cu, vbat_raw, 10, VBAT_RAW_POLYS[0], "VBAT_RAW_TOP")
        for poly in VBAT_RAW_POLYS[1:]:
            outline = z.Outline()
            outline.NewOutline()
            for x, y in poly:
                outline.Append(mm(x), mm(y))

    print("zones now: %d" % len(board.Zones()))

    # --- GND stitching: place the candidates that are still clear
    track_pts = []
    blocked_segs = []
    for m in re.finditer(r"\n\t\(segment\n\t\t\(start ([\d.-]+) ([\d.-]+)\)\n\t\t\(end ([\d.-]+) ([\d.-]+)\)", open(PCB, encoding="utf-8").read()):
        blocked_segs.append(tuple(float(v) for v in m.groups()))
    for m in re.finditer(r"\n\t\(via\n\t\t\(at ([\d.-]+) ([\d.-]+)\)", open(PCB, encoding="utf-8").read()):
        track_pts.append(tuple(float(v) for v in m.groups()))
    # a stitch needs room plus clearance from any existing copper
    SAFE = 0.6

    # the mounting holes are drilled through the finished board: a stitch on top
    # of one is a manufacturing defect
    hole_pts = []
    for i in range(len(board.GetFootprints())):
        fp = board.GetFootprints()[i]
        for pad in fp.Pads():
            if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                hp = pad.GetPosition()
                hole_pts.append((hp.x / 1e6, hp.y / 1e6))

    def blocked_near(x, y):
        px, py = mm(x), mm(y)
        for hx, hy in hole_pts:
            if ((x - hx) ** 2 + (y - hy) ** 2) ** 0.5 < 2.0:
                return True
        for i in range(len(board.GetFootprints())):
            bb = board.GetFootprints()[i].GetBoundingBox(False, False)
            if (px > bb.GetX() - mm(1.0) and px < bb.GetRight() + mm(1.0)
                    and py > bb.GetY() - mm(1.0) and py < bb.GetBottom() + mm(1.0)):
                return True
        for (x1, y1, x2, y2) in blocked_segs:
            dx, dy = x2 - x1, y2 - y1
            L2 = dx * dx + dy * dy
            tt = 0.0 if L2 == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / L2))
            if ((x - (x1 + tt * dx)) ** 2 + (y - (y1 + tt * dy)) ** 2) ** 0.5 < SAFE:
                return True
        for vx, vy in track_pts:
            if ((x - vx) ** 2 + (y - vy) ** 2) ** 0.5 < SAFE:
                return True
        return False

    stitched = 0
    for x, y in STITCH_CANDIDATES:
        if blocked_near(x, y):
            continue
        via = pcbnew.PCB_VIA(board)
        via.SetPosition(pcbnew.VECTOR2I(mm(x), mm(y)))
        via.SetWidth(mm(0.6))
        via.SetDrill(mm(0.3))
        via.SetNetCode(gnd.GetNetCode())
        via.SetViaType(pcbnew.VIATYPE_THROUGH)
        board.Add(via)
        stitched += 1
    print("GND stitching vias: %d of %d candidates" % (stitched, len(STITCH_CANDIDATES)))
    # Isolated pour slivers (the DRC flags them as "isolated copper fill") are
    # not connected to anything. Always remove them; deliberately stitched
    # islands stay, because they are connected through their via.
    for z in board.Zones():
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    # fab files (CPL) are exported relative to the aux origin: put it at the
    # board's lower-left so the coordinates come out positive for JLCPCB
    board.GetDesignSettings().SetAuxOrigin(
        pcbnew.VECTOR2I(pcbnew.FromMM(0.0), pcbnew.FromMM(BOARD_W)))
    board.Save(PCB)
    print("filled and saved %s" % PCB)


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)   # _pcbnew segfaults during interpreter teardown; skip it
