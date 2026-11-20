#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查 duck_console.py 的 PAGE 模板内没有字面反斜杠n(Python 会展开成真换行弄坏 JS)。"""
import ast
import re
from pathlib import Path

src = Path(r"C:\duck_work\ota\duck_console.py").read_text(encoding="utf-8")
ast.parse(src)
m = re.search(r'PAGE = """(.*?)"""', src, re.S)
assert m, "PAGE 未找到"
tpl = m.group(1)
bsn = tpl.count("\\n")  # 字面反斜杠+n
print("py-syntax-ok; 模板字面\\n数量:", bsn)
if bsn:
    for i, line in enumerate(tpl.split("\n"), 1):
        if "\\n" in line:
            print(f"  模板第{i}行: {line.strip()[:100]}")
    raise SystemExit("FAIL: 仍有字面\\n")
print("模板干净 PASS")
