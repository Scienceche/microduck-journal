#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""部署后原地站立 5 分钟监测(2026-11-26)。

回答两个问题:
  1. 主循环延迟有没有波动: 订阅 robot.state @50Hz, 聚合 control_loop.hz 的 min/max/均值,
     以及 missed 的 5 分钟增量。
  2. 影子是否在正常出数据: 每 15s 查 robot.shadowReport, 看 submitted/inferred 是否在涨
     (驱动时约 10/s), invalid / dropped_full 是否为 0, 单次推理耗时。
另外每 30s 记一次电池电压(robot.busStatus), 首尾各查一次 robot.health。
产物: /root/shadow_stand5min_<时间戳>.json
用法(在鸭子上): nohup python3 monitor_stand5min.py > /tmp/monitor5.log 2>&1 &
       或前台: python3 monitor_stand5min.py
"""
import json
import socket
import statistics
import time

SOCK = "/run/robotd.sock"
DURATION = float(__import__("sys").argv[1]) if len(__import__("sys").argv) > 1 else 300.0
SHADOW_POLL = 15.0          # 影子计数器采样间隔
BUS_POLL = 30.0             # 电压采样间隔
T0 = time.time()
OUT = "/root/shadow_monitor_%s.json" % time.strftime("%Y%m%d-%H%M%S")


def call(method, params=None, timeout=6.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(SOCK)
    s.sendall((json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                           **({"params": params} if params else {})}) + "\n").encode())
    line = s.makefile("rb").readline()
    s.close()
    return json.loads(line).get("result")


def bus_voltage():
    d = call("robot.busStatus") or {}
    js = (d.get("native") or {}).get("joints") or []
    vs = [j.get("volts") for j in js if j.get("volts")]
    return round(sum(vs) / len(vs), 3) if vs else None


health_start = call("robot.health")
volts = [{"t": 0.0, "V": bus_voltage()}]
shadow = [{"t": 0.0, **(call("robot.shadowReport") or {})}]

# ---- 订阅流聚合 ----
req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "robot.subscribe",
                  "params": {"hz": 50}}) + "\n"
sub = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sub.settimeout(4.0)
sub.connect(SOCK)
sub.sendall(req.encode())
stream = sub.makefile("rb")

hz_samples = []
missed_first = None
missed_last = None
labels = {}
fallen_frames = 0
n_frames = 0
last_shadow_t = last_bus_t = 0.0
was_fallen = None   # None=还没见过帧
fall_edges = []

while time.time() - T0 < DURATION:
    now = time.time() - T0
    if now - last_shadow_t >= SHADOW_POLL:
        shadow.append({"t": round(now, 1), **(call("robot.shadowReport") or {})})
        last_shadow_t = now
    if now - last_bus_t >= BUS_POLL:
        volts.append({"t": round(now, 1), "V": bus_voltage()})
        last_bus_t = now
    try:
        line = stream.readline()
    except socket.timeout:
        continue
    if not line:
        break
    try:
        frame = json.loads(line).get("result") or json.loads(line).get("params") or {}
    except Exception:
        continue
    if not isinstance(frame, dict) or "loop" not in frame:
        continue
    n_frames += 1
    cl = frame.get("loop") or {}
    if cl.get("hz"):
        hz_samples.append(float(cl["hz"]))
    m = cl.get("missed")
    if m is not None:
        missed_first = m if missed_first is None else missed_first
        missed_last = m
    lbl = frame.get("policy")
    if lbl:
        labels[lbl] = labels.get(lbl, 0) + 1
    if (frame.get("safety") or {}).get("fallen"):
        fallen_frames += 1
    # 2026-11-26 影子期: 记录倒地时间轴(rising edge), 供 ≥10 次/四姿态核对
    fallen_now = bool((frame.get("safety") or {}).get("fallen"))
    if fallen_now and not was_fallen:
        fall_edges.append(round(now, 1))
    was_fallen = fallen_now

sub.close()
health_end = call("robot.health")
shadow.append({"t": round(time.time() - T0, 1), **(call("robot.shadowReport") or {})})

# ---- 汇总 ----
first, last = shadow[0], shadow[-1]
report = {
    "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    "duration_s": round(time.time() - T0, 1),
    "health_start": health_start,
    "health_end": health_end,
    "loop": {
        "frames_seen": n_frames,
        "hz_min": min(hz_samples) if hz_samples else None,
        "hz_max": max(hz_samples) if hz_samples else None,
        "hz_mean": round(statistics.mean(hz_samples), 2) if hz_samples else None,
        "hz_stdev": round(statistics.pstdev(hz_samples), 3) if len(hz_samples) > 1 else None,
        "missed_start": missed_first,
        "missed_end": missed_last,
        "missed_delta_5min": (missed_last - missed_first)
        if (missed_last is not None and missed_first is not None) else None,
    },
    "policy_labels": labels,
    "fallen_frames": fallen_frames,
    "fall_rising_edges_t": fall_edges,
    "volts": volts,
    "volts_min": min((v["V"] for v in volts if v["V"] is not None), default=None),
    "volts_max": max((v["V"] for v in volts if v["V"] is not None), default=None),
    "shadow_first": first,
    "shadow_last": last,
    "shadow": shadow,
    "shadow_growth": {
        "submitted": (last.get("submitted", 0) - first.get("submitted", 0)),
        "inferred": (last.get("inferred", 0) - first.get("inferred", 0)),
        "invalid_delta": (last.get("invalid", 0) - first.get("invalid", 0)),
        "dropped_full_delta": (last.get("dropped_full", 0) - first.get("dropped_full", 0)),
        "invalid_total": last.get("invalid"),
        "dropped_full_total": last.get("dropped_full"),
        "elapsed_ms_mean": last.get("elapsed_ms_mean"),
        "elapsed_ms_max": last.get("elapsed_ms_max"),
        "diff_norm_mean": last.get("diff_norm_mean"),
        "active": last.get("active"),
        "sha256": last.get("sha256"),
    },
}
with open(OUT, "w") as f:
    json.dump(report, f, ensure_ascii=False, indent=1)

print(json.dumps({k: v for k, v in report.items()
                  if k not in ("shadow", "volts", "health_start", "health_end")},
                 ensure_ascii=False, indent=1))
print("health_start:", json.dumps(health_start, ensure_ascii=False)[:220])
print("health_end:  ", json.dumps(health_end, ensure_ascii=False)[:220])
print("volts:", " ".join("%s:%.2f" % (v["t"], v["V"]) for v in volts if v["V"] is not None))
print("报告:", OUT)
