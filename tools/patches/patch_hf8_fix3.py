#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from pathlib import Path

P = Path(r"C:\duck_work\kissqy-firmware\robotd\src\feetech_commissioning.rs")
s = P.read_text(encoding="utf-8")

# 1) abort_motion_all: format 内联 {suffix} 已捕获, 去掉位置参数 suffix
old = """        let _ = moving.reply.send(Err(format!(
            "{reason}; active ID {}; completed IDs {:?}{suffix}",
            moving.active_id, moving.completed, suffix
        )));"""
new = """        let _ = moving.reply.send(Err(format!(
            "{reason}; active ID {}; completed IDs {:?}{suffix}",
            moving.active_id, moving.completed
        )));"""
assert old in s, "suffix"
s = s.replace(old, new, 1)

# 2) 第二个 process_job 调用点(1070 区域)补 held_ids
old = """                        &mut motion,
                        &ids,
                        &mut off_pending,
                    );
                } else {"""
new = """                        &mut motion,
                        &ids,
                        &mut off_pending,
                        &mut held_ids,
                    );
                } else {"""
assert old in s, "second call"
s = s.replace(old, new, 1)

P.write_text(s, encoding="utf-8")
c = P.read_text(encoding="utf-8")
assert c.count("&mut held_ids,") == 2
assert "moving.completed\n" in c
print("fix3 落盘 ✓")
