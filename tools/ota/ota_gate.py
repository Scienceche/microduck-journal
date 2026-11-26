#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stand 槽接管的部署门禁(阶段 8, 2026-11-26 定稿)。

用户定的规矩, 逐条落实:
  1. 影子报告 sha256 == 候选包 sha256(写死于本文件) == 鸭子上候选文件实际哈希 == manifest 链。
  2. 影子运行 ≥30 分钟 且 falls ≥10(上升沿计数)。
  3. invalid ≤0.1%。
  4. 主循环 missed 增速 ≤5/分钟(用累计值/运行时长算, 不依赖某一次采样窗口)。
  5. RSS ≤ 基线+20MB(基线写死: 影子期两段实测 47.0MB@44min / 46.7MB@15min)。
  6. 回放报告必须有且 verdict=ok, 覆盖四种姿态(俯/仰/左/右, 每类≥1, 总数≥10)
     —— replay_falls.py 的产物; 没有真实倒地数据就不可能有 verdict。
  7. diff_norm 只打印记录, 不是门禁项。
  8. 4 位部署 Token 由用户现场输入(不回显, 不写任何文件/日志, 校验后即弃)。
  9. 契约核对: 候选包 contract.slot=="stand" 且 firmware_p==5(与现役一致)。
 10. 报告活性: 连读两次(间隔2秒), running_secs 必须增长 —— 证明读到的是活报告,
     不是陈旧快照(报告每秒发布一次, 最多滞后1秒)。

全部通过才写 verdict 文件(不含 Token), 供 ota_deploy.py --slot stand 使用;
verdict 文件 10 分钟内有效(新鲜度), 过期作废重跑。
用法:
  python ota_gate.py --replay <replay_report.json> [--out verdict.json]
  (须在有终端的窗口运行 —— Token 要现场输入; 非交互环境直接拒绝)
