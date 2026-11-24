#!/usr/bin/env python3
"""pad_live.py — GameSir 手柄实时监视器（端口 8091）

三个数据源：
  xbox  = 桥输出的虚拟 Xbox（= padd 实际读到的，桥运行时永远可见）
  raw   = 蓝牙原始手柄节点（桥运行时被其独占，看不见是正常的）
  cons  = 原始节点的媒体键子节点（同样被桥独占；Start 之谜最可能藏在这）

POST /hold — "抓原始键码"模式：临时停 gamesir-xboxd + padd（此时手柄不可能驱动机器人），
90 秒后自动恢复。停桥期间 grab 释放，raw/cons 的按键码就都能看见了。
"""
import http.server
import json
import re
import select
import struct
import subprocess
import threading
import time

PORT = 8091
NODES = {
    'xbox': 'Xbox 360 Controller (GameSir Nova 2 Lite)',
    'raw': 'GameSir-Nova Lite',
    'cons': 'GameSir-Nova Lite Consumer Control',
}
LABEL = {'xbox': '桥输出·Xbox', 'raw': '原始手柄', 'cons': '媒体键节点'}
BTN = {304: 'A(下)', 305: 'B(右)', 306: 'C', 307: 'Y(上)', 308: 'X(左)', 309: 'Z',
       310: 'LB', 311: 'RB', 312: 'TL2', 313: 'TR2', 314: 'SELECT', 315: 'START',
       316: 'HOME', 317: 'L3(左摇杆按)', 318: 'R3(右摇杆按)'}
KEYN = {102: 'Home键', 158: '返回Back', 159: '前进Forward', 163: '下一曲', 164: '播放/暂停',
        165: '上一曲', 166: '停止', 226: '媒体键', 353: '选择Select', 358: '信息Info',
        364: '频道+', 365: '频道-', 402: '频道上', 403: '频道下'}
AXN = {0: 'LX', 1: 'LY', 2: 'LT(Z)', 3: 'RX', 4: 'RY', 5: 'RT(RZ)',
       9: 'RT(GAS)', 10: 'LT(BRAKE)', 16: '十字左右', 17: '十字上下'}

state = {k: {'present': False, 'buttons': {}, 'axes': {}, 'last_down': {}} for k in NODES}
events = []
lock = threading.Lock()
fds = {}
paths = {}
hold_until = 0.0


def find_event(name):
    try:
        text = open('/proc/bus/input/devices').read()
    except OSError:
        return None
    for block in text.split('\n\n'):
        if f'N: Name="{name}"' in block:
            m = re.search(r'event\d+', block)
            if m:
                return '/dev/input/' + m.group(0)
    return None


def rescan():
    for kind, name in NODES.items():
        path = find_event(name)
        with lock:
            if path == paths.get(kind):
                continue
            old = fds.pop(kind, None)
            if old is not None:
                try:
                    old.close()
                except OSError:
                    pass
            paths[kind] = path
            state[kind]['present'] = path is not None
        if path:
            try:
                fd = open(path, 'rb', buffering=0)
                import os
                os.set_blocking(fd.fileno(), False)
                with lock:
                    fds[kind] = fd
                push_event('系统', f'{LABEL[kind]} 节点已接入 {path}', 1)
            except OSError:
                with lock:
                    paths[kind] = None
                    state[kind]['present'] = False


def push_event(dev, name, val):
    events.append({'t': round(time.time(), 2), 'dev': dev, 'name': name, 'val': val})
    del events[:-120]


def reader_loop():
    while True:
        rescan()
        with lock:
            watch = list(fds.items())
        if not watch:
            time.sleep(0.5)
            continue
        try:
            r, _, _ = select.select([f for _, f in watch], [], [], 2.0)
        except (OSError, ValueError):
            time.sleep(1.0)
            continue
        now = time.time()
        for kind, fd in watch:
            if fd not in r:
                continue
            try:
                data = fd.read(4096)
            except (BlockingIOError, OSError):
                continue
            for i in range(0, len(data) - 23, 24):
                _, _, typ, code, val = struct.unpack_from('<qqHHi', data, i)
                if typ == 1:  # EV_KEY
                    label = BTN.get(code) or KEYN.get(code) or f'键码{code}'
                    with lock:
                        state[kind]['buttons'][code] = val
                        if val == 1:
                            state[kind]['last_down'][code] = now
                    if val != 2:
                        push_event(LABEL[kind], label, val)
                elif typ == 3:  # EV_ABS
                    with lock:
                        state[kind]['axes'][code] = val
                    if code in (16, 17) and val != 0:
                        push_event(LABEL[kind], AXN.get(code, code), val)


