#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Remove GND vias flagged via_dangling by the latest DRC report, so the
next pour pass deletes the junk islands they anchor."""
import os
import re
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(PROJ, "imu_to_ttl.kicad_pcb")
RPT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(PROJ, "analysis", "drc_v38k.rpt")

t = open(RPT, encoding='utf-8').read()
pts = [(float(a), float(b)) for a, b in re.findall(
    r'\[via_dangling\][^\n]*\n(?:.*override[^\n]*\n)?\s+@\(([\d.]+) mm, ([\d.]+) mm\): Via \[GND\]', t)]
if not pts:
    print('no dangling GND vias in report')
    sys.exit(0)

board = pcbnew.LoadBoard(PCB)
ts = board.Tracks()
doomed = []
for i in range(len(ts)):
    tr = ts[i]
    if tr.GetClass() != 'PCB_VIA' or tr.GetNetname() != 'GND':
        continue
    v = tr.GetPosition()
    for px, py in pts:
        if abs(v.x / 1e6 - px) < 0.05 and abs(v.y / 1e6 - py) < 0.05:
            doomed.append(tr)
            break
for tr in doomed:
    board.Remove(tr)
board.Save(PCB)
print('removed %d dangling GND vias' % len(doomed))
sys.stdout.flush()
os._exit(0)
