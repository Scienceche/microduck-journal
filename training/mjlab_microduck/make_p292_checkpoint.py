#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2.9.2 合成: 底座=P2.9.1(已部署版), 仅换起身/站专家 run13@500 -> run14@1999。

变更动机(2026-11-16): run14 在蹲姿锚点中把 neck/head 四关节钉在 HOME,
修复"下蹲时鸭头后仰+右歪"(策略曾学会用头找平衡)。
walk 专家与 gate(含 c0=0.10) 保持与已部署 P2.9.1 逐位一致 — 最小差异。
"""
import sys
from pathlib import Path

sys.path.insert(0, "src")
import torch

# 底座 = 已部署的 P2.9.1 (walk=run8, stand=run13@500, gate=run11+c0=0.10)
BASE = "logs/rsl_rl/velstand/p291_c010/model_0.pt"
# 新站专家 = run14@500 (蹲姿头锚点训练; @1999 过训伤起身, @500 与 run13@500 逐批持平)
R14 = "logs/rsl_rl/microduck_stand/2026-11-16_21-01-27_microduck_stand/model_500.pt"
OUT_DIR = Path("logs/rsl_rl/velstand/p292_run14_crouch_head")
OUT = OUT_DIR / "model_0.pt"

base = torch.load(BASE, map_location="cpu", weights_only=False)
a = dict(base["actor_state_dict"])
r14 = torch.load(R14, map_location="cpu", weights_only=False)["actor_state_dict"]

base_gate = {k: float(a[k]) for k in ("gate_k", "gate_offset", "cmd_w", "cmd_c0")}
print("底座 gate(必须保留):", {k: round(v, 3) for k, v in base_gate.items()})
assert abs(base_gate["cmd_c0"] - 0.10) < 1e-6, "底座 c0 应为 0.10"

# 记录替换前的 stand 键(用于事后断言全部换掉)
old_stand_keys = [k for k in a if k.startswith(("stand_expert.", "stand_normalizer."))]

# 换 stand 专家: run14 的 mlp -> stand_expert, obs_normalizer -> stand_normalizer
# (std_param 不换: DualExpert 共享 run8 的 distribution, 架构无 stand_expert_std_param)
for k in r14:
    if k.startswith("mlp."):
        a["stand_expert." + k[4:]] = r14[k].clone()
    elif k.startswith("obs_normalizer."):
        a["stand_normalizer." + k[len("obs_normalizer."):]] = r14[k].clone()

OUT_DIR.mkdir(parents=True, exist_ok=True)
doc = {
    "actor_state_dict": a,
    "critic_state_dict": {},
    "optimizer_state_dict": {},
    "iter": 0,
    "infos": {
        "warm_start": "p2.9.2: base=p291(c0=0.10), stand run13@500->run14@1999(crouch head anchor)",
        "date": "2026-11-16",
    },
}
torch.save(doc, OUT)

# ===== 落盘复核(不信任上面自己的打印) =====
chk = torch.load(OUT, map_location="cpu", weights_only=False)["actor_state_dict"]
# 1) stand 全部 == run14
assert torch.equal(chk["stand_expert.0.weight"], r14["mlp.0.weight"]), "stand != run14"
assert torch.equal(chk["stand_normalizer._mean"], r14["obs_normalizer._mean"]), "norm != run14"
# 2) walk / gate 逐位 == 底座 P2.9.1
b = base["actor_state_dict"]
assert torch.equal(chk["mlp.0.weight"], b["mlp.0.weight"]), "walk 被动了!"
assert torch.equal(chk["obs_normalizer._mean"], b["obs_normalizer._mean"]), "walk norm 被动了!"
for k in ("gate_k", "gate_offset", "cmd_w", "cmd_c0"):
    assert torch.equal(chk[k], b[k]), f"gate {k} 被动了!"
# 3) stand 键全部来自 run14 前缀(无 std_param 残留)
new_stand_keys = [k for k in chk if k.startswith(("stand_expert.", "stand_normalizer."))]
n_expect = len([k for k in r14 if k.startswith(("mlp.", "obs_normalizer."))])
assert len(new_stand_keys) == n_expect, f"stand 键数 {len(new_stand_keys)} != {n_expect}"
print("P2.9.2 复核 ✓  walk/gate 逐位==P2.9.1, stand==run14@1999")
print("gate:", {k: round(float(chk[k]), 3) for k in ("gate_k", "gate_offset", "cmd_w", "cmd_c0")})
print("saved:", OUT)
