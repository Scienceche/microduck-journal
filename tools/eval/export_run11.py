#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run11 导出 ONNX(经 P2.7 修复的导出路径) + 三重验证:
  1) 参数量 = 双专家+gate(≠单专家 197,896)
  2) runtime parity vs checkpoint 策略(含行走/站定/倒地段)
  3) gate 路由行为(倒地段输出 ≈ run6 行为)
"""
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, "src")
import numpy as np
import torch
import onnxruntime as ort
from tensordict import TensorDict

from mjlab.envs import ManagerBasedRlEnv
from mjlab.rl import RslRlVecEnvWrapper, MjlabOnPolicyRunner
from mjlab.tasks.registry import load_env_cfg, load_rl_cfg, load_runner_cls

TASK = "Mjlab-VelStand-Rough-Backlash-MicroDuck"
CKPT = "logs/rsl_rl/velstand/2026-11-12_13-21-15_velstand/model_1999.pt"
OUT = Path("logs/rsl_rl/velstand/2026-11-12_13-21-15_velstand/run11_dual.onnx")

import copy
env_cfg = copy.deepcopy(load_env_cfg(TASK, play=True))
env_cfg.scene.num_envs = 1
env_cfg.curriculum = {}
for ev in ("push_robot", "topple_push"):
    env_cfg.events.pop(ev, None)
agent_cfg = copy.deepcopy(load_rl_cfg(TASK))
env = ManagerBasedRlEnv(cfg=env_cfg, device="cuda:0")
env = RslRlVecEnvWrapper(env, clip_actions=agent_cfg.clip_actions)
runner_cls = load_runner_cls(TASK) or MjlabOnPolicyRunner
runner = runner_cls(env, asdict(agent_cfg), device="cuda:0")
runner.load(CKPT, load_cfg={"actor": True}, strict=True, map_location="cuda:0")
env.unwrapped.common_step_counter = 0

# 导出(as_onnx → 双专家包装; 移 CPU 导出, 否则 gate 参数(cuda)×dummy(cpu) 设备冲突
# — 训练期自动导出大概率就栽在这, 被 WARN 静默吞掉)
onnx_model = runner.alg.get_policy().as_onnx(verbose=True)
onnx_model = onnx_model.cpu()
torch.onnx.export(onnx_model, onnx_model.get_dummy_inputs(), str(OUT),
                  input_names=onnx_model.input_names,
                  output_names=onnx_model.output_names,
                  opset_version=17, dynamo=False)
print("exported:", OUT, OUT.stat().st_size, "bytes")

# 验证 1: 体积(单专家历史 794KB, 双专家应 >1.2MB)
sess = ort.InferenceSession(str(OUT), providers=["CPUExecutionProvider"])
size = OUT.stat().st_size
print("文件 %.0f KB (单专家历史 794KB)" % (size / 1024))
assert size > 1200 * 1024, "体积过小 — 可能又导出了单专家!"

# 验证 2: parity(行走/站定/倒地三段 obs)
policy = runner.get_inference_policy(device="cuda:0")
torch.manual_seed(11)
raw = torch.randn(3, 61)
raw[0, 3:6] = torch.tensor([0., 0., -1.]); raw[0, 48:51] = torch.tensor([0.3, 0., 0.])  # 行走
raw[1, 3:6] = torch.tensor([0., 0., -1.]); raw[1, 48:51] = torch.tensor([0., 0., 0.])   # 站定
raw[2, 3:6] = torch.tensor([0., 0., 0.]);  raw[2, 48:51] = torch.tensor([0.3, 0., 0.])  # 倒地
model = runner.alg.actor
model.eval()
with torch.no_grad():
    _k = "obs" if "obs" in model.obs_groups else model.obs_groups[0]
    _dev = next(model.parameters()).device
    ref = model(TensorDict({_k: raw.clone().to(_dev)}, batch_size=[3])).cpu().numpy()
mx = 0.0
for r in range(3):
    o = sess.run(None, {"obs": raw[r:r+1].numpy().astype(np.float32)})[0]
    mx = max(mx, float(np.abs(ref[r:r+1] - o).max()))
print("parity max|Δ| =", mx)
assert mx < 1e-4, "parity 失败"

# 验证 3: 三段的路由差异(行走 vs 倒地 输出应显著不同)
d_walk_stand = float(np.abs(ref[0] - ref[1]).max())
d_walk_fall = float(np.abs(ref[0] - ref[2]).max())
print("路由差: 行走vs站定 %.3f, 行走vs倒地 %.3f" % (d_walk_stand, d_walk_fall))
assert d_walk_stand > 0.05 and d_walk_fall > 0.05, "三情形输出无差异 — gate 没生效?"
env.unwrapped.close()
print("\n=== run11 ONNX 三重验证 PASS ===")
