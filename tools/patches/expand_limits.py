#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""方案 C 步骤 1: 扩展所有 XML 的主动关节限位 ±90° → ±180° (π rad)。

只改 chosen_actuator 类的 8 个关节(左右 hip_pitch/knee/ankle + hip_yaw/roll 不动)。
head/neck 关节范围更小, 不碰。
"""
from pathlib import Path
import re

NEW_RANGE = "range=\"-3.141592653589793 3.141592653589793\""

for xml_name in ("robot_allcollisions.xml", "robot_groundcontact_backlash.xml"):
    P = Path(r"C:\duck_work\microduck_rl\src\mjlab_microduck\robot\microduck") / xml_name
    s = P.read_text(encoding="utf-8")

    # 找所有 ±90° (±1.5707963...) 的 range, 只在 chosen_actuator class 的关节上替换
    # 模式: range="-1.5707963... 1.5707963..." 后面跟着 class="chosen_actuator"
    pattern = re.compile(
        r'range="-1\.5707963\d+ 1\.5707963\d+"( class="chosen_actuator")'
    )

    matches = pattern.findall(s)
    count = len(matches)
    s = pattern.sub(NEW_RANGE + r'\1', s)

    P.write_text(s, encoding="utf-8")

    # 复核
    c = P.read_text(encoding="utf-8")
    remaining = len(re.findall(r'range="-1\.5707963\d+ 1\.5707963\d+" class="chosen_actuator"', c))
    expanded = len(re.findall(NEW_RANGE + ' class="chosen_actuator"', c))
    print(f"{xml_name}: 替换 {count} 个关节限位, 剩余 ±90°: {remaining}, 扩到 ±180°: {expanded}")
    assert remaining == 0, f"还有 {remaining} 个未替换!"
    assert expanded == count, f"替换数不匹配!"
