# computer-use 自动化速查（踩坑结晶）

2026-10-13 用微信发文件实战总结。下次同类任务先读这里，别再现场摸。

## 一、微信桌面版（Weixin.exe）

**成品脚本**：`wechat.mjs` → `sendFileToWeChat({chat, filePath})`，一次调用完成
搜索会话→切会话→弹文件框→填路径→发送→确认上传。下次直接用。

规律（实测）：

| 操作 | 有效手段 | 无效手段 |
|---|---|---|
| 搜索框填词 | `setValue`（能触发搜索逻辑） | 真实键入中文（IME 吞字） |
| 搜索结果行/列表/按钮点击 | `elements()` 拿 bounds → 换算光栅 → `left_click {strategy:"event"}` | a11y `AXPress`（自绘控件不响应） |
| 文件对话框填路径 | 对话框前台时 `type` 纯 ASCII 路径 + `Return` | `setValue`（自绘 combobox 不收） |
| 原生小错误框（"找不到文件"） | a11y 点"确定" | — |

**坐标换算公式**（全局 bounds → 光栅坐标）：
```
cx = (elX + elW/2 - winX) * rasterW / winW
cy = (elY + elH/2 - winY) * rasterH / winH
```
光栅尺寸从 `getScreenshot({emit:false})` 的 PNG 头解析（`pngSize()`，IHDR 第 16-24 字节）。

**关键结构**：
- "选择文件"对话框是**独立顶层窗口**（大号 >500px 宽的那个；输错路径会弹**小号**同名错误框，注意区分）
- `event` 策略要求目标窗口前台——动手前 `getState` 确认 `active`，前台不对就先点标题栏激活

## 二、通用教训

1. **a11y bounds 优先于 vision 猜坐标**：有树就 `elements()` 拿 bounds 换算，
   快且准；vision 看坐标要走截图→CDN→分析一个来回，还常错（这次就错点过书签栏）。
2. **每 cell 一条动作链**：bootstrap + 动作 + 观察（`getAXState`，别截图）打包一个
   cell；纯文本树比截图便宜一个数量级。
3. **验证用树不用眼**：发送成功 = 会话列表 row 出现文件名且无"上传中"——树能读就别看图。
4. **幻觉防线**：长会话里模型会开始"脑补执行成功"。规矩：只信**最新一条工具输出**，
   每个动作后必须有一条真实观察佐证；没有观察 = 没发生。
5. **不值得自动化的**：嘉立创EDA（桌面/在线版都是）——自绘画布 + 无障碍树缺失 +
   截图管线在 GPU 合成窗口上返回冻结帧，无法验证。此类直接给手动步骤。

## 三、下次微信发文件

```js
// 1) 跑 computer-use bootstrap
// 2) 一行:
const { pathToFileURL } = await import("node:url");
const { sendFileToWeChat } = await import(pathToFileURL(
  "E:/c1349/复刻microduck/工具/computer_use/wechat.mjs").href);
await sendFileToWeChat({ chat: "文件传输助手",
  filePath: "C:\\Users\\c1349\\Downloads\\xxx.zip" });
```
耗时预期：30 秒内（本次首次摸索约 15 轮调用，成本已付清）。
