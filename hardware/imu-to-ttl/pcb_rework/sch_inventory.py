#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""顶层遍历 04_bus.kicad_sch: 每个顶层块的类型+关键信息"""
import io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

s = open('04_bus.kicad_sch', encoding='utf-8').read()

def blk_span(s, st):
    d = 0; j = st
    while j < len(s):
        if s[j] == '(': d += 1
        elif s[j] == ')':
            d -= 1
            if d == 0: return j + 1
        j += 1
    return -1

# 从 '(kicad_sch' 开始, 遍历顶层子块
st = s.find('(kicad_sch')
i = s.find('\n', st)
while i < len(s):
    # 跳过空白行
    while i < len(s) and s[i] in '\n \t':
        i += 1
    if i >= len(s) or s[i] != '(':
        break
    e = blk_span(s, i)
    head = s[i:s.find('\n', i)]
    blk = s[i:e]
    kind = head[1:20].split(' ')[0].split('\n')[0]
    info = ''
    m = re.search(r'\(lib_id "([^"]+)"', blk)
    a = re.search(r'\(at ([\d.\-]+) ([\d.\-]+)(?: ([\d.]+))?\)', blk)
    t = re.search(r'\((?:text|value) "([^"]{0,40})"', blk)
    r = re.search(r'\(property "Reference" "([^"]*)"', blk)
    if kind == 'symbol' and m and r:
        info = '%s [%s] @(%s,%s) rot%s' % (r.group(1), m.group(1).split(':')[-1], a.group(1), a.group(2), a.group(3) or 0)
    elif kind in ('global_label', 'label', 'hierarchical_label') and t:
        info = '"%s" @(%s,%s) rot%s %s' % (t.group(1), a.group(1), a.group(2), a.group(3) or 0,
              'mirror' if '(mirror' in blk else '')
    elif kind == 'wire':
        pts = re.findall(r'\(xy ([\d.\-]+) ([\d.\-]+)\)', blk)
        info = ' '.join('(%s,%s)' % p for p in pts)
    elif kind == 'text':
        info = '"%s"' % (t.group(1) if t else '?')
    print('%-22s %s' % (kind, info))
    i = e