def hold_loop():
    global hold_until
    stopped = False
    while True:
        now = time.time()
        if stopped and now >= hold_until:
            for unit in ('gamesir-xboxd.service', 'padd.service'):
                subprocess.run(['systemctl', 'start', unit], timeout=30,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            push_event('系统', '抓码模式结束，桥与 padd 已恢复', 1)
            stopped = False
        time.sleep(1.0)


def start_hold():
    global hold_until
    with lock:
        if time.time() < hold_until:
            return False
        hold_until = time.time() + 90
    for unit in ('gamesir-xboxd.service', 'padd.service'):
        subprocess.run(['systemctl', 'stop', unit], timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    push_event('系统', '抓码模式：桥与 padd 已停 90 秒，现在按手柄上的键看原始码', 1)
    return True


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype='text/html; charset=utf-8'):
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/state':
            with lock:
                payload = json.dumps({
                    'now': time.time(),
                    'hold_until': hold_until,
                    'nodes': {k: state[k] for k in NODES},
                    'events': events[-48:],
                }, ensure_ascii=False)
            self._send(payload.encode(), 'application/json; charset=utf-8')
        else:
            self._send(PAGE.encode())

    def do_POST(self):
        if self.path == '/api/bt-pair':
            proc = subprocess.run(
                ['/opt/duck-calib/bt_pair_proper.py'],
                capture_output=True, text=True, timeout=60)
            result = proc.stdout[-800:] + ('\nERR: ' + proc.stderr[-200:] if proc.returncode else '')
            body = json.dumps({'ok': proc.returncode == 0, 'output': result},
                              ensure_ascii=False).encode()
            self._send(body, 'application/json; charset=utf-8')
        elif self.path == '/hold':
            ok = start_hold()
            body = json.dumps({'ok': ok}, ensure_ascii=False).encode()
            self._send(body, 'application/json; charset=utf-8')
        else:
            self.send_error(404)


PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>启明星手柄监视器</title><style>
:root{--bg:#101418;--panel:#1a2027;--line:#2c3540;--txt:#d8e0e8;--dim:#8a97a3;--acc:#ff9d2e;--ok:#39c26d}
*{box-sizing:border-box;margin:0}
body{background:var(--bg);color:var(--txt);font:14px/1.5 system-ui,"Microsoft YaHei",sans-serif;padding:14px}
h1{font-size:17px;margin-bottom:4px}
.dim{color:var(--dim);font-size:12px}
.row{display:flex;gap:14px;flex-wrap:wrap;align-items:flex-start;margin-top:12px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px;flex:1;min-width:330px}
.panel h2{font-size:14px;margin-bottom:8px}
.chip{display:inline-block;border:1px solid var(--line);border-radius:20px;padding:1px 10px;font-size:12px;margin:0 6px 6px 0;color:var(--dim)}
.chip.on{color:var(--txt);border-color:var(--acc);background:rgba(255,157,46,.12)}
.chip.flash{color:#111;background:var(--acc);border-color:var(--acc);font-weight:600}
#holdBtn{background:#243b4d;border:1px solid #3c5a74;color:var(--txt);border-radius:8px;padding:8px 14px;font-size:13px;cursor:pointer}
#holdBtn:disabled{opacity:.5;cursor:default}
.pad{position:relative;width:330px;height:210px;margin:6px auto;background:#141a20;border:1px solid var(--line);border-radius:16px}
.btn{position:absolute;width:46px;height:46px;border-radius:50%;border:2px solid #3a4550;background:#1c242c;display:flex;flex-direction:column;align-items:center;justify-content:center;font-size:12px;color:var(--dim);transition:all .1s}
.btn .fn{font-size:8px;color:inherit}
.btn.rect{border-radius:10px;width:52px;height:26px}
.btn.hot{border-color:var(--acc);color:#111;background:var(--acc);font-weight:700;box-shadow:0 0 14px rgba(255,157,46,.5)}
.stick{position:absolute;width:86px;height:86px;border:2px solid #3a4550;border-radius:50%;background:#141a20}
.knob{position:absolute;width:26px;height:26px;border-radius:50%;background:#3a4550;left:28px;top:28px;transition:all .08s}
.bar{height:10px;border-radius:5px;background:#232c35;position:relative;overflow:hidden;margin:4px 0 10px}
.bar i{position:absolute;top:0;bottom:0;background:var(--acc);border-radius:5px;left:0;width:0}
.axrow{display:flex;gap:10px;align-items:center;font-size:12px;color:var(--dim);margin-top:4px}
.axrow b{width:64px;font-weight:400}
#log{list-style:none;font:12px/1.7 Consolas,monospace}
#log li{border-bottom:1px solid #20282f;padding:1px 2px}
#log li b{color:var(--acc)}
.note{background:#20242a;border-left:3px solid var(--acc);padding:8px 10px;border-radius:6px;font-size:12px;color:var(--dim);margin-top:10px}
</style></head><body>
<h1>🎮 启明星手柄监视器 <span class="dim">· 鸭子 192.168.31.96</span></h1>
<div style="display:flex;gap:8px;margin:8px 0;flex-wrap:wrap">
<a href="http://192.168.31.96:8085" style="color:#6ab0e8;text-decoration:none;font-size:13px;border:1px solid #2c4a6a;border-radius:6px;padding:4px 10px">🔧 标定台</a>
<a href="http://192.168.31.96:8086" style="color:#6ab0e8;text-decoration:none;font-size:13px;border:1px solid #2c4a6a;border-radius:6px;padding:4px 10px">🔗 配对页</a>
<a href="http://192.168.31.96:8096" style="color:#6ab0e8;text-decoration:none;font-size:13px;border:1px solid #2c4a6a;border-radius:6px;padding:4px 10px">📊 训练台</a>
<button id="btPairBtn" onclick="btPair()" style="background:#5a3a1e;border:1px solid #8a6030;color:#ffb84d;border-radius:6px;padding:4px 10px;font-size:13px;cursor:pointer">🎮 蓝牙配对</button>
</div>
<div id="btPairResult" style="display:none;background:#1e242a;border:1px solid #3a4550;border-radius:8px;padding:10px;margin:8px 0;font-size:12px;font-family:monospace;white-space:pre-wrap;max-height:200px;overflow-y:auto"></div>
<div class="dim" id="hint">亮灯 = 鸭子实际收到的键码（桥直通，无变换）。标签 = 键码对应的 Xbox 位置功能。</div>
<div class="row">
<div class="panel"><h2>鸭子收到的（桥输出 · 标准 Xbox）</h2>
  <div class="pad" id="pad">
    <div class="btn rect" data-b="310" style="left:66px;top:14px">LB</div>
    <div class="btn rect" data-b="311" style="left:212px;top:14px">RB</div>
    <div class="btn rect" data-b="314" style="left:118px;top:56px;font-size:10px">SELECT</div>
    <div class="btn rect" data-b="315" style="left:162px;top:56px;font-size:10px">START</div>
    <div class="btn" data-b="316" style="left:136px;top:22px;width:34px;height:34px;font-size:10px">HOME</div>
    <div class="btn" data-b="308" style="left:236px;top:52px">Y·头部</div>
    <div class="btn" data-b="307" style="left:200px;top:86px">X·翻滚</div>
    <div class="btn" data-b="305" style="left:272px;top:86px">B·姿态</div>
    <div class="btn" data-b="304" style="left:236px;top:120px">A·捡拾</div>
    <div class="stick" data-s="L" style="left:34px;top:78px"><div class="knob" id="knobL"></div></div>
    <div class="stick" data-s="R" style="left:212px;top:110px"><div class="knob" id="knobR"></div></div>
    <div class="btn" data-b="317" style="left:62px;top:106px;width:30px;height:30px;font-size:9px">L3</div>
    <div class="btn" data-b="318" style="left:240px;top:138px;width:30px;height:30px;font-size:9px">R3</div>
    <div class="btn" data-b="-16" style="left:118px;top:96px;width:32px;height:32px;font-size:12px">✛</div>
  </div>
  <div class="axrow"><b>左摇杆</b><span id="lsTxt">0, 0</span></div>
  <div class="axrow"><b>右摇杆</b><span id="rsTxt">0, 0</span></div>
  <div class="axrow"><b>LT 扳机</b></div><div class="bar"><i id="ltBar"></i></div>
  <div class="axrow"><b>RT 扳机</b></div><div class="bar"><i id="rtBar"></i></div>
</div>
<div class="panel"><h2>原始节点 + 媒体键节点</h2>
  <div id="rawChips"><span class="dim">桥运行时被独占——点下面按钮进入抓码模式</span></div>
  <div style="margin-top:10px"><button id="holdBtn">🔓 抓原始键码（停桥+padd 90秒，鸭子绝不会动）</button></div>
  <div class="dim" id="holdInfo" style="margin-top:6px"></div>
  <div class="note">Start 键之谜：在抓码模式里按一下 HOME 右边的小键，看"原始手柄"或"媒体键节点"里哪个名字亮——把亮的名字告诉我，我就能让桥把它转成 START。</div>
</div>
</div>
<div class="panel row" style="margin-top:0"><div style="flex:1;min-width:300px"><h2>事件流（最新在上）</h2><ul id="log"></ul></div></div>
<script>
const BTN={304:'A(下)',305:'B(右)',306:'C',307:'X(左)',308:'Y(上)',310:'LB',311:'RB',314:'SELECT',315:'START',316:'HOME',317:'L3',318:'R3'};
let lastT=0;
async function tick(){
 try{
  const s=await (await fetch('/state')).json();
  const skew=s.now-Date.now()/1000;
  const x=s.nodes.xbox, now=Date.now()/1000+skew;
  document.querySelectorAll('#pad .btn').forEach(el=>{
    const b=+el.dataset.b; let hot=false;
    if(b>=0){ hot=(x.buttons[b]===1)||(now-(x.last_down[b]||0)<1.2); }
    else { const ax=x.axes[-b]; hot=(ax!==undefined&&ax!==0); }
    el.classList.toggle('hot',hot);
  });
  const lx=x.axes[0]||0, ly=x.axes[1]||0, rx=x.axes[3]||0, ry=x.axes[4]||0;
  const put=(id,v)=>{const e=document.getElementById(id);if(e){e.style.left=(28+v*26)+'px';}};
  const putY=(id,v)=>{const e=document.getElementById(id);if(e){e.style.top=(28+v*26)+'px';}};
  put('knobL',lx/32768);putY('knobL',ly/32768);put('knobR',rx/32768);putY('knobR',ry/32768);
  document.getElementById('lsTxt').textContent=(lx/32768).toFixed(2)+', '+(ly/32768).toFixed(2);
  document.getElementById('rsTxt').textContent=(rx/32768).toFixed(2)+', '+(ry/32768).toFixed(2);
  const lt=Math.max(x.axes[2]||0,x.axes[10]||0), rt=Math.max(x.axes[5]||0,x.axes[9]||0);
  document.getElementById('ltBar').style.width=(lt/255*100)+'%';
  document.getElementById('rtBar').style.width=(rt/255*100)+'%';
  const chips=[];
  for(const k of ['raw','cons']){
    const n=s.nodes[k];
    chips.push('<span class="chip '+(n.present?'on':'')+'">'+(k==='raw'?'原始':'媒体键')+(n.present?'':'(不在)')+'</span>');
    for(const c in n.buttons){ if(n.buttons[c]===1||now-(n.last_down[c]||0)<1.2){
      chips.push('<span class="chip flash">'+(BTN[c]||('码'+c))+' 按下</span>');}}
  }
  const hx=x.axes[16],hy=x.axes[17];
  if(hx)chips.push('<span class="chip on">十字 '+(hx<0?'←':'→')+'</span>');
  if(hy)chips.push('<span class="chip on">十字 '+(hy<0?'↑':'↓')+'</span>');
  if(s.hold_until>now){const left=Math.ceil(s.hold_until-now);
    document.getElementById('holdBtn').disabled=true;
    document.getElementById('holdInfo').textContent='抓码模式进行中，还剩 '+left+' 秒（结束后自动恢复桥和 padd）';
  }else{document.getElementById('holdBtn').disabled=false;
    document.getElementById('holdInfo').textContent='';}
  document.getElementById('rawChips').innerHTML=chips.join('')||'<span class="dim">无</span>';
  const seen=s.events[s.events.length-1];
  if(seen&&seen.t!==lastT){lastT=seen.t;
    const ul=document.getElementById('log');
    ul.innerHTML=s.events.slice().reverse().map(e=>'<li>'+new Date(e.t*1000).toLocaleTimeString()+' <b>['+e.dev+']</b> '+e.name+(e.val===0?' 松开':'')+'</li>').join('');
  }
 }catch(e){}
 setTimeout(tick,180);
}
async function btPair(){
  const btn=document.getElementById('btPairBtn');
  const out=document.getElementById('btPairResult');
  btn.disabled=true; btn.textContent='配对中…';
  out.style.display='block'; out.textContent='正在配对（手柄需 A+HOME 蓝灯快闪）…';
  try{
    const r=await fetch('/api/bt-pair',{method:'POST'});
    const d=await r.json();
    out.textContent=d.output||d.error||JSON.stringify(d);
    out.style.borderColor=d.ok?'#39c26d':'#e74c3c';
  }catch(e){ out.textContent='错误: '+e; out.style.borderColor='#e74c3c'; }
  btn.disabled=false; btn.textContent='🎮 蓝牙配对';
}
document.getElementById('holdBtn').onclick=async()=>{await fetch('/hold',{method:'POST'});};
tick();
</script></body></html>"""


def main():
    threading.Thread(target=reader_loop, daemon=True).start()
    threading.Thread(target=hold_loop, daemon=True).start()
    srv = http.server.ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    print(f'pad_live on :{PORT}', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
