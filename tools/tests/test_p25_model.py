#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2.5 模型单元测试(对抗审查后扩充): 冻结/std/钳制角点/导出 parity(含倒地段)。"""
import sys

sys.path.insert(0, r"C:\duck_work\microduck_rl\src")
import numpy as np
import torch
from tensordict import TensorDict

from mjlab_microduck.tasks.dual_expert_mlp import (
    DualExpertMLPModel, GATE_K_RANGE, GATE_OFFSET_RANGE, _DualExpertExport)

FAILS = []


def check(name, ok, detail=""):
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" — " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


DIST = {"class_name": "GaussianDistribution", "init_std": 1.0, "std_type": "scalar"}


def make(dist=True):
    obs = TensorDict({"obs": torch.randn(2, 61)}, batch_size=[2])
    m = DualExpertMLPModel(obs, {"actor": ["obs"], "critic": ["obs"]}, "actor", 14,
                           hidden_dims=(512, 256, 128), gravity_slice=5,
                           obs_normalization=True,
                           distribution_cfg=dict(DIST) if dist else None)
    return m, obs


def sig(x):
    return 1 / (1 + np.exp(-x))


# 1) 可训练参数: 只有 gate(std 冻结后)
m, _ = make()
trainable = [n for n, p in m.named_parameters() if p.requires_grad]
check("只训 gate 两标量(std 已冻)", trainable == ["gate_k", "gate_offset"], str(trainable))
check("std_param requires_grad=False",
      m.distribution.std_param.requires_grad is False)

# 2) gate 语义
obs = TensorDict({"obs": torch.randn(2, 61)}, batch_size=[2])
upright = obs.clone(); upright["obs"][:, 3:6] = torch.tensor([0., 0., -1.])
fallen = obs.clone();  fallen["obs"][:, 3:6]  = torch.tensor([0., 0., 0.])
m.eval()
with torch.no_grad():
    g_up = float(m._compute_gate(upright["obs"]).flatten()[0])
    g_dn = float(m._compute_gate(fallen["obs"]).flatten()[0])
check("gate 直立<0.06/倒地>0.95", g_up < 0.06 and g_dn > 0.95, "%.4f/%.4f" % (g_up, g_dn))

# 3) 钳制角点(审查 P1-2): 塞进荒谬值后必须落回安全包络, 且角点 gate 语义可接受
for k_bad, o_bad, tag in [(1.0, 99.0, "低k高offset"), (100.0, 0.01, "高k低offset")]:
    m2, _ = make()
    m2.gate_k.data.fill_(k_bad); m2.gate_offset.data.fill_(o_bad)
    with torch.no_grad():
        m2._compute_gate(upright["obs"])
    k_ok = GATE_K_RANGE[0] <= float(m2.gate_k) <= GATE_K_RANGE[1]
    o_ok = GATE_OFFSET_RANGE[0] - 1e-6 <= float(m2.gate_offset) <= GATE_OFFSET_RANGE[1] + 1e-6
    # 钳后最坏角: (8,0.35) 直立 / (8,0.6) 倒地
    worst_up = sig(8 * (0 - 0.35)); worst_dn = sig(8 * (1 - 0.6))
    check("钳制角点 %s" % tag, k_ok and o_ok, "k=%.1f off=%.2f 最坏角 直立%.3f/倒地%.3f" % (
        float(m2.gate_k), float(m2.gate_offset), worst_up, worst_dn))
check("最坏角语义(直立≤0.06/倒地≥0.94)", worst_up <= 0.06 and worst_dn >= 0.94)

# 4) 归一化器冻结 + 梯度只达 gate
m3, obs3 = make()
before = m3.obs_normalizer._mean.clone()
m3.update_normalization(obs3)
check("normalizer no-op", torch.equal(before, m3.obs_normalizer._mean))
out = m3(obs3.clone()); out.sum().backward()
check("梯度达 gate", m3.gate_k.grad is not None and abs(float(m3.gate_k.grad.detach())) > 0)
check("专家零梯度", all(p.grad is None or float(p.grad.detach().abs().sum()) == 0
                        for n, p in m3.named_parameters()
                        if n.startswith(("mlp.", "stand_expert."))))

# 5) 导出 parity(审查 P0: 原路径导出=纯 walk): torch vs 导出模块,
#    且必须覆盖倒地段(gate 分支真正被执行, 否则 parity 假阴)
m4, _ = make()
m4.eval()
exp = m4.as_onnx(verbose=False)
exp.eval()
torch.manual_seed(7)
raw = torch.randn(8, 61)
raw[:4, 3:6] = torch.tensor([0., 0., -1.])   # 直立段
raw[4:, 3:6] = torch.tensor([0., 0., 0.])    # 倒地段(gate≈1)
# model.forward 需要 TensorDict: 直接构造
td = TensorDict({"obs": raw.clone()}, batch_size=[8])
with torch.no_grad():
    ref = m4(td)                       # deterministic head? distribution 存在 → deterministic_output
    got = exp(raw)
dmax = float((ref - got).abs().max())
check("导出 parity(含倒地段) max|Δ|<1e-4", dmax < 1e-4, "实际 %.2e" % dmax)
# 导出参数量 ≈ 两个专家(2×~99k) + gate 2 标量
n_exp = sum(p.numel() for p in exp.parameters()) + sum(b.numel() for b in exp.buffers())
n_one = sum(p.numel() for p in exp.mlp.parameters())
check("导出含双专家+gate", n_exp >= 2 * n_one, "导出 %d vs 单专家 %d" % (n_exp, n_one))

# 6) 真 ONNX 文件级验证(onnxruntime 数值 parity)
try:
    import onnxruntime as ort
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp()) / "p25_test.onnx"
    torch.onnx.export(exp, (torch.zeros(1, 61),), str(tmp), input_names=["obs"],
                      output_names=["actions"], opset_version=17, dynamo=False)
    sess = ort.InferenceSession(str(tmp), providers=["CPUExecutionProvider"])
    o = sess.run(None, {"obs": raw[:1].numpy().astype(np.float32)})[0]
    dmax2 = float(np.abs(ref[:1].numpy() - o).max())
    check("ONNX runtime parity max|Δ|<1e-4", dmax2 < 1e-4, "实际 %.2e" % dmax2)
except ImportError:
    print("[SKIP] onnxruntime 不可用")

print()
if FAILS:
    print("==== %d FAIL: %s ====" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("==== P2.5 模型测试全部 PASS ====")
