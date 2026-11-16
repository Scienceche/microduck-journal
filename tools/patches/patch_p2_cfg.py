#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2 Step 2: 把 VelStand 的 actor class 指向 DualExpertMLPModel + 加载逻辑."""
from pathlib import Path
import shutil

# ---- 1. 修改 microduck_velstand_env_cfg.py 的 actor cfg ----
P = Path(r"C:\duck_work\microduck_rl\src\mjlab_microduck\tasks\microduck_velstand_env_cfg.py")
src = P.read_text(encoding="utf-8")

# Backup
bak = P.with_suffix(".py.bak-p2")
shutil.copy(P, bak)
print("backup:", bak.name)

# Find the actor RslRlModelCfg block
old_actor = '''    actor=RslRlModelCfg(
        hidden_dims=(512, 256, 128),
        activation="elu",
        obs_normalization=True,'''

new_actor = '''    actor=RslRlModelCfg(
        class_name="mjlab_microduck.tasks.dual_expert_mlp.DualExpertMLPModel",
        hidden_dims=(512, 256, 128),
        activation="elu",
        obs_normalization=True,'''

assert old_actor in src, "actor cfg block not found"
src = src.replace(old_actor, new_actor, 1)
print("1. actor class_name -> DualExpertMLPModel")

P.write_text(src, encoding="utf-8")

# Verify
check = P.read_text(encoding="utf-8")
assert "DualExpertMLPModel" in check
assert "class_name=" in check
print("2. verified in file")

# ---- 2. 修改 distill.py 的 BC 蒸馏 — 双专家模式下不再需要外部教师 ----
# 因为两个分支本身就是"教师"(run8 行走 + run6 站立)
# 所以 PpoWithExpertBc 的 BC loss 可以关掉(或只保留 gate 学习的梯度)
P2 = Path(r"C:\duck_work\microduck_rl\src\mjlab_microduck\tasks\distill.py")
src2 = P2.read_text(encoding="utf-8")

# 把 BC coef 降为 0(双专家内部已有锚定, 不需要外部教师)
old_bc = '"coef": 1.0,'
new_bc = '"coef": 0.0,  # P2: dual expert has internal anchoring, no external BC needed'
assert old_bc in src2, "BC coef not found"
src2 = src2.replace(old_bc, new_bc, 1)

# 锚点也关掉(walk branch is frozen, doesn't need anchor)
old_anchor_coef = '"anchor_coef": 0.6'
new_anchor_coef = '"anchor_coef": 0.0  # P2: walk expert frozen, no anchor needed'
assert old_anchor_coef in src2, "anchor coef not found"
src2 = src2.replace(old_anchor_coef, new_anchor_coef, 1)

P2.write_text(src2, encoding="utf-8")

# Verify
check2 = P2.read_text(encoding="utf-8")
assert '"coef": 0.0' in check2
assert '"anchor_coef": 0.0' in check2
print("3. BC/anchor disabled (dual expert internal anchoring)")
print("\nDone - ready for run10 launch")
