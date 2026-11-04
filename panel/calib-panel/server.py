#!/usr/bin/env python3
"""duck-calib — 舵机标定台 (robotd commissioning 桥接) · :8085

复刻老 duck_panel 的标定页，全部总线操作改走 robotd 的 /run/robotd.sock
JSON-RPC（robot.busStatus / robot.busCommand / robot.relax），不再直接开 UART，
因此与 robotd 无总线竞争。模式切换调 /opt/robot/feetech-ft5-r5/service.sh
enable|disable（与官方控制台同一入口）。仅依赖 Python3 标准库。
"""
import json
import re
import socket
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SOCKET = "/run/robotd.sock"
SERVICE_SH = "/opt/robot/feetech-ft5-r5/service.sh"
ROOT = Path(__file__).resolve().parent
POSES = ROOT / "poses"
POSES.mkdir(exist_ok=True)
OFFICIAL_QUAT = [0.70710678, 0.0, 0.70710678, 0.0]
NAME_RE = re.compile(r"^[\w\-\u4e00-\u9fff]{1,24}$")

# 与 console/service_control.py 相同的超时预算：动作时长 + 15s。
CENTER_EXTRA = 120


def rpc(method, params=None, timeout=20.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(SOCKET)
        req = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            req["params"] = params
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        line = buf.decode(errors="replace").strip()
        if not line:
            raise RuntimeError("robotd 关闭了连接")
        reply = json.loads(line)
        if reply.get("error"):
            raise RuntimeError(reply["error"].get("message", str(reply["error"])))
        return reply.get("result")
    finally:
        s.close()


def bus_timeout(command):
    if command.get("action") == "center":
        return CENTER_EXTRA + command.get("duration_ms", 2000) / 1000.0 * 15
    return 15 + command.get("duration_ms", 0) / 1000.0


def run_service_sh(mode):
    """service.sh disable → 标定模式；enable → 运动模式。可能耗时 ~30s。"""
    proc = subprocess.run(
        ["/bin/bash", SERVICE_SH, "disable" if mode == "commissioning" else "enable"],
        capture_output=True, text=True, timeout=120,
    )
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-15:])
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "output": tail}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "duck-calib/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.address_string(), fmt % args))

    # ---- 基础 ----
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, name, ctype):
        path = ROOT / name
        if not path.is_file():
            self._json({"error": "not found"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode())
        except Exception:
            return {}

    # ---- GET ----
    def do_GET(self):
        p = self.path.split("?")[0]
        try:
            if p in ("/", "/index.html"):
                self._file("index.html", "text/html; charset=utf-8")
            elif p == "/cal_ref4.png":
                self._file("cal_ref4.png", "image/png")
            elif p == "/api/status":
                self._json(rpc("robot.busStatus", timeout=8.0) or {})
            elif p == "/api/cal":
                try:
                    self._json(rpc("robot.busCommand",
                                   {"action": "calibration-status"}, timeout=8.0) or {})
                except Exception:
                    # 运动模式不发布 bus_calibration：退回直读标定 TOML（只读展示，
                    # 写入仍一律经 robotd capture）。
                    import tomllib
                    with open("/etc/robot/feetech-ft5/hd1910-calibration.toml", "rb") as f:
                        cal = tomllib.load(f)
                    self._json({"calibration": cal, "source": "file"})
            elif p == "/api/poses":
                items = []
                for f in sorted(POSES.glob("*.json")):
                    try:
                        items.append(json.loads(f.read_text(encoding="utf-8")))
                    except Exception:
                        pass
                self._json({"poses": items})
            elif p == "/api/health":
                self._json({"ok": True})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._json({"error": str(exc)}, 200)

    # ---- POST ----
    def do_POST(self):
        p = self.path.split("?")[0]
        data = self._body()
        try:
            if p == "/api/bus":
                action = data.get("action")
                if action == "relax":
                    result = rpc("robot.relax", {}, timeout=25.0)
                elif action == "verify-imu":
                    result = rpc("robot.busCommand", data, timeout=25.0)
                else:
                    result = rpc("robot.busCommand", data, timeout=bus_timeout(data))
                self._json(result if result is not None else {"ok": True})
            elif p == "/api/mode":
                target = data.get("target")
                if target not in ("commissioning", "motion"):
                    self._json({"error": "target 必须是 commissioning 或 motion"})
                else:
                    self._json(run_service_sh(target))
            elif p == "/api/poses":
                name = str(data.get("name", "")).strip()
                if not NAME_RE.match(name):
                    self._json({"error": "姿态名只能用中文/字母/数字/_/-，≤24 字"})
                    return
                status = rpc("robot.busStatus", timeout=8.0) or {}
                servos = ((status.get("last_sample") or {}).get("servos")) or []
                if not servos:
                    self._json({"error": "没有实时关节反馈，无法保存姿态"})
                    return
                pose = {
                    "name": name,
                    "created": __import__("time").strftime("%m-%d %H:%M"),
                    "joints": {str(s["id"]): s["position_raw"] for s in servos},
                }
                (POSES / (name + ".json")).write_text(
                    json.dumps(pose, ensure_ascii=False, indent=1), encoding="utf-8")
                self._json({"saved": name})
            elif p == "/api/pose/apply":
                name = str(data.get("name", "")).strip()
                path = POSES / (name + ".json")
                if not NAME_RE.match(name) or not path.is_file():
                    self._json({"error": "姿态不存在"})
                    return
                pose = json.loads(path.read_text(encoding="utf-8"))
                status = rpc("robot.busStatus", timeout=8.0) or {}
                if status.get("mode") != "commissioning":
                    self._json({"error": "回放姿态需要先进入标定模式"})
                    return
                done, errors = [], []
                for sid, raw in sorted(pose["joints"].items(), key=lambda kv: int(kv[0])):
                    try:
                        rpc("robot.busCommand",
                            {"action": "move", "id": int(sid), "position": int(raw),
                             "duration_ms": 1200}, timeout=bus_timeout(
                                {"action": "move", "duration_ms": 1200}))
                        done.append(int(sid))
                    except Exception as exc:
                        errors.append({"id": int(sid), "error": str(exc)})
                        break  # 一个关节失败即停，避免后续关节带病动作
                self._json({"applied": done, "errors": errors})
            elif p == "/api/pose/delete":
                name = str(data.get("name", "")).strip()
                path = POSES / (name + ".json")
                if NAME_RE.match(name) and path.is_file():
                    path.unlink()
                    self._json({"deleted": name})
                else:
                    self._json({"error": "姿态不存在"})
            else:
                self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._json({"error": str(exc)}, 200)


if __name__ == "__main__":
    addr = ("0.0.0.0", 8085)
    print(f"duck-calib listening on http://{addr[0]}:{addr[1]}", flush=True)
    ThreadingHTTPServer(addr, Handler).serve_forever()
