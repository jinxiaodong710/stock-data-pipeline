#!/bin/bash
pkill -f redis_data_receiver1.3 2>/dev/null
pkill -f writer_service_optimized 2>/dev/null
echo "$(date): L1 stopped" >> /tmp/l1_startup.log
