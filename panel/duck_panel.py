#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Microduck 中控面板 v2 — 手机 PWA + 桌面自适应。

跑在主控上，手机/电脑浏览器访问 http://<主控IP>:8080，可"添加到主屏幕"当 App 用。
零第三方依赖（stdlib + pyserial），复用同目录 feetech.py。
功能：IMU 姿态 / 舵机分组控制+姿态库 / 总线诊断 / 固件烧录 / 系统操作。
姿态文件兼容 servo-web 的 poses/*.json（rows: [{id, joint, pos, deg}]）。

注意：占用 /dev/ttyS2 时 robotd 保持停止；可用 DUCK_BUS 环境变量换串口。
"""
import json
import math
import os
import platform
import re
import struct
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import feetech

PORT = 8080
BASE = os.path.dirname(os.path.abspath(__file__))
POSE_DIR = os.path.join(BASE, "poses")
BUS_PORT = os.environ.get("DUCK_BUS", "/dev/ttyS2")
BUS_BAUD = int(os.environ.get("DUCK_BAUD", "1000000"))
IMU_ID = 200
IMU_BLOCK_ADDR = 124
ELF_PATH = "/root/imu_fw/build/imu_to_dxl.elf"
GYRO_FS_DPS = 500.0
# 官方关节分组（与 duck-control 约定一致）
GROUPS = [("右腿", [10, 11, 12, 13, 14]), ("左腿", [20, 21, 22, 23, 24]), ("头颈", [30, 31, 32, 33, 34])]
JOINT_NAMES = {
    10: "r_hip_yaw", 11: "r_hip_roll", 12: "r_hip_pitch", 13: "r_knee", 14: "r_ankle",
    20: "l_hip_yaw", 21: "l_hip_roll", 22: "l_hip_pitch", 23: "l_knee", 24: "l_ankle",
    30: "head_yaw", 31: "head_pitch", 32: "head_roll", 33: "neck_yaw", 34: "neck_pitch",
}

_bus = None
_bus_err = None
_bus_lock = threading.Lock()
_bus_last_used = 0.0
#: 空闲多久后释放串口，让 IMU 仪表台（imu-scope @ :8081）能接管总线。
BUS_IDLE_RELEASE_S = 4.0
_robotd_cache = (0.0, None)

os.makedirs(POSE_DIR, exist_ok=True)


def get_bus():
    global _bus, _bus_err, _bus_last_used
    with _bus_lock:
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
    """空闲回收线程：get_bus 只在下次请求时才检查，没有新请求就永远不释放。
    面板页切后台即停轮询（poll 有 document.hidden 守卫），4 秒后这里释放串口，
    IMU 仪表台（imu-scope @ :8081）即可接管总线。"""
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
                print("[reaper] 总线空闲已释放（仪表台可连接）", flush=True)


threading.Thread(target=_bus_reaper, daemon=True).start()


def bus_status():
    get_bus()
    if _bus is None:
        return {"open": False, "error": _bus_err, "port": BUS_PORT}
    return {"open": True, "port": BUS_PORT, "stats": dict(_bus.stats)}


# ---------------- IMU ----------------

def read_imu():
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
    roll = math.degrees(math.atan2(2 * (w * qx + qy * qz), 1 - 2 * (qx * qx + qy * qy)))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, 2 * (w * qy - qz * qx)))))
    yaw = math.degrees(math.atan2(2 * (w * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz)))
    k = GYRO_FS_DPS / 32768.0
    return {
        "ok": True, "id": IMU_ID,
        "whoami_ok": bool(status & 1), "sflp_live": bool(status & 2),
        "status_raw": status, "sample_count": sample_count,
        "quat": [round(qx, 4), round(qy, 4), round(qz, 4), round(w, 4)],
        "mod": round(mod, 4),
        "euler": [round(roll, 1), round(pitch, 1), round(yaw, 1)],
        "gyro_dps": [round(gx * k, 1), round(gy * k, 1), round(gz * k, 1)],
        "accel_raw": [ax, ay],
    }


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


def servo_action(op, ids, value=None, speed=None):
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
    for sid in ids:
        try:
            if op == "torque":
                bus.write_u8(sid, 40, 1 if value else 0)
            elif op == "move":
                pos = max(0, min(4095, int(value)))
                if speed is not None:
                    bus.write_u16(sid, 46, max(0, min(3400, int(speed))))
                bus.write_u16(sid, 42, pos)
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


def pose_apply(fname, speed=300):
    path = os.path.join(POSE_DIR, os.path.basename(fname))
    if not os.path.isfile(path):
        raise FileNotFoundError(fname)
    doc = json.load(open(path, encoding="utf-8"))
    rows = [(int(r["id"]), int(r["pos"])) for r in doc.get("rows", [])]
    if rows:
        ids = [i for i, _ in rows]
        servo_action("torque", ids, True)
        for sid, pos in rows:
            servo_action("move", [sid], pos, speed)
    return len(rows)


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


# ---------------- HTTP ----------------

class Handler(BaseHTTPRequestHandler):
    server_version = "DuckPanel/2.0"

    def _send(self, body, ctype="application/json; charset=utf-8", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj, ensure_ascii=False).encode(), code=code)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            try:
                return json.loads(self.rfile.read(n))
            except Exception:
                return {}
        return {}

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        p = self.path.split("?")[0]
        files = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/index.html": ("index.html", "text/html; charset=utf-8"),
            "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
            "/sw.js": ("sw.js", "application/javascript; charset=utf-8"),
            "/icon.svg": ("icon.svg", "image/svg+xml"),
        }
        if p in files:
            try:
                with open(os.path.join(BASE, files[p][0]), "rb") as f:
                    self._send(f.read(), files[p][1])
            except FileNotFoundError:
                self._json({"ok": False, "err": f"{files[p][0]} 缺失"}, 500)
        elif p == "/api/status":
            self._json({"ok": True, "system": read_system(), "imu": read_imu(),
                        "bus": bus_status()})
        elif p == "/api/poses":
            self._json({"ok": True, "poses": poses_list()})
        else:
            self._json({"ok": False, "err": "not found"}, 404)

    def do_POST(self):
        b = self._body()
        try:
            if self.path == "/api/scan":
                found = scan_ids(int(b.get("from", 1)), int(b.get("to", 40)))
                servos = [i for i in found if i != IMU_ID]
                states = servo_action("states", servos) if servos else {}
                self._json({"ok": True, "found": found, "servos": servos, "states": states})
            elif self.path == "/api/servo":
                ids = [int(i) for i in b.get("ids", [])]
                out = servo_action(b.get("op"), ids, b.get("value"), b.get("speed"))
                self._json({"ok": True, "results": out})
            elif self.path == "/api/estop":
                estop([int(i) for i in b.get("ids", [])])
                self._json({"ok": True})
            elif self.path == "/api/pose":
                op = b.get("op")
                if op == "save":
                    fn = pose_save(b.get("name", "pose"), b.get("positions", {}))
                    self._json({"ok": True, "file": fn, "poses": poses_list()})
                elif op == "apply":
                    n = pose_apply(b.get("file"), b.get("speed", 300))
                    self._json({"ok": True, "moved": n})
                elif op == "delete":
                    if not b.get("confirm"):
                        self._json({"ok": False, "err": "需要 confirm:true"})
                    else:
                        self._json({"ok": pose_delete(b.get("file"))})
                else:
                    self._json({"ok": False, "err": f"unknown op {op}"})
            elif self.path == "/api/flash":
                if not b.get("confirm"):
                    self._json({"ok": False, "log": "需要 confirm:true"})
                else:
                    self._json(flash_imu())
            elif self.path == "/api/selftest":
                self._json({"ok": True, "identity": imu_identity(), "imu": read_imu()})
            elif self.path == "/api/system":
                op = b.get("op")
                if not b.get("confirm"):
                    self._json({"ok": False, "err": "需要 confirm:true"})
                elif op == "reboot":
                    subprocess.Popen(["shutdown", "-r", "now"])
                    self._json({"ok": True, "msg": "主机重启中…"})
                elif op == "robotd_restart":
                    r = subprocess.run(["systemctl", "restart", "robotd"], capture_output=True, timeout=30)
                    self._json({"ok": r.returncode == 0, "err": r.stderr.decode()[-500:]})
                else:
                    self._json({"ok": False, "err": f"unknown op {op}"})
            else:
                self._json({"ok": False, "err": "not found"}, 404)
        except Exception as e:
            self._json({"ok": False, "err": f"{type(e).__name__}: {e}"}, 200)


def main():
    print(f"duck panel v2 on 0.0.0.0:{PORT}  bus={BUS_PORT}  poses={POSE_DIR}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
