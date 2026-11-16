#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""固件热修复 6: HD1910 观测位置 mod-4096 回卷。

根因(2026-11-13 黑匣子+双关节实测): 摔机冲击后舵机多圈计数器漂整圈(±4096),
观测路径把 −933/5105 这类值直接喂给策略/标定 → 策略读到假 ±5.7rad 关节角
→ 输出饱和冻结(run11 桥式卡住的真凶)。断电可复位但每摔一次复发。
回卷实测: 左膝 −933→+8.2°✓ 右髋 +5105→−2.4°✓(其余 14 关节不受影响)。
控制路径(position_signed_raw, Move 预载)保留原值, 防目标跳整圈。
"""
from pathlib import Path
import ast  # noqa: F401  (py 侧仅校验自身)

P = Path(r"C:\duck_work\kissqy-firmware\duck-control\src\feetech.rs")
s = P.read_text(encoding="utf-8")

old = """    pub fn decode(raw: &[u8]) -> Result<Self> {
        if raw.len() != 11 {
            return Err(IoError::ShortRead {
                what: "HD1910 feedback",
                expected: 11,
                got: raw.len(),
            });
        }
        Ok(Self {
            position_raw: signed_magnitude(u16::from_le_bytes([raw[0], raw[1]]), 15),
            position_wire_raw: u16::from_le_bytes([raw[0], raw[1]]),
            position_signed_raw: signed_magnitude(u16::from_le_bytes([raw[0], raw[1]]), 15),"""
new = """    pub fn decode(raw: &[u8]) -> Result<Self> {
        if raw.len() != 11 {
            return Err(IoError::ShortRead {
                what: "HD1910 feedback",
                expected: 11,
                got: raw.len(),
            });
        }
        // Hot-fix 6 (2026-11-13): observation positions wrap mod 4096. A hard
        // fall knocks the servo's multi-turn counter by exactly ±4096 (seen on
        // left_knee -933 and right_hip_pitch +5105 in the same evening); the
        // unwrapped value fed the policy a fake ±5.7 rad joint and saturated
        // its output (run11 bridge stall). The control path
        // (position_signed_raw, Move preload) keeps the raw value so goals
        // never jump a full turn.
        let pos_signed = signed_magnitude(u16::from_le_bytes([raw[0], raw[1]]), 15);
        Ok(Self {
            position_raw: pos_signed.rem_euclid(4096),
            position_wire_raw: u16::from_le_bytes([raw[0], raw[1]]),
            position_signed_raw: pos_signed,"""
assert old in s, "decode anchor"
s = s.replace(old, new, 1)
P.write_text(s, encoding="utf-8")

c = P.read_text(encoding="utf-8")
assert "pos_signed.rem_euclid(4096)" in c
assert c.count("position_signed_raw: pos_signed,") == 1
print("hot-fix 6 落盘 ✓ (观测回卷, 控制保留)")
