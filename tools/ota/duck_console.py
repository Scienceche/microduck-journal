#!/usr/bin/env python3
"""鸭子统一中控台 — :8090 一个入口访问所有工具。

面板: 仪表盘(状态总览) | 手柄(实时监视+配对) | 标定(校零/扭矩) | 黑匣子(事件) | 服务
"""
import http.server
import json
import math
import subprocess
import threading
import time
import urllib.error
import urllib.request

PORT = 8090
DUCK = "192.168.31.96"

PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>🦆 鸭子中控台</title>
<style>
:root{--bg:#0d1117;--nav:#161b22;--panel:#1c2128;--line:#30363d;--txt:#e6edf3;--dim:#8b949e;
--acc:#58a6ff;--ok:#3fb950;--warn:#d29922;--err:#f85149;--purple:#bc8cff}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--txt);font:14px/1.5 system-ui,"Microsoft YaHei",sans-serif;display:flex;min-height:100vh}
nav{background:var(--nav);border-right:1px solid var(--line);padding:14px 0;min-width:200px;position:fixed;top:0;bottom:0;left:0;z-index:10}
nav h1{font-size:16px;padding:0 16px 14px;border-bottom:1px solid var(--line);color:var(--acc)}
nav a{display:flex;align-items:center;gap:8px;padding:10px 16px;color:var(--dim);text-decoration:none;font-size:14px;transition:.15s}
nav a:hover{color:var(--txt);background:rgba(88,166,255,.1)}
nav a.active{color:var(--acc);background:rgba(88,166,255,.15);border-left:3px solid var(--acc)}
main{margin-left:200px;padding:20px;flex:1}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
.panel h2{font-size:15px;margin-bottom:12px;color:var(--txt)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
.stat{display:flex;align-items:center;gap:10px;padding:10px 14px;background:var(--bg);border:1px solid var(--line);border-radius:8px}
.stat .icon{font-size:24px}
.stat .val{font-size:18px;font-weight:600}
.stat .label{font-size:11px;color:var(--dim)}
.stat.ok .val{color:var(--ok)} .stat.err .val{color:var(--err)} .stat.warn .val{color:var(--warn)}
iframe{width:100%;height:calc(100vh - 140px);border:none;border-radius:8px;background:var(--bg)}
button{background:#21262d;border:1px solid var(--line);color:var(--txt);border-radius:8px;padding:8px 16px;font-size:13px;cursor:pointer}
button:hover{border-color:var(--acc)}
button.primary{background:#1a3a5c;border-color:var(--acc);color:var(--acc)}
.log{font:12px/1.6 monospace;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:10px;max-height:300px;overflow-y:auto;white-space:pre-wrap}
.hidden{display:none}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.badge.on{background:rgba(63,185,80,.15);color:var(--ok);border:1px solid var(--ok)}
.badge.off{background:rgba(248,81,73,.15);color:var(--err);border:1px solid var(--err)}
</style></head><body>
<nav>
<h1>🦆 鸭子中控台</h1>
<a href="#" onclick="show('dash')" id="nav-dash" class="active">📊 仪表盘</a>
<a href="#" onclick="show('pad')" id="nav-pad">🎮 手柄监视</a>
<a href="#" onclick="show('calib')" id="nav-calib">🔧 标定台</a>
<a href="#" onclick="show('pair')" id="nav-pair">🔗 蓝牙配对</a>
<a href="#" onclick="show('blackbox')" id="nav-blackbox">📦 黑匣子</a>
<a href="#" onclick="show('train')" id="nav-train">🏃 训练台</a>
<a href="#" onclick="show('servo')" id="nav-servo">⚙️ 舵机调试</a>
<a href="#" onclick="show('imu')" id="nav-imu">🧭 IMU</a>
<div style="padding:12px 16px;margin-top:auto;border-top:1px solid var(--line);font-size:11px;color:var(--dim)">
  DUCK_IP: 192.168.31.96<br>
  <span id="uptime"></span>
</div>
</nav>
<main>

<!-- 仪表盘 -->
<div id="page-dash">
<div class="grid">
<div class="stat ok" id="st-robotd"><span class="icon">🤖</span><div><div class="val" id="v-mode">--</div><div class="label">robotd 模式</div></div></div>
<div class="stat" id="st-servos"><span class="icon">🔩</span><div><div class="val" id="v-servos">--</div><div class="label">舵机在线</div></div></div>
<div class="stat" id="st-batt"><span class="icon">🔋</span><div><div class="val" id="v-batt">--</div><div class="label">电池电压</div></div></div>
<div class="stat" id="st-policy"><span class="icon">🎯</span><div><div class="val" id="v-policy">--</div><div class="label">策略状态</div></div></div>
<div class="stat" id="st-pad"><span class="icon">🎮</span><div><div class="val" id="v-pad">--</div><div class="label">手柄</div></div></div>
<div class="stat" id="st-blackbox"><span class="icon">📦</span><div><div class="val" id="v-bb">--</div><div class="label">黑匣子</div></div></div>
</div>
<div class="panel"><h2>快速操作</h2>
<div style="display:flex;gap:10px;flex-wrap:wrap">
<button class="primary" onclick="api('robot.init')">▶️ 初始化/站立</button>
<button onclick="api('robot.relax')">⏹ 松开/停止</button>
<button onclick="location.href='http://DUCK:8091'">🎮 手柄监视</button>
<button onclick="location.href='http://DUCK:8085'">🔧 标定台</button>
</div></div>
<div class="panel"><h2>系统日志</h2><div class="log" id="syslog">等待数据...</div></div>
</div>

<!-- 手柄（iframe 嵌入 :8091） -->
<div id="page-pad" class="hidden">
<iframe src="http://DUCK:8091"></iframe>
</div>

<!-- 标定（iframe 嵌入 :8085） -->
<div id="page-calib" class="hidden">
<iframe src="http://DUCK:8085"></iframe>
</div>

<!-- 配对（iframe 嵌入 :8086） -->
<div id="page-pair" class="hidden">
<iframe src="http://DUCK:8086"></iframe>
</div>

<!-- 黑匣子 -->
<div id="page-blackbox" class="hidden">
<div class="panel"><h2>📦 黑匣子事件</h2><div class="log" id="bblog">加载中...</div></div>
<div class="panel"><h2>心跳日志</h2><div class="log" id="hb-log">加载中...</div></div>
</div>

<!-- 训练台 -->
<div id="page-train" class="hidden">
<iframe src="http://DUCK:8096"></iframe>
</div>

<!-- 舵机调试（原版 HLS 工具 + 调试模式：停 robotd 让出总线） -->
<div id="page-servo" class="hidden">
<div class="panel" style="margin:0 0 10px 0">
  <h2>⚙️ 舵机调试模式（原版 HLS 工具）</h2>
  <div id="sd-banner" class="stat warn" style="margin-bottom:10px;padding:10px 14px;border-radius:8px;border:1px solid var(--line)">
    状态加载中...
  </div>
  <div style="display:flex;gap:10px;flex-wrap:wrap">
    <button class="primary" onclick="sdOn()">🔌 进入调试模式（停 robotd → 连接总线）</button>
    <button onclick="sdOff()">↩️ 退出调试模式（断开 → 拉起 robotd）</button>
  </div>
  <div style="margin-top:8px;font-size:12px;color:var(--dim)">
    进入调试模式会停止 robotd：策略/运动全部暂停，鸭子不会再被驱动。排查舵机接线、
    扫描 ID、校零都在这个模式下做；用完记得退出恢复。
  </div>
</div>
<iframe src="http://DUCK:8082"></iframe>
</div>

<!-- IMU 监视 -->
<div id="page-imu" class="hidden">
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px;margin-bottom:10px">
<div class="panel"><h2>🧭 姿态（robot.state 流）</h2>
  <div id="imu-tilt" style="font-size:44px;font-weight:700;margin:8px 0">--°</div>
  <div id="imu-detail" style="font-size:13px;color:var(--dim);white-space:pre-wrap">等待数据...</div>
</div>
<div class="panel"><h2>📡 总线探活（调试模式）</h2>
  <div style="font-size:12px;color:var(--dim);margin-bottom:8px">
    IMU 是总线设备 ID 200。robotd 活着时它独占总线，探活需先在「⚙️ 舵机调试」进入调试模式。
  </div>
  <button class="primary" onclick="imuPing()">Ping IMU (ID 200)</button>
  <div id="imu-ping" style="margin-top:10px;font-size:14px"></div>
</div>
</div>
<div class="panel" style="margin-bottom:10px">
  <h2>🔬 原版 IMU 仪表（IMU bench · 3D 姿态 + 瀑布图）</h2>
  <div id="is-banner" class="stat warn" style="margin-bottom:10px;padding:10px 14px;border-radius:8px;border:1px solid var(--line)">状态加载中...</div>
  <div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:8px">
    <button class="primary" onclick="isOn()">🔌 启动仪表（停 robotd → 起 imu-scope）</button>
    <button onclick="isOff()">↩️ 停止仪表（恢复 robotd）</button>
  </div>
  <div style="font-size:12px;color:var(--dim)">启动后在下方仪表页里点连接（ttyS2 · 1000000 · FEE/md1）即可看 3D 姿态与瀑布图。与舵机调试模式互斥，自动先断开 HLS。</div>
</div>
<iframe src="http://DUCK:8081"></iframe>
</div>

</main>
<script>
function show(id){
  document.querySelectorAll('main > div').forEach(e=>e.classList.add('hidden'));
  document.getElementById('page-'+id).classList.remove('hidden');
  document.querySelectorAll('nav a').forEach(e=>e.classList.remove('active'));
  document.getElementById('nav-'+id).classList.add('active');
  if(id==='blackbox') loadBB();
  if(id==='servo') sdTick();
  if(id==='imu') imuTick();
}
let imuTimer=null;
async function imuTick(){
  clearInterval(imuTimer);
  imuTimer=setInterval(imuRefresh,1000);
  await imuRefresh();
}
async function imuRefresh(){
  try{
    const r=await(await fetch('/api/imu')).json();
    const t=document.getElementById('imu-tilt'), d=document.getElementById('imu-detail');
    if(t){
      if(r.ok){
        t.textContent=(r.tilt!=null?r.tilt:'?')+'°';
        t.style.color=r.tilt!=null&&r.tilt<30?'#4caf50':(r.tilt!=null?'#ff9800':'#888');
        d.textContent='重力向量: '+(r.gravity?r.gravity.map(x=>Number(x).toFixed(3)).join(', '):'无')
          +(r.fallen?'  ⚠️ fallen':'')
          +'  |  数据龄: '+r.age_s+'s'+(r.age_s>3?'（停更!）':'')
          +(r.imu_raw?'  |  imu原始: '+JSON.stringify(r.imu_raw).slice(0,120):'');
      }else{
        t.textContent='--°'; t.style.color='#f44336';
        d.textContent='❌ '+r.reason;
      }
    }
  }catch(e){}
  try{
    const s=await(await fetch('/api/imu_scope')).json();
    const b=document.getElementById('is-banner');
    if(b){
      const r2=await(await fetch('/api/servo_debug')).json();
      let txt,cls='warn';
      if(s.service==='active'&&s.http){
        txt='🟢 仪表运行中（:8081）· robotd: '+r2.robotd+'。在下方点「连接」开始看数据。';
        cls='ok';
      }else if(s.service==='active'){
        txt='🟠 imu-scope 服务在跑但页面无响应(:8081)，稍候或重启仪表'; 
      }else{
        txt='⚪ 仪表未启动。robotd: '+r2.robotd+'。要看 3D 姿态/瀑布图点上方「启动仪表」。';
      }
      b.textContent=txt; b.className='stat '+cls;
    }
  }catch(e){}
}
async function isOn(){
  if(!confirm('启动原版 IMU 仪表？robotd 将停止，HLS 若连接会先断开。')) return;
  const b=document.getElementById('is-banner'); b.textContent='⏳ 正在启动仪表...';
  try{const r=await(await fetch('/api/imu_scope/on',{method:'POST'})).json();
    alert(r.ok?'✅ 仪表已启动，在下方仪表页点「连接」（ttyS2 · 1M · FEE）':'❌ '+(r.error||'启动失败'));
  }catch(e){alert('失败: '+e)}
  imuRefresh();
}
async function isOff(){
  const b=document.getElementById('is-banner'); b.textContent='⏳ 正在停止仪表并恢复 robotd...';
  try{const r=await(await fetch('/api/imu_scope/off',{method:'POST'})).json();
    alert(r.ok?'✅ 已恢复 robotd':'❌ '+(r.error||'失败'));
  }catch(e){alert('失败: '+e)}
  imuRefresh();
}
async function imuPing(){
  const el=document.getElementById('imu-ping');
  el.textContent='⏳ 探测中...';
  try{
    const r=await(await fetch('/api/imu_ping')).json();
    el.innerHTML=r.ok?'✅ <b>IMU 在线</b>（ID 200 应答, status='+r.status+'）'
      :(r.no_answer?'📡 '+r.error:'❌ '+r.error);
  }catch(e){el.textContent='失败: '+e}
}
let sdTimer=null;
async function sdTick(){
  clearInterval(sdTimer);
  sdTimer=setInterval(sdRefresh,3000);
  await sdRefresh();
}
async function sdRefresh(){
  try{
    const r=await(await fetch('/api/servo_debug')).json();
    const b=document.getElementById('sd-banner');
    if(!b) return;
    const hls=r.hls||{};
    let txt,cls='warn';
    if(hls.connected){
      txt='🟢 调试中：HLS 已连接 '+(hls.port||'')+(hls.baud?' @'+hls.baud:'')+'（robotd: '+r.robotd+'）';
      cls='ok';
    }else if(r.robotd==='active'){
      txt='🟡 常规模式：robotd 占用总线，工具未连接。要调试舵机请点下方「进入调试模式」';
    }else{
      txt='🟠 robotd 已停（'+r.robotd+'）但工具未连接。重连可点「进入调试模式」，或「退出」恢复 robotd';
    }
    if(hls.error) txt+=' ⚠️ '+hls.error;
    b.textContent=txt;
    b.className='stat '+cls;
    b.style.whiteSpace='pre-wrap';
  }catch(e){}
}
async function sdOn(){
  if(!confirm('进入舵机调试模式？robotd 将停止（策略/运动暂停），HLS 工具接管总线。')) return;
  const b=document.getElementById('sd-banner'); b.textContent='⏳ 正在停 robotd 并连接总线...';
  try{const r=await(await fetch('/api/servo_debug/on',{method:'POST'})).json();
    alert(r.ok?'✅ 调试模式已进入：HLS 已连接 '+r.port+' 在下方页面扫描/调试舵机。':'❌ '+(r.error||'连接失败'));
  }catch(e){alert('失败: '+e)}
  sdRefresh();
}
async function sdOff(){
  const b=document.getElementById('sd-banner'); b.textContent='⏳ 正在断开并拉起 robotd...';
  try{const r=await(await fetch('/api/servo_debug/off',{method:'POST'})).json();
    alert(r.ok?'✅ 已恢复：robotd 重新运行':'❌ '+(r.error||'失败，手动检查 systemctl status robotd'));
  }catch(e){alert('失败: '+e)}
  sdRefresh();
}
async function api(method){
  try{const r=await fetch('/api/rpc',{method:'POST',body:JSON.stringify({method})});alert(await r.text());}catch(e){alert(e)}
}
async function tick(){
  try{
    const s=await(await fetch('/api/status')).json();
    set('v-mode',s.mode||'--',s.mode==='motion'?'ok':'warn');
    set('v-servos',s.servos+'/',s.servos===15?'ok':'err');
    set('v-batt',s.V?s.V+'V':'--',s.V>=7.8?'ok':s.V>=7.4?'warn':'err');
    set('v-policy',s.policy?'启用':'待机',s.policy?'ok':'');
    set('v-pad',s.pad||'--',s.pad==='connected'?'ok':'err');
    set('v-bb',s.bb||'--',s.bb==='active'?'ok':'err');
    document.getElementById('st-robotd').className='stat '+(s.mode==='motion'?'ok':'warn');
    document.getElementById('st-servos').className='stat '+(s.servos===15?'ok':'err');
    document.getElementById('st-batt').className='stat '+(s.V>=7.8?'ok':s.V>=7.4?'warn':'err');
  }catch(e){}
  setTimeout(tick,2000);
}
function set(id,val,cls){const e=document.getElementById(id);if(e){e.textContent=val;}}
async function loadBB(){
  try{
    const r=await(await fetch('/api/blackbox')).json();
    document.getElementById('bblog').textContent=r.events||'无事件';
    document.getElementById('hb-log').textContent=r.heartbeat||'无';
  }catch(e){document.getElementById('bblog').textContent='加载失败: '+e}
}
tick();
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, body, ctype='text/html; charset=utf-8'):
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode())

    def _json(self, obj, code=200):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode())

    def do_GET(self):
        if self.path == '/':
            self._send(PAGE)
        elif self.path == '/api/status':
            self._json(get_status())
        elif self.path == '/api/blackbox':
            self._json(get_blackbox())
        elif self.path == '/api/servo_debug':
            self._json(servo_debug_status())
        elif self.path == '/api/imu':
            self._json(get_imu())
        elif self.path == '/api/imu_ping':
            self._json(imu_ping())
        elif self.path == '/api/imu_scope':
            self._json(imu_scope_status())
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == '/api/rpc':
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            self._json(rpc_call(body.get('method', '')))
        elif self.path == '/api/servo_debug/on':
            self._json(servo_debug_on())
        elif self.path == '/api/servo_debug/off':
            self._json(servo_debug_off())
        elif self.path == '/api/imu_scope/on':
            self._json(imu_scope_on())
        elif self.path == '/api/imu_scope/off':
            self._json(imu_scope_off())
        else:
            self.send_error(404)


def rpc_call(method):
    try:
        import socket
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(8)
        s.connect('/run/robotd.sock')
        s.sendall((json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method}) + '\n').encode())
        d = json.loads(s.makefile().readline())['result']
        s.close()
        return d
    except Exception as e:
        return {'error': str(e)}


