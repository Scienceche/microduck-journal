#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地实跑 pad_live.py 里【真实的】 hold_loop / start_hold(不连鸭子)。

只替换它们依赖的东西: 时钟、subprocess、unit_active、_rescan_once、push_event。
hold_loop 本体是 while True, 用异常在跑满指定秒数后跳出。
不手抄被测逻辑——上一版测试把循环体抄了一遍, 测的是抄本不是真函数。

前两次"恢复分支永不执行"(stopped / restored)都是只读代码没实跑漏掉的。
"""
import importlib.util
import sys
import types

SRC = r"C:\duck_work\ota\pad_live_broken_on_duck.py"


class _Enough(Exception):
    pass


def load():
    spec = importlib.util.spec_from_file_location("pad_live_under_test", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)      # __name__ 不是 __main__, 不会起服务
    return mod


class Rig:
    def __init__(self, mod):
        self.mod = mod
        self.t = 1000.0
        self.bridge = True
        self.padd = True
        self.starts = []          # (时刻, unit)
        self.stops = []
        self.rescans = []
        self.events = []
        self.run_for = 0.0

    # --- 假件 ---
    def time(self): return self.t
    def sleep(self, s):
        self.t += s
        if self.run_for and self.t >= self._deadline:
            raise _Enough()
    def unit_active(self, unit):
        return self.bridge if unit == self.mod.BRIDGE_UNIT else self.padd
    def run(self, cmd, **kw):
        act, unit = cmd[1], cmd[2]
        if act == 'start':
            if unit == self.mod.BRIDGE_UNIT: self.bridge = True
            else: self.padd = True
            self.starts.append((round(self.t, 1), unit.split('.')[0]))
        elif act == 'stop':
            if unit == self.mod.BRIDGE_UNIT: self.bridge = False
            else: self.padd = False
            self.stops.append((round(self.t, 1), unit.split('.')[0]))
        return types.SimpleNamespace(returncode=0, stdout='', stderr='')

    def install(self):
        m = self.mod
        m.time = types.SimpleNamespace(time=self.time, sleep=self.sleep)
        m.unit_active = self.unit_active
        m.subprocess = types.SimpleNamespace(run=self.run, DEVNULL=-3)
        m._rescan_once = lambda allowed: self.rescans.append((round(self.t, 1), allowed))
        m.push_event = lambda dev, name, val: self.events.append((round(self.t, 1), name))
        m.find_event = lambda name: None       # 不碰真实设备

    def spin(self, seconds):
        """让真实的 hold_loop 跑 seconds 秒(模拟时间)。"""
        self._deadline = self.t + seconds
        self.run_for = seconds
        try:
            self.mod.hold_loop()
        except _Enough:
            pass
        finally:
            self.run_for = 0.0
            self._deadline = float('inf')


def main():
    mod = load()
    rig = Rig(mod)
    rig.install()

    H = mod.HOLD_SECONDS
    out = []

    def check(name, cond, detail=''):
        out.append((name, bool(cond)))
        print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                                '' if cond else '   <- ' + str(detail)))

    # A 平时: 本进程没发起过抓码
    mod.hold_began = None; mod.hold_until = 0.0
    rig.spin(5)
    check("A 平时不启动桥", rig.starts == [], rig.starts)

    # B 发起抓码: 必须停桥, 且先关 xbox 再停
    ok = mod.start_hold()
    rig.spin(3)
    check("B1 start_hold 返回 True", ok is True)
    check("B2 桥与 padd 已停", (not rig.bridge) and (not rig.padd))
    check("B3 窗口内不启动桥", rig.starts == [], rig.starts)
    check("B4 发起抓码时已释放 xbox(rescan 只留 raw/cons)",
          rig.rescans and rig.rescans[0][1] == ('raw', 'cons'), rig.rescans)
    check("B5 窗口内重复点击被拒绝", mod.start_hold() is False)

    # C 窗口结束 -> 必须启动, 且先释放 raw/cons 再启动桥
    rig.starts.clear(); rig.rescans.clear()
    rig.spin(H + 3)
    check("C1 窗口结束后启动了桥", any(u == 'gamesir-xboxd' for _, u in rig.starts), rig.starts)
    check("C2 同时启动了 padd", any(u == 'padd' for _, u in rig.starts), rig.starts)
    check("C3 恢复后桥与 padd 都在跑", rig.bridge and rig.padd)
    if rig.rescans and rig.starts:
        check("C4 先释放 raw/cons 再启动桥(顺序)", rig.rescans[0][0] <= rig.starts[0][0]
              and rig.rescans[0][1] == ('xbox',), (rig.rescans, rig.starts))
    else:
        check("C4 先释放 raw/cons 再启动桥(顺序)", False, (rig.rescans, rig.starts))
    check("C5 发出'已恢复'事件", any('已恢复' in n for _, n in rig.events), rig.events)

    # D 恢复后不重复启动
    rig.starts.clear()
    rig.spin(5)
    check("D 恢复后不重复启动", rig.starts == [], rig.starts)

    # E 第二次抓码也能完整恢复
    rig.starts.clear(); rig.events.clear()
    check("E1 可再次发起抓码", mod.start_hold() is True)
    rig.spin(H + 3)
    check("E2 第二次也能恢复", rig.bridge and rig.padd and rig.starts, (rig.bridge, rig.padd, rig.starts))

    # F 启动失败要报警而不是假装成功
    rig.starts.clear(); rig.events.clear()
    mod.start_hold()
    rig.spin(H - 2)
    orig_run = rig.run
    def failing_run(cmd, **kw):
        if cmd[1] == 'start' and cmd[2] == mod.BRIDGE_UNIT:
            return types.SimpleNamespace(returncode=1, stdout='', stderr='')   # 启动失败
        return orig_run(cmd, **kw)
    mod.subprocess.run = failing_run
    try:
        rig.spin(6)
    finally:
        mod.subprocess.run = orig_run
    check("F 桥启动失败时有告警事件", any('未能启动' in n for _, n in rig.events), rig.events)

    print()
    bad = [n for n, p in out if not p]
    print("结果: %d/%d 通过%s" % (len(out) - len(bad), len(out), '' if not bad else '  失败: ' + '; '.join(bad)))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
