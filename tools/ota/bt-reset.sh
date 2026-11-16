#!/bin/bash
# AIC8800 蓝牙软恢复 — 按 microduck_yocto zero3-board.md 的正确顺序
# 注意: 不能 systemctl restart bluetooth (会让内核丢 hci0, 只能 reboot)
echo '[bt-reset] 1. rfkill 双清...'
rfkill block bluetooth; rfkill unblock bluetooth; rfkill unblock bluetooth
sleep 1
echo '[bt-reset] 2. 重新 hciattach...'
killall hciattach 2>/dev/null; sleep 0.5
hciattach -n -s 1500000 /dev/ttyS1 any 1500000 flow &
sleep 3
echo '[bt-reset] 3. 验证...'
if hciconfig hci0 2>/dev/null | grep -q 'UP'; then
    echo '[bt-reset] ✓ hci0 UP, 尝试连接手柄...'
    bluetoothctl connect 99:16:56:C3:54:4F 2>&1
    bluetoothctl info 99:16:56:C3:54:4F | grep Connected
else
    echo '[bt-reset] ✗ hci0 仍 DOWN — 芯片终态崩溃, 需断电'
    exit 1
fi