def get_status():
    try:
        d = rpc_call('robot.busStatus')
        js = (d.get('native') or {}).get('joints') or []
        volts = [j.get('volts') for j in js if j.get('volts')]
        return {'mode': d.get('mode'), 'servos': len(js),
                'V': round(sum(volts)/len(volts), 2) if volts else None,
                'policy': d.get('policy_enabled'), 'phase': d.get('phase'),
                'pad': 'connected' if any('GameSir' in str(j) for j in js) else 'unknown',
                'bb': 'active'}
    except Exception:
        return {'mode': 'offline', 'servos': 0, 'V': None}


def hls_api(path, body=None):
    """调本机 HLS 舵机调试器(:8082)的 API。body=None 走 GET。"""
    url = 'http://127.0.0.1:8082' + path
    if body is None:
        req = urllib.request.Request(url)  # GET
    else:
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())


def servo_debug_status():
    """robotd 与 HLS 的占用/连接状态。"""
    robotd = 'unknown'
    try:
        robotd = subprocess.run(['systemctl', 'is-active', 'robotd'],
                                capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        pass
    hls = {}
    try:
        d = hls_api('/api/status').get('data', {})
        hls = {'connected': d.get('connected'), 'port': d.get('port'),
               'baud': d.get('baudrate')}
    except Exception as e:
        hls = {'connected': False, 'error': 'HLS 服务不可达: %s' % e}
    return {'robotd': robotd, 'hls': hls}


def servo_debug_on():
    """进入调试模式: 停 robotd(让出总线) → HLS 连接 ttyS2@1Mbps。"""
    try:
        subprocess.run(['systemctl', 'stop', 'robotd'], timeout=30)
    except Exception as e:
        return {'ok': False, 'error': 'robotd 停止失败: %s' % e}
    time.sleep(1.5)
    try:
        r = hls_api('/api/connect', {'port': '/dev/ttyS2', 'baudrate': 1000000,
                                     'timeout_ms': 100})
        d = r.get('data', {})
        if r.get('ok') and d.get('connected'):
            return {'ok': True, 'port': d.get('port'), 'baud': d.get('baudrate')}
        return {'ok': False, 'error': r.get('error') or json.dumps(d, ensure_ascii=False)[:200]}
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get('error', '')
        except Exception:
            msg = ''
        return {'ok': False, 'error': 'HLS 拒绝连接(HTTP %d): %s' % (e.code, msg)}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def servo_debug_off():
    """退出调试模式: HLS 断开(带重试, 失败要报) → 拉起 robotd。"""
    disc = None
    for _ in range(3):
        try:
            d = hls_api('/api/disconnect', {})  # 必须带 body 走 POST, 否则 405
            disc = True if d.get('data', {}).get('connected') is False else str(d)[:120]
            if d.get('data', {}).get('connected') is False:
                break
        except Exception as e:
            disc = str(e)
        time.sleep(1)
    try:
        subprocess.run(['systemctl', 'start', 'robotd'], timeout=30)
        return {'ok': True, 'hls_disconnected': disc is True,
                'note': '' if disc is True else 'HLS 断开异常(%s), 已重试' % disc}
    except Exception as e:
        return {'ok': False, 'error': 'robotd 启动失败: %s' % e}


# ---------------- IMU 监视(订阅 robot.state 流, 与黑匣子/3D 视图同源) ----------------
IMU_LOCK = threading.Lock()
IMU_CACHE = {'ts': 0.0, 'frame': None}


def _imu_subscriber():
    """后台线程: 订阅 robot.state @10Hz, 缓存最新帧(广播拷贝, 零额外总线事务)。"""
    import socket as sk
    while True:
        try:
            s = sk.socket(sk.AF_UNIX, sk.SOCK_STREAM)
            s.settimeout(3)
            s.connect('/run/robotd.sock')
            s.sendall((json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'robot.subscribe',
                                   'params': {'hz': 10}}) + '\n').encode())
            for line in s.makefile('rb'):
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get('id') == 1 or msg.get('method') != 'robot.state':
                    continue
                if isinstance(msg.get('params'), dict):
                    with IMU_LOCK:
                        IMU_CACHE['ts'] = time.time()
                        IMU_CACHE['frame'] = msg['params']
            s.close()
        except Exception:
            pass
        time.sleep(2)


