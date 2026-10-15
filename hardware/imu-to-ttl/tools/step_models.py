#!/usr/bin/env python3
"""Attach the locally generated 3D models the KiCad libraries are missing.

KiCad ships no STEP model for any 2.54 mm 2-pin screw terminal, and the board's
mechanical check needs the tallest component. `lib/3d/terminal_xy308_2p.step`
is a body envelope generated from the footprint's own F.Fab outline
(5.7 x 6.6 mm) plus the terminal's rated body height (8.5 mm above the board).

The model is attached to the footprint *instance* in the board file, so a
netlist re-import cannot lose it: this runs after assignment, every build.

Usage:  python3 tools/step_models.py [board.kicad_pcb]
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJ, "imu_to_ttl.kicad_pcb")

# reference -> model file (relative to the project), plus the reason it is local
LOCAL_MODELS = {
    # v3.5: J1 is a pair of bare solder pads now (height 0) - no envelope needed;
    # the old 8.8 mm terminal block envelope would lie about the tallest part.
    "U2": ("lib/3d/lsm6dsv16x.step",
           "the IMU footprint is project-local and carries no 3D model"),
}


def main():
    text = open(PCB, encoding="utf-8").read()
    changed = 0

    for ref, (model, reason) in LOCAL_MODELS.items():
        # find the footprint block *containing* this reference: locate the
        # reference, then walk back to its enclosing (footprint
        m = re.search(r'\(property "Reference" "%s"' % ref, text)
        if not m:
            print("  %s: reference not found" % ref)
            continue
        start = text.rfind("\n\t(footprint", 0, m.start())
        if start < 0:
            print("  %s: enclosing footprint not found" % ref)
            continue
        nxt = text.find("\n\t(footprint", m.start())
        block = text[start:nxt if nxt > 0 else len(text)]
        model_expr = '${KIPRJMOD}/' + model
        if model_expr in block:
            continue
        new_block, n = re.subn(r'\(model "[^"]*"',
                               '(model "%s"' % model_expr, block, count=1)
        if n == 0:
            # no model node at all: add one before the footprint's closing paren
            close = block.rstrip().rfind(")")
            model_block = ('\t\t(model "%s"\n\t\t\t(offset (xyz 0 0 0))\n'
                           '\t\t\t(scale (xyz 1 1 1))\n\t\t\t(rotate (xyz 0 0 0))\n\t\t)\n'
                           % model_expr)
            new_block = block[:close] + model_block + block[close:]
        text = text[:start] + new_block + text[start + len(block):]
        changed += 1
        print("  %s: attached %s (%s)" % (ref, model, reason))

    if changed:
        open(PCB, "w", encoding="utf-8").write(text)
    print("local 3D models attached: %d" % changed)


if __name__ == "__main__":
    main()
