#!/bin/sh
# fast placement-only loop: fresh board -> netlist -> place -> pack -> checks
set -e
KIPY="C:/Users/c1349/AppData/Local/Programs/KiCad/10.0.7/bin/python.exe"
cd "$(dirname "$0")/.."
NET=$(mktemp /tmp/pc.XXXX.net)
kicad-cli sch export netlist --format kicadsexpr --output "$NET" imu_to_ttl.kicad_sch > /dev/null
"$KIPY" tools/gen_board_v38.py
"$KIPY" tools/assign_netlist.py "$NET"
"$KIPY" tools/place_board.py
"$KIPY" tools/pack_loose.py
"$KIPY" tools/fix_overlaps.py
rm -f "$NET"
"$KIPY" pcb_rework/check_anchors.py
