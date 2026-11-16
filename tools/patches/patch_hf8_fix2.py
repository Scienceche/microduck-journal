#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from pathlib import Path

P = Path(r"C:\duck_work\kissqy-firmware\robotd\src\feetech_commissioning.rs")
s = P.read_text(encoding="utf-8")

# 1) format 内联捕获与位置参数冲突 → 去掉位置参数
old = """            moving.active_id, moving.completed, suffix
        )));"""
new = """            moving.active_id, moving.completed
        )));"""
assert old in s
s = s.replace(old, new, 1)

# 2) 第二个 process_job 调用点补 held_ids
import re
# 找所有 process_job( 调用并检查是否带 &mut held_ids
count = 0
out = []
i = 0
text = s
while True:
    j = text.find("process_job(", i)
    if j < 0:
        out.append(text[i:])
        break
    # 找该调用的闭合 "                );" 块
    k = text.find(");", j)
    block = text[j:k]
    if "held_ids" not in block and "fn process_job" not in block:
        # 在 ");" 前插入参数 — 找最后一个参数行
        lastnl = text.rfind("\n", j, k)
        indent = " " * 20
        text = text[:lastnl+1] + indent + "&mut held_ids,\n" + text[lastnl+1:]
        # 重算 k
        k = text.find(");", j)
        count += 1
    out.append(text[i:j])
    i = j
s = text
assert count == 1, "expected 1 unpatched call, got %d" % count
P.write_text(s, encoding="utf-8")
print("fix2 落盘 ✓ (补了 %d 个调用点)" % count)
