#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""黑匣子 v3 测试: robot.state 订阅路径的 fall 状态机/brownout/断线重连。"""
import gzip
import json
import shutil
import sys
import tempfile
import time as real_time
from pathlib import Path

BASE = Path(r"C:\duck_work\ota")
sys.path.insert(0, str(BASE))

FAILS = []


def check(name, ok, detail=""):
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" — " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


import duck_blackbox as bx


class FakeTime:
    def __init__(self):
        self.t = 1000.0
    def monotonic(self):
        return self.t
    def time(self):
        return self.t
    def sleep(self, s):
        self.t += max(s, 0.0)
    def strftime(self, fmt):
        return real_time.strftime(fmt, real_time.localtime(self.t))


def mkframe(tilt_deg=None, fallen=False, V=7.9, phase="control"):
    safety = {}
    if tilt_deg is not None:
        import math
        t = math.radians(tilt_deg)
        safety["gravity"] = [0.0, 0.0, -math.cos(t)]  # 倾角 t 的重力 z
    safety["fallen"] = fallen
    return {
        "t": 0.1,
        "safety": safety,
        "movement": {"phase": phase, "mode": "motion", "policy": "off"},
        "joints": [0.1] * 14,
        "targets": [0.0] * 14,
    }


class Script:
    """帧脚本: [(时长s, tilt_deg or None, fallen, V), ...] — 每帧推进共享 FakeTime"""
    def __init__(self, segs, ft):
        self.segs = segs
        self.i = 0
        self.used = 0.0
        self.ft = ft
        self.cur_V = 7.9
    def frames(self):
        while self.i < len(self.segs):
            dur, tilt, fallen, V = self.segs[self.i]
            self.ft.t += 0.05  # 20Hz 帧距, 同时驱动 FakeTime
            self.used += 0.05
            self.cur_V = V
            if self.used >= dur:
                self.i += 1
                self.used = 0.0
            yield mkframe(tilt, fallen, V)
        raise KeyboardInterrupt


def run_bb(segs, volts_seq=None):
    ft = FakeTime()
    bx.time = ft
    script = Script(segs, ft)
    dumps = []

    def fake_frames(timeout=3.0):
        return script.frames()

    def fake_volts():
        return script.cur_V

    bx.state_frames = fake_frames
    bx.bus_volts = fake_volts

    def fake_dump(etype, ring, meta):
        dumps.append((etype, len(ring), dict(meta, _t=ft.t)))
        return Path("/tmp/fake_%s" % etype)
    bx.dump_event = fake_dump
    bx.journal_context = lambda pre_s=40: ["fake journal"]
    bx.hb = lambda msg: None
    bx.disk_ok = lambda: True
    try:
        bx.record()
    except KeyboardInterrupt:
        pass
    return dumps


# 语法
import py_compile
py_compile.compile(str(BASE / "duck_blackbox.py"), doraise=True)
check("语法", True)

# A: 基本链路 fall(fallen 标志触发)→recover→brownout
dumps = run_bb([
    (40.0, 5.0, False, 7.9),
    (1.0, 50.0, True, 7.9),    # fallen 标志 + 高倾角
    (11.0, 35.0, False, 7.9),  # 挣扎区
    (3.0, 8.0, False, 7.9),    # 恢复
    (1.0, 5.0, False, 7.0),    # 低压
    (1.0, 5.0, False, 7.9),
])
types = [d[0] for d in dumps]
check("A: fall(fallen 触发)", "fall" in types, str(types))
check("A: fallen 标志入 meta", any(d[2].get("fallen_flag") for d in dumps if d[0] == "fall"))
check("A: recover", "recover" in types, str(types))
check("A: brownout", "brownout" in types, str(types))

# B: 纯倾角触发(fallen=False, tilt>45° 持续 3 帧)
dumps_b = run_bb([
    (40.0, 5.0, False, 7.9),
    (1.0, 50.0, False, 7.9),   # 无 fallen 标志, 靠备用倾角
    (50.0, 55.0, False, 7.9),  # 躺倒(stuck 后解除武装)
])
tb = [d[0] for d in dumps_b]
check("B: 备用倾角触发 fall", "fall" in tb, str(tb))
check("B: 躺倒只 1 次 fall+1 stuck", tb.count("fall") == 1 and tb.count("stuck") == 1, str(tb))

# C: fallen 标志瞬间触发(<300ms 也立即)
dumps_c = run_bb([
    (40.0, 5.0, False, 7.9),
    (0.3, 10.0, True, 7.9),    # fallen=True 但倾角低(前倾爬行态)
    (3.0, 8.0, False, 7.9),
])
check("C: fallen 标志低倾角也触发", "fall" in [d[0] for d in dumps_c],
      str([d[0] for d in dumps_c]))

# D: gzip OSError 不崩
ft2 = FakeTime()
bx.time = ft2
script_d = Script([(40.0, 5.0, False, 7.9), (1.0, 50.0, True, 7.9), (10.0, 55.0, True, 7.9)], ft2)
frames_seen = []
bx.state_frames = lambda timeout=3.0: script_d.frames()
bx.bus_volts = lambda: 7.9
bx.OUT_DIR = Path(tempfile.mkdtemp(prefix="bb3_"))
bx.journal_context = lambda pre_s=40: []
bx.hb = lambda msg: None
bx.disk_ok = lambda: True
class BadGzip:
    def open(self, *a, **kw):
        raise OSError("Read-only file system")
bx.gzip = BadGzip()
survived = True
try:
    bx.record()
except KeyboardInterrupt:
    pass
except Exception:
    survived = False
check("D: gzip OSError 不崩", survived)
shutil.rmtree(bx.OUT_DIR, ignore_errors=True)
bx.OUT_DIR = Path("/root/blackbox")
bx.gzip = gzip

print()
if FAILS:
    print("==== %d FAIL: %s ====" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("==== 黑匣子 v3 测试全部 PASS ====")
