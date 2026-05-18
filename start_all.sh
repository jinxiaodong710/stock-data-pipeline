#!/bin/bash
export DYLD_LIBRARY_PATH="/opt/homebrew/opt/expat/lib:$DYLD_LIBRARY_PATH"
LOG_DIR=/tmp/stock_services
mkdir -p "$LOG_DIR"

PYTHON=/opt/homebrew/bin/python3.13
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 启动行情数据服务..."

# 启动 receiver (填 Redis)
cd ~/go
$PYTHON -u ~/go/redis_data_receiver1.3.py >> "$LOG_DIR/receiver.log" 2>&1 &
RECEIVER_PID=$!
echo "receiver PID: $RECEIVER_PID"

sleep 2

# 启动 writer (Redis → DuckDB)
$PYTHON -u ~/go/writer_service_optimized.py >> "$LOG_DIR/writer.log" 2>&1 &
WRITER_PID=$!
echo "writer PID: $WRITER_PID"

echo "两个服务已启动"
wait
