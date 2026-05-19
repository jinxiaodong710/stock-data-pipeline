#!/bin/bash
# Mac 本地健康检查 — 小五审查修复版
set -o pipefail

export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"

# 预检查
if ! command -v docker &>/dev/null; then
  TZ=Asia/Shanghai date "+🩺 Mac %H:%M"
  echo "  ❌ Docker 命令不存在"
  exit 1
fi

# Docker 容器状态（10秒超时防卡死）
CT=$(timeout 10 docker compose -f ~/go/docker-compose.yml ps 2>/dev/null || echo "")
REC=$(echo "$CT" | grep -E "receiver.*Up" | wc -l | tr -d ' ')
WRI=$(echo "$CT" | grep -E "writer.*Up" | wc -l | tr -d ' ')
RDS=$(echo "$CT" | grep -E "redis.*healthy" | wc -l | tr -d ' ')

# snapshot 进程（精确匹配脚本名）
SNAPSHOT=$(ps aux | grep '[s]napshot_loop\.sh' | wc -l | tr -d ' ')

[ "$REC" -ge 1 ] && R="✅" || R="❌"
[ "$WRI" -ge 1 ] && W="✅" || W="❌"
[ "$RDS" -ge 1 ] && RD="✅" || RD="❌"
[ "$SNAPSHOT" -ge 1 ] && S="✅" || S="❌"

TZ=Asia/Shanghai date "+🩺 Mac %H:%M"
echo "  receiver:$R writer:$W redis:$RD snapshot:$S"

if [ "$R" = "❌" ] || [ "$W" = "❌" ] || [ "$RD" = "❌" ] || [ "$S" = "❌" ]; then
  if [ -x /opt/homebrew/bin/openclaw ]; then
    /opt/homebrew/bin/openclaw message send \
      --channel openclaw-weixin \
      --target 'o9cq800mXY4thLpF87zbMEtKIrsg@im.wechat' \
      --message "⚠️ Mac报警 | receiver:$R writer:$W redis:$RD snapshot:$S" 2>/dev/null
  fi
fi
