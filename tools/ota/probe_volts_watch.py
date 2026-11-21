#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标定操作同步监测: 逐秒记录每舵机 volts + 掉线, 捕捉电流冲击→末端电压塌的瞬间。"""
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
        res = r.get('result', {})
        dc = res.get('device_communication', {}).get('by_id', {})
        bad = sorted(int(k) for k, v in dc.items() if v.get('consecutive_timeout', 0) > 3)
        js = res.get('native', {}).get('joints') or []
        volts = {j.get('id'): j.get('volts') for j in js if j.get('volts')}
        return bad, volts
    except Exception:
        return ['RPC-ERR'], {}


print('监测 90 秒 — 现在去标定台重复锁定操作!')
prev_bad = None
min_v = {}
t0 = time.time()
while time.time() - t0 < 90:
    bad, volts = bus()
    for sid, v in volts.items():
        if v is not None:
            min_v[sid] = min(min_v.get(sid, 99), v)
    if bad != prev_bad:
        vs = ' '.join('%d:%.2f' % (k, v) for k, v in sorted(volts.items())[:16])
        print('%s %s | %s' % (time.strftime('%H:%M:%S'),
                              'OK' if not bad else '掉:' + str(bad), vs), flush=True)
        prev_bad = bad
    time.sleep(1.0)
print('--- 各舵机期间最低电压 ---')
for sid in sorted(min_v):
    print('ID%02d 最低 %.2fV' % (sid, min_v[sid]))
