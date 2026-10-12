import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
s = open('rework_pcb_v3.py', encoding='utf-8').read()
old = "fat = re.search(r'\\n\\t\\t\\(at "
new = "fat = re.search(r'\\n\\t+\\(at "
assert old in s, 'fat行未找到'
s = s.replace(old, new, 1)
open('rework_pcb_v3.py', 'w', encoding='utf-8').write(s)
print('fat正则已放宽为1+tab')
