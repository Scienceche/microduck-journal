#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标定台 v2 姿态库补丁: 加「采集运动姿态」(N 秒 × 25Hz 关节序列后台录制)。

改 /opt/duck-calib/server.py:
  + 运动录制线程/状态
  + POST /api/pose/record  (name, seconds) → 后台线程
  + GET  /api/pose/record  → 进度
  + GET  /api/poses        → 附带 motions 列表
  + /api/pose/delete       → 兼容 motion 文件
改 /opt/duck-calib/index.html:
  + 采集行(时长选择 + 🎥按钮 + 状态) + motionList 渲染
每个替换 assert 命中, 改完 ast/py 语法校验。
"""
import ast
import re
from pathlib import Path

SRV = Path("/opt/duck-calib/server.py")
IDX = Path("/opt/duck-calib/index.html")

srv = SRV.read_text(encoding="utf-8")

# ---------- server.py: 录制机制 ----------
anchor1 = "class Handler(BaseHTTPRequestHandler):"
block1 = '''# ---- 运动姿态录制(2026-11-21): 后台线程采样, 不阻塞请求 ----
MOTION_DIR = POSES / "motion"
MOTION_DIR.mkdir(parents=True, exist_ok=True)
_REC = {"running": False, "name": None, "target": 0, "got": 0, "done": True,
        "error": None, "last_file": None}


def _record_thread(name, seconds, hz):
    import threading
    import time as _t
    try:
        period = 1.0 / max(1.0, hz)
        samples = []
        t0 = _t.monotonic()
        while _t.monotonic() - t0 < seconds:
            ts = round(_t.monotonic() - t0, 3)
            status = rpc("robot.busStatus", timeout=4.0) or {}
            servos = ((status.get("last_sample") or {}).get("servos")) or []
            if servos:
                samples.append({
                    "t": ts,
                    "joints": {str(s["id"]): s["position_raw"] for s in servos},
                })
            _REC["got"] = len(samples)
            _t.sleep(max(0.005, period - (_t.monotonic() - t0) % period))
        doc = {
            "name": name, "type": "motion", "hz": hz, "seconds": seconds,
            "created": _t.strftime("%m-%d %H:%M"),
            "samples": samples,
        }
        fn = MOTION_DIR / (name + ".json")
        fn.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        _REC["last_file"] = str(fn)
    except Exception as exc:  # 录制失败要可见
        _REC["error"] = str(exc)[-200:]
    finally:
        _REC["running"] = False
        _REC["done"] = True


def start_record(name, seconds, hz):
    import threading
    if _REC["running"]:
        return {"error": "已有采集在进行: %s" % _REC["name"]}
    _REC.update({"running": True, "name": name, "target": int(seconds * hz),
                 "got": 0, "done": False, "error": None, "last_file": None})
    threading.Thread(target=_record_thread, args=(name, seconds, hz),
                     daemon=True).start()
    return {"started": name, "seconds": seconds, "hz": hz}


class Handler(BaseHTTPRequestHandler):'''
assert anchor1 in srv, "server.py Handler 锚点未找到"
srv = srv.replace(anchor1, block1, 1)

# ---------- server.py: GET 路由 ----------
anchor2 = '''            elif p == "/api/health":'''
block2 = '''            elif p == "/api/pose/record":
                self._json({k: _REC[k] for k in
                            ("running", "name", "target", "got", "done",
                             "error", "last_file")})
            elif p == "/api/health":'''
assert anchor2 in srv, "GET 路由锚点未找到"
srv = srv.replace(anchor2, block2, 1)

# GET /api/poses 附带 motions
anchor3 = '''                self._json({"poses": items})'''
block3 = '''                motions = []
                for f in sorted(MOTION_DIR.glob("*.json")):
                    try:
                        m = json.loads(f.read_text(encoding="utf-8"))
                        motions.append({"name": m.get("name", f.stem),
                                        "created": m.get("created", ""),
                                        "samples": len(m.get("samples", [])),
                                        "hz": m.get("hz"), "seconds": m.get("seconds")})
                    except Exception:
                        pass
                self._json({"poses": items, "motions": motions})'''
assert anchor3 in srv, "poses GET 锚点未找到"
srv = srv.replace(anchor3, block3, 1)

# ---------- server.py: POST /api/pose/record ----------
anchor4 = '''            elif p == "/api/pose/delete":'''
block4 = '''            elif p == "/api/pose/record":
                name = str(data.get("name", "")).strip()
                seconds = float(data.get("seconds", 5))
                hz = float(data.get("hz", 25))
                if not NAME_RE.match(name):
                    self._json({"error": "采集名只能用中文/字母/数字/_/-，≤24 字"})
                    return
                if not (1.0 <= seconds <= 60.0):
                    self._json({"error": "时长 1-60 秒"})
                    return
                self._json(start_record(name, seconds, hz))
            elif p == "/api/pose/delete":'''
assert anchor4 in srv, "POST record 插入锚点未找到"
srv = srv.replace(anchor4, block4, 1)

# delete 兼容 motion
anchor5 = '''                path = POSES / (name + ".json")
                if NAME_RE.match(name) and path.is_file():'''
block5 = '''                mpath = MOTION_DIR / (name + ".json")
                if NAME_RE.match(name) and mpath.is_file():
                    mpath.unlink()
                    self._json({"deleted": name + " (motion)"})
                    return
                path = POSES / (name + ".json")
                if NAME_RE.match(name) and path.is_file():'''
assert anchor5 in srv, "delete 锚点未找到"
srv = srv.replace(anchor5, block5, 1)

SRV.write_text(srv, encoding="utf-8")
ast.parse(SRV.read_text(encoding="utf-8"))
print("server.py 补丁 ✓ 语法过")

# ---------- index.html ----------
idx = IDX.read_text(encoding="utf-8")
a6 = '''    <button class="primary" id="btnPoseSave">存当前</button>
  </div>
  <div id="poseList" style="margin-top:8px"><span class="dim">无姿态</span></div>
</div>'''
b6 = '''    <button class="primary" id="btnPoseSave">存当前</button>
  </div>
  <div class="row" style="margin-top:6px">
    <select id="motionSec"><option value="5">5 秒</option><option value="10">10 秒</option><option value="20">20 秒</option></select>
    <button id="btnMotionRec">🎥 采集运动姿态</button>
    <span id="motionStatus" class="dim"></span>
  </div>
  <div id="poseList" style="margin-top:8px"><span class="dim">无姿态</span></div>
  <div id="motionList" style="margin-top:4px"></div>
</div>'''
assert a6 in idx, "前端姿态库锚点未找到"
idx = idx.replace(a6, b6, 1)

# JS: 采集函数 + motions 渲染(loadPoses 尾部扩展)
a7 = '''async function loadPoses(){
  const r=await api("/api/poses");
  const box=$("#poseList");
  if(!r.poses||!r.poses.length){box.innerHTML=`<span class="dim">无姿态</span>`;return}'''
b7 = '''async function loadPoses(){
  const r=await api("/api/poses");
  const mbox=$("#motionList");
  if(mbox){
    const ms=r.motions||[];
    mbox.innerHTML = ms.length ? ms.map(m=>{
      const n=m.name.replace(/"/g,"&quot;");
      return `<div class="row" style="margin-bottom:4px"><span style="min-width:80px">🎥 ${m.name}</span><span class="dim">${m.samples}样本 ${m.seconds}s@${m.hz}Hz ${m.created}</span><button class="small danger" data-pose="delm" data-name="${n}">删</button></div>`;
    }).join("") : "";
  }
  const box=$("#poseList");
  if(!r.poses||!r.poses.length){box.innerHTML=`<span class="dim">无姿态</span>`;return}'''
assert a7 in idx, "loadPoses 锚点未找到"
idx = idx.replace(a7, b7, 1)

# JS: 绑定采集按钮(挂在 btnPoseSave 绑定之后 — 找绑定区)
m = re.search(r'btnPoseSave[^;]*;[^\\n]*\\n', idx)
a8 = "$(\"#btnPoseSave\")"
# 直接在 </script> 前追加绑定块
a9 = "</script>"
b9 = '''
// ---- 运动姿态采集(2026-11-21) ----
async function recMotion(){
  const name=($("#poseName").value||"").trim()||("运动"+new Date().toTimeString().slice(0,5).replace(/:/g,""));
  const sec=parseFloat($("#motionSec").value||"5");
  const st=$("#motionStatus");
  st.textContent="启动中...";
  const r=await api("/api/pose/record",{name,seconds:sec,hz:25});
  if(r.error){toast(r.error);st.textContent="";return}
  const timer=setInterval(async()=>{
    try{
      const s=await api("/api/pose/record");
      st.textContent=`采集中 ${s.got}/${s.target} ...`;
      if(s.done){
        clearInterval(timer);
        st.textContent=s.error?("失败: "+s.error):"完成 "+s.got+" 样本";
        if(!s.error){toast("已存: "+name+" ("+s.got+" 样本)");loadPoses()}
      }
    }catch(e){}
  },600);
}
document.addEventListener("click",e=>{
  const b=e.target.closest("button");if(!b)return;
  if(b.id==="btnMotionRec")recMotion();
  if(b.dataset.pose==="delm"){if(confirm("删除该采集?"))api("/api/pose/delete",{name:b.dataset.name}).then(()=>loadPoses())}
});
</script>'''
assert a9 in idx, "script 尾锚点未找到"
idx = idx.replace(a9, b9, 1)

IDX.write_text(idx, encoding="utf-8")
# 落盘复核
chk = IDX.read_text(encoding="utf-8")
for probe in ("btnMotionRec", "motionList", "recMotion", "/api/pose/record"):
    assert probe in chk, probe + " 未落盘"
print("index.html 补丁 ✓ 采集按钮就位")
