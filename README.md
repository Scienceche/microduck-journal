# 🦆 复刻 Microduck —— 项目日志仓库

对 [fanhao375/microduck-replica](https://github.com/fanhao375/microduck-replica) /
[OSHWhub imu_to_servo 团购板](https://oshwhub.com/team_wijnbslh/project_jhgdebrg) 的复刻实作记录：
一块自己焊的 IMU 总线板（STM32G031 + LSM6DSV16X + SN74LVC2G241）、一个 Radxa Zero 3W 主控、
15 颗飞特 HD1910、自训 RL 步态策略，以及把这一切调通的全部血泪。

> 私人仓库 · 每日自动同步自工作目录 `E:\c1349\复刻microduck`

## 当前状态（2026-11-06）

| 子系统 | 状态 |
|---|---|
| IMU 板（总线从机 ID 200 @1Mbps，FT6 协议） | ✅ 完全工作，装向四元数实测校准至 0.00° |
| 固件栈 | ✅ kissqy 官方栈 robotd 0.15.1（control.43）+ 三轮交叉编译热修复 |
| 总线质量 | ✅ 丢包 6-9% → 0.004%（ID12/13、ID34→10 两处时隙碰撞已断开） |
| 整机标定 | ✅ 15 舵机零位/方向/限位（标定台 :8085，robotd 桥接） |
| RL 训练（mjlab/MuJoCo Warp） | ✅ walk5 前进 0.19m/s；VelStand 抗扰站立 99% 存活（未部署） |
| 仿真保真 | ✅ 936g 整机质量 + 202g 电池（**含碰撞盒 70×38×35mm**，2026-11-06 起） |
| 步态/站立（kissqy 自训模型） | ✅ 满电+校向后 10 次冲击（翻滚/前冲/侧切/旋转）不倒 |
| 起身恢复 | ⏳ 测试中：门卫链路已打通，待满电跑 5 次推倒统计 |
| 摄像头（IMX219 CSI） | ✅ 单帧验证通过；mediad WebRTC 待装 GStreamer 栈 |
| 电源 | ⚠️ 2S 软包（7.72V@今晨）；峰值曾拉穿电压致整机重启，起身测试需满电 |

## 目录

```
docs/        调试记录（最重要的文件——完整故障树与修复过程）
firmware/    imu-to-ttl 固件（STM32G031 裸机，make g031-dxl）
hardware/    KiCad 复刻工程 + 元件手册
panel/       中控面板 duck_panel（旧栈）+ calib-panel 标定台（:8085，robotd 桥接）
sync.sh      每日同步脚本（工作目录 → 本仓库 → GitHub）
```

## 硬件速查

- **IMU 板**：团购 PCB + 克隆 STM32G031F8P6 + LSM6DSV16X（换过一颗，旧颗其实没坏）
- **舵机**：飞特 HD1910 ×15，ID 10-14 右腿 / 20-24 左腿 / 30-34 颈头嘴，IMU=200
- **接线**：imu-to-ttl 板自带 4 路舵机总线口 J_L/J_R/J_NECK/J_HEAD（丝印），比外接 hub 电流路径更均衡；
  dxl_hub（3 口无源）留作备用
- **电池**：2S 软包 70×38×35mm + 底座共 202g，装于躯干后下 (-0.027, 0, -0.055)；仿真已含碰撞盒
- 总线：飞特协议 1Mbps 半双工；U3=SN74LVC2G241（注意：**4脚是GND**，3脚=RX出，6脚=TX出）
- 已知坑：克隆 MCU 的 USART **DEM 硬件无效**（固件用软件 DE）；克隆批次 IMU 的 **SFLP 引擎输出乱转**
  （固件改软件 Mahony）；PB8/BOOT0 悬空随机进 bootloader（10k 拉带解决）

## 关键教训（详见调试记录）

1. 两层显示假象叠加可以完美伪装成"传感器坏轴"——用原始字节对质，不要信中间层
2. Mahony 积分项必须与比例项**同号相加**；接反=正反馈发散（主机回放 30 秒实锤）
3. 照抄参考代码写寄存器时，**核对每个地址和值的字面常量**（IF_CFG 0x02≠0x03 害人一整天）
4. 克隆 J-Link 自报产品名 "MS-ARGB"；WinUSB/WCID 一直在正常工作
5. Sync Read 相邻 ID 存在**应答时隙碰撞**（12→13、34→10 两处）：下一 ID 提前 1-2 比特应答污染上一 ID
   校验和。回包按 ID 解码与包序无关——换包序即可修复，硬件无需动
6. 仿真加配重**必须同时加碰撞几何**：只焊质量不焊盒子，策略对物理障碍"无感"，真机上脚怼进电池
7. IMU 装向校验要看**全重力向量**：gz 在小角度下与 0° 无异（cos8°=0.990），gx/gy 才暴露偏差
8. robotd 的标定/运动模式写进 systemd drop-in，**重启会继承**——"策略没反应"先查模式；
   init 不是起身控制器，趴姿 init 起不来

