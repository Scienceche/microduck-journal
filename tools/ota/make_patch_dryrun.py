#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 patch_recorder_imu.py 的干跑副本: 目标指向本地假文件, 不碰鸭子。"""
from pathlib import Path

SRC = Path("C:/duck_work/ota/patch_recorder_imu.py")
FAKE = "C:/duck_work/ota/dryrun/server_fake.py"
OUT = Path("C:/duck_work/ota/dryrun/patch_dryrun.py")

s = SRC.read_text(encoding="utf-8")
a1 = 'SRV = Path("/opt/duck-calib/server.py")'
a2 = 'BAK = Path("/opt/duck-calib/server.py.bak-imu")'
assert s.count(a1) == 1, "SRV 行不符"
assert s.count(a2) == 1, "BAK 行不符"
s = s.replace(a1, 'SRV = Path("%s")' % FAKE)
s = s.replace(a2, 'BAK = Path("%s.bak")' % FAKE)
assert "/opt/duck-calib" not in s, "仍有鸭子路径残留"
OUT.write_text(s, encoding="utf-8")
print("干跑副本已生成:", OUT)
