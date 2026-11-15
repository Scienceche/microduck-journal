#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OTA + 黑匣子本地干跑测试 v2(对抗审查后)。

覆盖:
  1. 三脚本语法
  2. ota_helper: 假 toml 上 snapshot/activate/rollback 全链路
     + 段落限定(段外同名键不受影响) + 单引号值 + 目标文件预检 + 备份槽位完整性
  3. duck_blackbox v2: fall/recover/brownout + 躺倒重武装(防事件风暴驱逐历史)
     + dump OSError 保护 + NTP 跳变免疫(monotonic)
  4. ota_deploy: restart_and_health 慢启动不打断 / sha_seen 全量判断 / tar 往返
"""
import gzip
import io
import json
import py_compile
import shutil
import sys
import tempfile
import time as real_time
from pathlib import Path

BASE = Path(r"C:\duck_work\ota")
sys.path.insert(0, str(BASE))

FAILS = []


def check(name, ok, detail=""):
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                         (" — " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


# ---------- 1. 语法 ----------
for f in ("ota_helper.py", "duck_blackbox.py", "ota_deploy.py"):
    try:
        py_compile.compile(str(BASE / f), doraise=True)
        check("语法 %s" % f, True)
    except py_compile.PyCompileError as e:
        check("语法 %s" % f, False, str(e)[:120])

# ---------- 2. ota_helper toml 全链路 ----------
import argparse
import ota_helper as oh

tmp = Path(tempfile.mkdtemp(prefix="ota_test_"))
try:
    fake_toml = tmp / "robotd.toml"
    # [robot] 段内放一个 decoy walk 键 → 段落限定必须不受影响
    fake_toml.write_text("""# robotd 配置(测试用)
[robot]
name = "microduck"
walk = "decoy-should-not-change"

