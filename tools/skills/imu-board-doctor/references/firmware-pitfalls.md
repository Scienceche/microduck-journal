# 固件级天坑全集（烧录/构建/寄存器）

> 全部来自 2026-10-09 首板调试（一次会话 6 个真 bug）及其后的复检更正，源：
> `microduck-journal/docs/调试记录-2026-10-09.md`。固件源码在
> `microduck-journal/firmware/imu-to-ttl/`。

## 版本演化与验证状态（2026-11-16 核对）

| 版本 | 产物 | 内容 | 验证状态 |
|---|---|---|---|
| v1.x | `工具/imu_to_dxl.elf`（10-12）| 初始版 | 指南时代基线 |
| v2.0 | `工具/imu_to_dxl_iwdg.elf`（10-21）| 加 IWDG（PR 档位还错着）| **只在 PC，从未上板** |
| v2.x diag | `工具/imu_to_dxl_wdt2_diag.elf`（10-22）| IWDG_PR=3 修正 + 诊断寄存器 144-151 | 中间产物 |
| **v2.x fixed** | `工具/imu_to_dxl_wdt2_fixed_driver.elf`（10-22）| + 10-12 驱动初始化顺序修复 | **记录在案的"当前板上版本"**：静置 70s 漂移正常、看门狗在线自证 0→1→2 |
| v2.1 | `工具/imu_v2.1.elf`（10-22 01:21）| 版本可观测体系 | 记录未标注是否上板 |
| v2.4 | 仅源码（md_config.h MINOR=4）| 开机信标门控 reg150、上电完全静默 | **无产物 ELF、无验证记录**——新板从源码构建得到的就是它，先验证再当基线 |

主机协议契约 v1→v2 不变（ID=200、1M、0x7C 12B 读块、±500dps），host 解码兼容。
板上到底跑的哪版：读 addr 0/1（FW 大小版本）+ **reg147 构建日期**（v2.x 起可观测）。

烧录通道备忘：J-Link + PC 端 xpack-openocd-0.12.0（`C:\duck_work`，
`interface/jlink.cfg` + `target/stm32g0x.cfg`）；Radxa 面板烧录按钮烧的是
Radxa `/root/imu_fw/build/` 下的通用名文件——**先核对 reg147/构建日期再信它**。

## 构建

- 本 Windows 机没有 arm 工具链。两条路：
  1. Radxa Zero3W（或任意 Linux）：`sudo apt install gcc-arm-none-eabi binutils-arm-none-eabi make`，
     把 `firmware/` 拷过去，`make g031-dxl`（dxl 板）或 `make g031`（ttl 板）；
  2. Windows：xpack arm-none-eabi-gcc（解压即用）+ make，或 STM32CubeIDE 导入。
- 两目标唯一差别：`-DMD_BUS_DE=1` → PA1 配成 USART2 硬件 DE
  （AF1 = USART2_RTS_DE_CK，DS12992 Rev 4 Table 12），`USART_CR3 = DEM|DEP|DEAT/DEDT=4/32 bit`。
- PC 上过协议单测：`make host-test`（需本机 gcc）。

## 烧录

- STM32CubeProgrammer（推荐）：Connect（SWD, 4000 kHz）→ 读出 "STM32G031F8" →
  Download `build/imu_to_dxl.elf` → 硬复位。
- 或 OpenOCD：
  ```sh
  openocd -f interface/stlink.cfg -f target/stm32g0x.cfg \
          -c "program build/imu_to_dxl.elf verify reset exit"
  ```
- 连不上 SWD 的第一嫌疑是供电（回 Gate B），其次 SWDIO/SWCLK 虚焊、排针装反。

## STM32G0 天坑（真实事故）

### 1. BOOT0 悬空 → 每次复位随机进 bootloader

OSHWhub 版 PB8(=BOOT0) 悬空（dxl 图纸的 R7 上拉没焊），nBOOT_SEL=1 时启动目标由引脚电平决定。

**修法**：SWD 写选项行（0x1FFF7800），**nBOOT_SEL(bit24)=0**（改用选项位、忽略引脚）。
写入后 OPTR=0xFFEFFEAA，三连复位全部进 flash。

**大坑中坑**：G0 的 **OPTSTRT 是 CR bit17**，不是 G4 系列的 bit29——第一次就错在这，
写入无效还以为是自己姿势不对。

### 2. SPI 单字节传输死等

G0 的 `SPI_CR2` 需 **FRXTH=1 (bit12)**，否则 RXNE 凑满 16 位才置位，单字节 xfer 永远等不到。
`board_g031.c`: `SPI_CR2 = (7<<8) | (1<<12)`。

