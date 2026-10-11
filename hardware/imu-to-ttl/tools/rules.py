#!/usr/bin/env python3
"""Write the PCB design rules and netclasses into the project file (idempotent).

Sizes follow what JLCPCB's 2-layer process supports with margin, tightened for
the one dense part on this board: the LSM6DSV16X is a 0.5 mm-pitch LGA, so its
own pads sit 0.15 mm apart and a 0.2 mm clearance rule would flag the part
itself. 0.15 mm is still twice JLC's 0.127 mm minimum.

Usage:  python3 tools/rules.py
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PRO = os.path.join(PROJ, "imu_to_ttl.kicad_pro")

CLEARANCE = 0.15
CLASSES = {
    # name:   (clearance, track width, via dia, via drill)
    "Default": (CLEARANCE, 0.20, 0.60, 0.30),
    "Power":   (CLEARANCE, 0.40, 0.70, 0.35),
    "Bus":     (CLEARANCE, 0.30, 0.60, 0.30),
}
PATTERNS = {
    "Power": ["VBAT", "VBAT_RAW", "VBAT_FUSED", "VBAT_LOGIC", "+3V3", "GND"],
    "Bus": ["BUS_DATA", "BUS_LOGIC", "BUS_TX", "BUS_CLAMP"],
}
BOARD_RULES = {
    "min_clearance": CLEARANCE,
    "min_track_width": 0.15,
    "min_through_hole_diameter": 0.3,
    "min_via_diameter": 0.55,
    "min_hole_clearance": 0.25,
    "min_hole_to_hole": 0.25,
    "min_copper_edge_clearance": 0.3,
    "min_resolved_spokes": 1,
    "min_text_height": 0.8,
    "min_text_thickness": 0.08,
    "min_silk_clearance": 0.0,
}

d = json.load(open(PRO))
ns = d.setdefault("net_settings", {})
ns["classes"] = [
    {"name": n, "clearance": c, "track_width": w, "via_diameter": vd,
     "via_drill": vdr, "bus_width": 0.25, "diff_pair_gap": 0.2,
     "diff_pair_width": 0.2, "line_style": 0, "microvia_diameter": 0.3,
     "microvia_drill": 0.1, "pcb_color": "rgba(0, 0, 0, 0.000)",
     "schematic_color": "rgba(0, 0, 0, 0.000)", "wire_width": 6}
    for n, (c, w, vd, vdr) in CLASSES.items()
]
ns["netclass_patterns"] = [
    {"netclass": cls, "pattern": net}
    for cls, nets in PATTERNS.items() for net in nets
]
rules = d.setdefault("board", {}).setdefault("design_settings", {}).setdefault("rules", {})
rules.update(BOARD_RULES)
json.dump(d, open(PRO, "w"), indent=2)
print("rules + %d netclasses + %d net patterns written"
      % (len(CLASSES), len(ns["netclass_patterns"])))
