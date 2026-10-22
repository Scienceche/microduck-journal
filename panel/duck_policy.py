"""duck_policy — 50Hz ONNX 策略运行时（microduck_rl 训练产物部署）.

观测布局（61 维，与 Mjlab-Velocity-Flat-MicroDuck 训练逐位对齐）:
  [ 0: 3] base_ang_vel        机体角速度 rad/s（陀螺仪，体坐标系）
  [ 3: 6] projected_gravity   单位重力向量（体坐标系，四元数 R^T·[0,0,-1]，
                              对偏航漂移免疫）
  [ 6:20] joint_pos - HOME    14 关节位置 rad
  [20:34] joint_vel           14 关节速度 rad/s（上一步的值，编码器均值效应）
  [34:48] last_action         上一步策略输出
  [48:51] twist 指令          vx, vy, wz
  [51:55] head_pose 指令      4 关节相对 HOME 增量
  [55:61] body_pose 指令      6 维（本任务恒 0）

动作: 14 维 = 目标关节相对 HOME 的增量(rad) → 舵机目标 = HOME计数 + sign×action×k

安全: 重力倾角 > 40° 立即停止并回中位扭矩锁定可配。
"""

import json
import math
import os
import struct
import threading
import time

import feetech

# ---------------- 常量映射（policy 下标 → 舵机） ----------------
# 模型关节顺序: 左腿5 头4 右腿5（与官方 XML 一致）
# SIGN 待真机逐关节核对（悬空测试: /walk/probe 下发单关节 +0.2rad 看方向）
POLICY_JOINTS = [
    ("left_hip_yaw", 20), ("left_hip_roll", 21), ("left_hip_pitch", 22),
    ("left_knee", 23), ("left_ankle", 24),
    ("neck_pitch", 30), ("head_pitch", 31), ("head_yaw", 32), ("head_roll", 33),
    ("right_hip_yaw", 10), ("right_hip_roll", 11), ("right_hip_pitch", 12),
    ("right_knee", 13), ("right_ankle", 14),
]
SERVO_IDS = [sid for _, sid in POLICY_JOINTS]

# 官方 STAND2 HOME（rad）
HOME_RAD = {
    "left_hip_yaw": 0.0, "left_hip_roll": -0.0873, "left_hip_pitch": -0.4579,
    "left_knee": -0.0049, "left_ankle": 0.4530,
    "neck_pitch": 0.3491, "head_pitch": 0.3491, "head_yaw": 0.0, "head_roll": 0.0,
    "right_hip_yaw": 0.0, "right_hip_roll": 0.0873, "right_hip_pitch": 0.4579,
    "right_knee": 0.0049, "right_ankle": -0.4530,
}
HOME_VEC = [HOME_RAD[n] for n, _ in POLICY_JOINTS]

# 模型关节角 → 舵机计数方向（+1/-1）。默认全 -1，probe 校准后修正。
SIGN = [-1] * 14
# 关节限幅（rad，相对零位的绝对限）——策略输出 clamp 用，防打坏齿轮
JOINT_RANGE = {  # (min, max) 相对机械零位，保守值
    "hip_yaw": (-1.0, 1.0), "hip_roll": (-0.6, 0.6), "hip_pitch": (-1.57, 1.0),
    "knee": (-2.2, 0.3), "ankle": (-1.0, 1.0),
    "neck_pitch": (-1.57, 1.57), "head_pitch": (-1.57, 1.57),
    "head_yaw": (-1.57, 1.57), "head_roll": (-0.35, 0.35),
}

COUNTS_PER_RAD = 4096.0 / (300.0 * math.pi / 180.0)  # Feetech 4096 / 300°
# 当前速度寄存器单位 0.732 RPM（BIT15 方向）→ rad/s
RPM_TO_RAD = 0.732 * 2.0 * math.pi / 60.0
WRITE_SPEED = 3000  # 0.732RPM 单位，远超物理上限 ≈ 不限速（HLS 位置模式下 0 语义有歧义）
IMU_ID = 200
IMU_BLOCK_ADDR = 124  # 0x7C，20 字节块（与面板/固件一致）
GYRO_FS_DPS = 500.0
TILT_LIMIT_DEG = 40.0

# IMU 体轴映射: 模型(x前,y左,z上) ← 舵机板陀螺仪原始 (gx,gy,gz) 的线性组合。
# 待 /walk/probe 真机核对（ nose-down 时 g_model_x 应 < 0 等）。
IMU_AXIS = {  # model_axis: (raw_axis_idx, sign)
    "x": (0, +1.0), "y": (1, +1.0), "z": (2, +1.0),
}


