#!/usr/bin/env python3
"""Clean dangling copper: snap loose track ends onto copper, prune dead stubs.

A routed board usually carries leftovers: short stubs the router abandoned, and
track ends that stop a few hundredths of a millimetre short of a via or a pad -
close enough that they look connected on screen, far enough that KiCad's
connectivity check still calls the net unconnected. This walks every track and
classifies each endpoint against same-net copper (pads, vias, other tracks):

  * already touching (<= TOUCH_MM)          -> keep
  * stopping within SNAP_MM of copper       -> extend the segment onto it
  * touching nothing                        -> dangling

A dangling segment is deleted when it is a stub, or when the net is GND (the
pours carry that net); anything else is reported rather than removed, because
deleting it could orphan a pad. Zones are refilled and DRC re-run at the end.

Usage:  python3 tools/clean_tracks.py [board.kicad_pcb] [--dry-run]
"""

import json
import math
import os
import re
import subprocess
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
PCB = _args[0] if _args else os.path.join(PROJ, "imu_to_ttl.kicad_pcb")
DRY = "--dry-run" in sys.argv

TOUCH_MM = 0.02       # already touching
SNAP_MM = 0.12        # extend an end this close onto the copper
STUB_MM = 4.0         # a dangling segment shorter than this is a stub

SEG_RE = re.compile(
    r"\n\t\(segment\n"
    r"\t\t\(start ([-\d.]+) ([-\d.]+)\)\n"
    r"\t\t\(end ([-\d.]+) ([-\d.]+)\)\n"
    r"\t\t\(width ([-\d.]+)\)\n"
    r"\t\t\(layer \"([^\"]+)\"\)\n"
    r"\t\t\(net \"([^\"]+)\"\)\n"
    r"\t\t\(uuid \"[^\"]*\"\)\n"
    r"\t\)")


def run_drc_counts(path):
    out = "/tmp/clean_tracks_drc.json"
    subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json", "--output", out,
                    "--severity-error", path], capture_output=True, timeout=600)
    d = json.load(open(out))
    return len(d.get("violations", [])), len(d.get("unconnected_items", []))


def dist_to_box(x, y, box):
    dx = max(box[0] - x, 0.0, x - box[2])
    dy = max(box[1] - y, 0.0, y - box[3])
    return math.hypot(dx, dy)


def clamp_to_box(x, y, box):
    return (min(max(x, box[0]), box[2]), min(max(y, box[1]), box[3]))


def main():
    sys.path.insert(0, HERE)
    import patch_routes as model

    _board, _obstacles, pads, _ends = model.load_model(PCB)
    print("before: %d violations, %d unconnected items" % run_drc_counts(PCB))

    pad_boxes = [(p["box"], p["net"]) for p in pads]
    text = open(PCB, encoding="utf-8").read()

    # vias and tracks straight from the file (pads come from the model above)
    via_re = re.compile(r"\n\t\(via\n\t\t\(at ([-\d.]+) ([-\d.]+)\)\n"
                        r"\t\t\(size ([-\d.]+)\)\n\t\t\(drill [^\n]*\)\n"
                        r"\t\t\(layers [^\n]*\)\n\t\t\(net \"([^\"]+)\"\)\n"
                        r"\t\t\(uuid [^\"]*\"\)\n\t\)")
    vias = []
    for m in via_re.finditer(text):
        x, y, size, net = float(m.group(1)), float(m.group(2)), float(m.group(3)), m.group(4)
        vias.append(((x - size / 2, y - size / 2, x + size / 2, y + size / 2), net))

    segs = []
    for m in SEG_RE.finditer(text):
        x1, y1, x2, y2 = (float(m.group(i)) for i in range(1, 5))
        segs.append((m.start(), m.end(), x1, y1, x2, y2, m.group(7)))

    def nearest_copper(x, y, net, own):
        """Closest same-net copper point, ignoring the segment itself."""
        best, bestd = None, 1e9
        for box, pnet in pad_boxes:
            if pnet != net:
                continue
            d = dist_to_box(x, y, box)
            if d < bestd:
                best, bestd = clamp_to_box(x, y, box), d
        for box, vnet in vias:
            if vnet != net:
                continue
            d = dist_to_box(x, y, box)
            if d < bestd:
                best, bestd = clamp_to_box(x, y, box), d
        for s in segs:
            if s[6] != net or (s[2], s[3], s[4], s[5]) == own:
                continue
            box = (min(s[2], s[4]), min(s[3], s[5]), max(s[2], s[4]), max(s[3], s[5]))
            d = dist_to_box(x, y, box)
            if d < bestd:
                best, bestd = clamp_to_box(x, y, box), d
        return best, bestd

    edits, snapped, pruned, reported = [], 0, 0, 0
    for start, end, x1, y1, x2, y2, net in segs:
        own = (x1, y1, x2, y2)
        new1, new2 = (x1, y1), (x2, y2)
        dangling = []
        for i, (px, py) in enumerate(((x1, y1), (x2, y2))):
            target, d = nearest_copper(px, py, net, own)
            if target is None or d > SNAP_MM:
                dangling.append(i)
            elif d > TOUCH_MM:
                if i == 0:
                    new1 = target
                else:
                    new2 = target
        if new1 != (x1, y1) or new2 != (x2, y2):
            body = ("\n\t(segment\n\t\t(start %.4f %.4f)\n\t\t(end %.4f %.4f)\n"
                    "\t\t(width %s)\n\t\t(layer \"%s\")\n\t\t(net \"%s\")\n"
                    "\t\t(uuid \"%s\")\n\t)")
            m = SEG_RE.match(text, start)
            uuid = re.search(r'\(uuid "([^"]*)"\)', m.group(0)).group(1)
            edits.append((start, end, body % (new1[0], new1[1], new2[0], new2[1],
                                              m.group(5), m.group(6), net, uuid)))
            snapped += 1
            continue
        length = math.hypot(x2 - x1, y2 - y1)
        if dangling:
            if length <= STUB_MM or net == "GND":
                edits.append((start, end, ""))
                pruned += 1
            else:
                reported += 1

    for start, end, repl in sorted(edits, reverse=True):
        text = text[:start] + repl + text[end:]
    if edits and not DRY:
        open(PCB, "w", encoding="utf-8").write(text)

    print("snapped ends: %d (within %.2f mm)" % (snapped, SNAP_MM))
    print("pruned stubs: %d (kept but reported: %d)" % (pruned, reported))
    if DRY:
        print("dry run: nothing written")
        return

    board2 = pcbnew.LoadBoard(PCB)
    pcbnew.ZONE_FILLER(board2).Fill(board2.Zones())
    board2.Save(PCB)
    print("after : %d violations, %d unconnected items" % run_drc_counts(PCB))
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
