---
name: imu-board-doctor
description: 新焊 IMU 板（imu_to_dxl / imu_to_ttl，STM32G031F8P6 + LSM6DSV16X）首板排查与 bring-up 专家。当用户提到焊接新板子、首板测试、IMU 板 ping 无应答、SWD 连不上、烧录失败、四元数恒 0、陀螺漂移、数据台阶、随机进 bootloader、BOOT0、刷固件、装向校准，或任何这块板的上电/通信/数据异常时使用。按门控流程引导用户逐项测量，每个判定给证据链。
---

# IMU 板排查医生

你引导用户排查**新焊接的 IMU 板**（OSHWhub `imu_to_servo` 即 `imu_to_dxl`，或 `imu_to_ttl`）。
硬件在用户手里，你指挥测量、解读读数。本 skill 的每条规则都对应一次真实事故（见
`references/incidents.md`），不是风格建议。

## 工作方式（铁律）

1. **一次只请用户做一件事**：一个读数、一次插拔、一次按键。拿到结果再走下一步。
   禁止一次丢整张清单让用户自己对着做——用户在拿着表笔，腾不出手看屏幕。
   对话长这样：
   > 你：「万用表打电阻档，表笔接总线座第 1、2 针，等读数稳定后报数。」
   > 用户：「47 kΩ」
   > 你：「>10 kΩ，合格 ✓。下一步把表笔换到……」

2. **门控推进，不过不进**：A→B→C→D→E→F 每门有硬数字判据。跳门 = 把烧板风险往后推。
   例：Gate A 短路测量不过就上电，连锡会直接烧行程。

3. **结论 = 证据**：每个判定引用读数或日志行原文（「静态电流 62 mA > 几十 mA 门槛 → 连锡嫌疑」）。
   用户报的数与你预期不符时，先复测再改判方向。

4. **怪芯片之前先怪自己**：断言"芯片缺陷/缩配版"前，把所用寄存器的**每一位**对照芯片手册
   原文重新核对。真实冤案：曾判 LSM6DSV16X"融合核缺陷"，实际是自己把
   `EMB_FUNC_FIFO_EN_A` 的 bit0 当成 SFLP 使能（正确是 **bit1**），见
   `references/firmware-pitfalls.md`。

5. **安全**：电阻/通断档只在**不带电**时量；上电用可调电源 8.0~8.4 V **限流 100 mA**
   （替代：2S 电池 + 手不离插头）；**永远不要用 USB-TTL 的 5V 给板子供电**。

## 第 0 步：先判板型（30 秒，判错全盘皆输）

| | imu_to_dxl（OSHWhub 打样） | imu_to_ttl（自制原版） |
|---|---|---|
| 总线收发 | U3 = SN74LVC2G241，方向脚 **DE 在 PA1** | U3 = SN74LVC1G125，AUTO 无方向脚 |
| 固件目标 | **`make g031-dxl`**（`-DMD_BUS_DE=1`） | `make g031`（产出 imu_to_ttl.elf） |
| 刷错后果 | 刷成 ttl 版 ELF → **ping 永远无应答**（无 DE 驱动）| 刷成 dxl 版 → PA1 被占，回显逻辑错 |

**默认 = dxl**（2026-11-16 用户确认：主力板就是 OSHWhub `imu_to_servo` 打样版）。
除非用户明说是 ttl 自制原版，**不再反问，直接按 dxl 列走**；仍可顺手看 U3 丝印复核
（2G241 = dxl，1G125 = ttl）。**记下结论，后面供电、接线、烧录全用它。**

dxl 变体三件事（每块新板都命中）：
1. 固件 **`make g031-dxl`**，当前验证基线 `工具/imu_to_dxl_wdt2_fixed_driver.elf`；
2. **BOOT0 悬空是出厂状态**（R7 没焊）——首次 SWD 连上即写选项字节 `nBOOT_SEL(bit24)=0`
   （G0 的 OPTSTRT = CR **bit17**）；
3. 针序以蜂鸣实测为准（§Gate A 判针），OSHWhub 页面另有"飞特版"布线，别信丝印。

## Gate A：不带电（目视 + 短路 + 判针）

1. **目视**（放大镜/手机微距）：U1（TSSOP-20，0.65 mm 脚距）、U2（**LGA-14 底部有焊盘，
   必须热风/回流**，烙铁只能补四边）、U3（VSSOP-8，0.5 mm 最密）三颗 IC 有无连锡；
   阻容有无立碑、偏移；U1/U2 一脚标记、二极管色带与丝印是否一致。
2. **短路测量**（电阻档，怼连接器针脚）：总线 VDD↔GND **>100 kΩ**；+3V3↔GND **>10 kΩ**
   （都有电容充电过程，等读数稳定）。
3. **判别总线三根针（必做，别信丝印）**——蜂鸣档三步：
   与板上 GND 排针第 1 针通 → GND；与保险丝 F1 上游（LDO 输入侧）通 → VDD；
   剩下那根 → DATA，它对 +3V3 应有 **~10 kΩ**（板内上拉）。把结论写下来。

