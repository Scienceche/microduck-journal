#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构造回滚演练用坏包: policy.onnx = 随机字节, manifest/runtime 哈希对它重算
(哈希链自洽 → 能过所有校验), robotd 加载必然失败 → 触发健康检查失败 → 自动回滚。"""
import hashlib
import json
import shutil
from pathlib import Path

SRC = Path(r"C:\duck_work\pkg_build")
GOOD = SRC / "models" / "7a2fddbd3ba0609db257c8c3befefc3e3bdebedb0dc8f674827b20c41a63ff7c"
BAD = SRC / "models" / "dead0000bad0000bad0000bad0000bad0000bad0000bad0000bad0000bad00ff"
if BAD.exists():
    shutil.rmtree(BAD)
shutil.copytree(GOOD, BAD)

# 随机字节 onnx(不是合法 ONNX)
(bad_onnx := BAD / "policy.onnx").write_bytes(
    b"GARBAGE-NOT-ONNX-" + hashlib.sha256(b"drill").digest() * 100)
bad_sha = hashlib.sha256(bad_onnx.read_bytes()).hexdigest()

m = json.loads((BAD / "manifest.json").read_text(encoding="utf-8"))
m["onnx_sha256"] = bad_sha
m["name"] = "DRILL_bad_package_rollback_test"
(BAD / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
r = json.loads((BAD / "deployment-runtime.json").read_text(encoding="utf-8"))
r["model_sha256"] = bad_sha
(BAD / "deployment-runtime.json").write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")

# 自检: 哈希链自洽(能过校验), 但内容是垃圾
m2 = json.loads((BAD / "manifest.json").read_text(encoding="utf-8"))
real = hashlib.sha256((BAD / "policy.onnx").read_bytes()).hexdigest()
assert m2["onnx_sha256"] == real == r["model_sha256"]
assert (BAD / "policy.onnx").read_bytes()[:5] == b"GARBA"
print("坏包就绪:", BAD)
print("sha:", bad_sha[:16], "(哈希自洽, 内容为垃圾 → robotd 必拒载)")
