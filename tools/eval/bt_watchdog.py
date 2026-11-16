#!/usr/bin/env python3
"""bt_watchdog — AIC8800 BT 固件卡死自愈 + padd 设备绑定自检（2026-11-08）

背景：当晚 hci0 两次固件级卡死（dmesg: "command 0x0c1a tx timeout"），只能整机重启；
且手柄每次休眠回连后 padd 常落在原始 evdev 节点上（X/Y 功能颠倒）。

每 20 秒两项检查：
  1) BT 健康：hci0 存在 && `bluetoothctl show` 在 8s 内给出 Powered: yes。
     失败走恢复阶梯（每级最多尝试一次，连续失败升级）：
       T1 rfkill block/unblock bluetooth（轻弹）
       T2 stop bluetooth → 重载 hci_uart + aic8800_btlpm（不碰 aic8800_fdrv/WiFi）→ start bluetooth
       T3 持续失败 ≥6 分钟 → 自动重启整机（最后的手段）
  2) padd 绑定：padd 进程打开的 event 节点若是原始 GameSir-* 节点、而虚拟 Xbox 节点已存在
     → systemctl restart padd（30s 冷却，避免和桥的 5s 兜底扫描打架）。

开机保护：hci0 在该板上约 73s 才出现，服务启动后前 150 秒只记录不动作。
回退路径：systemctl disable --now bt-watchdog（不改任何其他配置）。
"""
import os
import re
import subprocess
import time

# journalctl 在子进程管道里仍可能带 ANSI 颜色码（会把 "driving pad=" 撕开），先洗掉
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

INTERVAL = 20
BOOT_GRACE = 150          # hci0 ~73s 才出现，前 150s 只观察
T2_AFTER = 2              # 连续失败 N 个周期后升到 T2
T3_AFTER = 18             # 连续失败 N 个周期后整机重启（含探测超时，实际约 8 分钟）
PADD_COOLDOWN = 30
PADD_RESTART_CAP = 3      # 每次开机最多自动重启 padd 3 次，防死循环
padd_restarts = 0


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def run(args, timeout=12):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr)
    except subprocess.TimeoutExpired:
        return 124, "TIMEOUT"
    except OSError as e:
        return 127, str(e)


def bt_healthy():
    """真 HCI 交互探测：bluetoothctl show 会返回缓存假阳性（芯片死了也说 Powered: yes），
    改用 `power on`——它强制一次 mgmt 写操作，芯片假死时会挂起超时。"""
    if not os.path.exists("/sys/class/bluetooth/hci0"):
        return False, "hci0 不存在"
    rc, out = run(["bluetoothctl", "power", "on"], timeout=6)
    if rc == 0 and ("Changing power on succeeded" in out or "already" in out.lower()):
        return True, "ok"
    return False, f"probe rc={rc} {out[:120].strip()}"


def recover_t1():
    log("T1 rfkill bounce bluetooth")
    run(["rfkill", "block", "bluetooth"])
    time.sleep(2)
    run(["rfkill", "unblock", "bluetooth"])


def recover_t2():
    log("T2 重载 BT 串口栈（不动 WiFi 驱动）")
    run(["systemctl", "stop", "bluetooth.service"], timeout=20)
    time.sleep(1)
    run(["modprobe", "-r", "aic8800_btlpm"])
    run(["modprobe", "-r", "hci_uart"])
    time.sleep(1)
    run(["modprobe", "hci_uart"])
    run(["modprobe", "aic8800_btlpm"])
    time.sleep(2)
    run(["systemctl", "start", "bluetooth.service"], timeout=20)
    time.sleep(3)


def recover_t3():
    log("T3 持续失败达阈值——执行整机自愈重启")
    time.sleep(2)
    run(["reboot"])


