#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""固件热修复 7: BusCommand::TorqueHold — 单关节保持扭矩(标定台锁定开关)。

用户需求: 标定时逐关节调整 — 摆好一个关节锁住(扭矩保持当前位置), 再去摆下一个。
Move 动作会动完自动松扭矩, 不满足"锁定"语义。TorqueHold{on:true} 复用 Move 的
Preload→ConfirmGoal→Enable→ConfirmEnabled 安全链后在确认扭矩后直接结束状态机
(不进入 Moving/Disable); TorqueHold{on:false} 对单关节 torque_off+确认。
Relax 仍然一键切断全部扭矩(既有安全语义不变)。
"""
from pathlib import Path

P = Path(r"C:\duck_work\kissqy-firmware\robotd\src\feetech_commissioning.rs")
s = P.read_text(encoding="utf-8")

# 1) 枚举加变体(接在 Move 之后)
old = """    Move {
        #[arg(long)]
        id: u8,
        #[arg(long)]
        position: u16,
        #[arg(long, default_value_t = 2000)]
        duration_ms: u64,
    },
    /// Sequentially center loose, unassembled servos. Never a joint calibration."""
new = """    Move {
        #[arg(long)]
        id: u8,
        #[arg(long)]
        position: u16,
        #[arg(long, default_value_t = 2000)]
        duration_ms: u64,
    },
    /// Hold one joint's torque at its current position (calibration aid);
    /// `on=false` releases just that joint. Relax still cuts everything.
    TorqueHold {
        #[arg(long)]
        id: u8,
        #[arg(long)]
        on: bool,
    },
    /// Sequentially center loose, unassembled servos. Never a joint calibration."""
assert old in s, "enum anchor"
s = s.replace(old, new, 1)

# 2) deadline 表(接在 Move 的 deadline 分支前)
old = """fn command_timeout(command: &BusCommand) -> Duration {
    match command {
        BusCommand::Move { duration_ms, .. } => {"""
new = """fn command_timeout(command: &BusCommand) -> Duration {
    match command {
        BusCommand::TorqueHold { .. } => SETTLE_TIMEOUT + Duration::from_secs(10),
        BusCommand::Move { duration_ms, .. } => {"""
assert old in s, "deadline anchor"
s = s.replace(old, new, 1)

# 3) Motion 结构体加 hold 字段
old = """    completed: Vec<u8>,
    center: bool,
    stage: MotionStage,"""
new = """    completed: Vec<u8>,
    center: bool,
    hold: bool,
    stage: MotionStage,"""
assert old in s, "Motion struct anchor"
s = s.replace(old, new, 1)

# 4) Move 与 Center 两个构造点补 hold: false(第 3 处 Some(false) 在测试里, 模式不同不受影响)
s = s.replace("center: false,\n                stage: MotionStage::Preload,",
              "center: false,\n                hold: false,\n                stage: MotionStage::Preload,", 1)
s = s.replace("center: true,\n                stage: MotionStage::Preload,",
              "center: true,\n                hold: false,\n                stage: MotionStage::Preload,", 1)
assert s.count("hold: false,") == 2, "ctor holds: %d" % s.count("hold: false,")

# 5) 派发: TorqueHold 分支(插在 `match job.command {` 的 Move 分支前)
old = """    match job.command {
        BusCommand::Move {"""
new = """    match job.command {
        BusCommand::TorqueHold { id, on } => {
            if !ids.contains(&id) {
                let _ = job.reply.send(Err(format!("unknown joint id {id}")));
                return;
            }
            if on {
                let Some(fb) = samples.get(&id) else {
                    let _ = job
                        .job_reply_no_feedback();
                    return;
                };
                let current = fb.feedback.position_raw;
                if !(0..=4095).contains(&current) {
                    let _ = job.reply.send(Err(format!(
                        "position raw {current} outside 0..4095; bus unstable?"
                    )));
                    return;
                }
                *motion = Some(Motion {
                    remaining: VecDeque::new(),
                    active_id: id,
                    from: current,
                    target: current as u16,
                    started: Instant::now(),
                    duration: Duration::from_millis(0),
                    reply: job.reply,
                    completed: vec![],
                    center: false,
                    hold: true,
                    stage: MotionStage::Preload,
                    last_target: None,
                    torque_confirmed: Some(false),
                    deadline: Instant::now() + SETTLE_TIMEOUT + Duration::from_secs(5),
                });
            } else {
                let result = torque_off(bus, &[id])
                    .map(|_| json!({"id": id, "torque": "off"}));
                let _ = job.reply.send(result);
            }
        }
        BusCommand::Move {"""
assert old in s, "dispatch anchor"
s = s.replace(old, new, 1)

# 修正第 5 步里的占位(直接发 Err)
s = s.replace("""                    let _ = job
                        .job_reply_no_feedback();
                    return;""",
"""                    let _ = job
                        .reply
                        .send(Err("no feedback for this joint this cycle; retry".into()));
                    return;""", 1)

# 6) 状态机: ConfirmEnabled 后 hold 直接完成(不进 Moving)
old = """                m.torque_confirmed = Some(true);
                m.started = Instant::now();
                m.stage = MotionStage::Moving;"""
new = """                m.torque_confirmed = Some(true);
                m.started = Instant::now();
                if m.hold {
                    // 标定锁定: 扭矩已确认即完成 — 不运动、不自动释放。
                    return Ok(true);
                }
                m.stage = MotionStage::Moving;"""
assert old in s, "ConfirmEnabled anchor"
s = s.replace(old, new, 1)

# 7) 完成回复区分 hold
old = """        Ok(true) => {
            let done = motion.take().unwrap();
            let _ = done.reply.send(Ok(
                json!({"completed_ids":done.completed,"position_raw":done.target,"torque":"off"}),
            ));
        }"""
new = """        Ok(true) => {
            let done = motion.take().unwrap();
            let payload = if done.hold {
                json!({"id": done.active_id, "position_raw": done.target, "torque": "held"})
            } else {
                json!({"completed_ids":done.completed,"position_raw":done.target,"torque":"off"})
            };
            let _ = done.reply.send(Ok(payload));
        }"""
assert old in s, "reply anchor"
s = s.replace(old, new, 1)

P.write_text(s, encoding="utf-8")
c = P.read_text(encoding="utf-8")
for k in ("TorqueHold {", "BusCommand::TorqueHold { .. } => SETTLE_TIMEOUT",
          "hold: bool,", "hold: true,", "标定锁定", '"torque": "held"'):
    assert k in c, k
print("hot-fix 7(TorqueHold) 落盘 ✓")
