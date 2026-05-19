#!/bin/bash
# Mac 本地健康检查 - 看 Docker 容器

export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
CT=$(docker compose -f ~/go/docker-compose.yml ps 2>/dev/null)
REC=$(echo "$CT" | grep receiver | grep -c Up)
WRI=$(echo "$CT" | grep writer | grep -c Up)
RDS=$(echo "$CT" | grep redis | grep -c healthy)
SNAPSHOT=$(ps aux | grep snapshot_loop | grep -v grep | wc -l)
[ "$REC" -ge 1 ] && R="✅" || R="❌"
[ "$WRI" -ge 1 ] && W="✅" || W="❌"
[ "$RDS" -ge 1 ] && RD="✅" || RD="❌"
[ "$SNAPSHOT" -ge 1 ] && S="✅" || S="❌"

TZ=Asia/Shanghai date "+🩺 Mac %H:%M"
echo "  receiver:$R writer:$W redis:$RD snapshot:$S"

if [ "$R" = "❌" ] || [ "$W" = "❌" ] || [ "$RD" = "❌" ] || [ "$S" = "❌" ]; then
  /opt/homebrew/bin/openclaw message send \
    --channel openclaw-weixin \
    --target 'o9cq800mXY4thLpF87zbMEtKIrsg@im.wechat' \
    --message "⚠️ Mac报警 | receiver:$R writer:$W redis:$RD snapshot:$S" 2>/dev/null
fi
