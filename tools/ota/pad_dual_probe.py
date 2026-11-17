#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""双通道同步探针: 同时抓 hidraw(原始HID报文) + event4(虚拟Xbox事件)。

用法: python3 pad_dual_probe.py [秒]
用户配合: 期间持续 左摇杆大幅画圈 + 交替按 A/B 键。
判定矩阵:
  hidraw动 + xbox ABS动 + KEY动  -> 全链通(问题在 padd/策略)
  hidraw动 + xbox 仅KEY动       -> 桥轴转发断(轴映射/源码层)
  hidraw动 + xbox 全无          -> 桥没在读pad(grab丢失)或内核hid->evdev断
  hidraw不动                    -> pad休眠/僵尸, 先叫醒手柄
"""
import glob
import os
import struct
import sys
import time

DUR = int(sys.argv[1]) if len(sys.argv) > 1 else 8

# 找 GameSir hidraw
hid_dev = None
for h in glob.glob("/dev/hidraw*"):
    try:
        ue = open(f"/sys/class/hidraw/{os.path.basename(h)}/device/uevent").read()
    except OSError:
        continue
    if "GameSir" in ue and "Consumer" not in ue and "0004" not in ue:
        hid_dev = h
        break
if not hid_dev:
    # 退而求其次: 第一个 GameSir
    for h in glob.glob("/dev/hidraw*"):
        try:
            ue = open(f"/sys/class/hidraw/{os.path.basename(h)}/device/uevent").read()
        except OSError:
            continue
        if "GameSir" in ue:
            hid_dev = h
            break

# 找虚拟 Xbox
xbox_dev = None
for e in glob.glob("/dev/input/event*"):
    try:
        n = open(f"/sys/class/input/{os.path.basename(e)}/device/name").read()
    except OSError:
        continue
    if "Xbox" in n:
        xbox_dev = e
        break

print(f"hidraw={hid_dev}  xbox={xbox_dev}  采样{DUR}s")

hfd = os.open(hid_dev, os.O_RDONLY | os.O_NONBLOCK) if hid_dev else -1
xfd = os.open(xbox_dev, os.O_RDONLY | os.O_NONBLOCK) if xbox_dev else -1

hspan = {}
hlast_change = 0.0
key_events = 0
abs_span = {}
t0 = time.time()
while time.time() - t0 < DUR:
    ts = time.time() - t0
    if hfd >= 0:
        try:
            d = os.read(hfd, 64)
            if len(d) >= 7:
                for ch, idx in (("LX", 3), ("LY", 4)):
                    v = d[idx]
                    lo, hi = hspan.get(ch, (v, v))
                    if v != lo and v != hi or v < lo or v > hi:
                        hlast_change = ts
                    hspan[ch] = (min(lo, v), max(hi, v))
        except BlockingIOError:
            pass
    if xfd >= 0:
        try:
            buf = os.read(xfd, 4096)
            for off in range(0, len(buf) - 23, 24):
                _, typ, code, val = struct.unpack_from("qqhi", buf, off)
                if typ == 1:  # EV_KEY
                    key_events += 1
                elif typ == 3:  # EV_ABS
                    lo, hi = abs_span.get(code, (val, val))
                    abs_span[code] = (min(lo, val), max(hi, val))
        except BlockingIOError:
            pass
    time.sleep(0.002)

os.close(hfd) if hfd >= 0 else None
os.close(xfd) if xfd >= 0 else None

print("\n===== 结果 =====")
h = {c: hi - lo for c, (lo, hi) in hspan.items()}
a = {c: hi - lo for c, (lo, hi) in abs_span.items()}
print(f"hidraw 摇杆字节跨度: {h}")
print(f"xbox  KEY事件数: {key_events}")
print(f"xbox  ABS跨度: {a}")
hid_moving = max(h.values(), default=0) >= 40
print("\n===== 判定 =====")
if not hid_moving:
    print("hidraw 不动: pad 休眠或 HID 僵尸。叫醒手柄重来。")
elif a.get(0, 0) >= 10000 or a.get(1, 0) >= 10000:
    print("全链通: 摇杆数据直达虚拟 Xbox。问题在 padd/策略层。")
elif key_events > 0:
    print("桥断在轴转发: KEY 通、ABS 断 → 轴映射/转发层问题。")
else:
    print("桥整体没在读: hidraw 动但 xbox 全无 → grab 丢失或内核 hid->evdev 断。")
