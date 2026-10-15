#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""总线区 v2 改造: 1G125(AUTO) → 2G241(PA1/PB7方向控制) + R16启动拉带
匹配真板拓扑 (epro2网表实证) + 用户实测的10k boot strap。"""
import sys, io, uuid as uuidlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
def u(): return str(uuidlib.uuid4())

def blk_end(s, i):
    """从 i 处的 '(' 开始括号配对, 返回匹配 ')' 之后的位置"""
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c == '(':
            depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise ValueError('unbalanced block at %d' % i)

lab_tpl = '''	(global_label "%s"
		(shape bidirectional)
		(at %s %s %s)
		(fields_autoplaced yes)
		(effects
			(font
				(size 1.27 1.27)
			)
			(justify %s)
		)
		(uuid "%s")
		(property "Intersheetrefs" "${INTERSHEET_REFS}"
			(at %s %s 0)
			(effects
				(font
					(size 1.27 1.27)
				)
				(hide yes)
			)
		)
	)
'''
def mklabel(nm, x, y, ang):
    just = 'left' if ang == '0' else 'right'
    return lab_tpl % (nm, x, y, ang, just, u(), x, y)

# ============ 02_mcu: PA1/PB7加标签, PA3改名 ============
s = open('02_mcu.kicad_sch', encoding='utf-8').read()
if '(global_label "MCU_RX"' in s:
    print('02_mcu 已改造过，跳过 ✓')
else:
    # 1) BUS_LOGIC@125.73,102.87 → MCU_RX (整块替换第一个匹配)
    i = s.find('(global_label "BUS_LOGIC"')
    assert i > 0, '找不到BUS_LOGIC标签'
    e = blk_end(s, i)
    blk = s[i:e]
    assert '(at 125.73 102.87 0)' in blk, 'BUS_LOGIC坐标不符: ' + blk[:120]
    s = s[:i] + blk.replace('"BUS_LOGIC"', '"MCU_RX"', 1) + s[e:]
    print('PA3: BUS_LOGIC -> MCU_RX ✓')
    # 2) 新标签
    anchor = '\n\t(global_label "BUS_TX"'
    add = '\n' + mklabel('TX_EN_N', '125.73', '97.79', '0') + '\n' + mklabel('RX_EN', '74.93', '107.95', '180')
    s = s.replace(anchor, add + anchor, 1)
    print('PA1=TX_EN_N, PB7=RX_EN ✓')
    open('02_mcu.kicad_sch', 'w', encoding='utf-8').write(s)

# ============ 04_bus: 2G241 改造 ============
s = open('04_bus.kicad_sch', encoding='utf-8').read()

# 1) 删旧U3实例
i = s.find('(lib_id "74xGxx:74LVC1G125"')
assert i > 0
st = s.rfind('\n\t(symbol\n', 0, i)
nxt = s.find('\n\t(symbol\n', i)
if nxt == -1:
    nxt = s.find('\n\t(graphic', i)
u3_old = s[st:nxt]
assert 'SN74LVC1G125DBVT' in u3_old
s = s[:st] + s[nxt:]
print('旧U3实例已删(%d字节) ✓' % len(u3_old))

# 2) 删旧U3的5个标签
for (nm, x, y) in [('BUS_TX','100.33','100.33'), ('BUS_TX','85.09','110.49'),
                   ('GND','95.25','120.65'), ('BUS_LOGIC','113.03','110.49'),
                   ('+3V3','95.25','100.33')]:
    pat = '(global_label "%s"' % nm
    idx = 0
    while True:
        i = s.find(pat, idx)
        assert i > 0, '找不到 %s@%s,%s' % (nm, x, y)
        ati = s.find('(at ', i)
        if s[ati+4:ati+24].startswith(x + ' ' + y + ' '):
            e = blk_end(s, i)
            s = s[:i-2] + s[e:]
            print('删标签 %s@%s,%s ✓' % (nm, x, y))
            break
        idx = i + 1

# 3) 内嵌2G241库符号
PINS = [('1','input','~{1OE}'), ('2','input','1A'), ('3','tri_state','2Y'), ('4','power_in','GND'),
        ('5','input','2A'), ('6','tri_state','1Y'), ('7','input','2OE'), ('8','power_in','VCC')]
POS = {'1': (-10.16, 3.81, 0), '2': (-10.16, 1.27, 0), '3': (-10.16, -1.27, 0), '4': (-10.16, -3.81, 0),
       '5': (10.16, -3.81, 180), '6': (10.16, -1.27, 180), '7': (10.16, 1.27, 180), '8': (10.16, 3.81, 180)}
pin_defs = ''
for num, typ, name in PINS:
    x, y, ang = POS[num]
    pin_defs += '''			(pin %s line
				(at %s %s %s)
				(length 2.54)
				(name "%s"
					(effects
						(font
							(size 1.27 1.27)
						)
					)
				)
				(number "%s"
					(effects
						(font
							(size 1.27 1.27)
						)
					)
				)
			)
''' % (typ, x, y, ang, name, num)

def sym_prop(key, val, hide=False):
    h = '\n\t\t\t(hide yes)' if hide else ''
    return '''		(property "%s" "%s"
			(at 0 0 0)
			(show_name no)
			(do_not_autoplace no)%s
			(effects
				(font
					(size 1.27 1.27)
				)
			)
		)
''' % (key, val, h)

lib_sym = '''	(symbol "74xGxx:SN74LVC2G241"
			(exclude_from_sim no)
			(in_bom yes)
			(on_board yes)
			(in_pos_files yes)
			(duplicate_pin_numbers_are_jumpers no)
''' + sym_prop('Reference', 'U') + sym_prop('Value', 'SN74LVC2G241') + \
    sym_prop('Footprint', '', True) + sym_prop('Datasheet', '', True) + \
    sym_prop('Description', 'Dual buffer 3-state: TX ch 1OE# active-low, RX ch 2OE active-high. DCU-8: 1=1OE# 2=1A 3=2Y 4=GND 5=2A 6=1Y 7=2OE 8=VCC', True) + '''		(symbol "SN74LVC2G241_0_1"
			(rectangle
				(start -7.62 5.08)
				(end 7.62 -5.08)
				(stroke
					(width 0.254)
					(type default)
				)
				(fill
					(type background)
				)
			)
		)
		(symbol "SN74LVC2G241_1_1"
''' + pin_defs + '''		)
			(embedded_fonts no)
	)
'''
i_lib = s.find('\t(lib_symbols\n')
assert i_lib > 0
s = s[:i_lib + len('\t(lib_symbols\n')] + lib_sym + s[i_lib + len('\t(lib_symbols\n'):]
print('2G241库符号嵌入 ✓')

# 4) 新U3实例
inst = '''	(symbol
		(lib_id "74xGxx:SN74LVC2G241")
		(at 100.33 110.49 0)
		(unit 1)
		(exclude_from_sim no)
		(in_bom yes)
		(on_board yes)
		(dnp no)
		(uuid "%s")
		(property "Reference" "U3"
		(at 105.33 105.99 0)
		(effects
		(font
			(size 1.27 1.27)
		)
	)
	)
		(property "Value" "SN74LVC2G241DCUR"
		(at 105.33 116.84 0)
		(effects
		(font
			(size 1.27 1.27)
		)
	)
	)
		(property "Footprint" "Package_SO:VSSOP-8_2.3x2mm_P0.5mm"
		(at 100.33 110.49 0)
		(effects
		(font
			(size 1.27 1.27)
		)
			(hide yes)
	)
	)
		(property "Datasheet" "~"
		(at 100.33 110.49 0)
		(effects
		(font
			(size 1.27 1.27)
		)
			(hide yes)
	)
	)
		(property "Description" "Half-duplex PHY v2: PA1=TX_EN_N drives 1OE# (low=TX on), PB7=RX_EN drives 2OE. Matches real board (epro2 netlist), field-proven 2026-10-11"
		(at 100.33 110.49 0)
		(effects
		(font
			(size 1.27 1.27)
		)
			(hide yes)
	)
	)
		(property "MPN" "SN74LVC2G241DCUR"
		(at 100.33 110.49 0)
		(effects
		(font
			(size 1.27 1.27)
		)
			(hide yes)
	)
	)
		(property "LCSC" "C10430"
			(at 100.33 110.49 0)
			(effects
				(font
					(size 1.27 1.27)
				)
				(hide yes)
			)
		)
''' % u()
for p in range(1, 9):
    inst += '''		(pin "%d"
			(uuid "%s")
		)
''' % (p, u())
inst += '''		(instances
			(project "imu_to_ttl"
				(path "/3d3ee743-797d-5481-a518-f87a3584d0e2/fdb1a352-2feb-52d4-8282-cb48e1edb94e/de379f4c-5da4-5922-a10d-e8ae6b007dd2"
					(reference "U3")
					(unit 1)
				)
			)
		)
	)
'''

def mkres(ref, val, x, y, desc):
    t = '''	(symbol
		(lib_id "Device:R")
		(at %s %s 0)
		(unit 1)
		(exclude_from_sim no)
		(in_bom yes)
		(on_board yes)
		(dnp no)
		(uuid "%s")
		(property "Reference" "%s"
		(at %s %s 90)
		(effects
		(font
			(size 1.27 1.27)
		)
	)
	)
		(property "Value" "%s"
		(at %s %s 90)
		(effects
		(font
			(size 1.27 1.27)
		)
	)
	)
		(property "Footprint" "Resistor_SMD:R_0603_1608Metric"
		(at %s %s 0)
		(effects
		(font
			(size 1.27 1.27)
		)
			(hide yes)
	)
	)
		(property "Datasheet" "~"
		(at %s %s 0)
		(effects
		(font
			(size 1.27 1.27)
		)
			(hide yes)
	)
	)
		(property "Description" "%s"
		(at %s %s 0)
		(effects
		(font
			(size 1.27 1.27)
		)
			(hide yes)
	)
	)
		(pin "1"
			(uuid "%s")
		)
		(pin "2"
			(uuid "%s")
		)
		(instances
			(project "imu_to_ttl"
				(path "/3d3ee743-797d-5481-a518-f87a3584d0e2/fdb1a352-2feb-52d4-8282-cb48e1edb94e/de379f4c-5da4-5922-a10d-e8ae6b007dd2"
					(reference "%s")
					(unit 1)
				)
			)
		)
	)
'''
    uu1, uu2 = u(), u()
    return t % (x, y, u(), ref, x, y, val, x, y, x, y, x, y, desc, x, y, uu1, uu2, ref)

resistors = (mkres('R14', '10k', '74.93', '90.17', 'TX_EN_N pull-up: TX disabled at idle (real board R6)')
           + mkres('R15', '10k', '74.93', '110.49', 'RX_EN pull-up: RX enabled at idle (real board R5)')
           + mkres('R16', '10k', '74.93', '130.81', 'BOOT STRAP (field-proven): RX_EN-GND divider latches clone MCU boot mode; DNP if genuine MCU fitted'))

# 5) 新标签
labels = ''
def nl(nm, x, y, ang):
    global labels
    labels += mklabel(nm, x, y, ang)
# U3标签(y已按KiCad未镜像实例的坐标约定修正: 左列pin1在下, 右列pin8在下)
nl('TX_EN_N', '90.17', '106.68', '180'); nl('BUS_TX', '90.17', '109.22', '180')
nl('MCU_RX', '90.17', '111.76', '180'); nl('GND', '90.17', '114.3', '180')
nl('BUS_LOGIC', '110.49', '114.3', '0'); nl('BUS_LOGIC', '110.49', '111.76', '0')
nl('RX_EN', '110.49', '109.22', '0'); nl('+3V3', '110.49', '106.68', '0')
nl('+3V3', '74.93', '86.36', '0'); nl('TX_EN_N', '74.93', '93.98', '0')
nl('+3V3', '74.93', '106.68', '0'); nl('RX_EN', '74.93', '114.3', '0')
nl('RX_EN', '74.93', '127.0', '0'); nl('GND', '74.93', '134.62', '0')

# 插入点: 实例区开头(第一个 lib_id 实例块之前, 必为顶层位置)
import re as _re
m_first_inst = _re.search(r'\n\t\(symbol\n\t\t\(lib_id ', s)
assert m_first_inst, '找不到实例区锚点'
pos = m_first_inst.start() + 1
s = s[:pos] + inst + resistors + labels + s[pos:]

# 6) 标题
s = s.replace('(text "TTL BUS - AUTO direction, no DE, no RX_EN"',
              '(text "TTL BUS v2 - 2G241: PA1=TX_EN_N(1OE#) + PB7=RX_EN(2OE) + R16 boot strap (matches real board)"')
open('04_bus.kicad_sch', 'w', encoding='utf-8').write(s)
print('04_bus.kicad_sch 改造完成 ✓✓')
