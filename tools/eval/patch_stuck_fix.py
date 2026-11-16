#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hot-fix 9 bug 修复: 脉冲过期后的清零搬到检测区(不依赖 if in_pulse 分支)。"""
from pathlib import Path

P = Path(r"C:\duck_work\kissqy-firmware\robotd\src\main.rs")
s = P.read_text(encoding="utf-8")

# 在检测代码之后、in_pulse 计算之前，加过期清零
old = """                } else if !stuck_recover.active() {
                    stuck_recover.reset();
                }
                let in_pulse = stuck_recover.active();"""
new = """                } else if !stuck_recover.active() {
                    stuck_recover.reset();
                }
                // Bug fix: 脉冲过期后清零 + controller.reset() 必须在这里做,
                // 不能放在 if in_pulse 分支内(过期后 in_pulse=false 永远走不进去)
                if stuck_recover.pulse_ended() {
                    controller.reset();
                    stuck_recover.pulse_until = None;
                    stuck_recover.fallen_since = Some(tick_start);
                    tracing::warn!("stuck recovery: pulse done, controller reset");
                }
                let in_pulse = stuck_recover.active();"""
assert old in s, "detection area"
s = s.replace(old, new, 1)

# 原来在 if in_pulse 内的过期清零删掉(已搬到上面)
old = """                        if stuck_recover.pulse_ended() {
                            // 脉冲刚结束: 清观测历史, 策略从"新"姿态开始
                            controller.reset();
                            stuck_recover.pulse_until = None;
                            stuck_recover.fallen_since = Some(tick_start); // 重新计时
                            tracing::warn!("stuck recovery: pulse done, controller reset");
                        }"""
assert old in s, "old pulse_ended in in_pulse branch"
s = s.replace(old, "", 1)

P.write_text(s, encoding="utf-8")
c = P.read_text(encoding="utf-8")
# 确认 pulse_ended 只出现一次(检测区)
assert c.count("pulse_ended()") == 1, c.count("pulse_ended()")
print("pulse 清零 bug 修复 ✓")
