#!/bin/bash
export DYLD_LIBRARY_PATH="/opt/homebrew/opt/expat/lib:$DYLD_LIBRARY_PATH"
pkill -f redis_data_receiver1.3 2>/dev/null
pkill -f writer_service_optimized 2>/dev/null
sleep 2
cd ~/go
nohup /opt/homebrew/bin/python3.13 -u redis_data_receiver1.3.py >> /tmp/receiver.log 2>&1 &
sleep 2
nohup /opt/homebrew/bin/python3.13 -u writer_service_optimized.py >> /tmp/writer.log 2>&1 &
echo "$(date): L1 started" >> /tmp/l1_startup.log
