#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""固件热修复 9: 卡住自动恢复 — fallen 持续 >10s 时瞬时零增益放松 0.8s。

机制: 不动 safety 系统(零侵入), 在 controller.step() 的输出端拦截:
  检测到 fallen 持续 >10s 且策略在跑 → 接下来 0.8s 输出 gain=0 (关节自由)
  → 关节被动回弹(深折腿弹回可达范围) → controller.reset() 清观测历史
  → 策略从新姿态重试。最多 3 次, 恢复站立后归零。
"""
from pathlib import Path

P = Path(r"C:\duck_work\kissqy-firmware\robotd\src\main.rs")
s = P.read_text(encoding="utf-8")

# 1) 在 controller.step() 调用前加状态变量声明(找到外层作用域)
# 声明放在 (true, Some(sensors)) 分支之前的作用域 — 搜 was_driving 附近
old = """            (true, Some(sensors)) => {
                let controller = controller.as_mut().expect("driving implies a controller");"""
new = """            (true, Some(sensors)) => {
                let controller = controller.as_mut().expect("driving implies a controller");
                // ---- Hot-fix 9: 卡住自动恢复(方案 B) ----
                // fallen 持续 >10s → 零增益 0.8s → reset 观测 → 重试。最多 3 次。
                if safety.fallen() {
                    if stuck_recover.fallen_since.is_none() {
                        stuck_recover.fallen_since = Some(tick_start);
                        stuck_recover.pulses = 0;
                    }
                    let stuck_secs = stuck_recover
                        .fallen_since
                        .map(|t| t.elapsed().as_secs_f64())
                        .unwrap_or(0.0);
                    if stuck_secs > 10.0
                        && stuck_recover.pulses < 3
                        && stuck_recover.pulse_until.is_none()
                    {
                        stuck_recover.pulses += 1;
                        stuck_recover.pulse_until =
                            Some(tick_start + std::time::Duration::from_millis(800));
                        tracing::warn!(
                            attempt = stuck_recover.pulses,
                            stuck_secs,
                            "stuck recovery: pulsing gain to 0 for 0.8s"
                        );
                    }
                } else if !stuck_recover.active() {
                    stuck_recover.reset();
                }
                let in_pulse = stuck_recover.active();"""
assert old in s, "controller step anchor"
s = s.replace(old, new, 1)

# 2) 在 step 输出处拦截: 脉冲期替换为零增益 + 当前位置
old = """                match controller.step(sensors, &adapted, snapshot.pose.active, dt, scale_mult) {
                    Ok(step) => (
                        step.targets,
                        step.gain,"""
new = """                match controller.step(sensors, &adapted, snapshot.pose.active, dt, scale_mult) {
                    Ok(step) => if in_pulse {
                        // 卡住恢复脉冲: 零增益 + 保持当前位置 → 关节自由回弹
                        let now_targets: [f64; duck_control::NUM_JOINTS] =
                            core::array::from_fn(|i| sensors.positions.get(i).copied().unwrap_or(0.0));
                        if stuck_recover.pulse_ended() {
                            // 脉冲刚结束: 清观测历史, 策略从"新"姿态开始
                            controller.reset();
                            stuck_recover.pulse_until = None;
                            stuck_recover.fallen_since = Some(tick_start); // 重新计时
                            tracing::warn!("stuck recovery: pulse done, controller reset");
                        }
                        (
                            now_targets,
                            0.0,"""
assert old in s, "step output anchor"
s = s.replace(old, new, 1)

# 3) 闭合 if-else(脉冲期返回后, 正常路径保持)
old = """                        // A scripted move is motion whatever the twist says; so is walking.
                        step.busy || command.twist_magnitude() > 0.0,
                        step.label,
                    ),"""
new = """                            false,
                            "stuck_pulse".into(),
                        )
                    } else {
                        (
                            step.targets,
                            step.gain,
                            step.busy || command.twist_magnitude() > 0.0,
                            step.label,
                        )
                    },"""
assert old in s, "step close anchor"
s = s.replace(old, new, 1)

# 4) 声明 StuckRecovery 结构和实例(放在 main 函数的局部变量区 — 搜 was_driving 声明)
old = """    let mut was_driving = false;"""
assert old in s, "was_driving anchor"
# 在其前面插入结构体定义和实例
struct_def = """
/// Hot-fix 9: 卡住自动恢复状态(方案 B — 固件级, 不训练)
struct StuckRecovery {
    fallen_since: Option<std::time::Instant>,
    pulses: u32,
    pulse_until: Option<std::time::Instant>,
}
impl StuckRecovery {
    fn active(&self) -> bool {
        self.pulse_until.is_some_and(|t| std::time::Instant::now() < t)
    }
    fn pulse_ended(&self) -> bool {
        self.pulse_until.is_some_and(|t| std::time::Instant::now() >= t)
    }
    fn reset(&mut self) {
        self.fallen_since = None;
        self.pulses = 0;
        self.pulse_until = None;
    }
}
"""
s = s.replace(old, struct_def + old, 1)
# 实例声明
s = s.replace(
    "    let mut was_driving = false;",
    "    let mut was_driving = false;\n    let mut stuck_recover = StuckRecovery { fallen_since: None, pulses: 0, pulse_until: None };",
    1,
)

P.write_text(s, encoding="utf-8")
c = P.read_text(encoding="utf-8")
for k in ("StuckRecovery", "stuck_recover", "stuck_pulse", "pulsing gain to 0"):
    assert k in c, k
print("hot-fix 9 落盘 ✓")
