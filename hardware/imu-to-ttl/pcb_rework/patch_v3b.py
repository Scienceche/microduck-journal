import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
s = open('rework_pcb_v3.py', encoding='utf-8').read()
old = "print(f'占用表: {len(occ)}项(含{npads}焊盘)')"
new = """occ.append(U3BOX)  # U3整个区域禁放(身体在焊盘列之间)
print(f'占用表: {len(occ)}项(含{npads}焊盘+U3禁区)')"""
assert old in s
s = s.replace(old, new, 1)
open('rework_pcb_v3.py', 'w', encoding='utf-8').write(s)
print('U3禁区已加入占表')
