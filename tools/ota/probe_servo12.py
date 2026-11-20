#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复现实验 v2: 经 HLS(:8082) 读 12/13 对照, 对 12 无扭矩写目标, 看总线是否整组掉。"""
import json
import time
import urllib.request


def hls(path, body):
    req = urllib.request.Request(
        'http://127.0.0.1:8082' + path, data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())


def read_regs(sid, addr, length):
    d = hls('/api/read', {'id': sid, 'addr': addr, 'length': length})
    dd = d.get('data', {})
    return dd.get('data_hex') or ('ERR:' + str(d.get('error', '?'))[:60])


def ping(sid):
    d = hls('/api/ping', {'id': sid, 'timeout_ms': 100})
    return bool(d.get('ok'))


IDS = [10, 11, 12, 13, 14, 20, 21, 22, 23, 24, 30, 31, 32, 33, 34, 200]


def scan(tag):
    ok = [i for i in IDS if any(ping(i) for _ in range(2))]
    missing = sorted(set(IDS) - set(ok))
    print('%s: 在线 %d/16 %s' % (tag, len(ok), '缺:' + str(missing) if missing else '全在线'))


print('== HLS 连接 ==')
c = hls('/api/connect', {'port': '/dev/ttyS2', 'baudrate': 1000000, 'timeout_ms': 100})
print('connected:', c.get('data', {}).get('connected'))

print('== 对照读(2字节) ==')
for sid in (13, 12):
    print('ID%02d pos@56=%s goal@42=%s' % (sid, read_regs(sid, 56, 2), read_regs(sid, 42, 2)))

scan('① 基线')

print('== 复现: 对 12 无扭矩写目标(=当前位置) ==')
pos_hex = read_regs(12, 56, 2)
if pos_hex and not pos_hex.startswith('ERR'):
    b = list(bytes.fromhex(pos_hex.replace(' ', '')))
    print('12号当前位置 raw:', b)
    w = hls('/api/write', {'id': 12, 'addr': 42, 'data': b})
    print('write reg42 ok:', w.get('ok'))
    time.sleep(0.3)
    scan('② 写后')
    print('读回 reg42:', read_regs(12, 42, 2))
    time.sleep(0.3)
    scan('③ 终态')
else:
    print('12号读取失败:', pos_hex)

hls('/api/disconnect', {})
