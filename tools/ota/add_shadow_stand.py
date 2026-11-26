#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在鸭子上给 /etc/robot/feetech-ft5/robotd.toml 的 ["policy"] 段插入 shadow_stand 一行。

该文件的实际风格(2026-11-26 上电后 cat -A 实测): 段名与键名都带引号、LF 行尾、无缩进:
    ["policy"]
    "custom_only" = true
    ...
    "stand" = "/opt/robot/feetech-ft5-r5/models/13f456ab.../policy.onnx"
插入行与 "stand" 行同款风格, 紧跟其后。

安全设计(该文件有过两次整文件 0xFF 损坏的历史, 原因未明):
  1. 原始文件整体断言: 无 0xFF 字节、有 ["policy"] 段、没有 shadow_stand 键(幂等)。
  2. 新内容在内存里构造: 原字节 + 一行, 不做任何查找替换 —— 期望内容是确定性的。
  3. 期望内容写到 robotd.toml.new, 逐字节比对, 不等即中止(原文件未被碰)。
  4. 备份原文件(带时间戳, 备份哈希必须等于原文件哈希), os.replace 原子替换, 再读回
     最终文件逐字节比对期望内容。
  5. tomllib 解析最终文件: [policy] 段除多出 shadow_stand 外, 其余每个键的值逐一等于原文件。
任何一步失败都立刻退出, 原文件保持不动。
用法(在鸭子上): python3 add_shadow_stand.py
"""
import hashlib
import os
import re
import shutil
import sys
import time
import tomllib

TOML = "/etc/robot/feetech-ft5/robotd.toml"
CANDIDATE = "/opt/robot/feetech-ft5-r5/models/205c7469667f8d4e5f9875e55a0d76122a8767c5a7cebe4e4038a24ecc99d062/policy.onnx"
NEW_LINE = '"shadow_stand" = "%s"' % CANDIDATE

orig_bytes = open(TOML, "rb").read()
orig_sha = hashlib.sha256(orig_bytes).hexdigest()

# ---- 1. 原始文件断言 ----
assert b"\xff" not in orig_bytes, "原始文件里有 0xFF 字节 —— 就是历史上那种损坏, 中止"
assert b"shadow_stand" not in orig_bytes, "shadow_stand 已存在, 不做重复插入"
text = orig_bytes.decode("utf-8")
assert '\r' not in text, "该文件应为 LF 行尾, 现在混入了 CR, 中止"
assert os.path.isfile(CANDIDATE), "候选 ONNX 不存在: %s" % CANDIDATE

lines = text.split("\n")

# ---- 2. 定位 ["policy"] 段(段名带引号, 按实测风格逐字匹配) ----
policy_start = None
for i, ln in enumerate(lines):
    if ln.strip() == '["policy"]':
        policy_start = i
        break
assert policy_start is not None, '找不到 ["policy"] 段头'
policy_end = len(lines)
for i in range(policy_start + 1, len(lines)):
    if re.match(r"^\s*\[", lines[i]):
        policy_end = i
        break
assert policy_end > policy_start + 1, "[policy] 段是空的?"

# ---- 3. 找 "stand" 行, 插在其后 ----
stand_idx = None
for i in range(policy_start + 1, policy_end):
    if re.match(r'^\s*"stand"\s*=', lines[i]):
        stand_idx = i
        break
assert stand_idx is not None, '段内找不到 "stand" 行 —— 与实测结构不符, 中止'
indent = re.match(r'^(\s*)"stand"\s*=', lines[stand_idx]).group(1)
expected_lines = lines[: stand_idx + 1] + [indent + NEW_LINE] + lines[stand_idx + 1 :]
expected = "\n".join(expected_lines).encode("utf-8")
assert b"\xff" not in expected
assert expected.count(b"shadow_stand") == 1

# ---- 4. 写临时文件并逐字节比对 ----
NEW = TOML + ".new"
open(NEW, "wb").write(expected)
back = open(NEW, "rb").read()
assert back == expected, "临时文件写坏(逐字节不等), 中止; 原文件未动"
assert len(back) == len(orig_bytes) + len(NEW_LINE.encode("utf-8")) + 1, (
    "长度不对: %d -> %d" % (len(orig_bytes), len(back)))

# ---- 5. 备份 + 原子替换 + 读回复核 ----
ts = time.strftime("%Y%m%d-%H%M%S")
BAK = "%s.bak-shadow-%s" % (TOML, ts)
shutil.copy2(TOML, BAK)
bak_sha = hashlib.sha256(open(BAK, "rb").read()).hexdigest()
assert bak_sha == orig_sha, "备份与原文件哈希不一致, 中止"
os.replace(NEW, TOML)
final_bytes = open(TOML, "rb").read()
final_sha = hashlib.sha256(final_bytes).hexdigest()
assert final_bytes == expected, "替换后读回与期望内容不等! 备份在 " + BAK
assert b"\xff" not in final_bytes

# ---- 6. tomllib 双重解析 ----
with open(TOML, "rb") as f:
    policy = tomllib.load(f)["policy"]
before = tomllib.loads(text)["policy"]
assert policy["shadow_stand"] == CANDIDATE, repr(policy.get("shadow_stand"))
assert set(policy) == set(before) | {"shadow_stand"}, (
    "除 shadow_stand 外 [policy] 段还有别的键变化: %s" % (set(policy) ^ set(before)))
for k in before:
    assert policy[k] == before[k], "键 %s 的值变了" % k

print("OK 插入行: %s" % NEW_LINE)
print("OK 插入位置: 原第 %d 行(\"stand\")之后" % (stand_idx + 1))
print("OK 备份: %s (sha256 %s)" % (BAK, orig_sha[:16]))
print("OK 原文件 sha256 %s -> 新文件 sha256 %s" % (orig_sha[:16], final_sha[:16]))
print("OK tomllib 复核: [policy] %d 个键, 仅新增 shadow_stand, 其余键值逐一相同" % len(policy))
