#!/usr/bin/env python3
"""Tidy the silkscreen: smaller references, no overlaps, no silk over pads.

What was wrong on the first routed board: all 49 references sat on F.SilkS at
the KiCad default 1.0 mm with a 0.15 mm stroke - on 0603 parts that is bigger
than the part itself - and the passives' own side marks were long enough to
touch their neighbours. Full-severity DRC: 17 silk-over-copper and 19
silk-overlap violations. (The values live on F.Fab, so they never printed, but
they clutter the editor; they are shrunk and hidden too.)

The pass is split on purpose:
  * text-size, value-visibility and passive-outline changes go through the
    board *file* - this KiCad build does not bind FOOTPRINT.Reference().SetVisible
    and item removal through the API is unreliable here;
  * text *positions* go through the API, because placing a label needs its real
    rendered bounding box against pads, courtyards and the board edge.

Usage:  python3 tools/silk_refs.py [board.kicad_pcb]
"""

import os
import re
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJ, "imu_to_ttl.kicad_pcb")

REF_H = REF_W = 0.80     # the board house DRC floor is 0.80 mm
REF_T = 0.12
VAL_H = VAL_W = 0.80
VAL_T = 0.12
KEEP_VALUE_ON = {"J1", "J2", "J3", "J4", "J5"}   # labelled by function
# two-terminal passives: their silk side marks overlap the neighbours and carry
# no assembly information; ICs and connectors keep theirs (pin-1 marks)
DROP_SILK_PREFIXES = ("R_0603", "R_0805", "C_0603", "C_0805", "C_1206",
                      "L_0603", "LED_0603", "D_SOD", "Fuse_1206")
EDGE_KEEPOUT = 0.35
PAD_MARGIN = 0.10
TEXT_GAP = 0.10

FP_RE = re.compile(r"\n\t\(footprint \"([^\"]+)\"[\s\S]*?\n\t\)")


def mm(v):
    return pcbnew.FromMM(v)


def shrink_text(block, name, ref):
    """Resize this footprint's Reference/Value text in the file text."""
    if name.startswith(DROP_SILK_PREFIXES):
        # drop the footprint's F.SilkS graphics (fp_line / fp_rect / fp_circle /
        # fp_poly / fp_arc), keeping F.Fab and F.CrtYd drawings
        block = re.sub(
            r"\n\t\t\(fp_(?:line|rect|circle|poly|arc)\n(?:(?!\n\t\t\(fp_(?:line|rect|circle|poly|arc|text))[\s\S])*?"
            r"\(layer \"F\.SilkS\"\)\n[\s\S]*?\n\t\t\)", "", block)
    for prop, h, w, t in (("Reference", REF_H, REF_W, REF_T), ("Value", VAL_H, VAL_W, VAL_T)):
        m = re.search(r'\n\t\t\(property "%s" "([^"]*)"\n([\s\S]*?)\n\t\t\)' % prop, block)
        if not m:
            continue
        sub = m.group(0)
        sub = re.sub(r"\(size [\d.]+ [\d.]+\)", "(size %g %g)" % (w, h), sub, count=1)
        sub = re.sub(r"\(thickness [\d.]+\)", "(thickness %g)" % t, sub, count=1)
        hide = (prop == "Value" and ref not in KEEP_VALUE_ON)
        if hide and "(hide yes)" not in sub:
            sub = sub.replace("\t\t\t(effects\n", "\t\t\t(effects\n\t\t\t\t(hide yes)\n", 1)
        block = block.replace(m.group(0), sub)
    return block


def box_of(item):
    b = item.GetBoundingBox()
    return (b.GetX() / 1e6, b.GetY() / 1e6, b.GetRight() / 1e6, b.GetBottom() / 1e6)


def overlap(a, b, margin=0.0):
    return not (a[2] + margin <= b[0] or b[2] + margin <= a[0]
                or a[3] + margin <= b[1] or b[3] + margin <= a[1])


