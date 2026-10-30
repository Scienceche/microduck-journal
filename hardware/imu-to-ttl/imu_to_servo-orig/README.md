# imu_to_servo（microduck imu模块）—— 真板原件归档

> 2026-10-30 从 `C:\Users\c1349\Downloads\` 归档。来源：OSHWhub 打样、已焊接在手的
> 首板（**无 hub** 的 IMU 板，即本仓库 imu_to_ttl v3.x 系列的母本）。

## 文件

| 文件 | 说明 |
|---|---|
| `ProPrj_imu_to_servo（microduck imu模块）_copy_2026-10-11.epro2` | 立创EDA**专业版**完整工程（内含 project2.json + 2.4MB .epru 版图文档），2026-10-11 拷贝 |
| `BOM_imu_to_servo（microduck imu模块）.xlsx` | 采购 BOM（2026-10-03 导出，LCSC 料号齐全） |
| `ProDoc_P1_2026-10-11.epro` | 同一晚导出的单文档（原理图/PCB 单件） |
| `md5.txt` | 三件校验和 |

打开方式：**只能用立创EDA专业版**（客户端或网页版导入 .epro2）；
KiCad 无法打开此格式。

## 真板关键内容（BOM + 2026-10-11 epro2 网表实证）

- U2 STM32G031F8P6 (TSSOP-20) + U1 LSM6DSV16XTR (LGA-14) +
  U3 SN74LVC2G241DCUR (VSSOP-8) —— 三颗主芯片与 v3.x 完全同款
- LDO = TPS70933DBVT（v3.x 换 HT7533）
- 接口 = 2× S3B-EH 2.5mm 3P（CN2/CN4）；H1 = PZ254R 6P 弯排针（v3.x J2 的出处）
- 防护链 SS14 + SMF15A + ESD9B5.0 + 10Ω/1206 + 47µF 固态 —— v3.x 原样继承
- 总线拓扑实证（PCB-STATUS "v2 拓扑还原记录"）：
  U1 pad1=RX_EN pad8=TX_EN_N pad10=MCU_RX；U3 1=TX_EN_N 2=BUS_TX 3=MCU_RX
  5/6=BUS_LOGIC 7=RX_EN 8=+3V3；R2-R7 六颗 10k 含 boot 肩带（对应我们 R14-R16）
- 固件专属目标：`make g031-dxl`（方向脚 DE 在 PA1，区别于 AUTO 总线版）
- 装配/测试：`docs/imu_to_dxl-首板IMU测试指南.md`

## 与各版本关系

    imu_to_servo (OSHWhub, EasyEDA Pro, 本归档)   ← 母本，实物在手
      └─ imu_to_ttl v3.4→v3.6 (KiCad 复刻+扩三口)  ← 网表逐脚核对过
           └─ v3.7 IMU+HUB 合体板 (36×30 舵机顶置)
    官方 imu_to_dxl (GD32 LQFP-48, community-kit) ← 另一条线，非本板