"""
import argparse
import getpass
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

# ---- 写死的候选身份(2026-11-26 部署的影子候选) ----
CANDIDATE_SHA = "205c7469667f8d4e5f9875e55a0d76122a8767c5a7cebe4e4038a24ecc99d062"
CANDIDATE_DIR = "/opt/robot/feetech-ft5-r5/models/" + CANDIDATE_SHA
# ---- 门限(用户 2026-11-25 拍板) ----
MIN_RUNNING_SECS = 1800.0
MIN_FALLS = 10
MAX_INVALID_RATE = 0.001        # 0.1%
MAX_MISSED_PER_MIN = 5.0
RSS_BASELINE_MB = 47.0          # 影子期实测基线(见 2026-11-26 调试记录)
RSS_MAX_GROWTH_MB = 20.0
VERDICT_FRESH_SECS = 600       # verdict 10 分钟内有效
POSTURES = ("face_down", "face_up", "left", "right")   # 与 replay_falls.py 的分类键一致

DUCK = "root@192.168.31.96"
KEY = r"C:\Users\c1349\.ssh\id_ed25519_duck"
SSH = ["ssh", "-i", KEY, "-o", "ConnectTimeout=15",
       "-o", "StrictHostKeyChecking=no", DUCK]

checks = []  # (name, ok, detail)


def step(name, ok, detail=""):
    checks.append((name, bool(ok), detail))
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                         (" — " + detail) if detail else ""), flush=True)


def duck_json(cmd, timeout=40):
    """执行远程命令并解析最后一行 JSON。"""
    try:
        r = subprocess.run(SSH + [cmd], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        return None
    line = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    try:
        return json.loads(line)
    except Exception:
        return None


def call_ipc(method):
    """鸭子端经 /run/robotd.sock 读一个 IPC 方法。"""
    return duck_json("python3 -c \""
                     "import socket,json;"
                     "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);s.settimeout(6);"
                     "s.connect('/run/robotd.sock');"
                     "s.sendall((json.dumps({'jsonrpc':'2.0','id':1,'method':'%s'})+'\\n').encode());"
                     "print(json.dumps(json.loads(s.makefile().readline())['result']))\"" % method)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", required=True,
                    help="replay_falls.py 的报告 JSON(必须 verdict=ok)")
    ap.add_argument("--out", default=r"C:\duck_work\gate_verdict.json",
                    help="verdict 输出文件(不含 Token)")
    ap.add_argument("--skip-token", action="store_true",
                    help="仅供干跑检查各项数值; 不产出可用的 verdict")
    a = ap.parse_args()

    print("==== stand 槽接管的部署门禁 ====")
    print("候选(写死): %s" % CANDIDATE_SHA[:16])

    # ---- 1. 鸭子在线 + 候选包哈希链(鸭子端实算) ----
    probe = duck_json(
        "python3 -c \"import json,hashlib;"
        "d='" + CANDIDATE_DIR + "';"
        "sha=hashlib.sha256(open(d+'/policy.onnx','rb').read()).hexdigest();"
        "m=json.load(open(d+'/manifest.json'));"
        "r=json.load(open(d+'/deployment-runtime.json'));"
        "c=json.load(open(d+'/deployment-contract.json'));"
        "print(json.dumps({'sha':sha,'m':m.get('onnx_sha256'),'r':r.get('model_sha256'),"
        "'slot':c.get('slot'),'fp':c.get('model',{}).get('actuators',[{}])[0].get('kp_fw'),"
        "'task':c.get('task')}))\"", timeout=60)
    step("鸭子在线+候选包存在", probe is not None)
    if probe is None:
        sys.exit(2)
    step("候选包哈希链 文件=manifest=runtime=写死值",
         probe.get("sha") == probe.get("m") == probe.get("r") == CANDIDATE_SHA,
         str(probe.get("sha"))[:12])
    step("契约 slot==stand", probe.get("slot") == "stand", str(probe.get("slot")))
    step("契约 firmware_p==5(与现役一致)", probe.get("fp") == 5.0 or probe.get("fp") == 5,
         str(probe.get("fp")))

    # ---- 2. 影子报告: 身份 + 活性(两次读, running_secs 必须增长) ----
    rep1 = call_ipc("robot.shadowReport")
    step("影子报告可读", rep1 is not None)
    if rep1 is None:
        sys.exit(2)
    step("报告 sha256==候选(写死值)", rep1.get("sha256") == CANDIDATE_SHA,
         str(rep1.get("sha256"))[:12])
    step("报告路径==候选包", rep1.get("path") == CANDIDATE_DIR + "/policy.onnx",
         str(rep1.get("path"))[-40:])
    step("影子 active", rep1.get("active") is True)
    time.sleep(2)
    rep2 = call_ipc("robot.shadowReport")
    grew = (rep2 or {}).get("running_secs", 0) > rep1.get("running_secs", 0)
    step("报告是活的(running_secs 在增长)", grew,
         "%.1fs -> %.1fs" % (rep1.get("running_secs", 0), (rep2 or {}).get("running_secs", -1)))

    # ---- 3. 时长 / 摔倒次数 / 失败率 ----
    run = rep1.get("running_secs") or 0
    step("影子运行 ≥30 分钟", run >= MIN_RUNNING_SECS,
         "%.1f 分钟 (需要 ≥%.0f)" % (run / 60, MIN_RUNNING_SECS / 60))
    falls = rep1.get("falls") or 0
    step("falls ≥10", falls >= MIN_FALLS, "%d 次" % falls)
    inferred = rep1.get("inferred") or 0
    invalid_rate = (rep1.get("invalid") or 0) / inferred if inferred else 1.0
    step("invalid ≤0.1%%", invalid_rate <= MAX_INVALID_RATE,
         "%d/%d = %.4f%%" % (rep1.get("invalid") or 0, inferred, invalid_rate * 100))

    # ---- 4. 主循环 missed 增速(累计值 / 运行时长) ----
    h = call_ipc("robot.health") or {}
    cl = h.get("control_loop") or {}
    missed = cl.get("missed")
    pid = duck_json("systemctl show robotd -p MainPID --value | tr -d ' '")
    uptime = duck_json("ps -o etimes= -p %s | tr -d ' '" % pid) if pid else None
    if missed is not None and uptime:
        rate = missed / max(float(uptime) / 60.0, 0.1)
        step("主循环 missed ≤5/分钟", rate <= MAX_MISSED_PER_MIN,
             "累计 %d / 运行 %.0f 分钟 = %.2f/分钟" % (missed, float(uptime) / 60, rate))
    else:
        step("主循环 missed ≤5/分钟", False, "读不到 missed/uptime")

    # ---- 5. RSS 增长 ----
    rss = duck_json("awk '/VmRSS/{print $2}' /proc/%s/status" % pid) if pid else None
    if rss is not None:
        rss_mb = float(rss) / 1024.0
        step("RSS ≤ 基线+20MB", rss_mb <= RSS_BASELINE_MB + RSS_MAX_GROWTH_MB,
             "%.1fMB (基线 %.1f + 上限 %.1f)" % (rss_mb, RSS_BASELINE_MB, RSS_MAX_GROWTH_MB))
    else:
        step("RSS ≤ 基线+20MB", False, "读不到 VmRSS")

    # ---- 6. 回放报告 ----
    rp_path = Path(a.replay)
    step("回放报告文件存在", rp_path.is_file(), str(rp_path))
    rp = {}
    if rp_path.is_file():
        try:
            rp = json.loads(rp_path.read_text(encoding="utf-8"))
        except Exception as e:
            step("回放报告可解析", False, repr(e)[:80])
    if rp:
        step("回放 verdict=ok", rp.get("verdict") == "ok", str(rp.get("verdict")))
        post = rp.get("postures") or {}
        counts = {p: int(post.get(p) or 0) for p in POSTURES}
        step("回放覆盖四种姿态(每类≥1)", all(v >= 1 for v in counts.values()),
             " ".join("%s=%d" % kv for kv in counts.items()))
        step("回放倒地样本 ≥10", int(rp.get("falls_total") or 0) >= MIN_FALLS,
             "%d 段" % int(rp.get("falls_total") or 0))
        step("回放 sha==候选", rp.get("candidate_onnx_sha256") == CANDIDATE_SHA,
             str(rp.get("candidate_onnx_sha256"))[:12])

    # ---- 7. diff_norm: 只记录, 不是门禁 ----
    print("[记录] diff_norm_mean=%.4f (只记录, 不门禁; 站立期约0.15, 倒地期约0.62)"
          % (rep1.get("diff_norm_mean") or -1))

    n_fail = sum(1 for _, ok, _ in checks if not ok)
    print("\n==== 门禁 %d/%d PASS ====" % (len(checks) - n_fail, len(checks)))
    if n_fail:
        print("有 %d 项未过, 不进入 Token 环节" % n_fail)
        sys.exit(1)

    # ---- 8. Token(现场输入, 不回显, 不落任何文件; 非交互直接拒绝) ----
    if a.skip_token:
        print("干跑模式: 跳过 Token, 不写 verdict")
        sys.exit(0)
    if not sys.stdin.isatty():
        print("拒绝: 非交互环境不能出 verdict(Token 必须用户现场输入)")
        sys.exit(2)
    tok = getpass.getpass("现场输入 4 位部署 Token(输入不回显): ")
    if not (len(tok) == 4 and tok.isdigit()):
        print("Token 不是 4 位数字, 拒绝")
        sys.exit(2)
    # 用完即弃 —— 不写文件, 不打印, 不进任何日志
    del tok

    verdict = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "candidate_sha256": CANDIDATE_SHA,
        "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in checks],
        "diff_norm_mean_recorded": rep1.get("diff_norm_mean"),
        "falls": falls, "running_secs": run, "invalid_rate": invalid_rate,
        "token": "entered-live-not-stored",
        "fresh_for_s": VERDICT_FRESH_SECS,
    }
    Path(a.out).write_text(json.dumps(verdict, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    print("verdict 已写: %s (10 分钟内有效)" % a.out)
    print("接手命令: python ota_deploy.py <候选包目录> --slot stand --gate-verdict %s" % a.out)
    sys.exit(0)


if __name__ == "__main__":
    main()
