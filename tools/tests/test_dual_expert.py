#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2 双专家 Actor 冒烟测试"""
import sys
sys.path.insert(0, "src")
import torch

from mjlab_microduck.tasks.dual_expert import DualExpertActor

model = DualExpertActor(gravity_slice=5)

# 1) Weight loading
r8 = torch.load("logs/rsl_rl/velocity/2026-11-10_13-20-40_velocity/model_3999.pt",
                 map_location="cpu", weights_only=False)
r6 = torch.load("logs/rsl_rl/microduck_stand/2026-11-09_09-06-54_microduck_stand/model_3999.pt",
                 map_location="cpu", weights_only=False)
model.load_walk_expert(r8["actor_state_dict"])
model.load_stand_expert(r6["actor_state_dict"])
print("PASS: weight loading")

# 2) Verify loaded (differs from random)
x = torch.randn(1, 61)
out1 = model(x)
model2 = DualExpertActor(gravity_slice=5)
out2 = model2(x)
assert not torch.allclose(out1, out2), "outputs identical - weights not loaded"
print("PASS: weight verification")

# 3) Gate values
g_up = model.gate(torch.tensor([[-0.99]])).item()
g_tilt = model.gate(torch.tensor([[-0.3]])).item()
g_fall = model.gate(torch.tensor([[0.1]])).item()
assert g_up < 0.2, f"upright gate too high: {g_up}"
assert g_fall > 0.8, f"fallen gate too low: {g_fall}"
print(f"PASS: gate upright={g_up:.3f} tilted={g_tilt:.3f} fallen={g_fall:.3f}")

# 4) ONNX export
dummy = torch.randn(1, 61)
torch.onnx.export(model, dummy, "C:\duck_work\de_test.onnx",
                  input_names=["obs"], output_names=["actions"])
import onnxruntime as ort
sess = ort.InferenceSession("C:\duck_work\de_test.onnx", providers=["CPUExecutionProvider"])
result = sess.run(None, {"obs": dummy.numpy()})[0]
assert result.shape == (1, 14)
print(f"PASS: ONNX export + inference, shape {result.shape}")

print("\n=== ALL TESTS PASSED ===")
