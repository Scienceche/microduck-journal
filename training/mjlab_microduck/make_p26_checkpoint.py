#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2.6 起点: 复用 p25 起点(权重/归一化器/std 已验证), gate 增补命令分支
两个标量 (cmd_w=40, cmd_c0=0.15), k=12/offset=0.5 沿用。"""
import sys
from pathlib import Path

sys.path.insert(0, "src")
import torch

SRC = "logs/rsl_rl/velstand/p25_gate_only_init/model_0.pt"
OUT_DIR = Path("logs/rsl_rl/velstand/p26_dual_input_gate_init")
OUT_DIR.mkdir(parents=True, exist_ok=True)

m = torch.load(SRC, map_location="cpu", weights_only=False)
a = m["actor_state_dict"]
a["cmd_w"] = torch.tensor(40.0)
a["cmd_c0"] = torch.tensor(0.15)
m["infos"] = {"warm_start": "p2.6 dual-input gate (gravity+command), experts frozen"}
out = OUT_DIR / "model_0.pt"
torch.save(m, out)
chk = torch.load(out, map_location="cpu", weights_only=False)["actor_state_dict"]
assert abs(float(chk["gate_k"]) - 12.0) < 1e-6 and abs(float(chk["gate_offset"]) - 0.5) < 1e-6
assert abs(float(chk["cmd_w"]) - 40.0) < 1e-6 and abs(float(chk["cmd_c0"]) - 0.15) < 1e-6
n = len(chk)
print("P2.6 checkpoint: %d keys, gate=(k=12, off=0.5, w=40, c0=0.15)" % n)
print("saved:", out)
