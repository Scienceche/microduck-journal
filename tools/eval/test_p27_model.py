#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2.7 双归一化器测试: 继承 P2.6 全部路由/钳制/冻结/导出断言,
新增关键项: 【双分支与原生模型逐位对齐】—
  walk 分支输出 ≡ 原生 MLPModel(run8 权重+归一化器) 输出
  stand 分支输出 ≡ 原生 MLPModel(run6 权重+归一化器) 输出
(这是 A/B 实证后的回归防线: 合成不得改变任何专家的原生行为)
"""
import sys

sys.path.insert(0, r"C:\duck_work\microduck_rl\src")
import numpy as np
import torch
from tensordict import TensorDict
from rsl_rl.models.mlp_model import MLPModel

from mjlab_microduck.tasks.dual_expert_mlp import DualExpertMLPModel

FAILS = []


def check(name, ok, detail=""):
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, (" — " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


DIST = {"class_name": "GaussianDistribution", "init_std": 1.0, "std_type": "scalar"}
CKPT = r"C:\duck_work\microduck_rl\logs\rsl_rl\velstand\p27_dual_norm_init\model_0.pt"
R8 = r"C:\duck_work\microduck_rl\logs\rsl_rl\velocity\2026-11-10_13-20-40_velocity\model_3999.pt"
R6 = r"C:\duck_work\microduck_rl\logs\rsl_rl\microduck_stand\2026-11-09_09-06-54_microduck_stand\model_3999.pt"


def make():
    obs = TensorDict({"obs": torch.randn(2, 61)}, batch_size=[2])
    m = DualExpertMLPModel(
        obs, {"actor": ["obs"], "critic": ["obs"]}, "actor", 14,
        hidden_dims=(512, 256, 128), gravity_slice=5, cmd_slice=48,
        gate_k=12.0, gate_offset=0.5, cmd_w=40.0, cmd_c0=0.15,
        obs_normalization=True, distribution_cfg=dict(DIST))
    return m, obs


sd = torch.load(CKPT, map_location="cpu", weights_only=False)["actor_state_dict"]
m, obs_t = make()
info = m.load_state_dict({k: v for k, v in sd.items() if "distribution" not in k},
                         strict=False)
missing = [k for k in info.missing_keys if "distribution" not in k]
check("checkpoint 装载无缺失", not missing, str(missing[:4]))
m.eval()

# ---- 路由四情形(P2.6 同款) ----
def frame(gz, vx=0.0, vy=0.0, wz=0.0):
    o = torch.randn(1, 61)
    o[0, 3:6] = torch.tensor([0.0, 0.0, gz])
    o[0, 48:51] = torch.tensor([vx, vy, wz])
    return o


with torch.no_grad():
    g_walk = float(m._compute_gate(frame(-1.0, vx=0.3)).flatten()[0])
    g_stand = float(m._compute_gate(frame(-1.0)).flatten()[0])
    g_turn = float(m._compute_gate(frame(-1.0, wz=0.5)).flatten()[0])
    g_fall = float(m._compute_gate(frame(0.0, vx=0.3)).flatten()[0])
check("路由: 行走/站定/原地转/倒地",
      g_walk < 0.05 and g_stand > 0.95 and g_turn < 0.15 and g_fall > 0.98,
      "%.3f/%.3f/%.3f/%.3f" % (g_walk, g_stand, g_turn, g_fall))

# ---- 双分支与原生模型逐位对齐(P2.7 核心防线) ----
def native(path):
    obs = TensorDict({"obs": torch.randn(2, 61)}, batch_size=[2])
    nm = MLPModel(obs, {"actor": ["obs"], "critic": ["obs"]}, "actor", 14,
                  hidden_dims=(512, 256, 128), obs_normalization=True,
                  distribution_cfg=None)
    src = torch.load(path, map_location="cpu", weights_only=False)["actor_state_dict"]
    nm.load_state_dict({k: v for k, v in src.items()
                        if "stand_expert" not in k and "gate" not in k
                        and "distribution" not in k and "cmd" not in k}, strict=False)
    nm.eval()
    return nm


torch.manual_seed(3)
raw = torch.randn(6, 61)
raw[:3, 3:6] = torch.tensor([0., 0., -1.])   # 直立
raw[3:, 3:6] = torch.tensor([0., 0., 0.])    # 倒地
raw[:, 48:51] = torch.randn(6, 3) * 0.3      # 随机指令

n8, n6 = native(R8), native(R6)
with torch.no_grad():
    lat8 = m.get_latent(TensorDict({"obs": raw.clone()}, batch_size=[6]))
    walk_out = m.mlp(lat8)
    lat6 = m.stand_normalizer(raw)
    stand_out = m.stand_expert(lat6)
    ref8 = n8.mlp(n8.get_latent(TensorDict({"obs": raw.clone()}, batch_size=[6])))
    ref6 = n6.mlp(n6.get_latent(TensorDict({"obs": raw.clone()}, batch_size=[6])))
d8 = float((walk_out - ref8).abs().max())
d6 = float((stand_out - ref6).abs().max())
check("walk 分支 ≡ 原生 run8 (max|Δ|=0)", d8 == 0.0, "%.2e" % d8)
check("stand 分支 ≡ 原生 run6 (max|Δ|=0)", d6 == 0.0, "%.2e" % d6)

# ---- 冻结(含双归一化器) ----
m2, obs2 = make()
m2.load_state_dict({k: v for k, v in sd.items() if "distribution" not in k}, strict=False)
before = (m2.obs_normalizer._mean.clone(), m2.stand_normalizer._mean.clone())
m2.update_normalization(TensorDict({"obs": torch.randn(2, 61)}, batch_size=[2]))
check("双归一化器 no-op",
      torch.equal(before[0], m2.obs_normalizer._mean)
      and torch.equal(before[1], m2.stand_normalizer._mean))
trainable = [n for n, p in m2.named_parameters() if p.requires_grad]
check("只训 gate 四标量", trainable == ["gate_k", "gate_offset", "cmd_w", "cmd_c0"], str(trainable))
check("std_param 冻结", m2.distribution.std_param.requires_grad is False)
out = m2(obs2.clone()); out.sum().backward()
check("梯度达四标量", all(getattr(m2, n).grad is not None for n in
                          ("gate_k", "gate_offset", "cmd_w", "cmd_c0")))
check("专家零梯度", all(p.grad is None or float(p.grad.detach().abs().sum()) == 0
                        for n, p in m2.named_parameters()
                        if n.startswith(("mlp.", "stand_expert."))))

# ---- 导出 parity(四情形 + 双归一化器) ----
exp = m.as_onnx(verbose=False)
exp.eval()
m.eval()
with torch.no_grad():
    td = TensorDict({"obs": raw.clone()}, batch_size=[6])
    ref = m(td)
    got = exp(raw)
check("导出 parity max|Δ|<1e-4", float((ref - got).abs().max()) < 1e-4,
      "实际 %.2e" % float((ref - got).abs().max()))

try:
    import onnxruntime as ort
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp()) / "p27.onnx"
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
print("==== P2.7 模型测试全部 PASS ====")