def main():
    # ---------- pass 1: sizes, visibility, passive outlines (file text)
    text = open(PCB, encoding="utf-8").read()
    stripped = dropped = 0
    out, pos = [], 0
    for m in FP_RE.finditer(text):
        block = m.group(0)
        ref = re.search(r'\(property "Reference" "([^"]*)"', block)
        ref = ref.group(1) if ref else ""
        new = shrink_text(block, m.group(1), ref)
        if new != block:
            stripped += 1
            dropped += block.count("(fp_line") - new.count("(fp_line")
        out.append(text[pos:m.start()]); out.append(new)
        pos = m.end()
    out.append(text[pos:])
    open(PCB, "w", encoding="utf-8").write("".join(out))
    print("pass 1: %d footprints restyled, %d silk lines dropped" % (stripped, dropped))

    # ---------- pass 2: label placement (API)
    board = pcbnew.LoadBoard(PCB)
    fps = [board.GetFootprints()[i] for i in range(len(board.GetFootprints()))]
    e = board.GetBoardEdgesBoundingBox()
    board_box = (e.GetX() / 1e6 + EDGE_KEEPOUT, e.GetY() / 1e6 + EDGE_KEEPOUT,
                 e.GetRight() / 1e6 - EDGE_KEEPOUT, e.GetBottom() / 1e6 - EDGE_KEEPOUT)
    # obstacles: every pad, and every existing piece of *silkscreen* (the
    # footprints' own outlines) - the DRC checks silk against copper and against
    # other silk, so courtyards are not an obstacle but silk graphics are
    pads, silks = [], []
    for fp in fps:
        for pad in fp.Pads():
            pads.append(box_of(pad))
        try:
            items = fp.GraphicalItems()
            for i in range(len(items)):
                it = items[i]
                try:
                    if it.GetLayer() in (pcbnew.F_SilkS, pcbnew.B_SilkS):
                        silks.append(box_of(it))
                except Exception:
                    continue
        except Exception:
            pass
    silks = list(dict.fromkeys(silks))

    def own_court(fp):
        for layer in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
            ps = fp.GetCourtyard(layer)
            if ps.OutlineCount() > 0:
                b = ps.BBox()
                return (b.GetX() / 1e6, b.GetY() / 1e6, b.GetRight() / 1e6, b.GetBottom() / 1e6)
        return None

    placed, hidden = [], []
    order = sorted(fps, key=lambda f: (0 if str(f.GetReference()).startswith("J") else
                                       1 if str(f.GetReference()).startswith("U") else 2,
                                       str(f.GetReference())))
    for fp in order:
        cb = own_court(fp)
        if cb is None:
            continue
        cx, cy = (cb[0] + cb[2]) / 2, (cb[1] + cb[3]) / 2
        half_w, half_h = (cb[2] - cb[0]) / 2, (cb[3] - cb[1]) / 2
        texts = [fp.Reference()]
        try:
            if fp.Value().IsVisible():
                texts.append(fp.Value())
        except Exception:
            pass
        for txt in texts:
            size = txt.GetBoundingBox()
            tw, th = size.GetWidth() / 1e6, size.GetHeight() / 1e6
            best = None
            for row in (0, -1, 1, -2, 2):
                if best:
                    break
                for col in (0, -1, 1, -2, 2):
                    if row == 0 and col == 0:
                        continue
                    px = cx + col * (half_w + tw / 2 + TEXT_GAP)
                    py = cy + row * (half_h + th / 2 + TEXT_GAP)
                    cand = (px - tw / 2, py - th / 2, px + tw / 2, py + th / 2)
                    if not (board_box[0] <= cand[0] and cand[2] <= board_box[2]
                            and board_box[1] <= cand[1] and cand[3] <= board_box[3]):
                        continue
                    if any(overlap(cand, p, PAD_MARGIN) for p in pads):
                        continue
                    if any(overlap(cand, s, 0.0) for s in silks):
                        continue
                    if any(overlap(cand, q, TEXT_GAP) for q in placed):
                        continue
                    best = cand
                    break
            if best is None:
                hidden.append(str(fp.GetReference()))
            else:
                txt.SetPosition(pcbnew.VECTOR2I(mm((best[0] + best[2]) / 2),
                                                mm((best[1] + best[3]) / 2)))
                placed.append(best)
    board.Save(PCB)
    print("pass 2: %d labels placed, %d with no free spot: %s"
          % (len(placed), len(hidden), " ".join(hidden) if hidden else "-"))

    # ---------- pass 3: hide the labels that had nowhere to go (file text, as
    # the API does not bind SetVisible here). Fab assembly uses the CPL, and a
    # hidden reference is better than one printed across a neighbouring pad.
    if hidden:
        text = open(PCB, encoding="utf-8").read()
        n = 0
        for ref in hidden:
            m = re.search(r'\n\t\t\(property "Reference" "%s"\n([\s\S]*?)\n\t\t\)' % re.escape(ref), text)
            if not m:
                continue
            sub = m.group(0)
            if "(hide yes)" in sub:
                continue
            text = text.replace(sub, sub.replace("\t\t\t(effects\n",
                                                 "\t\t\t(effects\n\t\t\t\t(hide yes)\n", 1))
            n += 1
        open(PCB, "w", encoding="utf-8").write(text)
        print("pass 3: %d references hidden" % n)


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
