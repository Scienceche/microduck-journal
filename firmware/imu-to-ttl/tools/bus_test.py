#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首板总线测试脚本 —— imu_to_dxl / OSHWhub "imu_to_servo" 板（固件：本仓库 g031-dxl）。

接线（USB-TTL 适配器，3.3 V 电平优先；5 V 也可，板上有缓冲）：

    适配器 TX ──┬────────────► 总线 DATA   (J1/J2 第 3 针，或 J4/J5 第 1 针)
    适配器 RX ──┘
    适配器 GND ───────────────► 总线 GND   (J1/J2 第 1 针，或 J4/J5 第 3 针)

    板子供电：VDD_BUS (J1/J2 第 2 针 / J4/J5 第 2 针) 接 6.0~8.4 V
    （可调电源限流 100 mA 最稳；也可以用 2S 电池 + 手不离插头）。

    TX/RX 并联到同一根 DATA 线是飞特半双工总线的标准调试接法：
    自己发的帧会回显进 RX，脚本会自动核对本机回显并剥离。

用法：
    python bus_test.py COM5                  # 默认 1 Mbps、ID 200：ping + 识别 + 循环读四元数
    python bus_test.py COM5 --count 100      # 只读 100 帧
    python bus_test.py COM5 --rate 50        # 50 Hz
    python bus_test.py COM5 --scan           # 扫 0~253 找 ID（固件默认 200 = 0xC8）
    python bus_test.py COM5 --port-echo off  # 适配器不回显时关掉剥离逻辑

判读：
    status.bit0 = IMU WHO_AM_I 应答 (0x70) —— 0 说明 SPI/IMU 焊接有问题
    status.bit1 = SFLP 姿态融合在跑
    四元数模长 |q| 应 ≈ 1；绕板法向转 90°，对应欧拉角应变化 ≈ 90°
