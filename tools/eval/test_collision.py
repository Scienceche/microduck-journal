#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""方案 C 碰撞检查: 把关节推到极限(±180°), 看 mesh 穿模和接触力是否物理合理。"""
import sys, math
sys.path.insert(0, r"C:\duck_work\microduck_rl\src")
import copy
import torch
import mujoco

from mjlab.tasks.registry import load_env_cfg
from mjlab_microduck.robot.microduck_constants import MICRODUCK_ALLCOLLISIONS_XML

# 直接用 MuJoCo 加载模型检查
model = mujoco.MjModel.from_xml_path(str(MICRODUCK_ALLCOLLISIONS_XML))
data = mujoco.MjData(model)

joint_names = [model.joint(i).name for i in range(model.njnt)]
print("关节数:", model.njnt)
print("关节:", joint_names[:20])

# 主动关节的 qpos 地址
actuated = {}
for i in range(model.njnt):
    j = model.joint(i)
    if j.type == mujoco.mjtJoint.mjJNT_HINGE:
        actuated[j.name] = i

# 测试: 逐个把腿关节推到 ±180°, 检查穿透
test_joints = [n for n in actuated if any(k in n for k in ("hip_pitch", "knee", "ankle"))]
print("测试关节:", test_joints)

for jn in test_joints:
    jid = actuated[jn]
    qadr = model.jnt_qposadr[jid]
    rng = model.jnt_range[jid]
    for target in [rng[1], rng[0]]:  # max, min
        mujoco.mj_resetData(model, data)
        # 站立姿态基础
        data.qpos[2] = 0.10
        data.qpos[qadr] = target
        mujoco.mj_forward(model, data)
        # 检查最大穿透
        max_pen = 0
        for c in range(data.ncon):
            contact = data.contact[c]
            pen = -contact.dist
            if pen > max_pen:
                max_pen = pen
                names = (model.geom(contact.geom1).name, model.geom(contact.geom2).name)
        flag = "⚠️" if max_pen > 0.005 else "✓"  # 5mm 穿透阈值
        print(f"  {jn:20s} → {math.degrees(target):+6.0f}°: 穿透 {max_pen*1000:.1f}mm {flag}")
        if max_pen > 0.005:
            print(f"    碰撞对: {names}")

print("\n==== 碰撞检查完成 ====")