def _quat_to_gravity(qx, qy, qz, qw):
    """projected_gravity = R^T @ [0,0,-1]（重力方向在体坐标系）.

    竖直站立时 ≈ [0,0,-1]（重力朝下 = 体 -z 方向）——与 mjlab 训练一致。
    先算世界 +Z（上）在体坐标系的向量，再取负。
    """
    up_x = 2 * (qx * qz + qw * qy)
    up_y = 2 * (qy * qz - qw * qx)
    up_z = 1 - 2 * (qx * qx + qy * qy)
    return -up_x, -up_y, -up_z


class PolicyRunner:
    """一个后台线程跑 50Hz 推理环，面板端点控制启停与指令。"""

    def __init__(self, bus_factory, onnx_path):
        self._bus_factory = bus_factory
        self.onnx_path = onnx_path
        self.thread = None
        self.stop_flag = threading.Event()
        self.lock = threading.Lock()
        self.cmd = {"vx": 0.0, "vy": 0.0, "wz": 0.0, "head": [0.0] * 4}
        self.status = {"running": False, "hz": 0.0, "tilt_deg": 0.0,
                       "action_absmax": 0.0, "err": None, "iters": 0}
        self._obs = None
        self._last_action = [0.0] * 14
        self._last_qvel = [0.0] * 14

    # ---------- 指令 ----------
    def set_cmd(self, **kw):
        with self.lock:
            for k in ("vx", "vy", "wz"):
                if k in kw:
                    self.cmd[k] = max(-1.0, min(1.0, float(kw[k] or 0)))
            if "head" in kw and len(kw["head"]) == 4:
                self.cmd["head"] = [float(v) for v in kw["head"]]

    def start(self):
        if self.thread and self.thread.is_alive():
            return {"ok": True, "note": "already running"}
        import onnxruntime as ort
        self._sess = ort.InferenceSession(
            self.onnx_path, providers=["CPUExecutionProvider"])
        self._iname = self._sess.get_inputs()[0].name
        shape = self._sess.get_inputs()[0].shape
        if list(shape)[-1] != 61:
            return {"ok": False, "err": f"obs dim {shape} != 61"}
        self.stop_flag.clear()
        self.status.update(running=True, err=None, iters=0)
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return {"ok": True}

    def stop(self):
        self.stop_flag.set()
        if self.thread:
            self.thread.join(timeout=2.0)
        self.status["running"] = False
        return {"ok": True}

    # ---------- 50Hz 主环 ----------
    def _loop(self):
        period = 1.0 / 50.0
        n = 0
        t_report = time.time()
        while not self.stop_flag.is_set():
            t0 = time.time()
            try:
                obs = self._build_obs()
                if obs is None:
                    # IMU/总线失联兜底：连续 10 帧(0.2s)读不到 → 回 STAND2 静态站姿
                    self._imu_miss += 1
                    if self._imu_miss == 10:
                        self._send_home()
                    time.sleep(0.02)
                    continue
                self._imu_miss = 0
                act = self._sess.run(None, {self._iname: obs})[0][0]
                self._apply(act)
                self._last_action = [float(v) for v in act]
                n += 1
            except Exception as e:
                self.status["err"] = f"{type(e).__name__}: {e}"
                time.sleep(0.5)
                continue
            now = time.time()
            if now - t_report >= 1.0:
                self.status["hz"] = n / (now - t_report)
                self.status["iters"] += n
                n = 0
                t_report = now
            dt = period - (time.time() - t0)
            if dt > 0:
                time.sleep(dt)

    def _build_obs(self):
        bus = self._bus_factory()
        if bus is None:
            self.status["err"] = "bus unavailable"
            return None
        # --- IMU ---
        _, p = bus.read(IMU_ID, IMU_BLOCK_ADDR, 20)
        gx, gy, gz = struct.unpack_from("<3h", p, 0)
        qx, qy, qz = struct.unpack_from("<3e", p, 6)
        qw = math.sqrt(max(0.0, 1.0 - (qx * qx + qy * qy + qz * qz)))
        k = GYRO_FS_DPS / 32768.0 * math.pi / 180.0
        gyro = [gx * k, gy * k, gz * k]
        # 轴映射（probe 后修正 IMU_AXIS）
        av = [IMU_AXIS["x"][1] * gyro[IMU_AXIS["x"][0]],
              IMU_AXIS["y"][1] * gyro[IMU_AXIS["y"][0]],
              IMU_AXIS["z"][1] * gyro[IMU_AXIS["z"][0]]]
        gr = _quat_to_gravity(qx, qy, qz, qw)
        gv = [IMU_AXIS["x"][1] * gr[IMU_AXIS["x"][0]],
              IMU_AXIS["y"][1] * gr[IMU_AXIS["y"][0]],
              IMU_AXIS["z"][1] * gr[IMU_AXIS["z"][0]]]
        # 倾角 = 体z轴与世界向上的夹角; 站立时 gv≈[0,0,-1] → 0°
        tilt = math.degrees(math.acos(max(-1.0, min(1.0, -gv[2]))))
        self.status["tilt_deg"] = round(tilt, 1)
        if tilt > TILT_LIMIT_DEG:
            self.status["err"] = f"tilt {tilt:.0f}° > {TILT_LIMIT_DEG}°, stopped"
            self.stop_flag.set()
            return None
        # --- 舵机 ---
        st = bus.states(SERVO_IDS)
        qpos, qvel = [], []
        for i, sid in enumerate(SERVO_IDS):
            s = st.get(sid) if isinstance(st, dict) else st[i]
            if s is None or s.get("pos") is None:
                self.status["err"] = f"servo {sid} no echo"
                return None
            rad = (s["pos"] - 2048) / COUNTS_PER_RAD  # 相对零位
            qpos.append(SIGN[i] * rad - HOME_VEC[i])  # 相对 HOME
            qvel.append(SIGN[i] * (s.get("speed") or 0) * RPM_TO_RAD)
        with self.lock:
            c = self.cmd
            cmd = [c["vx"], c["vy"], c["wz"]] + list(c["head"]) + [0.0] * 6
        self._last_qvel, qvel_prev = qvel, self._last_qvel
        obs = av + gv + qpos + qvel_prev + list(self._last_action) + cmd
        assert len(obs) == 61
        import numpy as np
        return np.array([obs], dtype=np.float32)

    def _send_home(self):
        """IMU 失联兜底：以中速回 STAND2（位置舵机静态站立本身稳）。"""
        try:
            bus = self._bus_factory()
            if bus is not None:
                go_home(bus, speed=200)
                self.status["err"] = "IMU 失联>0.2s，已回 HOME 站姿待恢复"
        except Exception:
            pass

    def _apply(self, act):
        bus = self._bus_factory()
        targets = []
        for i, (name, sid) in enumerate(POLICY_JOINTS):
            key = name.split("_", 1)[1] if name.startswith(("left", "right")) else name
            lo, hi = JOINT_RANGE[key]
            target_rad = HOME_VEC[i] + float(act[i])
            target_rad = max(lo, min(hi, target_rad))
            counts = 2048 + SIGN[i] * target_rad * COUNTS_PER_RAD
            targets.append((sid, int(counts)))
        self.status["action_absmax"] = round(
            max(abs(float(v)) for v in act), 3)
        bus.sync_write_pos_ex(targets, speed=WRITE_SPEED, acc=0)