def input_nodes():
    """eventX -> 设备名。走 sysfs（/sys/class/input/input*/name），比 procfs 文本解析稳。"""
    nodes = {}
    base = "/sys/class/input"
    try:
        entries = os.listdir(base)
    except OSError:
        return nodes
    for entry in entries:
        if not entry.startswith("input"):
            continue
        try:
            with open(f"{base}/{entry}/name") as f:
                name = f.read().strip()
        except OSError:
            continue
        try:
            for ev in os.listdir(f"{base}/{entry}"):
                if ev.startswith("event"):
                    nodes[f"/dev/input/{ev}"] = name
        except OSError:
            pass
    return nodes


def padd_fix(virtual_exists, raw_exists, cooldown_until):
    """padd 最近一次绑定的是原始节点而虚拟节点已在 → 重启 padd。返回新的冷却截止。

    双重闸门（防无限重启循环）：
    (a) raw 节点必须当前真实存在——pad 离线时 journal 里的 raw 绑定行是陈旧的，
        此时重启 padd 毫无意义且会变成 30 秒一次的无限重启；
    (b) 每次开机最多 PADD_RESTART_CAP 次。
    注意不能用 /proc/PID/fd 判断：gilrs 枚举所有手柄节点会同时打开原始+虚拟，
    打开≠驱动。权威信号是 padd 自己的 journal：`pad connected — driving pad=NAME`。
    所有时长用 time.monotonic()（板无 RTC，墙钟会被 NTP 跳变）。
    """
    global padd_restarts
    if not virtual_exists or not raw_exists:
        return cooldown_until
    if padd_restarts >= PADD_RESTART_CAP:
        return cooldown_until
    # -g 让 journald 服务端过滤（全量输出会超时：活跃使用时 padd 日志量很大）
    rc, out = run(["journalctl", "-u", "padd", "-b", "-g", "pad connected",
                   "-o", "cat", "--no-pager"], timeout=15)
    if rc != 0:
        return cooldown_until
    out = ANSI.sub("", out)
    last = None
    for line in out.splitlines():
        if "pad connected" in line and "driving pad=" in line:
            last = line
    if last is None or "Xbox 360 Controller" in last:
        return cooldown_until  # 已在虚拟设备上，无需干预
    if time.monotonic() < cooldown_until:
        return cooldown_until
    log("padd 绑定在原始节点而虚拟节点已存在 → 重启 padd")
    padd_restarts += 1
    run(["systemctl", "restart", "padd"], timeout=30)
    return time.monotonic() + PADD_COOLDOWN


def main():
    log(f"bt_watchdog 启动，开机保护 {BOOT_GRACE}s，周期 {INTERVAL}s")
    start = time.monotonic()
    fails = 0
    acted_t = -1          # 本轮失败已执行的最高阶梯
    t3_fired = False      # T3 只发一次 reboot，防止日志刷屏/重复调用
    padd_cooldown = 0.0
    while True:
        time.sleep(INTERVAL)
        uptime = time.monotonic() - start
        ok, why = bt_healthy()
        if not ok and uptime < BOOT_GRACE:
            log(f"开机保护期内（{int(uptime)}s），BT 暂不可用不动作：{why}")
            continue
        if ok:
            if fails:
                log(f"BT 恢复正常（此前连续失败 {fails} 次）")
            fails, acted_t = 0, -1
            t3_fired = False
        else:
            fails += 1
            if fails == 1 or fails % 3 == 0:
                log(f"BT 异常（连续第 {fails} 次）：{why}")
            if fails >= T3_AFTER:
                if not t3_fired:
                    t3_fired = True
                    recover_t3()
                continue
            tier = 1 if fails < T2_AFTER else 2
            if tier > acted_t:
                acted_t = tier
                (recover_t1 if tier == 1 else recover_t2)()
        # padd 绑定检查（BT 健康与否都查：虚拟节点存在才有意义）
        nodes = input_nodes()
        virtual_exists = any("Xbox 360" in n for n in nodes.values())
        raw_exists = any(("GameSir" in n) and ("Xbox 360" not in n) for n in nodes.values())
        padd_cooldown = padd_fix(virtual_exists, raw_exists, padd_cooldown)


if __name__ == "__main__":
    main()
