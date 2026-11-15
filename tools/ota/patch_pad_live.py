#!/usr/bin/env python3
"""给 pad_live.py 添加: 蓝牙配对端点 + 配对按钮 + 导航链接。"""
from pathlib import Path

P = Path('/root/pad_live.py')
s = P.read_text(encoding='utf-8')

# 1. 添加配对端点
old = "    def do_POST(self):\n        if self.path == '/hold':"
new = """    def do_POST(self):
        if self.path == '/api/bt-pair':
            proc = subprocess.run(
                ['/opt/duck-calib/bt_pair_proper.py'],
                capture_output=True, text=True, timeout=60)
            result = proc.stdout[-800:] + ('\\nERR: ' + proc.stderr[-200:] if proc.returncode else '')
            body = json.dumps({'ok': proc.returncode == 0, 'output': result},
                              ensure_ascii=False).encode()
            self._send(body, 'application/json; charset=utf-8')
        elif self.path == '/hold':"""
assert old in s, 'POST anchor'
s = s.replace(old, new, 1)

# 2. 在标题下加导航栏 + 配对按钮
old2 = '''<h1>🎮 启明星手柄监视器 <span class="dim">· 鸭子 192.168.31.96</span></h1>'''
new2 = '''<h1>🎮 启明星手柄监视器 <span class="dim">· 鸭子 192.168.31.96</span></h1>
<div style="display:flex;gap:8px;margin:8px 0;flex-wrap:wrap">
<a href="http://192.168.31.96:8085" style="color:#6ab0e8;text-decoration:none;font-size:13px;border:1px solid #2c4a6a;border-radius:6px;padding:4px 10px">🔧 标定台</a>
<a href="http://192.168.31.96:8086" style="color:#6ab0e8;text-decoration:none;font-size:13px;border:1px solid #2c4a6a;border-radius:6px;padding:4px 10px">🔗 配对页</a>
<a href="http://192.168.31.96:8096" style="color:#6ab0e8;text-decoration:none;font-size:13px;border:1px solid #2c4a6a;border-radius:6px;padding:4px 10px">📊 训练台</a>
<button id="btPairBtn" onclick="btPair()" style="background:#5a3a1e;border:1px solid #8a6030;color:#ffb84d;border-radius:6px;padding:4px 10px;font-size:13px;cursor:pointer">🎮 蓝牙配对</button>
</div>
<div id="btPairResult" style="display:none;background:#1e242a;border:1px solid #3a4550;border-radius:8px;padding:10px;margin:8px 0;font-size:12px;font-family:monospace;white-space:pre-wrap;max-height:200px;overflow-y:auto"></div>'''

assert old2 in s, 'title anchor'
s = s.replace(old2, new2, 1)

# 3. 在 </script> 前加配对函数
old3 = " setTimeout(tick,180);"
new3 = """ setTimeout(tick,180);
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
  btn.disabled=false; btn.textContent='🎮 蓝牙配对';"""
assert old3 in s, 'script anchor'
s = s.replace(old3, new3, 1)

P.write_text(s, encoding='utf-8')
import py_compile; py_compile.compile(str(P), doraise=True)
print('pad_live.py patched OK')
