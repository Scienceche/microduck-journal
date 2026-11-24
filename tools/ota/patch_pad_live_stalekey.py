#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复 pad_live.py 抓码模式结束后原始节点按键状态残留(2026-11-24)。

缺陷: 抓码模式结束 → 桥重启并重新 EVIOCGRAB 原始节点 → 监视器对该节点的 fd
      不再收到事件; 但 rescan() 只在节点路径变化时动作, 路径没变就直接 continue,
      于是最后读到的 "按下"(val=1) 永久留在页面上, 表现为 LB/TL2 常亮。
修法:
  1) hold_loop 到点恢复桥/padd 后, 主动关闭原始(raw)与媒体(cons)节点的 fd 并清零其
     按键/轴状态; 这些节点归桥独占, 监视器此后本就读不到, 不应保留陈旧值。
  2) reader_loop 对节点 read 得到 0 字节(EOF/被抢走)时同样清零, 不等待路径变化。
"""
import ast
import shutil
import sys
from pathlib import Path

P = Path("/root/pad_live.py")
BAK = Path("/root/pad_live.py.bak-stalekey")

src = P.read_text(encoding="utf-8")
assert "hold_active" in src, "需先打 holdfix 补丁"
if "def _drop_node(" in src:
    print("已修过, 跳过")
    sys.exit(0)

if not BAK.exists():
    shutil.copy2(P, BAK)

NODES_LINE = [l for l in src.splitlines() if l.startswith("NODES = ")]
assert NODES_LINE, "NODES 定义未找到"
print("NODES:", NODES_LINE[0])

# ---- 1) 新增 _drop_node: 关闭某类节点的 fd 并清零状态 ----
a1 = "def push_event(dev, name, val):"
b1 = '''def _drop_node(kind, reason=''):
    """关闭某类节点的 fd 并清零其按键/轴状态。

    节点被桥重新独占后本进程读不到任何事件, 保留最后读到的值会让页面上的按键常亮。
    只在持锁状态下调用。
    """
    fd = fds.pop(kind, None)
    if fd is not None:
        try:
            fd.close()
        except OSError:
            pass
    paths[kind] = None
    st = state[kind]
    for c in list(st['buttons'].keys()):
        if st['buttons'][c]:
            st['buttons'][c] = 0
    st['axes'].clear()
    st['present'] = False
    if reason:
        push_event('系统', reason, 1)


def push_event(dev, name, val):


'''.rstrip("\n") + "\n\n\n"
assert src.count(a1) == 1, "push_event 锚点数量异常: %d" % src.count(a1)
src = src.replace(a1, b1, 1)

# ---- 2) hold_loop: 恢复桥/padd 后丢弃归桥独占的节点 ----
a2 = '''            hold_active = False
            push_event('系统', '抓码模式结束，桥与 padd 已恢复', 1)
'''
b2 = '''            hold_active = False
            with lock:
                # 桥回来后原始/媒体节点归它独占, 监视器的 fd 不再收到事件:
                # 丢弃它们并清零状态, 否则最后的"按下"会常亮在页面上。
                for kind in list(fds.keys()):
                    if kind != 'xbox':
                        _drop_node(kind)
            push_event('系统', '抓码模式结束，桥与 padd 已恢复', 1)
'''
assert a2 in src, "hold_loop 恢复段未逐字匹配"
src = src.replace(a2, b2, 1)

# ---- 3) reader_loop: read 返回空(节点被抢走/EOF) 时清零 ----
a3 = '''            try:
                data = fd.read(4096)
            except (BlockingIOError, OSError):
                continue
'''
b3 = '''            try:
                data = fd.read(4096)
            except (BlockingIOError, OSError):
                continue
            if not data:
                # 0 字节: 节点消失或已被桥独占。清零状态, 等待 rescan 重新接入。
                with lock:
                    if fds.get(kind) is fd:
                        _drop_node(kind)
                continue
'''
assert a3 in src, "reader_loop read 段未逐字匹配"
src = src.replace(a3, b3, 1)

ast.parse(src)
P.write_text(src, encoding="utf-8")

chk = P.read_text(encoding="utf-8")
ast.parse(chk)
for probe in ("def _drop_node(", "if fds.get(kind) is fd:", "if kind != 'xbox':"):
    assert probe in chk, probe + " 未落盘"
print("stale-key 修复落盘 ✓ 备份: %s" % BAK)
