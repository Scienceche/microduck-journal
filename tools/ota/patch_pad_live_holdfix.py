#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复 pad_live.py 抓码模式不自动恢复的 bug(2026-11-24)。

缺陷: hold_loop() 里的 stopped 恒为 False, 恢复分支永不执行 →
      点「抓原始键码」后桥与 padd 一直停着。
修法: start_hold() 置模块级 hold_active=True, hold_loop 到点恢复并清零。
      另加进程启动自愈: 若服务启动时桥/padd 是 inactive 且不在抓码窗口内, 拉起。
"""
import ast
import shutil
import sys
import time
from pathlib import Path

P = Path("/root/pad_live.py")
BAK = Path("/root/pad_live.py.bak-holdfix")

src = P.read_text(encoding="utf-8")
assert "def hold_loop():" in src, "hold_loop 未找到"
assert "def start_hold():" in src, "start_hold 未找到"
if "hold_active" in src:
    print("已修过, 跳过")
    sys.exit(0)

# 备份
if not BAK.exists():
    shutil.copy2(P, BAK)

# ---- 1) 全局状态 ----
a1 = "lock = threading.Lock()"
b1 = ("lock = threading.Lock()\n"
      "# 抓码窗口是否处于激活中(2026-11-24 修复: 原先 hold_loop 的 stopped 恒为 False,\n"
      "# 到点恢复的分支永远不执行, 桥与 padd 会被一直停着)\n"
      "hold_active = False")
assert src.count(a1) == 1, "lock 锚点数量异常: %d" % src.count(a1)
src = src.replace(a1, b1, 1)

# ---- 2) hold_loop: 到点恢复 ----
a2 = '''def hold_loop():
    global hold_until
    stopped = False
    while True:
        now = time.time()
        if stopped and now >= hold_until:
            for unit in ('gamesir-xboxd.service', 'padd.service'):
                subprocess.run(['systemctl', 'start', unit], timeout=30,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            push_event('系统', '抓码模式结束，桥与 padd 已恢复', 1)
            stopped = False
        time.sleep(1.0)
'''
b2 = '''def _units_active(units):
    out = {}
    for u in units:
        try:
            r = subprocess.run(['systemctl', 'is-active', u], capture_output=True,
                               text=True, timeout=10)
            out[u] = (r.stdout.strip() == 'active')
        except Exception:
            out[u] = False
    return out


HOLD_UNITS = ('gamesir-xboxd.service', 'padd.service')


def hold_loop():
    global hold_until, hold_active
    while True:
        now = time.time()
        if hold_active and now >= hold_until:
            for unit in HOLD_UNITS:
                subprocess.run(['systemctl', 'start', unit], timeout=30,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            hold_active = False
            push_event('系统', '抓码模式结束，桥与 padd 已恢复', 1)
        time.sleep(1.0)
'''
assert a2 in src, "hold_loop 原文未逐字匹配"
src = src.replace(a2, b2, 1)

# ---- 3) start_hold: 置位 ----
a3 = '''def start_hold():
    global hold_until
    with lock:
        if time.time() < hold_until:
            return False
        hold_until = time.time() + 90
'''
b3 = '''def start_hold():
    global hold_until, hold_active
    with lock:
        if time.time() < hold_until and hold_active:
            return False
        hold_until = time.time() + 90
        hold_active = True
'''
assert a3 in src, "start_hold 原文未逐字匹配"
src = src.replace(a3, b3, 1)

# ---- 4) 启动自愈: 服务起来时若桥/padd 不在跑且无抓码窗口, 拉起 ----
a4 = "    threading.Thread(target=hold_loop, daemon=True).start()"
b4 = '''    threading.Thread(target=hold_loop, daemon=True).start()
    # 启动自愈(2026-11-24): 上次抓码模式若因本进程被杀而没走到恢复, 桥/padd 会留在
    # inactive。进程重新起来时把它们拉回——此刻不可能处于抓码窗口内。
    st = _units_active(HOLD_UNITS)
    dead = [u for u, ok in st.items() if not ok]
    if dead:
        for u in dead:
            subprocess.run(['systemctl', 'start', u], timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print('[pad_live] 启动自愈: 已拉起 %s' % ', '.join(dead), flush=True)'''
assert src.count(a4) == 1, "线程启动锚点数量异常: %d" % src.count(a4)
src = src.replace(a4, b4, 1)

ast.parse(src)
P.write_text(src, encoding="utf-8")

# ---- 落盘复核(不信任上面的打印) ----
chk = P.read_text(encoding="utf-8")
ast.parse(chk)
for probe in ("hold_active = False", "if hold_active and now >= hold_until:",
              "hold_active = True", "启动自愈"):
    assert probe in chk, probe + " 未落盘"
assert "stopped = False" not in chk, "旧 stopped 逻辑仍残留"
print("修复落盘 ✓ 备份: %s" % BAK)
