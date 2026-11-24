#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对打过阶段4补丁的采集代码做行为测试(不连鸭子)。

喂仿 robot.state 格式的假帧, 检查 _sample_now 的输出:
  - 关节顺序与 JOINT_IDS 一致, 弧度和原始值按同一下标对应
  - gravity / fallen / policy / velocities / targets 都取到
  - 帧不完整(关节数不是 15)时整帧丢弃, 不悄悄截断
  - velocities 缺失时为 None 而不是被当成全零
"""
import importlib.util
import json
import sys
import threading
import types

SRC = "C:/duck_work/ota/dryrun/server_fake.py"
spec = importlib.util.spec_from_file_location("rec_under_test", SRC)
m = importlib.util.module_from_spec(spec)
sys.modules["rec_under_test"] = m
spec.loader.exec_module(m)

IDS = [20, 21, 22, 23, 24, 30, 31, 32, 33, 34, 10, 11, 12, 13, 14]
ok = True


def check(name, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name, "" if cond else "   <- " + str(detail)))


def frame(joints=None, **kw):
    f = {"joints": [0.01 * i for i in range(15)] if joints is None else joints,
         "targets": [0.02 * i for i in range(15)],
         "velocities": [0.001 * i for i in range(15)],
         "safety": {"fallen": False, "gravity": [0.0, 0.0, -1.0]},
         "policy": "walk", "loop": {"hz": 50.0, "missed": 3}}
    f.update(kw)
    return f


def put(f):
    with m._state_lock:
        m._STATE["ts"] = 1234.5
        m._STATE["frame"] = f


print("1) 正常帧")
put(frame())
m.RAW_POS.clear(); m.RAW_POS.update({str(i): 2000 + k for k, i in enumerate(IDS)})
s = m._sample_now()
check("joint_ids 与固件 JOINT_IDS 一致", s["joint_ids"] == IDS, s["joint_ids"])
check("joints 15 个且逐位对应", s["joints"] == [round(0.01 * i, 5) for i in range(15)])
check("joints_raw[0] 是 ID20 的原始值", s["joints_raw"][0] == 2000 and s["joints_raw"][10] == 2010,
      s["joints_raw"][:3])
check("gravity 取到", s["gravity"] == [0.0, 0.0, -1.0])
check("fallen 取到", s["fallen"] is False)
check("policy 取到", s["policy"] == "walk")
check("velocities 15 个", s["velocities"] == [round(0.001 * i, 4) for i in range(15)])
check("loop 取到", s["loop"] == {"hz": 50.0, "missed": 3})

print("2) 倒地帧")
f = frame(); f["safety"] = {"fallen": True, "gravity": [0.3, -0.2, -0.93]}; f["policy"] = "stand_fallen"
put(f)
s = m._sample_now()
check("fallen=True", s["fallen"] is True)
check("policy=stand_fallen", s["policy"] == "stand_fallen")
check("重力向量非竖直", s["gravity"] == [0.3, -0.2, -0.93])

print("3) 不完整的帧")
put(frame(joints=[0.1] * 14))
check("关节数 14 → 整帧丢弃", m._sample_now() is None)
put(frame(velocities=[]))
s = m._sample_now()
check("velocities 缺失 → None(不是全零)", s is not None and s["velocities"] is None)
put(frame())
m._STATE["frame"]["safety"] = {"fallen": False, "gravity": [0.0, 0.0]}
check("gravity 只有 2 个分量 → None", m._sample_now()["gravity"] is None)

print("4) 流断了")
m._STATE["frame"] = None
check("无帧 → None", m._sample_now() is None)

print("5) 语法与关键结构")
import ast
ast.parse(open(SRC, encoding="utf-8").read())
check("打完补丁后语法正确", True)
src = open(SRC, encoding="utf-8").read()
check("订阅线程已挂", "target=_state_subscriber, daemon=True" in src)
check("旧采样路径(每帧轮询 busStatus 取关节)已移除",
      'rpc("robot.busStatus", timeout=4.0) or {}\n    joints' not in src)

print("\n结果: %s" % ("全部通过" if ok else "有失败"))
sys.exit(0 if ok else 1)
