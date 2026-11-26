#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集代码 _sample_now 的行为测试。用法: python test_recorder_imu_body.py <打过补丁的 server.py>

夹具原则(2026-11-25): 只加载被测文件本身, 不往它的命名空间里塞任何真实文件没有的东西。
真实文件顶层有 POSES 目录定义, 会执行 POSES / "motion", 所以这里原样加载即可。
"""
import importlib.util
import sys

SRC = sys.argv[1]
spec = importlib.util.spec_from_file_location("rec_under_test", SRC)
m = importlib.util.module_from_spec(spec)
sys.modules["rec_under_test"] = m
spec.loader.exec_module(m)

IDS = [20, 21, 22, 23, 24, 30, 31, 32, 33, 34, 10, 11, 12, 13, 14]
state = {"ok": True}


def check(name, cond, detail=""):
    state["ok"] = state["ok"] and bool(cond)
    print("    [%s] %s%s" % ("PASS" if cond else "FAIL", name, "" if cond else "   <- " + str(detail)))


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


print("  行为测试:")
put(frame())
m.RAW_POS.clear(); m.RAW_POS.update({str(i): 2000 + k for k, i in enumerate(IDS)})
s = m._sample_now()
check("joint_ids 与固件 JOINT_IDS 一致", s["joint_ids"] == IDS, s["joint_ids"])
check("joints 15 个且逐位对应", s["joints"] == [round(0.01 * i, 5) for i in range(15)])
check("joints_raw[0]=ID20, [10]=ID10 的原始值", s["joints_raw"][0] == 2000 and s["joints_raw"][10] == 2010)
check("gravity / fallen / policy / velocities / loop 取到",
      s["gravity"] == [0.0, 0.0, -1.0] and s["fallen"] is False and s["policy"] == "walk"
      and s["velocities"] == [round(0.001 * i, 4) for i in range(15)] and s["loop"] == {"hz": 50.0, "missed": 3})

f = frame(); f["safety"] = {"fallen": True, "gravity": [0.3, -0.2, -0.93]}; f["policy"] = "stand_fallen"
put(f); s = m._sample_now()
check("倒地帧: fallen/policy/重力", s["fallen"] is True and s["policy"] == "stand_fallen"
      and s["gravity"] == [0.3, -0.2, -0.93])

put(frame(joints=[0.1] * 14)); check("关节数 14 → 整帧丢弃", m._sample_now() is None)
put(frame(velocities=[])); s = m._sample_now()
check("velocities 缺失 → None(不是全零)", s is not None and s["velocities"] is None)
put(frame()); m._STATE["frame"]["safety"] = {"fallen": False, "gravity": [0.0, 0.0]}
check("gravity 2 个分量 → None", m._sample_now()["gravity"] is None)
m._STATE["frame"] = None; check("流断 → None", m._sample_now() is None)

# 订阅线程函数体引用的全局名是否都有定义。不实际连接, 只做静态核对。
# 内建名(isinstance/dict/str/Exception/ValueError...)本来就不需要导入, 要算作已定义
# (第一版漏了这一条, 把内建名误报成缺失)。
import builtins, dis
missing = set()
for fn in (m._state_subscriber, m._raw_pos_refresher):
    for ins in dis.get_instructions(fn):
        if ins.opname == "LOAD_GLOBAL":
            missing.add(ins.argval)
defined = set(dir(m)) | set(dir(builtins))
undefined = missing - defined
check("订阅/刷新线程引用的全局名都有定义", not undefined, undefined)
if not undefined:
    print("      (引用的全局名: %s)" % ", ".join(sorted(missing)))

print("    -- 行为测试: %s" % ("通过" if state["ok"] else "有失败"))
sys.exit(0 if state["ok"] else 1)
