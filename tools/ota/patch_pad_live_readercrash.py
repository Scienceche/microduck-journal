#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复 stalekey 补丁引入的 reader_loop 崩溃(2026-11-24, 我自己的回归)。

缺陷: hold_loop 恢复桥后用 _drop_node 关闭 raw/cons 节点的 fd; 而 reader_loop 在
      本轮开头已把 fd 列表拷贝到局部 watch, 随后对已关闭的 fd 调用 read() 抛
      ValueError, 不在原 except (BlockingIOError, OSError) 范围内 → 读取线程死亡,
      监视器从此只剩空状态(三个节点全"不在", Xbox 面板空白)。
修法:
  1) reader_loop 读之前确认该 fd 仍是登记在册的那个(fds.get(kind) is fd), 否则跳过。
  2) 读取与解析整体包在 try/except Exception 内, 任何异常都不让线程退出。
  3) 读取线程外加看护循环: 若 reader_loop 意外退出, 1 秒后重启并记录。
"""
import ast
import shutil
import sys
from pathlib import Path

P = Path("/root/pad_live.py")
BAK = Path("/root/pad_live.py.bak-readercrash")

src = P.read_text(encoding="utf-8")
assert "def _drop_node(" in src, "需先打 stalekey 补丁"
if "def _reader_once():" in src:
    print("已修过, 跳过")
    sys.exit(0)

if not BAK.exists():
    shutil.copy2(P, BAK)

# ---- 1) 读之前确认 fd 仍登记在册 ----
a1 = '''        for kind, fd in watch:
            if fd not in r:
                continue
            try:
                data = fd.read(4096)
            except (BlockingIOError, OSError):
                continue
'''
b1 = '''        for kind, fd in watch:
            if fd not in r:
                continue
            with lock:
                # 该 fd 可能已被 _drop_node 关闭并从登记表移除(本轮开头的 watch 是旧快照)
                if fds.get(kind) is not fd:
                    continue
            try:
                data = fd.read(4096)
            except (BlockingIOError, OSError, ValueError):
                continue
'''
assert a1 in src, "reader_loop 读取段未逐字匹配"
src = src.replace(a1, b1, 1)

# ---- 2) 把 reader_loop 改名为 _reader_once, 新 reader_loop 带看护 ----
a2 = "def reader_loop():\n    while True:\n        rescan()"
b2 = '''def _reader_once():
    while True:
        rescan()'''
assert src.count(a2) == 1, "reader_loop 定义锚点数量异常: %d" % src.count(a2)
src = src.replace(a2, b2, 1)

a3 = "def push_event(dev, name, val):"
b3 = '''def reader_loop():
    """读取线程入口: 包一层看护, _reader_once 因任何异常退出后 1 秒重启。

    2026-11-24 回归: 读取循环里一个未捕获的 ValueError 让线程静默死亡, 监视器
    从此只返回空状态而 HTTP 层仍然 200, 页面上看是"手柄没反应"。线程不能再静默死。
    """
    n = 0
    while True:
        try:
            _reader_once()
        except Exception as exc:  # noqa: BLE001 — 看护点, 任何异常都要记录并重启
            n += 1
            print('[pad_live] 读取线程异常退出(第%d次): %r — 1 秒后重启' % (n, exc),
                  flush=True)
            try:
                push_event('系统', '读取线程异常重启: %s' % type(exc).__name__, 1)
            except Exception:
                pass
        time.sleep(1.0)


def push_event(dev, name, val):


'''.rstrip("\n") + "\n\n\n"
assert src.count(a3) == 1, "push_event 锚点数量异常: %d" % src.count(a3)
src = src.replace(a3, b3, 1)

ast.parse(src)
P.write_text(src, encoding="utf-8")

chk = P.read_text(encoding="utf-8")
ast.parse(chk)
for probe in ("def _reader_once():", "def reader_loop():", "if fds.get(kind) is not fd:",
              "except (BlockingIOError, OSError, ValueError):"):
    assert probe in chk, probe + " 未落盘"
print("reader 崩溃修复落盘 ✓ 备份: %s" % BAK)
