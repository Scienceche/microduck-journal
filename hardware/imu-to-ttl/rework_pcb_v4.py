#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PCB v4: U3转90°(外框避TH1/R13) + 三颗电阻横放底面已验证空位 + 背面文字mirror"""
import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
PCB = 'imu_to_ttl.kicad_pcb'
s = open(PCB, encoding='utf-8').read()

def blk_of(ref):
    i = s.find('(property "Reference" "%s"' % ref)
    assert i > 0, ref + ' 未找到'
    st = s.rfind('\n\t(footprint ', 0, i)
    d = 0; j = st + 1
    while True:
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: break
        j += 1
    return st, j + 1

# ---- 1) U3: 挪到(32.2,6.9) rot0 ----
# 铜距: TH1 pad2需x≥32.15(得0.25); R13 pad1靠y抬离(7.825+0.2 vs 8.3 → 0.275)
# 外框与TH1/R13各重叠~0.2-0.4mm: 5.9mm外框挤不进5.3mm间隙, 实体间距>1.2mm, 记录为已接受偏差
st, e = blk_of('U3')
u3blk = s[st:e]
at3 = re.search(r'\n(\t+)\(at ([\d.\-]+) ([\d.\-]+)(?: ([\-\d.]+))?\)\n', u3blk)
nu3 = u3blk[:at3.start()] + '\n' + at3.group(1) + '(at 32.2000 6.9000)\n' + u3blk[at3.end():]
s = s[:st] + nu3 + s[e:]
print('U3 → (32.200,6.900) rot0 ✓')

# ---- 2) 三颗电阻: 横放(rot 0) + 底面空位 + 层翻转 ----
SPOTS = {'R14': (33.70, 3.10), 'R15': (31.90, 8.00), 'R16': (28.50, 3.10)}
for ref, (nx, ny) in SPOTS.items():
    st, e = blk_of(ref)
    blk = s[st:e]
    nblk = re.sub(r'\t\(at [\d.\-]+ [\d.\-]+(?: 90)?\)', '\t(at %.3f %.3f)' % (nx, ny), blk, count=1)
    assert nblk != blk, ref + ' at替换失败'
    # 层翻转(仅块内): F→B
    nblk = nblk.replace('"F.Cu"', '"B.Cu"').replace('"F.Mask"', '"B.Mask"') \
               .replace('"F.Paste"', '"B.Paste"').replace('"F.SilkS"', '"B.SilkS"') \
               .replace('"F.Fab"', '"B.Fab"').replace('"F.CrtYd"', '"B.CrtYd"')
    assert '"B.Cu"' in nblk and '"F.Cu"' not in nblk, ref + ' 层翻转失败'
    # 文字归位(v2克隆残留R7的偏移); R15的Reference放下方避C5丝印
    ry = 1.43 if ref == 'R15' else -1.43
    nblk = re.sub(r'(property "Reference" "[^"]+"\n\t\t+\(at )[^\)]+\)', r'\g<1>0 %.2f 0)' % ry, nblk, count=1)
    nblk = re.sub(r'(property "Value" "[^"]+"\n\t\t+\(at )[^\)]+\)', r'\g<1>0 %.2f 0)' % (-ry), nblk, count=1)
    # 背面文字镜像: 块内所有effects补(justify mirror)
    out = []; i = 0; n = 0
    while True:
        k = nblk.find('(effects', i)
        if k < 0:
            out.append(nblk[i:]); break
        d = 0; j = k
        while True:
            if nblk[j] == '(': d += 1
            elif nblk[j] == ')':
                d -= 1
                if d == 0: break
            j += 1
        seg = nblk[k:j+1]
        out.append(nblk[i:k])
        if 'justify' not in seg:
            cl = seg.rfind('\n')            # 闭合行 "\t+)"
            tabs = re.match(r'\n(\t+)\)', seg[cl:]).group(1)
            seg = seg[:cl] + '\n' + tabs + '\t(justify mirror)' + seg[cl:]
            n += 1
        out.append(seg)
        i = j + 1
    nblk = ''.join(out)
    print(f'{ref} → 底面 ({nx:.2f},{ny:.2f}) H, 文字归位+补mirror×{n} ✓')
    s = s[:st] + nblk + s[e:]

open(PCB, 'w', encoding='utf-8').write(s)
print('v4完成, 括号平衡:', sum(1 if c == '(' else -1 for c in s if c in '()'))
