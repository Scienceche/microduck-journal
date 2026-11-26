#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""比较三份 HD1910 BAM M6 电机参数文件的数值, 判断它们是不是同一套辨识参数。

  A  Training Studio 的 hd1910_m6.json            —— 固件契约解析器硬性要求它的 sha256(ef2d51ad…)
  B  bam/params/hd1910_kissqy/m6.json              —— run15 训练实际使用的(motor_name="hd1910_kissqy")
  C  bam/params/feetech_hd1910_7_4V/m6.json        —— 飞特官方辨识
  D  现役 stand 包契约里归档的 parameter_file.data —— run6 部署时记录的
用法: python compare_bam_params.py
"""
import hashlib
import json
import sys
from pathlib import Path

A = Path(r"C:\duck_work\kissqy-studio\Microduck-Training-Studio-R1.5.27\training\hd1910_m6.json")
B = Path(r"C:\duck_work\bam\bam\params\hd1910_kissqy\m6.json")
C = Path(r"C:\duck_work\bam\bam\params\feetech_hd1910_7_4V\m6.json")
LIVE = Path(r"C:\duck_work\pkg_build\models\13f456ab9548ee17cf51da6f28f747913856bf9357eab212f306f0140829d166")


def flat(v, prefix=""):
    """嵌套 dict 展平成 {点路径: 数值/字符串}"""
    out = {}
    if isinstance(v, dict):
        for k, x in v.items():
            out.update(flat(x, f"{prefix}{k}."))
    else:
        out[prefix[:-1]] = v
    return out


def compare(name_a, a, name_b, b):
    fa, fb = flat(a), flat(b)
    keys = sorted(set(fa) | set(fb))
    only_a = [k for k in keys if k not in fb]
    only_b = [k for k in keys if k not in fa]
    diffs = []
    for k in keys:
        if k in fa and k in fb and fa[k] != fb[k]:
            x, y = fa[k], fb[k]
            if isinstance(x, (int, float)) and isinstance(y, (int, float)) and x != 0:
                rel = abs(x - y) / abs(x)
                diffs.append((k, x, y, rel))
            else:
                diffs.append((k, x, y, None))
    print(f"--- {name_a}  vs  {name_b} ---")
    print(f"    字段数 {len(fa)} / {len(fb)}; 只在前者: {only_a}; 只在后者: {only_b}")
    exact = [d for d in diffs if d[3] is None or d[3] > 0]
    print(f"    共有字段中数值不同: {len(diffs)} 处")
    for k, x, y, rel in sorted(diffs, key=lambda d: -(d[3] or 0))[:25]:
        how = "类型/非数值" if rel is None else f"相对差 {rel:.2e}"
        print(f"      {k:<34} {str(x)[:26]:<26} | {str(y)[:26]:<26} {how}")
    if diffs:
        worst = max(d[3] or 0 for d in diffs)
        print(f"    最大相对差: {worst:.3e}")
    return diffs


def main():
    a = json.loads(A.read_text(encoding="utf-8"))
    b = json.loads(B.read_text(encoding="utf-8"))
    c = json.loads(C.read_text(encoding="utf-8"))
    d = json.loads(LIVE.joinpath("deployment-contract.json").read_text(encoding="utf-8"))["model"]["actuators"][0]["parameter_file"]["data"]
    for name, p in (("A Studio hd1910_m6.json", A), ("B hd1910_kissqy/m6.json", B), ("C feetech_hd1910_7_4V/m6.json", C)):
        print(f"{name}: sha256 {hashlib.sha256(p.read_bytes()).hexdigest()[:16]}…  {p.stat().st_size} 字节")
    print()
    da = compare("A Studio", a, "B kissqy(run15 训练用)", b)
    print()
    dc = compare("A Studio", a, "D 现役包归档(run6)", d)
    print()
    compare("A Studio", a, "C 飞特官方", c)
    print()
    print("结论:")
    if not da:
        print("  A 与 B 数值完全一致 → 同一套辨识参数, 只是文件名/排版不同。")
    else:
        print(f"  A 与 B 有 {len(da)} 处数值不同 → 不是同一套参数。")
    if not dc:
        print("  A 与 D(现役包归档的 parameter_file.data) 数值完全一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