**为什么仿真没抓到**：Renode 的 SPI 模型不仿真 FIFO 阈值。仿真过 ≠ 硬件过。

### 3. IWDG / 复位诊断的位与偏移坑（2026-10-22）

- `IWDG_PR=4` 是档位（÷64），不是注释以为的 ÷32 → 改 **PR=3**（÷32），超时才是设计的 1.5 s。
- G0 的 **RCC_CSR 偏移是 0x60**，不是 F4 的 0x94（已对官方 stm32g031xx.h 核对）。
- 诊断寄存器（控制表 144-151）：144=复位原因（bit0 IWDG）、145=IWDG 复位计数
  （`.noinit` RAM + magic，跨看门狗复位保 SRAM 累加，断电清零）、146=写 0xA5 在线自证、
  **147=构建日期**（yy mm dd）、150=心跳信标开关（v2.4 起默认关，上电静默）。
- 看门狗的适用边界：它救的是 `bus_tx` 卡 TC 死等、DE 拉死总线这类**主循环停喂**的挂死；
  若只是通信层死、内核还活着（照常喂狗），IWDG 咬不到——此时靠 144/145 读数说话，
  别急着断言"看门狗没用"。

### 4. WFI 睡眠中经 SWD 读 RAM 得到假乱码

开 `DBGMCU_CR=7`（睡眠调试）后读取才可靠。这个天坑本身曾是误诊来源——别把睡眠假数据
当成 RAM 损坏或程序跑飞。

## LSM6DSV16X 天坑（真实事故）

### 5. 冤案警示：位定义错一位，"芯片缺陷"冤案

曾判定"融合核缺陷/疑似 16B 缩配版"，**实际全是自己位定义错误**：

- `EMB_FUNC_FIFO_EN_A (0x44)`：**bit1 (0x02) 才是 SFLP_GAME_FIFO_EN；bit0 (0x01) 是重力**。
  写 0x01 = 只开重力批量（tag 0x17 稳定流出 ✓），四元数（tag 0x13）永远为零 ✓——
  完美解释"缺陷"现象。
- `EMB_FUNC_INIT_A (0x66)`：**bit1 (0x02) 才是 SFLP_GAME_INIT**；写 0x08 命中保留位 →
  卡死不自清。

**流程规则**：下"芯片坏"结论前，逐位对照 DS 原文（DS13510 表号可查）重核所有写过的寄存器，
并让用户放大镜读 U2 顶标核对型号（LSM6DSV16X 与 16B 的 WHO_AM_I 同为 0x70，顶标是唯一线索）。

### 6. SFLP_GAME_INIT 位跨复位存活

早期固件在传感器上电时写 INIT 请求，卡在 1；`CTRL3` 的 **SW_RESET 不清 EMB 域**。

**修法**：用官方 **SW_POR**（`FUNC_CFG_ACCESS` bit2 = 0x04，等效上电复位）开局，之后读回
INIT=0 ✓。

### 7. ODR / FIFO 编码跨代差异

- `SFLP_ODR` 字段在 bit[5:3] 且 bit6/1/0 必须为 1（DS13510 Table 323/324）：30 Hz 档是
  **0x4B**（曾错写 0x03）。
- FIFO 连续模式 DSV16X = **011**；110(0x06) 是 LSM6DSL 时代编码、在 DSV16X 是保留值 →
  FIFO 永远空。`FIFO_CTRL4 = 0x03`。

### 8. 克隆芯片鉴别（相邻事故，对象是 HAT 板载 IMU，非本板，但采购渠道相同）

LCSC C194919 疑似克隆 BMI088：**只有触发内部状态切换的寄存器写入被丢**（回读位翻转
0x80/0x81），静态配置正常——与克隆 STM32(DEM 失效)、克隆 LSM6DSV(SFLP 乱转) 同款风格。
现象吻合"特定寄存器写了等于没写"时，把克隆料列入嫌疑，换正渠芯片对照。

## 软件 Mahony 兜底（固件已内置）

`src/lsm6dsv16x.c`：100 Hz 原始陀螺(±500dps)+加计(±4g) 6 轴 Mahony（Kp=0.6/Ki=0.1，软浮点，
每次约百微秒），输出写原四元数槽位（f16 x/y/z，w 由主机端恢复）。
**若硬件融合 tag 0x13 出现则优先采用**（混布部署安全）。实测 |q|=1.000、偏航精确跟随
（+72.1° 验证 ✓）。

即：四元数有数据但怀疑来源——先确认它是 Mahony 兜底还是硬件 SFLP（诊断块 status.bit1 +
tag 0x13 是否出现），再决定要不要追硬件。