def get_imu():
    with IMU_LOCK:
        ts, frame = IMU_CACHE['ts'], IMU_CACHE['frame']
    if not frame:
        return {'ok': False, 'reason': '尚无数据：robotd 未就绪或 IMU 断线'}
    safety = frame.get('safety') or {}
    g = safety.get('gravity')
    tilt = None
    if isinstance(g, (list, tuple)) and len(g) == 3:
        try:
            tilt = round(math.degrees(math.acos(max(-1.0, min(1.0, -float(g[2]))))), 1)
        except (ValueError, TypeError):
            pass
    return {'ok': True, 'age_s': round(time.time() - ts, 1), 'tilt': tilt,
            'fallen': safety.get('fallen'), 'gravity': g,
            'imu_raw': frame.get('imu')}


def imu_ping():
    """调试模式下经 HLS 探 IMU(ID 200) 总线应答。

    502 = ping 已发出但无应答(IMU 掉线) —— 这正是诊断价值所在, 不是"未连接"。
    """
    try:
        r = hls_api('/api/ping', {'id': 200, 'timeout_ms': 150})
        return {'ok': bool(r.get('ok')), 'status': (r.get('data') or {}).get('status')}
    except urllib.error.HTTPError as e:
        if e.code == 502:
            try:
                detail = json.loads(e.read()).get('error', '')
            except Exception:
                detail = ''
            return {'ok': False, 'no_answer': True,
                    'error': 'ping 已发出, ID 200 无应答 —— IMU 掉线/未上电。'
                             '插好线后重点这个按钮。(%s)' % detail[:80]}
        return {'ok': False, 'error': 'HLS 异常(HTTP %d)——先在舵机调试页进入调试模式' % e.code}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


