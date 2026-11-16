#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run12 补丁 v2: 精确锚点插入桥式桶 + 站立后向速度。"""
from pathlib import Path

import ast

P = Path(r"C:\duck_work\microduck_rl\src\mjlab_microduck\tasks\mdp.py")
s = P.read_text(encoding="utf-8")

# 1) 签名
old = """def set_random_ground_state(
    env: ManagerBasedRlEnv,
    env_ids: torch.Tensor,
    asset_cfg: SceneEntityCfg = _DEFAULT_ASSET_CFG,
    face_down_prob: float = 0.4,
    face_up_prob: float = 0.4,
    sitting_prob: float = 0.2,
    standing_prob: float = 0.0,"""
new = """def set_random_ground_state(
    env: ManagerBasedRlEnv,
    env_ids: torch.Tensor,
    asset_cfg: SceneEntityCfg = _DEFAULT_ASSET_CFG,
    face_down_prob: float = 0.4,
    face_up_prob: float = 0.4,
    sitting_prob: float = 0.2,
    standing_prob: float = 0.0,
    bridge_prob: float = 0.0,
    standing_back_vel_prob: float = 0.0,"""
if "bridge_prob: float = 0.0" not in s:
    assert old in s, "signature"
    s = s.replace(old, new, 1)

# 2) 归一化 + 掩码
old = """    total = face_down_prob + face_up_prob + sitting_prob + standing_prob
    p_fd  = face_down_prob / total
    p_fu  = (face_down_prob + face_up_prob) / total
    p_sit = (face_down_prob + face_up_prob + sitting_prob) / total"""
new = """    total = (face_down_prob + face_up_prob + sitting_prob
             + standing_prob + bridge_prob)
    p_fd  = face_down_prob / total
    p_fu  = (face_down_prob + face_up_prob) / total
    p_sit = (face_down_prob + face_up_prob + sitting_prob) / total
    p_stand = (face_down_prob + face_up_prob + sitting_prob + standing_prob) / total"""
if "p_stand = (face_down_prob" not in s:
    assert old in s, "normalize"
    s = s.replace(old, new, 1)

old = """    u = torch.rand(num, device=env.device)
    is_fd    = u < p_fd
    is_fu    = (u >= p_fd) & (u < p_fu)
    is_sit   = (u >= p_fu) & (u < p_sit)
    is_stand = u >= p_sit"""
new = """    u = torch.rand(num, device=env.device)
    is_fd     = u < p_fd
    is_fu     = (u >= p_fd) & (u < p_fu)
    is_sit    = (u >= p_fu) & (u < p_sit)
    is_stand  = (u >= p_sit) & (u < p_stand)
    is_bridge = u >= p_stand  # run12: 桥式/头撑(真机 OOD, 黑匣子 2026-11-13 校准)"""
if "is_bridge = u >= p_stand" not in s:
    assert old in s, "masks"
    s = s.replace(old, new, 1)

# 3) 桥式四元数(接在站立 quat 赋值后)
old = """    new_quat = face_down.clone()
    new_quat[is_fu]    = face_up[is_fu]
    new_quat[is_sit]   = sitting[is_sit]
    new_quat[is_stand] = sitting[is_stand]"""
new = """    new_quat = face_down.clone()
    new_quat[is_fu]    = face_up[is_fu]
    new_quat[is_sit]   = sitting[is_sit]
    new_quat[is_stand] = sitting[is_stand]
    # 桥式躯干: 80% 头朝下斜撑 55-95° / 20% 背撑 55-85°, ±25° 侧倾, 随机 yaw
    if bridge_prob > 0.0 and is_bridge.any():
        sign = torch.where(torch.rand(num, device=env.device) < 0.8, 1.0, -1.0)
        pitch_b = sign * (torch.rand(num, device=env.device)
                          * (math.radians(95) - math.radians(55)) + math.radians(55))
        roll_b = (torch.rand(num, device=env.device) * 2 - 1) * math.radians(25)
        cp = torch.cos(pitch_b * 0.5); sp = torch.sin(pitch_b * 0.5)
        cr = torch.cos(roll_b * 0.5);  sr = torch.sin(roll_b * 0.5)
        bridge_q = torch.stack([
            cr * cp * cy + sr * sp * sy,
            sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy,
            cr * cp * sy - cr * sp * cy,
        ], dim=1)
        new_quat[is_bridge] = bridge_q[is_bridge]"""
if "bridge_q = torch.stack" not in s:
    assert old in s, "quat"
    s = s.replace(old, new, 1)

