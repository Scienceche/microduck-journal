#!/usr/bin/env python3
"""Import the schematic netlist onto the PCB.

Reads the netlist exported by `kicad-cli sch export netlist --format kicadsexpr`
(parsed as s-expressions), loads every component's footprint from the KiCad
libraries plus the project's local `lib/imu_ttl.pretty`, places it, creates the
board nets and binds every pad to its net.

Placement here is only a starting position; the real floorplan comes from the
layout pass. Re-running is safe: it rebuilds the footprint set from scratch and
keeps the Edge.Cuts frame and the mounting holes that `gen_board.py` created.

Usage:  python3 tools/assign_netlist.py <netlist> [board.kicad_pcb]
"""

import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
import os as _o
KICAD_FP = next(_p for _p in [_o.environ.get("KICAD_FOOTPRINTS", ""),
    "C:/Users/c1349/AppData/Local/Programs/KiCad/10.0.7/share/kicad/footprints", "/usr/share/kicad/footprints"]
    if _p and _o.path.isdir(_p))
LOCAL_LIBS = {"imu_ttl": os.path.join(PROJ, "lib", "imu_ttl.pretty")}

NET = sys.argv[1] if len(sys.argv) > 1 else "/tmp/imu_ttl.net"
PCB = sys.argv[2] if len(sys.argv) > 2 else os.path.join(PROJ, "imu_to_ttl.kicad_pcb")


# ------------------------------------------------------------- s-expressions

def sexp(text):
    """Parse the whole file into nested lists of strings."""
    toks = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "(":
            toks.append("(")
            i += 1
        elif c == ")":
            toks.append(")")
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while j < n and text[j] != '"':
                if text[j] == "\\":
                    j += 1
                buf.append(text[j])
                j += 1
            toks.append("".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in " \t\r\n()":
                j += 1
            toks.append(text[i:j])
            i = j

    stack = [[]]
    for t in toks:
        if t == "(":
            new = []
            stack[-1].append(new)
            stack.append(new)
        elif t == ")":
            stack.pop()
        else:
            stack[-1].append(t)
    return stack[0]


def kids(node, tag):
    return [c for c in node if isinstance(c, list) and c and c[0] == tag]


def first(node, tag, default=None):
    k = kids(node, tag)
    return k[0] if k else default


def field(node, tag):
    k = first(node, tag)
    return k[1] if k and len(k) > 1 else ""


def parse_netlist(path):
    tree = sexp(open(path).read())
    export = tree[0]
    comps = []
    for c in kids(first(export, "components"), "comp"):
        comps.append({
            "ref": field(c, "ref"),
            "value": field(c, "value"),
            "footprint": field(c, "footprint"),
        })
    nets = []
    nets_node = first(export, "nets")
    for nt in kids(nets_node, "net") if nets_node else []:
        nodes = []
        for nd in kids(nt, "node"):
            nodes.append((field(nd, "ref"), field(nd, "pin")))
        nets.append((field(nt, "name"), nodes))
    return comps, nets


# ------------------------------------------------------------------- board io

def mm(v):
    return pcbnew.FromMM(v)


def strip_fp_clearance(fp):
    """KiCad库封装(TSSOP等)自带0.2局部间距, 会压过全局0.15规则 → 统一清0"""
    try:
        fp.SetLocalClearance(pcbnew.FromMM(0))
    except Exception:
        pass


def vec(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def load_footprint(fpid):
    lib, name = fpid.split(":", 1)
    path = LOCAL_LIBS.get(lib, os.path.join(KICAD_FP, lib + ".pretty"))
    fp = pcbnew.FootprintLoad(path, name)
    if fp is None:
        raise SystemExit("cannot load footprint %s from %s" % (name, path))
    return fp


def main():
    comps, nets = parse_netlist(NET)
    board = pcbnew.LoadBoard(PCB)

    # keep the mechanical footprints gen_board.py placed: they are not in the
    # netlist, so without this they are silently dropped every rebuild (the
    # mounting holes vanished that way - the keep-set still named the old
    # MH1/MH2 pair from the 45 x 22 layout)
    def is_mechanical(fp):
        # match on the *item* name: FootprintLoad() leaves the library nickname
        # empty (or a full path), so checking the nickname alone silently
        # dropped them again
        fp_id = fp.GetFPID()
        text = "%s %s" % (fp_id.GetLibNickname(), fp_id.GetLibItemName())
        return "MountingHole" in text

    fps = board.GetFootprints()
    for i in range(len(fps) - 1, -1, -1):
        fp = fps[i]
        if not is_mechanical(fp):
            board.Remove(fp)

    netinfo = {}
    for name, _nodes in nets:
        ni = pcbnew.NETINFO_ITEM(board, name)
        board.Add(ni)
        netinfo[name] = ni

    placed = {}
    x0, y0, dx, dy, per_row = 3.0, 3.0, 7.0, 5.0, 6
    for idx, c in enumerate(comps):
        fp = load_footprint(c["footprint"])
        fp.SetReference(c["ref"])
        fp.SetValue(c["value"])
        strip_fp_clearance(fp)
        fp.SetPosition(vec(x0 + (idx % per_row) * dx, y0 + ((idx // per_row) % 3) * dy))
        board.Add(fp)
        placed[c["ref"]] = fp

    bound = 0
    missing = []
    for name, nodes in nets:
        ni = netinfo[name]
        for ref, pin in nodes:
            fp = placed.get(ref)
            if fp is None:
                continue
            pad = fp.FindPadByNumber(pin)
            if pad is None:
                missing.append("%s.%s" % (ref, pin))
                continue
            pad.SetNet(ni)
            bound += 1

    board.BuildListOfNets()
    board.Save(PCB)

    print("components: %d" % len(placed))
    print("nets: %d, pad bindings: %d" % (len(nets), bound))
    if missing:
        print("unbound pads (%d): %s" % (len(missing), " ".join(missing[:15])))


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)   # _pcbnew segfaults during interpreter teardown; skip it