"""

import argparse
import math
import struct
import sys
import time

try:
    import serial
except ImportError:
    sys.exit("需要 pyserial：pip install pyserial")


def ft_checksum(data: bytes) -> int:
    return (~sum(data)) & 0xFF


def build_frame(sid: int, inst: int, params: bytes = b"") -> bytes:
    length = len(params) + 2
    body = bytes([sid, length, inst]) + params
    return b"\xff\xff" + body + bytes([ft_checksum(body)])


class Bus:
    def __init__(self, port: str, baud: int, echo: bool):
        self.ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.005,
        )
        self.echo = echo
        self.timeouts = 0

    def _read_exact(self, n: int, deadline: float) -> bytes:
        buf = b""
        while len(buf) < n and time.monotonic() < deadline:
            chunk = self.ser.read(n - len(buf))
            if chunk:
                buf += chunk
        return buf

    def transact(self, sid: int, inst: int, params: bytes = b"") -> tuple[int, bytes]:
        """发一帧，剥回显，收应答。返回 (err, params)。"""
        frame = build_frame(sid, inst, params)
        self.ser.reset_input_buffer()
        self.ser.write(frame)
        self.ser.flush()
        deadline = time.monotonic() + 0.1

        if self.echo:
            echo = self._read_exact(len(frame), time.monotonic() + 0.02)
            if echo != frame:
                # 没收到或对不上：可能适配器不回显，把已收到的留给应答解析
                self.ser.timeout = 0.005
                if echo:
                    self.ser.timeout = 0  # 无法退回，直接在解析里容错
                # 回显长度不匹配时，把已读字节并入待解析流
                pending = echo
            else:
                pending = b""
        else:
            pending = b""

        data = pending
        # 在缓冲里找包头 FF FF（任意位置；应答帧可能已整个被回显窗口读进来）
        while b"\xff\xff" not in data:
            b = self._read_exact(1, deadline)
            if not b:
                self.timeouts += 1
                raise TimeoutError(f"无应答（已等 100 ms），累计超时 {self.timeouts}")
            data += b
        data = data[data.index(b"\xff\xff"):]
        # 补齐 FF FF ID LEN
        while len(data) < 4:
            b = self._read_exact(1, deadline)
            if not b:
                raise TimeoutError("包头不完整")
            data += b
        rid, rlen = data[2], data[3]
        # 补齐整帧（ID LEN + LEN 字节应答体）
        while len(data) < 4 + rlen:
            b = self._read_exact(1, deadline)
            if not b:
                raise TimeoutError(f"应答不完整 LEN={rlen} 只收到 {len(data) - 4}")
            data += b
        body = data[2:4 + rlen]
        rest = body[2:]
        if ft_checksum(body) != 0:
            raise ValueError(f"校验错: {body.hex()}")
        err = rest[0]
        return err, rest[1:-1]

    def ping(self, sid: int) -> bool:
        try:
            self.transact(sid, 0x01)
            return True
        except (TimeoutError, ValueError):
            return False

    def read(self, sid: int, addr: int, count: int) -> bytes:
        err, params = self.transact(sid, 0x02, bytes([addr, count]))
        if err != 0:
            raise ValueError(f"从机报错 err={err}")
        if len(params) != count:
            raise ValueError(f"长度不符: 要 {count} 得 {len(params)}")
        return params


def quat_to_euler(x: float, y: float, z: float, w: float) -> tuple[float, float, float]:
    """ZYX 欧拉角（偏航/俯仰/横滚），单位度。"""
    sinr_cosp = 2 * (w * x + y * z)
    cosr_cosp = 1 - 2 * (x * x + y * y)
    roll = math.atan2(sinr_cosp, cosr_cosp)
    sinp = 2 * (w * y - z * x)
    sinp = max(-1.0, min(1.0, sinp))
    pitch = math.asin(sinp)
    siny_cosp = 2 * (w * z + x * y)
    cosy_cosp = 1 - 2 * (y * y + z * z)
    yaw = math.atan2(siny_cosp, cosy_cosp)
    return (math.degrees(roll), math.degrees(pitch), math.degrees(yaw))


def main() -> None:
    ap = argparse.ArgumentParser(description="imu_to_dxl 首板总线测试")
    ap.add_argument("port", help="串口，如 COM5 或 /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=1000000)
    ap.add_argument("--id", type=int, default=200, help="从机 ID（固件默认 200）")
    ap.add_argument("--rate", type=float, default=10.0, help="四元数读取频率 Hz")
    ap.add_argument("--count", type=int, default=0, help="读多少帧后退出（0=一直）")
    ap.add_argument("--scan", action="store_true", help="扫总线找 ID 后退出")
    ap.add_argument("--port-echo", choices=["on", "off"], default="on",
                    help="适配器是否回显自己发的帧（TX/RX 并联时=on）")
    args = ap.parse_args()

    bus = Bus(args.port, args.baud, args.port_echo == "on")

    if args.scan:
        print(f"扫描 0~253 @ {args.baud} bps ...")
        found = []
        for sid in range(254):
            if bus.ping(sid):
                found.append(sid)
                print(f"  ID {sid} (0x{sid:02X}) 应答")
        print("扫描结束。" + (f"发现: {found}" if found else "什么都没发现。"))
        return

    if not bus.ping(args.id):
        sys.exit(f"[FAIL] ID {args.id} ping 无应答 —— 查供电/接线/波特率/固件")
    print(f"[OK] ping ID {args.id} @ {args.baud} bps")

    ident = bus.read(args.id, 0, 9)
    fw_major, fw_minor = ident[0], ident[1]
    rid, baud_code = ident[5], ident[6]
    print(f"[OK] 固件 v{fw_major}.{fw_minor}  ID 寄存器={rid}  波特率码={baud_code} (0=1M)")
    if rid != args.id:
        print(f"[WARN] ID 寄存器 {rid} != {args.id}，后面按 ID 寄存器实际值通信")

    diag = bus.read(args.id, 136, 8)
    sample_count = struct.unpack_from("<H", diag, 0)[0]
    status = diag[2]
    ax, ay = struct.unpack_from("<2h", diag, 4)
    print(f"IMU 状态字节 = 0x{status:02X}: "
          f"bit0 WHO_AM_I {'OK' if status & 1 else '!!FAIL!!'}, "
          f"bit1 SFLP 融合 {'RUNNING' if status & 2 else '!!STOPPED!!'}")
    print(f"采样计数 = {sample_count}  accel_raw = ({ax}, {ay})")

    if (status & 3) != 3:
        print("[WARN] IMU 侧异常，仍继续读 —— 若四元数恒为 0，先查 U2 焊接（LGA 底部焊盘需热风）")

    period = 1.0 / args.rate
    n = 0
    err_streak = 0
    print(f"\n开始循环读四元数 @ {args.rate} Hz（Ctrl+C 退出）")
    print("帧号 | 陀螺 x/y/z (dps) | 四元数 x y z w | |q| | 欧拉 R/P/Y (deg)")
    t_next = time.monotonic()
    try:
        while True:
            try:
                blk = bus.read(args.id, 124, 12)
                gx, gy, gz = struct.unpack_from("<3h", blk, 0)
                qx, qy, qz = struct.unpack_from("<3e", blk, 6)
                w = math.sqrt(max(0.0, 1.0 - (qx * qx + qy * qy + qz * qz)))
                mod = math.sqrt(qx * qx + qy * qy + qz * qz + w * w)
                dps = 500.0 / 32768.0
                roll, pitch, yaw = quat_to_euler(qx, qy, qz, w)
                flag = "" if abs(mod - 1.0) < 0.05 else "  <-- |q| 偏离 1"
                n += 1
                err_streak = 0
                print(f"{n:5d} | {gx*dps:7.1f} {gy*dps:7.1f} {gz*dps:7.1f} | "
                      f"{qx:+.3f} {qy:+.3f} {qz:+.3f} {w:+.3f} | {mod:.3f} | "
                      f"{roll:7.1f} {pitch:6.1f} {yaw:7.1f}{flag}")
            except (TimeoutError, ValueError) as e:
                err_streak += 1
                print(f"[ERR] 第 {err_streak} 次连续失败: {e}")
                if err_streak >= 10:
                    sys.exit("连续 10 次失败，退出 —— 查接线/共地/上拉")
            if args.count and n >= args.count:
                print(f"读完 {n} 帧，退出。")
                break
            t_next += period
            delay = t_next - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                t_next = time.monotonic()
    except KeyboardInterrupt:
        print(f"\n结束。共 {n} 帧，累计超时 {bus.timeouts} 次。")


if __name__ == "__main__":
    main()
