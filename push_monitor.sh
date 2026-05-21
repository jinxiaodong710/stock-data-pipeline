#!/bin/bash
# 从首尔拉取监控报告，去重后推微信（仅10:00-11:30, 13:00-15:05 北京时间）

HOUR=$(TZ='Asia/Shanghai' date +%H)
MIN=$(TZ='Asia/Shanghai' date +%M)
NOW=$((10#$HOUR * 60 + 10#$MIN))
MORNING=$((10*60)); MORNING_END=$((11*60+30))
AFTER=$((13*60)); AFTER_END=$((15*60+5))
IN_SESSION=false
[ $NOW -ge $MORNING ] && [ $NOW -le $MORNING_END ] && IN_SESSION=true
[ $NOW -ge $AFTER ] && [ $NOW -le $AFTER_END ] && IN_SESSION=true
[ "$IN_SESSION" = "false" ] && exit 0

MSG=$(ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -i /Users/jin/.ssh/tencent_cloud ubuntu@43.155.197.236 "cat /tmp/market_monitor.txt 2>/dev/null" 2>/dev/null || true)

if [ -z "$MSG" ] || [ "$MSG" = "NO_DATA" ] || [ "$MSG" = "DB_LOCKED" ]; then
    exit 0
fi

HASH=$(echo "$MSG" | md5 2>/dev/null || echo "")
LASTFILE=/tmp/market_monitor_last.hash
LAST=$(cat "$LASTFILE" 2>/dev/null || true)
if [ "$HASH" = "$LAST" ] && [ -n "$HASH" ]; then
    exit 0
fi
echo "$HASH" > "$LASTFILE"

/opt/homebrew/bin/openclaw message send \
  --channel openclaw-weixin \
  --target 'o9cq800mXY4thLpF87zbMEtKIrsg@im.wechat' \
  --message "$MSG" 2>&1 || true
