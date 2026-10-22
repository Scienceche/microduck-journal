"""verify_board — IMU 板烧后自检门禁（每次烧录/复位后必须跑，PASS 才算完成）。

部署在 Radxa /root/duck-panel/，用法（在 Radxa 上）：
  python3 verify_board.py            # 快检：身份/版本/诊断/30s 漂移采样
  python3 verify_board.py --wdt      # 加测看门狗（板会复位一次）

检查项（覆盖"变更点"和"没动的东西"两类——2026-10-22 驱动回退事故的教训）：
  1 身份与版本    reg0-1，必须 >= 2.0（旧版无版本可观测性）
  2 构建日期戳    reg147-149（板上到底哪一版，一读便知）
  3 复位诊断      reg144-146（复位原因/看门狗计数/测试位）
  4 融合在线      status bit1（SFLP 引擎活）
  5 静置漂移      30s 四元数最大漂移 < 0.2（翻滚会 >>1，正常 yaw 漂 ~0.04/min）
  6 陀螺零偏      均值 |g| < 60 LSB（~0.9 dps）
  7 采样推进      sample_count 持续增长（~100Hz）
  8 [--wdt]      看门狗实测：计数 +1 且 IWDG 标志置位

退出码 0=PASS 1=FAIL。
"""

import struct
import sys
import time

import feetech

IMU_ID = 200
GYRO_FS_LSB = 32768.0
QUAT_DRIFT_LIMIT = 0.2     # 30s 内四元数最大漂移
GYRO_BIAS_LIMIT_LSB = 60   # ~0.9 dps
SAMPLE_SECONDS = 30


def main():
    wdt = "--wdt" in sys.argv
    bus = feetech.FeetechBus("/dev/ttyS2")
    fails = []

    def check(name, ok, detail):
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        if not ok:
            fails.append(name)

    def rd(addr, n, retries=3):
        for i in range(retries):
            try:
                _, p = bus.read(IMU_ID, addr, n)
                return p
            except feetech.BusError:
                if i == retries - 1:
                    raise
                time.sleep(0.5)

    print("== IMU 板自检 ==")
    # 1-3 身份/版本/诊断
    ident = rd(0, 2)
    fw = f"{ident[0]}.{ident[1]}"
    check("固件版本", ident[0] >= 2, f"v{fw}" + ("（旧版，无版本可观测，请重烧）" if ident[0] < 2 else ""))
    diag = rd(144, 6)
    flags, count, test = diag[0], diag[1], diag[2]
    y, m, d = diag[3], diag[4], diag[5]
    check("构建日期戳", 20 <= y <= 40 and 1 <= m <= 12 and 1 <= d <= 31,
          f"20{y:02d}-{m:02d}-{d:02d}")
    cs = []
    for bit, nm in ((1, "IWDG"), (2, "WWDG"), (4, "SFT"), (8, "POR"), (16, "PIN"), (32, "OBL")):
        if flags & bit:
            cs.append(nm)
    print(f"  [信息] 上次复位: {'+'.join(cs) or '-'} · 看门狗累计复位 {count} 次 · 测试位 {test}")

    # 4-7 静置采样
    def sample():
        p = rd(124, 20)
        g = struct.unpack_from("<3h", p, 0)
        q = struct.unpack_from("<3e", p, 6)
        cnt, = struct.unpack_from("<H", p, 12)
        status = p[14]
        return cnt, status, q, g

    cnt0, st0, q0, _ = sample()
    check("融合在线(SFLP)", bool(st0 & 2), f"status=0x{st0:02x}")
    print(f"  [信息] 采样 {SAMPLE_SECONDS}s（鸭子静置）…")
    qs = [q0]
    gs = []
    n = SAMPLE_SECONDS
    for i in range(n):
        time.sleep(1)
        try:
            _, _, q, g = sample()
            qs.append(q)
            gs.append(g)
        except feetech.BusError:
            fails.append("采样连续性")
            print("  [FAIL] 采样连续性: 总线读失败")
            break
    cnt1, st1, _, _ = sample()
    if gs:
        import math
        drift = max(
            math.sqrt(sum((a - b) ** 2 for a, b in zip(qa, qb)))
            for qa in qs for qb in qs
        )
        check(f"静置漂移(<{QUAT_DRIFT_LIMIT})", drift < QUAT_DRIFT_LIMIT,
              f"30s 最大 |Δq| = {drift:.3f}")
        mean_g = [sum(c[i] for c in gs) / len(gs) for i in range(3)]
        bias = max(abs(v) for v in mean_g)
        check("陀螺零偏", bias < GYRO_BIAS_LIMIT_LSB,
              f"均值 ({mean_g[0]:.0f},{mean_g[1]:.0f},{mean_g[2]:.0f}) LSB")
        check("采样推进", cnt1 > cnt0, f"{cnt0} → {cnt1}（应 ~100/s）")

    # 8 看门狗
    if wdt:
        print("  [信息] 看门狗实测：写停喂 → 等复位…")
        before = rd(144, 3)
        bus.write(IMU_ID, 146, b"\xA5")
        time.sleep(3.0)
        for _ in range(3):
            try:
                after = rd(144, 3)
                break
            except feetech.BusError:
                time.sleep(1.0)
        ok = bool(after[0] & 1) and after[1] > before[1]
        check("看门狗自证", ok,
              f"计数 {before[1]}→{after[1]} · IWDG 位={'1' if after[0] & 1 else '0'}")

    bus.close()
    print("== 结果: " + ("全部 PASS ✓" if not fails else f"FAIL × {len(fails)}: {', '.join(fails)}") + " ==")
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
