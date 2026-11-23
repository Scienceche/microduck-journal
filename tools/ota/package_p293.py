#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2.9.2 部署包: 克隆已部署 P2.9.1 包, 换 run14@500 双专家 ONNX, 重算哈希链。

变更: 仅起身/站专家 run13@500 -> run14@500(蹲姿头锚点: neck/head 钉 HOME)。
walk/gate(含 c0=0.10) 与 P2.9.1 逐位一致。HOME/契约原样克隆。
"""
import hashlib
import json
import shutil
from pathlib import Path

PKG_ROOT = Path(r"C:\duck_work\pkg_build")
BASE = PKG_ROOT / "models" / "7e0dad9de2ef540388384df51d26b63196db0d0a71e425955a7d0e1a88984237"
OUR = Path(r"C:\duck_work\microduck_rl\logs\rsl_rl\velstand\p293_mass828\p293.onnx")
R14_500 = Path(r"C:\duck_work\microduck_rl\logs\rsl_rl\microduck_stand\2026-11-16_21-01-27_microduck_stand\model_500.pt")

assert BASE.is_dir(), "P2.9.1 包目录不存在"
NEW_SHA = hashlib.sha256(OUR.read_bytes()).hexdigest()
OUT = PKG_ROOT / f"models/{NEW_SHA}"
OUT.mkdir(parents=True, exist_ok=True)

# 克隆整包(P2.9.1 全套: contract/runtime/aux)
for f in BASE.iterdir():
    if f.name == "policy.onnx":
        continue
    if f.is_file():
        shutil.copy(f, OUT / f.name)

# 换模型
shutil.copy(OUR, OUT / "policy.onnx")

# manifest: 哈希 + 溯源
m = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
m["onnx_sha256"] = NEW_SHA
m["name"] = "p293_mass828"
m["description"] = ("P2.9.3: 电池整改(NP-F550 108g, 整机828g)适配版; walk=run8不动, "
                    "stand=828g重训@500(六批次100/100/73/100/100/100)")
m["training"].update({
    "checkpoint": 500,
    "source_file": str(R14_500),
    "training_snapshot_sha256": hashlib.sha256(R14_500.read_bytes()).hexdigest(),
    "note": "run14: _CROUCH_ANCHOR_BY_NAME 加 neck/head 四关节(2026-11-16 头后仰+右歪修复)",
})
(OUT / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")

# runtime: 哈希链
r = json.loads((OUT / "deployment-runtime.json").read_text(encoding="utf-8"))
r["model_sha256"] = NEW_SHA
(OUT / "deployment-runtime.json").write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")

mi = {"name": "P2.9.3 双专家(stand=828g质量适配@500, walk不动)", "source_filename": "p293.onnx"}
(OUT / "model-info.json").write_text(json.dumps(mi, ensure_ascii=False), encoding="utf-8")

# ===== 落盘复核 =====
m2 = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
r2 = json.loads((OUT / "deployment-runtime.json").read_text(encoding="utf-8"))
c2 = json.loads((OUT / "deployment-contract.json").read_text(encoding="utf-8"))
real = hashlib.sha256((OUT / "policy.onnx").read_bytes()).hexdigest()
base_m = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))
base_c = json.loads((BASE / "deployment-contract.json").read_text(encoding="utf-8"))
assert m2["onnx_sha256"] == NEW_SHA == r2["model_sha256"] == real, "哈希链断裂"
assert m2["task"] == c2["task"] == base_m["task"], "task 契约不符"
# HOME 与线上逐位一致(克隆未动)
assert json.dumps(c2["model"]["joints"], sort_keys=True) == json.dumps(base_c["model"]["joints"], sort_keys=True), "HOME 被意外改动"
print("包目录:", OUT)
print("sha256:", NEW_SHA[:16])
print("task:", m2["task"])
print("哈希链 ✓ task契约 ✓ HOME与线上逐位一致 ✓")
