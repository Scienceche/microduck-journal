#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v5: 运维可见 — 抓拍行下加范围说明 + 环形缓冲实时水位(预热中xx/120s 或 已满)。"""
import ast
from pathlib import Path

SRV = Path("/opt/duck-calib/server.py")
IDX = Path("/opt/duck-calib/index.html")

s = SRV.read_text(encoding="utf-8")
a1 = '''            elif p == "/api/pose/record":
                self._json({k: _REC[k] for k in
                            ("running", "name", "target", "got", "done",
                             "error", "last_file")})'''
b1 = '''            elif p == "/api/pose/record":
                with _ring_lock:
                    ring_s = round(len(RING) / RING_HZ, 1)
                self._json({k: _REC[k] for k in
                            ("running", "name", "target", "got", "done",
                             "error", "last_file")} | {"ring_s": ring_s,
                                                       "ring_max_s": RING_SECONDS})'''
assert a1 in s, "状态端点锚点未找到"
s = s.replace(a1, b1, 1)
SRV.write_text(s, encoding="utf-8")
ast.parse(s)

h = IDX.read_text(encoding="utf-8")
a2 = '''    <span class="dim">+后</span><input type="number" id="motionPost" value="3" min="0" max="30" step="1" style="width:50px"><span class="dim">秒</span>'''
b2 = a2 + '''
  </div>
  <div class="dim" style="margin-top:4px;font-size:12px">范围: 回溯 1-120 秒 · 补录 0-30 秒 · 单次最长 150 秒(约700KB)。环形滚录持续后台运行, 服务重启后需预热约 2 分钟。缓冲水位: <span id="ringFill">--</span></div>
  <div style="display:none">'''
assert a2 in h, "UI 行锚点未找到"
# 上面插法复杂化了 — 直接替换整行块更稳: 找到该行所在的 row div 结束
h = h.replace(a2, a2 + '''
  <div class="row" style="margin-top:4px"><span class="dim" style="font-size:12px">范围: 回溯 1-120 秒 · 补录 0-30 秒 · 单次最长 150 秒(约700KB)。环形滚录后台常驻, 重启后预热约 2 分钟。缓冲: <span id="ringFill">--</span></span></div>''', 1)

a3 = '''document.addEventListener("click",e=>{'''
b3 = '''async function ringTick(){
  try{
    const s=await api("/api/pose/record");
    const el=$("#ringFill"); if(!el)return;
    el.textContent = s.ring_s>=s.ring_max_s-1 ? "已满(120s)" : ("预热中 "+Math.floor(s.ring_s)+"/"+s.ring_max_s+"s");
  }catch(e){}
}
setInterval(ringTick,5000); ringTick();
document.addEventListener("click",e=>{'''
assert a3 in h, "JS 尾锚点未找到"
h = h.replace(a3, b3, 1)
IDX.write_text(h, encoding="utf-8")
chk = IDX.read_text(encoding="utf-8")
for probe in ("ringFill", "ringTick", "回溯 1-120"):
    assert probe in chk, probe + " 未落盘"
print("v5 ✓ 范围说明+缓冲水位上页面")
