#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读取头部 HAT 板上的姿态传感器 —— 实物是 BMI088, 不是本地更新版图纸的 LSM6DSV16X(2026-11-26 澄清)。

身份依据: fanhaoo375/microduck-replica 的官方 HAT BOM 逆向 —— U11 = BMI088("贴了但软件不用"),
          accel@0x19(或0x18, SA0接法而定) / gyro@0x68(或0x69); EEPROM CAT24C32 DNP 不贴。
          实测 i2c-10(hat_i2c_main): 0x18=codec(被占) 0x19=accel 0x68=gyro 0x29=ToF。
寄存器(BMI088 数据手册):
  accel CHIP_ID (0x00) = 0x1E; 上电 suspend, 读寄存器前要一次 dummy read; 唤醒: PWR_CTRL(0x7D)=0x04
  gyro  CHIP_ID (0x00) = 0x0E; 上电 suspend, 唤醒: RANGE(0x0F)=0x00(±2000dps) LPM1(0x11)=0x00(正常)
  accel 输出 (0x12 起小端 int16, ±3g 默认量程 → 3.0517578125e-5 g/LSB); 静止 |a|≈1.000g 自校验
  gyro  输出 (0x02 起小端 int16, ±2000dps → 1/16.384 dps/LSB)
安全性: 只对 0x19/0x68 两颗"官方软件不用"的传感器做唤醒-读-恢复 suspend; 不碰 0x18(codec
        正被音频用)/0x29(ToF 属 robotd)。读完恢复上电默认态并读回确认。
用法(在鸭子上): python3 read_hatimu.py
"""
import ctypes
import fcntl
import math
import os
import struct
import time

I2C_RDWR = 0x0707
I2C_M_RD = 0x0001
BUS = "/dev/i2c-10"
ACC = 0x19
GYR = 0x68

fd = os.open(BUS, os.O_RDWR)

class Msg(ctypes.Structure):
    _fields_ = [("addr", ctypes.c_uint16), ("flags", ctypes.c_uint16),
                ("len", ctypes.c_uint16), ("buf", ctypes.POINTER(ctypes.c_ubyte))]

class RdwrData(ctypes.Structure):
    _fields_ = [("msgs", ctypes.POINTER(Msg)), ("nmsgs", ctypes.c_uint32)]

def rd(addr, reg, n=1):
    wa = (ctypes.c_ubyte * 1)(reg)
    ra = (ctypes.c_ubyte * n)()
    m1 = Msg(addr, 0, 1, ctypes.cast(wa, ctypes.POINTER(ctypes.c_ubyte)))
    m2 = Msg(addr, I2C_M_RD, n, ctypes.cast(ra, ctypes.POINTER(ctypes.c_ubyte)))
    arr = (Msg * 2)(m1, m2)
    fcntl.ioctl(fd, I2C_RDWR, RdwrData(ctypes.cast(arr, ctypes.POINTER(Msg)), 2))
    return bytes(ra)

def wr(addr, reg, val):
    wa = (ctypes.c_ubyte * 2)(reg, val)
    m = Msg(addr, 0, 2, ctypes.cast(wa, ctypes.POINTER(ctypes.c_ubyte)))
    arr = (Msg * 1)(m)
    fcntl.ioctl(fd, I2C_RDWR, RdwrData(ctypes.cast(arr, ctypes.POINTER(Msg)), 1))

print("=== 头部 HAT 板载 BMI088(实物) ===")
# ---- accel @0x19: suspend 下第一次读可能无效, dummy read 后读 CHIP_ID ----
rd(ACC, 0x00)
acc_id = rd(ACC, 0x00)[0]
print("accel CHIP_ID = 0x%02X  (BMI088 = 0x1E)" % acc_id)
gyr_id = rd(GYR, 0x00)[0]
print("gyro  CHIP_ID = 0x%02X  (BMI088 = 0x0E)" % gyr_id)
if acc_id != 0x1E or gyr_id != 0x0E:
    print("RESULT: unexpected_chip — 身份不符, 不继续")
    os.close(fd)
    raise SystemExit(0)

# ---- 唤醒并采样 ----
wr(ACC, 0x7D, 0x04)            # PWR_CTRL = active
time.sleep(0.05)
wr(GYR, 0x0F, 0x00)            # RANGE = ±2000 dps
wr(GYR, 0x11, 0x00)            # LPM1 = normal mode
time.sleep(0.05)
print("唤醒: ACC_PWR_CTRL=0x%02X GYR_RANGE=0x%02X GYR_LPM1=0x%02X" % (
    rd(ACC, 0x7D)[0], rd(GYR, 0x0F)[0], rd(GYR, 0x11)[0]))

mags = []
print("=== 12 帧采样 ===")
for i in range(12):
    ax, ay, az = struct.unpack("<3h", rd(ACC, 0x12, 6))
    gx, gy, gz = struct.unpack("<3h", rd(GYR, 0x02, 6))
    a_mg = (ax, ay, az)
    g_dps = (gx / 16.384, gy / 16.384, gz / 16.384)
    mag = math.sqrt(sum(v * v for v in a_mg)) * 3.0517578125e-5
    mags.append(mag)
    print("  %2d acc=[%+6d %+6d %+6d] |a|=%.3fg   gyro=[%+7.2f %+7.2f %+7.2f] dps" % (
        i, ax, ay, az, mag, g_dps[0], g_dps[1], g_dps[2]))
    time.sleep(0.03)
print("|a| 均值 = %.3f g  (静止应≈1.000 — 读数正确性的自校验)" % (sum(mags) / len(mags)))

# ---- 恢复上电默认态(suspend) ----
wr(ACC, 0x7D, 0x00)
wr(GYR, 0x11, 0x03)            # LPM1 = suspend
print("恢复: ACC_PWR_CTRL=0x%02X GYR_LPM1=0x%02X (suspend=上电默认)" % (
    rd(ACC, 0x7D)[0], rd(GYR, 0x11)[0]))
os.close(fd)
print("RESULT: ok")
