#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2: 用累计 timeout 计数增量捕捉掉线(不受 consecutive 重置影响),
同步抓黑匣子电压。用户重复锁定操作时运行。"""
import json
import socket
import time


def bus():
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(4)
        s.connect('/run/robotd.sock')
        s.sendall((json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'robot.busStatus'}) + '\n').encode())
        buf = b''
        while True:
            try:
                d = s.recv(262144)
            except socket.timeout:
                break
            if not d:
                break
            buf += d
            try:
                r = json.loads(buf.decode().splitlines()[0])
                break
            except Exception:
                pass
        dc = r.get('result', {}).get('device_communication', {}).get('by_id', {})
        return {int(k): v.get('timeout', 0) for k, v in dc.items()}
    except Exception:
        return None


print('监测 75 秒 — 现在去点一次「锁定 12」!')
prev = bus()
t0 = time.time()
events = 0
while time.time() - t0 < 75:
    cur = bus()
    if cur is not None and prev is not None:
        delta = {k: cur[k] - prev.get(k, 0) for k in cur if cur[k] - prev.get(k, 0) > 0}
        if delta:
            events += 1
            print('%s 超时增量: %s' % (time.strftime('%H:%M:%S'), delta), flush=True)
    if cur is not None:
        prev = cur
    time.sleep(1.0)
print('--- 结束: %d 次掉线事件 ---' % events)
