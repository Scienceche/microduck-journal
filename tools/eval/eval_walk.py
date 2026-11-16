"""Headless walk eval: ONNX policy + mjlab env, fixed command vx=0.3, 2x20s episodes.

Measures: achieved forward velocity vs command, distance, falls, trunk height.
"""
import sys

import numpy as np
import torch

from mjlab.envs import ManagerBasedRlEnv
from mjlab.rl import RslRlVecEnvWrapper
from mjlab.tasks.registry import load_env_cfg, load_rl_cfg

import onnxruntime as ort

TASK = "Mjlab-Velocity-Flat-MicroDuck"


def find_root(env):
    """Locate the robot root-state accessor defensively (API varies)."""
    scene = env.unwrapped.scene
    robot = scene.entities.get("robot") if hasattr(scene, "entities") else None
    if robot is None and hasattr(scene, "entities"):
        for k in scene.entities:
            print("  entity:", k)
    return robot


def main(onnx_path):
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
    # 确定性起点(模拟真机部署:从固定站姿启动) — 传 --random 恢复训练式随机起点
    if "--random" not in sys.argv:
        try:
            rb = env_cfg.events["reset_base"]
            rb.params["pose_range"] = {
                "x": (0.0, 0.0), "y": (0.0, 0.0),
                "z": (0.12, 0.12), "yaw": (0.0, 0.0),
            }
            rb.params["velocity_range"] = {
                "x": (0.0, 0.0), "y": (0.0, 0.0), "z": (0.0, 0.0),
                "roll": (0.0, 0.0), "pitch": (0.0, 0.0), "yaw": (0.0, 0.0),
            }
            rj = env_cfg.events["reset_robot_joints"]
            rj.params["position_range"] = (0.0, 0.0)
            rj.params["velocity_range"] = (0.0, 0.0)
            print("[eval] deterministic start (deployment-like)")
        except Exception as e:
            print("[eval] reset pin skipped:", e)
    env = ManagerBasedRlEnv(cfg=env_cfg, device="cuda:0")
    env = RslRlVecEnvWrapper(env, clip_actions=agent_cfg.clip_actions)

    robot = find_root(env)
    data = robot.data

    sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    iname = sess.get_inputs()[0].name

    for ep in range(2):
        obs, _ = env.reset()
        xs, zs, vxs, falls, steps = [], [], [], 0, 0
        for step in range(1000):
            o = obs["actor"] if not torch.is_tensor(obs) else obs
            act = sess.run(None, {iname: o.detach().cpu().numpy().astype(np.float32)})[0]
            obs, rew, dones, extras = env.step(torch.tensor(act, device=env.unwrapped.device))
            steps += 1
            pos = data.root_com_pos_w.cpu().numpy()[0]
            vel = data.root_com_lin_vel_w.cpu().numpy()[0]
            xs.append(pos[0]); zs.append(pos[2]); vxs.append(vel[0])
            if bool(dones.any()):
                falls += 1
        xs, zs, vxs = map(np.array, (xs, zs, vxs))
        print(f"[{onnx_path.split('_')[-1]}] ep{ep}: dist={xs[-1]-xs[0]:+.2f}m "
              f"avg_vx={vxs.mean():+.3f} (cmd 0.3) last5s_vx={vxs[-250:].mean():+.3f} "
              f"height={zs.mean():.3f} term={falls} steps={steps}")
    env.unwrapped.close()


if __name__ == "__main__":
    main(sys.argv[1])
