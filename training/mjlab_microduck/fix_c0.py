#!/usr/bin/env python3
"""修复 gate cmd_c0: 0.282 → 0.10（纯前进时 gate 35%站立导致小步）。"""
import math
import sys
from pathlib import Path

sys.path.insert(0, "src")
import torch

CKPT = "logs/rsl_rl/velstand/p29_run13_500_init/model_0.pt"
d = torch.load(CKPT, map_location="cpu", weights_only=False)
a = d["actor_state_dict"]
print("旧 gate:", {k: round(float(a[k]), 3) for k in ("gate_k", "gate_offset", "cmd_w", "cmd_c0")})

a["cmd_c0"] = torch.tensor(0.10)

def sig(x):
    return 1 / (1 + math.exp(-x))

print("新 gate 验证:")
for label, cmd in [("前进0.3", 0.3), ("前进0.15", 0.15), ("停止", 0.0)]:
    g = 1 - (1 - sig(10.289 * (0 - 0.45))) * (1 - sig(36.619 * (0.10 - cmd)))
    print("  %s: gate=%.3f" % (label, g))

out = "logs/rsl_rl/velstand/p291_c010/model_0.pt"
Path(out).parent.mkdir(parents=True, exist_ok=True)
d["infos"] = {"fix": "c0 0.282->0.10, 纯前进时 gate 35%站立导致小步"}
torch.save(d, out)
chk = torch.load(out, map_location="cpu", weights_only=False)["actor_state_dict"]
assert abs(float(chk["cmd_c0"]) - 0.10) < 1e-6
assert torch.equal(chk["mlp.0.weight"], a["mlp.0.weight"])
print("saved:", out)
