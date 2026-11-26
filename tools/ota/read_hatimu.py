#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读取头部 HAT 板上的姿态传感器(LSM6DSV16XTR @ 0x6A)——一次性探测, 2026-11-26。

背景: HAT(elec_RPI_Robot_HAT, KiCad: U12=LSM6DSV16XTR, I2C 0x6A, 挂 RPi I2C1/GPIO2-3,
与音频编解码器 TLV320AIC3104(0x18)、HAT 身份 EEPROM(0x50) 同总线)。robotd 没有它的
驱动、无人读它——本脚本是一次性只读验证 + 短暂使能采样, 结束时恢复上电默认(power-down)。

寄存器常数(全部出自本机数据手册 docs/part - lsm6dsv16x.pdf, 非记忆):
  WHO_AM_I (0Fh) 固定 70h — "This is a read-only register. Its value is fixed at 70h."
  CTRL1 (10h): [7:4]=ODR_XL, [2:0]=OP_MODE_XL(000=高性能, 默认); 表52: 0110=120Hz
  CTRL2 (11h): 同构, 陀螺
  OUT_TEMP_L (20h), OUTX_L_G (22h), OUTX_L_A (28h); 高字节紧随, 小端 int16
  上电默认量程: 加速度 ±2g → 0.061 mg/LSB(静止时 |a| 应≈1.000g, 这本身校验读数正确性)

安全性: 只对 0x6A 这颗"无人使用"的传感器写 CTRL1/CTRL2 两个字节(使能采样), 读完立刻写回
0x00(power-down=上电默认)并读回确认。不碰总线上其它器件(0x18/0x50 只做无副作用读应答探测),
不碰机器人任何其它部分。
用法(在鸭子上): python3 read_hatimu.py
"""
import fcntl
import glob
import math
import os
import struct
import time

I2C_SLAVE = 0x0703
MARKERS = [0x6A, 0x6B, 0x18, 0x50]   # IMU(SA0 两档) / 音频编解码器 / HAT EEPROM


def probe(bus_path, addr):
    """只读探测: 有应答即在线。不做任何写操作。"""
    try:
        fd = os.open(bus_path, os.O_RDWR)
    except OSError:
        return None
    try:
        fcntl.ioctl(fd, I2C_SLAVE, addr)
        os.read(fd, 1)
        return fd
    except OSError:
        os.close(fd)
        return None


# ---- 1. 扫描: HAT 特征地址在哪个总线 ----
buses = sorted(glob.glob("/dev/i2c-*"), key=lambda p: int(p.rsplit("-", 1)[1]))
imu_fd = imu_bus = imu_addr = None
print("=== 扫描 HAT 特征地址(IMU 0x6A/0x6B, 编解码器 0x18, EEPROM 0x50) ===")
for path in buses:
    hits = []
    for a in MARKERS:
        if probe(path, a) is not None:
            hits.append("0x%02X" % a)
    if hits:
        print("  %-14s: %s" % (path, "  ".join(hits)))
    for a in (0x6A, 0x6B):
        fd = probe(path, a)
        if fd is not None:
            imu_fd, imu_bus, imu_addr = fd, path, a
            break
    if imu_fd is not None:
        break

if imu_fd is None:
    print("RESULT: not_found — 没有任何总线应答 0x6A/0x6B")
    raise SystemExit(0)

print("=== IMU 在 %s @ 0x%02X ===" % (imu_bus, imu_addr))
fd = imu_fd


def rr(reg, n=1):
    os.write(fd, bytes([reg]))
    return os.read(fd, n)


def wr(reg, val):
    os.write(fd, bytes([reg, val]))


# ---- 2. WHO_AM_I ----
who = rr(0x0F)[0]
print("WHO_AM_I (0Fh) = 0x%02X   [手册: LSM6DSV16X 固定 0x70]" % who)
if who != 0x70:
    print("RESULT: unexpected_chip (应答但身份不是 0x70)")
    wr(0x10, 0); wr(0x11, 0)
    os.close(fd)
    raise SystemExit(0)

# ---- 3. 短暂使能采样: 加速度+陀螺 120Hz 高性能(CTRL=0110_0000=0x60) ----
wr(0x10, 0x60)
wr(0x11, 0x60)
time.sleep(0.05)
print("CTRL1 读回=0x%02X CTRL2 读回=0x%02X  (0x60 = ODR 0110=120Hz, OP_MODE 000 高性能)" % (
    rr(0x10)[0], rr(0x11)[0]))

mags = []
print("=== 连续 12 帧原始数据(加速度 0x28 起 / 陀螺 0x22 起, int16 小端) ===")
for i in range(12):
    acc = struct.unpack("<3h", rr(0x28, 6))
    gyr = struct.unpack("<3h", rr(0x22, 6))
    traw = struct.unpack("<h", rr(0x20, 2))[0]
    g = math.sqrt(sum(x * x for x in acc)) * 0.061 / 1000.0
    mags.append(g)
    print("  %2d  acc=[%+6d %+6d %+6d] |a|=%.3fg   gyro=[%+6d %+6d %+6d]   temp_raw=%d" % (
        i, acc[0], acc[1], acc[2], g, gyr[0], gyr[1], gyr[2], traw))
    time.sleep(0.02)
print("|a| 12 帧均值 = %.3f g  (±2g 量程, 0.061mg/LSB; 静止应≈1.000 — 这校验读数正确)" % (
    sum(mags) / len(mags)))

# ---- 4. 恢复上电默认态 ----
wr(0x10, 0x00)
wr(0x11, 0x00)
print("恢复: CTRL1=0x%02X CTRL2=0x%02X (0x00 = power-down, 上电默认态)" % (rr(0x10)[0], rr(0x11)[0]))
os.close(fd)
print("RESULT: ok")
