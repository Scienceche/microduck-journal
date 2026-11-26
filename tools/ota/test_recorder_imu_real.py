#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段4 补丁的端到端测试: 用鸭子上的真实 server.py 副本, 不用手造的假文件。

2026-11-25 教训: 第一版测试用我手造的假文件, 夹具里自己多加了 `import time`,
而鸭子上的真实文件没有 —— 测试全过, 上机后订阅线程 NameError。
所以这里的被测文件只来自真实副本, 夹具不加任何真实文件里没有的东西。

覆盖两条路径:
  A. 原文件(未打补丁)          -> 打补丁 -> 行为测试
  B. 已打过但缺顶层 import 的文件 -> 再打补丁 -> 行为测试   (鸭子上的现场状态)

用法: python test_recorder_imu_real.py <鸭子上的 server.py 副本>
"""
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS = Path("C:/duck_work/ota")
PATCH = TOOLS / "patch_recorder_imu.py"
BEHAVIOR_TEST = TOOLS / "test_recorder_imu_body.py"

if len(sys.argv) < 2:
    print("用法: python test_recorder_imu_real.py <server.py 真实副本>")
    sys.exit(2)
REAL = Path(sys.argv[1])
IDS = [20, 21, 22, 23, 24, 30, 31, 32, 33, 34, 10, 11, 12, 13, 14]


def run_patch_on(target):
    """把补丁指向 target 跑一遍, 返回 (退出码, 输出)。"""
    s = PATCH.read_text(encoding="utf-8")
    s = s.replace('SRV = Path("/opt/duck-calib/server.py")', 'SRV = Path(r"%s")' % target.as_posix())
    s = s.replace('BAK = Path("/opt/duck-calib/server.py.bak-imu")',
                  'BAK = Path(r"%s.bak")' % target.as_posix())
    assert "/opt/duck-calib" not in s
    p = target.parent / "patch.py"
    p.write_text(s, encoding="utf-8", newline="\n")
    r = subprocess.run([sys.executable, str(p)], capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def static_checks(path, label):
    import ast
    ok = True
    src = path.read_text(encoding="utf-8", newline="")
    b = path.read_bytes()
    def chk(name, cond, detail=""):
        nonlocal ok
        ok = ok and bool(cond)
        print("    [%s] %s%s" % ("PASS" if cond else "FAIL", name, "" if cond else "   <- " + str(detail)))
    try:
        tree = ast.parse(src)
        chk("语法正确", True)
    except SyntaxError as e:
        chk("语法正确", False, e)
        return False
    chk("无 CR(换行符保持 LF)", b"\r" not in b, b.count(b"\r"))
    # 顶层 import 必须覆盖新代码用到的模块级名字。
    # 同时认 `import X` 与 `from M import name` 两种形式(第一版只认前者, 把 deque 误报成缺失)。
    top = set()
    for n in tree.body:
        if isinstance(n, ast.Import):
            top.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            top.add(n.module)
            top.update(a.name for a in n.names)
    for mod in ("json", "threading", "time", "deque", "re"):
        chk("顶层有 %s" % mod, mod in top, sorted(top))
    # 新代码在模块级用到的名字, 必须能在文件里找到定义或导入(粗查)
    for name in ("_state_subscriber", "_raw_pos_refresher", "_sample_now", "JOINT_IDS", "RAW_POS"):
        chk("定义了 %s" % name, re.search(r"^(def %s\b|%s\s*=)" % (name, name), src, re.M) is not None)
    chk("JOINT_IDS 与固件一致",
        "JOINT_IDS = (20, 21, 22, 23, 24, 30, 31, 32, 33, 34, 10, 11, 12, 13, 14)" in src)
    chk("订阅线程已挂", "target=_state_subscriber, daemon=True" in src)
    chk("旧采样路径已移除", 'rpc("robot.busStatus", timeout=4.0) or {}\n    joints' not in src)
    print("    -- %s: %s" % (label, "静态检查通过" if ok else "静态检查有失败"))
    return ok


def behavior(path):
    """跑行为测试(独立脚本, 避免已加载模块互相污染)。"""
    r = subprocess.run([sys.executable, str(BEHAVIOR_TEST), str(path)],
                       capture_output=True, text=True)
    tail = [l for l in r.stdout.splitlines() if l.strip()][-3:]
    for l in tail:
        print("    " + l.strip())
    return r.returncode == 0


def main():
    real = REAL.read_text(encoding="utf-8", newline="")
    print("真实副本: %s (%d 字节, CRLF %d)" % (REAL.name, len(real.encode()), real.count("\r\n")))
    work = Path(tempfile.mkdtemp(prefix="rec_real_"))
    allok = True

    print("\n[A] 原文件 -> 打补丁")
    a = work / "a_server.py"; shutil.copy(REAL, a)
    rc, out = run_patch_on(a)
    print("  补丁退出码 %d | %s" % (rc, out.splitlines()[-1] if out else ""))
    allok &= (rc == 0)
    if rc == 0:
        allok &= static_checks(a, "路径A")
        allok &= behavior(a)
        diff = subprocess.run(["diff", str(REAL), str(a)], capture_output=True, text=True)
        n = sum(1 for l in diff.stdout.splitlines() if l[:1] in "<>")
        print("    差异行数 %d (应约 112, 不应是全文件)" % n)
        allok &= n < 200

    print("\n[B] 已打过但缺顶层 import(鸭子现场状态) -> 再打补丁")
    b = work / "b_server.py"
    noimp = re.sub(r"^import time\n", "", a.read_text(encoding="utf-8", newline=""), flags=re.M)
    assert noimp != a.read_text(encoding="utf-8", newline=""), "路径A 结果里没有 import time, 夹具失效"
    b.write_text(noimp, encoding="utf-8", newline="\n")
    rc, out = run_patch_on(b)
    print("  补丁退出码 %d | %s" % (rc, out.splitlines()[-1] if out else ""))
    allok &= (rc == 0)
    if rc == 0:
        allok &= static_checks(b, "路径B")
        allok &= behavior(b)
        # 单独判断再并入, 不把前面各项的失败结果混进这一条的输出
        same = b.read_text(encoding="utf-8", newline="") == a.read_text(encoding="utf-8", newline="")
        print("    [%s] 路径B 结果与路径A 完全一致" % ("PASS" if same else "FAIL"))
        allok &= same

    print("\n[C] 已打好且 import 齐全 -> 再打应无变化")
    c = work / "c_server.py"; shutil.copy(a, c)
    before = c.read_text(encoding="utf-8", newline="")
    rc, out = run_patch_on(c)
    same = c.read_text(encoding="utf-8", newline="") == before
    print("  退出码 %d | %s | 文件未变: %s" % (rc, out.splitlines()[-1] if out else "", same))
    allok &= (rc == 0 and same)

    print("\n结果: %s" % ("全部通过" if allok else "有失败"))
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
