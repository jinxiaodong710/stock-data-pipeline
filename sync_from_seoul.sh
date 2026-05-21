#!/bin/bash
# sync_from_seoul.sh — 从首尔同步日线数据库到 Mac
# 在首尔日线采集完成后运行（首尔 18:47 ≈ 北京 17:47）

set -e

SEOL_HOST="seoul"
SRC_PATH="/home/ubuntu/go/data/stock_data.duckdb"
DST_PATH="/Users/jin/go/data/stock_data.duckdb"
TMP_PATH="${DST_PATH}.tmp"
LOG_PATH="/tmp/seoul_sync.log"

echo "$(date '+%Y-%m-%d %H:%M:%S') 开始从首尔同步..." >> "$LOG_PATH"

# 1. 检查首尔文件是否新鲜（30分钟内修改过）
FRESH=$(ssh -o ConnectTimeout=5 "$SEOL_HOST" "find $SRC_PATH -mmin -60 2>/dev/null | wc -l" 2>/dev/null || echo "0")
if [ "$FRESH" -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') ⚠️ 首尔文件超过60分钟未更新，跳过同步" >> "$LOG_PATH"
    exit 0
fi

# 2. 获取首尔文件大小
SRC_SIZE=$(ssh -o ConnectTimeout=5 "$SEOL_HOST" "stat -c%s $SRC_PATH" 2>/dev/null || echo "0")
echo "$(date '+%Y-%m-%d %H:%M:%S') 首尔文件大小: ${SRC_SIZE} bytes" >> "$LOG_PATH"

# 3. SCP 到临时文件
scp -o ConnectTimeout=30 "$SEOL_HOST:$SRC_PATH" "$TMP_PATH" 2>> "$LOG_PATH"

# 4. 校验大小
TMP_SIZE=$(stat -f%z "$TMP_PATH" 2>/dev/null || echo "0")
echo "$(date '+%Y-%m-%d %H:%M:%S') 本地临时文件: ${TMP_SIZE} bytes" >> "$LOG_PATH"

if [ "$SRC_SIZE" -gt 100000000 ] && [ "$TMP_SIZE" = "$SRC_SIZE" ]; then
    # 5. 原子替换
    mv "$TMP_PATH" "$DST_PATH"
    echo "$(date '+%Y-%m-%d %H:%M:%S') ✅ 同步完成 ($(du -h "$DST_PATH" | cut -f1))" >> "$LOG_PATH"
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') ❌ 大小校验失败！首尔:${SRC_SIZE} 本地:${TMP_SIZE}" >> "$LOG_PATH"
    rm -f "$TMP_PATH"
    exit 1
fi
