#!/bin/bash
# Mac 本地健康检查
# 检查 Docker 容器 + 快照进程，异常时微信报警
# 运行: launchd 每30分钟

set -euo pipefail
LOG=/tmp/health_check.log
STOCK_DIR="$HOME/go"

# ── 预检查 ──
if ! command -v docker &>/dev/null; then
  echo "$(TZ=Asia/Shanghai date '+%H:%M') docker命令不存在" >> "$LOG"
  exit 1
fi
docker info &>/dev/null || { echo "$(TZ=Asia/Shanghai date '+%H:%M') docker引擎未启动" >> "$LOG"; exit 1; }

COMPOSE_FILE="$STOCK_DIR/docker-compose.yml"
[ -f "$COMPOSE_FILE" ] || { echo "$(TZ=Asia/Shanghai date '+%H:%M') compose文件不存在" >> "$LOG"; exit 1; }

# ── Docker 容器状态（用 json 格式，不靠文本解析）─
CT=$(docker compose -f "$COMPOSE_FILE" ps --format json 2>>"$LOG" || true)
REC=$(echo "$CT" | python3 -c "import sys,json; print(sum(1 for l in sys.stdin if l.strip() and json.loads(l).get('Service')=='receiver' and json.loads(l).get('State')=='running'))" 2>/dev/null || echo 0)
WRI=$(echo "$CT" | python3 -c "import sys,json; print(sum(1 for l in sys.stdin if l.strip() and json.loads(l).get('Service')=='writer' and json.loads(l).get('State')=='running'))" 2>/dev/null || echo 0)
RDS=$(echo "$CT" | python3 -c "import sys,json; print(sum(1 for l in sys.stdin if l.strip() and json.loads(l).get('Service')=='redis' and json.loads(l).get('State')=='running' and json.loads(l).get('Health')=='healthy'))" 2>/dev/null || echo 0)

# ── 快照进程 ──
SNAPSHOT=$(pgrep -f 'snapshot_loop\.sh' 2>/dev/null | wc -l | tr -d ' ')
SNAPSHOT=${SNAPSHOT:-0}

# ── 状态判断（数字逻辑，展示时才转换）─
receiver_ok=false; writer_ok=false; redis_ok=false; snapshot_ok=false
[ "${REC:-0}" -ge 1 ] && receiver_ok=true
[ "${WRI:-0}" -ge 1 ] && writer_ok=true
[ "${RDS:-0}" -ge 1 ] && redis_ok=true
[ "${SNAPSHOT:-0}" -ge 1 ] && snapshot_ok=true

R=$($receiver_ok && echo "✅" || echo "❌")
W=$($writer_ok && echo "✅" || echo "❌")
RD=$($redis_ok && echo "✅" || echo "❌")
S=$($snapshot_ok && echo "✅" || echo "❌")

TS=$(TZ=Asia/Shanghai date '+%H:%M')
echo "[$TS] receiver:$R writer:$W redis:$RD snapshot:$S" >> "$LOG"
echo "🩺 Mac $TS"
echo "  receiver:$R writer:$W redis:$RD snapshot:$S"

# ── 分级告警 ──
CRITICAL=false; WARNING=false
! $receiver_ok && CRITICAL=true
! $writer_ok && CRITICAL=true
! $redis_ok && WARNING=true
! $snapshot_ok && WARNING=true

if $CRITICAL || $WARNING; then
  LEVEL=$($CRITICAL && echo "CRITICAL" || echo "WARNING")
  MSG="⚠️ Mac[$LEVEL] | receiver:$R writer:$W redis:$RD snapshot:$S"
  
  if [ -x /opt/homebrew/bin/openclaw ]; then
    /opt/homebrew/bin/openclaw message send \
      --channel openclaw-weixin \
      --target 'o9cq800mXY4thLpF87zbMEtKIrsg@im.wechat' \
      --message "$MSG" 2>>"$LOG" || echo "[$TS] 推送失败" >> "$LOG"
  fi
fi

# 状态总结
if $receiver_ok && $writer_ok && $redis_ok && $snapshot_ok; then
  echo "  status: OK"
elif $CRITICAL; then
  echo "  status: CRITICAL"
else
  echo "  status: DEGRADED"
fi
