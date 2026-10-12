#!/bin/sh
# Rebuild the PCB from the schematic: frame -> netlist -> floorplan -> packing
# -> routing -> pours. Every step is deterministic; run it from hardware/.
set -e
KIPY="C:/Users/c1349/AppData/Local/Programs/KiCad/10.0.7/bin/python.exe"
cd "$(dirname "$0")/.."
NETLIST=$(mktemp /tmp/imu_ttl.XXXX.net)
kicad-cli sch export netlist --format kicadsexpr --output "$NETLIST" imu_to_ttl.kicad_sch > /dev/null
"$KIPY" tools/gen_board.py         2>/dev/null
"$KIPY" tools/assign_netlist.py "$NETLIST" 2>/dev/null
"$KIPY" tools/place_board.py       2>/dev/null
"$KIPY" tools/pack_loose.py        2>/dev/null
"$KIPY" tools/rules.py             2>/dev/null
"$KIPY" tools/route.py --passes 150 2>/dev/null
"$KIPY" tools/pours.py             2>/dev/null
rm -f "$NETLIST"
# v3 硬校验: 防止链条中途崩掉产出空板(2026-10-12 空板事故)
N=$(grep -c '(property "Reference"' imu_to_ttl.kicad_pcb)
SZ=$(wc -c < imu_to_ttl.kicad_pcb)
if [ "$N" -lt 40 ] || [ "$SZ" -lt 60000 ]; then
    echo "BUILD FAILED: footprints=$N size=$SZ (期望>40/60KB), 链条中途断裂!"
    exit 1
fi
echo "board rebuilt: imu_to_ttl.kicad_pcb (footprints=$N, ${SZ}B)"
