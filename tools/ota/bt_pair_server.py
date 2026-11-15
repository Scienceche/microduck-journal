#!/usr/bin/env python3
"""独立蓝牙配对服务器 — :8086 一键配对页面。"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import subprocess
import json

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>蓝牙配对</title>
<style>body{font-family:system-ui;background:#1a1f25;color:#e0e0e0;display:flex;
flex-direction:column;align-items:center;padding:40px}
button{font-size:20px;padding:20px 60px;background:#1c4a78;color:#fff;border:2px solid #2b6db1;
border-radius:12px;cursor:pointer;margin:20px}
button:hover{background:#2b6db1}
.result{font-size:14px;white-space:pre-wrap;font-family:monospace;
background:#222b35;padding:15px;border-radius:8px;margin:10px;max-width:600px}</style></head>
<body>
<h2>🎮 手柄蓝牙配对</h2>
<p>1. 手柄按 A+HOME 进入配对模式（蓝灯快闪）<br>2. 点下面按钮</p>
<button onclick="pair()">🔗 立刻配对</button>
<div class="result" id="out">等待操作...</div>
<script>
async function pair(){
  document.getElementById('out').textContent = '配对中...';
  try{
    const r = await fetch('/api/pair', {method:'POST'});
    const d = await r.json();
    document.getElementById('out').textContent = d.output || d.error || JSON.stringify(d);
  }catch(e){
    document.getElementById('out').textContent = '错误: ' + e;
  }
}
</script></body></html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def do_POST(self):
        if self.path == "/api/pair":
            proc = subprocess.run(
                ["/opt/duck-calib/bt-pair.sh"],
                capture_output=True, text=True, timeout=30)
            result = {
                "ok": proc.returncode == 0,
                "output": proc.stdout[-500:],
                "error": proc.stderr[-200:] if proc.returncode else None
            }
            body = json.dumps(result, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

print("蓝牙配对页面: http://192.168.31.96:8086")
HTTPServer(("0.0.0.0", 8086), Handler).serve_forever()
