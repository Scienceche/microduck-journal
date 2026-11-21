#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集补丁修正: last_sample.servos 在 motion 模式为 None → 改用 native.joints
(两模式都有, 含 position_raw)。"""
import ast
from pathlib import Path

SRV = Path("/opt/duck-calib/server.py")
srv = SRV.read_text(encoding="utf-8")

old = '''            status = rpc("robot.busStatus", timeout=4.0) or {}
            servos = ((status.get("last_sample") or {}).get("servos")) or []
            if servos:
                samples.append({
                    "t": ts,
                    "joints": {str(s["id"]): s["position_raw"] for s in servos},
                })'''
new = '''            status = rpc("robot.busStatus", timeout=4.0) or {}
            servos = ((status.get("last_sample") or {}).get("servos")) or []
            if not servos:
                # motion 模式 last_sample 为空 → native.joints 两模式都在
                servos = (status.get("native") or {}).get("joints") or []
            if servos:
                samples.append({
                    "t": ts,
                    "joints": {str(s["id"]): s.get("position_raw")
                               for s in servos if s.get("position_raw") is not None},
                })'''
assert old in srv, "采样块锚点未找到"
srv = srv.replace(old, new, 1)
SRV.write_text(srv, encoding="utf-8")
ast.parse(SRV.read_text(encoding="utf-8"))
print("采样源修正 ✓")
