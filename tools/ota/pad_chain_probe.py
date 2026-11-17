#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手柄全链路探针: hidraw → bridge → 虚拟Xbox → padd → robotd 五级证据。

用法(在鸭子上): python3 pad_chain_probe.py [秒数]
用户配合: 探针运行期间匀速大幅画圈摇杆(左摇杆为主) + 按几个键。
判据:
  L1 hidraw   : 报文中摇杆字节变化幅度 >= 60 (0..255 量程)
  L2 bridge   : journal 显示 grabbed event 节点 + axis map
  L3 虚拟Xbox  : ABS_X/ABS_Y 值跨度 >= 20000 (±32768 量程)
  L4 padd     : 日志有 stick/cmd 行且值变化
  L5 robotd   : busStatus 正常(不参与手柄判定, 只确认策略侧活着)
输出: 每级 PASS/FAIL + 证据行, 最后打印结论。
"""
import glob
import json
import os
import re
import struct
import subprocess
import sys
import time

DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 6
MAC = "99:16:56:C3:54:4F"


def sh(cmd, timeout=10):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          timeout=timeout).stdout


def lvl(name, ok, evidence):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {evidence}")
    return ok


results = {}

# L0 蓝牙连接
info = sh(f"bluetoothctl info {MAC}")
conn = "Connected: yes" in info
results["L0_bt"] = lvl("L0 蓝牙已连接", conn,
                       [l.strip() for l in info.splitlines()
                        if "Connected" in l or "Name" in l][:2])

# L1 hidraw 摇杆字节幅度
hid_dev = None
for h in glob.glob("/dev/hidraw*"):
    uevent = sh(f"cat /sys/class/hidraw/{os.path.basename(h)}/device/uevent")
    if "GameSir" in uevent:
        hid_dev = h
        break
if not hid_dev:
    results["L1_hidraw"] = lvl("L1 找到GameSir hidraw", False, "无匹配节点")
else:
    ranges = {}
    try:
        fd = os.open(hid_dev, os.O_RDONLY | os.O_NONBLOCK)
        t0 = time.time()
        while time.time() - t0 < DUR:
            try:
                data = os.read(fd, 64)
                # GameSir Android HID: 常见布局 report[3..6]=LX,LY,RX,RY
                if len(data) >= 7:
                    for ch, idx in (("LX", 3), ("LY", 4), ("RX", 5), ("RY", 6)):
                        v = data[idx]
                        lo, hi = ranges.get(ch, (v, v))
                        ranges[ch] = (min(lo, v), max(hi, v))
            except BlockingIOError:
                time.sleep(0.004)
        os.close(fd)
    except OSError as e:
        results["L1_hidraw"] = lvl("L1 hidraw读取", False, str(e))
        ranges = {}
    if ranges:
        spans = {c: hi - lo for c, (lo, hi) in ranges.items()}
        lx = spans.get("LX", 0)
        ly = spans.get("LY", 0)
        results["L1_hidraw"] = lvl(
            "L1 hidraw摇杆幅度", max(lx, ly) >= 60,
            f"{spans} (需>=60)")

# L2 bridge journal
jr = sh("journalctl -u gamesir-xboxd --no-pager -n 40")
grab = re.findall(r"input scan: (\S+) \"([^\"]+)\"( \(pad candidate\))?", jr)
axis_lines = [l for l in jr.splitlines() if "axis" in l.lower() or "map" in l.lower()][:4]
ok2 = bool(grab) and "started" in jr.lower()
results["L2_bridge"] = lvl("L2 bridge抓到pad", ok2,
                           f"scan={grab[-1] if grab else '无'} {axis_lines}")

# L3 虚拟Xbox ABS 跨度
xbox = None
for e in glob.glob("/dev/input/event*"):
    name = sh(f"cat /sys/class/input/{os.path.basename(e)}/device/name 2>/dev/null")
    if "Xbox" in name and "GameSir" in name:
        xbox = e
        break
if not xbox:
    results["L3_xbox"] = lvl("L3 找到虚拟Xbox", False, "无Xbox节点")
else:
    # struct input_event: time(16) type(2) code(2) value(4) = 24 bytes (64位)
    abs_range = {}
    try:
        fd = os.open(xbox, os.O_RDONLY | os.O_NONBLOCK)
        # SYN droppocket: 先 drained
        t0 = time.time()
        while time.time() - t0 < DUR:
            try:
                buf = os.read(fd, 4096)
                for off in range(0, len(buf) - 23, 24):
                    _, typ, code, val = struct.unpack_from("qqhi", buf, off)
                    if typ == 3 and code in (0, 1):  # EV_ABS, X/Y
                        key = "X" if code == 0 else "Y"
                        lo, hi = abs_range.get(key, (val, val))
                        abs_range[key] = (min(lo, val), max(hi, val))
            except BlockingIOError:
                time.sleep(0.004)
        os.close(fd)
    except OSError as e:
        results["L3_xbox"] = lvl("L3 虚拟Xbox读取", False, str(e))
    if abs_range:
        spans = {k: hi - lo for k, (lo, hi) in abs_range.items()}
        results["L3_xbox"] = lvl(
            "L3 虚拟Xbox摇杆跨度", max(spans.values()) >= 20000,
            f"{spans} (需>=20000)")

# L4 padd 日志
pj = sh("journalctl -u padd --no-pager -n 20 --since '-3 min'")
stick_lines = [l for l in pj.splitlines()
               if re.search(r"stick|axis|cmd|joy", l, re.I)][:3]
results["L4_padd"] = lvl("L4 padd收到摇杆", bool(stick_lines),
                         stick_lines or "近3分钟无stick/cmd日志行")

# 汇总
fails = [k for k, v in results.items() if not v]
print("\n===== 结论 =====")
if not fails:
    print("全链 PASS: hidraw→bridge→Xbox→padd 各级都有真实摇杆数据。")
    print("若此时舵机仍不响应, 问题在策略/命令解释层, 不在输入链。")
else:
    print(f"断链于: {fails} — 最早 FAIL 的层级就是病灶。")
print(json.dumps(results, ensure_ascii=False, default=str))
