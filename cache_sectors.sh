#!/bin/bash
# 缓存板块排行数据到本地（从首尔拉）
# 盘中每5分钟刷新一次
ssh -o ConnectTimeout=10 seoul 'python3 /tmp/sector_rank.py' > /tmp/sector_cache.txt 2>/dev/null