[policy]
walk = "/opt/robot/feetech-ft5-r5/models/AAAA1111/policy.onnx"
stand = "/opt/robot/feetech-ft5-r5/models/BBBB2222/policy.onnx"
sitstand = "/opt/robot/feetech-ft5-r5/models/CCCC3333/policy.onnx"
""", encoding="utf-8")
    oh.TOML_PATH = fake_toml
    # flock 锁: 测试环境直接跳过(Windows 无 fcntl 也兼容)
    oh.acquire_lock = lambda: None

    # 目标文件预检: activate 检查 Path(new_path).is_file() — 打桩
    real_path_cls = oh.Path

    class FakeP:
        def __init__(self, p):
            self._p = str(p)
        def __fspath__(self):
            return self._p
        def is_file(self):
            return "DEAD4242" in self._p or real_path_cls(self._p).is_file()
    oh.Path = FakeP

    def run_sub(fn, **kw):
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            fn(argparse.Namespace(**kw))
        except SystemExit:
            pass
        finally:
            sys.stdout = old
        return json.loads(buf.getvalue().strip().splitlines()[-1])

    s = run_sub(oh.cmd_snapshot)
    check("snapshot ok", s.get("ok") is True and "toml_sha256" in s, str(s)[:100])
    check("snapshot 三槽位", set(s.get("slots", {})) == {"walk", "stand", "sitstand"})

    new_path = "/opt/robot/feetech-ft5-r5/models/DEAD4242/policy.onnx"
    act = run_sub(oh.cmd_activate, slot="walk", path=new_path)
    check("activate ok", act.get("ok") is True, str(act)[:150])
    bak = real_path_cls(str(act.get("backup", "")))
    check("activate 生成备份(pid 唯一名)", bak.is_file(), bak.name)

    txt = fake_toml.read_text(encoding="utf-8")
    check("walk 已切换", "DEAD4242" in txt)
    check("[robot] 段 decoy 键未被触碰", "decoy-should-not-change" in txt)
    check("stand/sitstand 未动", "BBBB2222" in txt and "CCCC3333" in txt)
    check("注释保留", "robotd 配置(测试用)" in txt)

    rb = run_sub(oh.cmd_rollback, backup=str(bak))
    check("rollback ok", rb.get("ok") is True, str(rb)[:100])
    check("rollback 后 walk 还原", "AAAA1111" in fake_toml.read_text(encoding="utf-8"))

    check("非法槽位拒绝", run_sub(oh.cmd_activate, slot="jump", path=new_path).get("ok") is not True)
    check("不存在备份拒绝回滚", run_sub(oh.cmd_rollback, backup=str(tmp / "none.bak")).get("ok") is not True)
    check("目标文件不存在拒绝", run_sub(
        oh.cmd_activate, slot="walk", path="/opt/nope/EEEE9999/policy.onnx").get("ok") is not True)

    # 备份缺槽位 → 拒绝恢复(防把槽位丢光)
    bad_bak = tmp / "bad.toml.bak"
    bad_bak.write_text('[robot]\nname = "x"\n', encoding="utf-8")
    check("缺槽位备份拒绝恢复", run_sub(oh.cmd_rollback, backup=str(bad_bak)).get("ok") is not True)

    # 单引号值支持
    fake_toml.write_text('[policy]\nwalk = \'/opt/robot/feetech-ft5-r5/models/AAAA1111/policy.onnx\'\nstand = "s"\nsitstand = "t"\n', encoding="utf-8")
    act2 = run_sub(oh.cmd_activate, slot="walk", path=new_path)
    check("单引号值可切换", act2.get("ok") is True and "DEAD4242" in fake_toml.read_text(encoding="utf-8"))

    oh.Path = real_path_cls
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ---------- 3. blackbox v2 状态机 ----------
import duck_blackbox as bx


class FakeTime:
    """可拨动的时钟: sleep 推进 t, monotonic/time 读 t。"""
    def __init__(self):
        self.t = 1000.0
    def monotonic(self):
        return self.t
    def time(self):
        return self.t
    def sleep(self, s):
        self.t += max(s, 0.0)
    def strftime(self, fmt):
        return real_time.strftime(fmt, real_time.localtime(self.t))


class Script:
    """帧脚本: 段列表 [(时长s, tilt, V), ...], 依次供给。"""
    def __init__(self, segs):
        self.segs = list(segs)
        self.i = 0        # 当前段
        self.used = 0.0   # 当前段已消耗
    def frame(self):
        if self.i >= len(self.segs):
            raise KeyboardInterrupt
        dur, tilt, V = self.segs[self.i]
        f = {"wall": None, "phase": "control", "mode": "motion",
             "policy_enabled": True, "tilt": tilt, "imu_ok": True,
             "ids": list(range(15)), "q": [0.1] * 15,
             "V_min": V, "servos": 15, "fresh": True}
        self.used += 0.1
        if self.used >= dur:
            self.i += 1
            self.used = 0.0
        return f


def run_blackbox(segs):
    """跑一段脚本, 返回 (事件类型列表, ring 大小列表, 元数据列表)。"""
    ft = FakeTime()
    bx.time = ft
    script = Script(segs)
    dumps = []

    def scripted(timeout=3.0):
        f = script.frame()
        f["wall"] = ft.t
        return f

    def fake_dump(etype, ring, meta):
        meta = dict(meta)
        meta["_t"] = ft.t
        dumps.append((etype, len(ring), meta))
        return Path("/tmp/fake_%s.json.gz" % etype)

    bx.bus_status = scripted
    bx.dump_event = fake_dump
    bx.journal_context = lambda pre_s=40: ["fake journal"]
    bx.hb = lambda msg: None
    bx.disk_ok = lambda: True  # Windows 测试无法探测 /root, 打桩
    try:
        bx.record()
    except KeyboardInterrupt:
        pass
    return dumps


# 场景A: 正常→摔倒→恢复→低压 (基本链路)
dumps = run_blackbox([
    (40.0, 5.0, 7.9),    # 填满 300 环
    (0.5, 50.0, 7.9),    # 摔倒(3 帧确认)
    (11.0, 35.0, 7.9),   # 挣扎(30-45° 区间: 不恢复也不脱离事件) — 让 recover 落盘脱离 10s 共享冷却
    (3.0, 8.0, 7.9),     # 恢复
    (0.5, 5.0, 7.0),     # 低压
    (1.0, 5.0, 7.9),
])
types = [d[0] for d in dumps]
check("A: fall 触发", "fall" in types, str(types))
check("A: fall 前缓冲满(300)", any(d[0] == "fall" and d[1] == 300 for d in dumps),
      str([d[1] for d in dumps if d[0] == "fall"]))
check("A: recover 落盘", "recover" in types, str(types))
check("A: brownout 触发", "brownout" in types, str(types))

# 场景B: 躺倒不扶 90s — 只允许 1 fall + 1 stuck, 不允许重复 fall(证据驱逐防线)
dumps_b = run_blackbox([
    (35.0, 5.0, 7.9),
    (0.5, 50.0, 7.9),
    (90.0, 55.0, 7.9),   # 持续躺倒
])
tb = [d[0] for d in dumps_b]
n_fall = tb.count("fall")
check("B: 躺倒 90s 只 1 次 fall(无重复触发)", n_fall == 1, str(tb))
check("B: stuck 落盘 1 次", tb.count("stuck") == 1, str(tb))

# 场景B2: 躺倒后扶起(倾角回稳) → 重新武装 → 允许再次 fall
dumps_b2 = run_blackbox([
    (35.0, 5.0, 7.9),
    (0.5, 50.0, 7.9),
    (50.0, 55.0, 7.9),   # 躺倒(stuck 后解除武装)
    (3.0, 5.0, 7.9),     # 扶起 → 回稳 2s → 重新武装
    (0.5, 50.0, 7.9),    # 再摔 → 应触发新 fall
])
tb2 = [d[0] for d in dumps_b2]
check("B2: 扶起后重新武装(可再触发 fall)", tb2.count("fall") == 2, str(tb2))

# 场景C: NTP 跳变免疫 — wall 时间回拨 30min, 状态机用 monotonic 不受影响
# (如果用 wall 时钟, stuck 40s 超时会永不触发; 这里 45s 躺倒应触发 stuck)
class JumpTime(FakeTime):
    def time(self):
        # wall 比 monotonic 慢 1800s(模拟回拨后)
        return self.t - 1800.0
ft = JumpTime()
script_c = Script([(35.0, 5.0, 7.9), (0.5, 50.0, 7.9), (50.0, 55.0, 7.9)])
dumps_c = []
bx.time = ft
def scripted_c(timeout=3.0):
    f = script_c.frame()
    f["wall"] = ft.time()
    return f
bx.bus_status = scripted_c
bx.dump_event = lambda etype, ring, meta: (dumps_c.append(etype), Path("/tmp/f"))[1]
bx.hb = lambda msg: None
bx.disk_ok = lambda: True
bx.journal_context = lambda pre_s=40: []
try:
    bx.record()
except KeyboardInterrupt:
    pass
check("C: wall 回拨 30min 不卡死(stuck 仍触发)", "stuck" in dumps_c, str(dumps_c))

# 场景D: dump_event OSError 保护 — gzip 失败不崩, 服务继续
ft2 = FakeTime()
bx.time = ft2
script_d = Script([(35.0, 5.0, 7.9), (0.5, 50.0, 7.9), (10.0, 55.0, 7.9)])
frames_seen = []
def scripted_d(timeout=3.0):
    f = script_d.frame()
    f["wall"] = ft2.t
    frames_seen.append(1)
    return f
bx.bus_status = scripted_d
bx.OUT_DIR = Path(tempfile.mkdtemp(prefix="bb_dmp_"))
bx.journal_context = lambda pre_s=40: []
bx.hb = lambda msg: None
bx.disk_ok = lambda: True
class BadGzip:
    def open(self, *a, **kw):
        raise OSError("Read-only file system")
bx.gzip = BadGzip()
survived = True
try:
    bx.record()
except KeyboardInterrupt:
    pass
except Exception as e:
    survived = False
check("D: gzip OSError 不崩服务(继续采样)", survived and len(frames_seen) >= 400,
      "frames=%d survived=%s" % (len(frames_seen), survived))
shutil.rmtree(bx.OUT_DIR, ignore_errors=True)
bx.OUT_DIR = Path("/root/blackbox")

# ---------- 4. ota_deploy ----------
import ota_deploy as od


class FakeMono:
    def __init__(self):
        self.t = 0.0
    def sleep(self, s):
        self.t += s
    def monotonic(self):
        return self.t


def run_rh(health_seq, restarts=2):
    od.time = FakeMono()
    calls = {"restart": 0, "health": 0}
    def fake_ssh(cmd, timeout=60):
        if "restart robotd" in cmd:
            calls["restart"] += 1
        return 0, "1700000000", ""
    def fake_helper(sub, timeout=60, **kw):
        if sub == "health":
            i = min(calls["health"], len(health_seq) - 1)
            calls["health"] += 1
            return health_seq[i]
        return {"ok": True}
    od.ssh, od.helper = fake_ssh, fake_helper
    import importlib
    try:
        h = od.restart_and_health(sha12="abc123", restarts=restarts)
    finally:
        importlib.reload(od)
    return h, calls["restart"]


h, n = run_rh([{"ok": False}, {"ok": False, "sha_seen": False},
               {"ok": True, "sha_seen": True}])
check("慢启动不被反复 restart", h.get("ok") is True and n == 1, "restart=%d" % n)

h2, n2 = run_rh([{"ok": False}])
check("彻底失败恰好 2 次 restart", h2.get("ok") is not True and n2 == 2, "restart=%d" % n2)

# sha_seen 全量判断(误回滚回归)
fake_journal_out = "\n".join(
    ["WARN robotd: bus read failed error=telemetry too slow"] * 12
    + ["INFO robotd: policy loaded walk=/opt/robot/feetech-ft5-r5/models/abc123def456/policy.onnx"]
    + ["WARN robotd: bus read failed error=telemetry too slow"] * 3)


class FakeProc:
    def __init__(self, out):
        self.stdout = out
        self.returncode = 0


orig_run = oh.subprocess.run
orig_bus = oh.bus_status
oh.subprocess.run = lambda cmd, **kw: (
    FakeProc("active") if "systemctl" in cmd
    else FakeProc(fake_journal_out) if "journalctl" in cmd
    else FakeProc(""))
oh.bus_status = lambda timeout=6.0: {"mode": "motion", "phase": "control",
                                     "servos": 15, "fresh": True, "V": 8.0,
                                     "V_min": 7.9, "tilt": 0.0}
buf = io.StringIO()
_old = sys.stdout
sys.stdout = buf
try:
    oh.cmd_health(argparse.Namespace(sha="abc123def456", since=None))
except SystemExit:
    pass
finally:
    sys.stdout = _old
    oh.subprocess.run = orig_run
    oh.bus_status = orig_bus
hv = json.loads(buf.getvalue().strip().splitlines()[-1])
check("sha_seen 全量判断(加载行被错误行淹没仍 True)", hv.get("sha_seen") is True,
      str([ln[:28] for ln in hv.get("journal", [])[-2:]]))

# tar 往返(含子目录)
import tarfile
src = Path(tempfile.mkdtemp(prefix="ota_tar_src_"))
dst = Path(tempfile.mkdtemp(prefix="ota_tar_dst_"))
try:
    (src / "policy.onnx").write_bytes(b"fake-onnx")
    (src / "manifest.json").write_text("{}", encoding="utf-8")
    (src / "upstream-params").mkdir()
    (src / "upstream-params" / "env.yaml").write_text("k: v", encoding="utf-8")
    tar_path = Path(tempfile.gettempdir()) / "ota_test_roundtrip.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(str(src), arcname=".")
    with tarfile.open(tar_path) as tf:
        tf.extractall(dst, filter="data")
    ok_rt = ((dst / "policy.onnx").read_bytes() == b"fake-onnx"
             and (dst / "upstream-params" / "env.yaml").read_text(encoding="utf-8") == "k: v")
    check("tar 往返(含 upstream-params 子目录)", ok_rt)
    tar_path.unlink(missing_ok=True)
finally:
    shutil.rmtree(src, ignore_errors=True)
    shutil.rmtree(dst, ignore_errors=True)

# ---------- 汇总 ----------
print()
if FAILS:
    print("==== %d 项 FAIL: %s ====" % (len(FAILS), ", ".join(FAILS)))
    sys.exit(1)
print("==== 全部 PASS ====")
