#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行走评测(checkpoint 路径) — 对抗审查后净化版。

净化项(审查 P0-2/P0-4/P0-5/P1-6, 2026-11-12):
  - deepcopy cfg(注册表返回同一可变对象, 进程内 mutation 会累积)
  - curriculum 全清(checkpoint 的 common_step_counter 会让 prone/standing/
    head/terrain 课程按历史步数档位覆盖手工参数)
  - 摘 push_robot(±0.3m/s 每 0.5-1s) 和 topple_push(±1.2m/s 每 1-2s)
    — play 配置默认开着, 任何策略都会被推得走不动
  - 每个方向钉全部三轴指令(此前只钉 lin_vel_x, vy/wz 随机重采样)
  - 摔倒判定 = 倾角>45° 上升沿; done 不再计数(20s 到时必 done,
    此前被当摔倒, 判据数学上不可通过)

用法: EVAL_TASK=<任务> [EVAL_DUAL=1] python eval_walk_ckpt.py <ckpt>
"""
import copy
import os
import sys
from dataclasses import asdict

sys.path.insert(0, "src")
import numpy as np
import torch

from mjlab.envs import ManagerBasedRlEnv
from mjlab.rl import RslRlVecEnvWrapper, MjlabOnPolicyRunner
from mjlab.tasks.registry import load_env_cfg, load_rl_cfg, load_runner_cls

TASK = os.environ.get("EVAL_TASK", "Mjlab-VelStand-Rough-Backlash-MicroDuck")
CKPT = sys.argv[1]
DEVICE = "cuda:0"


def run_dir(name, vx, vy, wz):
    env_cfg = copy.deepcopy(load_env_cfg(TASK, play=True))
    env_cfg.scene.num_envs = 1
    # -- 净化 --
    env_cfg.curriculum = {}
    for ev in ("push_robot", "topple_push"):
        env_cfg.events.pop(ev, None)
    tw = env_cfg.commands["twist"]
    tw.ranges.lin_vel_x = (vx, vx)
    tw.ranges.lin_vel_y = (vy, vy)
    tw.ranges.ang_vel_z = (wz, wz)
    tw.rel_standing_envs = 0.0
    if hasattr(tw, "rel_turn_in_place_envs"):
        tw.rel_turn_in_place_envs = 0.0
    # 确定性起点(部署式)
    try:
        rb = env_cfg.events["reset_base"]
        rb.params["pose_range"] = {"x": (0, 0), "y": (0, 0), "z": (0.12, 0.12), "yaw": (0, 0)}
        rb.params["velocity_range"] = {k: (0, 0) for k in "x y z roll pitch yaw".split()}
        rj = env_cfg.events["reset_robot_joints"]
        rj.params["position_range"] = (0.0, 0.0)
        rj.params["velocity_range"] = (0.0, 0.0)
    except Exception as e:
        print("[eval] 起点固定跳过:", e)

    agent_cfg = copy.deepcopy(load_rl_cfg(TASK))
    if os.environ.get("EVAL_DUAL"):
        agent_cfg.actor.class_name = "mjlab_microduck.tasks.dual_expert_mlp.DualExpertMLPModel"
    env = ManagerBasedRlEnv(cfg=env_cfg, device=DEVICE)
    env = RslRlVecEnvWrapper(env, clip_actions=agent_cfg.clip_actions)
    runner_cls = load_runner_cls(TASK) or MjlabOnPolicyRunner
    runner = runner_cls(env, asdict(agent_cfg), device=DEVICE)
    runner.load(CKPT, load_cfg={"actor": True}, strict=True, map_location=DEVICE)
    env.unwrapped.common_step_counter = 0  # 双保险(课程已清)
    policy = runner.get_inference_policy(device=DEVICE)
    rdata = env.unwrapped.scene.entities["robot"].data

    for ep in range(2):
        obs, _ = env.reset()
        vxs, wzs = [], []
        fall_events, prev_fallen = 0, False
        for s in range(1000):
            o = obs["actor"] if isinstance(obs, dict) else obs
            with torch.no_grad():
                act = policy(o)
            obs, _, dones, extras = env.step(act)
            vxs.append(float(rdata.root_com_lin_vel_w[0, 0]))
            wzs.append(float(rdata.geom_ang_vel_w[0, 2].flatten()[0]))
            # 摔倒 = 倾角>45° 上升沿(不数 time_out)
            q = rdata.root_link_quat_w[0]
            gz = -(1 - 2 * (q[1] ** 2 + q[2] ** 2))
            tilt = torch.rad2deg(torch.acos(torch.clamp(-gz, -1.0, 1.0)))
            fallen_now = bool(tilt > 45)
            if fallen_now and not prev_fallen:
                fall_events += 1
            prev_fallen = fallen_now
        print("[%s ep%d] avg_vx=%+.3f avg_wz=%+.3f 摔倒=%d" % (
            name, ep, np.mean(vxs), np.mean(wzs), fall_events), flush=True)
    env.unwrapped.close()


if __name__ == "__main__":
    print("ckpt:", CKPT, "| task:", TASK, flush=True)
    run_dir("前进", 0.3, 0.0, 0.0)
    run_dir("后向", -0.3, 0.0, 0.0)
    run_dir("转向", 0.0, 0.0, 0.5)
