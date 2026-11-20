#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修 duck_console.py: PAGE 模板内 JS 字符串里的 \\n 会被 Python 解析成真换行,
导致 <script> 块语法错误(已发生两次)。本脚本找出 PAGE 模板内所有反斜杠n并替换为空格。"""
import ast
import re
from pathlib import Path

P = Path(r"C:\duck_work\ota\duck_console.py")
src = P.read_text(encoding="utf-8")
ast.parse(src)

m = re.search(r'PAGE = """(.*?)"""', src, re.S)
assert m, "PAGE 模板未找到"
tpl = m.group(1)

# 找所有字面 \n (反斜杠+n 两个字符) 的位置
hits = [i for i in range(len(tpl) - 1) if tpl[i] == "\\" and tpl[i + 1] == "n"]
print(f"模板内字面 \\n 数量: {len(hits)}")
for i in hits:
    line = tpl[:i].count("\n") + 1
    ctx = tpl[max(0, i - 40):i + 20].replace("\n", "⏎")
    print(f"  模板第{line}行: ...{ctx}...")

if hits:
    tpl_new = tpl.replace("\\n", " ")
    src_new = src.replace(tpl, tpl_new, 1)
    assert "\\n" not in re.search(r'PAGE = """(.*?)"""', src_new, re.S).group(1), "仍残留"
    P.write_text(src_new, encoding="utf-8")
    print("已全部替换为空格并落盘")

# 复核: 渲染后的 script 里不应再有 Python 展开的真换行断串问题
src2 = P.read_text(encoding="utf-8")
tpl2 = re.search(r'PAGE = """(.*?)"""', src2, re.S).group(1)
assert "\\n" not in tpl2, "复核失败"
ast.parse(src2)
print("复核 ✓ 模板内已无字面 \\n")
