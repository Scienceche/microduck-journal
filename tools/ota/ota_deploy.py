#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OTA 原子部署编排器(PC 端)。

用法:
  python ota_deploy.py <本地包目录> --slot walk [--expect-task Vel] [--yes]

本地包目录需含: policy.onnx / manifest.json / deployment-runtime.json / deployment-contract.json

流程(守则第2节可执行版):
  预检(ping+SSH+总线+低电压熔断) → 上传 .staging → 鸭子端二次哈希校验
  → 原子切换(models/ rename + toml 原子改写, 先备份) → 重启
  → 健康检查(服务+journal 策略行+总线探针+新 sha 出现)
  → 任一失败: 自动回滚 toml 备份 → 重启 → 复检旧模型

退出码: 0=成功  1=失败已回滚  2=失败且回滚也失败(需人工)
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

DUCK = "root@192.168.31.96"
KEY = r"C:\Users\c1349\.ssh\id_ed25519_duck"
SSH_BASE = ["ssh", "-i", KEY, "-o", "ConnectTimeout=15",
            "-o", "StrictHostKeyChecking=no", DUCK]
SCP_BASE = ["scp", "-i", KEY, "-o", "ConnectTimeout=15",
            "-o", "StrictHostKeyChecking=no"]
HELPER_LOCAL = Path(__file__).with_name("ota_helper.py")
HELPER_REMOTE = "/opt/duck/ota/ota_helper.py"
MODELS_ROOT = "/opt/robot/feetech-ft5-r5/models"
STAGING_ROOT = "/opt/robot/feetech-ft5-r5/.staging"

steps = []  # (name, ok, detail)
_CTX = {"backup": None, "sha": None, "slot": None,
        "verified": False, "state_written": False}


def write_state(result, extra=None):
    if _CTX["state_written"]:
        return
    _CTX["state_written"] = True
    st = {"at": time.strftime("%Y-%m-%d %H:%M:%S"),
          "slot": _CTX.get("slot"), "sha": (_CTX.get("sha") or "")[:12],
          "result": result,
          "pass": sum(1 for _, ok in steps if ok), "total": len(steps)}
    if _CTX.get("backup"):
        st["backup"] = _CTX["backup"]
    if extra:
        st.update(extra)
    try:
        Path(r"C:\duck_work\deploy_state.json").write_text(
            json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def emergency_rollback():
    """未捕获异常时的最后防线: 已切 toml 且未验证成功 → 回滚到部署前状态。
    回滚恢复的正是部署前内容, 方向永远安全; 已验证成功则不动。"""
    if not _CTX.get("backup") or _CTX.get("verified"):
        return
    print("\n!! 异常中止且 toml 可能已改, 紧急回滚: %s" % _CTX["backup"], flush=True)
    try:
        rb = helper("rollback", backup=_CTX["backup"], timeout=30)
        ssh("systemctl restart robotd", timeout=40)
        print("   紧急回滚 %s" % ("成功" if rb.get("ok") else
                                  "失败(需人工): %s" % rb.get("error")), flush=True)
    except Exception as e:
        print("   紧急回滚通道失败(需人工): %r" % e, flush=True)


def step(name, ok, detail=""):
    steps.append((name, ok))
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                         (" — " + detail) if detail else ""), flush=True)


