#!/usr/bin/env python3
"""Fix ONNX export in test_p2_pipeline.py"""
from pathlib import Path

p = Path(r"C:\duck_work\test_p2_pipeline.py")
s = p.read_text(encoding="utf-8")

old_block = '''torch.onnx.export(
    model._dual_forward, dummy, r"C:\\duck_work\\de_mlp_test.onnx",
    input_names=["obs"], output_names=["actions"])'''

new_block = '''class _ExportWrapper(nn.Module):
    def __init__(self, m):
        super().__init__()
        self.m = m
    def forward(self, obs):
        return self.m._dual_forward(obs)

wrapper = _ExportWrapper(model)
torch.onnx.export(
    wrapper, dummy, r"C:\\duck_work\\de_mlp_test.onnx",
    input_names=["obs"], output_names=["actions"])'''

assert old_block in s, "ONNX block not found"
s = s.replace(old_block, new_block)

# Add nn import
if "import torch.nn as nn" not in s:
    s = s.replace("import torch\n", "import torch\nimport torch.nn as nn\n")

p.write_text(s, encoding="utf-8")
print("fixed")
