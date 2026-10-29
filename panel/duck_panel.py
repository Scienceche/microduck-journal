#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Microduck 中控面板 v3 — 统一面板（单服务 / 单端口 8080）。

v2 的全部功能（IMU 姿态 / 舵机分组控制+姿态库 / 总线诊断 / 固件烧录 / 系统操作）
之上，把 IMU 仪表台（原 imu-scope 独立服务 :8081）整个并进来：

    /               统一 SPA：姿态总览 / 仪表 / 舵机+姿态库 / 总线 / 工具
    /scope/*        仪表台前端（原样复用 /root/imu-scope/web，零修改）
    /ws             仪表台 WebSocket（前端写死 /ws，因此挂在根路径）
    /api/ports      仪表台的串口枚举
    /api/...        v2 全部 API，另加 /api/scope_defaults（自动连接参数）

总线仲裁：仪表台 Session 连接时独占总线，面板的 FeetechBus 立即让位并拒绝新
操作；仪表断开后面板自动恢复。前端在切走「仪表」页或页面退后台时会自动向 /ws
发 disconnect 释放总线，所以平时舵机操作不受影响。

依赖：python3-aiohttp（imu-scope 部署时已装）。前端零 CDN，离线可用。
"""
import asyncio
import importlib.util
import json
import math
import os
import platform
import re
import struct
import subprocess
import threading
import time

from aiohttp import web

import feetech

PORT = 8080
BASE = os.path.dirname(os.path.abspath(__file__))
POSE_DIR = os.path.join(BASE, "poses")
BUS_PORT = os.environ.get("DUCK_BUS", "/dev/ttyS2")
BUS_BAUD = int(os.environ.get("DUCK_BAUD", "1000000"))
SCOPE_DIR = os.environ.get("DUCK_SCOPE_DIR", "/root/imu-scope")
IMU_ID = 200
IMU_BLOCK_ADDR = 124
ELF_PATH = "/root/imu_fw/build/imu_to_dxl.elf"
GYRO_FS_DPS = 500.0
# 官方关节分组（与 duck-control 约定一致）
GROUPS = [("右腿", [10, 11, 12, 13, 14]), ("左腿", [20, 21, 22, 23, 24]), ("头颈", [30, 31, 32, 33, 34])]
JOINT_NAMES = {
    10: "r_hip_yaw", 11: "r_hip_roll", 12: "r_hip_pitch", 13: "r_knee", 14: "r_ankle",
    20: "l_hip_yaw", 21: "l_hip_roll", 22: "l_hip_pitch", 23: "l_knee", 24: "l_ankle",
    # 官方 ID 约定(站.json + MuJoCo alpha 模型):30 脖子俯仰 31 头俯仰
    # 32 头偏航 33 头侧倾 34 下巴(嘴)
    30: "neck_pitch", 31: "head_pitch", 32: "head_yaw", 33: "head_roll", 34: "mouth",
}

# ---------------- 仪表台模块（复用部署版，含 md1/串口枚举补丁） ----------------

_spec = importlib.util.spec_from_file_location(
    "scope_server", os.path.join(SCOPE_DIR, "server.py"))
scope = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scope)
SESSION = scope.SESSION

# ---------------- 总线仲裁：面板 FeetechBus vs 仪表台 Session ----------------

_bus = None
_bus_err = None
_bus_lock = threading.Lock()
_bus_last_used = 0.0
#: 空闲多久后释放串口（保留：给外部工具/robotd 让路的能力）
BUS_IDLE_RELEASE_S = 4.0
_scope_hold = threading.Event()
_robotd_cache = (0.0, None)

os.makedirs(POSE_DIR, exist_ok=True)


def _panel_bus_close():
    """立刻关掉面板的串口（仪表台要接管时调用）。"""
    global _bus
    with _bus_lock:
        if _bus is not None:
            try:
                _bus.close()
            except Exception:
                pass
            _bus = None
            print("[arbiter] 面板总线已让位给仪表台", flush=True)


_orig_connect = SESSION.connect
_orig_disconnect = SESSION.disconnect


_scope_gate = {"last_rel": 0.0}


def _scope_connect(*a, **k):
    # 防抖:释放后 3 秒内拒绝重连,防仪表页自动重连抽打串口(曾把面板打到 92%CPU 卡死)
    if time.time() - _scope_gate["last_rel"] < 3.0:
        raise RuntimeError("scope 重连过快(防抖 3s)")
    _panel_bus_close()
    _scope_hold.set()
    try:
        r = _orig_connect(*a, **k)
    except Exception:
        _scope_hold.clear()
        raise
    # 原版 connect() 第一行会调 self.disconnect()（已被包装），把刚置的
    # hold 清掉 —— 连接成功后必须重新置位，否则面板会同时抢串口。
    _scope_hold.set()
    print("[arbiter] 仪表台已接管总线", flush=True)
    return r


def _scope_disconnect():
    try:
        return _orig_disconnect()
    finally:
        _scope_hold.clear()
        _scope_gate["last_rel"] = time.time()
        print("[arbiter] 仪表台已释放总线，面板恢复", flush=True)


SESSION.connect = _scope_connect
SESSION.disconnect = _scope_disconnect


# ---------------- 行走策略运行时（duck_policy 懒加载） ----------------
POLICY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "policy.onnx")
_runner = None


def _get_runner():
    """懒加载策略运行时；onnxruntime 未装或模型缺失时返回 (None, 错误说明)。"""
    global _runner
    if _runner is not None:
        return _runner, None
    try:
        import duck_policy
    except ImportError as e:
        return None, f"duck_policy 导入失败: {e}"
    if not os.path.exists(POLICY_PATH):
        return None, f"模型不存在: {POLICY_PATH}"
    _runner = duck_policy.PolicyRunner(get_bus, POLICY_PATH)
    return _runner, None


def _walk(op, b):
    if op == "status":
        loaded = _runner is not None
        if _runner is None and os.path.exists(POLICY_PATH):
            return {"ok": True, "running": False, "loaded": True, "armed": True,
                    "policy": POLICY_PATH,
                    "note": "模型就绪未加载（点启动时加载）"}
        if _runner is None:
            return {"ok": True, "running": False, "loaded": False,
                    "policy": POLICY_PATH}
        st = dict(_runner.status)
        st["loaded"] = loaded
        st["policy"] = POLICY_PATH
        return {"ok": True, **st}
    if op == "start":
        r, err = _get_runner()
        if r is None:
            return {"ok": False, "err": err}
        return r.start()
    if op == "stop":
        r, _ = _get_runner()
        if r is not None:
            return r.stop()
        return {"ok": True, "note": "not running"}
    if op == "cmd":
        r, err = _get_runner()
        if r is None:
            return {"ok": False, "err": err}
        r.set_cmd(vx=b.get("vx"), vy=b.get("vy"), wz=b.get("wz"))
        return {"ok": True}
    if op == "home":
        bus = get_bus()
        if bus is None:
            return {"ok": False, "err": _bus_err}
        import duck_policy
        return duck_policy.go_home(bus, speed=int(b.get("speed", 100)))
    if op == "imu":
        bus = get_bus()
        if bus is None:
            return {"ok": False, "err": _bus_err}
        import duck_policy
        return {"ok": True, **duck_policy.imu_raw(bus)}
    if op == "probe":
        bus = get_bus()
        if bus is None:
            return {"ok": False, "err": _bus_err}
        import duck_policy
        return duck_policy.probe_joint(bus, int(b.get("idx", 0)),
                                       float(b.get("delta", 0.2)))
    return {"ok": False, "err": f"unknown op {op}"}


def get_bus():
    global _bus, _bus_err, _bus_last_used
    with _bus_lock:
        if _scope_hold.is_set():
            _bus_err = "IMU仪表占用总线（切走仪表页自动释放）"
            return None
        if _bus is not None and time.time() - _bus_last_used > BUS_IDLE_RELEASE_S:
            try:
                _bus.close()
            except Exception:
                pass
            _bus = None
        if _bus is None:
            try:
                _bus = feetech.FeetechBus(BUS_PORT, baud=BUS_BAUD)
                _bus_err = None
            except Exception as e:
                _bus_err = str(e)
        _bus_last_used = time.time()
        return _bus


def _bus_reaper():
    """空闲回收线程（v2 保留行为）。"""
    global _bus, _bus_last_used
    while True:
        time.sleep(2.0)
        with _bus_lock:
            if _bus is not None and time.time() - _bus_last_used > BUS_IDLE_RELEASE_S:
                try:
                    _bus.close()
                except Exception:
                    pass
                _bus = None


threading.Thread(target=_bus_reaper, daemon=True).start()


def bus_status():
    get_bus()
    if _bus is None:
        return {"open": False, "error": _bus_err, "port": BUS_PORT,
                "scope_holds": _scope_hold.is_set()}
    return {"open": True, "port": BUS_PORT, "stats": dict(_bus.stats),
            "scope_holds": _scope_hold.is_set()}


# ---------------- IMU ----------------

def _qmul(a, b):
    x1, y1, z1, w1 = a
    x2, y2, z2, w2 = b
    return [w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2]


def _qinv(q):
    return [-q[0], -q[1], -q[2], q[3]]


# IMU 标零参考(q0,板端重启保留;yaw 无磁力计是相对参考,漂移 ~20°/min 需勤标)
IMU_REF_FILE = os.path.join(BASE, "imu_ref.json")
_imu_ref = None
_imu_acc0 = None
try:
    with open(IMU_REF_FILE) as _f:
        _d = json.load(_f)
        _imu_ref = _d.get("q0")
        _imu_acc0 = _d.get("acc0")
except Exception:
    pass

# 1g 对应的原始计数(±2g 量程 16bit,实测标定 ~8174)
G_COUNTS = 8174.0


def _accel_tilt(ax, ay):
    """加速度计重力倾角(度),对 yaw 漂移免疫——竖装板子的可靠倾角。
    返回 None(未标零)或 [前倾°, 左倾°](线性近似,±20° 内误差 <6%)。"""
    if not _imu_acc0:
        return None
    k = 57.2958 / G_COUNTS
    return [round((ax - _imu_acc0[0]) * k, 1), round((ay - _imu_acc0[1]) * k, 1)]


def read_imu():
    if _runner is not None and _runner.status.get("running"):
        return {"ok": False, "err": "策略运行中（总线独占），停止策略后恢复"}
    bus = get_bus()
    if bus is None:
        return {"ok": False, "err": _bus_err}
    try:
        _, p = bus.read(IMU_ID, IMU_BLOCK_ADDR, 20)
    except feetech.BusError as e:
        return {"ok": False, "err": str(e)}
    gx, gy, gz = struct.unpack_from("<3h", p, 0)
    qx, qy, qz = struct.unpack_from("<3e", p, 6)
    sample_count, = struct.unpack_from("<H", p, 12)
    status = p[14]
    ax, ay = struct.unpack_from("<2h", p, 16)
    w = math.sqrt(max(0.0, 1.0 - (qx * qx + qy * qy + qz * qz)))
    mod = math.sqrt(qx * qx + qy * qy + qz * qz + w * w)
    quat_raw = [qx, qy, qz, w]
    quat = quat_raw
    if _imu_ref:
        quat = _qmul(_qinv(_imu_ref), quat_raw)
    qx, qy, qz, w = quat
    roll = math.degrees(math.atan2(2 * (w * qx + qy * qz), 1 - 2 * (qx * qx + qy * qy)))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, 2 * (w * qy - qz * qx)))))
    yaw = math.degrees(math.atan2(2 * (w * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz)))
    k = GYRO_FS_DPS / 32768.0
    # 复位诊断（固件 reg144=原因 bit0 IWDG,145=看门狗复位计数,146=测试,147-149=构建日期）
    diag = None
    try:
        _, iv = bus.read(IMU_ID, 0, 2)
        _, dp = bus.read(IMU_ID, 144, 6)
        diag = {"flags": dp[0], "wdt_resets": dp[1], "test": dp[2],
                "build": f"20{dp[3]:02d}-{dp[4]:02d}-{dp[5]:02d}",
                "fw_ver": f"{iv[0]}.{iv[1]}"}
    except Exception:
        pass  # 旧固件无此寄存器
    return {
        "ok": True, "id": IMU_ID,
        "whoami_ok": bool(status & 1), "sflp_live": bool(status & 2),
        "status_raw": status, "sample_count": sample_count,
        "quat": [round(c, 4) for c in quat],
        "quat_raw": [round(c, 4) for c in quat_raw],
        "zeroed": bool(_imu_ref),
        "mod": round(mod, 4),
        "euler": [round(roll, 1), round(pitch, 1), round(yaw, 1)],
        "gyro_dps": [round(gx * k, 1), round(gy * k, 1), round(gz * k, 1)],
        "accel_raw": [ax, ay],
        "tilt": _accel_tilt(ax, ay),
        "diag": diag,
    }


def imu_wdt_test():
    """看门狗在线自证：写 0xA5 → 固件停喂 → IWDG 应在 ~1.5s 复位 → 读回诊断。"""
    bus = get_bus()
    if bus is None:
        return {"ok": False, "err": _bus_err}
    try:
        _, p0 = bus.read(IMU_ID, 144, 3)
        before = {"flags": p0[0], "wdt_resets": p0[1]}
    except feetech.BusError as e:
        return {"ok": False, "err": f"读取失败: {e}"}
    try:
        bus.write(IMU_ID, 146, b"\xA5")
    except feetech.BusError as e:
        return {"ok": False, "err": f"写入测试寄存器失败: {e}", "before": before}
    time.sleep(3.0)
    for attempt in range(3):
        try:
            _, p1 = bus.read(IMU_ID, 144, 3)
            after = {"flags": p1[0], "wdt_resets": p1[1]}
            iwdg = bool(p1[0] & 1)
            return {"ok": iwdg and p1[1] > before["wdt_resets"], "before": before,
                    "after": after, "iwdg_reset": iwdg,
                    "note": "IWDG 复位发生" if iwdg else "板子没复位——看门狗未生效"}
        except feetech.BusError:
            time.sleep(1.0)
    return {"ok": False, "err": "复位后读回失败", "before": before}


async def h_imu_zero(request):
    return web.json_response({"zeroed": bool(_imu_ref), "q0": _imu_ref})


async def h_imu_zero_post(request):
    """IMU 标零:捕获当前姿态(1s 四元数均值)为参考;clear 清除。"""
    global _imu_ref, _imu_acc0
    try:
        b = await request.json()
    except Exception:
        b = {}
    op = b.get("op")
    if op == "clear":
        _imu_ref = None
        _imu_acc0 = None
        try:
            os.remove(IMU_REF_FILE)
        except OSError:
            pass
        return web.json_response({"ok": True, "zeroed": False})
    if op == "set":
        qs = []
        t_end = time.time() + 1.0
        while time.time() < t_end and len(qs) < 25:
            r = read_imu()
            if r.get("ok"):
                qs.append(r["quat_raw"])
            await asyncio.sleep(0.04)
        if not qs:
            return web.json_response({"ok": False, "err": "读不到 IMU"})
        q0 = qs[0][:]
        acc = [0.0, 0.0, 0.0, 0.0]
        for q in qs:
            if sum(a * b for a, b in zip(q, q0)) < 0:
                q = [-c for c in q]
            for i in range(4):
                acc[i] += q[i]
        n = math.sqrt(sum(c * c for c in acc)) or 1.0
        _imu_ref = [round(c / n, 6) for c in acc]
        ax0 = ay0 = 0
        cn = 0
        for r in ():
            pass
        # 采加速度基线
        aa = []
        t_end = time.time() + 0.6
        while time.time() < t_end and len(aa) < 12:
            r2 = read_imu()
            if r2.get("ok"):
                aa.append(r2["accel_raw"])
            await asyncio.sleep(0.04)
        if aa:
            _imu_acc0 = [round(sum(v[0] for v in aa) / len(aa)),
                         round(sum(v[1] for v in aa) / len(aa))]
        try:
            with open(IMU_REF_FILE, "w") as f:
                json.dump({"q0": _imu_ref, "acc0": _imu_acc0,
                           "time": time.strftime("%F %T")}, f)
        except OSError:
            pass
        return web.json_response({"ok": True, "zeroed": True, "q0": _imu_ref,
                                  "acc0": _imu_acc0, "samples": len(qs)})


def imu_identity():
    bus = get_bus()
    if bus is None:
        return {"ok": False, "err": _bus_err}
    try:
        _, p = bus.read(IMU_ID, 0, 9)
    except feetech.BusError as e:
        return {"ok": False, "err": str(e)}
    return {"ok": True, "fw": f"{p[0]}.{p[1]}", "id": p[5], "baud_code": p[6], "level": p[8]}


# ---------------- 系统 ----------------

def read_system():
    global _robotd_cache
    now = time.time()
    if now - _robotd_cache[0] > 3:
        try:
            r = subprocess.run(["systemctl", "is-active", "robotd"], capture_output=True, timeout=5)
            active = r.stdout.decode().strip() == "active"
        except Exception:
            active = None
        _robotd_cache = (now, active)

    def rf(path, scale=1.0):
        try:
            with open(path) as f:
                return float(f.read().split()[0]) * scale
        except Exception:
            return None

    mem = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith(("MemTotal:", "MemAvailable:")):
                    k, v = line.split(":")
                    mem[k.strip()] = int(v.split()[0]) // 1024
    except Exception:
        pass
    disk_free = None
    try:
        s = os.statvfs("/")
        disk_free = s.f_bavail * s.f_frsize // (1024 * 1024)
    except Exception:
        pass
    ip = None
    try:
        r = subprocess.run(["hostname", "-I"], capture_output=True, timeout=5)
        ip = r.stdout.decode().split()[0] if r.stdout else None
    except Exception:
        pass
    return {
        "host": platform.node(), "ip": ip, "uptime_s": int(rf("/proc/uptime") or 0),
        "load1": round(rf("/proc/loadavg") or 0, 2),
        "temp_c": round(t, 1) if (t := rf("/sys/class/thermal/thermal_zone0/temp", 0.001)) is not None else None,
        "mem_total_mb": mem.get("MemTotal"), "mem_avail_mb": mem.get("MemAvailable"),
        "disk_free_mb": disk_free, "robotd": _robotd_cache[1],
    }


# ---------------- 舵机 ----------------

def scan_ids(lo, hi, extra=(IMU_ID,)):
    bus = get_bus()
    if bus is None:
        raise RuntimeError(_bus_err)
    found = []
    for sid in list(range(lo, hi + 1)) + list(extra):
        if bus.ping(sid) is not None and sid not in found:
            found.append(sid)
    return found


# ---- 速度单位标定 + 速度换算 + 回读重发(参考 servo-web) ----
_speed_cfg = {"unit": 50, "done": False}


def _speed_unit(bus):
    if not _speed_cfg["done"]:
        try:
            ph = bus.read_u8(10, 18)
            _speed_cfg["unit"] = 1 if (ph & 0x04) else 50
            print("[calib] 相位=%#x → SPEED_UNIT=%d 步/格" % (ph, _speed_cfg["unit"]), flush=True)
        except Exception:
            _speed_cfg["unit"] = 50
        _speed_cfg["done"] = True
    return _speed_cfg["unit"]


def _vreg(steps_per_s, bus):
    u = _speed_unit(bus)
    return max(3, min(32767, int(steps_per_s * 1.2 / u)))


def _verify_resend(bus, want, rounds=4):
    """读回 42 号目标,偏差>5 counts 单播重发;返回修复数。"""
    fixed = 0
    for _ in range(rounds):
        bad = []
        for sid, pos in want.items():
            got = bus.read_goal(sid)
            if got is None or abs(got - pos) > 5:
                bad.append((sid, pos))
        if not bad:
            return fixed
        for sid, pos in bad:
            try:
                bus.write_u16(sid, 42, pos)
                fixed += 1
            except feetech.BusError:
                pass
    return fixed


MOVE_ACC = int(os.environ.get("DUCK_ACC", "25"))  # 8.7°/s²/count × 25 ≈ 218°/s²


def servo_action(op, ids, value=None, speed=None, acc=None, **kw):
    if _runner is not None and _runner.status.get("running"):
        return [{"ok": False, "err": "策略运行中（总线独占）"} for _ in (ids or [0])]
    bus = get_bus()
    if bus is None:
        raise RuntimeError(_bus_err)
    out = {}
    if op == "states":
        raw = bus.sync_read(ids, 56, 15)
        for sid in ids:
            v = raw.get(sid)
            st = feetech.FeetechBus._decode_state(*v) if v else None
            if st:
                st["joint"] = JOINT_NAMES.get(sid, "")
            out[sid] = st
        return out
    if op == "calpos":
        # 零位校准用:原始位置(56) + 状态字(65),重试 2 次
        for sid in ids:
            pos = st = None
            for _ in range(3):
                try:
                    pos = bus.read_u16(sid, 56)
                    st = bus.read_u8(sid, 65)
                    break
                except feetech.BusError:
                    pos = None
            out[sid] = {"pos": pos, "status": st,
                        "joint": JOINT_NAMES.get(sid, "")} if pos is not None else None
        return out
    if op == "cal":
        # 中位校准:扭矩关 → 0x0B(当前位置→2048) → 回读位置
        for sid in ids:
            try:
                bus.write_u8(sid, 40, 0)
                err = bus.calibrate_to(sid)
                pos = bus.read_u16(sid, 56)
                out[sid] = {"cal": "ok" if err is not None else "noresp",
                            "pos": pos}
            except feetech.BusError as e:
                out[sid] = {"cal": str(e)}
        return out
    for sid in ids:
        try:
            if op == "torque":
                bus.write_u8(sid, 40, 1 if value else 0)
            elif op == "move":
                pos = max(0, min(4095, int(value)))
                sp = max(0, min(3400, int(speed)) if speed is not None else 300)
                acc = max(0, min(255, int(acc if acc is not None else MOVE_ACC)))
                try:
                    bus.move_to(sid, pos, sp, acc)
                    out[sid] = "ok"
                except feetech.BusError as e:
                    out[sid] = str(e)
                continue
            elif op == "speed":
                bus.write_u16(sid, 46, max(0, min(3400, int(value))))
            elif op == "reboot":
                bus.reboot(sid)
            else:
                raise ValueError(f"unknown op {op}")
            out[sid] = "ok"
        except feetech.BusError as e:
            out[sid] = str(e)
    return out


def estop(ids):
    bus = get_bus()
    if bus is None:
        raise RuntimeError(_bus_err)
    if ids:
        bus.sync_write(40, 1, [(sid, b"\x00") for sid in ids])


# ---------------- 姿态库（兼容 servo-web poses 格式） ----------------

def poses_list():
    out = []
    for fn in sorted(os.listdir(POSE_DIR)):
        if fn.endswith(".json"):
            try:
                d = json.load(open(os.path.join(POSE_DIR, fn), encoding="utf-8"))
                out.append({"file": fn, "name": d.get("name", fn[:-5]),
                            "time": d.get("time", ""), "count": len(d.get("rows", []))})
            except Exception:
                pass
    return out


def pose_save(name, positions):
    name = re.sub(r'[\\/:*?"<>|]', "_", str(name).strip()) or "pose"
    rows = [{"id": int(i), "joint": JOINT_NAMES.get(int(i), ""), "pos": int(p), "deg": 0}
            for i, p in positions.items()]
    doc = {"name": name, "time": time.strftime("%Y-%m-%d %H:%M"), "rows": rows}
    fn = f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    json.dump(doc, open(os.path.join(POSE_DIR, fn), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return fn


def pose_apply(fname, speed=300, acc=None):
    path = os.path.join(POSE_DIR, os.path.basename(fname))
    if not os.path.isfile(path):
        raise FileNotFoundError(fname)
    doc = json.load(open(path, encoding="utf-8"))
    rows = [(int(r["id"]), int(r["pos"])) for r in doc.get("rows", [])]
    if rows:
        ids = [i for i, _ in rows]
        servo_action("torque", ids, True)
        # ── 主机端 min-jerk 轨迹流(25Hz sync_write)──
        # 每一瞬间全体关节严格等比 → 无"上关节先动"失配;亦即步态原语
        bus = get_bus()
        cur = servo_action("states", ids)
        cp = {i: (cur.get(i) or {}).get("pos", 2048) for i in ids}
        tgt = {sid: pos for sid, pos in rows}
        dmax = max(abs(tgt[s] - cp[s]) for s in ids) or 1
        dmax_deg = dmax * 0.0879
        v = max(20.0, float(speed))          # 速度滑杆 → 峰值 °/s
        T = max(0.4, 1.875 * dmax_deg / v)   # min-jerk 峰值速度=1.875·d/T
        # 舵机侧限速给足余量(峰值计数率×1.5),加速度适中
        v_reg = min(3400, int(v / 0.0879 / 50 * 1.5) + 40)
        accv = max(0, min(255, int(acc if acc is not None else MOVE_ACC)))
        bus.sync_write(46, 2, [(s, struct.pack("<H", v_reg)) for s in ids])
        bus.sync_write(41, 1, [(s, bytes([accv])) for s in ids])
        # 一次性限速(峰值×2余量)+适度加速度——逐帧改速度会"走停走"发抖,弃用
        v_reg = min(3400, int(v / 0.0879 / _speed_unit(bus) * 2.0) + 40)
        accv = max(0, min(255, int(acc if acc is not None else MOVE_ACC)))
        try:
            bus.sync_write(46, 2, [(s2, struct.pack("<H", v_reg)) for s2 in ids])
            bus.sync_write(41, 1, [(s2, bytes([accv])) for s2 in ids])
        except feetech.BusError:
            pass
        t0 = time.time()
        dt = 0.05
        frame_i = 0
        while True:
            tau = (time.time() - t0) / T
            if tau >= 1.0:
                break
            s10 = tau ** 3 * (10 - 15 * tau + 6 * tau * tau)
            frame = [(s2, int(round(cp[s2] + (tgt[s2] - cp[s2]) * s10))) for s2 in ids]
            try:
                if frame_i == 0:     # 首帧单播带应答,保证左右同时起步
                    for sid, p in frame:
                        try:
                            bus.write_u16(sid, 42, p)
                        except feetech.BusError:
                            pass
                else:
                    bus.sync_write(42, 2,
                                   [(s2, struct.pack("<H", p)) for s2, p in frame])
            except feetech.BusError:
                pass
            frame_i += 1
            time.sleep(dt)
        # 终值:回读重发(广播无应答,单播补发到位)
        fixed = _verify_resend(bus, tgt)
    return {"moved": len(rows), "verified": len(ids), "fixed": fixed}


def pose_delete(fname):
    path = os.path.join(POSE_DIR, os.path.basename(fname))
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


# ---------------- 烧录 ----------------

def flash_imu():
    if not os.path.exists(ELF_PATH):
        return {"ok": False, "log": f"找不到固件 {ELF_PATH}（先在主控上 make g031-dxl）"}
    try:
        usb = subprocess.run(["lsusb"], capture_output=True, timeout=5).stdout.decode()
        if "0483:" not in usb:
            return {"ok": False, "log": "没检测到 ST-Link（lsusb 里没有 0483: 设备）。\n" + usb}
    except Exception as e:
        return {"ok": False, "log": f"lsusb 失败: {e}"}
    r = subprocess.run(
        ["openocd", "-f", "interface/stlink.cfg", "-f", "target/stm32g0x.cfg",
         "-c", f"program {ELF_PATH} verify reset exit"],
        capture_output=True, timeout=90)
    log = (r.stdout + r.stderr).decode("utf-8", "replace")
    return {"ok": r.returncode == 0 and "verified" in log.lower(), "log": log[-4000:]}


# ---------------- TOF (VL53L5CX, HAT J5, i2c-gpio 总线10) ----------------
# TOF 用的是 I2C, 不占舵机总线(ttyS2), 可与面板其他功能并行。

_tof = {"proc": None, "task": None, "state": "idle", "frame": None,
        "err": "", "thresh": 200}


async def _tof_reader():
    proc = _tof["proc"]
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if "state" in obj:
            _tof["state"] = obj["state"]
        elif "z" in obj:
            _tof["state"] = "running"
            _tof["frame"] = obj
    if proc.returncode is None:
        try:
            proc.kill()
        except Exception:
            pass
    _tof["proc"] = None
    if _tof["state"] != "stopped":
        _tof["state"] = "idle"


async def h_tof(request):
    return web.json_response({
        "state": _tof["state"], "frame": _tof["frame"],
        "thresh": _tof["thresh"], "err": _tof["err"]})


async def h_tof_post(request):
    try:
        b = await request.json()
    except Exception:
        b = {}
    op = b.get("op")
    if op == "start":
        if _tof["proc"] and _tof["proc"].returncode is None:
            return web.json_response({"ok": True, "msg": "already running"})
        _tof["thresh"] = int(b.get("thresh", _tof["thresh"]))
        _tof["state"] = "loading"
        _tof["frame"] = None
        try:
            _tof["proc"] = await asyncio.create_subprocess_exec(
                "/root/tof_pkg/tof_stream", str(_tof["thresh"]),
                env={**os.environ, "TOF_BUS": "10"},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
        except Exception as e:
            _tof["state"] = "idle"
            _tof["err"] = str(e)
            return web.json_response({"ok": False, "err": str(e)})
        _tof["task"] = asyncio.create_task(_tof_reader())
        return web.json_response({"ok": True, "msg": "固件加载约10秒"})
    if op == "stop":
        if _tof["proc"] and _tof["proc"].returncode is None:
            try:
                _tof["proc"].terminate()
            except Exception:
                pass
        return web.json_response({"ok": True})
    return web.json_response({"ok": False, "err": f"unknown op {op}"})


# ---------------- 摄像头 (IMX219 → rkisp mainpath, /dev/video0 MPLANE) ----------------
# ffmpeg 直接开 video0 会 VIDIOC_G_INPUT 报错(不认 mplane)，必须 v4l2-ctl 抓
# NV12 原始帧再转 JPEG。系统没跑 rkaiq 3A → 出厂默认曝光在室内严重欠曝，
# 曝光/增益手动控(v4l2-ctl set-ctrl)，面板提供一键自动曝光。

CAM_DEV = "/dev/video0"
CAM_JPG = os.path.join(BASE, "cam_last.jpg")
CAM_RAW = "/tmp/cam.nv12"
CAM_CFG = os.path.join(BASE, "cam.json")   # rot/白平衡增益，重启保留
_cam_lock = asyncio.Lock()   # 采集+转码全程串行，避免抢 /dev/video0
_cam = {"exposure": None, "gain": None, "ts": 0, "mean_y": None, "w": 0, "h": 0,
        "rot": 0, "wb_r": 1.0, "wb_b": 1.0}


def _cam_cfg_load():
    try:
        with open(CAM_CFG) as f:
            d = json.load(f)
        for k in ("rot", "wb_r", "wb_b"):
            if k in d:
                _cam[k] = d[k]
    except Exception:
        pass


def _cam_cfg_save():
    try:
        with open(CAM_CFG, "w") as f:
            json.dump({"rot": _cam["rot"], "wb_r": _cam["wb_r"],
                       "wb_b": _cam["wb_b"]}, f)
    except Exception:
        pass


_cam_cfg_load()


async def _cam_run(cmd, timeout=30):
    p = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        await p.communicate()
        raise RuntimeError("子进程超时: " + cmd[0])
    return p.returncode, out.decode(errors="replace"), err.decode(errors="replace")


def _cam_mean_y(path, w, h):
    # NV12 前 w*h 字节是亮度平面；抽样 65536 点求均值，纯 python 也是毫秒级
    try:
        with open(path, "rb") as f:
            data = f.read(w * h)
        if len(data) < w * h:
            return None
        step = max(1, (w * h) // 65536)
        return sum(data[::step]) / len(data[::step])
    except Exception:
        return None


async def cam_get_ctrls():
    try:
        rc, out, _ = await _cam_run(
            ["v4l2-ctl", "-d", CAM_DEV, "--get-ctrl", "exposure,gain"], 5)
        if rc != 0:
            return None
        d = {}
        for name in ("exposure", "gain"):
            m = re.search(name + r":\s*(\d+)", out) or \
                re.search(name + r".*?value=(\d+)", out)
            if m:
                d[name] = int(m.group(1))
        return d or None
    except Exception:
        return None


def _cam_rgb_means(data):
    """rawvideo rgb24 字节流 → (r,g,b) 均值（抽样）。"""
    try:
        r = data[0::48]
        g = data[1::48]
        b = data[2::48]
        return sum(r) / len(r), sum(g) / len(g), sum(b) / len(b)
    except Exception:
        return None


async def cam_capture(w=640, h=480, skip=3, count=5, want_rgb=False):
    async with _cam_lock:
        rc, _, err = await _cam_run([
            "v4l2-ctl", "-d", CAM_DEV,
            "--set-fmt-video=width=%d,height=%d,pixelformat=NV12" % (w, h),
            "--stream-mmap", "--stream-skip=%d" % skip,
            "--stream-count=%d" % count, "--stream-to=" + CAM_RAW, "--silent"], 40)
        if rc != 0 or not os.path.isfile(CAM_RAW):
            raise RuntimeError("采集失败: " + err.strip()[:180])
        loop = asyncio.get_running_loop()
        mean = await loop.run_in_executor(None, _cam_mean_y, CAM_RAW, w, h)
        frame_off = str((count - 1) * w * h * 3 // 2)   # 跳到最后一帧，免去 select 滤镜
        rgb = None
        if want_rgb:
            # 白平衡测量：和出图同一条 NV12→RGB 路径，测得的增益才能直接套用
            p = await asyncio.create_subprocess_exec(
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "rawvideo", "-pix_fmt", "nv12", "-s", "%dx%d" % (w, h),
                "-skip_initial_bytes", frame_off, "-i", CAM_RAW,
                "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            out, _ = await asyncio.wait_for(p.communicate(), 30)
            rgb = await loop.run_in_executor(None, _cam_rgb_means, out)
        vf = []
        if abs(_cam["wb_r"] - 1) > 0.01 or abs(_cam["wb_b"] - 1) > 0.01:
            vf.append("colorchannelmixer=rr=%.3f:bb=%.3f" % (_cam["wb_r"], _cam["wb_b"]))
        if _cam["rot"] == 90:
            vf.append("transpose=1")
        elif _cam["rot"] == 180:
            vf.append("transpose=1,transpose=1")
        elif _cam["rot"] == 270:
            vf.append("transpose=2")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "nv12", "-s", "%dx%d" % (w, h),
               "-skip_initial_bytes", frame_off, "-i", CAM_RAW,
               "-frames:v", "1", "-y"]
        if vf:
            cmd += ["-vf", ",".join(vf)]
        cmd.append(CAM_JPG)
        rc, _, err = await _cam_run(cmd, 40)
        try:
            os.remove(CAM_RAW)
        except OSError:
            pass
        if rc != 0:
            raise RuntimeError("转码失败: " + err.strip()[:180])
        _cam.update(ts=time.time(), mean_y=mean, w=w, h=h)
        d = dict(_cam)
        d["rgb"] = rgb
        return d


async def cam_awb():
    """灰世界白平衡：以 G 均值为基准算 R/B 增益（对准白/灰色物体后校准最准）。"""
    st = await cam_capture(640, 480, 3, 4, want_rgb=True)
    rgb = st.get("rgb")
    if not rgb:
        raise RuntimeError("取色失败")
    r, g, b = rgb
    if r < 1 or b < 1:
        raise RuntimeError("画面太暗，先自动曝光")
    _cam["wb_r"] = round(min(2.4, max(0.6, g / r)), 3)
    _cam["wb_b"] = round(min(2.4, max(0.6, g / b)), 3)
    _cam_cfg_save()
    st = await cam_capture(640, 480, 3, 4)   # 用新增益重拍一张
    return {"ok": True, "wb_r": _cam["wb_r"], "wb_b": _cam["wb_b"],
            "rgb": [round(x, 1) for x in rgb], "mean_y": st.get("mean_y"),
            "ts": st["ts"]}


async def cam_autoex(target=115):
    """逐次逼近：升档时先拉曝光(到顶才拉增益)，降档时先收增益。最后顺手做白平衡。"""
    vals = await cam_get_ctrls() or {}
    exp = int(vals.get("exposure", 1575))
    gain = int(vals.get("gain", 256))
    for _ in range(4):
        st = await cam_capture(640, 480, 3, 4)
        m = st.get("mean_y") or 1.0
        if 85 <= m <= 150:
            break
        scale = target / max(m, 1)
        if scale > 1:
            new_exp = min(4095, int(exp * scale))
            gain = min(43663, int(gain * max(1.0, scale * exp / max(new_exp, 1))))
            exp = new_exp
        else:
            new_gain = max(256, int(gain * scale))
            exp = max(10, int(exp * (scale * gain / max(new_gain, 1))))
            gain = new_gain
        await _cam_run(["v4l2-ctl", "-d", CAM_DEV, "--set-ctrl",
                        "exposure=%d,gain=%d" % (exp, gain)], 5)
    st = await cam_capture(640, 480, 3, 4)
    _cam.update(exposure=exp, gain=gain)
    out = {"ok": True, "exposure": exp, "gain": gain,
           "mean_y": st.get("mean_y"), "ts": st["ts"]}
    try:
        awb = await cam_awb()
        if awb.get("ok"):
            out["wb_r"], out["wb_b"] = awb["wb_r"], awb["wb_b"]
    except Exception as e:
        out["awb_err"] = str(e)[:120]
    return out


async def h_cam(request):
    present = os.path.exists(CAM_DEV)
    if present:
        vals = await cam_get_ctrls()
        if vals:
            _cam.update(vals)
    d = dict(_cam)
    d["present"] = present
    return web.json_response(d)


async def h_cam_img(request):
    if not os.path.isfile(CAM_JPG):
        raise web.HTTPNotFound()
    return _no_store(web.FileResponse(CAM_JPG, headers={"Content-Type": "image/jpeg"}))


async def h_cam_post(request):
    try:
        b = await request.json()
    except Exception:
        b = {}
    op = b.get("op")
    try:
        if op == "snap":
            w = int(b.get("w", 640))
            h = int(b.get("h", 480))
            st = await cam_capture(w, h, 3, 5)
            return web.json_response({"ok": True, **st})
        if op == "set":
            parts = []
            if b.get("exposure") is not None:
                parts.append("exposure=%d" % int(b["exposure"]))
                _cam["exposure"] = int(b["exposure"])
            if b.get("gain") is not None:
                parts.append("gain=%d" % int(b["gain"]))
                _cam["gain"] = int(b["gain"])
            if parts:
                rc, _, err = await _cam_run(
                    ["v4l2-ctl", "-d", CAM_DEV, "--set-ctrl", ",".join(parts)], 5)
                if rc != 0:
                    return web.json_response({"ok": False, "err": err.strip()[:180]})
            if b.get("rot") is not None:
                rot = int(b["rot"])
                if rot not in (0, 90, 180, 270):
                    return web.json_response({"ok": False, "err": "rot 只能 0/90/180/270"})
                _cam["rot"] = rot
                _cam_cfg_save()
            if b.get("wb_reset"):
                _cam["wb_r"] = _cam["wb_b"] = 1.0
                _cam_cfg_save()
            return web.json_response({"ok": True, "exposure": _cam["exposure"],
                                      "gain": _cam["gain"], "rot": _cam["rot"],
                                      "wb_r": _cam["wb_r"], "wb_b": _cam["wb_b"]})
        if op == "autoex":
            return web.json_response(await cam_autoex())
        if op == "awb":
            return web.json_response(await cam_awb())
    except Exception as e:
        return web.json_response({"ok": False, "err": str(e)})
    return web.json_response({"ok": False, "err": "unknown op %s" % op})


# ---------------- 喇叭 (TLV320AIC3104 用户态 init + aplay) ----------------
# codec 由 /root/audio/tlv320_init.py 初始化(重启后复位,必须重跑);音量=DAC 数字增益
# 0 ~ -63.5 dB(0.5 步进)。功放 PAM8406D 由 +BATT 供电,USB-C 供电时无声属正常。
_spk = {"inited": False, "gain": -10.0, "last": ""}


async def _spk_run(cmd, timeout=25):
    p = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        await p.communicate()
        raise RuntimeError("子进程超时")
    return p.returncode, out.decode(errors="replace").strip()


async def h_spk(request):
    return web.json_response(_spk)


async def h_spk_post(request):
    try:
        b = await request.json()
    except Exception:
        b = {}
    op = b.get("op")
    try:
        if op == "init":
            g = min(0.0, max(-63.5, float(b.get("gain", _spk["gain"]))))
            rc, out = await _spk_run(
                ["python3", "/root/audio/tlv320_init.py", "10", str(g)], 25)
            _spk["inited"] = rc == 0
            _spk["gain"] = g
            _spk["last"] = out[-200:]
            return web.json_response({"ok": rc == 0, "gain": g, "log": out[-300:]})
        if op == "play":
            n = min(3, max(1, int(b.get("count", 1))))
            for _ in range(n):
                rc, out = await _spk_run(
                    ["aplay", "-q", "-D", "hw:HATTLV320,0", "/root/audio/test440.wav"], 15)
                if rc != 0:
                    return web.json_response({"ok": False, "log": out[-200:]})
                await asyncio.sleep(0.3)
            _spk["last"] = "played x%d" % n
            return web.json_response({"ok": True, "played": n, "gain": _spk["gain"]})
    except Exception as e:
        return web.json_response({"ok": False, "err": str(e)})
    return web.json_response({"ok": False, "err": "unknown op %s" % op})



# ---------------- 示教录制 / 轨迹回放 ----------------
# 手扶演示(扭矩关)→ 高频记录关节角 → 按录制的路径回放(天然平衡路径)。
# 这也是步态示教的原语:迈步序列同样录/放。
_teach = {"rec": False, "t0": 0.0, "dt": 0.05, "frames": [], "th": None,
          "err": ""}
TRAJ_DIR = os.path.join(BASE, "trajectories")
os.makedirs(TRAJ_DIR, exist_ok=True)


def _teach_worker(delay=0.0, dur=0.0, name="轨迹"):
    ids = list(GROUP_IDS) if "GROUP_IDS" in globals() else         list(range(10, 15)) + list(range(20, 25)) + list(range(30, 35))
    if delay > 0:
        time.sleep(delay)          # 倒计时:点击后给用户时间抓手(扭矩尚未关)
    _teach["t0"] = time.time()
    servo_action("torque", ids, False)   # 此刻才扭矩关,开始录
    while _teach["rec"]:
        t = time.time() - _teach["t0"]
        try:
            st = servo_action("states", ids)
            frame = [t]
            for i in ids:
                s = st.get(i)
                frame.append(s["pos"] if s else None)
            if all(p is not None for p in frame[1:]):
                _teach["frames"].append(frame)
        except Exception as e:
            _teach["err"] = str(e)
        if dur > 0 and t >= dur:
            break                 # 到时自动停
        time.sleep(_teach["dt"])
    # 自动停:直接存档(worker 线程内完成,无需用户点停止)
    if dur > 0 and _teach["rec"]:
        _teach["rec"] = False
        n = len(_teach["frames"])
        if n >= 5:
            fn = "%s-%s.json" % (name, time.strftime("%Y%m%d-%H%M%S"))
            json.dump({"name": name, "dt": _teach["dt"], "frames": _teach["frames"]},
                      open(os.path.join(TRAJ_DIR, fn), "w"), ensure_ascii=False)
            _teach["last_auto"] = fn


def trajs_list():
    out = []
    for f in sorted(os.listdir(TRAJ_DIR)):
        if f.endswith(".json"):
            try:
                d = json.load(open(os.path.join(TRAJ_DIR, f), encoding="utf-8"))
                out.append({"file": f, "name": d.get("name", f),
                            "frames": len(d.get("frames", [])),
                            "dur": round(d["frames"][-1][0], 1) if d.get("frames") else 0})
            except Exception:
                pass
    return out


def traj_play(fname, rate=1.0, acc=None):
    path = os.path.join(TRAJ_DIR, os.path.basename(fname))
    d = json.load(open(path, encoding="utf-8"))
    frames = d["frames"]
    if len(frames) < 2:
        raise ValueError("轨迹太短")
    ids = list(range(10, 15)) + list(range(20, 25)) + list(range(30, 35))
    n = len(ids)
    servo_action("torque", ids, True)
    bus = get_bus()
    dur = frames[-1][0] / max(0.1, rate)
    dmax = max(max(f) - min(f) for f in zip(*[[fr[i + 1] for fr in frames] for i in range(n)]))
    v_reg = min(3400, int(dmax / dur / _speed_unit(bus) * 2.0) + 40)
    accv = max(0, min(255, int(acc if acc is not None else MOVE_ACC)))
    try:
        bus.sync_write(46, 2, [(s2, struct.pack("<H", v_reg)) for s2 in ids])
        bus.sync_write(41, 1, [(s2, bytes([accv])) for s2 in ids])
    except feetech.BusError:
        pass
    t0 = time.time()
    k = 0
    traj_i = 0
    lastp = None
    dt = 0.05
    while True:
        t = (time.time() - t0) * max(0.1, rate)
        if k + 1 >= len(frames):
            break
        while k + 2 < len(frames) and frames[k + 1][0] < t:
            k += 1
        f0, f1 = frames[k], frames[k + 1]
        span = max(1e-3, f1[0] - f0[0])
        u = max(0.0, min(1.0, (t - f0[0]) / span))
        pos = [int(round(a + (b - a) * u)) for a, b in zip(f0[1:], f1[1:])]
        frame = list(zip(ids, pos))
        try:
            if traj_i == 0:
                for sid, p in frame:
                    try:
                        bus.write_u16(sid, 42, p)
                    except feetech.BusError:
                        pass
            else:
                bus.sync_write(42, 2,
                               [(sid, struct.pack("<H", p)) for sid, p in frame])
        except feetech.BusError:
            pass
        traj_i += 1
        time.sleep(dt)
    # 终值 + ① 回读重发
    want = {ids[si]: int(p) for si, p in enumerate(frames[-1][1:])}
    fixed = _verify_resend(bus, want)
    return {"moved": len(frames), "fixed": fixed}


async def h_teach(request):
    return web.json_response({"rec": _teach["rec"],
                              "frames": len(_teach["frames"]),
                              "err": _teach["err"],
                              "trajs": trajs_list()})


async def h_teach_post(request):
    import threading
    try:
        b = await request.json()
    except Exception:
        b = {}
    op = b.get("op")
    if op == "start":
        if _teach["rec"]:
            return web.json_response({"ok": False, "err": "已在录制"})
        delay = min(10.0, max(0.0, float(b.get("delay", 0))))
        dur = min(60.0, max(0.0, float(b.get("dur", 0))))
        name = b.get("name", "").strip() or "轨迹"
        _teach.update(rec=True, t0=time.time(), frames=[], err="", last_auto=None)
        _teach["th"] = threading.Thread(
            target=_teach_worker, args=(delay, dur, name), daemon=True)
        _teach["th"].start()
        return web.json_response({"ok": True, "rec": True,
                                  "delay": delay, "dur": dur,
                                  "msg": "%.0f秒后开始,录%.0f秒自动停" % (delay, dur) if dur else "已开始"})
    if op == "stop":
        name = b.get("name", "").strip() or "轨迹"
        _teach["rec"] = False
        if _teach["th"]:
            _teach["th"].join(timeout=2)
        n = len(_teach["frames"])
        if n < 5:
            return web.json_response({"ok": False, "err": "样本太少(%d)" % n})
        fn = "%s-%s.json" % (name, time.strftime("%Y%m%d-%H%M%S"))
        json.dump({"name": name, "dt": _teach["dt"], "frames": _teach["frames"]},
                  open(os.path.join(TRAJ_DIR, fn), "w"), ensure_ascii=False)
        return web.json_response({"ok": True, "file": fn, "frames": n,
                                  "dur": round(_teach["frames"][-1][0], 1)})
    if op == "play":
        try:
            r = traj_play(b.get("file"), float(b.get("rate", 1.0)), b.get("acc"))
            if isinstance(r, dict):
                return web.json_response({"ok": True, "frames": r["moved"],
                                          "fixed": r.get("fixed", 0)})
            return web.json_response({"ok": True, "frames": r})
        except Exception as e:
            return web.json_response({"ok": False, "err": str(e)})
    if op == "delete":
        f = os.path.join(TRAJ_DIR, os.path.basename(b.get("file", "")))
        if os.path.isfile(f):
            os.remove(f)
        return web.json_response({"ok": True})
    return web.json_response({"ok": False, "err": "unknown op"})


# ---------------- HTTP（aiohttp） ----------------

def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


async def h_file(name, ctype):
    def handler(request):
        path = os.path.join(BASE, name)
        if not os.path.isfile(path):
            raise web.HTTPNotFound()
        return _no_store(web.FileResponse(path, headers={"Content-Type": ctype}))
    return handler


async def h_status(request):
    loop = asyncio.get_running_loop()
    d = await loop.run_in_executor(
        None, lambda: {"ok": True, "system": read_system(), "imu": read_imu(),
                       "bus": bus_status()})
    return web.json_response(d)


async def h_poses(request):
    return web.json_response({"ok": True, "poses": poses_list()})


async def h_scope_defaults(request):
    # 我们的板子说飞特协议（单字节地址的 sync_read），md1 块在 124；
    # dxl 是 Robotis 双字节地址格式，发给这块板只会全部超时。
    return web.json_response({
        "port": BUS_PORT, "baud": BUS_BAUD, "protocol": scope.PROTO_FEE,
        "mode": "sync_read", "read_len": 20, "id": IMU_ID, "rate": 50.0,
    })


async def h_post(request):
    """统一的 v2 POST API 分发。"""
    try:
        b = await request.json()
    except Exception:
        b = {}
    loop = asyncio.get_running_loop()

    def work():
        try:
            path = request.path
            if path == "/api/walk":
                return _walk(b.get("op", "status"), b)
            if path == "/api/scan":
                found = scan_ids(int(b.get("from", 1)), int(b.get("to", 40)))
                servos = [i for i in found if i != IMU_ID]
                states = servo_action("states", servos) if servos else {}
                return {"ok": True, "found": found, "servos": servos, "states": states}
            if path == "/api/servo":
                ids = [int(i) for i in b.get("ids", [])]
                out = servo_action(b.get("op"), ids, b.get("value"), b.get("speed"))
                return {"ok": True, "results": out}
            if path == "/api/estop":
                estop([int(i) for i in b.get("ids", [])])
                return {"ok": True}
            if path == "/api/pose":
                op = b.get("op")
                if op == "save":
                    fn = pose_save(b.get("name", "pose"), b.get("positions", {}))
                    return {"ok": True, "file": fn, "poses": poses_list()}
                if op == "apply":
                    r = pose_apply(b.get("file"), b.get("speed", 300), b.get("acc"))
                    if isinstance(r, dict):
                        return {"ok": True, **r}
                    return {"ok": True, "moved": r}
                if op == "shift":
                    # 重校零后批量平移已存姿态(参考 shift_saved_poses)
                    # deltas: {"14": 12, "24": -12, ...};备份到 poses/.trash-pre-shift/
                    deltas = {int(k): int(v) for k, v in (b.get("deltas") or {}).items()}
                    if not deltas or not b.get("confirm"):
                        return {"ok": False, "err": "需要 deltas 和 confirm:true"}
                    import shutil
                    bk = os.path.join(POSE_DIR, ".trash-pre-shift")
                    os.makedirs(bk, exist_ok=True)
                    n = 0
                    for f in os.listdir(POSE_DIR):
                        if not f.endswith(".json"):
                            continue
                        p = os.path.join(POSE_DIR, f)
                        try:
                            doc = json.load(open(p, encoding="utf-8"))
                        except Exception:
                            continue
                        shutil.copy(p, os.path.join(bk, f))
                        ch = False
                        for r in doc.get("rows", []):
                            if r["id"] in deltas:
                                r["pos"] = max(0, min(4095, r["pos"] + deltas[r["id"]]))
                                r["deg"] = round((r["pos"] - 2048) * 0.0879, 1)
                                ch = True
                        if ch:
                            json.dump(doc, open(p, "w", encoding="utf-8"),
                                      ensure_ascii=False)
                            n += 1
                    json.dump(deltas, open(os.path.join(bk, ".last_shift.json"), "w"))
                    return {"ok": True, "shifted": n, "backup": bk}
                if op == "unshift":
                    import shutil
                    bk = os.path.join(POSE_DIR, ".trash-pre-shift")
                    lf = os.path.join(bk, ".last_shift.json")
                    if not os.path.isfile(lf):
                        return {"ok": False, "err": "无可撤销的平移"}
                    deltas = {int(k): -int(v) for k, v in json.load(open(lf)).items()}
                    n = 0
                    for f in os.listdir(POSE_DIR):
                        p = os.path.join(POSE_DIR, f)
                        if not f.endswith(".json"):
                            continue
                        try:
                            doc = json.load(open(p, encoding="utf-8"))
                        except Exception:
                            continue
                        ch = False
                        for r in doc.get("rows", []):
                            if r["id"] in deltas:
                                r["pos"] = max(0, min(4095, r["pos"] + deltas[r["id"]]))
                                r["deg"] = round((r["pos"] - 2048) * 0.0879, 1)
                                ch = True
                        if ch:
                            json.dump(doc, open(p, "w", encoding="utf-8"),
                                      ensure_ascii=False)
                            n += 1
                    os.remove(lf)
                    return {"ok": True, "restored": n}
                if op == "delete":
                    if not b.get("confirm"):
                        return {"ok": False, "err": "需要 confirm:true"}
                    return {"ok": pose_delete(b.get("file"))}
                return {"ok": False, "err": f"unknown op {op}"}
            if path == "/api/flash":
                if not b.get("confirm"):
                    return {"ok": False, "log": "需要 confirm:true"}
                return flash_imu()
            if path == "/api/selftest":
                return {"ok": True, "identity": imu_identity(), "imu": read_imu()}
            if path == "/api/imu_wdt":
                if not b.get("confirm"):
                    return {"ok": False, "err": "需要 confirm:true（测试会让 IMU 板复位一次）"}
                return imu_wdt_test()
            if path == "/api/system":
                op = b.get("op")
                if not b.get("confirm"):
                    return {"ok": False, "err": "需要 confirm:true"}
                if op == "reboot":
                    subprocess.Popen(["shutdown", "-r", "now"])
                    return {"ok": True, "msg": "主机重启中…"}
                if op == "robotd_restart":
                    r = subprocess.run(["systemctl", "restart", "robotd"],
                                       capture_output=True, timeout=30)
                    return {"ok": r.returncode == 0, "err": r.stderr.decode()[-500:]}
                return {"ok": False, "err": f"unknown op {op}"}
            return {"ok": False, "err": "not found"}, 404
        except Exception as e:
            return {"ok": False, "err": f"{type(e).__name__}: {e}"}

    r = await loop.run_in_executor(None, work)
    code = 200
    if isinstance(r, tuple):
        r, code = r
    return web.json_response(r, status=code)


async def on_startup(app):
    app["broadcaster"] = asyncio.create_task(scope.broadcaster(app))


async def on_cleanup(app):
    app["broadcaster"].cancel()
    try:
        SESSION.disconnect()
    except Exception:
        pass


def build_app():
    app = web.Application()
    r = app.router
    r.add_get("/", lambda req: _no_store(
        web.FileResponse(os.path.join(BASE, "index.html"),
                         headers={"Content-Type": "text/html; charset=utf-8"})))
    r.add_get("/index.html", lambda req: web.HTTPFound("/"))
    r.add_get("/manifest.webmanifest", lambda req: _no_store(
        web.FileResponse(os.path.join(BASE, "manifest.webmanifest"),
                         headers={"Content-Type": "application/manifest+json"})))
    r.add_get("/sw.js", lambda req: _no_store(
        web.FileResponse(os.path.join(BASE, "sw.js"),
                         headers={"Content-Type": "application/javascript; charset=utf-8"})))
    r.add_get("/icon.svg", lambda req: _no_store(
        web.FileResponse(os.path.join(BASE, "icon.svg"),                         headers={"Content-Type": "image/svg+xml"})))
    r.add_get("/cal_ref.png", lambda req: _no_store(
        web.FileResponse(os.path.join(BASE, "cal_ref.png"),
                         headers={"Content-Type": "image/png"})))
    r.add_get("/cal_ref4.png", lambda req: _no_store(
        web.FileResponse(os.path.join(BASE, "cal_ref4.png"),
                         headers={"Content-Type": "image/png"})))
    # 仪表台（scope 模块自带 handlers；前端 WS 写死 /ws 所以挂根路径）
    r.add_get("/scope/", scope.index_handler)
    r.add_get("/scope/{name}", scope.static_handler)
    r.add_get("/ws", scope.ws_handler)
    r.add_get("/api/ports", scope.ports_handler)
    r.add_get("/api/state", scope.state_handler)
    # 面板 API
    r.add_get("/api/status", h_status)
    r.add_get("/api/poses", h_poses)
    r.add_get("/api/scope_defaults", h_scope_defaults)
    r.add_get("/api/tof", h_tof)
    r.add_post("/api/tof", h_tof_post)
    r.add_get("/api/speaker", h_spk)
    r.add_post("/api/speaker", h_spk_post)
    r.add_get("/api/imu_zero", h_imu_zero)
    r.add_post("/api/imu_zero", h_imu_zero_post)
    r.add_get("/api/teach", h_teach)
    r.add_post("/api/teach", h_teach_post)
    r.add_get("/api/cam", h_cam)
    r.add_post("/api/cam", h_cam_post)
    r.add_get("/cam/last.jpg", h_cam_img)
    for p in ("/api/scan", "/api/servo", "/api/estop", "/api/pose",
              "/api/flash", "/api/selftest", "/api/system", "/api/walk",
              "/api/imu_wdt"):
        r.add_post(p, h_post)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main():
    print(f"duck panel v3 (unified) on 0.0.0.0:{PORT}  bus={BUS_PORT}  "
          f"scope={SCOPE_DIR}  poses={POSE_DIR}", flush=True)
    web.run_app(build_app(), host="0.0.0.0", port=PORT, print=None)


if __name__ == "__main__":
    main()
