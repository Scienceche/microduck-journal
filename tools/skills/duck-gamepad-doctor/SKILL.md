---
name: duck-gamepad-doctor
description: 鸭子手柄（GameSir T4n Lite / GameSir-Nova Lite）蓝牙配对、输入绑定、开机竞态、AIC8800 蓝牙卡死急救专家。当用户报告手柄连不上、配对失败、按了没反应、padd 不响应、蓝牙芯片卡死、HID 无数据时使用。含 2026-11-15 六小时实战全部经验。
---

# 鸭子手柄急救专家

> 基于 2026-11-15 六小时实战（从"手柄无效"到"完整链路恢复"）。
> 硬件: Radxa Zero 3W + AIC8800 蓝牙 + GameSir T4n Lite
> 关键文档: `microduck-journal/docs/调试记录-2026-11-15-蓝牙手柄.md`

## 快速诊断流程

### 1. 手柄按了没反应 → 先查 padd 绑定（最常见根因）

```bash
# padd 在读哪个节点？
PADD_PID=$(systemctl show padd -p MainPID --value)
ls -la /proc/$PADD_PID/fd | grep input
```

**正确**: 应看到 `event6`（虚拟 Xbox "Xbox 360 Controller"）
**错误**: 只看到 `event4`（物理手柄，被桥 EVIOCGRAB 独占，读不到数据）

**修复（开机竞态）**:
```bash
bluetoothctl disconnect 99:16:56:C3:54:4F
systemctl restart padd
sleep 3
bluetoothctl connect 99:16:56:C3:54:4F
```

**根治**: 桥补丁使虚拟节点持久存在（见下文"桥补丁"节）。

### 2. 蓝牙连接断开 → 查芯片状态

```bash
hciconfig hci0 | head -1       # 应显示 "UP RUNNING"
bluetoothctl info 99:16:56:C3:54:4F | grep -E 'Paired|Connected'
```

**hci0 不存在或 DOWN** → AIC8800 假死，运行看门狗或手动恢复:
```bash
/usr/bin/aic-bluetooth  # 含 -r 硬件复位
hciconfig hci0 up
systemctl restart bluetooth  # ⚠️ 只在 aic-bluetooth 成功后才安全
```

### 3. 配对失败 → 交互式会话配对

**三要素**（缺一不可）:
1. `hcitool scan` 发现设备（bluetoothctl scan 看不到经典蓝牙！）
2. `hcitool cc <MAC>` 先建 ACL（让设备进入 bluetoothctl 数据库）
3. **交互式 bluetoothctl 会话**（agent NoInputNoOutput + pair 同进程）

**配对网页**: `http://192.168.31.96:8086`（鸭子端 bt-pair-page 服务）

**手动配对脚本**: 鸭子 `/opt/duck-calib/bt_pair_proper.py`

## 绝对禁止的操作

| 操作 | 后果 |
|---|---|
| `systemctl restart bluetooth` | AIC8800 丢 hci0 直到重启（已知问题） |
| `bluetoothctl remove <MAC>` 删有效配对 | AIC8800 可能永远无法重新认证 |
| `Privacy = device` 写入 main.conf | 部分板子加密失败、适配器崩溃 |
| 手柄恢复出厂（A+B+X+Y+HOME）| MAC 漂移 + bond 槽位残留 + 模式混乱 |
| `hciattach` 手动重启（不带 -r） | UART 状态脏，SDP/L2CAP 持续失败 |

## AIC8800 已知怪癖

1. **MAC 地址漂移**: 同一手柄可能以 C3:54:4F / C4:54:4F / C8:54:4F 出现
   - C3:54:4F = 正常 Android 模式（经典蓝牙）
   - C4:54:4F = Pro Controller 模式（BLE，恢复出厂后）
   - C8:54:4F = OTA 模式（BLE，配对残留）
2. **bluetoothctl scan 不拾取经典蓝牙设备** → 用 `hcitool scan`
3. **假死窗口**: 偶发 `ACL packet for unknown handle`，看门狗自动恢复
4. **bond 槽位**: GameSir 只有一个 → 先配手机再删才能释放

## 输入链路架构

```
物理手柄(GameSir-Nova Lite)
  → 蓝牙 HID → /dev/input/event4 (被桥 EVIOCGRAB 独占)
  → gamesir-xboxd 桥 → /dev/input/event6 (虚拟 Xbox 360 Controller)
  → padd → /run/robotd.sock → robotd
```

**关键**: padd 必须绑 event6（虚拟 Xbox），不是 event4（物理手柄）。

## 桥补丁（虚拟节点持久化）

位置: 鸭子 `/root/patch_gamesir_persistent_uinput.py`
备份: `/usr/local/sbin/gamesir-xboxd.bak-persistent-20261115`

效果: 虚拟 Xbox 节点在桥启动时创建、进程存活期间不销毁。
OTA 覆盖桥后需重跑补丁脚本。

## 蓝牙配置（当前生效）

| 文件 | 设置 | 值 |
|---|---|---|
| /etc/bluetooth/input.conf | UserspaceHID | **false**（内核 hidp 路径）|
| /etc/bluetooth/input.conf | ClassicBondedOnly | **false**（允许无加密 HID）|
| /etc/bluetooth/main.conf | AlwaysPairable | **true** |
| /etc/udev/rules.d/99-aic8800-uart.rules | ttyS 电源 | **on**（防休眠死锁）|

## 服务清单

| 服务 | 功能 | 状态 |
|---|---|---|
| bt-pair-page (:8086) | 配对网页 | 开机自启 |
| bt-watchdog | 蓝牙探活 + 自动恢复 | 开机自启 |
| gamesir-xboxd (修补版) | 物理手柄 → 虚拟 Xbox 桥 | 开机自启 |
| padd | 虚拟 Xbox → robotd 控制 | 开机自启 |
