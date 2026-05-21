#!/bin/bash
# 从首尔拉取监控报告，推送到微信
MSG=$(ssh -o ConnectTimeout=5 -i /Users/jin/.ssh/tencent_cloud ubuntu@43.155.197.236 'cat /tmp/market_monitor.txt 2>/dev/null' 2>/dev/null)

if [ -z "$MSG" ] || [ "$MSG" = "NO_DATA" ]; then
    exit 0
fi

# 对比上次，避免重复推送
HASH=$(echo "$MSG" | md5)
LASTFILE=/tmp/market_monitor_last.hash
LAST=$(cat "$LASTFILE" 2>/dev/null)
if [ "$HASH" = "$LAST" ]; then
    exit 0
fi
echo "$HASH" > "$LASTFILE"

# 推送到微信
echo "$MSG"
