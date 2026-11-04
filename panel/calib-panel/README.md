# duck-calib — 舵机标定台（robotd 桥接版）

复刻老 `duck_panel` 的标定页，但**不再直接开 UART**——所有总线操作都走
robotd 的 `/run/robotd.sock` JSON-RPC（`robot.busStatus` / `robot.busCommand` /
`robot.relax`），与 robotd 无总线竞争；写入一律经 robotd 的 capture
（带限位/编码器边界校验），保存到 `/etc/robot/feetech-ft5/hd1910-calibration.toml`。

## 部署在鸭子上的位置

- 代码：`/opt/duck-calib/`（server.py, index.html, cal_ref4.png, poses/）
- 服务：`duck-calib.service`（:8085，root，stdlib-only Python3）
- 源码同步：本目录（PC）→ `scp * root@duck:/opt/duck-calib/` + `systemctl restart duck-calib`

## 功能与老面板对照

| 老面板 | 新页面 |
|---|---|
| ① 读原始位置 | 实时轮询 busStatus（1s），raw/角度/偏差/V/°C |
| ② 一键全校 | 「② 一键全校」逐关节 capture angle=0 |
| ③ 验证 2048 | 「③ 验证零位」逐关节 move 到 zero_raw |
| 行内「校」 | 每行「校零」= 当前位置记为 0° |
| 方向/限位 | 行内 ±1 按钮 / min° max° 输入（换向时限位自动镜像） |
| ±10° 示范 | 行内 +5°/−5°（robotd 强制限位） |
| 姿态库 | 存当前 / 逐关节回放（限标定模式） |
| 急停 | 顶部「松开/停止」（relax，两模式通用） |

新增：模式切换（service.sh enable/disable）、IMU 装向确认（官方 quat
[0.7071,0,0.7071,0]）、总线健康面板（丢包率/按 ID 计数——兼作 ID12/13
时隙热修复的验证面板）。

## 注意

- 逐关节操作只在**标定模式**下可用（页面顶部切换，会重启 robotd）。
- capture/verify 后 `motion_enabled` 被置 false；「回运动模式」时
  service.sh enable 会校验并恢复。
- 鸭子摆标准零位参考图：页面内「📐 零位参考图」折叠区（cal_ref4.png）。
