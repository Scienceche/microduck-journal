#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run12 初始态测试: 桥式桶真实生成 + 物理沉降稳定 + 站立后向初速注入。"""
import copy
import sys

sys.path.insert(0, r"C:\duck_work\microduck_rl\src")
import torch

from mjlab.envs import ManagerBasedRlEnv
from mjlab.tasks.registry import load_env_cfg

TASK = "Mjlab-StandUp-Rough-Backlash-MicroDuck"

cfg = copy.deepcopy(load_env_cfg(TASK, play=True))
cfg.scene.num_envs = 64
cfg.curriculum = {}
for ev in ("push_robot", "topple_push"):
    cfg.events.pop(ev, None)
env = ManagerBasedRlEnv(cfg=cfg, device="cuda:0")
robot = env.scene.entities["robot"]
rdata = robot.data

# 手动把事件概率设成纯桥式/纯站立+后速, 分别验证
gs = env.event_manager.get_term_cfg("set_ground_state") if hasattr(env, "event_manager") else None

obs, _ = env.reset()
# 纯桥式一轮
env.unwrapped.event_manager.get_term_cfg("set_ground_state").params.update({
    "face_down_prob": 0, "face_up_prob": 0, "sitting_prob": 0, "standing_prob": 0,
    "bridge_prob": 1.0})
obs, _ = env.reset()

import math
q = rdata.root_link_quat_w  # wxyz
gz = -(1 - 2 * (q[:, 1] ** 2 + q[:, 2] ** 2))
tilt = torch.rad2deg(torch.acos(torch.clamp(-gz, -1.0, 1.0)))
z = rdata.root_com_pos_w[:, 2]
jp = rdata.joint_pos  # [N, J] (含 passive?)
print("桥式桶 spawn: tilt med=%.0f° [p10 %.0f, p90 %.0f] z med=%.3f" % (
    tilt.median(), tilt.quantile(0.1), tilt.quantile(0.9), z.median()))
assert 40 < tilt.median() < 110, tilt.median()
assert 0.06 < z.median() < 0.16, z.median()  # 新出生带 0.08-0.13

# 关节折叠核验(servo 关节: 用 joint_names 找 left_hip_pitch 等)
names = list(robot.joint_names) if hasattr(robot, "joint_names") else []
def jidx(nm):
    return names.index(nm) if nm in names else None
checks = [("left_hip_pitch", -2.9, -1.5), ("left_knee", 1.5, 3.0)] if jidx("left_knee") else []
# 折叠腿左右各半, 检查每条腿的深折比例
lh = jidx("left_hip_pitch"); lk = jidx("left_knee"); rh = jidx("right_hip_pitch"); rk = jidx("right_knee")
lp_fold = ((jp[:, lh] < -1.5) & (jp[:, lk] > 1.5)).float().mean()
rp_fold = ((jp[:, rh] > 1.5) & (jp[:, rk] < -1.5)).float().mean()
print("左腿深折比例 %.0f%% 右腿 %.0f%%" % (lp_fold * 100, rp_fold * 100))
assert lp_fold + rp_fold > 0.9, "至少一条腿深折"
hp = jidx("head_pitch")
print("head_pitch med=%.2f rad (期望 0.5-1.2)" % jp[:, hp].median())
assert 0.3 < jp[:, hp].median() < 1.4

# 沉降 100 步(零动作) — 无 NaN/无爆飞
for s in range(100):
    obs, _, _, _, _ = env.step(torch.zeros(64, 14, device="cuda:0"))
    if s == 99:
        zz = rdata.root_com_pos_w[:, 2]
        vv = (rdata.root_com_lin_vel_w[:, :2] ** 2).sum(-1).sqrt()
        assert torch.isfinite(zz).all() and torch.isfinite(vv).all(), "NaN!"
        # 注: 零动作=目标HOME=站立 → 桥式腿伸展会把躯干顶起(z 可到 0.2+,
        # 属"顶起站立"而非弹飞); 健康判据 = 有限 + 高度/速度不离谱
        print("沉降后: z med=%.3f max=%.3f | 水平速度 med=%.2f max=%.2f" % (
            zz.median(), zz.max(), vv.median(), vv.max()))
        assert zz.max() < 0.3 and vv.max() < 2.5

# 站立+后向初速
env.unwrapped.event_manager.get_term_cfg("set_ground_state").params.update({
    "face_down_prob": 0, "face_up_prob": 0, "sitting_prob": 0, "standing_prob": 1.0,
    "bridge_prob": 0.0, "standing_back_vel_prob": 1.0})
obs, _ = env.reset()
vx = rdata.root_com_lin_vel_w[:, 0]
vy = rdata.root_com_lin_vel_w[:, 1]
vmag = (vx ** 2 + vy ** 2).sqrt()
print("站立桶后向初速: |v| med=%.2f m/s (期望 0.15-0.35)" % vmag.median())
assert 0.1 < vmag.median() < 0.4
env.unwrapped.close()
print("\n==== run12 初始态测试 PASS ====")