# ---------------- 单关节探针（方向标定用） ----------------
def imu_raw(bus):
    """标定用原始读数：陀螺 raw/deg、四元数、重力投影（映射前后）。

    与仿真真值对照（见 明早验证清单.md）：竖直站立 gravity_mapped ≈ [0,0,-1]，
    机头下压 30° 时 x 分量变负等。
    """
    _, p = bus.read(IMU_ID, IMU_BLOCK_ADDR, 20)
    gx, gy, gz = struct.unpack_from("<3h", p, 0)
    qx, qy, qz = struct.unpack_from("<3e", p, 6)
    qw = math.sqrt(max(0.0, 1.0 - (qx * qx + qy * qy + qz * qz)))
    gr = _quat_to_gravity(qx, qy, qz, qw)
    k = GYRO_FS_DPS / 32768.0
    return {
        "gyro_raw": [gx, gy, gz],
        "gyro_dps": [round(gx * k, 1), round(gy * k, 1), round(gz * k, 1)],
        "quat": [round(v, 3) for v in (qx, qy, qz, qw)],
        "gravity_raw": [round(v, 3) for v in gr],
        "gravity_mapped": [round(IMU_AXIS[a][1] * gr[IMU_AXIS[a][0]], 3) for a in "xyz"],
        "tilt_deg": round(math.degrees(math.acos(max(-1, min(1, -IMU_AXIS["z"][1] * gr[IMU_AXIS["z"][0]])))), 1),
    }


def probe_joint(bus, policy_idx, delta_rad=0.2, speed=80):
    """单关节下探: HOME + delta，供人工比对 MuJoCo 中该关节正向旋转方向."""
    name, sid = POLICY_JOINTS[policy_idx]
    target = HOME_VEC[policy_idx] + delta_rad
    counts = 2048 + SIGN[policy_idx] * target * COUNTS_PER_RAD
    bus.sync_write_pos_ex([(sid, int(counts))], speed=speed, acc=20)
    return {"joint": name, "sid": sid, "policy_idx": policy_idx,
            "delta_rad": delta_rad, "counts": int(counts)}


def go_home(bus, speed=100):
    """全体回 STAND2 HOME。"""
    targets = []
    for i, (name, sid) in enumerate(POLICY_JOINTS):
        counts = 2048 + SIGN[i] * HOME_VEC[i] * COUNTS_PER_RAD
        targets.append((sid, int(counts)))
    bus.sync_write_pos_ex(targets, speed=speed, acc=20)
    return {"ok": True, "pose": "STAND2"}
