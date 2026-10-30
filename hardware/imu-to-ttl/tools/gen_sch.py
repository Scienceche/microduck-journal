#!/usr/bin/env python3
"""Deterministic KiCad 10 hierarchical schematic generator for IMU-to-TTL board.

Technique (proven by fanduck banana-pcb generator):
- embed symbol definitions into each sheet's lib_symbols
- connectivity via global labels placed exactly at pin connection points
- stable UUIDs so regenerating is idempotent
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

SYSTEM_SYMBOLS = Path(os.environ.get(
    "KICAD_SYMBOLS",
    "C:/Users/c1349/AppData/Local/Programs/KiCad/10.0.7/share/kicad/symbols"))
PROJECT = Path(__file__).resolve().parent.parent
LIB_SYMBOLS = PROJECT / "lib" / "imu_ttl.kicad_sym"
OUT = PROJECT

NAMESPACE = uuid.UUID("7e71ac15-cd11-4fb8-a74f-41a90727e9b1")


def stable_uuid(key: str) -> str:
    return str(uuid.uuid5(NAMESPACE, key))


def quote(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------------------------------------------------------- s-expr

def tokenize(text: str):
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "(":
            tokens.append("(")
            i += 1
        elif c == ")":
            tokens.append(")")
            i += 1
        elif c == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    j += 2
                elif text[j] == '"':
                    break
                else:
                    j += 1
            tokens.append(text[i : j + 1])
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in " \t\r\n()":
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def parse_sexpr(text: str):
    tokens = tokenize(text)
    pos = 0

    def parse():
        nonlocal pos
        assert tokens[pos] == "(", tokens[pos]
        pos += 1
        node = []
        while pos < len(tokens) and tokens[pos] != ")":
            if tokens[pos] == "(":
                node.append(parse())
            else:
                node.append(tokens[pos])
                pos += 1
        assert pos < len(tokens)
        pos += 1
        return node

    tree = parse()
    assert pos == len(tokens)
    return tree


def find_sexpr(node, name):
    """Find first child list whose head atom == name."""
    for child in node:
        if isinstance(child, list) and child and child[0] == name:
            return child
    return None


def find_all_sexpr(node, name):
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


# ---------------------------------------------------------------- symbols

def symbol_block_from_file(path: Path, name: str) -> str:
    """Return the raw (unindented) top-level symbol block (textual scan)."""
    text = path.read_text(encoding="utf-8")
    needle = f'(symbol "{name}"'
    i = text.find(needle)
    if i < 0:
        raise KeyError(f"symbol {name} not found in {path}")
    depth = 0
    instr = False
    esc = False
    j = i
    while j < len(text):
        c = text[j]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        else:
            if c == '"':
                instr = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return text[i : j + 1]
        j += 1
    raise ValueError("unterminated symbol")


def strip_pin_hide(block: str) -> str:
    """Remove (hide yes) inside pin definitions so power pins are visible."""
    return re.sub(r"(\(pin [^()]*?\(at [^()]*?\)[^()]*?\))\(hide yes\)", r"\1", block, flags=re.S)


def embed_symbol(source: Path, name: str, lib_id: str | None = None) -> str:
    """Extract symbol block, resolve extends, reindent with one tab."""
    block = symbol_block_from_file(source, name)
    m = re.search(r'\(extends "([^"]+)"\)', block)
    if m:
        base = symbol_block_from_file(source, m.group(1))
        block = base + "\n" + block
    target = lib_id or name
    block = block.replace(f'(symbol "{name}"', f'(symbol "{target}"', 1)
    block = strip_pin_hide(block)
    return "\n".join("\t" + line for line in block.splitlines())


def parse_pins(block: str):
    """Return {pin_number: dict(name, type, x, y, angle)} from all unit sub-blocks."""
    pins = {}
    # iterate every (symbol "<name>_<u>_<v>" ...) sub-block
    for m1 in re.finditer(r'\(symbol "[^"]+_\d+_\d+"', block):
        unit_start = m1.start()
        depth = 0
        instr = False
        esc = False
        j = unit_start
        while j < len(block):
            c = block[j]
            if instr:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    instr = False
            else:
                if c == '"':
                    instr = True
                elif c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        unit = block[unit_start : j + 1]
        tree = parse_sexpr(unit)
        body = tree
        for child in body:
            if not isinstance(child, list) or child[0] != "pin":
                continue
            at = find_sexpr(child, "at")
            name_n = find_sexpr(child, "name")
            num_n = find_sexpr(child, "number")
            if not at or not name_n or not num_n:
                continue
            x, y = float(at[1]), float(at[2])
            angle = int(at[3]) if len(at) > 3 else 0
            pin_type = child[1] if len(child) > 1 and isinstance(child[1], str) else "?"
            pins[str(num_n[1]).strip('"')] = {
                "name": str(name_n[1]).strip('"'),
                "type": pin_type,
                "x": x,
                "y": y,
                "angle": angle,
            }
    if not pins:
        raise ValueError("no pins found")
    return pins


# ---------------------------------------------------------------- blocks

def effects(hidden: bool = False, justify: str | None = None) -> str:
    hide = "\n\t\t\t(hide yes)" if hidden else ""
    just = f"\n\t\t\t(justify {justify})" if justify else ""
    return (
        "(effects\n"
        "\t\t(font\n"
        "\t\t\t(size 1.27 1.27)\n"
        "\t\t)"
        f"{just}{hide}\n"
        "\t)"
    )


def property_block(name: str, value: str, x: float, y: float, *, hidden: bool = False) -> str:
    return (
        f'\t(property {quote(name)} {quote(value)}\n'
        f"\t\t(at {x:g} {y:g} 0)\n"
        f"\t\t{effects(hidden=hidden)}\n"
        "\t)"
    )


@dataclass
class Component:
    lib_id: str
    reference: str
    value: str
    footprint: str
    x: float
    y: float
    pins: dict  # {pin_number: net_name}
    description: str = ""
    rotation: int = 0
    dnp: bool = False
    bom: bool = True
    mpn: str = ""
    lcsc: str = ""


def snap(v: float) -> float:
    return round(v / 1.27) * 1.27


def component_block(comp: Component, project: str, instance_prefix: str, uuid_key: str) -> str:
    comp = Component(comp.lib_id, comp.reference, comp.value, comp.footprint,
                     snap(comp.x), snap(comp.y), comp.pins, comp.description,
                     comp.rotation, comp.dnp, comp.bom, comp.mpn, comp.lcsc)
    key = f"{project}:{comp.reference}:{uuid_key}"
    symbol_uuid = stable_uuid(key)
    lines = [
        "(symbol",
        f"\t(lib_id {quote(comp.lib_id)})",
        f"\t(at {comp.x:g} {comp.y:g} {comp.rotation})",
        "\t(unit 1)",
        "\t(exclude_from_sim no)",
        f"\t(in_bom {'yes' if comp.bom else 'no'})",
        "\t(on_board yes)",
        f"\t(dnp {'yes' if comp.dnp else 'no'})",
        f"\t(uuid {quote(symbol_uuid)})",
        property_block("Reference", comp.reference, comp.x + 5.0, comp.y - 4.5),
        property_block("Value", comp.value, comp.x + 5.0, comp.y + 4.5),
        property_block("Footprint", comp.footprint, comp.x, comp.y, hidden=True),
        property_block("Datasheet", "~", comp.x, comp.y, hidden=True),
        property_block("Description", comp.description, comp.x, comp.y, hidden=True),
    ]
    if comp.mpn:
        lines.append(property_block("MPN", comp.mpn, comp.x, comp.y, hidden=True))
    if comp.lcsc:
        lines.append(property_block("LCSC", comp.lcsc, comp.x, comp.y, hidden=True))
    for pin in comp.pins:
        lines.extend(
            [
                f'\t(pin {quote(pin)}',
                f"\t\t(uuid {quote(stable_uuid(key + ':pin:' + pin))})",
                "\t)",
            ]
        )
    path = f"{instance_prefix}/{symbol_uuid}"
    lines.extend(
        [
            "\t(instances",
            f"\t\t(project {quote(project)}",
            f"\t\t\t(path {quote(path)}",
            f"\t\t\t\t(reference {quote(comp.reference)})",
            "\t\t\t\t(unit 1)",
            "\t\t\t)",
            "\t\t)",
            "\t)",
            ")",
        ]
    )
    return "\n".join("\t" + line for line in lines)


def global_label(name: str, x: float, y: float, key: str, rotation: int = 0) -> str:
    justify = "right" if rotation == 180 else "left"
    return (
        f'\t(global_label {quote(name)}\n'
        "\t\t(shape bidirectional)\n"
        f"\t\t(at {x:g} {y:g} {rotation})\n"
        "\t\t(fields_autoplaced yes)\n"
        f"\t\t{effects(justify=justify)}\n"
        f"\t\t(uuid {quote(stable_uuid('label:' + key))})\n"
        "\t)"
    )


def text_note(text: str, x: float, y: float, key: str, *, bold: bool = False) -> str:
    bold_line = " (bold yes)" if bold else ""
    return (
        f'\t(text {quote(text)}\n'
        f"\t\t(exclude_from_sim no) (at {x:g} {y:g} 0)\n"
        f"\t\t(effects (font (size 1.2 1.2){bold_line}) (justify left bottom))\n"
        f"\t\t(uuid {quote(stable_uuid('text:' + key))})\n"
        "\t)"
    )


def no_connect(x: float, y: float, key: str) -> str:
    return (
        f"\t(no_connect (at {x:g} {y:g})"
        f" (uuid {quote(stable_uuid('nc:' + key))}))"
    )


# ---------------------------------------------------------------- sheet

def schematic(
    *,
    project: str,
    title: str,
    schematic_uuid: str,
    instance_prefix: str,
    library_symbols: list[str],
    components: list[Component],
    labels: list[str],
    notes: list[str],
    nc_marks: list[str] | None = None,
    page: str = "1",
) -> str:
    embedded = "\n".join(library_symbols)
    placed = "\n".join(component_block(c, project, instance_prefix, f"{schematic_uuid}") for c in components)
    body = "\n".join([*labels, *(nc_marks or []), *notes, placed])
    return f'''(kicad_sch
\t(version 20250114)
\t(generator "eeschema")
\t(generator_version "10.0.6")
\t(uuid {quote(schematic_uuid)})
\t(paper "A4")
\t(title_block
\t\t(title {quote(title)})
\t\t(date "2026-09-16")
\t\t(rev "v1.0")
\t\t(company "IMU-to-TTL")
\t\t(comment 1 "Feetech TTL slave ID 200 + trunk power hub")
\t)
\t(lib_symbols
{embedded}
\t)
{body}
\t(embedded_fonts no)
)
'''


def sheet_instance(sheet_uuid: str, name: str, file: str, x: float, y: float, root_uuid: str) -> str:
    return f'''\t(sheet
\t\t(at {x:g} {y:g})
\t\t(size 63.5 44.45)
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(dnp no)
\t\t(fields_autoplaced yes)
\t\t(stroke
\t\t\t(width 0.1524)
\t\t\t(type solid)
\t\t)
\t\t(fill
\t\t\t(color 0 0 0 0.0000)
\t\t)
\t\t(uuid {quote(sheet_uuid)})
\t\t(property "Sheetname" {quote(name)}
\t\t\t(at {x + 0.63:g} {y - 0.71:g} 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t\t(justify left bottom)
\t\t\t)
\t\t)
\t\t(property "Sheetfile" {quote(file)}
\t\t\t(at {x + 0.63:g} {y + 45.16:g} 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t\t(justify left top)
\t\t\t)
\t\t)
\t\t(instances
\t\t\t(project {quote(project_name())}
\t\t\t\t(path {quote("/" + root_uuid + "/" + sheet_uuid)}
\t\t\t\t\t(page "1")
\t\t\t\t)
\t\t\t)
\t\t)
\t)
'''


def project_name() -> str:
    return "imu_to_ttl"



# ---------------------------------------------------------------- final parts table
# Verified against the live LCSC catalog 2026-09-16: every LCSC code in this
# table was resolved back to its catalogue MPN (jlcsearch + wmsc.lcsc.com).
PARTS_BY_REF = {
    "U1": ("STM32G031F8P6", "C529334"),
    "U2": ("LSM6DSV16XTR", "C5267406"),
    "U3": ("SN74LVC1G125DBVR", "C23654"),        # TI original (C3040625 was a -TP clone)
    "U4": ("HT7533-1", "C14289"),
    "Q1": ("AO4407C", "C469397"),                 # 30V 11.5mOhm P-MOS SOIC-8
    "F1": ("0466005.NRHF", "C57525"),             # 5A fast fuse 1206
    "F2": ("MF-NSMF020X-2", "C210358"),           # PPTC 200mA 1206
    "D1": ("SMBJ12A/TR13", "C111091"),           # 600W TVS 12V SMB
    "D2": ("SMF12A", "C2943870"),                 # TVS SOD-123FL
    "D3": ("BZT52C5V1-7-F", "C151588"),           # 5.1V zener SOD-323
    "TH1": ("PRG18BB101MB1RB", "C913703"),        # 100R PTC 0603
    "FB1": ("BLM18AG601SN1D", "C19330"),          # ferrite bead 0603
    "LED1": ("KT-0603R", "C2286"),                # red LED 0603
    "J1": ("", ""),                              # v3.1+: bare solder pads on the PCB, nothing to buy
    "J2": ("PZ254R-11-07P", "C492415"),           # v3.5: 1x7 RIGHT-ANGLE 2.54 (board has the lying footprint)
    "J3": ("B3B-PH-K-S(LF)(SN)", "C131339"),      # v3.7: JST-PH 立式, HD-1910 原装线直插
    "J4": ("S3B-PH-K-S(LF)(SN)", "C169277"),      # v3.8: JST-PH 卧贴侧出线(腿口, 板侧缘)
    "J5": ("S3B-PH-K-S(LF)(SN)", "C169277"),
    "SW1": ("MSK12C02-LG", "C431537"),            # v3.8: 电源拨动开关(串 Q1 栅极下拉支路, uA 级)
    "J6": ("B3B-PH-K-S(LF)(SN)", "C131339"),      # v3.7: 头部/备用分支口
}

PARTS_BY_VALUE = {
    "10k": ("0603WAF1002T5E", "C25804"),
    "100k": ("0603WAF1003T5E", "C25803"),
    "1M": ("0603WAF1004T5E", "C22935"),
    "1k": ("0603WAF1001T5E", "C21190"),
    "2.2k FIT": ("0603WAF2201T5E", "C4190"),
    "4.7k ALT DNP": ("0603WAF4701T5E", "C23162"),
    "10k ALT DNP": ("0603WAF1002T5E", "C25804"),
    "150R": ("0603WAF1500T5E", "C22808"),
    "0R": ("0603WAF0000T5E", "C21189"),
    "100nF 50V": ("CC0603KRX7R9BB104", "C14663"),
    "4.7uF 10V X7R": ("CL10A475KO8NNNC", "C19666"),
    "22uF 16V X5R": ("EMK325BJ226KMHP", "C359910"),   # v3.7 舵机浪涌储能(太诱 1210)
    "10uF 10V": ("CL10A106KP8NNNC", "C19702"),
    "10uF 25V X7R": ("GRM31CR71E106KA12L", "C77093"),   # 1206
    "1uF 25V X7R": ("CL10B105KA8NNNC", "C29936"),
    "PPTC 200mA": ("MF-NSMF020X-2", "C210358"),
}

PARTS_BY_REF_FOOTPRINT = {
    "J1": "imu_to_ttl:BattPad_1x02_P3.60mm_Drill1.2mm",  # must match the PCB pads (2.54mm pitch)
    "F1": "Fuse:Fuse_1206_3216Metric",
    "J4": "imu_to_ttl:CONN-TH_S3B-PH-K-S",   # v3.8: side entry
    "J5": "imu_to_ttl:CONN-TH_S3B-PH-K-S",
    "SW1": "Button_Switch_SMD:SW_SPDT_Shouhan_MSK12C02",
}


# ---------------------------------------------------------------- data

SYSTEM_LIBS = {
    "MCU_ST_STM32G0": "STM32G031F_4-6-8_Px",
    "Regulator_Linear": "HT75xx-1-SOT89",
    "74xGxx": "74LVC1G125",
    "Transistor_FET": "Q_PMOS_GDS",
    "Device": "D_TVS",
    "Device": "D_Zener",
    "Device": "R",
    "Device": "C",
    "Device": "LED",
    "Device": "Fuse",
    "Device": "Polyfuse",
    "Device": "Thermistor",
    "Device": "FerriteBead",
    "Connector": "TestPoint",
    "power": "PWR_FLAG",
}
LOCAL_LIBS = {
    "imu_ttl": ["LSM6DSV16X", "JST_PH_B3B_3P", "SWD_Header_7P", "ScrewTerminal_2P", "AO4407C"],
}


def embed_any(lib: str, name: str) -> str:
    if lib == "imu_ttl":
        return embed_symbol(LIB_SYMBOLS, name, f"imu_ttl:{name}")
    return embed_symbol(SYSTEM_SYMBOLS / f"{lib}.kicad_sym", name, f"{lib}:{name}")


def pin_positions(lib: str, name: str) -> dict:
    if lib == "imu_ttl":
        block = symbol_block_from_file(LIB_SYMBOLS, name)
    else:
        block = symbol_block_from_file(SYSTEM_SYMBOLS / f"{lib}.kicad_sym", name)
    return parse_pins(block)


def emit_sheet(
    title: str,
    sheet_uuid: str,
    root_uuid: str,
    lib_symbols: list[tuple[str, str]],
    components: list[Component],
    labels: list[tuple[str, str, str, int]],  # (net, ref, pin, rot)
    notes: list[tuple[str, float, float, str, bool]],
    nc: list[tuple[float, float, str]] | None = None,
    flags: list[str] | None = None,
    page: str = "1",
) -> str:
    """Build one sheet. labels are derived from components: (net, ref, pin)."""
    # fill MPN/LCSC/footprint from the final parts table
    fixed = []
    for comp in components:
        mpn = comp.mpn
        lcsc = comp.lcsc
        fp = comp.footprint
        if comp.reference in PARTS_BY_REF:
            mpn, lcsc = PARTS_BY_REF[comp.reference]
        elif comp.value in PARTS_BY_VALUE:
            mpn, lcsc = PARTS_BY_VALUE[comp.value]
        if comp.reference in PARTS_BY_REF_FOOTPRINT:
            fp = PARTS_BY_REF_FOOTPRINT[comp.reference]
        fixed.append(Component(comp.lib_id, comp.reference, comp.value, fp,
                               comp.x, comp.y, comp.pins, comp.description,
                               comp.rotation, comp.dnp, comp.bom, mpn, lcsc))
    components = fixed

    # verify every pin exists in the symbol
    for comp in components:
        pins = pin_positions(*comp.lib_id.split(":", 1))
        for pin in comp.pins:
            if pin not in pins:
                raise KeyError(f"{comp.reference} pin {pin} not in {comp.lib_id}")
    placed_labels = []
    for net, ref, pin in labels:
        comp = next(c for c in components if c.reference == ref)
        comp = Component(comp.lib_id, comp.reference, comp.value, comp.footprint,
                         snap(comp.x), snap(comp.y), comp.pins, comp.description,
                         comp.rotation, comp.dnp, comp.bom, comp.mpn, comp.lcsc)
        pins = pin_positions(*comp.lib_id.split(":", 1))
        p = pins[pin]
        # absolute position: schematic Y axis points down, symbol Y up
        ax = comp.x + p["x"]
        ay = comp.y - p["y"]
        # rotation: label text faces outward from symbol
        rot = 180 if p["x"] < 0 else 0
        placed_labels.append(global_label(net, ax, ay, f"{sheet_uuid}:{ref}:{pin}", rot))
    flag_components = []
    if flags:
        for i, net in enumerate(flags):
            fx = 180.0 + (i % 3) * 25.4
            fy = 20.0 + (i // 3) * 12.7
            fx, fy = snap(fx), snap(fy)
            fcomp = Component("power:PWR_FLAG", f"#FLG{page}{i+1}", "PWR_FLAG", "", fx, fy, {"1": net}, bom=False)
            flag_components.append(fcomp)
            pins = pin_positions("power", "PWR_FLAG")
            p = pins["1"]
            placed_labels.append(global_label(net, fx + p["x"], fy - p["y"], f"{sheet_uuid}:flag{i}", 180 if p["x"] < 0 else 0))
    return schematic(
        project=project_name(),
        title=title,
        schematic_uuid=sheet_uuid,
        instance_prefix=f"/{root_uuid}/{sheet_uuid}",
        library_symbols=[embed_any(lib, name) for lib, name in lib_symbols],
        components=[*components, *flag_components],
        labels=placed_labels,
        notes=[text_note(t, x, y, f"{sheet_uuid}:n:{k}", bold=(n[4] if len(n) > 4 else False))
               for n in notes for t, x, y, k in [n[:4]]],
        nc_marks=[no_connect(x, y, f"{sheet_uuid}:nc:{k}") for x, y, k in (nc or [])],
        page=page,
    )


# ---------------------------------------------------------------- sheets data

ROOT_UUID = stable_uuid("imu_to_ttl:root")

R = "Resistor_SMD:R_0603_1608Metric"
C = "Capacitor_SMD:C_0603_1608Metric"

# ---- sheet 01 power

def sheet_power(root: str) -> str:
    uuid_ = stable_uuid("imu_to_ttl:sheet:power")
    comps = [
        Component("imu_ttl:ScrewTerminal_2P", "J1", "BATT IN 2.54 2P",
                  "imu_to_ttl:BattPad_1x02_P3.60mm_Drill1.2mm",
                  40, 110, {"1": "VBAT_RAW", "2": "GND"}, "Battery input; wire side uses keyed XT30"),
        Component("Device:Fuse", "F1", "FUSE 5A 2512", "Fuse:Fuse_2512_6332Metric",
                  80, 110, {"1": "VBAT_RAW", "2": "VBAT_FUSED"}, "Main fast fuse 5A"),
        Component("imu_ttl:AO4407C", "Q1", "AO4407C", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                  120, 110, {"1": "VBAT", "2": "VBAT", "3": "VBAT", "4": "Q1_GATE",
                             "5": "VBAT_FUSED", "6": "VBAT_FUSED", "7": "VBAT_FUSED", "8": "VBAT_FUSED"},
                  "Main bus reverse protection + soft start"),
        Component("Device:R", "R9", "100k", R, 150, 145, {"1": "Q1_GATE", "2": "PWR_ON_LO"}, "Q1 gate pull-down via SW1"),
        Component("Device:R", "R10", "1M", R, 150, 165, {"1": "Q1_GATE", "2": "VBAT"}, "Q1 gate-source pull-up"),
        Component("Switch:SW_SPST", "SW1", "SW SPDT MSK12C02", "Button_Switch_SMD:SW_SPDT_Shouhan_MSK12C02",
                  150, 190, {"1": "GND", "2": "PWR_ON_LO"},
                  "v3.8 power slide: ON=R9 pulls Q1 gate low (bus on); OFF=R10 1M G-S turns Q1 off. uA path only"),
        Component("Device:C", "C14", "100nF 50V", C, 150, 125, {"1": "Q1_GATE", "2": "GND"}, "soft-start timing"),
        Component("Device:D_TVS", "D1", "SMBJ12A", "Diode_SMD:D_SMB", 120, 75, {"1": "VBAT", "2": "GND"},
                  "bus TVS 12V standoff"),
        Component("Device:C", "C1", "10uF 25V X7R", "Capacitor_SMD:C_1206_3216Metric", 155, 75, {"1": "VBAT", "2": "GND"}),
        Component("Device:C", "C15", "22uF 16V X5R", "Capacitor_SMD:C_1210_3225Metric", 175, 75, {"1": "VBAT", "2": "GND"},
                 "v3.7 servo surge bulk (dxl_hub 1000uF 的贴片化替代)"),
        Component("Device:C", "C16", "22uF 16V X5R", "Capacitor_SMD:C_1210_3225Metric", 195, 75, {"1": "VBAT", "2": "GND"}),
        Component("Device:C", "C2", "100nF 50V", C, 155, 55, {"1": "VBAT_C2", "2": "GND"}),
        Component("Device:R", "R17", "0R (jumper)", R, 175, 55, {"1": "VBAT", "2": "VBAT_C2"},
                 "0R jumper: merges the two VBAT copper islands across the NRST/C6 fence (2-layer density limit)"),
        Component("Device:Polyfuse", "F2", "PPTC 200mA", "Fuse:Fuse_1206_3216Metric", 190, 110, {"1": "VBAT", "2": "VBAT_LOGIC"},
                  "board self-power fuse; board faults never drag the bus"),
        Component("Device:D_TVS", "D2", "SMF12A", "Diode_SMD:D_SOD-123F", 190, 75, {"1": "VBAT_LOGIC", "2": "GND"}),
        Component("Device:C", "C3", "10uF 25V X7R", "Capacitor_SMD:C_0805_2012Metric", 225, 75, {"1": "VBAT_LOGIC", "2": "GND"}),
        Component("Device:C", "C4", "100nF 50V", C, 225, 55, {"1": "VBAT_LOGIC", "2": "GND"}),
        Component("Regulator_Linear:HT75xx-1-SOT89", "U4", "HT7533-1", "Package_TO_SOT_SMD:SOT-89-3",
                  260, 110, {"1": "GND", "2": "VBAT_LOGIC", "3": "+3V3"}, "30V-input 100mA LDO"),
        Component("Device:C", "C5", "10uF 10V", C, 295, 75, {"1": "+3V3", "2": "GND"}),
        Component("Device:C", "C6", "100nF 50V", C, 295, 55, {"1": "+3V3", "2": "GND"}),
        Component("Device:R", "R1", "1k", R, 295, 110, {"1": "+3V3", "2": "LED_A"}),
        Component("Device:LED", "LED1", "LED 0603", "LED_SMD:LED_0603_1608Metric", 330, 110, {"1": "LED_A", "2": "GND"}, "power indicator"),
        Component("Connector:TestPoint", "TP8", "HAT_PWR+ DNP", "TestPoint:TestPoint_Pad_D1.5mm", 80, 160, {"1": "VBAT"},
                  "DNP: dedicated +BATT pair to head if J_NECK current budget is exceeded"),
        Component("Connector:TestPoint", "TP9", "HAT_PWR- DNP", "TestPoint:TestPoint_Pad_D1.5mm", 80, 180, {"1": "GND"},
                  "DNP: dedicated GND pair to head"),
    ]
    labels = [(n, r, p) for c in comps for r, p, n in []]
    labels = []
    for c in comps:
        for p, n in c.pins.items():
            labels.append((n, c.reference, p))
    notes = [
        ("POWER & PROTECTION - battery in, trunk power hub", 25, 25, "title", True),
        ("J1 wire side: keyed XT30 on battery cable. Measure polarity before first plug.", 25, 40, "xt30"),
        ("Q1: D=VBAT_FUSED S=VBAT G via 100k/1M + 100nF -> reverse protection with ~10ms soft start.", 25, 55, "q1"),
        ("Per-branch current budget: PH 2.0 contact = 2A. J_NECK also feeds the head HAT.", 25, 70, "budget"),
        ("SW1 master power slide (v3.8): switches Q1 gate pull only - main current stays on Q1/F1.", 25, 85, "sw1"),
    ]
    return emit_sheet("Power & Protection", uuid_, root,
                      [("Device", "Fuse"), ("imu_ttl", "AO4407C"), ("Device", "R"), ("Device", "C"),
                       ("Device", "D_TVS"), ("Device", "Polyfuse"), ("Regulator_Linear", "HT75xx-1-SOT89"),
                       ("Device", "LED"), ("Connector", "TestPoint"), ("imu_ttl", "ScrewTerminal_2P"),
                       ("Switch", "SW_SPST"), ("power", "PWR_FLAG")],
                      comps, labels, notes,
                      flags=["VBAT_RAW", "VBAT_FUSED", "VBAT", "VBAT_C2", "VBAT_LOGIC", "GND"],
                      page="2")


# ---- sheet 02 mcu

def sheet_mcu(root: str) -> str:
    uuid_ = stable_uuid("imu_to_ttl:sheet:mcu")
    comps = [
        Component("MCU_ST_STM32G0:STM32G031F_4-6-8_Px", "U1", "STM32G031F8P6", "Package_SO:TSSOP-20_4.4x6.5mm_P0.65mm",
                  100, 110,
                  {"1": "NC_PB7", "2": "NC_PC14", "3": "NC_PC15", "4": "+3V3", "5": "GND", "6": "NRST",
                   "7": "IMU_INT1", "8": "NC_PA1", "9": "BUS_TX", "10": "BUS_LOGIC",
                   "11": "SPI_CS", "12": "SPI_SCK", "13": "SPI_MISO", "14": "SPI_MOSI",
                   "15": "IMU_INT2", "16": "UART1_TX", "17": "UART1_RX", "18": "SWDIO", "19": "SWCLK",
                   "20": "NC_PB3"},
                  "HSI16 only; no crystal. USART2 hardware-de not used (AUTO bus)."),
        Component("Device:C", "C7", "100nF 50V", C, 55, 150, {"1": "+3V3", "2": "GND"}, "U1 VDD decoupling"),
        Component("Device:C", "C8", "4.7uF 10V X7R", C, 80, 150, {"1": "+3V3", "2": "GND"}, "U1 VDD bulk (ST Fig.13)"),
        Component("Device:R", "R2", "10k", R, 55, 60, {"1": "+3V3", "2": "NRST"}),
        Component("Device:C", "C9", "100nF 50V", C, 80, 60, {"1": "NRST", "2": "GND"}),
        Component("imu_ttl:SWD_Header_7P", "J2", "SWD+UART+NRST", "Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical",
                  220, 110,
                  {"1": "GND", "2": "SWCLK", "3": "SWDIO", "4": "UART1_TX", "5": "UART1_RX", "6": "+3V3", "7": "NRST"},
                  "debugger must NOT power the board; 3V3 is sense-only"),
    ]
    labels = []
    for c in comps:
        for p, n in c.pins.items():
            if n.startswith("NC_"):
                continue
            labels.append((n, c.reference, p))
    nc = []
    for c in comps:
        for p, n in c.pins.items():
            if n.startswith("NC_"):
                pins = pin_positions(*c.lib_id.split(":", 1))
                nc.append((snap(c.x) + pins[p]["x"], snap(c.y) - pins[p]["y"], f"{c.reference}:{p}"))
    notes = [
        ("MCU - STM32G031F8P6 (TSSOP-20)", 25, 25, "title", True),
        ("Clock: HSI16 only. No crystal - PC14 is LSE, NOT HSE (initial-review fix).", 25, 40, "clock"),
        ("USART1 debug on pins 16/17 needs SYSCFG remap (PA11/PA12 -> PA9/PA10).", 25, 55, "uart1"),
        ("Unused pins: default analog high-Z after reset; left unconnected by design.", 25, 70, "nc"),
    ]
    return emit_sheet("MCU & Debug", uuid_, root,
                      [("MCU_ST_STM32G0", "STM32G031F_4-6-8_Px"), ("Device", "C"), ("Device", "R"),
                       ("imu_ttl", "SWD_Header_7P"), ("power", "PWR_FLAG")],
                      comps, labels, notes, nc=nc, page="3")


# ---- sheet 03 imu

def sheet_imu(root: str) -> str:
    uuid_ = stable_uuid("imu_to_ttl:sheet:imu")
    comps = [
        Component("imu_ttl:LSM6DSV16X", "U2", "LSM6DSV16XTR", "imu_ttl:ST_LGA14_3x2.5_P0.5_LSM6DSV16X_TN0018",
                  100, 110,
                  {"1": "SPI_MISO", "2": "SDx", "3": "SCx", "4": "IMU_INT1", "5": "V3V3_IMU", "6": "GND",
                   "7": "GND", "8": "V3V3_IMU", "9": "IMU_INT2", "10": "OCS_AUX", "11": "NC_SDOAUX",
                   "12": "SPI_CS", "13": "SPI_SCK", "14": "SPI_MOSI"},
                  "SFLP hardware fusion; quaternion + gyro ±500dps"),
        Component("Device:FerriteBead", "FB1", "BLM18AG601SN1D", "Inductor_SMD:L_0603_1608Metric",
                  160, 60, {"1": "+3V3", "2": "V3V3_IMU"}, "IMU supply isolation 600R@100MHz"),
        Component("Device:C", "C10", "100nF 50V", C, 160, 80, {"1": "V3V3_IMU", "2": "GND"}, "U2 VDD decoupling"),
        Component("Device:C", "C11", "100nF 50V", C, 160, 95, {"1": "V3V3_IMU", "2": "GND"}, "U2 VDD_IO decoupling"),
        Component("Device:C", "C12", "1uF 25V X7R", C, 160, 110, {"1": "V3V3_IMU", "2": "GND"}, "post-FB bulk"),
        Component("Device:R", "R3", "10k", R, 55, 80, {"1": "+3V3", "2": "SPI_CS"}, "CS idle high; no I2C strays during reset"),
        Component("Device:R", "R4", "0R", R, 55, 150, {"1": "SDx", "2": "GND"}, "pin2 SDx must be grounded (ST)"),
        Component("Device:R", "R5", "0R", R, 55, 170, {"1": "SCx", "2": "GND"}, "pin3 SCx must be grounded (ST)"),
        Component("Device:R", "R6", "0R", R, 160, 135, {"1": "OCS_AUX", "2": "+3V3"}, "OCS_Aux conservative tie (initial-review fix)"),
    ]
    labels = []
    for c in comps:
        for p, n in c.pins.items():
            if n.startswith("NC_"):
                continue
            labels.append((n, c.reference, p))
    nc = []
    for c in comps:
        for p, n in c.pins.items():
            if n.startswith("NC_"):
                pins = pin_positions(*c.lib_id.split(":", 1))
                nc.append((snap(c.x) + pins[p]["x"], snap(c.y) - pins[p]["y"], f"{c.reference}:{p}"))
    notes = [
        ("IMU - LSM6DSV16X (SPI + SFLP)", 25, 25, "title", True),
        ("Gyro full scale must be +/-500dps to match duck-control decode.", 25, 40, "gyro"),
        ("Mount axis: trunk = [+raw_z, +raw_y, -raw_x]; silkscreen X/Y/Z on board.", 25, 55, "axis"),
        ("SDO_Aux unused -> NC (marked). SDx/SCx grounded via 0R (cuttable).", 25, 70, "aux"),
    ]
    return emit_sheet("IMU", uuid_, root,
                      [("imu_ttl", "LSM6DSV16X"), ("Device", "FerriteBead"), ("Device", "C"), ("Device", "R"),
                       ("power", "PWR_FLAG")],
                      comps, labels, notes, nc=nc, flags=["V3V3_IMU"], page="4")


# ---- sheet 04 bus

def sheet_bus(root: str) -> str:
    uuid_ = stable_uuid("imu_to_ttl:sheet:bus")
    comps = [
        Component("74xGxx:74LVC1G125", "U3", "SN74LVC1G125DBVT", "Package_TO_SOT_SMD:SOT-23-5",
                  100, 110, {"1": "BUS_TX", "2": "BUS_TX", "3": "GND", "4": "BUS_LOGIC", "5": "+3V3"},
                  "AUTO: TX drives A and /OE; TX=0 pulls bus low, TX=1/idle releases"),
        Component("Device:R", "R7", "10k", R, 55, 90, {"1": "+3V3", "2": "BUS_TX"}, "reset-safe: /OE held high while PA2 high-Z"),
        Component("Device:R", "R8", "2.2k FIT", R, 150, 70, {"1": "+3V3", "2": "BUS_LOGIC"}, "default bus pull-up"),
        Component("Device:R", "R11", "4.7k ALT DNP", R, 150, 90, {"1": "+3V3", "2": "BUS_LOGIC"}, "alt pull-up; fit only one of R8/R11/R12", dnp=True),
        Component("Device:R", "R12", "10k ALT DNP", R, 150, 110, {"1": "+3V3", "2": "BUS_LOGIC"}, "alt pull-up; fit only one of R8/R11/R12", dnp=True),
        Component("Device:R", "R13", "150R", R, 200, 110, {"1": "BUS_LOGIC", "2": "BUS_CLAMP"}, "isolation to clamp node"),
        Component("Device:D_Zener", "D3", "BZT52C5V1", "Diode_SMD:D_SOD-323", 200, 80, {"1": "BUS_CLAMP", "2": "GND"},
                  "5.1V clamp (official HAT part)"),
        Component("Device:Thermistor", "TH1", "PRG18BB101MB1RB", "Resistor_SMD:R_0603_1608Metric",
                  245, 110, {"1": "BUS_CLAMP", "2": "BUS_DATA"}, "100R PTC line fault limiter (official HAT part)"),
        Component("Device:C", "C13", "100nF 50V", C, 55, 140, {"1": "+3V3", "2": "GND"}, "U3 decoupling"),
    ]
    labels = []
    for c in comps:
        for p, n in c.pins.items():
            labels.append((n, c.reference, p))
    notes = [
        ("TTL BUS - AUTO direction, no DE, no RX_EN", 25, 25, "title", True),
        ("PA3 RX is direct on BUS_LOGIC: FT 5V-tolerant pin + 150R + 5.1V zener + 100R PTC.", 25, 40, "rx"),
        ("Echo: during TX the UART sees its own bytes on RX - firmware must ignore RX while transmitting.", 25, 55, "echo"),
        ("1Mbps timing gate: SPICE sweep (2.2k/4.7k/10k x 100/200/300pF) before PCB.", 25, 70, "gate"),
    ]
    return emit_sheet("TTL Bus AUTO", uuid_, root,
                      [("74xGxx", "74LVC1G125"), ("Device", "R"), ("Device", "D_Zener"),
                       ("Device", "Thermistor"), ("Device", "C"), ("power", "PWR_FLAG")],
                      comps, labels, notes, page="5")


# ---- sheet 05 connectors

def sheet_conn(root: str) -> str:
    uuid_ = stable_uuid("imu_to_ttl:sheet:conn")
    comps = [
        Component("imu_ttl:JST_PH_B3B_3P", "J3", "J_NECK", "imu_to_ttl:CONN-TH_B3B-PH-K-S",
                  55, 110, {"1": "BUS_DATA", "2": "VBAT", "3": "GND"}, "neck chain 30->31->32->33 -> HAT (head)"),
        Component("imu_ttl:JST_PH_B3B_3P", "J4", "J_L", "imu_to_ttl:CONN-TH_S3B-PH-K-S",
                  55, 145, {"1": "BUS_DATA", "2": "VBAT", "3": "GND"}, "left leg 20-24 (v3.8 side entry)"),
        Component("imu_ttl:JST_PH_B3B_3P", "J5", "J_R", "imu_to_ttl:CONN-TH_S3B-PH-K-S",
                  55, 180, {"1": "BUS_DATA", "2": "VBAT", "3": "GND"}, "right leg 10-14 (v3.8 side entry)"),
        Component("imu_ttl:JST_PH_B3B_3P", "J6", "J_HEAD", "imu_to_ttl:CONN-TH_B3B-PH-K-S",
                  55, 215, {"1": "BUS_DATA", "2": "VBAT", "3": "GND"}, "v3.7 head/spare branch"),
        Component("Connector:TestPoint", "TP1", "VBAT", "TestPoint:TestPoint_Pad_D1.0mm", 160, 60, {"1": "VBAT"}),
        Component("Connector:TestPoint", "TP2", "GND", "TestPoint:TestPoint_Pad_D1.0mm", 160, 80, {"1": "GND"}),
        Component("Connector:TestPoint", "TP3", "+3V3", "TestPoint:TestPoint_Pad_D1.0mm", 160, 100, {"1": "+3V3"}),
        Component("Connector:TestPoint", "TP4", "BUS_DATA", "TestPoint:TestPoint_Pad_D1.0mm", 160, 120, {"1": "BUS_DATA"}, "primary scope point"),
        Component("Connector:TestPoint", "TP5", "BUS_TX", "TestPoint:TestPoint_Pad_D1.0mm", 160, 140, {"1": "BUS_TX"}),
        Component("Connector:TestPoint", "TP6", "BUS_LOGIC", "TestPoint:TestPoint_Pad_D1.0mm", 160, 160, {"1": "BUS_LOGIC"}),
        Component("Connector:TestPoint", "TP7", "NRST", "TestPoint:TestPoint_Pad_D1.0mm", 160, 180, {"1": "NRST"}),
    ]
    labels = []
    for c in comps:
        for p, n in c.pins.items():
            labels.append((n, c.reference, p))
    notes = [
        ("CONNECTORS - Feetech PH 2.0, all same pinout", 25, 25, "title", True),
        ("PINOUT: 1=SIG(TTL) 2=VCC(bus) 3=GND - HD-1910 spec A/0. NOT Dynamixel order!", 25, 40, "pinout", True),
        ("All three bus ports are one shared net; VDD/GND pass through with wide 2oz copper.", 25, 55, "bus"),
        ("Per-port current <= 2A (PH contact). J_NECK also carries the head HAT input.", 25, 70, "current"),
    ]
    return emit_sheet("Connectors & Test", uuid_, root,
                      [("imu_ttl", "JST_PH_B3B_3P"), ("Connector", "TestPoint"), ("power", "PWR_FLAG")],
                      comps, labels, notes, page="6")


# ---- root

def sheet_root() -> str:
    sheets = [
        ("Power & Protection", "power", "01_power.kicad_sch", 50, 50),
        ("MCU & Debug", "mcu", "02_mcu.kicad_sch", 50, 100),
        ("IMU", "imu", "03_imu.kicad_sch", 50, 150),
        ("TTL Bus AUTO", "bus", "04_bus.kicad_sch", 50, 200),
        ("Connectors & Test", "conn", "05_connectors.kicad_sch", 50, 250),
    ]
    body = "\n".join(
        sheet_instance(stable_uuid(f"imu_to_ttl:sheet:{short}"), name, fname, x, y, ROOT_UUID)
        for name, short, fname, x, y in sheets
    )
    return f'''(kicad_sch
\t(version 20250114)
\t(generator "eeschema")
\t(generator_version "10.0.6")
\t(uuid {quote(ROOT_UUID)})
\t(paper "A4")
\t(title_block
\t\t(title "IMU-to-TTL board")
\t\t(date "2026-09-16")
\t\t(rev "v1.0")
\t\t(company "IMU-to-TTL")
\t\t(comment 1 "Feetech TTL slave ID 200 + trunk power hub for MicroDuck HD-1910 build")
\t)
{body}
\t(embedded_fonts no)
)
'''


# ---------------------------------------------------------------- main

def main() -> None:
    root = sheet_root()
    (OUT / "imu_to_ttl.kicad_sch").write_text(root, encoding="utf-8")
    sheets = {
        "01_power.kicad_sch": sheet_power(ROOT_UUID),
        "02_mcu.kicad_sch": sheet_mcu(ROOT_UUID),
        "03_imu.kicad_sch": sheet_imu(ROOT_UUID),
        "04_bus.kicad_sch": sheet_bus(ROOT_UUID),
        "05_connectors.kicad_sch": sheet_conn(ROOT_UUID),
    }
    for fname, content in sheets.items():
        (OUT / fname).write_text(content, encoding="utf-8")
    print("generated:", "imu_to_ttl.kicad_sch", *sheets.keys())


if __name__ == "__main__":
    main()
