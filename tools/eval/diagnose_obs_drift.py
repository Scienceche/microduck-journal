#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐通道 z-score: 今日 obs 分布 vs run8 训练归一化统计。"""
import sys

sys.path.insert(0, r"C:\duck_work\microduck_rl\src")
import copy

import numpy as np
import torch

from mjlab.envs import ManagerBasedRlEnv
from mjlab.tasks.registry import load_env_cfg

TASK = "Mjlab-Velocity-Flat-MicroDuck"
cfg = copy.deepcopy(load_env_cfg(TASK, play=True))
cfg.scene.num_envs = 1
cfg.curriculum = {}
for ev in ("push_robot", "topple_push"):
    cfg.events.pop(ev, None)
tw = cfg.commands["twist"]
tw.ranges.lin_vel_x = (0.3, 0.3)
tw.ranges.lin_vel_y = (0.0, 0.0)
tw.ranges.ang_vel_z = (0.0, 0.0)
tw.rel_standing_envs = 0.0

env = ManagerBasedRlEnv(cfg=cfg, device="cuda:0")
obs, _ = env.reset()

r8 = torch.load(
    r"C:\duck_work\microduck_rl\logs\rsl_rl\velocity\2026-11-10_13-20-40_velocity\model_3999.pt",
    map_location="cpu", weights_only=False)["actor_state_dict"]
mean = r8["obs_normalizer._mean"].flatten().numpy()
std = r8["obs_normalizer._std"].flatten().numpy()

frames = []
for s in range(200):
    o = obs["actor"] if not torch.is_tensor(obs) else obs
    o = o["actor"] if hasattr(o, "keys") else o
    a = torch.zeros(1, 14, device=o.device)
    obs, _, _, _, _ = env.step(a)
    o2 = obs["actor"] if not torch.is_tensor(obs) else obs
    o2 = o2["actor"] if hasattr(o2, "keys") else o2
    frames.append(o2.cpu().numpy())
    if s == 0:
        print("obs shape:", tuple(o2.shape), flush=True)

cur = np.concatenate(frames, 0)
z = np.abs((cur - mean) / np.maximum(std, 1e-6))
names = ["ang_vel3", "grav3", "jpos14", "jvel14", "act14", "cmd9", "tail4"]
bounds = [0, 3, 6, 20, 34, 48, 57, 61]
print("BLOCK   today_mean  run8_mean  run8_std  z_med  z_p95", flush=True)
for i, nm in enumerate(names):
    seg = slice(bounds[i], bounds[i + 1])
    print("%-7s %9.3f %10.3f %9.4f %6.1f %7.1f" % (
        nm, cur[:, seg].mean(), mean[seg].mean(), std[seg].mean(),
        float(np.median(z[:, seg])), float(np.percentile(z[:, seg], 95))), flush=True)
env.unwrapped.close()
