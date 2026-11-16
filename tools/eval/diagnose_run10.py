#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run10 诊断: 对比 model_0(热启动) vs model_2999(终版) 的 gate/权重漂移 + 奖励曲线。"""
import sys
from pathlib import Path

sys.path.insert(0, "src")
import torch

RUN = Path("logs/rsl_rl/velstand/2026-11-11_23-11-30_velstand")
m0 = torch.load("logs/rsl_rl/velstand/p2_dual_expert_init/model_0.pt",
                map_location="cpu", weights_only=False)
m2999 = torch.load(RUN / "model_2999.pt", map_location="cpu", weights_only=False)

a0, a9 = m0["actor_state_dict"], m2999["actor_state_dict"]

print("=== gate 参数漂移 ===")
for k in sorted(a0):
    if "gate" in k:
        v0, v9 = float(a0[k]), float(a9[k])
        print("  %-28s %8.4f → %8.4f" % (k, v0, v9))

print("\n=== 权重漂移(相对 L2 范数) ===")
groups = {}
for k in a0:
    g = ("walk(mlp)" if k.startswith("mlp.") else
         "stand_expert" if k.startswith("stand_expert.") else
         "normalizer" if "normalizer" in k else "other")
    if g == "normalizer":
        g = "normalizer.mean" if "mean" in k else "normalizer.var/std"
    d = (a9[k].float() - a0[k].float()).norm() / (a0[k].float().norm() + 1e-9)
    groups.setdefault(g, []).append((k, float(d)))
for g, items in groups.items():
    mx = max(d for _, d in items)
    print("  %-16s max=%.4f  (%d tensors)" % (g, mx, len(items)))
    for k, d in sorted(items, key=lambda x: -x[1])[:3]:
        print("      %-44s %.4f" % (k, d))

# obs normalizer 的 twist 通道漂移(历史坑: run8 教师归一化器 std=0.005)
print("\n=== normalizer 关键通道 ===")
for k in a0:
    if "normalizer" in k and ("mean" in k or "var" in k or "std" in k):
        v0, v9 = a0[k].float(), a9[k].float()
        if v0.numel() >= 61:
            # 61 维 obs: 打印 twist 段(通常最后几维)统计
            print("  %s: 整体 L2 漂移 %.4f" % (k, float((v9 - v0).norm() / (v0.norm() + 1e-9))))

print("\n=== iter / infos ===")
print("  m0 iter:", m0.get("iter"), " m2999 iter:", m2999.get("iter"))
print("  m2999 keys:", [k for k in m2999.keys() if k != "actor_state_dict" and k != "critic_state_dict"])

# 奖励曲线: 用 tfevents(如果 venv 有 tensorboard)
try:
    from tensorboard.backend.event_processing import event_accumulator
    ea = event_accumulator.EventAccumulator(str(RUN), size_guidance={
        event_accumulator.Scalars: 0})
    ea.Reload()
    tags = [t for t in ea.Tags()["scalars"] if "reward" in t.lower() or "loss" in t.lower()]
    print("\n=== 奖励/损失曲线(采样) ===")
    for t in tags[:6]:
        evs = ea.Scalars(t)
        pts = [(e.step, e.value) for e in evs]
        if not pts:
            continue
        sel = [pts[0], pts[len(pts)//4], pts[len(pts)//2], pts[3*len(pts)//4], pts[-1]]
        print("  %-40s %s" % (t[:40],
              " → ".join("(%d, %.2f)" % p for p in sel)))
except ImportError:
    print("\n(tensorboard 不可用, 跳过曲线)")
