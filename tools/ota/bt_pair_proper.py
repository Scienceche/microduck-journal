#!/usr/bin/env python3
"""正确配对 v2: hcitool 扫描(能看到经典蓝牙) + 交互式 bluetoothctl 会话配对。"""
import os, select, subprocess, sys, time, re

MAC = '99:16:56:C3:54:4F'
HID = '00001124-0000-1000-8000-00805f9b34fb'

# === 第一步: 用 hcitool 扫描(可靠地发现经典蓝牙设备) ===
print('hcitool 扫描中...', flush=True)
scan = subprocess.run(['hcitool', 'scan', '--length=8'],
                      capture_output=True, text=True, timeout=15)
found = MAC in scan.stdout
if not found:
    # 也试 BLE 扫描
    ble = subprocess.run(['bluetoothctl', '--timeout=5', 'scan', 'on'],
                         capture_output=True, text=True, timeout=10)
    found = 'GameSir' in ble.stdout
if not found:
    print('✗ 扫描未发现手柄(手机能看到说明手柄在广播)', flush=True)
    print('尝试直接连接已知 MAC...', flush=True)

# === 第二步: 交互式 bluetoothctl 会话 ===
proc = subprocess.Popen(['bluetoothctl'], stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        bufsize=0, env={**os.environ, 'LC_ALL': 'C', 'TERM': 'dumb'})

def read(timeout=3):
    if proc.poll() is not None: return ''
    if not select.select([proc.stdout], [], [], timeout)[0]: return ''
    return os.read(proc.stdout.fileno(), 16384).decode('utf-8', errors='replace')

def cmd(command, markers, timeout=15):
    proc.stdin.write((command + '\n').encode()); proc.stdin.flush()
    text = ''
    end = time.time() + timeout
    while time.time() < end:
        text += read(0.5)
        for m in markers:
            if m in text: return text
    return text

read(2)
cmd('agent off', ['unregistered', 'No agent'], 5)
cmd('agent NoInputNoOutput', ['registered', 'already'], 5)
print('✓ agent NoInputNoOutput', flush=True)
cmd('pairable on', ['succeeded'], 5)
print('✓ pairable on', flush=True)

# === 第三步: 用 hcitool 建立连接(让设备出现在 bluetoothctl 里) ===
subprocess.run(['hcitool', 'cc', MAC], capture_output=True, timeout=10)
time.sleep(1)
print('✓ ACL 连接已建立', flush=True)

# === 第四步: 在交互式会话中配对 ===
print('配对中...', flush=True)
r = cmd('pair ' + MAC, ['Pairing successful', 'Failed'], 30)
if 'successful' in r:
    print('✓ 配对成功!', flush=True)
    cmd('trust ' + MAC, ['succeeded'], 5)
    print('✓ trusted', flush=True)
    r2 = cmd('connect ' + MAC, ['successful', 'Already'], 15)
    r3 = cmd('connect ' + MAC + ' ' + HID, ['successful', 'Already', 'Failed'], 20)
    print(f'连接: {r2[-40:]}', flush=True)
    print(f'HID: {r3[-40:]}', flush=True)
else:
    print(f'✗ 配对失败: {r[-120:]}', flush=True)

# === 状态 ===
info = cmd('info ' + MAC, ['Name'], 5)
for l in info.split('\n'):
    if any(k in l for k in ('Name', 'Paired', 'Connected')):
        print(l.strip(), flush=True)

proc.stdin.write(b'quit\n'); proc.stdin.flush(); proc.wait()

time.sleep(3)
events = subprocess.run(['ls', '/dev/input/'], capture_output=True, text=True).stdout
n = len([e for e in events.split() if e.startswith('event')])
names = subprocess.run(['cat', '/proc/bus/input/devices'],
                       capture_output=True, text=True).stdout
for line in names.split('\n'):
    if 'Name=' in line:
        print(line.strip(), flush=True)
print(f'输入设备: {n} 个', flush=True)
