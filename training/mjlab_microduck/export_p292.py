#!/usr/bin/env python3
"""导出 P2.9.2 (stand=run14 crouch head) ONNX。"""
import copy
import sys
from pathlib import Path
from dataclasses import asdict
import torch

sys.path.insert(0, "src")
from mjlab.envs import ManagerBasedRlEnv
from mjlab.rl import RslRlVecEnvWrapper, MjlabOnPolicyRunner
from mjlab.tasks.registry import load_env_cfg, load_rl_cfg, load_runner_cls

TASK = "Mjlab-VelStand-Rough-Backlash-MicroDuck"
CKPT = "logs/rsl_rl/velstand/p292_run14_crouch_head/model_0.pt"
OUT = Path("logs/rsl_rl/velstand/p292_run14_crouch_head/p292.onnx")

cfg = copy.deepcopy(load_env_cfg(TASK, play=True))
cfg.scene.num_envs = 1
cfg.curriculum = {}
for ev in ("push_robot", "topple_push"):
    cfg.events.pop(ev, None)
ag = copy.deepcopy(load_rl_cfg(TASK))
env = ManagerBasedRlEnv(cfg=cfg, device="cuda:0")
env = RslRlVecEnvWrapper(env, clip_actions=ag.clip_actions)
rc = (load_runner_cls(TASK) or MjlabOnPolicyRunner)(env, asdict(ag), device="cuda:0")
rc.load(CKPT, load_cfg={"actor": True}, strict=True, map_location="cuda:0")
m = rc.alg.get_policy().as_onnx(verbose=False).cpu()
torch.onnx.export(m, m.get_dummy_inputs(), str(OUT),
                  input_names=m.input_names, output_names=m.output_names,
                  opset_version=17, dynamo=False)
print("exported:", OUT, OUT.stat().st_size, "bytes")
env.unwrapped.close()
