#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""起身评测 battery v2 (run12) — 基于 StandUp 任务的 set_ground_state 桶。

批次: 俯卧/仰面/桥式(run12 新增, 黑匣子校准)/后仰接手(站立+后向初速)/坐姿/站立对照。
成功 = 达到站立(高>0.105m 且 倾角<30°)并保持 ≥2s。
净化(2026-11-12 对抗审查): deepcopy cfg / 课程全清 / 摘双推力 / 指令全零 /
counter 归零。
双专家 checkpoint: EVAL_DUAL=1 时 actor 类指到 DualExpertMLPModel。
"""
import copy
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, "src")
import torch

CKPT = sys.argv[1] if len(sys.argv) > 1 else None
if not CKPT:
    print("用法: [EVAL_DUAL=1] python eval_getup.py <ckpt>"); sys.exit(2)
N_ENVS = 64
STEPS = 1000          # 20s @50Hz
HOLD = 100            # 2s 保持
DEVICE = "cuda:0"
# 2026-11-24: 加随机种子。原先不设种子, "同一评测跑两遍看方差"无法复现。
# EVAL_SEED 未设时保持旧行为(随机); 设了则 torch/随机/环境同种子。
SEED = os.environ.get("EVAL_SEED")
SEED = int(SEED) if SEED not in (None, "") else None

from mjlab.envs import ManagerBasedRlEnv
from mjlab.rl import RslRlVecEnvWrapper, MjlabOnPolicyRunner
from mjlab.tasks.registry import load_env_cfg, load_rl_cfg, load_runner_cls

TASK = "Mjlab-StandUp-Rough-Backlash-MicroDuck"

BATCHES = [
    # (名, set_ground_state 概率覆盖, standing_back_vel_prob)
    ("俯卧 fd",     dict(face_down_prob=1.0), 0.0),
    ("仰面 fu",     dict(face_up_prob=1.0), 0.0),
    ("桥式 bridge", dict(bridge_prob=1.0), 0.0),
    ("后仰接手",     dict(standing_prob=1.0), 1.0),
    ("坐姿 sit",    dict(sitting_prob=1.0), 0.0),
    ("站立对照",     dict(standing_prob=1.0), 0.0),
]


def run_batch(name, probs, back_vel):
    env_cfg = copy.deepcopy(load_env_cfg(TASK, play=True))
    env_cfg.scene.num_envs = N_ENVS
    env_cfg.curriculum = {}
    for ev in ("push_robot", "topple_push"):
        env_cfg.events.pop(ev, None)
    tw = env_cfg.commands["twist"]
    tw.ranges.lin_vel_x = (0.0, 0.0)
    tw.ranges.lin_vel_y = (0.0, 0.0)
    tw.ranges.ang_vel_z = (0.0, 0.0)
    tw.rel_standing_envs = 1.0
    # 覆盖初始状态桶(全零再叠批次值)
    gs = env_cfg.events["set_ground_state"]
    for k in ("face_down_prob", "face_up_prob", "sitting_prob",
              "standing_prob", "bridge_prob"):
        gs.params[k] = 0.0
    gs.params.update(probs)
    gs.params["standing_back_vel_prob"] = back_vel

    agent_cfg = copy.deepcopy(load_rl_cfg(TASK))
    if SEED is not None:
        import random
        import numpy as np
        random.seed(SEED)
        np.random.seed(SEED)
        torch.manual_seed(SEED)
        env_cfg.seed = SEED
    if os.environ.get("EVAL_DUAL"):
        agent_cfg.actor.class_name = (
            "mjlab_microduck.tasks.dual_expert_mlp.DualExpertMLPModel")
    env = ManagerBasedRlEnv(cfg=env_cfg, device=DEVICE)
    env = RslRlVecEnvWrapper(env, clip_actions=agent_cfg.clip_actions)
    runner_cls = load_runner_cls(TASK) or MjlabOnPolicyRunner
    runner = runner_cls(env, asdict(agent_cfg), device=DEVICE)
    runner.load(CKPT, load_cfg={"actor": True}, strict=True, map_location=DEVICE)
    env.unwrapped.common_step_counter = 0
    policy = runner.get_inference_policy(device=DEVICE)
    rdata = env.unwrapped.scene.entities["robot"].data

    obs, _ = env.reset()
    hold = torch.zeros(N_ENVS, device=DEVICE)
    first_stand = torch.full((N_ENVS,), float("inf"), device=DEVICE)
    success = torch.zeros(N_ENVS, dtype=torch.bool, device=DEVICE)
    end_standing = torch.zeros(N_ENVS, dtype=torch.bool, device=DEVICE)
    for step in range(STEPS):
        o = obs["actor"] if isinstance(obs, dict) else obs
        with torch.no_grad():
            act = policy(o)
        obs, _, dones, _ = env.step(act)
        with torch.no_grad():
            h = rdata.root_com_pos_w[:, 2]
            q = rdata.root_link_quat_w
            gz = -(1 - 2 * (q[:, 1] ** 2 + q[:, 2] ** 2))
            tilt = torch.rad2deg(torch.acos(torch.clamp(-gz, -1.0, 1.0)))
            standing = (h > 0.105) & (tilt < 30.0)
            hold = torch.where(standing, hold + 1, torch.zeros_like(hold))
            newly = (hold >= HOLD) & ~success
            first_stand[newly] = step
            success |= newly
            if step == STEPS - 1:
                end_standing = standing.clone()
    env.unwrapped.close()
    s = success.float().cpu().tolist()
    fs = sorted(t / 50.0 for t, ok in zip(first_stand.cpu().tolist(), s) if ok)
    med = fs[len(fs) // 2] if fs else None
    return {"batch": name, "success_pct": sum(s) / len(s) * 100,
            "end_standing_pct": end_standing.float().mean().item() * 100,
            "median_rise_s": med, "n": len(s)}


if __name__ == "__main__":
    print("[eval] ckpt: %s | dual=%s" % (CKPT, bool(os.environ.get("EVAL_DUAL"))), flush=True)
    results = []
    for name, probs, bv in BATCHES:
        t0 = time.time()
        r = run_batch(name, probs, bv)
        r["secs"] = round(time.time() - t0, 1)
        results.append(r)
        print("[result] %-12s 成功 %.0f%% · 终局站立 %.0f%% · 中位 %ss (%.0fs)" % (
            r["batch"], r["success_pct"], r["end_standing_pct"],
            ("%.1f" % r["median_rise_s"]) if r["median_rise_s"] else "—", r["secs"]), flush=True)
    # 2026-11-24: 文件名带检查点名+种子, 不再固定覆盖上一次
    stem = Path(CKPT).parent.name.split("_")[0] + "_" + Path(CKPT).stem
    out = Path("eval_getup_%s_seed%s.json" % (stem, SEED if SEED is not None else "rand"))
    out.write_text(json.dumps({
        "ckpt": CKPT,
        "ckpt_sha256": __import__("hashlib").sha256(Path(CKPT).read_bytes()).hexdigest(),
        "task": TASK, "seed": SEED, "n_envs": N_ENVS,
        "criteria": {"height_m": 0.105, "tilt_deg": 30.0, "hold_s": 2.0},
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "results": results,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved:", out, flush=True)