def ssh(cmd, timeout=60):
    # 鸭子端输出 UTF-8(中文错误消息), Windows 默认 cp936 strict 解码会概率性
    # UnicodeDecodeError —— 显式 UTF-8 + replace(与 ping 的 GBK 处理同源教训)
    r = subprocess.run(SSH_BASE + [cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def ssh_json(cmd, timeout=60):
    """执行远程命令并解析最后一行 JSON; 通道故障(超时/空输出/非 JSON)显式报错。"""
    try:
        rc, out, err = ssh(cmd, timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "SSH 通道超时"}
    if rc != 0:
        return {"ok": False, "error": "SSH rc=%d %s" % (rc, err.strip()[-120:])}
    line = out.strip().splitlines()[-1] if out.strip() else ""
    try:
        return json.loads(line)
    except Exception:
        return {"ok": False, "error": "非 JSON 输出: %r" % line[:120]}


def helper(sub, timeout=60, **kw):
    args = ""
    for k, v in kw.items():
        if v is not None:
            args += " --%s '%s'" % (k.replace("_", "-"), v)
    return ssh_json("python3 %s %s%s" % (HELPER_REMOTE, sub, args), timeout)


def sha256_local(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def restart_and_health(sha12=None, restarts=2, poll_s=30):
    """重启 robotd 一次 → 轮询健康(3s 间隔, 不再 restart, 给慢启动留时间);
    整轮未过 → 最后再 restart 一轮。返回 health dict(ok 字段为准)。

    反例(已修): 曾实现为"每轮失败就再 restart"——robotd 启动>4s 时会被
    反复打死永远起不来。journal 锚定 restart 前的鸭子 epoch(防相对窗口
    在错误风暴下截断加载行 → 误回滚)。
    """
    h = {"ok": False}
    for attempt in range(restarts):
        since_epoch = None
        try:
            rc, out, _ = ssh("date +%s", timeout=15)
            if rc == 0:
                since_epoch = float(out.strip())
        except subprocess.TimeoutExpired:
            pass
        try:
            rc, _, _ = ssh("systemctl restart robotd", timeout=40)
            if rc != 0:
                print("    restart 命令 rc=%d, 继续观察服务状态" % rc, flush=True)
        except subprocess.TimeoutExpired:
            print("    restart 命令超时(鸭子端可能仍在执行), 继续轮询", flush=True)
        t_end = time.monotonic() + poll_s
        while time.monotonic() < t_end:
            time.sleep(3)
            try:
                h = helper("health", sha=sha12, since=since_epoch, timeout=40)
            except subprocess.TimeoutExpired:
                continue
            if h.get("ok") and (sha12 is None or h.get("sha_seen")):
                return h
        print("    健康轮询未过(第 %d 轮): %s" % (
            attempt + 1, json.dumps(h, ensure_ascii=False)[:200]), flush=True)
    return h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pkg", help="本地部署包目录")
    ap.add_argument("--slot", default="walk", choices=["walk", "stand", "sitstand"])
    ap.add_argument("--expect-task", default=None,
                    help="校验 manifest.task 应包含此子串(如 Vel / StandUp), 防拿错包")
    ap.add_argument("--yes", action="store_true", help="跳过确认")
    ap.add_argument("--force-voltage-unknown", action="store_true",
                    help="电压无读数时仍继续(默认拒绝)")
    ap.add_argument("--force-standing", action="store_true",
                    help="策略正在驱动(鸭子站立)时仍重启(默认拒绝 — restart 会瘫倒摔机)")
    a = ap.parse_args()

    pkg = Path(a.pkg).resolve()
    _CTX["slot"] = a.slot
    print("==== OTA 部署: %s → 槽位 %s ====" % (pkg.name[:16], a.slot))

    # ---- 1. 本地包校验 ----
    for n in ("policy.onnx", "manifest.json", "deployment-runtime.json",
              "deployment-contract.json"):
        if not (pkg / n).is_file():
            print("本地包缺 %s" % n); sys.exit(2)
    sha = sha256_local(pkg / "policy.onnx")
    _CTX["sha"] = sha
    m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    r = json.loads((pkg / "deployment-runtime.json").read_text(encoding="utf-8"))
    c = json.loads((pkg / "deployment-contract.json").read_text(encoding="utf-8"))
    step("本地哈希链 manifest=runtime=实际",
         m.get("onnx_sha256") == sha == r.get("model_sha256"), sha[:12])
    # task 缺失 = None==None 会静默通过 —— 显式拒绝
    task_ok = (m.get("task") and c.get("task")
               and m.get("task") == c.get("task"))
    if a.expect_task:
        task_ok = task_ok and a.expect_task in str(m.get("task", ""))
    step("task 契约一致(%s)" % str(m.get("task") or "缺失")[:40], task_ok)
    if not all(ok for _, ok in steps):
        sys.exit(2)

    # ---- 2. 鸭子预检 ----
    # Windows ping 输出 GBK(中文系统), 显式编码避免读取线程报错
    ping_r = subprocess.run(["ping", "-n", "1", "-w", "3000", "192.168.31.96"],
                            capture_output=True, text=True,
                            encoding="gbk", errors="ignore")
    rc = ping_r.returncode
    step("鸭子 ping 可达", rc == 0)
    if rc != 0:
        print("鸭子不在线(断电?), 无法部署"); sys.exit(2)

    rc, out, err = ssh("echo ok", timeout=20)
    step("SSH 可用", rc == 0)
    if rc != 0:
        sys.exit(2)

    # helper 自举(不存在或版本过期则上传 — 比对 sha256; 上传后回读校验+旧版备份)
    local_helper_sha = sha256_local(HELPER_LOCAL)
    rc, out, _ = ssh("sha256sum %s 2>/dev/null | grep -q %s" % (HELPER_REMOTE, local_helper_sha))
    if rc != 0:
        ssh("mkdir -p %s" % HELPER_REMOTE.rsplit("/", 1)[0])
        ssh("test -f %s && cp %s %s.bak || true" % (HELPER_REMOTE, HELPER_REMOTE, HELPER_REMOTE))
        r2 = subprocess.run(SCP_BASE + [str(HELPER_LOCAL), "%s:%s" % (DUCK, HELPER_REMOTE)],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=300)
        ok_up = r2.returncode == 0
        if ok_up:
            rc2, out2, _ = ssh("sha256sum %s | grep -q %s" % (HELPER_REMOTE, local_helper_sha))
            ok_up = rc2 == 0
        step("上传/更新 ota_helper.py(回读校验)", ok_up, local_helper_sha[:12])
        if not ok_up:
            sys.exit(2)
    snap = helper("snapshot")
    step("读取当前槽位", snap.get("ok") is True,
         json.dumps(snap.get("slots", {}), ensure_ascii=False)[:160])
    if not snap.get("ok"):
        sys.exit(2)
    old_path = (snap.get("slots") or {}).get(a.slot)

    h0 = helper("health")
    bus = h0.get("bus") or {}
    step("robotd 当前健康", h0.get("ok") is True)
    step("总线 15 舵机在线", bus.get("servos") == 15, str(bus))
    V = bus.get("V_min") or bus.get("V")
    if V is None:
        # 无读数 ≠ 电压正常 — 历史事故"低电压重启整机跌落"的防线不能静默放行
        low_v = not a.force_voltage_unknown
        step("电压熔断(≥7.4V)", not low_v,
             "无读数, 拒绝部署(可 --force-voltage-unknown 强制)")
    else:
        if 7.4 <= V < 7.8:
            print("  ⚠ 电压 %.2fV 在 7.4-7.8V 之间: 可部署, 但部署后避免立即跑大动作" % V)
        low_v = V < 7.4
        step("电压熔断(≥7.4V)", not low_v, "%.2fV — 低电压禁止部署/重启" % V)
    # 姿态防线: 策略正在驱动时 restart = 鸭子瘫倒摔机(守则第 6 节)
    if bus.get("policy_enabled"):
        step("鸭子静息(策略未驱动)", a.force_standing,
             "policy_enabled=True — restart 会摔机, 需 --force-standing")
    if not all(ok for _, ok in steps):
        sys.exit(2)

    # ---- 3. 确认 ----
    print("\n  即将部署: %s" % sha[:12])
    print("    名称: %s" % m.get("name", "?"))
    print("    槽位 %s: %s" % (a.slot, (old_path or "?")[-40:],))
    print("            → %s" % ("%s/%s/policy.onnx" % (MODELS_ROOT, sha))[-40:])
    if not a.yes:
        ans = input("  确认部署? [y/N] ").strip().lower()
        if ans != "y":
            print("已取消"); sys.exit(0)

    # ---- 4. 上传到暂存区(tar 整包, 覆盖子目录如 upstream-params/) ----
    target_dir = "%s/%s" % (MODELS_ROOT, sha)
    stage_dir = "%s/%s" % (STAGING_ROOT, sha)
    new_full = "%s/policy.onnx" % target_dir
    rc, out, _ = ssh("test -d %s" % target_dir)
    if rc == 0:
        # 已存在 ≠ 内容正确(历史残留/人为错放会静默洗白) — 仍鸭子端实算哈希
        sv = helper("stage-verify", dir=target_dir, timeout=60)
        step("目标包已存在(哈希复核)", sv.get("ok") is True,
             "sha=%s" % str(sv.get("sha", ""))[:12])
        if not sv.get("ok"):
            sys.exit(2)
    else:
        ssh("rm -rf %s && mkdir -p %s" % (stage_dir, stage_dir))
        import tarfile
        import tempfile
        tar_path = Path(tempfile.gettempdir()) / ("ota_%s.tar.gz" % sha[:12])
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(str(pkg), arcname=".")
        r2 = subprocess.run(
            SCP_BASE + [str(tar_path), "%s:%s/pkg.tar.gz" % (DUCK, stage_dir)],
            capture_output=True, text=True)
        tar_path.unlink(missing_ok=True)
        if r2.returncode != 0:
            step("上传 tar 包", False, r2.stderr[-100:]); sys.exit(2)
        rc, out, err = ssh("tar -xzf %s/pkg.tar.gz -C %s && rm %s/pkg.tar.gz"
                           % (stage_dir, stage_dir, stage_dir), timeout=60)
        step("解包到暂存区", rc == 0, err.strip()[-100:])
        if rc != 0:
            ssh("rm -rf %s" % stage_dir); sys.exit(2)
        sv = helper("stage-verify", dir=stage_dir, timeout=60)
        step("暂存区二次哈希校验", sv.get("ok") is True,
             "鸭子端实算 sha=%s" % str(sv.get("sha", ""))[:12])
        if not sv.get("ok"):
            ssh("rm -rf %s" % stage_dir); sys.exit(2)
        # 原子进场: 同文件系统 rename
        rc, out, err = ssh("mv %s %s" % (stage_dir, target_dir), timeout=30)
        step("原子移入 models/", rc == 0, err.strip()[:80])
        if rc != 0:
            sys.exit(2)

    # ---- 5. 切换 toml + 重启 + 健康(幂等: 已指向目标则只体检不重启) ----
    backup = None
    if old_path == new_full:
        step("槽位已指向目标包(跳过切换+重启)", True)
        h = helper("health", timeout=40)
    else:
        act = helper("activate", slot=a.slot, path=new_full, timeout=30)
        step("toml 原子切换(已备份)", act.get("ok") is True,
             "备份: %s" % str(act.get("backup", ""))[-40:])
        backup = act.get("backup")
        _CTX["backup"] = backup
        if not act.get("ok"):
            # activate 失败但已有备份 → toml 可能已改(落盘后校验失败的路径),
            # 必须先回滚再退出, 不能裸 exit(2) 留下"已改未验证"状态
            if backup:
                print("!! activate 失败且 toml 可能已改, 先回滚: %s" % backup)
                rb = helper("rollback", backup=backup, timeout=30)
                step("失败路径回滚 toml", rb.get("ok") is True)
            sys.exit(2)
        h = restart_and_health(sha12=sha[:12])
        step("journal 出现新模型加载行", h.get("sha_seen") is True)
    step("部署后健康检查(服务+总线)", h.get("ok") is True,
         json.dumps(h.get("bus", {}), ensure_ascii=False)[:120])
    if h.get("ok"):
        # 与预检同口径: 服务 active 不代表总线健康("不信任端口 200=正常")
        b = h.get("bus") or {}
        step("部署后总线完整(15 舵机+反馈新鲜)",
             b.get("servos") == 15 and b.get("fresh") is True, str(b)[:100])
        # 注意: 不检查 policy_enabled — robotd 重启后策略由用户显式 enable,
        # 该字段为 False 属正常部署后状态

    # ---- 6. 失败 → 自动回滚(仅当本次真的改过 toml) ----
    if not all(ok for _, ok in steps):
        if backup is None:
            # 幂等路径失败: 什么都没改, 无需回滚, 直接报失败
            write_state("failed-no-change")
            print("\n==== 失败(未改动任何配置, 无需回滚) ====")
            sys.exit(2)
        print("\n!! 部署失败, 自动回滚: %s" % backup)
        rb = helper("rollback", backup=backup, timeout=30)
        step("回滚 toml", rb.get("ok") is True)
        # 回滚后验证"旧模型路径"真的被加载(从 old_path 提取旧 sha), 而不是
        # 仅服务 active — 否则内存里可能还跑着失败的新模型
        old_sha12 = None
        if old_path:
            old_sha12 = old_path.rstrip("/").rsplit("/", 2)[-2][:12]
        h2 = restart_and_health(sha12=old_sha12, restarts=1, poll_s=40)
        step("回滚后旧模型健康", h2.get("ok") is True,
             json.dumps(h2.get("bus", {}), ensure_ascii=False)[:120])
        write_state("rolled-back")
        # 退出码: 回滚两步(回滚 toml + 回滚后健康)都过 → 1(失败但已安全回滚);
        # 任一失败 → 2(需人工)。部署失败本身的步不计入。
        rb_steps = [ok for name, ok in steps if name.startswith("回滚")]
        print("\n==== 回滚完成, 旧模型已恢复 ====")
        sys.exit(1 if (rb_steps and all(rb_steps)) else 2)

    # ---- 7. 成功 ----
    _CTX["verified"] = True
    write_state("deployed", {"task": m.get("task"),
                             "walk_path": "%s/policy.onnx" % target_dir,
                             "V": (h.get("bus") or {}).get("V")})
    print("\n==== OTA 部署成功: %s → %s槽 (%s) ====" % (sha[:12], a.slot, m.get("name", "?")))
    if backup:
        print("回滚路径(如需手动): ssh 鸭子 python3 %s rollback --backup %s" % (HELPER_REMOTE, backup))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        if not _CTX["state_written"]:
            write_state("aborted")
        raise
    except subprocess.TimeoutExpired:
        print("SSH 超时 — 鸭子可能掉线(先 ping)", flush=True)
        write_state("aborted-timeout")
        emergency_rollback()
        sys.exit(2)
    except Exception as e:
        import traceback
        traceback.print_exc()
        write_state("aborted-exception", {"error": repr(e)[:200]})
        emergency_rollback()
        sys.exit(2)
