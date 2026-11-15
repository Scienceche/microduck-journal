#!/usr/bin/env python3
"""交互式 bluetoothctl 配对(官方修复脚本方式)。"""
import os, select, subprocess, sys, time

ADDR = "99:16:56:C3:54:4F"
HID = "00001124-0000-1000-8000-00805f9b34fb"

proc = subprocess.Popen(["bluetoothctl"], stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        bufsize=0, env={**os.environ, "LC_ALL": "C", "TERM": "dumb"})

def read(timeout=3):
    if proc.poll() is not None: return ""
    if not select.select([proc.stdout], [], [], timeout)[0]: return ""
    return os.read(proc.stdout.fileno(), 16384).decode("utf-8", errors="replace")

def cmd(command, markers, timeout=15):
    proc.stdin.write((command + "\n").encode()); proc.stdin.flush()
    text = ""
    end = time.time() + timeout
    while time.time() < end:
        text += read(0.5)
        for m in markers:
            if m in text: return text
    return text

# 1. Agent 设置
read(2)
cmd("agent off", ["unregistered", "No agent"], 5)
cmd("agent NoInputNoOutput", ["registered", "already"], 5)
cmd("pairable on", ["succeeded"], 5)
print("agent ready", flush=True)

# 2. 扫描
cmd("scan on", ["started"], 5)
print("scanning...", flush=True)
found = False
end = time.time() + 12
while time.time() < end:
    text = read(1)
    if "GameSir" in text or ADDR in text:
        found = True; print("FOUND!", flush=True); break
cmd("scan off", ["stopped"], 3)

if not found:
    print("NOT FOUND in 12s", flush=True)
    proc.stdin.write(b"quit"); proc.stdin.flush(); proc.wait()
    sys.exit(1)

# 3. 配对
print("pairing...", flush=True)
r = cmd("pair " + ADDR, ["Pairing successful", "Failed", "auth"], 30)
if "successful" in r:
    print("PAIR OK!", flush=True)
    cmd("trust " + ADDR, ["succeeded"], 5)
    # 4. HID 连接
    r2 = cmd("connect " + ADDR + " " + HID, ["successful", "Failed", "Already"], 20)
    print("HID: " + r2[-100:], flush=True)
else:
    print("PAIR FAIL: " + r[-150:], flush=True)

# 5. 状态
info = cmd("info " + ADDR, ["Name"], 5)
for l in info.split("\n"):
    if any(k in l for k in ("Name", "Paired", "Connected")):
        print(l.strip(), flush=True)

proc.stdin.write(b"quit"); proc.stdin.flush(); proc.wait()
