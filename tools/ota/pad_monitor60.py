#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""60秒后台监控: hidraw 字节变化时间线 + event4 事件时间线。
用户随时动摇杆/按键, 事后看 /tmp/pad_monitor.log 的时间对应关系。
"""
import os
import struct
import time

LOG = "/tmp/pad_monitor.log"
DUR = 60

hfd = os.open("/dev/hidraw0", os.O_RDONLY | os.O_NONBLOCK)
xfd = os.open("/dev/input/event4", os.O_RDONLY | os.O_NONBLOCK)

lines = []
hprev = None
t0 = time.time()
while time.time() - t0 < DUR:
    ts = time.time() - t0
    try:
        d = os.read(hfd, 64)
        key = d[:8].hex()
        if key != hprev:
            lines.append(f"{ts:6.1f} HID {key}")
            hprev = key
    except BlockingIOError:
        pass
    try:
        buf = os.read(xfd, 4096)
        for off in range(0, len(buf) - 23, 24):
            _, typ, code, val = struct.unpack_from("qqhi", buf, off)
            if typ in (1, 3):  # KEY/ABS
                tn = "KEY" if typ == 1 else "ABS"
                lines.append(f"{ts:6.1f} {tn} code={code} val={val}")
    except BlockingIOError:
        pass
    time.sleep(0.001)

os.close(hfd)
os.close(xfd)
with open(LOG, "w") as f:
    f.write("\n".join(lines) + "\n")
print(f"captured {len(lines)} lines -> {LOG}")
