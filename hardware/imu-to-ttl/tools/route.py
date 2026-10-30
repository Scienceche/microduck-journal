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
for flag in ("--passes", "--inset"):
    if flag in argv:
        i = argv.index(flag)
        val = argv[i + 1]
        if flag == "--passes":
            globals()["PASSES"] = val    # route.py reads PASSES at call time below
        argv = argv[:i] + argv[i + 2:]   # consume flag + value so neither is read as a path
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

# v3.8 strategy preprocessing (see tools/dsn_tool.py):
#   --nognd        GND never enters the router - the pours carry it
#   --inset 0.25   board-edge clearance the DSN export omits
sys.path.insert(0, HERE)
import dsn_tool
if "--nognd" in sys.argv:
    t = open(DSN, encoding="utf-8").read()
    t, ok = dsn_tool.strip_net(t, "GND")
    open(DSN, "w", encoding="utf-8").write(t)
    print("  GND stripped from DSN: %s" % ok)
if "--inset" in sys.argv and False:  # DISABLED: miter self-intersections hang FR1.9; nudge_edges.py handles edge copper instead
    i = sys.argv.index("--inset")
    mm = float(sys.argv[i + 1])
    bb = board.GetBoardEdgesBoundingBox()
    w = pcbnew.ToMM(bb.GetWidth())
    h = pcbnew.ToMM(bb.GetHeight())
    t = open(DSN, encoding="utf-8").read()
    t, ok = dsn_tool.inset_boundary(t, w, h)
    open(DSN, "w", encoding="utf-8").write(t)
    print("  boundary inset %.2fmm: %s" % (mm, ok))

CLI = ""   # v2.5.0 CLI native image crashes on KiCad DSN (upstream serialization bug) - keep 1.9 jar
STAGE = os.path.expandvars(r"%TEMP%r_stage")
if os.path.isfile(CLI):
    print("running Freerouting CLI v2.5 (max %s passes) ..." % PASSES)
    # Java 25 mangles non-ASCII paths/cwd (中文目录) - stage via an ASCII temp dir
    import shutil
    os.makedirs(STAGE, exist_ok=True)
    shutil.copyfile(DSN, os.path.join(STAGE, "job.dsn"))
    cmd = [CLI, "-de", "job.dsn", "-do", "job.ses",
           "--router.autorouter.max_passes", str(PASSES), "-mt", "11", "-l", "en"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, cwd=STAGE)
    if os.path.exists(os.path.join(STAGE, "job.ses")):
        shutil.copyfile(os.path.join(STAGE, "job.ses"), SES)
else:
    print("running Freerouting 1.9 jar (max %s passes) ..." % PASSES)
    cmd = [JAVA, "-jar", JAR, "-de", DSN, "-do", SES, "-mp", str(PASSES), "-dr", "0"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
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
