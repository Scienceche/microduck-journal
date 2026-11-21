#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运动姿态采集 v3: 环形缓冲 + 事后抓拍(前N秒+后3秒), 面向运动模式的问题姿态。

- server.py: 常驻滚录线程(25Hz, deque 20s); /api/pose/record 改抓拍语义
  {name, pre=10, post=3}: 冻结环形副本 + 补录 post 秒 → 落盘
- index.html: 按钮改「🎥 抓取问题姿态」, select=前置秒数(5/10/15)
- duck_console.py: /api/pose_grab 代理 + 仪表盘快捷按钮(开车时点)
"""
import ast
import re
from pathlib import Path

SRV = Path("/opt/duck-calib/server.py")
IDX = Path("/opt/duck-calib/index.html")
CON = Path("/opt/duck-calib/duck_console.py")

# ================= server.py =================
srv = SRV.read_text(encoding="utf-8")

# 1) 常驻滚录线程替换原 start_record 机制
old_rec = srv[srv.index("# ---- 运动姿态录制(2026-11-21)"):srv.index("class Handler(BaseHTTPRequestHandler):")]
new_rec = '''# ---- 运动姿态采集 v3(2026-11-21): 环形缓冲 + 事后抓拍 ----
# 问题姿态发生时人还没点按钮 → 后台永远滚录最近 RING_SECONDS 秒,
# 按钮把 [前 pre 秒 + 后 post 秒] 落盘。面向运动模式。
MOTION_DIR = POSES / "motion"
MOTION_DIR.mkdir(parents=True, exist_ok=True)
RING_HZ = 25.0
RING_SECONDS = 20
RING = deque(maxlen=int(RING_HZ * RING_SECONDS))
_REC = {"running": False, "name": None, "target": 0, "got": 0, "done": True,
        "error": None, "last_file": None}
_ring_lock = threading.Lock()


def _sample_now():
    status = rpc("robot.busStatus", timeout=4.0) or {}
    joints = ((status.get("last_sample") or {}).get("servos")) or []
    if not joints:
        joints = (status.get("native") or {}).get("joints") or []
    if not joints:
        return None
    return {str(s["id"]): s.get("position_raw")
            for s in joints if s.get("position_raw") is not None}


def _ring_loop():
    import time as _t
    period = 1.0 / RING_HZ
    next_t = _t.monotonic()
    while True:
        try:
            snap = _sample_now()
            if snap:
                with _ring_lock:
                    RING.append((_t.time(), snap))
        except Exception:
            pass
        next_t += period
        sleep = next_t - _t.monotonic()
        _t.sleep(sleep if sleep > 0.002 else 0.002)
        if sleep < -1.0:
            next_t = _t.monotonic()  # 掉太多就重置节拍


def _grab_thread(name, pre, post):
    import time as _t
    try:
        cut = _t.time() - pre
        with _ring_lock:
            samples = [{"t": round(ts - cut, 3), "joints": js}
                       for ts, js in RING if ts >= cut]
        _REC["got"] = len(samples)
        t_end = _t.time() + post
        while _t.time() < t_end:
            snap = _sample_now()
            if snap:
                samples.append({"t": round(_t.time() - cut, 3), "joints": snap})
                _REC["got"] = len(samples)
            _t.sleep(1.0 / RING_HZ)
        doc = {"name": name, "type": "motion", "hz": RING_HZ,
               "pre_s": pre, "post_s": post, "created": _t.strftime("%m-%d %H:%M"),
               "samples": samples}
        fn = MOTION_DIR / (name + ".json")
        fn.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        _REC["last_file"] = str(fn)
    except Exception as exc:
        _REC["error"] = str(exc)[-200:]
    finally:
        _REC["running"] = False
        _REC["done"] = True


def start_record(name, pre, post):
    if _REC["running"]:
        return {"error": "上次抓拍还在写: %s" % _REC["name"]}
    _REC.update({"running": True, "name": name, "target": int((pre + post) * RING_HZ),
                 "got": 0, "done": False, "error": None, "last_file": None})
    threading.Thread(target=_grab_thread, args=(name, pre, post),
                     daemon=True).start()
    return {"started": name, "pre": pre, "post": post}


'''
srv = srv.replace(old_rec, new_rec, 1)
assert "_ring_loop" in srv and srv.count("_grab_thread") >= 2, "滚录块替换失败"

# deque/threading 导入检查
if "from collections import deque" not in srv:
    assert "import threading" in srv or True
    # 在 import 区补
    m = re.search(r"(import json\n)", srv)
    srv = srv.replace("import json\n", "import json\nimport threading\nfrom collections import deque\n", 1)
    assert "from collections import deque" in srv

# 2) POST /api/pose/record 参数语义替换
old_post = '''            elif p == "/api/pose/record":
                name = str(data.get("name", "")).strip()
                seconds = float(data.get("seconds", 5))
                hz = float(data.get("hz", 25))
                if not NAME_RE.match(name):
                    self._json({"error": "采集名只能用中文/字母/数字/_/-，≤24 字"})
                    return
                if not (1.0 <= seconds <= 60.0):
                    self._json({"error": "时长 1-60 秒"})
                    return
                self._json(start_record(name, seconds, hz))'''
new_post = '''            elif p == "/api/pose/record":
                name = str(data.get("name", "")).strip()
                pre = float(data.get("pre", 10))
                post = float(data.get("post", 3))
                if not NAME_RE.match(name):
                    self._json({"error": "采集名只能用中文/字母/数字/_/-，≤24 字"})
                    return
                if not (1.0 <= pre <= RING_SECONDS) or not (0.0 <= post <= 30.0):
                    self._json({"error": "前置 1-%d 秒 / 后补 0-30 秒" % RING_SECONDS})
                    return
                self._json(start_record(name, pre, post))'''
assert old_post in srv, "POST record 语义锚点未找到"
srv = srv.replace(old_post, new_post, 1)

# 3) 启动滚录线程(main 处)
a_main = "    ThreadingHTTPServer(addr, Handler).serve_forever()"
b_main = "    threading.Thread(target=_ring_loop, daemon=True).start()\n" + a_main
assert a_main in srv, "main 锚点未找到"
srv = srv.replace(a_main, b_main, 1)

SRV.write_text(srv, encoding="utf-8")
ast.parse(SRV.read_text(encoding="utf-8"))
print("server.py v3 环形抓拍 ✓")

# ================= index.html =================
idx = IDX.read_text(encoding="utf-8")
a_ui = '''  <div class="row" style="margin-top:6px">
    <select id="motionSec"><option value="5">5 秒</option><option value="10">10 秒</option><option value="20">20 秒</option></select>
    <button id="btnMotionRec">🎥 采集运动姿态</button>
    <span id="motionStatus" class="dim"></span>
  </div>'''
b_ui = '''  <div class="row" style="margin-top:6px">
    <span class="dim">问题姿态抓拍(环形滚录):</span>
    <select id="motionSec"><option value="5">前5秒</option><option value="10" selected>前10秒</option><option value="15">前15秒</option></select>
    <span class="dim">+后3秒</span>
    <button class="primary" id="btnMotionRec">🎥 抓取问题姿态</button>
    <span id="motionStatus" class="dim"></span>
  </div>'''
assert a_ui in idx, "前端采集行锚点未找到"
idx = idx.replace(a_ui, b_ui, 1)

a_js = '''  const sec=parseFloat($("#motionSec").value||"5");
  const st=$("#motionStatus");
  st.textContent="启动中...";
  const r=await api("/api/pose/record",{name,seconds:sec,hz:25});
  if(r.error){toast(r.error);st.textContent="";return}'''
b_js = '''  const pre=parseFloat($("#motionSec").value||"10");
  const st=$("#motionStatus");
  st.textContent="抓拍中(前"+pre+"秒+后3秒)...";
  const r=await api("/api/pose/record",{name,pre:pre,post:3});
  if(r.error){toast(r.error);st.textContent="";return}'''
assert a_js in idx, "前端 JS 锚点未找到"
idx = idx.replace(a_js, b_js, 1)
IDX.write_text(idx, encoding="utf-8")
chk = IDX.read_text(encoding="utf-8")
assert "抓取问题姿态" in chk and "pre:pre" in chk
print("index.html v3 ✓")

# ================= duck_console.py =================
con = CON.read_text(encoding="utf-8")
a_api = "        elif self.path == '/api/imu_scope':"
b_api = """        elif self.path == '/api/pose_grab':
            self._json(pose_grab())
        elif self.path == '/api/imu_scope':"""
assert a_api in con, "console GET 锚点未找到"
con = con.replace(a_api, b_api, 1)

a_fn = "def get_blackbox():"
b_fn = '''def pose_grab():
    """运动模式问题姿态抓拍: 转发标定台的环形缓冲抓拍(前10+后3秒)。"""
    try:
        req = urllib.request.Request(
            'http://127.0.0.1:8085/api/pose/record',
            data=json.dumps({'name': '问题' + time.strftime('%H%M%S'),
                             'pre': 10, 'post': 3}).encode(),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read())
    except Exception as e:
        return {'error': str(e)[:150]}


def get_blackbox():'''
assert a_fn in con, "console 函数锚点未找到"
con = con.replace(a_fn, b_fn, 1)

a_btn = '<button onclick="location.href=\'http://DUCK:8085\'">🔧 标定台</button>'
b_btn = a_btn + '''
<button onclick="poseGrab()">🎥 抓问题姿态</button>'''
assert a_btn in con, "console 按钮锚点未找到"
con = con.replace(a_btn, b_btn, 1)

a_js2 = "tick();"
b_js2 = """async function poseGrab(){
  try{const r=await(await fetch('/api/pose_grab')).json();
    alert(r.error?('抓拍失败: '+r.error):'✅ 已抓拍「'+r.started+'」(前10秒+后3秒), 在标定台-姿态库查看');}
  catch(e){alert('失败: '+e)}
}
tick();"""
assert a_js2 in con, "console JS 锚点未找到"
con = con.replace(a_js2, b_js2, 1)

CON.write_text(con, encoding="utf-8")
ast.parse(CON.read_text(encoding="utf-8"))
assert "\\n" not in re.search(r'PAGE = """(.*?)"""', CON.read_text(encoding="utf-8"), re.S).group(1), "模板出现字面\\n!"
print("duck_console.py 快捷按钮 ✓")
