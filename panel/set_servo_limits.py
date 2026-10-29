"""set_servo_limits — 把官方模型的关节包络写进舵机固件的角度限制寄存器（9/11）。

这是硬件级保护：即便软件发疯，舵机固件也会拒绝越限目标位置，杜绝顶死堵转。
（飞特 HLS 协议: reg9=最小角度限制 0~4094, reg11=最大角度限制 1~4095, 0.087°/count）

用法（Radxa 上）: python3 set_servo_limits.py           # 写入并回读校验
                  python3 set_servo_limits.py --show    # 只读当前限制
"""

import sys

import feetech
import struct

# 模型包络(rad) + 策略 SIGN（与 duck_policy 一致）
JOINTS = [
    ("left_hip_yaw", 20, -1), ("left_hip_roll", 21, -1), ("left_hip_pitch", 22, -1),
    ("left_knee", 23, -1), ("left_ankle", 24, -1),
    ("neck_pitch", 30, -1), ("head_pitch", 31, -1), ("head_yaw", 32, -1), ("head_roll", 33, -1),
    ("right_hip_yaw", 10, -1), ("right_hip_roll", 11, +1), ("right_hip_pitch", 12, -1),
    ("right_knee", 13, -1), ("right_ankle", 14, -1),
]
LIMITS = {
    "left_hip_yaw": (-0.4363, 0.5236), "left_hip_roll": (-0.3840, 0.3840),
    "left_hip_pitch": (-1.5708, 1.5708), "left_knee": (-1.5708, 1.5708),
    "left_ankle": (-1.5708, 1.5708),
    "neck_pitch": (-1.5708, 1.0472), "head_pitch": (-1.5708, 1.5708),
    "head_yaw": (-2.9671, 2.9671), "head_roll": (-0.4363, 0.4363),
    "right_hip_yaw": (-0.5236, 0.4363), "right_hip_roll": (-0.3840, 0.3840),
    "right_hip_pitch": (-1.5708, 1.5708), "right_knee": (-1.5708, 1.5708),
    "right_ankle": (-1.5708, 1.5708),
}
K = 4096.0 / (300.0 * 3.141592653589793 / 180.0)
REG_MIN, REG_MAX = 9, 11


def main():
    show_only = "--show" in sys.argv
    bus = feetech.FeetechBus("/dev/ttyS2")
    print(f"{'关节':18s} {'sid':>3s} {'范围(rad)':22s} {'计数范围':14s} 结果")
    for name, sid, sign in JOINTS:
        lo, hi = LIMITS[name]
        c1 = 2048 + sign * lo * K
        c2 = 2048 + sign * hi * K
        cmin, cmax = sorted((int(c1), int(c2)))
        cmin = max(0, cmin)
        cmax = min(4095, cmax)
        if show_only:
            _, cur_lo = bus.read(sid, REG_MIN, 2)
            _, cur_hi = bus.read(sid, REG_MAX, 2)
            cur_lo, = struct.unpack("<H", cur_lo)
            cur_hi, = struct.unpack("<H", cur_hi)
            print(f"{name:18s} {sid:3d} [{lo:+.3f},{hi:+.3f}]".ljust(42),
                  f"[{cmin},{cmax}] 当前[{cur_lo},{cur_hi}]")
            continue
        import time as _t
        bus.write(sid, REG_MIN, struct.pack("<H", cmin))
        _t.sleep(0.08)  # EEPROM 写消化时间
        bus.write(sid, REG_MAX, struct.pack("<H", cmax))
        _t.sleep(0.15)
        v1 = v2 = None
        for _ in range(4):  # 回读带重试
            try:
                _, r1 = bus.read(sid, REG_MIN, 2)
                _, r2 = bus.read(sid, REG_MAX, 2)
                v1, = struct.unpack("<H", r1)
                v2, = struct.unpack("<H", r2)
                break
            except Exception:
                _t.sleep(0.3)
        ok = (v1 == cmin and v2 == cmax)
        print(f"{name:18s} {sid:3d} [{lo:+.3f},{hi:+.3f}]".ljust(42),
              f"[{cmin},{cmax}] {'✓ 已写入并回读一致' if ok else f'✗ 回读不符 [{v1},{v2}]'}")
    bus.close()
    print("完成" if not show_only else "仅查看模式")


if __name__ == "__main__":
    main()
