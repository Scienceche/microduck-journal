#!/bin/bash
# 秒速配对：scan 短暂 → pair → trust → connect → HID
ADDR=99:16:56:C3:54:4F
HID=00001124-0000-1000-8000-00805f9b34fb

btmgmt bondable on >/dev/null 2>&1
hciconfig hci0 piscan 2>/dev/null

# 3秒扫描让设备进数据库
timeout 3 bluetoothctl scan on >/dev/null 2>&1

# 立刻配对+信任+连接+HID
R1=$(bluetoothctl pair $ADDR 2>&1 | tail -1)
R2=$(bluetoothctl trust $ADDR 2>&1 | tail -1)
R3=$(bluetoothctl connect $ADDR 2>&1 | tail -1)
R4=$(bluetoothctl connect $ADDR $HID 2>&1 | tail -1)

echo "pair: $R1"
echo "trust: $R2"
echo "connect: $R3"
echo "hid: $R4"
bluetoothctl info $ADDR | grep -E 'Name|Paired|Connected'
sleep 3
echo "events: $(ls /dev/input/event* | wc -l)"
cat /proc/bus/input/devices | grep 'Name' | tail -3
