#!/usr/bin/env python3
"""pad_live.py — GameSir 手柄实时监视器（端口 8091）

2026-11-24 重构。原版(11-15)有两处缺陷, 今晚打补丁又叠出读取线程崩溃与
和桥争抢节点两个回归, 故按原设计意图重写节点管理部分, 页面部分不变。

设计前提(原版文档已写明): 桥(gamesir-xboxd)运行时用 EVIOCGRAB 独占 raw/cons
两个节点, 监视器看不到它们是正常的。因此核心规则只有一条:

    同一时刻, 监视器只打开"桥没独占"的节点。

  桥在跑      : 只开 xbox
  抓码(桥停)  : 关 xbox(虚拟设备随桥销毁), 开 raw + cons
  抓码结束    : 先关 raw/cons 并清零状态 → 再启动桥 → xbox 回来后再开

顺序很重要: 必须先关 raw/cons 再启动桥。反过来会让监视器与桥同时持有
raw/cons, 互相吃事件(2026-11-24 回归的根因)。

三个数据源:
  xbox = 桥输出的虚拟 Xbox(= padd 实际读到的)
  raw   = 蓝牙原始手柄节点
  cons  = 原始节点的媒体键子节点

POST /hold — 抓原始键码: 停桥+padd 90 秒(此时手柄不可能驱动机器人), 到点自动恢复。
"""
import http.server
import json
import os
import re
import select
import struct
import subprocess
import threading
import time

PORT = 8091
HOLD_SECONDS = 90
BRIDGE_UNIT = 'gamesir-xboxd.service'
PADD_UNIT = 'padd.service'

NODES = {
    'xbox': 'Xbox 360 Controller (GameSir Nova 2 Lite)',
    'raw': 'GameSir-Nova Lite',
    'cons': 'GameSir-Nova Lite Consumer Control',
}
# 哪些节点归桥独占(桥在跑时监视器不得打开)
BRIDGE_OWNED = ('raw', 'cons')
LABEL = {'xbox': '桥输出·Xbox', 'raw': '原始手柄', 'cons': '媒体键节点'}
BTN = {304: 'A(下)', 305: 'B(右)', 306: 'C', 307: 'Y(上)', 308: 'X(左)', 309: 'Z',
       310: 'LB', 311: 'RB', 312: 'TL2', 313: 'TR2', 314: 'SELECT', 315: 'START',
       316: 'HOME', 317: 'L3(左摇杆按)', 318: 'R3(右摇杆按)'}
KEYN = {102: 'Home键', 158: '返回Back', 159: '前进Forward', 163: '下一曲', 164: '播放/暂停',
        165: '上一曲', 166: '停止', 226: '媒体键', 353: '选择Select', 358: '信息Info',
        364: '频道+', 365: '频道-', 402: '频道上', 403: '频道下'}
AXN = {0: 'LX', 1: 'LY', 2: 'LT(Z)', 3: 'RX', 4: 'RY', 5: 'RT(RZ)',
       9: 'RT(GAS)', 10: 'LT(BRAKE)', 16: '十字左右', 17: '十字上下'}

lock = threading.Lock()
state = {k: {'present': False, 'buttons': {}, 'axes': {}, 'last_down': {}} for k in NODES}
events = []
fds = {}          # kind -> 已打开的文件对象(仅当前"允许打开"的节点)
paths = {}        # kind -> 最近一次成功打开的节点路径

# 抓码窗口状态。hold_until > now 即窗口内。
# hold_began: 本进程最近一次发起抓码的时刻; None 表示本进程没发起过。
# 恢复逻辑只接管"自己发起的抓码", 不接管别人手动停掉的桥。
hold_until = 0.0
hold_began = None


# ---------------------------------------------------------------- 工具

def push_event(dev, name, val):
    events.append({'t': round(time.time(), 2), 'dev': dev, 'name': name, 'val': val})
    del events[:-120]


def unit_active(unit):
    try:
        r = subprocess.run(['systemctl', 'is-active', unit], capture_output=True,
                           text=True, timeout=10)
        return r.stdout.strip() == 'active'
    except Exception:
        return False


def find_event(name):
    try:
        text = open('/proc/bus/input/devices').read()
    except OSError:
        return None
    for block in text.split('\n\n'):
        if f'N: Name="{name}"' in block:
            m = re.search(r'event\d+', block)
            if m:
                return '/dev/input/' + m.group(0)
    return None


