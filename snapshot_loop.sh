#!/bin/bash
# 30秒快照循环 - 仅在交易时段运行
# 北京时间: 9:15-11:30, 13:00-15:05

export DYLD_LIBRARY_PATH="/opt/homebrew/opt/expat/lib:$DYLD_LIBRARY_PATH"
PYTHON=/opt/homebrew/bin/python3.13
SCRIPT=/Users/jin/go/snapshot_sender.py
LOG=/tmp/stock_services/snapshot.log

while true; do
    # 用北京时间判断
    HOUR=$(TZ='Asia/Shanghai' date +%H)
    MIN=$(TZ='Asia/Shanghai' date +%M)
    NOW=$((10#$HOUR * 60 + 10#$MIN))
    
    MORNING_START=$((9*60+15))
    MORNING_END=$((11*60+30))
    AFTER_START=$((13*60))
    AFTER_END=$((15*60+5))
    
    if ([ $NOW -ge $MORNING_START ] && [ $NOW -le $MORNING_END ]) || \
       ([ $NOW -ge $AFTER_START ] && [ $NOW -le $AFTER_END ]); then
        $PYTHON -u "$SCRIPT" >> "$LOG" 2>&1
    fi
    
    sleep 30
done
