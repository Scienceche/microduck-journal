#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2.8 合成: 走=run8(含归一化器), 起/站=run12@500(含其归一化器), gate=run11 学得值。"""
import sys
from pathlib import Path

sys.path.insert(0, "src")
import torch

R8 = torch.load("logs/rsl_rl/velocity/2026-11-10_13-20-40_velocity/model_3999.pt",
                map_location="cpu", weights_only=False)["actor_state_dict"]
R12 = torch.load("logs/rsl_rl/microduck_stand/2026-11-13_21-56-22_microduck_stand/model_500.pt",
                 map_location="cpu", weights_only=False)["actor_state_dict"]
R11 = torch.load("logs/rsl_rl/velstand/2026-11-12_13-21-15_velstand/model_1999.pt",
                 map_location="cpu", weights_only=False)["actor_state_dict"]

a = {}
# 走专家 = run8 (mlp + obs_normalizer)
for k in R8:
    if k.startswith("mlp.") or k.startswith("obs_normalizer.") or k == "distribution.std_param":
        a[k] = R8[k].clone()
# 起身/站专家 = run12@500 (mlp→stand_expert, obs_normalizer→stand_normalizer)
for k in R12:
    if k.startswith("mlp."):
        a["stand_expert." + k[4:]] = R12[k].clone()
    elif k.startswith("obs_normalizer."):
        a["stand_normalizer." + k[len("obs_normalizer."):]] = R12[k].clone()
# gate = run11 训练后的值(钳制包络内)
for k in ("gate_k", "gate_offset", "cmd_w", "cmd_c0"):
    a[k] = R11[k].clone()

out_dir = Path("logs/rsl_rl/velstand/p28_run12_500_init")
out_dir.mkdir(parents=True, exist_ok=True)
doc = {
    "actor_state_dict": a,
    "critic_state_dict": {},
    "optimizer_state_dict": {},
    "iter": 0,
    "infos": {"warm_start": "p2.8: walk=run8, stand=run12@500(bridge-trained), gate=run11-learned"},
}
p = out_dir / "model_0.pt"
torch.save(doc, p)

# 落盘复核
chk = torch.load(p, map_location="cpu", weights_only=False)["actor_state_dict"]
assert torch.equal(chk["mlp.0.weight"], R8["mlp.0.weight"]), "walk != run8"
assert torch.equal(chk["stand_expert.0.weight"], R12["mlp.0.weight"]), "stand != run12"
assert torch.equal(chk["stand_normalizer._mean"], R12["obs_normalizer._mean"]), "norm6 != run12"
assert torch.equal(chk["obs_normalizer._mean"], R8["obs_normalizer._mean"]), "norm8 != run8"
print("P2.8 gate:", {k: round(float(chk[k]), 3) for k in
                     ("gate_k", "gate_offset", "cmd_w", "cmd_c0")})
print("keys:", len(chk), "saved:", p)
