#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""会话记录器: 订阅 robot.state, 追加 CSV(t,tilt,fallen,V,phase,policy) +
每 10s 一行摘要到 stdout。运行于鸭子, nohup 拉起, 测试结束后 kill。"""
import json
import math
import socket
import sys
import time
from pathlib import Path

OUT = Path("/root/blackbox/session_%s.csv" % time.strftime("%Y%m%d_%H%M%S"))
SOCK = "/run/robotd.sock"


def tilt_of(g):
    try:
        if len(g) != 3:
            return None
        return round(math.degrees(math.acos(max(-1.0, min(1.0, -g[2])))), 1)
    except Exception:
        return None


def volts():
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(2.0)
        s.connect(SOCK)
        s.sendall((json.dumps({"jsonrpc": "2.0", "id": 2,
                               "method": "robot.busStatus"}) + "\n").encode())
        d = json.loads(s.makefile().readline())["result"]
        s.close()
        vs = [j.get("volts") for j in (d.get("native") or {}).get("joints") or []
              if j.get("volts")]
        return round(min(vs), 2) if vs else None
    except Exception:
        return None


f = OUT.open("a", encoding="utf-8")
f.write("# t,tilt_deg,fallen,V_min,policy\n")
print("[session] recording → %s" % OUT, flush=True)

while True:
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect(SOCK)
        s.sendall((json.dumps({"jsonrpc": "2.0", "id": 1, "method": "robot.subscribe",
                               "params": {"hz": 20}}) + "\n").encode())
        last_sum = 0.0
        n = 0
        tmax_v = None
        for line in s.makefile("rb"):
            if not line:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == 1:
                continue
            if msg.get("method") != "robot.state":
                continue
            p = msg["params"]
            safety = p.get("safety") or {}
            tilt = tilt_of(safety.get("gravity"))
            fallen = bool(safety.get("fallen"))
            pol = (p.get("policy") or {})
            pol_s = pol.get("enabled") if isinstance(pol, dict) else None
            t = time.time()
            if n % 20 == 0:  # ~1Hz 采电压
                tmax_v = volts()
            f.write("%.2f,%s,%d,%s,%s\n" % (t, tilt, int(fallen), tmax_v, pol_s))
            n += 1
            if t - last_sum >= 10:
                last_sum = t
                f.flush()
                print("[session %s] tilt=%s fallen=%s V=%s policy=%s n=%d" % (
                    time.strftime("%H:%M:%S"), tilt, fallen, tmax_v, pol_s, n), flush=True)
        f.flush()
    except Exception as e:
        print("[session] 流中断: %s, 2s 重连" % str(e)[:60], flush=True)
        time.sleep(2.0)
