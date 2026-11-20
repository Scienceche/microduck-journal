# 历史事故时间线（对照"我这是不是老问题"）

> 每条 = 日期 + 板/对象 + 现象 + 根因 + 修复。证据链在各日期的调试记录里。

| 日期 | 对象 | 现象 | 根因 | 修复 |
|---|---|---|---|---|
| 2026-09-17 | dxl 板 | 插头插不进/插错 | B3B-PH-K-S 外壳与飞特插头不配（削壳可插）；EH 2.5 与 AMP2.0 物理互插不进 | 用哪种线配哪种座；线序飞特 1=Sig/2=Vcc/3=GND 与 DXL 相反 |
| 2026-10-09 | dxl 板 | 每次复位随机进 bootloader | OSHWhub 版 BOOT0(PB8) 悬空，R7 上拉没焊 | SWD 写选项字节 nBOOT_SEL(bit24)=0；注意 G0 的 OPTSTRT=CR bit17 |
| 2026-10-09 | dxl 板 | SPI 单字节读 IMU 永远死等 | G0 SPI_CR2 缺 FRXTH=1；Renode 不仿真 FIFO 阈值所以仿真门没抓到 | board_g031.c 加 (1<<12) |
| 2026-10-09 | dxl 板 | SFLP 四元数一个样本都没有 | 自己位定义错（FIFO_EN bit0≠SFLP，INIT 0x08 命中保留位）——**芯片无罪** | 逐位对 DS 重核；SW_POR 开局清 EMB 域 |
| 2026-10-09 | dxl 板 | SWD 读 RAM 得乱码，险些误诊 | WFI 睡眠中读 RAM 不可靠 | DBGMCU_CR=7 开睡眠调试 |
| 2026-10-09 | 主控 HAT | 面板 ping IMU 无应答但 SWD 可见数据好 | HAT RX 支路物理断：U6.4↔U5.1 蜂鸣不通（虚焊/断线）；主控裸排针 8↔10 回环正常 | 补焊排座第 10 脚 + U6 区域，或飞线 |
| 2026-10-13 | 整机 | 面板 poll 永远 None（error 空、samples=0）| 协议选错：dxl 的 sync_read 是双字节地址，本固件是飞特 fee 单字节 | `/api/scope_defaults` 返回 PROTO_FEE |
| 2026-10-13 | 整机 | ttyS2 多进程互踩（pyserial "multiple access"）| 旧 imu-scope 服务一打开就自动连 :8081 抢总线 | `systemctl disable --now imu-scope` |
| 2026-10-15 | HAT 板载 IMU | 触发内部状态切换的寄存器写入被丢（回读 0x80/0x81 翻转）| LCSC C194919 疑似克隆 BMI088 | 断电重上电复测 → 换正渠芯片或放弃板载 IMU |
| 2026-10-22 | dxl 板 | "看门狗没生效"，现象依旧 | 板上一直烧的是 Radxa `/root/imu_fw/build/imu_to_dxl.elf`（通用名，10-10 旧构建，无 IWDG）；IWDG 版 ELF 只在 PC 从未上板 | 全量同步源树到 Radxa 重建，J-Link+OpenOCD 重烧 `工具/imu_to_dxl_wdt2_fixed_driver.elf`（记录在案的当前板上版本）|
| 2026-10-22 | dxl 板 | 重烧看门狗固件后静置数据翻滚漂移 | 只同步了改动的 4 个文件，其余用了 Radxa 10-10 旧树 → 10-12 的 lsm6dsv16x.c 初始化顺序修复被回退（这颗 die 对初始化顺序敏感）| **远端树重建前必须全量同步**，只带增量 = 赌远端是新的；重烧后静置 70s 验证漂移恢复 |
| 2026-11-05 | 整机 | 站立 IMU 带 ~4.5° 固定偏差，门卫倾角时过时不过 | 装向没校准；且仅看 gz 判装向不够 | 标定模式背贴门框读重力均值 → 修正四元数 → verify-imu 写入；必须看全向量 |
| 2026-11-17 | KiCad 复刻线 | v3.8 布线长期收不了口（WIP 自 10-30）| 用户裁定整线放弃（"搞的不行"）| 目录删除（两处，各留 imu_to_servo-orig 归档）；git 424ea8f（v3.6 交付态）/1978d37（v3.8 WIP）可恢复 |
| 2026-11-17 | 全部 IMU 板线 | 工作区板子线太多太乱 | 用户裁定**只留 imu_to_servo 母本** | 官方 GD32 线（community-kit/hardware/imu_to_dxl）与 kissqy RevA 工程文件删除，各自 git 可恢复；kissqy 的 HAT 资料保留（在用）|
| 2026-11-19 | 整机仪表 | ttyS2 'Write timeout'（imu-scope polls 涨 ok=0，重启服务无效）| 调试焊盘外接 USB-TTL 顶住半双工总线 + UART 卡死态；另：/api/state 单次快照被旧会话冻结值骗（32666 全 ok 实为历史）| 拔外接 USB + 完整服务重启序列后释放；**排障看计数必须两次读增量** |
| 2026-11-19 | 整机仪表 | 陀螺 xyz 有数，欧拉角/四元数恒平线（quat_raw 逐位冻结、flags=0、quat_valid=False）| imu-scope 默认 md1 人格解码 **FT6 固件**板（身份字 06 00 00 00 F2、model 0xF210 fw 6.0）——20 字节块布局错位；中途"SFLP 没起来"“reboot 无效”全是错位幻觉（reboot 经 vendor 窗口发不出去）| drop-in 加 `Environment=SCOPE_FEE_PERSONALITY=ft6` → daemon-reload → 重启 → `quat_valid=True`/`|q|`=1.000/欧拉角实时（详见 journal 调试记录-2026-11-16 补记 7）|

## 沉淀规则

- 用户报的现象若能在上表命中 → 按修复列直接给方案，但仍要求一项复测证据。
- 新事故排查完毕后，**追加一行到本表**（保持同样五列），并在
  `microduck-journal/docs/调试记录-YYYY-MM-DD.md` 留完整证据链。
