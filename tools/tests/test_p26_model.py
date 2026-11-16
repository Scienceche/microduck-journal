#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2.6 双输入 gate 模型测试: 四情形路由 / 四参数钳制角点 / 冻结 / 导出 parity。

路由期望(初始 k=12,off=0.5,w=40,c0=0.15):
  行走(vx=0.3)   → gate<0.05  (走专家)
  站定(cmd=0)    → gate>0.95  (站专家)
  原地转(wz=0.5) → gate<0.15  (走专家, 转弯要踏步)
  倒地(任意指令) → gate>0.98  (起身专家)
"""
import sys

sys.path.insert(0, r"C:\duck_work\microduck_rl\src")
import numpy as np
import torch
from tensordict import TensorDict

from mjlab_microduck.tasks.dual_expert_mlp import (
    DualExpertMLPModel, GATE_K_RANGE, GATE_OFFSET_RANGE,
    CMD_W_RANGE, CMD_C0_RANGE)

FAILS = []


def check(name, ok, detail=""):
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" — " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


DIST = {"class_name": "GaussianDistribution", "init_std": 1.0, "std_type": "scalar"}


def make():
    obs = TensorDict({"obs": torch.randn(2, 61)}, batch_size=[2])
    return DualExpertMLPModel(
        obs, {"actor": ["obs"], "critic": ["obs"]}, "actor", 14,
        hidden_dims=(512, 256, 128), gravity_slice=5, cmd_slice=48,
        gate_k=12.0, gate_offset=0.5, cmd_w=40.0, cmd_c0=0.15,
        obs_normalization=True, distribution_cfg=dict(DIST))


# 1) 可训练参数 = gate 四标量, std 冻结
m = make()
trainable = [n for n, p in m.named_parameters() if p.requires_grad]
check("只训 gate 四标量", trainable == ["gate_k", "gate_offset", "cmd_w", "cmd_c0"], str(trainable))
check("std_param 冻结", m.distribution.std_param.requires_grad is False)

# 2) 四情形路由
def frame(gz, vx=0.0, vy=0.0, wz=0.0):
    o = torch.randn(1, 61)
    o[0, 3:6] = torch.tensor([0.0, 0.0, gz])
    o[0, 48:51] = torch.tensor([vx, vy, wz])
    return o


m.eval()
with torch.no_grad():
    g_walk = float(m._compute_gate(frame(-1.0, vx=0.3)).flatten()[0])
    g_stand = float(m._compute_gate(frame(-1.0)).flatten()[0])
    g_turn = float(m._compute_gate(frame(-1.0, wz=0.5)).flatten()[0])
    g_fall = float(m._compute_gate(frame(0.0, vx=0.3)).flatten()[0])
    g_fall0 = float(m._compute_gate(frame(0.0)).flatten()[0])
check("行走→走专家", g_walk < 0.05, "%.4f" % g_walk)
check("站定→站专家", g_stand > 0.95, "%.4f" % g_stand)
check("原地转→走专家", g_turn < 0.15, "%.4f" % g_turn)
check("倒地(行走指令)→起身专家", g_fall > 0.98, "%.4f" % g_fall)
check("倒地(零指令)→起身专家", g_fall0 > 0.98, "%.4f" % g_fall0)

# 3) 四参数钳制角点
m2 = make()
with torch.no_grad():
    m2.gate_k.fill_(1.0); m2.gate_offset.fill_(99.0)
    m2.cmd_w.fill_(1.0); m2.cmd_c0.fill_(99.0)
    m2._compute_gate(frame(-1.0))
ok = (GATE_K_RANGE[0] <= float(m2.gate_k) <= GATE_K_RANGE[1]
      and GATE_OFFSET_RANGE[0] - 1e-6 <= float(m2.gate_offset) <= GATE_OFFSET_RANGE[1] + 1e-6
      and CMD_W_RANGE[0] <= float(m2.cmd_w) <= CMD_W_RANGE[1]
      and CMD_C0_RANGE[0] - 1e-6 <= float(m2.cmd_c0) <= CMD_C0_RANGE[1] + 1e-6)
check("四参数钳制回安全包络", ok,
      "k=%.0f off=%.2f w=%.0f c0=%.2f" % (float(m2.gate_k), float(m2.gate_offset),
                                          float(m2.cmd_w), float(m2.cmd_c0)))
# 钳后最坏角语义: (k=8,off=0.35,w=8,c0=0.4) 站定 gate / (w=60,c0=0.1) 行走 gate
m3 = make()
with torch.no_grad():
    m3.gate_k.fill_(8.0); m3.gate_offset.fill_(0.35)
    m3.cmd_w.fill_(8.0); m3.cmd_c0.fill_(0.40)
    worst_stand = float(m3._compute_gate(frame(-1.0)).flatten()[0])
    m3.cmd_w.fill_(60.0); m3.cmd_c0.fill_(0.10)
    worst_walk = float(m3._compute_gate(frame(-1.0, vx=0.3)).flatten()[0])
check("最坏角: 站定仍偏站(≥0.6)/行走仍偏走(≤0.2)", worst_stand >= 0.6 and worst_walk <= 0.2,
      "%.2f/%.2f" % (worst_stand, worst_walk))

# 4) 归一化器 no-op + 梯度只达 gate 四标量
m4 = make()
obs4 = TensorDict({"obs": torch.randn(2, 61)}, batch_size=[2])
before = m4.obs_normalizer._mean.clone()
m4.update_normalization(obs4)
check("normalizer no-op", torch.equal(before, m4.obs_normalizer._mean))
out = m4(obs4.clone()); out.sum().backward()
grads_ok = all(getattr(m4, n).grad is not None and abs(float(getattr(m4, n).grad.detach())) > 0
               for n in ("gate_k", "gate_offset", "cmd_w", "cmd_c0"))
check("梯度达四标量(含命令分支)", grads_ok)
check("专家零梯度", all(p.grad is None or float(p.grad.detach().abs().sum()) == 0
                        for n, p in m4.named_parameters()
                        if n.startswith(("mlp.", "stand_expert."))))

# 5) 导出 parity: 覆盖 行走/站定/倒地 三段(两个分支都要被执行)
exp = m.as_onnx(verbose=False)
exp.eval()
m.eval()
raw = torch.cat([frame(-1.0, vx=0.3), frame(-1.0), frame(0.0, vx=0.3),
                 frame(-1.0, wz=0.5)], dim=0)
td = TensorDict({"obs": raw.clone()}, batch_size=[4])
with torch.no_grad():
    ref = m(td)
    got = exp(raw)
check("导出 parity(四情形) max|Δ|<1e-4", float((ref - got).abs().max()) < 1e-4,
      "实际 %.2e" % float((ref - got).abs().max()))
n_exp = sum(p.numel() for p in exp.parameters()) + sum(b.numel() for b in exp.buffers())
n_one = sum(p.numel() for p in exp.mlp.parameters())
check("导出含双专家+gate", n_exp >= 2 * n_one, "%d vs 单专家 %d" % (n_exp, n_one))

# 6) ONNX runtime parity
try:
    import onnxruntime as ort
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp()) / "p26.onnx"
    torch.onnx.export(exp, (torch.zeros(1, 61),), str(tmp), input_names=["obs"],
                      output_names=["actions"], opset_version=17, dynamo=False)
    sess = ort.InferenceSession(str(tmp), providers=["CPUExecutionProvider"])
    o = sess.run(None, {"obs": raw[:1].numpy().astype(np.float32)})[0]
    check("ONNX runtime parity", float(np.abs(ref[:1].numpy() - o).max()) < 1e-4)
except ImportError:
    print("[SKIP] onnxruntime")

print()
if FAILS:
    print("==== %d FAIL: %s ====" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("==== P2.6 模型测试全部 PASS ====")
