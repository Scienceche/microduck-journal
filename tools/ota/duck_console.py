#!/usr/bin/env python3
"""鸭子统一中控台 — :8090 一个入口访问所有工具。

面板: 仪表盘(状态总览) | 手柄(实时监视+配对) | 标定(校零/扭矩) | 黑匣子(事件) | 服务
"""
import http.server
import json
import subprocess
import threading
import time

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

<!-- 舵机调试 -->
<div id="page-servo" class="hidden">
<iframe src="http://DUCK:8082"></iframe>
</div>

</main>
<script>
function show(id){
  document.querySelectorAll('main > div').forEach(e=>e.classList.add('hidden'));
  document.getElementById('page-'+id).classList.remove('hidden');
  document.querySelectorAll('nav a').forEach(e=>e.classList.remove('active'));
  document.getElementById('nav-'+id).classList.add('active');
  if(id==='blackbox') loadBB();
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
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path == '/api/rpc':
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            self._json(rpc_call(body.get('method', '')))
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
    print(f"🦆 鸭子中控台: http://{DUCK}:{PORT}")
    http.server.HTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
