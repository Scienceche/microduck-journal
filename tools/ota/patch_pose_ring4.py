#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集 v4: 环形缓冲 20s→120s, 前置/后补秒数自由填写(前5-120 / 后0-30)。"""
import ast
import re
from pathlib import Path

SRV = Path("/opt/duck-calib/server.py")
IDX = Path("/opt/duck-calib/index.html")

s = SRV.read_text(encoding="utf-8")
a1 = "RING_SECONDS = 20"
b1 = "RING_SECONDS = 120  # 2026-11-21 v4: 用户要求可回溯更长问题场景"
assert a1 in s
s = s.replace(a1, b1, 1)
SRV.write_text(s, encoding="utf-8")
ast.parse(s)

h = IDX.read_text(encoding="utf-8")
a2 = '''    <span class="dim">问题姿态抓拍(环形滚录):</span>
    <select id="motionSec"><option value="5">前5秒</option><option value="10" selected>前10秒</option><option value="15">前15秒</option></select>
    <span class="dim">+后3秒</span>'''
b2 = '''    <span class="dim">问题姿态抓拍:</span>
    <span class="dim">前</span><input type="number" id="motionPre" value="10" min="1" max="120" step="1" style="width:56px"><span class="dim">秒</span>
    <span class="dim">+后</span><input type="number" id="motionPost" value="3" min="0" max="30" step="1" style="width:50px"><span class="dim">秒</span>'''
assert a2 in h, "UI 锚点未找到"
h = h.replace(a2, b2, 1)

a3 = '''  const pre=parseFloat($("#motionSec").value||"10");
  const st=$("#motionStatus");
  st.textContent="抓拍中(前"+pre+"秒+后3秒)...";
  const r=await api("/api/pose/record",{name,pre:pre,post:3});'''
b3 = '''  const pre=Math.min(120,Math.max(1,parseFloat($("#motionPre").value||"10")));
  const post=Math.min(30,Math.max(0,parseFloat($("#motionPost").value||"3")));
  const st=$("#motionStatus");
  st.textContent="抓拍中(前"+pre+"秒+后"+post+"秒)...";
  const r=await api("/api/pose/record",{name,pre:pre,post:post});'''
assert a3 in h, "JS 锚点未找到"
h = h.replace(a3, b3, 1)
IDX.write_text(h, encoding="utf-8")
chk = IDX.read_text(encoding="utf-8")
for probe in ("motionPre", "motionPost"):
    assert probe in chk, probe + " 未落盘"
print("v4 ✓ 缓冲120s + 前后秒数自填")
