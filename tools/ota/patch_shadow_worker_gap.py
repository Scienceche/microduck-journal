#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修 shadow.rs 里影子线程"等帧超时即退出"的 bug, 并加回归测试(2026-11-26)。

现象(真控制环集成测试第一次跑出来): 报告 active=false, running_secs=0.0507, submitted=1,
inferred=0 —— 影子线程启动 50ms 后自己退出。
原因: worker 用 `while let Ok(frame) = rx.recv_timeout(WAKE)`, recv_timeout 等不到帧返回
      Err(Timeout), while let 把它当成循环结束。主循环只在"主策略出了步"的拍上投帧, 上电后
      先有 2 秒回零斜坡, 第一个 50ms 间隙就把影子线程结束了。原有单测全部连续喂帧, 没有
      间隙, 所以抓不到。
修法: 超时 = 醒来再看一眼停止标志, 不是退出; 循环条件改成看标志, Disconnected 才退出。
每处替换都 assert 命中, 写完读回复核。shadow.rs 是 LF。
"""
from pathlib import Path

P = Path(r"C:\duck_work\kissqy-firmware\robotd\src\shadow.rs")

src = P.read_bytes().decode("utf-8")
assert "\r" not in src, "shadow.rs 应为 LF"
orig = src


def replace_once(s, old, new, what):
    n = s.count(old)
    assert n == 1, f"{what}: 锚点应为 1 处, 实际 {n}"
    return s.replace(old, new)


# 1. 引入 RecvTimeoutError
src = replace_once(
    src,
    "use std::sync::mpsc::{Receiver, SyncSender, TrySendError, sync_channel};\n",
    "use std::sync::mpsc::{RecvTimeoutError, Receiver, SyncSender, TrySendError, sync_channel};\n",
    "mpsc 引入",
)

# 2. worker 循环: 超时不是退出
src = replace_once(
    src,
    "    // `recv_timeout` rather than `recv`: the stop flag is checked on every wake, so the\n"
    "    // worker leaves on its own even while a cloned sender keeps the queue open.\n"
    "    while let Ok(mut frame) = rx.recv_timeout(WAKE) {\n"
    "        if !shared.enabled.load(Ordering::Relaxed) {\n"
    "            break;\n"
    "        }\n"
    "        // Act on the present, not the recent past: keep only the newest frame offered.\n",
    "    // `recv_timeout` rather than `recv`: the stop flag is checked on every wake, so the\n"
    "    // worker leaves on its own even while a cloned sender keeps the queue open.\n"
    "    //\n"
    "    // And a timeout is not an exit. The control thread offers a frame only on a tick where the\n"
    "    // driving policy produced a step, so there are gaps longer than `WAKE` between frames — the\n"
    "    // homing ramp before the first one, to start with. As `while let Ok(recv_timeout(..))` the\n"
    "    // worker took its first quiet `WAKE` for its last: a shadow that loaded, ran for 50 ms and\n"
    "    // stopped before it had seen anything. Every other test here feeds frames without a pause,\n"
    "    // which is why none of them noticed; the one that runs the real control loop did.\n"
    "    while shared.enabled.load(Ordering::Relaxed) {\n"
    "        let mut frame = match rx.recv_timeout(WAKE) {\n"
    "            Ok(frame) => frame,\n"
    "            Err(RecvTimeoutError::Timeout) => continue,\n"
    "            Err(RecvTimeoutError::Disconnected) => break,\n"
    "        };\n"
    "        // Act on the present, not the recent past: keep only the newest frame offered.\n",
    "worker 循环",
)

# 3. 回归测试: 帧与帧之间有间隙, 线程必须还活着
TEST = '''    /// **A gap in the frames is not the end.** The control thread offers a frame only on a tick
    /// where the driving policy produced a step, so there are quiet stretches longer than
    /// [`WAKE`] — the homing ramp before the first one, to start with. The worker used to take
    /// its first quiet `WAKE` for its last: a shadow that loaded, ran for 50 ms and stopped
    /// before it had seen anything. Every other test here feeds frames without a pause, which is
    /// why none of them noticed.
    #[test]
    fn the_worker_outlives_a_gap_in_frames() {
        let (mut shadow, calls) = start_with(ok([0.0; ACTION_LEN]));
        let gap = WAKE * 8;
        std::thread::sleep(gap);
        assert!(shadow.handle().is_active(), "the worker left during the gap");
        assert!(
            feed_until(&shadow, 10, |_| false),
            "nothing was inferred after the gap"
        );
        shadow.stop();
        let r = shadow.report();
        assert!(r.inferred >= 10, "{r:?}");
        assert!(calls.load(Ordering::SeqCst) >= 10);
        assert!(
            r.running_secs >= gap.as_secs_f64(),
            "the worker ran through the gap: {r:?}"
        );
    }

    // ---- Which candidates may be shadowed ----
'''
src = replace_once(
    src,
    "    // ---- Which candidates may be shadowed ----\n",
    TEST,
    "回归测试插入点",
)

assert src != orig
assert "\r" not in src
P.write_bytes(src.encode("utf-8"))

# 读回复核
chk = P.read_bytes().decode("utf-8")
assert "\r" not in chk
for probe in (
    "use std::sync::mpsc::{RecvTimeoutError, Receiver, SyncSender, TrySendError, sync_channel};\n",
    "    while shared.enabled.load(Ordering::Relaxed) {\n",
    "            Err(RecvTimeoutError::Timeout) => continue,\n",
    "            Err(RecvTimeoutError::Disconnected) => break,\n",
    "fn the_worker_outlives_a_gap_in_frames()",
):
    assert probe in chk, f"复核失败, 缺: {probe!r}"
assert "while let Ok(mut frame) = rx.recv_timeout(WAKE)" not in chk, "旧循环还在"
assert chk.count("fn the_worker_outlives_a_gap_in_frames()") == 1
print("shadow.rs 落盘 ✓  行数 %d -> %d" % (orig.count("\n"), chk.count("\n")))
