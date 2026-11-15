#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run11 部署包: 克隆 kissqy 模板结构, 换 run11 双专家 ONNX, STAND3 HOME, 重算哈希链。"""
import hashlib
import json
import shutil
from pathlib import Path

SRC = Path(r"C:\duck_work\pkg_build")
OUR = SRC / ".." / "microduck_rl" / "logs" / "rsl_rl" / "velstand" / "2026-11-12_13-21-15_velstand" / "run11_dual.onnx"
OUR = Path(r"C:\duck_work\microduck_rl\logs\rsl_rl\velstand\2026-11-12_13-21-15_velstand\run11_dual.onnx")
NEW_SHA = hashlib.sha256(OUR.read_bytes()).hexdigest()
OUT = SRC / f"models/{NEW_SHA}"
OUT.mkdir(parents=True, exist_ok=True)

HOME_NEW = {  # STAND3 前倾 HOME(rad) — 与现役部署一致
    "left_hip_yaw": 0.0, "left_hip_roll": -0.0873, "left_hip_pitch": -0.3350,
    "left_knee": -0.0049, "left_ankle": 0.3315,
    "neck_pitch": 0.3491, "head_pitch": 0.3491, "head_yaw": 0.0, "head_roll": 0.0,
    "right_hip_yaw": 0.0, "right_hip_roll": 0.0873, "right_hip_pitch": 0.3350,
    "right_knee": 0.0049, "right_ankle": -0.3315,
}

shutil.copy(OUR, OUT / "policy.onnx")

contract = json.loads((SRC / "deployment-contract.json").read_text(encoding="utf-8"))
for j in contract["model"]["joints"]:
    n = j["name"]
    if n in HOME_NEW:
        j["home_rad"] = HOME_NEW[n]
        j["home_deg"] = HOME_NEW[n] * 57.29577951308232
if "training" in contract:
    contract["training"]["checkpoint"] = 1999
(OUT / "deployment-contract.json").write_text(
    json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")

m = json.loads((SRC / "manifest.json").read_text(encoding="utf-8"))
m["onnx_sha256"] = NEW_SHA
m["name"] = "run11_dual_gate"
m["description"] = ("P2.7 双专家(run8行+run6起)+双输入gate(重力+命令)+双归一化器; "
                    "gate-only 训练 2000iter; sim 起身 100%/2.5s; "
                    "专家与双归一化器训练后逐位不变(实证)")
m["training"].update({
    "checkpoint": 1999,
    "source_file": "C:/duck_work/microduck_rl/logs/rsl_rl/velstand/2026-11-12_13-21-15_velstand/model_1999.pt",
})
m["training_snapshot_sha256"] = hashlib.sha256(Path(
    r"C:\duck_work\microduck_rl\logs\rsl_rl\velstand\2026-11-12_13-21-15_velstand\model_1999.pt"
).read_bytes()).hexdigest()
(OUT / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")

r = json.loads((SRC / "deployment-runtime.json").read_text(encoding="utf-8"))
r["model_sha256"] = NEW_SHA
(OUT / "deployment-runtime.json").write_text(
    json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")

for aux in ("export-data.zip", "training-request.json"):
    if (SRC / aux).exists():
        shutil.copy(SRC / aux, OUT / aux)
mi = {"name": "run11 双专家软门控", "source_filename": "run11_dual.onnx"}
(OUT / "model-info.json").write_text(json.dumps(mi, ensure_ascii=False), encoding="utf-8")

# 哈希链自检
m2 = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
r2 = json.loads((OUT / "deployment-runtime.json").read_text(encoding="utf-8"))
c2 = json.loads((OUT / "deployment-contract.json").read_text(encoding="utf-8"))
real = hashlib.sha256((OUT / "policy.onnx").read_bytes()).hexdigest()
assert m2["onnx_sha256"] == NEW_SHA == r2["model_sha256"] == real
assert m2["task"] == c2["task"]
print("包目录:", OUT)
print("sha256:", NEW_SHA)
print("task:", m2["task"])
print("哈希链自检 ✓  task 契约 ✓")
