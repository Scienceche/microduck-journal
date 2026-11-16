#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""观测者: 等人工推倒 -> 计时自主起身, 重复5次。跑在鸭子本机, nohup 脱离 SSH。"""
import socket, json, time, subprocess, re, os

def rpc(m, p=None, t=6):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(t); s.connect('/run/robotd.sock')
    req = {'jsonrpc': '2.0', 'id': 1, 'method': m}
    if p is not None: req['params'] = p
    s.sendall((json.dumps(req) + '\n').encode())
    return json.loads(s.makefile().readline())['result']

def falls(since):
    out = subprocess.run(['journalctl', '-u', 'robotd', '--since', '@%d' % since, '-o', 'json', '--no-pager'],
                         capture_output=True, text=True).stdout
    ev = []
    for line in out.splitlines():
        try: e = json.loads(line)
        except Exception: continue
        m = e.get('MESSAGE', '')
        if 'fall verdict changed' in m:
            g = re.search(r'gravity_z=(-?[\d.]+)', m)
            ev.append((int(e['__REALTIME_TIMESTAMP']) / 1e6, 'fallen=true' in m, float(g.group(1)) if g else 0))
    return sorted(ev)

if __name__ == '__main__':
    d = rpc('robot.busStatus')
    if not d.get('policy_enabled'):
        print('策略未驱动! 先 enable 再推'); raise SystemExit(1)
    print('监测就绪: 请推倒它(共5次, 每次等它爬起或75s超时后再推下一次)', flush=True)
    results = []
    t_start = time.time()
    while len(results) < 5:
        if os.path.exists('/tmp/getup_stop'):
            print('[stop]'); break
        ev = falls(t_start)
        f = [e for e in ev if e[1]]
        if not f:
            time.sleep(0.4); continue
        fallen_at = f[-1][0]
        print('第%d次: 已倒(gz=%.2f), 计时起身...' % (len(results) + 1, f[-1][2]), flush=True)
        t_rec = None; last_up = None; deadline = time.time() + 75
        while time.time() < deadline:
            if os.path.exists('/tmp/getup_stop'): break
            ev2 = falls(fallen_at)
            if ev2 and not ev2[-1][1]:
                if last_up is None: last_up = time.time()
                if time.time() - last_up > 5.0: t_rec = time.time(); break
            else:
                last_up = None
            time.sleep(0.4)
        rec = {'n': len(results) + 1, 't': round(t_rec - fallen_at, 1) if t_rec else None}
        results.append(rec)
        print(json.dumps(rec), '(成功)' if t_rec else '(超时失败)', flush=True)
        if t_rec: time.sleep(2)
        t_start = time.time()
        if not t_rec:
            print('超时: 可将其扶起站稳(策略继续驱动)后再推下一次', flush=True)
    ok = sum(1 for r in results if r['t'])
    print('==== SUMMARY: %d/%d 自主起身 ====' % (ok, len(results)), flush=True)
    json.dump(results, open('/root/logs/getup-watch-%s.json' % time.strftime('%H%M%S'), 'w'), indent=1)