# ---------------- 原版 IMU 仪表(imu-scope @:8081, 与 robotd 互斥占用 ttyS2) ----------------
def imu_scope_status():
    svc = 'unknown'
    try:
        svc = subprocess.run(['systemctl', 'is-active', 'imu-scope'],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        pass
    http_ok = False
    try:
        with urllib.request.urlopen('http://127.0.0.1:8081/', timeout=3) as r:
            http_ok = r.status == 200
    except Exception:
        pass
    return {'service': svc, 'http': http_ok}


def imu_scope_on():
    """启动原版 IMU 仪表: 先断 HLS(互斥) → 停 robotd → 起 imu-scope。"""
    try:
        d = hls_api('/api/status').get('data', {})
        if d.get('connected'):
            hls_api('/api/disconnect', {})
            time.sleep(0.5)
    except Exception:
        pass
    try:
        subprocess.run(['systemctl', 'stop', 'robotd'], timeout=30)
    except Exception as e:
        return {'ok': False, 'error': 'robotd 停止失败: %s' % e}
    time.sleep(1.2)
    try:
        subprocess.run(['systemctl', 'start', 'imu-scope'], timeout=30)
        st = {'service': 'inactive', 'http': False}
        for _ in range(10):  # 轮询至多 ~6s 等 HTTP 就绪
            st = imu_scope_status()
            if st['service'] == 'active' and st['http']:
                break
            time.sleep(0.6)
        return {'ok': st['service'] == 'active' and st['http'],
                'service': st['service'], 'http': st['http']}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def imu_scope_off():
    """停止仪表: 停 imu-scope(串口随进程释放) → 拉起 robotd。"""
    try:
        subprocess.run(['systemctl', 'stop', 'imu-scope'], timeout=30)
    except Exception as e:
        return {'ok': False, 'error': 'imu-scope 停止失败: %s' % e}
    time.sleep(0.8)
    try:
        subprocess.run(['systemctl', 'start', 'robotd'], timeout=30)
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'error': 'robotd 启动失败: %s' % e}


def get_blackbox():
    result = {'events': '', 'heartbeat': ''}
    try:
        r = subprocess.run(['ls', '-lt', '/root/blackbox/'], capture_output=True, text=True, timeout=5)
        lines = r.stdout.strip().split('\n')[:10]
        result['events'] = '\n'.join(lines)
    except Exception:
        result['events'] = '无法读取'
    try:
        r = subprocess.run(['tail', '-10', '/root/blackbox/heartbeat.log'],
                          capture_output=True, text=True, timeout=5)
        result['heartbeat'] = r.stdout
    except Exception:
        pass
    return result


if __name__ == '__main__':
    PAGE = PAGE.replace('DUCK', DUCK)
    threading.Thread(target=_imu_subscriber, daemon=True).start()
    print(f"🦆 鸭子中控台: http://{DUCK}:{PORT}")
    http.server.HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
