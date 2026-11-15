#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""鸭子端 OTA 辅助: toml 快照/原子切换/回退/健康探测。

部署在鸭子 /opt/duck/ota/ota_helper.py, 由 PC 端 ota_deploy.py 通过 SSH 调用。
所有输出一行 JSON, 便于 PC 端解析。

子命令:
  snapshot                  读当前 toml → {slots, toml_sha256}
  activate --slot walk --path /opt/.../policy.onnx
                            备份 toml → 原子改写指定槽位 → 校验 → {ok, backup}
  rollback --backup <file>  从备份恢复 toml(校验后原子替换)
  health [--sha <前12位>]   is-active + journalctl 策略行 + 总线探针
  stage-verify --dir <dir>  校验 .staging 包哈希链(manifest=runtime=实际)

设计要点(对应守则第2节事故):
  - toml 改写绝不用 sed: 读文本→正则改单键→tomllib 校验→tmp 文件→os.replace 原子落盘
  - 每个备份写完立即回读校验 sha256, 备份不可用则中止
"""
import argparse
import hashlib
import os
import json
import math
import re
import shutil
import socket
import subprocess
import sys
import time
import tomllib
from pathlib import Path

TOML_PATH = Path("/etc/robot/feetech-ft5/robotd.toml")
LOCK_PATH = Path("/etc/robot/feetech-ft5/.ota.lock")
SLOTS = ("walk", "stand", "sitstand")


def out(d):
    print(json.dumps(d, ensure_ascii=False))
    sys.exit(0)


def fail(msg, **kw):
    d = {"ok": False, "error": str(msg)}
    d.update(kw)
    out(d)


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_toml():
    try:
        with open(TOML_PATH, "rb") as f:
            return tomllib.load(f)
    except Exception as e:
        fail("toml 解析失败: %s" % e)


def bus_status(timeout=6.0):
    """总线探针: 与 deploy_verify.py 同款, 返回汇总 dict。"""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect("/run/robotd.sock")
    req = {"jsonrpc": "2.0", "id": 1, "method": "robot.busStatus"}
    s.sendall((json.dumps(req) + "\n").encode())
    d = json.loads(s.makefile().readline())["result"]
    s.close()
    n = d.get("native") or {}
    js = n.get("joints") or []
    volts = [j.get("volts") for j in js if j.get("volts")]
    imu = (d.get("last_sample") or {}).get("imu") or {}
    g = imu.get("gravity") or [0, 0, -1]
    tilt = math.degrees(math.acos(max(-1.0, min(1.0, -g[2]))))
    return {
        "mode": d.get("mode"), "phase": d.get("phase"),
        "policy_enabled": d.get("policy_enabled"),
        "servos": len(js), "fresh": n.get("all_joints_fresh"),
        "V": round(sum(volts) / len(volts), 2) if volts else None,
        "V_min": round(min(volts), 2) if volts else None,
        "tilt": round(tilt, 1),
    }


# ---------------- 互斥锁(防并发 OTA/手动 helper 交叉改 toml) ----------------
_lock_fh = None


def acquire_lock():
    global _lock_fh
    import fcntl  # Linux 独有, 惰性导入(Windows 测试环境跳过锁)
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _lock_fh = open(LOCK_PATH, "a+")
    try:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fail("ota.lock 被占用 — 另一个 OTA/维护进程正在操作 toml")


# ---------------- snapshot ----------------
def cmd_snapshot(_):
    acquire_lock()
    cfg = load_toml()
    pol = cfg.get("policy") or {}
    slots = {k: pol.get(k) for k in SLOTS if pol.get(k)}
    out({"ok": True, "slots": slots, "toml_sha256": sha256_file(TOML_PATH)})


# ---------------- activate ----------------
def replace_in_policy_section(text, slot, new_path):
    """只在 [policy] 段内替换 slot = "..." 行; 段外同名键不受影响(也不误匹配)。
    返回 (新文本, 是否找到)。支持双/单引号值、带引号的键(真实 robotd.toml
    用 "walk" = "..." 语法, 2026-11-12 回滚演练实证)。"""
    lines = text.splitlines(keepends=True)
    sec_start = None
    for i, ln in enumerate(lines):
        # 真实 robotd.toml 段头也带引号: ["policy"] — 两种都匹配
        if re.match(r'^\s*\[\s*"?policy"?\s*\]\s*(#.*)?$', ln):
            sec_start = i
            break
    if sec_start is None:
        return text, False
    pat = re.compile(r'^(\s*"?%s"?\s*=\s*)(["\'])(.*?)\2(.*)$' % re.escape(slot))
    for i in range(sec_start + 1, len(lines)):
        if re.match(r"^\s*\[", lines[i]):  # 进入下一节
            break
        m = pat.match(lines[i].rstrip("\n"))
        if m:
            lines[i] = "%s\"%s\"%s\n" % (m.group(1), new_path, m.group(4))
            return "".join(lines), True
    return text, False


def cmd_activate(a):
    if a.slot not in SLOTS:
        fail("slot 必须是 %s" % "/".join(SLOTS))
    acquire_lock()
    # 不做 resolve(): toml 写入调用方给的规范绝对路径
    # (Windows 下 resolve 会把 /opt/.. 变 C:\opt\.., 已在测试中暴露)
    new_path = str(a.path)
    if not new_path.startswith("/"):
        fail("path 必须是绝对路径: %s" % new_path)
    # 目标文件必须存在(死路径写进 toml 要靠 robotd 加载失败才兜住, 多付一轮重启)
    if not Path(new_path).is_file():
        fail("目标文件不存在: %s" % new_path)

    # 1) 备份(先备份再改); 秒级时间戳 + pid 防同秒并发覆盖
    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = TOML_PATH.with_name("robotd.toml.bak-%s-p%d" % (ts, os.getpid()))
    shutil.copy2(TOML_PATH, bak)
    if sha256_file(bak) != sha256_file(TOML_PATH):
        fail("备份校验失败", backup=str(bak))

    # 2) 段落限定单键改写(不动其他内容/注释/段外同名键)
    text = TOML_PATH.read_text(encoding="utf-8")
    new_text, found = replace_in_policy_section(text, a.slot, new_path)
    if not found:
        fail("toml [policy] 段内找不到键 %s" % a.slot, backup=str(bak))

    # 3) 校验: 解析成功 + 目标槽位 == 新路径 + 其他槽位不变
    try:
        cfg2 = tomllib.loads(new_text)
    except Exception as e:
        fail("改写后 toml 解析失败: %s" % e, backup=str(bak))
    pol2 = cfg2.get("policy") or {}
    if pol2.get(a.slot) != new_path:
        fail("槽位 %s 改写校验失败(=%r)" % (a.slot, pol2.get(a.slot)), backup=str(bak))
    old_cfg = load_toml()
    for k in SLOTS:
        if k != a.slot and (old_cfg.get("policy") or {}).get(k) != pol2.get(k):
            fail("意外改动其他槽位 %s" % k, backup=str(bak))

    # 4) 原子落盘: tmp(pid 唯一名) → os.replace
    tmp = TOML_PATH.with_name(".robotd.toml.ota-tmp-p%d" % os.getpid())
    tmp.write_text(new_text, encoding="utf-8")
    # 再验一次落盘文件
    if tomllib.loads(tmp.read_text(encoding="utf-8"))["policy"][a.slot] != new_path:
        fail("tmp 文件校验失败", backup=str(bak))
    os.replace(tmp, TOML_PATH)
    if tomllib.load(open(TOML_PATH, "rb"))["policy"][a.slot] != new_path:
        fail("落盘后校验失败", backup=str(bak))

    out({"ok": True, "backup": str(bak), "slot": a.slot, "path": new_path})


# ---------------- rollback ----------------
def cmd_rollback(a):
    acquire_lock()
    bak = Path(a.backup)
    if not bak.is_file():
        fail("备份不存在: %s" % bak)
    # 备份必须可解析且 [policy] 三槽位齐全(拿错备份会把槽位丢光)
    try:
        cfg = tomllib.load(open(bak, "rb"))
    except Exception as e:
        fail("备份文件损坏: %s" % e)
    bak_slots = {k: (cfg.get("policy") or {}).get(k) for k in SLOTS}
    missing = [k for k, v in bak_slots.items() if not v]
    if missing:
        fail("备份缺槽位 %s — 拒绝恢复(防把槽位丢光)" % missing)
    tmp = TOML_PATH.with_name(".robotd.toml.ota-rollback-p%d" % os.getpid())
    shutil.copy2(bak, tmp)
    os.replace(tmp, TOML_PATH)
    if sha256_file(TOML_PATH) != sha256_file(bak):
        fail("回退后哈希与备份不一致")
    out({"ok": True, "restored_from": str(bak), "slots": bak_slots})


# ---------------- health ----------------
def cmd_health(a):
    try:
        r = subprocess.run(["systemctl", "is-active", "robotd"],
                           capture_output=True, text=True, timeout=10)
        active = r.stdout.strip() == "active"
    except Exception as e:
        out({"ok": False, "error": "systemctl 失败: %s" % e})
    d = {"ok": active, "service": r.stdout.strip()}
    # journal: 锚定绝对时刻(--since @epoch, 由部署端在 restart 前取的鸭子时钟),
    # 不用 "-2min -n 150" 相对窗口 — 错误风暴下加载行会被挤出条数窗口(误回滚根源)
    lines_all = []
    try:
        cmd = ["journalctl", "-u", "robotd", "--no-pager", "-o", "cat"]
        cmd += (["--since", "@%d" % int(a.since)] if a.since
                else ["--since", "-2min", "-n", "400"])
        j = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        lines_all = [ln for ln in j.stdout.splitlines() if "policy" in ln.lower()
                     or "panic" in ln.lower() or "error" in ln.lower()]
        d["journal"] = lines_all[-6:]
    except Exception as e:
        d["journal"] = ["journalctl 失败: %s" % e]
    # 新 sha 是否出现在加载行里(全量判断, 与展示截断解耦)
    if a.sha:
        d["sha_seen"] = any(a.sha in ln for ln in lines_all)
    # 总线
    try:
        d["bus"] = bus_status()
        if not active:
            d["ok"] = False
    except Exception as e:
        d["bus_error"] = str(e)[:120]
        d["ok"] = False
    out(d)


# ---------------- stage-verify ----------------
def cmd_stage_verify(a):
    d = Path(a.dir)
    need = ["policy.onnx", "manifest.json", "deployment-runtime.json",
            "deployment-contract.json"]
    for n in need:
        if not (d / n).is_file():
            fail("缺文件 %s" % n)
    sha = sha256_file(d / "policy.onnx")
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    r = json.loads((d / "deployment-runtime.json").read_text(encoding="utf-8"))
    c = json.loads((d / "deployment-contract.json").read_text(encoding="utf-8"))
    out({
        "ok": m.get("onnx_sha256") == sha == r.get("model_sha256")
              and m.get("task") == c.get("task"),
        "sha": sha, "task": m.get("task"), "name": m.get("name"),
        "hash_chain": m.get("onnx_sha256") == sha == r.get("model_sha256"),
        "task_match": m.get("task") == c.get("task"),
    })


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot")
    ap = sub.add_parser("activate")
    ap.add_argument("--slot", required=True)
    ap.add_argument("--path", required=True)
    rp = sub.add_parser("rollback")
    rp.add_argument("--backup", required=True)
    hp = sub.add_parser("health")
    hp.add_argument("--sha", default=None)
    hp.add_argument("--since", default=None, type=float,
                    help="epoch 秒, journal 锚定该时刻(部署端 restart 前取的鸭子时钟)")
    vp = sub.add_parser("stage-verify")
    vp.add_argument("--dir", required=True)
    a = p.parse_args()
    fn = {
        "snapshot": cmd_snapshot, "activate": cmd_activate,
        "rollback": cmd_rollback, "health": cmd_health,
        "stage-verify": cmd_stage_verify,
    }[a.cmd]
    try:
        fn(a)
    except SystemExit:
        raise
    except Exception as e:  # 顶层兜底: 任何异常都输出单行 JSON, 不裸 traceback
        out({"ok": False, "error": "未捕获异常: %s: %s" % (type(e).__name__, e)})


if __name__ == "__main__":
    main()
