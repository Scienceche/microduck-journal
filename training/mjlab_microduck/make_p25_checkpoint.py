#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2.5 热启动起点: 复用 p2 model_0(权重已验证=run8+run6+run8归一化器),
只把 gate 初始值改尖锐: k 8→12, offset 0.4→0.5
(直立 gate 0.039→0.0025, 混合污染小一个量级; 倒地 0.997)。
"""
import sys
from pathlib import Path

sys.path.insert(0, "src")
import torch

SRC = "logs/rsl_rl/velstand/p2_dual_expert_init/model_0.pt"
OUT_DIR = Path("logs/rsl_rl/velstand/p25_gate_only_init")
OUT_DIR.mkdir(parents=True, exist_ok=True)

m0 = torch.load(SRC, map_location="cpu", weights_only=False)
a = m0["actor_state_dict"]
print("旧 gate: k=%.3f offset=%.3f" % (a["gate_k"], a["gate_offset"]))
a["gate_k"] = torch.tensor(12.0)
a["gate_offset"] = torch.tensor(0.5)
# 验证新 gate 语义(用公式直接算)
import math
# sigmoid(x) = 1/(1+e^{-x}); 模型公式 sigmoid(k*(tilt-offset))
def sig(x):
    return 1 / (1 + math.exp(-x))
g_up = sig(12 * (0.0 - 0.5))
g_dn = sig(12 * (1.0 - 0.5))
print("新 gate: k=12 offset=0.5 → 直立 %.4f / 倒地 %.4f" % (g_up, g_dn))
assert g_up < 0.005 and g_dn > 0.99

m0["infos"] = {"warm_start": "p2.5 gate-only (experts+normalizer frozen)"}
out = OUT_DIR / "model_0.pt"
torch.save(m0, out)
# 落盘复核
chk = torch.load(out, map_location="cpu", weights_only=False)["actor_state_dict"]
assert float(chk["gate_k"]) == 12.0 and float(chk["gate_offset"]) == 0.5
print("saved:", out)
