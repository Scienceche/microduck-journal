#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修 /opt/duck-calib/server.py 录制帧缺 IMU 字段(2026-11-26 真缺陷, 第二版锚点)。

第一版补丁锚点按记忆写, 与实际缩进不符被断言拦下(未改任何东西) —— 本版锚点逐字取自
鸭子上的真实代码(2026-11-26 grep 读回)。

根因: _sample_now() 返回全字段(gravity/fallen/policy/joint_ids/targets/velocities/
      joints_raw/loop), 环形缓冲 RING 里存的也是完整快照 —— 但 _grab_thread 存盘时
      把它手工剥成 {"t", "joints"}。所以 11-25 的补丁是"采到了没存上", 推倒会话
      运动2146.json 3000 帧全缺姿态字段, 回放不可用。
修法: 存盘帧(环形段+补录段)写完整快照, t 重算为相对时刻。
验证: 这次验最终产物 —— 打完后真录一段, 检查落盘 JSON 的帧字段。
"""
import shutil
import time
from pathlib import Path

P = Path("/opt/duck-calib/server.py")
src = P.read_text(encoding="utf-8")
orig = src


def replace_once(s, old, new, what):
    n = s.count(old)
    assert n == 1, f"{what}: 锚点应为 1 处, 实际 {n}"
    return s.replace(old, new)


# ---- 1. 环形段存盘帧: 全字段(锚点=2026-11-26 实读源码, 缩进8空格) ----
OLD_SLICE = '''        with _ring_lock:
            samples = [{"t": round(ts - cut, 3), "joints": js}
                       for ts, js in RING if ts >= cut]'''
NEW_SLICE = '''        with _ring_lock:
            # 2026-11-26: 存盘帧写完整快照(_sample_now() 的全部字段:
            # gravity/fallen/policy/joint_ids/... 此前被手工剥成 {"t","joints"},
            # 推倒会话的姿态字段全没进盘, 回放不可用)。t 重算为相对时刻。
            samples = []
            for ts, snap in RING:
                if ts < cut:
                    continue
                frame = dict(snap) if isinstance(snap, dict) else {"joints": snap}
                frame["t"] = round(ts - cut, 3)
                samples.append(frame)'''
src = replace_once(src, OLD_SLICE, NEW_SLICE, "存盘帧(环形段)")

# ---- 2. 补录段存盘帧: 全字段 ----
OLD_POST = '''            snap = _sample_now()
            if snap:
                samples.append({"t": round(fresh - cut, 3), "joints": snap})'''
NEW_POST = '''            snap = _sample_now()
            if snap:
                # 2026-11-26: 补录段帧同样写完整快照
                frame = dict(snap) if isinstance(snap, dict) else {"joints": snap}
                frame["t"] = round(fresh - cut, 3)
                samples.append(frame)'''
src = replace_once(src, OLD_POST, NEW_POST, "存盘帧(补录段)")

assert src != orig
BAK = P.with_suffix(".py.bak-fields-" + time.strftime("%Y%m%d-%H%M%S"))
shutil.copy2(P, BAK)
P.write_text(src, encoding="utf-8")

# ---- 读回复核 ----
chk = P.read_text(encoding="utf-8")
for probe in ("# 2026-11-26: 存盘帧写完整快照", "# 2026-11-26: 补录段帧同样写完整快照",
              "frame = dict(snap) if isinstance(snap, dict) else {\"joints\": snap}"):
    assert probe in chk, f"复核失败, 缺: {probe!r}"
assert '{"t": round(ts - cut, 3), "joints": js}' not in chk, "旧环形段构造还在"
assert 'samples.append({"t": round(fresh - cut, 3), "joints": snap})' not in chk, "旧补录段构造还在"
import py_compile
py_compile.compile(str(P), doraise=True)
print("server.py 补丁落盘 ✓  备份:", BAK.name)
print("存盘帧两处(环形段+补录段) → 完整快照")
