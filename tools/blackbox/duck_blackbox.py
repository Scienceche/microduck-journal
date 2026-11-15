#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""鸭子黑匣子(飞行记录仪) v3 — 订阅 robot.state 流(与中控台 3D 视图同源)。

v3 (2026-11-12 晚, 用户点破数据源后重写):
  - 旧版轮询 robot.busStatus(诊断口, 只有 payload_12_hex 原始字节) → 倾角全瞎
    (v2 的 imu_ok=BAD 诚实暴露; v1 的 tilt=0° 是缺省值假象)
  - v3 改订阅 robot.state 流: safety.gravity 解码好的重力向量 + safety.fallen
    官方摔倒判定(关节+重力融合) + joints/targets 全量 — 与中控台 3D 视图同源
  - 订阅是收 robotd 已有广播的拷贝, 零额外总线事务(旧 10Hz 轮询的压力问题消失)
  - 电压(brownout)用 1Hz busStatus 辅助轮询(原先 1/10 的负载)
  - fall 触发: safety.fallen 上升沿 OR tilt>45° 持续 300ms(双保险)

v2 关键机制保留: 躺倒重武装(防事件风暴驱逐历史)/dump OSError 保护/
monotonic 时钟/post 定长/递增冷却/心跳截断限速。
子命令: record(常驻) / tail(读最近事件)
"""
import gzip
import json
import math
import shutil
import socket
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

SOCK = "/run/robotd.sock"
OUT_DIR = Path("/root/blackbox")
HB_LOG = OUT_DIR / "heartbeat.log"

HZ = 20                 # 订阅帧率(服务端节流, 与中控台一致)
VOLT_POLL_S = 1.0       # 电压辅助轮询周期
PRE_S = 30              # 环形缓冲长度(秒)
POST_S = 10             # 事件后继续记录上限
FALL_TILT = 45.0        # 备用倾角触发(主判据是 safety.fallen)
FALL_CONFIRM = 3        # 倾角连续采样确认
RECOVER_TILT = 30.0
RECOVER_S = 2.0
EV_COOLDOWN = 10.0      # 所有事件落盘共享冷却
BROWNOUT_V = 7.4
BROWNOUT_COOLDOWN = 60.0
MAX_EVENTS = 50
MIN_FREE_MB = 100
HB_MAX_BYTES = 512 * 1024
HB_FAIL_COOLDOWN = 60.0
JOINT_N = 14


# ---------------- robot.state 订阅(与中控台 agent.py 同协议) ----------------
def state_frames(timeout=3.0):
    """生成器: 持续产出 robot.state 帧; 断线自动重连(2s 退避)。"""
    req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "robot.subscribe",
                      "params": {"hz": HZ}}) + "\n"
    while True:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(SOCK)
            s.sendall(req.encode())
            reader = s.makefile("rb")
            for line in reader:
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("id") == 1:
                    if not (msg.get("result") or {}).get("accepted"):
                        raise ValueError("订阅未获接受: %s" % msg.get("error"))
                    continue
                if msg.get("method") == "robot.state" and isinstance(msg.get("params"), dict):
                    yield msg["params"]
        except Exception:
            yield None  # 断线标记(调用方退避)
            time.sleep(2.0)


def bus_volts():
    """1Hz 辅助: busStatus 取最低舵机电压(brownout 监控用)。"""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(3.0)
        s.connect(SOCK)
        s.sendall((json.dumps({"jsonrpc": "2.0", "id": 1,
                               "method": "robot.busStatus"}) + "\n").encode())
        d = json.loads(s.makefile().readline())["result"]
        s.close()
        js = (d.get("native") or {}).get("joints") or []
        vs = [j.get("volts") for j in js if isinstance(j, dict) and j.get("volts")]
        return round(min(vs), 2) if vs else None
    except Exception:
        return None


def tilt_from_gravity(g):
    if (not isinstance(g, (list, tuple)) or len(g) != 3
            or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in g)):
        return None
    return math.degrees(math.acos(max(-1.0, min(1.0, -g[2]))))


def journal_context(pre_s=40):
    try:
        r = subprocess.run(
            ["journalctl", "-u", "robotd", "--since", "-%ds" % pre_s,
             "-n", "200", "--no-pager", "-o", "short-iso"],
            capture_output=True, text=True, timeout=3)
        return r.stdout.splitlines()[-200:]
    except Exception as e:
        return ["journalctl 失败: %s" % e]


def disk_ok():
    try:
        u = shutil.disk_usage(str(OUT_DIR))
        return (u.free >> 20) >= MIN_FREE_MB
    except Exception:
        return False


def prune_events():
    for p in sorted(OUT_DIR.glob("ev_*.json.gz"))[:-MAX_EVENTS]:
        try:
            p.unlink()
        except OSError:
            pass


def dump_event(etype, ring, meta):
    path = None
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        if not disk_ok():
            hb("落盘跳过: 磁盘空间不足/探测失败")
            return None
        ts = time.strftime("%Y%m%d-%H%M%S")
        path = OUT_DIR / ("ev_%s_%03d_%s.json.gz" % (
            ts, int(time.time() * 1000) % 1000, etype))
        doc = {"meta": meta, "frames": list(ring),
               "journal": journal_context() if etype == "fall" else []}
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
        prune_events()
        return path
    except OSError as e:
        if path is not None:
            try:
                path.unlink()
            except OSError:
                pass
        hb("落盘失败(%s): %s" % (etype, str(e)[:60]))
        return None


def hb(msg):
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        if HB_LOG.exists() and HB_LOG.stat().st_size > HB_MAX_BYTES:
            HB_LOG.unlink()
        with open(HB_LOG, "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def record():
    ring = deque(maxlen=PRE_S * HZ)
    post = deque(maxlen=(PRE_S + POST_S) * HZ)
    mono = time.monotonic
    last_hb = 0.0
    last_hb_fail = 0.0
    last_ev_dump = 0.0
    last_brownout_dump = 0.0
    last_volt_poll = 0.0
    v_min = None
    brownout_cd = BROWNOUT_COOLDOWN
    over_n = 0
    in_event = False
    event_start = None
    recov_start = None
    rearmed = True
    evt_meta = None

    print("[blackbox] v3 record: robot.state 订阅 %dHz + %ds 环形缓冲" % (HZ, PRE_S), flush=True)
    first_frame = True
    for f in state_frames():
        t0 = mono()
        if f is None:
            if t0 - last_hb_fail >= HB_FAIL_COOLDOWN:
                last_hb_fail = t0
                hb("robot.state 流中断(限速记录, 重连中)")
            continue
        if first_frame:
            first_frame = False
            hb("state 顶层: %s | policy: %s | move: %s" % (
                sorted(f.keys()),
                sorted((f.get("policy") or {}).keys()) if isinstance(f.get("policy"), dict) else f.get("policy"),
                sorted((f.get("move") or {}).keys()) if isinstance(f.get("move"), dict) else f.get("move")))

        safety = f.get("safety") or {}
        g = safety.get("gravity")
        tilt = tilt_from_gravity(g)
        fallen = bool(safety.get("fallen"))
        # 顶层 move/policy(11-12 发现 movement 键不存在, 顶帧有 move/policy)
        movement = f.get("move") or {}
        pol = f.get("policy")
        pol_state = pol.get("state") if isinstance(pol, dict) else pol
        joints = f.get("joints") or []

        # 电压辅助轮询(1Hz)
        if t0 - last_volt_poll >= VOLT_POLL_S:
            last_volt_poll = t0
            v_min = bus_volts()

        ring.append({
            "wall": time.time(), "phase": movement.get("phase"),
            "mode": movement.get("mode"),
            "policy": str(pol_state),
            "gx": round(g[0], 3) if isinstance(g, (list, tuple)) and len(g) == 3 else None,
            "gy": round(g[1], 3) if isinstance(g, (list, tuple)) and len(g) == 3 else None,
            "tilt": round(tilt, 1) if tilt is not None else None,
            "imu_ok": tilt is not None,
            "fallen": fallen,
            "q": [round(x, 4) for x in joints[:JOINT_N]
                  if isinstance(x, (int, float))][:JOINT_N],
            "tg": [round(x, 4) for x in (f.get("targets") or [])[:JOINT_N]
                   if isinstance(x, (int, float))][:JOINT_N],
            "V_min": v_min,
        })
        now = mono()

        # ---- fall 状态机: safety.fallen 上升沿为主, tilt>45 持续为备 ----
        if not in_event:
            trig = fallen or (tilt is not None and tilt > FALL_TILT
                              and (over_n := over_n + 1) >= FALL_CONFIRM)
            if tilt is None or tilt <= FALL_TILT:
                over_n = 0
            if trig and rearmed:
                in_event = True
                event_start = now
                recov_start = None
                post.clear()
                evt_meta = {
                    "type": "fall", "t_trigger_wall": ring[-1]["wall"],
                    "tilt_trigger": round(tilt, 1) if tilt is not None else None,
                    "fallen_flag": fallen,
                    "V_min_trigger": v_min,
                    "phase_trigger": movement.get("phase"),
                }
                if now - last_ev_dump >= EV_COOLDOWN:
                    p = dump_event("fall", ring, evt_meta)
                    if p:
                        last_ev_dump = now
                        hb("fall 落盘 %s tilt=%s fallen=%s" % (
                            p.name, evt_meta["tilt_trigger"], fallen))
        else:
            post.append(ring[-1])
            recovered = False
            if (tilt is not None and tilt < RECOVER_TILT and not fallen):
                if recov_start is None:
                    recov_start = now
                elif now - recov_start >= RECOVER_S:
                    recovered = True
            else:
                recov_start = None
            if recovered:
                evt_meta["t_recover_wall"] = ring[-1]["wall"]
                tilts_post = [x["tilt"] for x in post if x["tilt"] is not None]
                evt_meta["tilt_max_post"] = round(max(tilts_post), 1) if tilts_post else None
                if now - last_ev_dump >= EV_COOLDOWN:
                    p2 = dump_event("recover", post, evt_meta)
                    if p2:
                        last_ev_dump = now
                        hb("fall 恢复落盘 %s" % p2.name)
                in_event = False
                over_n = 0
                rearmed = True
                post.clear()
            elif now - event_start > PRE_S + POST_S:
                evt_meta["t_timeout_wall"] = ring[-1]["wall"]
                if now - last_ev_dump >= EV_COOLDOWN:
                    p3 = dump_event("stuck", post, evt_meta)
                    if p3:
                        last_ev_dump = now
                        hb("stuck 落盘 %s (解除武装直至回稳)" % p3.name)
                in_event = False
                over_n = 0
                rearmed = False
                recov_start = None
                post.clear()

        # 重武装判定(未在事件中)
        if not in_event and not rearmed and tilt is not None and tilt < RECOVER_TILT:
            if recov_start is None:
                recov_start = now
            elif now - recov_start >= RECOVER_S:
                rearmed = True
                hb("倾角回稳 %.0f°, fall 检测重新武装" % tilt)

        # ---- brownout(递增冷却) ----
        if v_min is not None and v_min < BROWNOUT_V:
            if now - last_brownout_dump >= brownout_cd and disk_ok():
                p = dump_event("brownout", ring, {
                    "type": "brownout", "t_trigger_wall": ring[-1]["wall"],
                    "V_min": v_min,
                    "tilt": round(tilt, 1) if tilt is not None else None,
                    "cd_next_s": int(min(brownout_cd * 2, 1800))})
                if p:
                    last_brownout_dump = now
                    brownout_cd = min(brownout_cd * 2, 1800.0)
                    hb("brownout 落盘 %s V=%.2f (下次冷却 %ds)" % (
                        p.name, v_min, int(brownout_cd)))
        elif v_min is not None and v_min >= BROWNOUT_V + 0.2:
            brownout_cd = BROWNOUT_COOLDOWN

        # ---- 心跳 ----
        if now - last_hb >= 60:
            last_hb = now
            hb("alive phase=%s tilt=%s V=%s joints=%d imu=%s" % (
                movement.get("phase"),
                "%.0f°" % tilt if tilt is not None else "NA",
                v_min, len(joints), "ok" if tilt is not None else "BAD"))


def tail():
    """打印最近一次事件摘要(坏文件跳过不崩)。"""
    evs = sorted(OUT_DIR.glob("ev_*.json.gz"))
    if not evs:
        print("无事件"); return
    for p in reversed(evs):
        try:
            with gzip.open(p, "rt", encoding="utf-8") as fh:
                d = json.load(fh)
            break
        except (OSError, ValueError):
            continue
    else:
        print("事件文件全部损坏"); return
    m = d["meta"]
    fr = d["frames"]
    print("文件: %s" % p.name)
    print("类型: %s  触发倾角 %s  fallen=%s  V=%s" % (
        m.get("type"), m.get("tilt_trigger"), m.get("fallen_flag"),
        m.get("V_min_trigger", m.get("V_min"))))
    if fr:
        tilts = [x["tilt"] for x in fr if x.get("tilt") is not None]
        if tilts:
            print("帧数 %d  时长 %.1fs  tilt: max %.0f° med %.0f°" % (
                len(fr), fr[-1]["wall"] - fr[0]["wall"],
                max(tilts), sorted(tilts)[len(tilts) // 2]))
        print("起止 phase: %s → %s" % (fr[0]["phase"], fr[-1]["phase"]))
    for ln in d.get("journal", [])[-15:]:
        print("  | %s" % ln[:150])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "record"
    if cmd == "record":
        record()
    elif cmd == "tail":
        tail()
    else:
        print("用法: duck_blackbox.py [record|tail]")
