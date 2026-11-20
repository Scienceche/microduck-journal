# IMU 板谱系（排查前先对号入座）

> **2026-11-17 用户裁定：只保留 imu_to_servo 母本，其余 IMU 板线全部删除。**
> 各线删除时间与恢复锚点见下。母本归档双副本（journal git 跟踪 + 硬件资料，md5 一致）。

## 谱系（现存唯一）

```
imu_to_servo (OSHWhub, EasyEDA Pro 归档) ← 母本，实物在手、整机跑通，唯一排查对象
```

## KiCad 自动化通道（2026-11-17 实测，重要）

- **KiCad 10.0.7**：`C:\Users\c1349\AppData\Local\Programs\KiCad\10.0.7\bin\`（kicad-cli、python.exe 带 pcbnew）
- **computer-use 对 pcbnew 完全可用**（wxWidgets）：菜单 AXPress ✓、画布滚轮缩放 ✓（状态栏
  X/Y/Z 元素即坐标/缩放预言机）、画布点击 ✓——与嘉立创EDA（全拒收）形成互补：EDA 只能看，KiCad 能操作
- **pcbnew Python API**（改板主力）：LoadBoard/FootprintLoad/NETINFO_ITEM/PCB_TRACK 全可用；
  坑：① FootprintLoad 不吃非 ASCII 路径（用 KiCad 官方库或 ASCII 临时目录）② KiCad10
  PCB_VIA::GetWidth() 无层参断言（journal stitch_grid 同坑，用常量/GetDiameter）③
  SetOrientation 要 EDA_ANGLE(90) ④ LED 封装 pad1=K pad2=A（接反不亮）
- v3.6 位号已占：R1-R17、LED1（新件用 R18/LED2）；U1=MCU(TSSOP-20)@(29,16)mm，pad3=PC15 空脚
- DRC 基线：v3.6 原板在 kicad-cli 10.0.7 下 37 违规 + 3 未连接（老本底）；ZONE_FILLER 重灌会
  另生 ~100 条潜在间距违规——改板后对比基线增量而不是绝对值

## 已删线与恢复锚点（全在 2026-11-17）

| 线 | 删除内容 | 恢复通道 |
|---|---|---|
| imu_to_ttl v3.4→v3.8（KiCad 复刻线）| 两处目录全删（各留母本归档）| journal git **424ea8f**（v3.6 交付态）/ **1978d37**（v3.8 WIP）；未提交前 `git checkout -- hardware/imu-to-ttl` 即整体复原 |
| 官方 imu_to_dxl（GD32，community-kit）| `microduck-community-kit/hardware/imu_to_dxl/` 整目录 | community-kit 仓库 `git checkout HEAD -- hardware/imu_to_dxl`（注意：kicad_pro 有一处本地改动随之丢失，仅项目设置）；或官方 GitHub 重下 |
| kissqy imu_to_dxl RevA | 仅两颗 `ProPrj_imu_to_dxl_RevA_V1_*.epro2` 工程文件 | 参考仓库 git 可恢复。**HAT 资料未动**（HAT_REVA epro2、TIMELINE、购物单——HAT 是鸭子在用硬件的参考）；IMU-TIMELINE/IMU-FIRMWARE.md 文档保留 |

外部无文件的参考：Xiao_lu microduck V0.2（仅照片，MPU6050）。

## 母本排查要点（看图/测量前先记这几点）

- **位号口径**：母本原理图 **U1=IMU、U2=MCU**（与《首板测试指南》的 U1=MCU/U2=IMU 写法
  差一位）——对照指南量电压/查连锡时先对好号再下手。
- LDO = **TPS70933DBVT**。
- 接口：2× S3B-EH 2.5mm 3P（CN2/CN4）+ H1 PZ254R 6P 弯排针。
- 总线：SN74LVC2G241，**PA1=TX_EN_N→1OE#（低=发）、PB7=RX_EN→2OE**（epro2 网表实证，
  2026-10-11 实战验证）→ 固件 `make g031-dxl`，基线 ELF `工具/imu_to_dxl_wdt2_fixed_driver.elf`。
- BOOT0：R7 上拉没焊（悬空→随机进 bootloader），首次 SWD 连上必写选项字节
  `nBOOT_SEL(bit24)=0`（G0 的 OPTSTRT=CR bit17）。
- 工程打开：嘉立创EDA专业版 `D:\lceda-pro\lceda-pro.exe` + `.epro2`（`.epro` 只是单文档导出）。
  **注意**：EDA 对 computer-use 全部合成输入免疫（2026-11-17 完整测试矩阵：后台/前台坐标点击、
  双击、UIA AXPress、Tab、typeText 全部零响应；对照实验计算器正常收键——是 Chromium 输入管线
  拒收合成事件，非通道问题；shell 非管理员，UIPI 亦排除）——**该应用只能人手操作 GUI**，
  computer-use 仅可观察（截图+a11y 只读）；
  自动化走文件侧：`.epro2` = zip（内名 GBK 乱码需 cp437→gb18030 修回），`.epru` = `|` 分隔的
  JSON 对象流（stub 带 type/id + content 无 type，ATTR 按 `parentId` 挂接；网络名在 WIRE 的
  NET attr 与走线 netName 上；PAD_NET id = [组,组件uuid,焊盘号,网络id]）。现成解析脚本：
  `工具/imu_servo_revB_work/`（extract/scan/pinmap/final_join 系列）。
- 引脚级事实（2026-11-17 符号库实证，与固件 md_config 逐脚吻合）：**PA0=第7脚**（与 IMU INT1
  同网 IMU_INT1）、**第2脚=PB9/PC14、第3脚=PC15 为无网络空脚**（板上无 LSE，可作 GPIO）、
  PA1=8/DE、PA2/3=9/10、PA4~7=11~14/SPI。板上无 LED、无 BOOT0 网络（PB8 网络级悬空），
  板框 **18.1×24.5mm**。
- **revB 已交付**（2026-11-18）：KiCad 版（基于 v3.6 git 424ea8f），R18(2.2k)@板西南 + LED2(红)@
  (5.0,26.3)，LED_ST 走 B.Cu 北走廊 y=0.6 + via-in-pad@U1.3(PC15)，LED_ST2 板内短走线。制造包：
  `工具/imu_revB_kicad/imu_to_servo_revB_fab.zip`（11 文件：gerber×9 + 钻孔 + gbrjob）。
  DRC 84 条（基线 37 + 新增 47 铜间距/DNP 类，JLCPCB 不拦）；LED 网络 0 未连。BOM 补 2 行：
  R18 2.2k 0603 / LED2 红 0603。Freerouting v2.3.0 jar + JRE25 可用（v2.4.1 SES 写出 bug，
  v2.5 Native Image 序列化崩；走 ASCII 路径 + Clash 代理 7897 下载）。
