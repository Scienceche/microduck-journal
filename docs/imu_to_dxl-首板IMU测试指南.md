# imu_to_dxl（OSHWhub `imu_to_servo`）首板 IMU 测试指南

> 适用对象：OSHWhub 上打样、已焊接的 `imu_to_servo` 板（STM32G031F8P6 + LSM6DSV16X +
> SN74LVC2G241，即 microduck-replica 仓库的 `imu_to_dxl` 设计）。
> 固件：本仓库（`imu-to-ttl/firmware`），**必须用 `make g031-dxl` 目标**（见 §3.2，
> 差别是这块板的收发方向脚 DE 在 PA1，而 imu_to_ttl 板是 AUTO 总线无方向脚）。
> OSHWhub 项目本身**没有公开固件**（2026-10-02 评论区还在求固件），固件以本仓库为准。

## 0. 需要的工具

| 工具 | 用途 | 替代 |
|---|---|---|
| 万用表 | 短路/通断/电压 | 必备 |
| 可调电源 8.4 V 限流 100 mA | 上电（最稳） | 2S 电池 + 手不离插头 |
| ST-Link V2（或任意 SWD 调试器） | 烧固件 | CMSIS-DAP / J-Link |
| USB-TTL 适配器（CH340/CP2102 均可，3.3 V 优先） | 总线测试 | 飞特 URT 调试器 + FD 软件（`tools/飞特/`）|
| 电脑装 Python 3 + pyserial | 跑 `firmware/tools/bus_test.py` | `pip install pyserial` |

## 1. 烧固件之前先做三件事（都是一分钟级别）

### 1.1 目视（放大镜/手机微距）

重点看三颗 IC 有没有连锡：U1（TSSOP-20 脚距 0.65 mm）、U2（LGA-14 **底部有焊盘**，
必须热风/回流，烙铁补四边即可）、U3（VSSOP-8 脚距 0.5 mm 最密）。
再看阻容有没有立碑、偏移。

### 1.2 不带电短路测量

万用表电阻档，表笔直接怼连接器针脚（针脚识别见 §1.3）：

| 测哪里 | 合格 |
|---|---|
| 总线 VDD ↔ GND | >100 kΩ（电容充电后稳定读数） |
| J3.6（+3V3）↔ J3.1（GND） | >10 kΩ（有 LDO 输出电容，充电后稳定） |

### 1.3 用万用表判别总线连接器三根针（必做，别猜丝印）

板上 EH 2.5 mm 座子如果是按 `imu_to_dxl` 原版布的，针序是 **1=GND / 2=VDD / 3=DATA**
（Dynamixel 侧线序）。但 OSHWhub 页面同时有"飞特版"，**以实物为准**，蜂鸣档三步判：

1. 和 **J3.1（GND 排针第 1 针）** 通的 → GND；
2. 和 **F1 保险丝上游**（或 J3.6 +3V3 的 LDO 输入侧）通的 → VDD；
3. 剩下那根 → DATA。它对 +3V3 应有 **~10 kΩ**（板内 R5 上拉），对 GND 不通。

> 记下结论。后面供电、接 USB-TTL、上整机全用它。

### 1.4 上电空板检查（确认 DE 静默 + 3.3 V）

按 §1.3 判明的针脚给 VDD/GND 上电（8.0~8.4 V，限流 100 mA）：

| 测什么 | 合格 | 不合格说明 |
|---|---|---|
| 静态电流 | ~5 mA 级 | 几十 mA 以上 → 有连锡/器件焊反 |
| J3.6 对 GND | 3.3 V ± 0.1 V | 无输出 → 查 F1 → D1 → U4 焊接 |
| U3 第 1 脚（1OE#）对 GND | ≈3.3 V（R4 上拉） | ≈0 V → 板子在静默期就会驱动总线，**别上总线**，先查 R4/U3 |
| DATA 针对 GND | ≈3.3 V（R5 上拉） | ≈0 V → 查 U3.6/D2/R6 |

空板（MCU 未编程）时 PA1 是高阻，R4 应把 1OE# 拉高 —— 这一条同时验证了
"DE=PA1"的假设（本固件补丁的前提）。**再用蜂鸣档量 U1 第 8 脚 ↔ U3 第 1 脚**，
确认两者连通；若不通，说明 OSHWhub 版改了方向脚，**停下来**，把实物这两点的
通断结果发我，固件要改一行。

## 2. 固件从哪来

```sh
cd 硬件资料/imu-to-ttl/firmware
make g031-dxl            # 产出 build/imu_to_dxl.elf
```

- **构建机**：本 Windows 机没有 arm 工具链。两条路任选：
  1. Radxa Zero3W（或任意 Linux）：`sudo apt install gcc-arm-none-eabi binutils-arm-none-eabi make`，然后把 `firmware/` 目录拷过去（或 git 同步）再 `make g031-dxl`；
  2. Windows：装 xpack 的 arm-none-eabi-gcc（解压即用）+ make，或直接 STM32CubeIDE 导入。
- 与 imu_to_ttl 板固件的**唯一差别**：`-DMD_BUS_DE=1` →
  PA1 配成 USART2 硬件 DE（AF1 = USART2_RTS_DE_CK，DS12992 Rev 4 Table 12），
  `USART_CR3 = DEM | DEP | DEAT/DEDT = 4/32 bit`。收线（2OE）经 R7 常开，
  回显丢弃逻辑与原板完全一致 —— 其余代码零改动。
