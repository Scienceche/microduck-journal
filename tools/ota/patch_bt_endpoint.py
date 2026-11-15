#!/usr/bin/env python3
"""给 calib panel server.py 添加蓝牙配对端点。"""
from pathlib import Path

P = Path("/opt/duck-calib/server.py")
s = P.read_text(encoding="utf-8")

# 在 /api/mode 处理之前插入 /api/bt-pair
old = '            elif p == "/api/mode":'
new = '''            elif p == "/api/bt-pair":
                import subprocess
                proc = subprocess.run(["/opt/duck-calib/bt-pair.sh"],
                    capture_output=True, text=True, timeout=30)
                self._json({"ok": proc.returncode == 0,
                            "output": proc.stdout[-500:],
                            "error": proc.stderr[-200:] if proc.returncode else None})
            elif p == "/api/mode":'''

assert old in s, "mode anchor"
s = s.replace(old, new, 1)
P.write_text(s, encoding="utf-8")
import py_compile
py_compile.compile(str(P), doraise=True)
print("server.py patched OK")
