#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修 shadow.rs 里 submit_never_waits_for_a_full_queue 测试的等待条件(2026-11-26)。

该测试的意图: 让 worker 退出但保留发送端(队列永远没人排空), 再测 submit 每次都立刻返回、
且没有任何推理发生。注释写"等 worker 自己退出", 但等待条件查的是 enabled 标志 —— 标志刚被
置 false, 循环立刻结束, 实际上没等。旧 worker 之所以真的退出了, 靠的是"recv 超时即退出"
那个 bug(同日另一补丁修掉)。worker 修好后, 测试把标志重新置 true 时 worker 还活着, 于是
推理了一次, 断言 "nothing was ever inferred" 失败。

修法: 等待条件改成 worker 线程真正结束(JoinHandle::is_finished)。断言一条不动。
每处替换都 assert 命中, 写完读回复核。shadow.rs 是 LF。
"""
from pathlib import Path

P = Path(r"C:\duck_work\kissqy-firmware\robotd\src\shadow.rs")

src = P.read_bytes().decode("utf-8")
assert "\r" not in src, "shadow.rs 应为 LF"
orig = src

OLD = (
    "        let (shadow, calls) = start_with(ok([0.0; ACTION_LEN]));\n"
    "        // Stop the worker without dropping the sender, so nothing ever drains the queue.\n"
    "        shadow.shared.enabled.store(false, Ordering::Relaxed);\n"
    "        let worker = shadow.worker.as_ref();\n"
    "        if let Some(w) = worker {\n"
    "            // The worker is parked in recv_timeout and will exit on its own; wait for that.\n"
    "            let deadline = Instant::now() + Duration::from_secs(5);\n"
    "            while shadow.shared.enabled.load(Ordering::Relaxed) && Instant::now() < deadline {\n"
    "                std::thread::sleep(Duration::from_millis(5));\n"
    "            }\n"
    "            let _ = w;\n"
    "        }\n"
)
NEW = (
    "        let (shadow, calls) = start_with(ok([0.0; ACTION_LEN]));\n"
    "        // Stop the worker without dropping the sender, so nothing ever drains the queue.\n"
    "        shadow.shared.enabled.store(false, Ordering::Relaxed);\n"
    "        // The worker is parked in `recv_timeout`: it wakes within `WAKE`, reads the flag and\n"
    "        // leaves. Wait until it actually has. It has to be waited on and not assumed, because\n"
    "        // the flag it reads is the very flag the handle is re-enabled through below — a worker\n"
    "        // still parked would wake to find it set again and carry on. (This used to wait on the\n"
    "        // flag, which was already clear, so it waited for nothing; the worker was in fact\n"
    "        // leaving on its first receive timeout, which is not what a timeout is for.)\n"
    "        let deadline = Instant::now() + Duration::from_secs(5);\n"
    "        while !shadow.worker.as_ref().is_some_and(|w| w.is_finished()) && Instant::now() < deadline {\n"
    "            std::thread::sleep(Duration::from_millis(5));\n"
    "        }\n"
    "        assert!(\n"
    "            shadow.worker.as_ref().is_some_and(|w| w.is_finished()),\n"
    "            \"the worker did not leave on the stop flag\"\n"
    "        );\n"
)
n = src.count(OLD)
assert n == 1, f"测试锚点应为 1 处, 实际 {n}"
src = src.replace(OLD, NEW)

assert src != orig
assert "\r" not in src
P.write_bytes(src.encode("utf-8"))

chk = P.read_bytes().decode("utf-8")
assert "\r" not in chk
for probe in (
    "while !shadow.worker.as_ref().is_some_and(|w| w.is_finished()) && Instant::now() < deadline {\n",
    "            \"the worker did not leave on the stop flag\"\n",
    "        assert_eq!(calls.load(Ordering::SeqCst), 0, \"nothing was ever inferred\");\n",
):
    assert probe in chk, f"复核失败, 缺: {probe!r}"
assert "while shadow.shared.enabled.load(Ordering::Relaxed) && Instant::now() < deadline {" not in chk
print("shadow.rs 落盘 ✓  行数 %d -> %d" % (orig.count("\n"), chk.count("\n")))