- 想在 PC 上先过协议单测：`make host-test`（需要本机有 gcc）。

## 3. 烧录与测试

### 3.1 SWD 接线（J3 六针排针）

| J3 针 | 网络 | ST-Link 对应 |
|---|---|---|
| 1 | GND | GND |
| 2 | SWDCLK | SWCLK |
| 3 | SWDIO | SWDIO |
| 6 | +3V3 | 3.3V（Vref，可不接，但接了更稳）|

（4 = UART_TX、5 = UART_RX，调试打印用，本次测试不需要。）

### 3.2 烧录

STM32CubeProgrammer（GUI，官方，推荐）：Connect（SWD, 4000 kHz）→ 能读出
"STM32G031F8" 即 SWD/电源/时钟全通 → Open file 选 `build/imu_to_dxl.elf` →
Download → 硬复位。

或 OpenOCD 命令行：

```sh
openocd -f interface/stlink.cfg -f target/stm32g0x.cfg \
        -c "program build/imu_to_dxl.elf verify reset exit"
```

> **连不上 SWD 的第一嫌疑是供电**：确认 §1.4 的 3.3 V 正常，再查 SWDIO/SWCLK 有没有虚焊。

### 3.3 总线测试（bus_test.py）

接线（USB-TTL 的 **TX 和 RX 并联**后接 DATA —— 半双工单线总线的标准调试接法，
自己发的帧会回显，脚本自动剥离）：

```
USB-TTL TX ──┬──► DATA（§1.3 判明的那根）
USB-TTL RX ──┘
USB-TTL GND ────► GND
板子供电照旧 6.0~8.4 V（别用 USB-TTL 的 5V 给板子供电）
```

```sh
cd 硬件资料/imu-to-ttl/firmware/tools
python bus_test.py COM5            # 波特率默认 1M、ID 默认 200
```

脚本流程：ping → 读识别寄存器（固件版本、ID、波特率码）→ 读 IMU 诊断块 →
循环读四元数并打印（陀螺 °/s、四元数 xyzw、模长、欧拉角）。

### 3.4 判读标准

| 检查 | 合格 | 说明 |
|---|---|---|
| ping ID 200 | 应答 | 全链路（供电、3.3V、MCU 固件、收发缓冲、DE）一次验证 |
| 识别寄存器 | 固件 v1.0，ID=200，波特率码=0 | |
| **status.bit0** | =1（WHO_AM_I 应答 0x70）| **=0 → IMU/SPI 有问题**：查 U2 焊接（LGA 底盘必须热风）、PA4~PA7 连锡、R1 |
| status.bit1 | =1（SFLP 融合在跑）| =0 且 bit0=1 → 固件配置没起来，极少见 |
| 四元数模长 \|q\| | ≈1.00（±0.05）| 恒为 0 → 同 bit0=0；轻微波动正常 |
| 静置 10 min | 缓慢漂移 | **出现台阶 = 机械应力**（U2 焊接受力/固定不牢），不是芯片坏 |
| 绕板法向转 90° | 对应欧拉角变化 ≈90°，回正能回 | 手持转板即可验 |
| 陀螺静止读数 | <0.5 dps | 大离谱 → 查 U2 供电去耦 C1/C2 |

### 3.5 通过之后（装机前）

- 接 1 个真实 HD1910 上总线重复上面测试（互不干扰 = §3.3 加 `--scan` 应看到舵机 ID 和 200 并存）。
- 装机注意：飞特 HD-1910 线序 **1=Signal / 2=Vcc / 3=GND**，与 Dynamixel 相反；
  2026-09-17 首板实测 **B3B-PH-K-S 外壳与飞特插头不配**（削壳可插），EH 座与飞特 AMP2.0
  插头物理上互插不进（2.5 vs 2.0 mm）—— 用哪种线就配哪种座。

## 4. 排障速查

| 现象 | 第一嫌疑 | 第二嫌疑 |
|---|---|---|
| SWD 连不上 | 3.3 V 不对（查 U4/F1/D1）| SWDIO/SWCLK 虚焊、排针装反 |
| ping 无应答 | DATA/GND 接错针（重做 §1.3）| 刷成 `imu_to_ttl.elf`（AUTO 版，无 DE）→ 必须用 `g031-dxl` 目标 |
| ping 偶通偶断 | 共地不良 | USB-TTL 太慢（劣质 CH340 @1M）→ 降 500k？不行，固件固定 1M，换 CP2102/FT232 |
| ping 通、status.bit0=0 | U2 焊接（热风重焊）| PA5~PA7 连锡、R1 焊接 |
| 四元数恒 0 | 同上 | —— |
| 数据有台阶 | U2 机械应力 | 固定方式别别劲 |
| 一切正常但舵机不回话 | 舵机线序/座子不配 | 舵机自身保护（出厂默认关闭，见《新手接线与装配》§2.1）|

---
*固件协议与 Renode 门（30 000 次压测、错帧恢复、回显丢弃）在同一份代码上验证过；
本指南的 `bus_test.py` 协议层已用模拟从机离线自测（ping 帧与 Renode gate 记录一致）。*
