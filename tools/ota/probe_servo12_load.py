#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""12 号负载测试: 上扭矩(目标=当前位置, 不动) + 微动 ±40 counts,
全程 ping 扫描, 看 12 带载时是否拖垮总线。[12,13,14]掉=12内伤实锤。"""
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
    return (d.get('data') or {}).get('data_hex') or None


def ping(sid):
    return bool(hls('/api/ping', {'id': sid, 'timeout_ms': 80}).get('ok'))


IDS = [10, 11, 12, 13, 14, 20, 21, 22, 23, 24, 30, 31, 32, 33, 34, 200]


def scan(tag):
    ok = [i for i in IDS if ping(i)]
    missing = sorted(set(IDS) - set(ok))
    print('%-14s %s' % (tag, '全在线' if not missing else '掉:' + str(missing)), flush=True)
    return missing


print('== 连接 ==')
hls('/api/connect', {'port': '/dev/ttyS2', 'baudrate': 1000000, 'timeout_ms': 100})

scan('① 空载基线')
for _ in range(6):
    ping(12); time.sleep(0.2)
scan('② 空载连发后')

pos = read_regs(12, 56, 2)
if not pos:
    print('12 位置读取失败'); raise SystemExit
cur = list(bytes.fromhex(pos.replace(' ', '')))
print('12 当前位置:', pos)

print('== 扭矩 ON(目标=当前位置, 不动) ==')
hls('/api/write', {'id': 12, 'addr': 42, 'data': cur})   # 先写目标再上扭矩 = 保持不动
print(hls('/api/write', {'id': 12, 'addr': 40, 'data': [1]}).get('ok'))
for t in range(3):
    time.sleep(1.0)
    scan('③ 扭矩%d秒' % ((t + 1) * 1))

print('== 微动测试 ±40 counts ==')
w = cur[0] + (cur[1] << 8)
for delta in (40, -40, 40, 0):
    tgt = [(w + delta) & 0xFF, ((w + delta) >> 8) & 0xFF]
    hls('/api/write', {'id': 12, 'addr': 42, 'data': tgt})
    time.sleep(0.6)
    scan('④ 目标%+d' % delta)

hls('/api/write', {'id': 12, 'addr': 40, 'data': [0]})   # 扭矩 OFF
time.sleep(0.5)
scan('⑤ 扭矩OFF后')
print('== 完成 ==')