# 4) 尾部插入: 关节姿态 + 后向速度(精确锚点 = noise 行 + 顶层注释之间)
tail_old = """            env.sim.data.qpos[sit_env_ids.unsqueeze(1).long(), cols.unsqueeze(0)] += noise


# Deep-crouch anchor pose"""
tail_new = """            env.sim.data.qpos[sit_env_ids.unsqueeze(1).long(), cols.unsqueeze(0)] += noise

    # ---- run12: 桥式桶关节姿态(黑匣子 13:21 stuck 实测: 一腿深折 hip/knee≈2.7rad
    # ankle≈1.8rad, 头下压 ~1.1rad, 躯干斜撑; 仿真符号按 SIT 折叠方向) ----
    if bridge_prob > 0.0 and is_bridge.any():
        br_env_ids = env_ids[is_bridge]
        n_br = len(br_env_ids)
        dev = env.device
        fold_left = torch.rand(n_br, device=dev) < 0.5
        hip = torch.rand(n_br, device=dev) * 1.1 + 1.8        # 1.8-2.9
        knee = torch.rand(n_br, device=dev) * 1.1 + 1.8       # 1.8-2.9
        ankle = torch.rand(n_br, device=dev) * 1.2 + 0.8      # 0.8-2.0
        ankle_sign = torch.where(torch.rand(n_br, device=dev) < 0.5, 1.0, -1.0)
        hip2 = torch.rand(n_br, device=dev) * 1.0 + 0.2       # 另一腿半折
        knee2 = torch.rand(n_br, device=dev) * 1.3 + 0.2
        head_p = torch.rand(n_br, device=dev) * 0.7 + 0.5     # 头下压 0.5-1.2
        neck_p = (torch.rand(n_br, device=dev) * 2 - 1) * 0.3
        head_y = (torch.rand(n_br, device=dev) * 2 - 1) * 0.4

        def _br_set(eids, j, vals):
            env.sim.data.qpos[eids, 7 + servo_ids[j]] = vals

        lf = br_env_ids[fold_left]
        rf = br_env_ids[~fold_left]
        if len(lf) > 0:
            k = torch.arange(len(lf), device=dev)
            _br_set(lf, 2, -hip[k]); _br_set(lf, 3, knee[k])
            _br_set(lf, 4, ankle_sign[k] * ankle[k])
            _br_set(lf, 11, hip2[k]); _br_set(lf, 12, -knee2[k]); _br_set(lf, 13, 0.0)
        if len(rf) > 0:
            k = torch.arange(len(rf), device=dev)
            _br_set(rf, 11, hip[k]); _br_set(rf, 12, -knee[k])
            _br_set(rf, 13, -ankle_sign[k] * ankle[k])
            _br_set(rf, 2, -hip2[k]); _br_set(rf, 3, knee2[k]); _br_set(rf, 4, 0.0)
        _br_set(br_env_ids, 5, neck_p)
        _br_set(br_env_ids, 6, head_p)
        _br_set(br_env_ids, 7, head_y)

    # ---- run12: 站立桶后向初速(减速接手态, 治"停住后仰") ----
    if standing_back_vel_prob > 0.0 and is_stand.any():
        st_env_ids = env_ids[is_stand]
        m = torch.rand(len(st_env_ids), device=env.device) < standing_back_vel_prob
        back_ids = st_env_ids[m]
        if len(back_ids) > 0:
            yaw_st = yaw[is_stand][m]
            v = torch.rand(len(back_ids), device=env.device) * 0.20 + 0.15  # 0.15-0.35
            env.sim.data.qvel[back_ids, 0] = -v * torch.cos(yaw_st)
            env.sim.data.qvel[back_ids, 1] = -v * torch.sin(yaw_st)


# Deep-crouch anchor pose"""
if "桥式桶关节姿态" not in s:
    assert tail_old in s, "tail anchor"
    s = s.replace(tail_old, tail_new, 1)

P.write_text(s, encoding="utf-8")

# 落盘复核 + 语法
c = P.read_text(encoding="utf-8")
for key in ("bridge_prob: float = 0.0", "is_bridge = u >= p_stand",
            "bridge_q = torch.stack", "桥式桶关节姿态", "站立桶后向初速",
            "standing_back_vel_prob: float = 0.0"):
    assert key in c, key
ast.parse(c)
assert "import math" in c.split("def set_random_ground_state")[0] or "import math" in c[:2000]
print("mdp.py 补丁落盘 ✓ 语法 ✓ (math 已导入)")
