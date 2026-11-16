#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2.7 起点: 双归一化器版。
mlp←run8 + obs_normalizer←run8 (走专家原生配对)
stand_expert←run6 + stand_normalizer←run6 (站专家原生配对 — A/B 实证必需)
gate 四标量 (k=12, off=0.5, w=40, c0=0.15)
"""
import sys
from pathlib import Path

sys.path.insert(0, "src")
import torch

R6 = torch.load("logs/rsl_rl/microduck_stand/2026-11-09_09-06-54_microduck_stand/model_3999.pt",
                map_location="cpu", weights_only=False)
R8 = torch.load("logs/rsl_rl/velocity/2026-11-10_13-20-40_velocity/model_3999.pt",
                map_location="cpu", weights_only=False)
P26 = torch.load("logs/rsl_rl/velstand/p26_dual_input_gate_init/model_0.pt",
                 map_location="cpu", weights_only=False)

a = dict(P26["actor_state_dict"])
a6, a8 = R6["actor_state_dict"], R8["actor_state_dict"]

# stand 专家归一化器 ← run6 (键名映射 obs_normalizer.* → stand_normalizer.*)
for k in list(a6):
    if k.startswith("obs_normalizer."):
        a[k.replace("obs_normalizer.", "stand_normalizer.")] = a6[k].clone()

out_dir = Path("logs/rsl_rl/velstand/p27_dual_norm_init")
out_dir.mkdir(parents=True, exist_ok=True)
doc = dict(P26)
doc["actor_state_dict"] = a
doc["infos"] = {"warm_start": "p2.7 dual normalizer (r8+r8norm / r6+r6norm), 4-scalar gate"}
p = out_dir / "model_0.pt"
torch.save(doc, p)

chk = torch.load(p, map_location="cpu", weights_only=False)["actor_state_dict"]
assert torch.equal(chk["stand_normalizer._mean"], a6["obs_normalizer._mean"])
assert torch.equal(chk["obs_normalizer._mean"], a8["obs_normalizer._mean"])
assert torch.equal(chk["stand_expert.0.weight"], a6["mlp.0.weight"])
assert torch.equal(chk["mlp.0.weight"], a8["mlp.0.weight"])
print("P2.7 checkpoint: %d keys" % len(chk))
print("  walk: mlp←run8 + norm←run8 | stand: expert←run6 + norm←run6")
print("saved:", p)
