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
