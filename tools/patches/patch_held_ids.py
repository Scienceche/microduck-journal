#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""固件热修复 8: held_ids 状态 + abort 分级(对抗审查 P0-1/P1-3)。

- abort_motion 改为只断【运动涉及关节】(active+remaining); 无 motion 时退回全量
- 新增 abort_motion_all: Relax(:470)/停机(:1204)保持一键全断(文档化急停)
- run() 维护 held_ids: TorqueHold on 成功加入 / on:false 移除 / 全断清空
- Status 增加 held_ids 字段(busStatus JSON 直达面板) + torque_state "held"
- 顺带修 P2-5: 测试 Motion 字面量补 hold: false
"""
from pathlib import Path

P = Path(r"C:\duck_work\kissqy-firmware\robotd\src\feetech_commissioning.rs")
s = P.read_text(encoding="utf-8")

# 1) abort_motion → 部分断; 新增 abort_motion_all
old = """fn abort_motion(
    bus: &mut Bus,
    ids: &[u8],
    motion: &mut Option<Motion>,
    reason: &str,
) -> Option<String> {
    let stop_error = torque_off(bus, ids).err();"""
new = """fn abort_motion(
    bus: &mut Bus,
    ids: &[u8],
    motion: &mut Option<Motion>,
    reason: &str,
) -> Option<String> {
    // Hot-fix 8 (2026-11-14 对抗审查 P0-1): 只断运动涉及关节(active+remaining),
    // 不波及 torque-hold 锁定的其他关节; 无 motion 时(空闲期总线连错)退回全量。
    let mut cut: Vec<u8> = motion
        .as_ref()
        .map(|m| {
            let mut v = vec![m.active_id];
            v.extend(m.remaining.iter().copied());
            v
        })
        .unwrap_or_else(|| ids.to_vec());
    cut.sort_unstable();
    cut.dedup();
    let stop_error = torque_off(bus, &cut).err();"""
assert old in s, "abort_motion"
s = s.replace(old, new, 1)

old = """fn abort_motion("""
new2 = """/// 全量断扭矩: Relax 与服务停机专用(急停语义, 会放掉所有锁定关节)。
fn abort_motion_all(
    bus: &mut Bus,
    ids: &[u8],
    motion: &mut Option<Motion>,
    reason: &str,
) -> Option<String> {
    let stop_error = torque_off(bus, ids).err();
    if let Some(moving) = motion.take() {
        let suffix = stop_error
            .as_ref()
            .map(|e| format!("; torque-off not confirmed: {e}"))
            .unwrap_or_default();
        let _ = moving.reply.send(Err(format!(
            "{reason}; active ID {}; completed IDs {:?}{suffix}",
            moving.active_id, moving.completed, suffix.trim_start_matches(';')
        )));
    }
    stop_error
}

fn abort_motion("""
i = s.find(new2)
assert s.count(new2) == 0, "already patched"
s = s.replace(old, new2, 1)

# 2) run() 循环加 held_ids 本地
old = """    let mut motion: Option<Motion> = None;
    let mut off_pending = None;"""
new = """    let mut motion: Option<Motion> = None;
    let mut off_pending = None;
    // Hot-fix 8: torque-hold 锁定中的关节(跨状态机存续, 全断/单关节解锁时移除)
    let mut held_ids: Vec<u8> = Vec::new();"""
assert old in s, "run locals"
s = s.replace(old, new, 1)

# 3) process_job 签名 + 调用点 + Relax 分支
old = """fn process_job("""
i = s.find(old)
# 读签名块
sig_end = s.find(") {", i)
sig = s[i:sig_end]
assert "off_pending" in sig, "process_job sig"
s = s.replace(sig, sig.replace("&mut off_pending", "&mut off_pending, held_ids: &mut Vec<u8>"), 1)
# 调用点(run 循环里)
old = """                    &mut motion,
                    &ids,
                    &mut off_pending,
                );"""
new = """                    &mut motion,
                    &ids,
                    &mut off_pending,
                    &mut held_ids,
                );"""
assert old in s, "process_job call"
s = s.replace(old, new, 1)
# Relax 分支 → abort_motion_all + 清 held
old = """    if matches!(job.command, BusCommand::Relax) {
        let stop_error = abort_motion(bus, ids, motion, "movement interrupted by relax");
        *off_pending = stop_error.clone();"""
new = """    if matches!(job.command, BusCommand::Relax) {
        let stop_error = abort_motion_all(bus, ids, motion, "movement interrupted by relax");
        if stop_error.is_none() {
            held_ids.clear();  // Relax 全断: 锁定状态一并清空(面板对账用)
        }
        *off_pending = stop_error.clone();"""
assert old in s, "relax branch"
s = s.replace(old, new, 1)

# 4) TorqueHold on:false 成功 → 移除 held
old = """            } else {
                let result = torque_off(bus, &[id])
                    .map(|_| json!({"id": id, "torque": "off"}));
                let _ = job.reply.send(result);
            }"""
new = """            } else {
                let result = torque_off(bus, &[id]).map(|_| {
                    held_ids.retain(|&x| x != id);
                    json!({"id": id, "torque": "off"})
                });
                let _ = job.reply.send(result);
            }"""
assert old in s, "hold-off branch"
s = s.replace(old, new, 1)

# 5) step_motion 签名 + 调用 + Ok(true) hold 入 held
old = """fn step_motion(
    bus: &mut Bus,
    samples: &BTreeMap<u8, ServoSample>,
    ids: &[u8],
    motion: &mut Option<Motion>,
    off_pending: &mut Option<String>,
) {"""
new = """fn step_motion(
    bus: &mut Bus,
    samples: &BTreeMap<u8, ServoSample>,
    ids: &[u8],
    motion: &mut Option<Motion>,
    off_pending: &mut Option<String>,
    held_ids: &mut Vec<u8>,
) {"""
assert old in s, "step_motion sig"
s = s.replace(old, new, 1)
# 调用点(run 循环, :1121 那个)
old = """            step_motion(&mut bus, &samples, &ids, &mut motion, &mut off_pending);
        }
        state
            .moving"""
new = """            step_motion(&mut bus, &samples, &ids, &mut motion, &mut off_pending, &mut held_ids);
        }
        state
            .moving"""
assert old in s, "step call"
s = s.replace(old, new, 1)
# Ok(true) hold → push
old = """        Ok(true) => {
            let done = motion.take().unwrap();
            let payload = if done.hold {"""
new = """        Ok(true) => {
            let done = motion.take().unwrap();
            if done.hold && !held_ids.contains(&done.active_id) {
                held_ids.push(done.active_id);
            }
            let payload = if done.hold {"""
assert old in s, "ok-true"
s = s.replace(old, new, 1)

# 6) 停机路径 → abort_motion_all + 清 held
old = """    let stop_error = abort_motion(&mut bus, &ids, &mut motion, "service stopping");"""
new = """    let stop_error = abort_motion_all(&mut bus, &ids, &mut motion, "service stopping");
    // (held_ids 在此即将随作用域结束丢弃; 全断已由上面完成)"""
assert old in s, "shutdown"
s = s.replace(old, new, 1)

# 7) Status 结构 + 构造点
old = """    torque_state: &'static str,
    torque_off_pending: Option<String>,"""
new = """    torque_state: &'static str,
    held_ids: Vec<u8>,
    torque_off_pending: Option<String>,"""
assert old in s, "Status struct"
s = s.replace(old, new, 1)
old = """                torque_state: if off_pending.is_some() {
                    "unknown"
                } else if let Some(m) = &motion {
                    match m.torque_confirmed {
                        Some(true) => "single_joint_enabled",
                        Some(false) => "off",
                        None => "unknown",
                    }
                } else {
                    "off"
                },
                torque_off_pending: off_pending.clone(),"""
new = """                torque_state: if off_pending.is_some() {
                    "unknown"
                } else if !held_ids.is_empty() {
                    "held"
                } else if let Some(m) = &motion {
                    match m.torque_confirmed {
                        Some(true) => "single_joint_enabled",
                        Some(false) => "off",
                        None => "unknown",
                    }
                } else {
                    "off"
                },
                held_ids: held_ids.clone(),
                torque_off_pending: off_pending.clone(),"""
assert old in s, "Status ctor"
s = s.replace(old, new, 1)

# 8) 测试字面量补 hold(P2-5)
old = """            torque_confirmed: Some(false),
            deadline: Instant::now() + SETTLE_TIMEOUT + Duration::from_secs(10),"""
if old in s:
    s = s.replace(old, """            hold: false,
            torque_confirmed: Some(false),
            deadline: Instant::now() + SETTLE_TIMEOUT + Duration::from_secs(10),""", 1)
else:
    # 测试里的构造格式可能不同: 找 :1353 附近含 last_target: None 的字面量补
    old2 = """                last_target: None,
                torque_confirmed: Some(false),
                deadline:"""
    assert old2 in s, "test literal"
    s = s.replace(old2, """                last_target: None,
                hold: false,
                torque_confirmed: Some(false),
                deadline:""", 1)

P.write_text(s, encoding="utf-8")
c = P.read_text(encoding="utf-8")
for k in ("abort_motion_all", "held_ids: Vec<u8>", "held_ids.clone()", '"held"',
          "held_ids.clear()", "held_ids.retain"):
    assert k in c, k
print("hot-fix 8 落盘 ✓")
