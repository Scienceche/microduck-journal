#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R1 微挪 (26.5,26)→(27.2,26.5) 避开 LED1 焊盘; 清其旧走线; 重灌"""
import io, sys, os, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import pcbnew

b = pcbnew.LoadBoard(os.path.abspath('imu_to_ttl.kicad_pcb'))
assert len(b.GetFootprints()) > 40

s = open('imu_to_ttl.kicad_pcb', encoding='utf-8').read()
i = s.find('(property "Reference" "R1"')
st = s.rfind('\n\t(footprint ', 0, i)
d = 0; j = st + 1
while True:
    if s[j] == '(': d += 1
    elif s[j] == ')':
        d -= 1
        if d == 0: break
    j += 1
blk = s[st:j]
a = re.search(r'\n\t+\(at ([\d.\-]+) ([\d.\-]+)(?: ([\d.\-]+))?\)', blk)
fx, fy, fr = float(a.group(1)), float(a.group(2)), float(a.group(3) or 0)
old = []
for pm in re.finditer(r'\(pad "[^"]+"[^\n]*\n\t+\(at ([\d.\-]+) ([\d.\-]+)', blk):
    px, py = float(pm.group(1)), float(pm.group(2))
    r = int(fr) % 360
    px, py = {0: (px, py), 90: (-py, px), 180: (-px, -py), 270: (py, -px)}[r]
    old.append((fx + px, fy + py))

fps = {f.GetReference(): f for f in b.GetFootprints()}
tracks = list(b.GetTracks())
kill = []
for t in tracks:
    for pt in (t.GetStart(), t.GetEnd()):
        if any(abs(pt.x/1e6-px) < 0.45 and abs(pt.y/1e6-py) < 0.45 for px, py in old):
            kill.append(t)
            break
kill2 = []
for t in kill:
    if not any(t is k for k in kill2):
        kill2.append(t)
for t in kill2:
    b.Remove(t)
fps['R1'].SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(27.2), pcbnew.FromMM(26.5)))
print('R1 → (27.2,26.5), 删旧走线', len(kill2))
pcbnew.ZONE_FILLER(b).Fill(b.Zones())
b.Save(os.path.abspath('imu_to_ttl.kicad_pcb'))
print('ok')
