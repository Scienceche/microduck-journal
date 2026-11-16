#!/bin/bash
# 蓝牙看门狗: 每 30s 探活, 卡死先软恢复(bt-reset.sh), 再失败自动重启 Zero
INTERVAL=30
MAX_SOFT_RETRIES=2
soft_count=0
while true; do
    sleep $INTERVAL
    # 探活: hci0 存在且能响应
    if ! hciconfig hci0 2>/dev/null | grep -q 'UP'; then
        echo "[$(date +%H:%M:%S)] hci0 DOWN!"
        soft_count=$((soft_count + 1))
        if [ $soft_count -le $MAX_SOFT_RETRIES ]; then
            echo "[$(date +%H:%M:%S)] 软恢复 attempt $soft_count..."
            /usr/local/bin/bt-reset.sh >> /tmp/bt-watchdog.log 2>&1
        else
            echo "[$(date +%H:%M:%S)] 软恢复无效, 重启 Zero..."
            logger -t bt-watchdog 'Bluetooth dead, rebooting Zero'
            reboot
        fi
    else
        soft_count=0
    fi
done
