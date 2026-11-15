#!/usr/bin/env python3
from pathlib import Path
P = Path('/opt/duck-calib/server.py')
s = P.read_text(encoding='utf-8')
old = '            elif p == "/api/mode":'
bt = '            elif p == "/api/bt-pair":\n'
bt += '                import subprocess\n'
bt += '                proc = subprocess.run(["/opt/duck-calib/bt-pair.sh"],\n'
bt += '                    capture_output=True, text=True, timeout=30)\n'
bt += '                self._json({"ok": proc.returncode == 0,\n'
bt += '                            "output": proc.stdout[-500:],\n'
bt += '                            "error": proc.stderr[-200:] if proc.returncode else None})\n'
assert old in s, 'anchor not found'
s = s.replace(old, bt + old, 1)
P.write_text(s, encoding='utf-8')
import py_compile; py_compile.compile(str(P), doraise=True)
print('PATCHED')
