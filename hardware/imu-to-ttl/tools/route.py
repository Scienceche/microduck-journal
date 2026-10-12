#!/usr/bin/env python3
"""Route the board with Freerouting (headless).

Specctra round trip: pcbnew exports the DSN (net classes, pads, outline, rules),
Freerouting routes it, pcbnew imports the resulting SES back into the board.

Usage:  python3 tools/route.py [board.kicad_pcb] [--passes N]
"""

import os
import subprocess
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PCB = os.path.join(PROJ, "imu_to_ttl.kicad_pcb")
DSN = os.path.join(PROJ, "analysis", "imu_to_ttl.dsn")
SES = os.path.join(PROJ, "analysis", "imu_to_ttl.ses")
if "--continue" in sys.argv:
    SES = os.path.join(PROJ, "analysis", "imu_to_ttl_finish.ses")
JAR = os.path.expanduser("~/.local/bin/freerouting.jar")
JAVA = os.path.expanduser("~/jre21/jdk-21.0.12.1+1-jre/bin/java.exe")
if not os.path.isfile(JAVA):
    JAVA = "java"
PASSES = "20"

argv = sys.argv[1:]
if "--passes" in argv:
    i = argv.index("--passes")
    PASSES = argv[i + 1]          # consume the value so it is not read as a path
    argv = argv[:i] + argv[i + 2:]
args = [a for a in argv if not a.startswith("--")]
if args:
    PCB = args[0]

os.makedirs(os.path.dirname(DSN), exist_ok=True)

print("board  : %s" % PCB)
board = pcbnew.LoadBoard(PCB)


def copper_counts(path):
    """KiCad 10's SWIG iterators are broken on Python 3.14, so count in the file."""
    text = open(path, encoding="utf-8").read()
    return (text.count("\n\t(segment"), text.count("\n\t(via"))


seg, via = copper_counts(PCB)
track_count = seg + via
print("existing copper: %d segments, %d vias" % (seg, via))
CONTINUE = "--continue" in sys.argv
if track_count and not CONTINUE:
    print("refusing to route a board that already has copper - clear it first")
    print("(pass --continue to route the remaining ratsnest on top of it)")
    sys.exit(1)

print("exporting DSN ...")
if not pcbnew.ExportSpecctraDSN(board, DSN):
    sys.exit("DSN export failed")
print("  %s (%.1f kB)" % (DSN, os.path.getsize(DSN) / 1024.0))

print("running Freerouting (max %s passes) ..." % PASSES)
proc = subprocess.run(
    [JAVA, "-jar", JAR, "-de", DSN, "-do", SES, "-mp", str(PASSES), "-dr", "0"],
    capture_output=True, text=True, timeout=1800)
tail = [l for l in proc.stdout.splitlines() if l.strip()][-6:]
for line in tail:
    print("  " + line)
if proc.returncode != 0:
    print(proc.stderr[-800:])
    sys.exit("freerouting failed with %d" % proc.returncode)
if not os.path.exists(SES):
    sys.exit("no SES produced")

print("importing SES ...")
if not pcbnew.ImportSpecctraSES(board, SES):
    sys.exit("SES import failed")
board.BuildListOfNets()
board.Save(PCB)

seg, via = copper_counts(PCB)
print("routed: %d segments, %d vias" % (seg, via))
print("saved %s" % PCB)