def _close_locked(kind):
    """关闭某节点的 fd 并清零其显示状态。调用方须持 lock。"""
    fd = fds.pop(kind, None)
    if fd is not None:
        try:
            fd.close()
        except OSError:
            pass
    st = state[kind]
    for c in list(st['buttons'].keys()):
        st['buttons'][c] = 0
    st['axes'].clear()
    st['present'] = False


def _allowed_kinds():
    """此刻允许打开的节点集合。"""
    with lock:
        holding = time.time() < hold_until
    return ('raw', 'cons') if holding else ('xbox',)


# ---------------------------------------------------------------- 读取线程

def _rescan_once(allowed):
    """让已打开的节点集合与 allowed 一致。"""
    with lock:
        for kind in list(fds.keys()):
            if kind not in allowed:
                _close_locked(kind)
                push_event('系统', f'{LABEL[kind]} 已释放(归桥独占)', 1)
    for kind in allowed:
        path = find_event(NODES[kind])
        with lock:
            if path is None:
                if kind in fds or paths.get(kind) is not None:
                    _close_locked(kind)
                    paths[kind] = None
                continue
            if paths.get(kind) == path and kind in fds:
                continue  # 已打开且路径没变
            # 路径变了或还没开: 换成新的
            _close_locked(kind)
        try:
            fd = open(path, 'rb', buffering=0)
            os.set_blocking(fd.fileno(), False)
        except OSError:
            with lock:
                paths[kind] = None
            continue
        with lock:
            fds[kind] = fd
            paths[kind] = path
            state[kind]['present'] = True
            push_event('系统', f'{LABEL[kind]} 节点已接入 {path}', 1)


def _read_ready_locked(kind, fd):
    """读一个就绪的 fd 并更新状态。调用方须持 lock, 且 fds[kind] is fd 已确认。"""
    try:
        data = fd.read(4096)
    except (BlockingIOError, OSError, ValueError):
        return True   # 暂时读不到, 不算失效
    if not data:
        return False  # 0 字节: 设备已消失(如桥停时虚拟 Xbox 被销毁)
    now = time.time()
    st = state[kind]
    for i in range(0, len(data) - 23, 24):
        _, _, typ, code, val = struct.unpack_from('<qqHHi', data, i)
        if typ == 1:  # EV_KEY
            st['buttons'][code] = val
            if val == 1:
                st['last_down'][code] = now
            if val != 2:
                push_event(LABEL[kind], BTN.get(code) or KEYN.get(code) or f'键码{code}', val)
        elif typ == 3:  # EV_ABS
            st['axes'][code] = val
            if code in (16, 17) and val != 0:
                push_event(LABEL[kind], AXN.get(code, code), val)
    return True


def _reader_once():
    while True:
        try:
            allowed = _allowed_kinds()
            _rescan_once(allowed)
            with lock:
                watch = [(k, f) for k, f in fds.items()]
            if not watch:
                time.sleep(0.3)
                continue
            by_fd = {f.fileno(): (k, f) for k, f in watch}
            r, _, _ = select.select([f for _, f in watch], [], [], 1.0)
            with lock:
                for f in r:
                    kind, fd = by_fd[f.fileno()]
                    if fds.get(kind) is not fd:
                        continue  # 本轮期间已被关闭/替换
                    if not _read_ready_locked(kind, fd):
                        # 设备消失: 关闭并清零, 下一轮 rescan 决定是否重开
                        _close_locked(kind)
                        paths[kind] = None
        except Exception as exc:  # noqa: BLE001 — 看护点, 线程不得静默死亡
            print('[pad_live] 读取循环异常: %r — 1 秒后继续' % (exc,), flush=True)
            time.sleep(1.0)


def reader_loop():
    """读取线程入口: 包一层看护。_reader_once 已自带 try, 这里再兜一层,
    保证任何情况下线程都活着(HTTP 层 200 而线程已死会让页面看起来像"手柄没反应")。"""
    n = 0
    while True:
        try:
            _reader_once()
        except Exception as exc:  # noqa: BLE001
            n += 1
            print('[pad_live] 读取线程退出(第%d次): %r — 1 秒后重启' % (n, exc), flush=True)
            time.sleep(1.0)


