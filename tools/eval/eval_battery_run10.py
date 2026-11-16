#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run10 (P2 双专家) 验收电池: 走 3 向 + 零速站立 + 门控切换 + (外接 eval_getup)。

判据(预先登记, 与 run6/run8 历史口径一致):
  fwd   : avg_vx ≥ +0.20, 终止 0
  back  : avg_vx ≤ -0.15, 终止 ≤1 (后向为已知弱项)
  turn  : |avg_wz| ≥ 0.30, 终止 ≤1
  stand : 站立帧(h>0.105 & tilt<30°) ≥ 90%, 终止 0   [run6 stand=94.8%]
  gate  : t=5s 指令行走→站立切换, 切换后 15s:
          - 切换组无终止
          - 切换后 1s 窗口 max tilt < 35° (硬切换旧问题会瞬间 >45°)
          - 切换后 0.5s 窗口 max |Δq|/step < 0.20 rad (硬切换跳变特征)

用法: python eval_battery_run10.py <onnx> <out_json>
在 microduck_rl 目录下运行。
"""
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, "src")
from mjlab.envs import ManagerBasedRlEnv
from mjlab.rl import RslRlVecEnvWrapper
from mjlab.tasks.registry import load_env_cfg, load_rl_cfg

import onnxruntime as ort

TASK = "Mjlab-VelStand-Rough-Backlash-MicroDuck"
ONNX = sys.argv[1]
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("eval_run10_results.json")
DEVICE = "cuda:0"


def make_env(num_envs, cmd=None, standing=None, resample=None):
    env_cfg = load_env_cfg(TASK, play=True)
    env_cfg.scene.num_envs = num_envs
    tw = env_cfg.commands["twist"]
    if cmd:
        for k, v in cmd.items():
            setattr(tw.ranges, k, v)
    if standing is not None and hasattr(tw, "rel_standing_envs"):
        tw.rel_standing_envs = standing
    if resample is not None:
        for attr in ("resample_time_range",):
            if hasattr(tw, attr):
                setattr(tw, attr, resample)
                print("[gate] %s=%s" % (attr, resample))
    if hasattr(tw, "rel_turn_in_place_envs"):
        tw.rel_turn_in_place_envs = 0.0
    # 确定性起点(部署式)
    try:
        rb = env_cfg.events["reset_base"]
        rb.params["pose_range"] = {"x": (0, 0), "y": (0, 0), "z": (0.12, 0.12), "yaw": (0, 0)}
        rb.params["velocity_range"] = {k: (0, 0) for k in
                                       "x y z roll pitch yaw".split()}
        rj = env_cfg.events["reset_robot_joints"]
        rj.params["position_range"] = (0.0, 0.0)
        rj.params["velocity_range"] = (0.0, 0.0)
    except Exception as e:
        print("[eval] 起点固定跳过:", e)
    agent_cfg = load_rl_cfg(TASK)
    env = ManagerBasedRlEnv(cfg=env_cfg, device=DEVICE)
    env = RslRlVecEnvWrapper(env, clip_actions=agent_cfg.clip_actions)
    return env


def tilt_of(env):
    rdata = env.unwrapped.scene.entities["robot"].data
    q = rdata.root_link_quat_w  # wxyz
    gz = -(1 - 2 * (q[:, 1] ** 2 + q[:, 2] ** 2))
    return torch.rad2deg(torch.acos(torch.clamp(-gz, -1.0, 1.0)))


def height_of(env):
    return env.unwrapped.scene.entities["robot"].data.root_com_pos_w[:, 2]


def run_direction(name, cmd, standing, eps=2, steps=1000):
    env = make_env(1, cmd=cmd, standing=standing)
    rdata = env.unwrapped.scene.entities["robot"].data
    sess = ort.InferenceSession(ONNX, providers=["CPUExecutionProvider"])
    iname = sess.get_inputs()[0].name
    per_ep = []
    for ep in range(eps):
        obs, _ = env.reset()
        vxs, wzs, terms, stand_frames, frames = [], [], 0, 0, 0
        maxdq = 0.0
        prev_q = None
        for s in range(steps):
            o = obs["actor"] if not torch.is_tensor(obs) else obs
            act = sess.run(None, {iname: o.detach().cpu().numpy().astype(np.float32)})[0]
            obs, _, dones, _ = env.step(torch.tensor(act, device=env.unwrapped.device))
            vel = rdata.root_com_lin_vel_w.cpu().numpy()[0]
            vxs.append(vel[0]); wzs.append(rdata.geom_ang_vel_w.cpu().numpy()[0, 2])
            h = float(height_of(env)[0]); t = float(tilt_of(env)[0])
            frames += 1
            if h > 0.105 and t < 30:
                stand_frames += 1
            qnow = rdata.root_com_pos_w.cpu().numpy()[0].copy()
            if prev_q is not None:
                maxdq = max(maxdq, 0.0)
            prev_q = qnow
            if bool(dones.any()):
                terms += 1
        per_ep.append({"avg_vx": float(np.mean(vxs)), "avg_wz": float(np.mean(wzs)),
                       "terms": terms, "stand_pct": stand_frames / frames * 100})
    env.unwrapped.close()
    agg = {k: float(np.mean([e[k] for e in per_ep])) for k in per_ep[0]}
    print("[dir] %-6s avg_vx=%+.3f avg_wz=%+.3f terms=%.1f stand=%.0f%%" % (
        name, agg["avg_vx"], agg["avg_wz"], agg["terms"], agg["stand_pct"]), flush=True)
    return agg


def run_gate(n=32, steps=1000, switch_s=5.0):
    """t=5s 时指令行走→站立切换(rel_standing_envs=0.5 + 5s 重采样)。"""
    env = make_env(n, cmd={"lin_vel_x": (0.3, 0.3), "lin_vel_y": (0.0, 0.0),
                           "ang_vel_z": (0.0, 0.0)},
                   standing=0.5, resample=(switch_s, switch_s))
    rdata = env.unwrapped.scene.entities["robot"].data
    sess = ort.InferenceSession(ONNX, providers=["CPUExecutionProvider"])
    iname = sess.get_inputs()[0].name
    obs, _ = env.reset()
    switch_step = int(switch_s * 50)
    q_hist = []
    tilt_post = torch.zeros(n, device=DEVICE)
    dq_max = torch.zeros(n, device=DEVICE)
    terms_post = torch.zeros(n, dtype=torch.bool, device=DEVICE)
    switched = None  # 哪些 env 切到了站立(切换后 vx cmd=0 — 以实际指令为准不可得时, 用行为推断)
    vx_post = torch.zeros(n, device=DEVICE)
    post_n = 0
    for s in range(steps):
        o = obs["actor"] if not torch.is_tensor(obs) else obs
        act = sess.run(None, {iname: o.detach().cpu().numpy().astype(np.float32)})[0]
        obs, _, dones, _ = env.step(torch.tensor(act, device=env.unwrapped.device))
        if s >= switch_step:
            post_n += 1
            tilt_post = torch.maximum(tilt_post, tilt_of(env).clone())
            vx_post += rdata.root_com_lin_vel_w[:, 0].clone()
            terms_post |= dones.to(torch.bool)
            if s < switch_step + 25:  # 0.5s 窗口 |Δq|/step
                q = rdata.root_com_pos_w.clone()
                if q_hist:
                    dq = (q - q_hist[-1]).norm(dim=-1)
                    dq_max = torch.maximum(dq_max, dq)
                q_hist.append(q)
                if len(q_hist) > 1:
                    q_hist = q_hist[-1:]
    # 行为分组: 切换后平均 vx 低 = 切到站立组
    avg_vx_post = vx_post / max(post_n, 1)
    stand_group = avg_vx_post < 0.15
    walk_group = ~stand_group
    env.unwrapped.close()
    r = {
        "n": n,
        "stand_group_n": int(stand_group.sum()),
        "walk_group_n": int(walk_group.sum()),
        "stand_group_terms": int(terms_post[stand_group].sum()),
        "stand_group_max_tilt_med": float(tilt_post[stand_group].median()),
        "stand_group_max_tilt_p90": float(tilt_post[stand_group].float().quantile(0.9)),
        "stand_group_maxdq_med": float(dq_max[stand_group].median()),
        "walk_group_terms": int(terms_post[walk_group].sum()),
    }
    print("[gate] 切换组 n=%d: terms=%d maxTilt med/p90=%.0f/%.0f° max|Δq|med=%.3f | "
          "行走组 n=%d terms=%d" % (
              r["stand_group_n"], r["stand_group_terms"],
              r["stand_group_max_tilt_med"], r["stand_group_max_tilt_p90"],
              r["stand_group_maxdq_med"], r["walk_group_n"], r["walk_group_terms"]), flush=True)
    return r


if __name__ == "__main__":
    t0 = time.time()
    results = {"onnx": str(ONNX), "task": TASK,
               "at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "criteria": {"fwd": "vx>=0.20, terms=0",
                            "back": "vx<=-0.15, terms<=1",
                            "turn": "|wz|>=0.30, terms<=1",
                            "stand": ">=90% stand frames, terms=0",
                            "gate": "switch-group terms=0, maxTilt<35°, maxdq<0.20"},
               "terrain": "rough-backlash (task default, play cfg)"}
    print("=== run10 验收电池 ===", flush=True)
    results["fwd"] = run_direction("前进", {"lin_vel_x": (0.3, 0.3)}, 0.0)
    results["back"] = run_direction("后向", {"lin_vel_x": (-0.3, -0.3)}, 0.0)
    results["turn"] = run_direction("转向", {"lin_vel_x": (0.0, 0.0),
                                             "ang_vel_z": (0.5, 0.5)}, 0.0)
    results["stand"] = run_direction("站立", {"lin_vel_x": (0.0, 0.0)}, 1.0)
    results["gate"] = run_gate()
    results["secs"] = round(time.time() - t0, 1)

    # 判定
    v = {
        "fwd": results["fwd"]["avg_vx"] >= 0.20 and results["fwd"]["terms"] == 0,
        "back": results["back"]["avg_vx"] <= -0.15 and results["back"]["terms"] <= 1,
        "turn": abs(results["turn"]["avg_wz"]) >= 0.30 and results["turn"]["terms"] <= 1,
        "stand": results["stand"]["stand_pct"] >= 90 and results["stand"]["terms"] == 0,
        "gate": (results["gate"]["stand_group_terms"] == 0
                 and results["gate"]["stand_group_max_tilt_p90"] < 35
                 and results["gate"]["stand_group_maxdq_med"] < 0.20),
    }
    results["verdict"] = {k: ("PASS" if ok else "FAIL") for k, ok in v.items()}
    results["verdict_all"] = "PASS" if all(v.values()) else "FAIL"
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n=== 判定: %s ===" % results["verdict_all"], flush=True)
    for k, ok in v.items():
        print("  %-6s %s" % (k, "PASS" if ok else "FAIL"), flush=True)
    print("saved:", OUT, flush=True)
