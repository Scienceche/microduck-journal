#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""甄别 P2 合成起点行走失效根因:
  默认: model_0 原 gate 评测 + gate 实时值/gravity 切片打印
  --force-walk: gate_offset=100 强制 gate≈0 → 纯 walk 专家(同 obs 管线)
"""
import sys
from dataclasses import asdict

sys.path.insert(0, "src")
import numpy as np
import torch

from mjlab.envs import ManagerBasedRlEnv
from mjlab.rl import RslRlVecEnvWrapper, MjlabOnPolicyRunner
from mjlab.tasks.registry import load_env_cfg, load_rl_cfg, load_runner_cls

import os
TASK = os.environ.get("EVAL_TASK", "Mjlab-VelStand-Rough-Backlash-MicroDuck")
if os.environ.get("EVAL_DUAL"):
    pass
DEVICE = "cuda:0"
FORCE_GATE_ZERO = "--force-walk" in sys.argv
CKPT = "logs/rsl_rl/velstand/p2_dual_expert_init/model_0.pt"

env_cfg = load_env_cfg(TASK, play=True)
env_cfg.scene.num_envs = 1
tw = env_cfg.commands["twist"]
tw.ranges.lin_vel_x = (0.3, 0.3)
tw.ranges.lin_vel_y = (0.0, 0.0)
tw.ranges.ang_vel_z = (0.0, 0.0)
tw.rel_standing_envs = 0.0
if hasattr(tw, "rel_turn_in_place_envs"):
    tw.rel_turn_in_place_envs = 0.0
agent_cfg = load_rl_cfg(TASK)
if TASK.startswith("Mjlab-Velocity"):
    agent_cfg.actor.class_name = "mjlab_microduck.tasks.dual_expert_mlp.DualExpertMLPModel"
env = ManagerBasedRlEnv(cfg=env_cfg, device=DEVICE)
env = RslRlVecEnvWrapper(env, clip_actions=agent_cfg.clip_actions)
runner_cls = load_runner_cls(TASK) or MjlabOnPolicyRunner
runner = runner_cls(env, asdict(agent_cfg), device=DEVICE)
runner.load(CKPT, load_cfg={"actor": True}, strict=True, map_location=DEVICE)
policy = runner.get_inference_policy(device=DEVICE)
model = runner.alg.actor

if FORCE_GATE_ZERO:
    with torch.no_grad():
        model.gate_offset.fill_(100.0)
    print("[hook] gate_offset=100 → gate≈0 (纯 walk 专家输出)", flush=True)

rdata = env.unwrapped.scene.entities["robot"].data
obs, _ = env.reset()
vxs, terms, gates = [], 0, []
sl = getattr(model, "gravity_slice", 5)
print("gravity_slice =", sl, flush=True)
for s in range(500):
    o = obs["actor"] if isinstance(obs, dict) else obs
    with torch.no_grad():
        act = policy(o)
    obs, _, dones, _ = env.step(act)
    vxs.append(float(rdata.root_com_lin_vel_w[0, 0]))
    if bool(dones.any()):
        terms += 1
    if s % 100 == 0:
        with torch.no_grad():
            # P2.5: gate 吃【原始】obs(归一化前) — 与新 forward 一致
            raw = o["actor"] if hasattr(o, "keys") else o
            g = model._compute_gate(raw)
            seg = raw[0, sl:sl + 3].tolist()
            gates.append(float(g.flatten()[0]))
            print("  s=%3d 原始obs[%d:%d+3]=%s gate=%.4f" % (
                s, sl, sl, ["%+.3f" % x for x in seg], float(g.flatten()[0])), flush=True)
print("[结果 %s] avg_vx=%+.3f terms=%d gate序列=%s" % (
    "纯walk专家" if FORCE_GATE_ZERO else "原gate", np.mean(vxs), terms,
    ["%.3f" % g for g in gates]), flush=True)
env.unwrapped.close()
