#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""站立监控: init+enable → 60秒实时报告倾角/高度/是否切到 stand 网络。"""
import socket, json, time, sys, math

def rpc(m, p=None, t=60):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(t); s.connect('/run/robotd.sock')
    req = {'jsonrpc': '2.0', 'id': 1, 'method': m}
    if p is not None: req['params'] = p
    s.sendall((json.dumps(req) + '\n').encode())
    return json.loads(s.makefile().readline())

def get_tilt():
    d = rpc('robot.busStatus', t=8)['result']
    n = d.get('native') or {}
    js = n.get('joints') or []
    # 用颈部代理倾角(neck_pitch 在 HOME=20°, 站直时约 20°, 倒地时偏差大)
    gz = None
    for j in js:
        if j.get('id') == 30:
            gz = j.get('position_rad', 0) * 57.2958
    return float(d.get('cycle') or 0), gz, len(js)

print("[monitor] 等 5 秒后发送 init + enable...", flush=True)
time.sleep(5)

# init
try:
    rpc('robot.init')
    print("[monitor] init 完成", flush=True)
except Exception as e:
    print("[monitor] init:", e); sys.exit(1)

# enable(带重试)
for attempt in range(15):
    try:
        rpc('robot.enable', {'on': True}, t=30)
    except Exception:
        pass
    time.sleep(1)
    d = rpc('robot.busStatus', t=8)['result']
    if d.get('policy_enabled'):
        print("[monitor] 策略已启动! 松手! 开始 60 秒监控...", flush=True)
        break
else:
    print("[monitor] enable 失败"); sys.exit(1)

# 监控 60 秒
tilts = []
for i in range(60):
    time.sleep(1)
    try:
        d = rpc('robot.busStatus', t=8)['result']
        n = d.get('native') or {}
        ls = d.get('last_sample') or {}
        imu = ls.get('imu') or {}
        g = imu.get('gravity') or [0, 0, -1]
        tilt = math.degrees(math.acos(max(-1, min(1, -g[2]))))
        h = None
        for j in (n.get('joints') or []):
            if j.get('id') == 30: h = j.get('position_rad', 0) * 57.2958
        tilts.append(tilt)
        standing = d.get('phase') == 'control' and tilt < 30
        if i % 10 == 0:
            print("[monitor] t=%2ds 倾角 %.1f° %s 阈值 %s" % (
                i+1, tilt, '✓站立' if standing else '⚠ 异常', d.get('phase')), flush=True)
    except Exception as e:
        print("[monitor] t=%d 读取失败: %s" % (i+1, str(e)[:60]), flush=True)

# 汇总
import statistics
if tilts:
    print("\n[monitor] === 60 秒汇总 ===", flush=True)
    print("  倾角: 中位 %.1f° | p90 %.1f° | 最大 %.1f°" % (
        statistics.median(tilts), sorted(tilts)[int(len(tilts)*0.9)], max(tilts)))
    print("  站立帧占比(倾角<30°): %.0f%%" % (sum(1 for t in tilts if t < 30) / len(tilts) * 100))
    print("  站立帧占比(倾角<20°): %.0f%%" % (sum(1 for t in tilts if t < 20) / len(tilts) * 100))
    # 放松
    rpc('robot.relax')
    print("[monitor] 已松开", flush=True)
