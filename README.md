# 🦆 复刻 Microduck —— 项目日志仓库

对 [fanhao375/microduck-replica](https://github.com/fanhao375/microduck-replica) /
[OSHWhub imu_to_servo 团购板](https://oshwhub.com/team_wijnbslh/project_jhgdebrg) 的复刻实作记录：
一块自己焊的 IMU 总线板（STM32G031 + LSM6DSV16X + SN74LVC2G241）、一个 Radxa Zero 3W 主控、
一套 Web 中控与 IMU 仪表台，以及把这一切调通的全部血泪。

> 私人仓库 · 每日自动同步自工作目录 `E:\c1349\复刻microduck`

## 当前状态（2026-10-12）

| 子系统 | 状态 |
|---|---|
| IMU 板（总线从机 ID 200 @1Mbps） | ✅ 完全工作 |
| 姿态融合（软件 Mahony @100Hz） | ✅ 横滚/俯仰锁定重力（静止 p99 = 0.01° / 0.62°），偏航漂移 ~8°/分（6轴物理极限） |
| HAT 链路 / Radxa 主控 | ✅ ttyS2 @1M |
| Web 中控面板（:8080） | ✅ 姿态/舵机/姿态库/PWA |
| IMU 仪表台（:8081，社区套件移植） | ✅ WebGL 3D + 瀑布图，50~100Hz |
| 舵机联调 / 整机组装 | ⏳ 待接电池 |
| 第二块空白板激活 | ⏳ 总线 bootloader 方案已备 |

## 目录

```
docs/        调试记录（最重要的文件——完整故障树与修复过程）
firmware/    imu-to-ttl 固件（STM32G031 裸机，make g031-dxl）
hardware/    KiCad 复刻工程 + 元件手册
panel/       中控面板 duck_panel + IMU 仪表台 imu_scope（社区套件适配版）
sync.sh      每日同步脚本（工作目录 → 本仓库 → GitHub）
```

## 硬件速查

- **IMU 板**：团购 PCB + 克隆 STM32G031F8P6 + LSM6DSV16X（换过一颗，旧颗其实没坏）
- 总线：飞特协议 1Mbps 半双工；U3=SN74LVC2G241（注意：**4脚是GND**，3脚=RX出，6脚=TX出）
- 已知坑：克隆 MCU 的 USART **DEM 硬件无效**（固件用软件 DE）；克隆批次 IMU 的 **SFLP 引擎输出乱转**
  （固件改软件 Mahony）；PB8/BOOT0 悬空随机进 bootloader（10k 拉带解决）

## 关键教训（详见调试记录）

1. 两层显示假象叠加可以完美伪装成"传感器坏轴"——用原始字节对质，不要信中间层
2. Mahony 积分项必须与比例项**同号相加**；接反=正反馈发散（主机回放 30 秒实锤）
3. 照抄参考代码写寄存器时，**核对每个地址和值的字面常量**（IF_CFG 0x02≠0x03 害人一整天）
4. 克隆 J-Link 自报产品名 "MS-ARGB"；WinUSB/WCID 一直在正常工作
