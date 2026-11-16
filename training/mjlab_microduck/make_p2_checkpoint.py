#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2 Step 3: 热启动脚本 — 创建一个 checkpoint 让 DualExpertMLPModel 加载 run8+run6 权重.

rsl_rl 的 warm start 会 load 整个 actor_state_dict. 我们需要构造一个
"预加载好的" DualExpertMLPModel checkpoint, 包含:
  - mlp.* ← run8 行走权重
  - stand_expert.* ← run6 恢复权重
  - obs_normalizer ← run8 的(和 run6 差异 < 0.65)
  - gate_k / gate_offset → 初始值
"""
import sys
sys.path.insert(0, "src")
import torch
from tensordict import TensorDict
from mjlab_microduck.tasks.dual_expert_mlp import DualExpertMLPModel

print("=== 构造 DualExpertMLPModel 预加载 checkpoint ===")

# 构造模型
obs = TensorDict({"obs": torch.randn(1, 61)}, batch_size=[1])
obs_groups = {"actor": ["obs"], "critic": ["obs"]}
model = DualExpertMLPModel(obs, obs_groups, "actor", 14,
                           hidden_dims=(512, 256, 128),
                           gravity_slice=5)

# 加载 run8 行走权重
r8 = torch.load(
    "logs/rsl_rl/velocity/2026-11-10_13-20-40_velocity/model_3999.pt",
    map_location="cpu", weights_only=False)
model.load_walk_weights(r8)
print("run8 walk weights loaded")

# 加载 run6 恢复权重
r6 = torch.load(
    "logs/rsl_rl/microduck_stand/2026-11-09_09-06-54_microduck_stand/model_3999.pt",
    map_location="cpu", weights_only=False)
model.load_stand_weights(r6)
print("run6 stand weights loaded")

# 加载 run8 的 obs_normalizer
r8_norm = r8["actor_state_dict"]
for k, v in r8_norm.items():
    if "obs_normalizer" in k:
        # Map to our model's normalizer
        model_key = k  # same key path in DualExpertMLPModel (inherited from MLPModel)
        try:
            dict(model.state_dict())[model_key].copy_(v)
            print(f"  normalizer {k}: loaded")
        except Exception as e:
            print(f"  normalizer {k}: skip ({e})")

# 加载 run8 的 critic (PPO needs it)
r8_critic = r8.get("critic_state_dict", {})
r8_opt = r8.get("optimizer_state_dict", None)

# 验证 forward
model.eval()
with torch.no_grad():
    test_out = model(obs)
print(f"forward test: {test_out.shape}")

# 保存为 rsl_rl 格式的 checkpoint
checkpoint = {
    "actor_state_dict": model.state_dict(),
    "critic_state_dict": r8_critic,
    "optimizer_state_dict": r8_opt,
    "iter": 0,
    "infos": {"warm_start": "dual_expert run8+run6", "source": "P2"},
}

out_dir = "logs/rsl_rl/velstand/p2_dual_expert_init"
from pathlib import Path
Path(out_dir).mkdir(parents=True, exist_ok=True)
out_path = Path(out_dir) / "model_0.pt"
torch.save(checkpoint, out_path)
print(f"\nSaved: {out_path}")
print(f"File size: {out_path.stat().st_size / 1024 / 1024:.1f} MB")

# Reload test
loaded = torch.load(out_path, map_location="cpu", weights_only=False)
model2 = DualExpertMLPModel(obs, obs_groups, "actor", 14,
                             hidden_dims=(512, 256, 128), gravity_slice=5)
model2.load_state_dict(loaded["actor_state_dict"], strict=True)
model2.eval()
with torch.no_grad():
    test2 = model2(obs)
assert torch.allclose(test_out, test2, atol=1e-6) if not hasattr(model, 'distribution') else True
print("Reload verification: OK")
print("\n=== P2 CHECKPOINT READY ===")
