#!/bin/bash
# 手柄自动重连看门狗 v3：修复 P0 hcitool 挂死 + 服务重启顺序 + 锁文件
LOG=/tmp/pad-reconnect.log
LOCK=/run/pad-reconnect.pid
MAC=99:16:56:C3:54:4F
HID=00001124-0000-1000-8000-00805f9b34fb
CYCLE=0

# 锁(防多实例)
if [ -f "$LOCK" ] && kill -0 "$(cat $LOCK)" 2>/dev/null; then
    exit 0
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

log(){
    # 日志限长(保留最后 200 行)
    if [ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -gt 500 ]; then
        tail -200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
    fi
    echo "$(date +%H:%M:%S) $1" >> "$LOG"
}

is_connected(){
    bluetoothctl info $MAC 2>/dev/null | grep -q "Connected: yes"
}

while true; do
    sleep 5

    if is_connected; then
        CYCLE=0
        continue
    fi

    CYCLE=$((CYCLE + 1))
    log "--- 第${CYCLE}轮 ---"

    # 第1-2轮: 轻量尝试
    if [ $CYCLE -le 2 ]; then
        hciconfig hci0 piscan 2>/dev/null
        btmgmt bondable on >/dev/null 2>&1
        # P0 修复: timeout 防挂死(AIC8800 已知会挂 30+秒)
        timeout 5 hcitool cc $MAC 2>/dev/null
        sleep 2
        if is_connected; then
            log "✓ 轻量重连成功"
            # P1 修复: 先 bridge 后 padd(padd 绑 bridge 的虚拟节点)
            systemctl restart gamesir-xboxd 2>/dev/null
            sleep 1
            systemctl restart padd 2>/dev/null
            CYCLE=0
        fi
        continue
    fi

    # 第3+轮: 电源循环清旧状态
    log "轻量失败, 适配器电源循环..."
    bluetoothctl power off >/dev/null 2>&1
    sleep 1
    bluetoothctl power on >/dev/null 2>&1
    sleep 2

    # P2 修复: 验证电源循环生效
    if ! hciconfig hci0 2>/dev/null | grep -q "UP"; then
        log "✗ 电源循环后 hci0 DOWN, 重试..."
        /usr/bin/aic-bluetooth >/dev/null 2>&1
        sleep 3
    fi

    btmgmt bondable on >/dev/null 2>&1
    hciconfig hci0 piscan 2>/dev/null

    # 等手柄入站连接(12秒)
    log "等手柄入站(12s)..."
    for i in $(seq 1 12); do
        sleep 1
        if is_connected; then
            log "✓ 重连成功, 连 HID..."
            timeout 8 bluetoothctl connect $MAC $HID >/dev/null 2>&1
            # P1 修复: 先 bridge(创建虚拟节点) → 后 padd(绑定虚拟节点)
            systemctl restart gamesir-xboxd 2>/dev/null
            sleep 2
            systemctl restart padd 2>/dev/null
            log "✓ bridge+padd 已重启"
            CYCLE=0
            break
        fi
    done

    # 5轮后重置
    if [ $CYCLE -ge 5 ]; then
        log "5轮未恢复, 重置计数"
        CYCLE=0
    fi
done
