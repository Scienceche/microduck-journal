# Microduck 中控 v2（手机 PWA）

跑在主控上的单页控制台，**手机浏览器打开 `http://192.168.31.168:8080`，"添加到主屏幕"即变 App**（全屏、离线外壳、图标）。桌面浏览器同样适用。

## v2 新增

| 功能 | 说明 |
|---|---|
| 📱 手机优先 PWA | 底部标签栏（姿态/舵机/总线/工具）、安全区适配、Service Worker 外壳缓存、manifest 图标 |
| 🦿 舵机分组卡片 | 按官方关节约定分组（右腿10-14 / 左腿20-24 / 头颈30-34），关节名直标，滑条+速度+单控，全组扭矩开关，悬浮急停键 |
| 🧍 **姿态库** | 扫描后一键"存当前"为命名姿态；应用=开扭矩+按速度下发全部关节；**文件格式兼容复刻仓库 servo-web 的 poses/*.json**（可互拷） |
| 📡 总线诊断 | 收/错/超时/校验计数、串口占用状态、IMU 自检（识别+姿态块） |
| 🔧 工具 | ST-Link 一键烧录、robotd 切换、主机重启；连接信息卡显示本机地址 |

## 页面结构

底部四个标签：
- **姿态**：IMU 在线/WHO_AM_I/融合/|q| 状态芯片、3D 立方体实时姿态、欧拉角/四元数/陀螺、趋势图（页面隐藏时自动暂停轮询）
- **舵机**：扫描 → 分组卡片 + 姿态库
- **总线**：诊断计数 + IMU 自检
- **工具**：烧录/系统/连接信息

## 文件

| 文件 | 说明 |
|---|---|
| `duck_panel.py` | 后端 v2：stdlib + pyserial；新增 `/api/poses`、`/api/pose`（save/apply/delete）、关节命名与分组 |
| `index.html` | 手机优先单页前端（零 CDN 依赖） |
| `manifest.webmanifest` / `sw.js` / `icon.svg` | PWA 三件套 |
| `feetech.py` | 复刻仓库 `tools/servo-web/feetech.py` 原样复用 |
| `duck-panel.service` | systemd 开机自启 |
| `poses/`（运行时生成） | 姿态库，兼容 servo-web 格式 `{name,time,rows:[{id,joint,pos,deg}]}` |

## 部署（主控上）

```sh
mkdir -p /root/duck-panel        # 七个文件放进去
systemctl link /root/duck-panel/duck-panel.service
systemctl enable --now duck-panel
```

## 借鉴来源

- **jyg9/microduck-community-kit**：host/web 的 WS 推送与调试台架构、SFLP 驱动位定义（v1 调试的关键参照）、双分区升级思路（后续可加 OTA 按钮）
- **fanhao375/microduck-replica**：servo-web 的 feetech.py、姿态文件格式、3D 模型查看器（待吸收进姿态页）
- **pollen-robotics/microduck**（官方）：关节 ID 分组约定、robotd 健康项

## 注意

- 面板占用 `/dev/ttyS2`：测试期间 robotd 保持停止（工具页有一键切换）
- 8080 端口局域网开放、无鉴权——家用局域网没问题，别暴露公网
- 烧录按钮需 ST-Link 插在主控 USB（`interface/stlink.cfg`）

