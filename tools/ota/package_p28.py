#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2.8 部署包: 克隆模板, 换 P2.8 双专家 ONNX, STAND3 HOME, 重算哈希链。"""
import hashlib
import json
import shutil
from pathlib import Path

SRC = Path(r"C:\duck_work\pkg_build")
OUR = Path(r"C:\duck_work\microduck_rl\logs\rsl_rl\velstand\p28_run12_500_init\p28_dual.onnx")
NEW_SHA = hashlib.sha256(OUR.read_bytes()).hexdigest()
OUT = SRC / f"models/{NEW_SHA}"
OUT.mkdir(parents=True, exist_ok=True)

HOME_NEW = {
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
    contract["training"]["checkpoint"] = 500
(OUT / "deployment-contract.json").write_text(
    json.dumps(contract, ensure_ascii=False, indent=2), encoding="utf-8")

m = json.loads((SRC / "manifest.json").read_text(encoding="utf-8"))
m["onnx_sha256"] = NEW_SHA
m["name"] = "p28_run12_500"
m["description"] = ("P2.8 双专家: run8行走 + run12@500起身(桥式训练) + run11 gate; "
                    "双归一化器; sim 六批全100% 含桥式/后仰接手")
m["training"].update({
    "checkpoint": 500,
    "source_file": "C:/duck_work/microduck_rl/logs/rsl_rl/microduck_stand/2026-11-13_21-56-22_microduck_stand/model_500.pt",
})
m["training_snapshot_sha256"] = hashlib.sha256(Path(
    r"C:\duck_work\microduck_rl\logs\rsl_rl\microduck_stand\2026-11-13_21-56-22_microduck_stand\model_500.pt"
).read_bytes()).hexdigest()
(OUT / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")

r = json.loads((SRC / "deployment-runtime.json").read_text(encoding="utf-8"))
r["model_sha256"] = NEW_SHA
(OUT / "deployment-runtime.json").write_text(
    json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")

for aux in ("export-data.zip", "training-request.json"):
    if (SRC / aux).exists():
        shutil.copy(SRC / aux, OUT / aux)
mi = {"name": "P2.8 双专家软门控(run12@500)", "source_filename": "p28_dual.onnx"}
(OUT / "model-info.json").write_text(json.dumps(mi, ensure_ascii=False), encoding="utf-8")

m2 = json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))
r2 = json.loads((OUT / "deployment-runtime.json").read_text(encoding="utf-8"))
c2 = json.loads((OUT / "deployment-contract.json").read_text(encoding="utf-8"))
real = hashlib.sha256((OUT / "policy.onnx").read_bytes()).hexdigest()
assert m2["onnx_sha256"] == NEW_SHA == r2["model_sha256"] == real
assert m2["task"] == c2["task"]
print("包目录:", OUT)
print("sha256:", NEW_SHA[:16])
print("task:", m2["task"])
print("哈希链 ✓ task 契约 ✓")
