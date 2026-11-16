#!/usr/bin/env bash
# 每日同步：工作目录 → 本仓库 → GitHub（私人仓库）
# 用法: bash sync.sh   （可手动跑，也被每日定时任务调用）
set -u
REPO="$(cd "$(dirname "$0")" && pwd)"
SRC="E:/c1349/复刻microduck"

cd "$REPO"

# 0) git 身份（首次）
git config user.name  >/dev/null 2>&1 || git config user.name  "duck-builder"
git config user.email >/dev/null 2>&1 || git config user.email "duck-builder@users.noreply.github.com"

# 1) 镜像各分区（cp + 清理排除项；robocopy 有中文路径坑，不用）
sync_tree() {  # $1=源目录 $2=仓库内相对路径 $3...=排除项
    local src="$1" rel="$2"; shift 2
    rm -rf "$REPO/$rel"
    cp -r "$src" "$REPO/$rel" || { echo "复制失败: $src"; return 1; }
    local x; for x in "$@"; do rm -rf "$REPO/$rel/$x"; done
}
sync_tree "$SRC/硬件资料/imu-to-ttl/firmware" "firmware/imu-to-ttl" build __pycache__
sync_tree "$SRC/硬件资料/imu-to-ttl/hardware" "hardware/imu-to-ttl" analysis
sync_tree "$SRC/中控页面"                     "panel"                __pycache__
# 源码备份（2026-11-15 加：固件热修 + 训练代码 + OTA/黑匣子工具）
sync_tree "C:/duck_work/kissqy-firmware/robotd/src"       "firmware/scienceche/robotd-src"       __pycache__
sync_tree "C:/duck_work/kissqy-firmware/duck-control/src" "firmware/scienceche/duck-control-src" __pycache__

# 训练代码：全部修改过的配置 + XML 限位 + checkpoint 脚本
mkdir -p training/mjlab_microduck
for f in dual_expert_mlp.py microduck_velstand_env_cfg.py microduck_velocity_env_cfg.py \
         microduck_standup_env_cfg.py mdp.py; do
    cp -f "C:/duck_work/microduck_rl/src/mjlab_microduck/tasks/$f" training/mjlab_microduck/ 2>/dev/null
done
cp -f "C:/duck_work/microduck_rl/src/mjlab_microduck/robot/microduck_constants.py" training/mjlab_microduck/ 2>/dev/null
for f in robot_allcollisions.xml robot_groundcontact_backlash.xml; do
    cp -f "C:/duck_work/microduck_rl/src/mjlab_microduck/robot/microduck/$f" training/mjlab_microduck/ 2>/dev/null
done
for f in make_p2_checkpoint.py make_p25_checkpoint.py make_p26_checkpoint.py \
         make_p27_checkpoint.py make_p28_checkpoint.py export_run11.py; do
    [ -f "C:/duck_work/microduck_rl/$f" ] && cp -f "C:/duck_work/microduck_rl/$f" training/mjlab_microduck/
done

# OTA + 黑匣子 + 蓝牙 + 中控台
mkdir -p tools/ota tools/blackbox tools/eval tools/patches tools/tests
cp -f C:/duck_work/ota/*.py C:/duck_work/ota/*.sh C:/duck_work/ota/*.service tools/ota/ 2>/dev/null
cp -f C:/duck_work/ota/duck-blackbox.service tools/blackbox/ 2>/dev/null
# 评测/诊断/监控
for f in eval_walk.py eval_getup.py eval_battery_run10.py eval_walk_ckpt.py \
         diagnose_run10.py diagnose_compose.py diagnose_obs_drift.py \
         bt_watchdog.py stand_monitor.py getup_watch.py watch_status.py \
         expand_limits.py fix_p2_test.py export_run11.py package_run11.py package_p28.py; do
    [ -f "C:/duck_work/$f" ] && cp -f "C:/duck_work/$f" tools/eval/
done
# 固件补丁
for f in patch_mod4096.py patch_torque_hold.py patch_held_ids.py \
         patch_stuck_recovery.py patch_stuck_fix.py patch_run12_init.py patch_p2_cfg.py \
         patch_hf8_fix.py patch_hf8_fix2.py patch_hf8_fix3.py; do
    [ -f "C:/duck_work/$f" ] && cp -f "C:/duck_work/$f" tools/patches/
done
# 测试
for f in test_collision.py test_bridge_init.py test_p25_model.py test_p26_model.py \
         test_p27_model.py test_dual_expert.py; do
    [ -f "C:/duck_work/$f" ] && cp -f "C:/duck_work/$f" tools/tests/
done

mkdir -p docs && cp -f "$SRC/硬件资料/imu-to-ttl/docs/"*.md docs/ 2>/dev/null
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
find . -name "*.pyc" -delete 2>/dev/null

# 2) 提交 + 推送
git add -A
if git diff --cached --quiet; then
    echo "[sync] $(date '+%F %T') 无变更"
else
    git commit -m "sync: $(date +%F) 日常更新" >/dev/null
    if git remote | grep -q origin; then
        if git push origin HEAD; then
            echo "[sync] $(date '+%F %T') 已推送到 GitHub ✓"
        else
            echo "[sync] 推送失败（网络/凭据），本地提交已保存"
            exit 1
        fi
    else
        echo "[sync] 未配置远程 origin（本地提交已保存）"
    fi
fi