# ---------------------------------------------------------------- 抓码模式

def hold_loop():
    """抓码窗口的到点恢复。

    只在本进程发起过抓码、且该次窗口已结束、而桥还没回来时接管——不接管别人
    (如手动 systemctl stop)造成的停止, 否则诊断时停不掉桥。

    不另记"是否已恢复"的标记: 需要恢复与否由可直接观察的事实决定——
    最近一次抓码的开始/结束时间 + 桥当前是否在跑。靠标记位的状态机在原版
    (stopped)和第一次重构(restored)里各漏了一次置位, 恢复分支都永不执行。
    """
    while True:
        try:
            with lock:
                began = hold_began
                ended = hold_until <= time.time() and hold_until > 0.0
            if began is not None and ended and not unit_active(BRIDGE_UNIT):
                # 顺序: 先关 raw/cons(它们归桥独占) → 启动桥 → xbox 由 rescan 接回
                _rescan_once(('xbox',))
                for unit in (BRIDGE_UNIT, PADD_UNIT):
                    subprocess.run(['systemctl', 'start', unit], timeout=30,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                ok = unit_active(BRIDGE_UNIT) and unit_active(PADD_UNIT)
                if ok:
                    push_event('系统', '抓码模式结束，桥与 padd 已恢复', 1)
                else:
                    push_event('系统', '⚠ 抓码结束但桥/padd 未能启动, 请手动: '
                               'systemctl start gamesir-xboxd padd', 1)
        except Exception as exc:  # noqa: BLE001 — 看护点
            print('[pad_live] 恢复循环异常: %r' % (exc,), flush=True)
        time.sleep(1.0)


def start_hold():
    global hold_until, hold_began
    with lock:
        if time.time() < hold_until:
            return False           # 已在窗口内
        hold_until = time.time() + HOLD_SECONDS
        hold_began = time.time()
    # 先关 xbox(桥一停虚拟设备就没了), 再停桥/padd; raw/cons 由 rescan 接入
    _rescan_once(('raw', 'cons'))
    for unit in (BRIDGE_UNIT, PADD_UNIT):
        subprocess.run(['systemctl', 'stop', unit], timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    push_event('系统', f'抓码模式：桥与 padd 已停 {HOLD_SECONDS} 秒，现在按手柄上的键看原始码', 1)
    return True


# ---------------------------------------------------------------- HTTP

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype='text/html; charset=utf-8'):
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/state':
            with lock:
                payload = json.dumps({
                    'now': time.time(),
                    'hold_until': hold_until,
                    'nodes': {k: state[k] for k in NODES},
                    'events': events[-48:],
                }, ensure_ascii=False)
            self._send(payload.encode(), 'application/json; charset=utf-8')
        else:
            self._send(PAGE.encode())

    def do_POST(self):
        if self.path == '/api/bt-pair':
            proc = subprocess.run(
                ['/opt/duck-calib/bt_pair_proper.py'],
                capture_output=True, text=True, timeout=60)
            result = proc.stdout[-800:] + ('\nERR: ' + proc.stderr[-200:] if proc.returncode else '')
            body = json.dumps({'ok': proc.returncode == 0, 'output': result},
                              ensure_ascii=False).encode()
            self._send(body, 'application/json; charset=utf-8')
        elif self.path == '/hold':
            ok = start_hold()
            body = json.dumps({'ok': ok}, ensure_ascii=False).encode()
            self._send(body, 'application/json; charset=utf-8')
        else:
            self.send_error(404)


def main():
    # 启动自愈: 若上次抓码窗口内本进程被杀, 桥/padd 会留在停止状态。进程重新起来时
    # 不可能处于窗口内(hold_until 初值为 0), 直接拉起。
    for unit in (BRIDGE_UNIT, PADD_UNIT):
        if not unit_active(unit):
            subprocess.run(['systemctl', 'start', unit], timeout=30,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print('[pad_live] 启动自愈: 已拉起 %s' % unit, flush=True)
    threading.Thread(target=reader_loop, daemon=True).start()
    threading.Thread(target=hold_loop, daemon=True).start()
    srv = http.server.ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    print(f'pad_live on :{PORT}', flush=True)
    srv.serve_forever()


if __name__ == '__main__':
    main()
