#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组装新版 pad_live.py: 新后端(pad_live_new_backend.py) + 原版 PAGE(逐字不动)。

校验:
  - PAGE 字符串与 11-15 原版逐字相等
  - 后端不含旧缺陷(stopped 标记 / 对所有 kind 无差别 drop)
  - py 语法通过
"""
import ast
import re
import sys
from pathlib import Path

ORIG = Path(r"C:\duck_work\ota\pad_live_original_1115.py")
NEW_BE = Path(r"C:\duck_work\ota\pad_live_new_backend.py")
OUT = Path(r"C:\duck_work\ota\pad_live.py")


def extract_page(src):
    m = re.search(r'\nPAGE = """(.*?)"""\n', src, re.S)
    assert m, "PAGE 未找到"
    return m.group(1)


orig = ORIG.read_text(encoding="utf-8")
page = extract_page(orig)
assert "<!doctype html>" in page and "holdBtn" in page, "PAGE 内容异常"

backend = NEW_BE.read_text(encoding="utf-8")
# 去掉模块文档串之后所有内容, 在末尾接上 PAGE 与入口
assert 'if __name__ == \'__main__\':' in backend
assert 'PAGE = """' not in backend, "新后端不应自带 PAGE"

combined = backend.rstrip("\n") + '\n\n\nPAGE = """' + page + '"""\n\n\nif __name__ == \'__main__\':\n    main()\n'

ast.parse(combined)

# --- 后端不含旧缺陷 ---
assert "stopped = False" not in combined, "旧 stopped 逻辑残留"
assert "if kind != 'xbox':" not in combined, "旧的无差别 drop 残留"
# --- 新规则在位 ---
for probe in ("def _allowed_kinds():", "BRIDGE_OWNED = ('raw', 'cons')",
              "return ('raw', 'cons') if holding else ('xbox',)",
              "def _rescan_once(allowed):", "def _read_ready_locked(kind, fd):",
              "def reader_loop():", "HOLD_SECONDS = 90", "hold_began = None",
              # 到点恢复的判定必须由可直接观察的事实构成(抓码是否结束 + 桥是否在跑),
              # 不能依赖需要手动复位的标记(原版 stopped / 首次重构 restored 都因此失效)
              "if began is not None and ended and not unit_active(BRIDGE_UNIT):",
              # 发起抓码时必须同时记下"发起过"这一事实
              "hold_began = time.time()"):
    assert probe in combined, probe + " 缺失"
# 只禁赋值语句(注释里允许提及这个名字作为事故记录)
assert not re.search(r'^\s*restored\s*=', combined, re.M), "不得再使用 restored 标记位"
assert not re.search(r'^\s*stopped\s*=', combined, re.M), "不得再使用 stopped 标记位"
# --- 抓码结束的顺序: 先 rescan(只留 xbox) 再启动桥 ---
i_rescan = combined.index("_rescan_once(('xbox',))")
i_start = combined.index("subprocess.run(['systemctl', 'start', unit]")
assert i_rescan < i_start, "抓码结束时应先释放 raw/cons 再启动桥"

# --- PAGE 逐字一致 ---
assert extract_page(combined) == page, "PAGE 与原版不一致"

OUT.write_text(combined, encoding="utf-8")
chk = OUT.read_text(encoding="utf-8")
ast.parse(chk)
assert extract_page(chk) == page
print("组装完成: %s" % OUT)
print("  总行数 %d | 后端 %d 行 | PAGE %d 字符(与 11-15 原版逐字一致)" % (
    len(chk.splitlines()),
    len(backend.rstrip("\n").splitlines()) + 4, len(page)))
