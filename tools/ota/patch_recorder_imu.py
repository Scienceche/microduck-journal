#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段 4: 采集工具补 IMU 姿态 (2026-11-24)。

现状: _sample_now() 每帧轮询 robot.busStatus, 只取关节 position_raw。
问题: 没有躯干姿态。同一组关节角, 趴/仰/侧躺都成立, 无法重建倒地姿态,
      仿真回放 (阶段 7) 做不了。

改法: 采样源从轮询 busStatus 改为订阅 robot.state (与黑匣子 duck_blackbox.py
      同协议: 订阅是收 robotd 已有广播的拷贝, 不额外占用总线事务)。
每帧多存:
  gravity     躯干坐标系下的重力向量 (定俯仰与横滚; 定不了偏航, 对起身无影响)
  fallen      固件的倒地判定
  velocities  实测关节角速度 rad/s (按 JOINT_NAMES 顺序)
  policy      这一拍哪个网络在出力 (walk / stand / stand_fallen / held ...)
  targets     这一拍下发的关节目标 rad
关节角改存 rad (joints), 同时保留原始值 (joints_raw) 以兼容已有的读取脚本。

每条锚点都带断言; 任一对不上立即中止, 不写文件。
"""
import ast
import re
import shutil
import sys
from pathlib import Path

SRV = Path("/opt/duck-calib/server.py")
BAK = Path("/opt/duck-calib/server.py.bak-imu")

src = SRV.read_text(encoding="utf-8", newline="")
if "def _state_subscriber()" in src:
    # 采集段已换过。可能是上次补丁成功, 也可能是上次补丁后缺顶层 import(第一版补丁
    # 没处理 import time, 2026-11-25 上机后订阅线程 NameError)。这里只补 import, 不重做。
    fixed = False
    if not re.search(r"^import time\s*$", src, re.M):
        assert src.count("import threading\n") >= 1
        src = src.replace("import threading\n", "import threading\nimport time\n", 1)
        fixed = True
    if not re.search(r"^import re\s*$", src, re.M):
        src = "import re\n" + src
        fixed = True
    if not fixed:
        print("已打过且 import 齐全, 跳过")
        sys.exit(0)
    ast.parse(src)
    assert "\r" not in src
    SRV.write_text(src, encoding="utf-8", newline="\n")
    print("已打过; 本次只补了缺失的顶层 import")
    sys.exit(0)
for must in ("def _sample_now():", "def _ring_loop():", "def _grab_thread(name, pre, post):"):
    assert must in src, "缺少锚点 %s —— 鸭子上的 server.py 与已知版本不符, 中止" % must
assert "RING = deque(maxlen=int(RING_HZ * RING_SECONDS))" in src, "RING 定义不符"

if not BAK.exists():
    shutil.copy2(SRV, BAK)

# ---------------------------------------------------------------- 1) 采样源
a1 = '''def _sample_now():
    status = rpc("robot.busStatus", timeout=4.0) or {}
    joints = ((status.get("last_sample") or {}).get("servos")) or []
    if not joints:
        joints = (status.get("native") or {}).get("joints") or []
    if not joints:
        return None
    return {str(s["id"]): s.get("position_raw")
            for s in joints if s.get("position_raw") is not None}
'''
b1 = '''# ---- 2026-11-24 阶段4: 采样源改为订阅 robot.state ----
# 最新一帧 (到达时间, 帧内容)。订阅线程只写, 采集线程只读。
_STATE = {"ts": 0.0, "frame": None}
_state_lock = threading.Lock()
RAW_POS = {}      # 关节序号 -> position_raw, 由 busStatus 低频补充(订阅流里没有原始值)


def _state_subscriber():
    """订阅 robot.state @50Hz, 缓存最新帧。断线自动重连(2s 退避)。"""
    import socket as _sk
    req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "robot.subscribe",
                      "params": {"hz": 50}}) + "\\n"
    while True:
        try:
            s = _sk.socket(_sk.AF_UNIX, _sk.SOCK_STREAM)
            s.settimeout(3.0)
            s.connect("/run/robotd.sock")
            s.sendall(req.encode())
            for line in s.makefile("rb"):
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("method") != "robot.state" or not isinstance(msg.get("params"), dict):
                    continue
                with _state_lock:
                    _STATE["ts"] = time.time()
                    _STATE["frame"] = msg["params"]
        except Exception:
            time.sleep(2.0)


def _raw_pos_refresher():
    """订阅流里没有 position_raw; 用 1Hz 的 busStatus 补一份映射(校零比对用)。"""
    while True:
        try:
            status = rpc("robot.busStatus", timeout=4.0) or {}
            servos = ((status.get("last_sample") or {}).get("servos")) or []
            if not servos:
                servos = (status.get("native") or {}).get("joints") or []
            m = {str(s["id"]): s.get("position_raw") for s in servos
                 if s.get("position_raw") is not None}
            if m:
                RAW_POS.clear()
                RAW_POS.update(m)
        except Exception:
            pass
        time.sleep(1.0)


def _sample_now():
    """取最新一帧, 组成采集记录。没有新帧(流断了)返回 None。"""
    with _state_lock:
        frame = _STATE["frame"]
    if not frame:
        return None
    safety = frame.get("safety") or {}
    g = safety.get("gravity")
    # 关节向量在流里是按位置索引的, 顺序即 JOINT_IDS。长度不是 15 说明这一帧不完整,
    # 宁可整帧丢弃也不能悄悄截断(截断后的关节角会错位到别的关节上)。
    j = frame.get("joints") or []
    if len(j) != len(JOINT_IDS):
        return None
    def _vec(key, nd):
        v = frame.get(key) or []
        return [round(float(x), nd) for x in v] if len(v) == len(JOINT_IDS) else None
    return {
        "joint_ids": list(JOINT_IDS),
        "joints": _vec("joints", 5),
        "targets": _vec("targets", 5),
        "velocities": _vec("velocities", 4),
        "gravity": [round(float(x), 4) for x in g] if isinstance(g, list) and len(g) == 3 else None,
        "fallen": bool(safety.get("fallen")),
        "policy": frame.get("policy"),
        "joints_raw": [RAW_POS.get(str(i)) for i in JOINT_IDS],
        "loop": {"hz": (frame.get("loop") or {}).get("hz"),
                 "missed": (frame.get("loop") or {}).get("missed")},
    }
'''
assert src.count(a1) == 1, "_sample_now 原文不符 (出现 %d 次), 中止" % src.count(a1)
src = src.replace(a1, b1, 1)

# JOINT_IDS 常量: 照抄固件 duck-control/src/model.rs 的 pub const JOINT_IDS。
# 曾按猜测写成 [右腿, 左腿, 头], 实际是 [左腿, 头, 右腿] —— 顺序错了弧度会错位到别的关节。
a1b = "RING = deque(maxlen=int(RING_HZ * RING_SECONDS))"
b1b = ('''RING = deque(maxlen=int(RING_HZ * RING_SECONDS))
# 关节顺序 = 固件 JOINT_IDS (duck-control/src/model.rs): 左腿 / 颈头嘴 / 右腿。
# robot.state 的 joints/targets/velocities 都按这个顺序按位置索引。
JOINT_IDS = (20, 21, 22, 23, 24, 30, 31, 32, 33, 34, 10, 11, 12, 13, 14)''')
assert src.count(a1b) == 1, "RING 定义不符, 中止"
src = src.replace(a1b, b1b, 1)

# ---------------------------------------------------------------- 2) 环形缓冲
# 原先每帧都 append(没有去重): 采样率 25Hz 而订阅 50Hz 时会存下重复帧。
a2 = '''            snap = _sample_now()
            if snap:
                with _ring_lock:
                    RING.append((_t.time(), snap))'''
b2 = '''            with _state_lock:
                fresh = _STATE["ts"]
            if fresh != _ring_loop._last_ts:
                _ring_loop._last_ts = fresh
                snap = _sample_now()
                if snap:
                    with _ring_lock:
                        RING.append((fresh, snap))'''
assert src.count(a2) == 1, "_ring_loop 采样段不符 (出现 %d 次), 中止" % src.count(a2)
src = src.replace(a2, b2, 1)

a3 = '''def _ring_loop():
    import time as _t'''
b3 = '''def _ring_loop():
    import time as _t
    _ring_loop._last_ts = None'''
assert src.count(a3) == 1, "_ring_loop 定义不符, 中止"
src = src.replace(a3, b3, 1)

# 补录段也是每个循环采一次, 同样受去重约束
a4 = '''        while _t.time() < t_end:
            snap = _sample_now()
            if snap:
                samples.append({"t": round(_t.time() - cut, 3), "joints": snap})'''
b4 = '''        last = None
        while _t.time() < t_end:
            with _state_lock:
                fresh = _STATE["ts"]
            if fresh == last:
                _t.sleep(0.004)
                continue
            last = fresh
            snap = _sample_now()
            if snap:
                samples.append({"t": round(fresh - cut, 3), "joints": snap})'''
assert src.count(a4) == 1, "补录段不符 (出现 %d 次), 中止" % src.count(a4)
src = src.replace(a4, b4, 1)

# ---------------------------------------------------------------- 3) 启动线程
a5 = "    threading.Thread(target=_ring_loop, daemon=True).start()"
b5 = ("    threading.Thread(target=_state_subscriber, daemon=True).start()\n"
      "    threading.Thread(target=_raw_pos_refresher, daemon=True).start()\n"
      "    threading.Thread(target=_ring_loop, daemon=True).start()")
assert src.count(a5) == 1, "线程启动锚点不符, 中止"
src = src.replace(a5, b5, 1)

ast.parse(src)

# ---------------------------------------------------------------- 4) 顶层 import
# 新代码在模块级用 time(订阅线程的时间戳与重连退避)。原文件没有顶层 import time
# —— 原采集代码都是在函数内局部 `import time as _t`。缺了它订阅线程一启动就
# NameError(2026-11-25 上机时发生: 我的测试夹具自己多加了 import time, 没抓到)。
if not re.search(r"^import time\s*$", src, re.M):
    a_imp = "import threading\n"
    assert src.count(a_imp) >= 1, "找不到 import threading 锚点"
    src = src.replace(a_imp, "import threading\nimport time\n", 1)
    print("已补: 顶层 import time")
if not re.search(r"^import re\s*$", src, re.M):
    src = "import re\n" + src
    print("已补: 顶层 import re")

ast.parse(src)
# 换行符: 显式按 LF 写。不指定 newline 的话 Windows 上会把 \n 写成 \r\n,
# 整个文件每一行都会变成"有改动"(2026-11-25 干跑时发现, 342 行全部被改了换行符)。
# 鸭子上的文件是 LF, 补丁必须只改它该改的行。
assert "\r" not in src, "补丁结果里出现了 CR, 中止"
SRV.write_text(src, encoding="utf-8", newline="\n")

# ---------------------------------------------------------------- 落盘复核
chk = SRV.read_bytes()
assert b"\r" not in chk, "落盘后出现了 CR, 换行符被改了"
chk = chk.decode("utf-8")
ast.parse(chk)
for probe in ("def _state_subscriber():", '"gravity": [round(float(x), 4)', '"fallen": bool(',
              '"policy": frame.get("policy")', "target=_state_subscriber, daemon=True"):
    assert probe in chk, "复核失败, 缺: %s" % probe
assert 'rpc("robot.busStatus", timeout=4.0) or {}\n    joints' not in chk, "旧采样路径仍在"
print("阶段4 补丁落盘 ✓ 备份: %s" % BAK)