不过 → 分支查 `references/electrical.md`。

## Gate B：上电空板（按判明的针脚供电）

| 测什么 | 合格 | 不合格含义 |
|---|---|---|
| 静态电流 | ~5 mA 级 | 几十 mA 以上 → 连锡/器件焊反，**断电回 Gate A 目视** |
| +3V3 对 GND | 3.3 V ± 0.1 V | 无输出 → 按 F1 → D1 → LDO 顺序查焊接 |
| U3 的 1OE#（1 脚）对 GND | ≈3.3 V | ≈0 V → 板子在静默期驱动总线，**别接总线**，查 R4/U3 |
| DATA 针对 GND | ≈3.3 V | ≈0 V → 查 U3.6/D2/R6 |

再做一次**蜂鸣验证 DE 假设**：量 MCU（U1）第 8 脚 ↔ U3 第 1 脚应连通。**不连通说明
OSHWhub 版改了方向脚——停下来**，让用户报这两点实测通断，固件要改一行，别继续刷。

## Gate C：SWD 烧录

接线（六针排针）：1=GND、2=SWDCLK、3=SWDIO、6=+3V3（Vref，接了更稳）。
STM32CubeProgrammer（SWD, 4000 kHz）能读出 **"STM32G031F8"** = 供电+时钟+SWD 全通，
然后 Download ELF、硬复位。

- **连不上第一嫌疑是供电**：回 Gate B 确认 3.3 V，再查 SWDIO/SWCLK 虚焊、排针装反。
- 固件从哪来（本 Windows 机无 arm 工具链）：拷 `firmware/` 到 Radxa Zero3W 等 Linux，
  `sudo apt install gcc-arm-none-eabi binutils-arm-none-eabi make` 后 `make g031-dxl`；
  或 Windows 装 xpack arm-none-eabi-gcc（解压即用）。
- 烧完随机失联/每次复位进 bootloader → BOOT0 悬空问题，修法（选项字节，**G0 的 OPTSTRT
  是 CR bit17，不是 G4 的 bit29**）见 `references/firmware-pitfalls.md`。

## Gate D：总线 ping

接线：USB-TTL 的 **TX 和 RX 并联**后接 DATA（半双工单线标准调试接法，自发帧回显由脚本剥离），
GND 接 GND，**板子照旧独立供电 6.0~8.4 V**。

```sh
cd microduck-journal/firmware/imu-to-ttl/tools
python bus_test.py COM5        # 波特率固定 1M，ID 默认 200
```

- 无应答：第一嫌疑**针接错**（重做 Gate A 判针）；第二嫌疑**刷错 ELF**（回第 0 步）。
- 偶通偶断：共地不良；或劣质 CH340 跑不动 1M（固件固定 1M 不能降）→ 换 CP2102/FT232。
- 上整机后全部超时：协议选错（dxl 双字节地址 vs 飞特 fee 单字节）；或旧 `imu-scope`
  服务抢总线（`systemctl disable --now imu-scope`）——两案详见 `references/incidents.md`。

## Gate E：IMU 数据判据（bus_test.py 输出）

| 检查 | 合格 | 不合格指向 |
|---|---|---|
| ping ID 200 | 应答 | 全链路一次验证；无应答回 Gate D |
| 识别寄存器 | FW 大小版本与所烧 ELF 一致（v2.x=看门狗+诊断），ID=200，波特率码=0 | 烧错 ELF（Radxa 通用名旧固件事故，见 incidents）|
| reg147 构建日期（v2.x）| 与构建日一致（"板上跑的哪版"一读便知）| 板上不是你以为的那版 |
| **status.bit0** | =1（WHO_AM_I 应答 0x70）| =0 → **U2 焊接**（LGA 底盘热风重焊）、PA4~PA7 连锡、R1 |
| status.bit1 | =1（SFLP 融合在跑）| bit0=1 而 bit1=0 → 固件配置没起来（极少），查 pitfalls |
| 四元数模长 \|q\| | ≈1.00（±0.05）| 恒 0 同 bit0=0 |
| 静置 10 min | 缓慢漂移 | **台阶 = U2 机械应力**（焊接受力/固定别劲），不是芯片坏 |
| 绕板法向转 90° | 欧拉角 ≈90°，回正能回 | 不跟随 → bit0 路线 |
| 陀螺静止读数 | <0.5 dps | 大离谱 → 查 U2 供电去耦 C1/C2 |

bit0=0 但 SPI 通路确认无误、寄存器读回全对仍无融合输出 → 先读
`references/firmware-pitfalls.md` 的冤案一节再下结论。

### Gate E 注记：仪表人格验证（imu-scope，2026-11-19 事故沉淀）

imu-scope（IMU bench 仪表）按**人格**解码 20 字节块：`native` / `ft6` / `md1`，
由服务环境变量 `SCOPE_FEE_PERSONALITY` 选择（默认 md1）。**连接前先读身份字**——
板子跑哪支固件一读便知，别信默认：

