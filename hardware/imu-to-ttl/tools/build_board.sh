#!/bin/sh
# Rebuild the PCB from the schematic: frame -> netlist -> floorplan -> packing
# -> routing -> pours. Every step is deterministic; run it from hardware/.
set -e
cd "$(dirname "$0")/.."
NETLIST=$(mktemp /tmp/imu_ttl.XXXX.net)
kicad-cli sch export netlist --format kicadsexpr --output "$NETLIST" imu_to_ttl.kicad_sch > /dev/null
python3 tools/gen_board.py         2>/dev/null
python3 tools/assign_netlist.py "$NETLIST" 2>/dev/null
python3 tools/place_board.py       2>/dev/null
python3 tools/pack_loose.py        2>/dev/null
python3 tools/rules.py             2>/dev/null
python3 tools/route.py --passes 150 2>/dev/null
python3 tools/pours.py             2>/dev/null
rm -f "$NETLIST"
echo "board rebuilt: imu_to_ttl.kicad_pcb"
