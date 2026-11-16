#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hot-fix 8 编译修正: process_job 签名 + 重复 hold 字段 + abort_motion_all format。"""
from pathlib import Path

P = Path(r"C:\duck_work\kissqy-firmware\robotd\src\feetech_commissioning.rs")
s = P.read_text(encoding="utf-8")

# 1) process_job 签名补 held_ids 参数
old = """    ids: &[u8],
    off_pending: &mut Option<String>,
) {
    if job.reply.is_closed() {"""
new = """    ids: &[u8],
    off_pending: &mut Option<String>,
    held_ids: &mut Vec<u8>,
) {
    if job.reply.is_closed() {"""
assert old in s, "sig"
s = s.replace(old, new, 1)

# 2) Move 构造点误插的第二个 hold(在 last_target 与 torque_confirmed 之间)
old = """                last_target: None,
                hold: false,
                torque_confirmed: Some(false),
                deadline: Instant::now()
                    + Duration::from_millis(duration_ms)"""
new = """                last_target: None,
                torque_confirmed: Some(false),
                deadline: Instant::now()
                    + Duration::from_millis(duration_ms)"""
assert old in s, "dupe Move"
s = s.replace(old, new, 1)

# 3) abort_motion_all 的 format: suffix 去掉错误的双引用
old = '''        let _ = moving.reply.send(Err(format!(
            "{reason}; active ID {}; completed IDs {:?}{suffix}",
            moving.active_id, moving.completed, suffix.trim_start_matches(';')
        )));'''
new = '''        let _ = moving.reply.send(Err(format!(
            "{reason}; active ID {}; completed IDs {:?}{suffix}",
            moving.active_id, moving.completed, suffix
        )));'''
if old in s:
    s = s.replace(old, new, 1)

P.write_text(s, encoding="utf-8")
c = P.read_text(encoding="utf-8")
assert "held_ids: &mut Vec<u8>,\n) {" in c
assert c.count("hold: false,") == 2, c.count("hold: false,")
print("修正落盘 ✓")