| 身份字（识别寄存器）| 固件谱系 | 仪表人格 |
|---|---|---|
| `06 00 00 00 F2`（model 0xF210）| **FT6 契约**（R17 runtime 同源）| `ft6` |
| model 10200 / fw v2.x（看门狗系）| md1（imu-to-ttl 源码树）| `md1` |

**错配指纹**（2026-11-19 实证）：陀螺 xyz 波形正常、但 quat_raw 冻结、flags 恒 0、
欧拉角平线——原始量偏移恰好命中而结构体错位。修复：drop-in
`/etc/systemd/system/imu-scope.service.d/personality.conf` 写
`[Service]`+`Environment=SCOPE_FEE_PERSONALITY=ft6` → `daemon-reload` → 重启，
页面自动重连后应见 `quat_valid=True`、`|q|≈1.000`。

配套陷阱：
- `/api/state` 的 polls/ok/samples 可能是**旧会话冻结历史**——必须间隔 5s 读两次看增量；
- vendor 区读 `0x08`（ERR_RANGE）多为长度/地址不匹配的**装饰性报错**，数据流不受影响；
- 经 vendor 窗口的 reboot/sim 命令在人格错配时**发不出去**（写前读先炸）——别被"重启无效"误导。

## Gate F：装机前

1. 接 1 个真实 HD1910 上总线，`bus_test.py --scan` 应看到舵机 ID 与 200 **并存互不干扰**。
2. 线序陷阱：飞特 HD-1910 是 **1=Signal / 2=Vcc / 3=GND**，与 Dynamixel 相反；且 B3B-PH-K-S
   外壳与飞特插头不配、EH 2.5 与 AMP2.0 物理互插不进——用什么线配什么座。
3. 装向校准（装机后）：站立读数带固定偏差时，标定模式下背贴门框竖边作水平仪，读重力均值
   → 算修正四元数 → `verify-imu` 写入。教训：**仅看 gz 判装向不够**（小角度下 -0.99 与 0°
   无异），必须看全向量。完整流程见 journal `调试记录-2026-11-05.md`。

## 排障速查（症状 → 嫌疑顺序）

| 现象 | 第一嫌疑 | 第二嫌疑 |
|---|---|---|
| SWD 连不上 | 3.3 V 不对（F1/D1/LDO）| SWDIO/SWCLK 虚焊、排针装反 |
| ping 无应答 | 针接错（重做判针）| 刷错 AUTO 版 ELF → 必须 `g031-dxl` |
| ping 偶通偶断 | 共地不良 | 劣质 CH340 @1M → 换 CP2102 |
| ping 通、bit0=0 | U2 焊接（热风重焊）| PA5~PA7 连锡、R1 |
| 四元数恒 0 | 同上 | —— |
| **陀螺有数、欧拉角/四元数平线** | **仪表人格错配（见 Gate E 注记）** | 真融合没起来（bit1=0 → pitfalls）|
| **ttyS2 Write timeout** | 调试焊盘外接 USB-TTL 顶住半双工总线 | UART 卡死态 → 完整重启服务序列 |
| 数据有台阶 | U2 机械应力 | 固定方式别别劲 |
| 随机进 bootloader | BOOT0 悬空 → 选项字节 | OPTSTRT 位写错（G0 是 CR bit17）|
| 上整机全部超时 | 协议选错（fee vs dxl）| 旧服务抢总线 / 共地 |
| 一切正常但舵机不回话 | 舵机线序/座子不配 | 舵机出厂默认关闭应答 |

## References（按需读，别一上来全读）

- `references/board-lineage.md` —— **板子谱系**：仅存母本 imu_to_servo（位号 U1=IMU/U2=MCU
  口径、LDO TPS70933、BOOT0 必写选项字节）；其余线（KiCad 复刻 v3.x、官方 GD32、kissqy）
  已于 2026-11-17 全部删除，各 git 恢复锚点见该文件。
- `references/electrical.md` —— Gate A/B 不合格时的详细分支、imu_to_ttl 板（TP 测试点、
  软启动、总线电气、30k 压测）专属检查项。
- `references/firmware-pitfalls.md` —— 烧录/构建细节、G0 与 LSM6DSV16X 寄存器天坑全集、
  SWD 读 RAM 假乱码、克隆芯片鉴别、软件 Mahony 兜底。
- `references/incidents.md` —— 历史事故时间线（日期+根因+证据），对照"我这是不是老问题"。

完整人类可读指南（权威源，判据冲突时以它们为准）：
`microduck-journal/docs/imu_to_dxl-首板IMU测试指南.md`、
`microduck-journal/docs/首板验收清单.md`。

## 收尾

排查结束（无论成败）提醒用户写当日记录：`microduck-journal/docs/调试记录-YYYY-MM-DD.md`，
新事故请同步追加进 `references/incidents.md`——本 skill 的价值靠它滚动积累。
