#!/bin/bash
# 排行榜 + 板块数据完整输出
export DYLD_LIBRARY_PATH="/opt/homebrew/opt/expat/lib:$DYLD_LIBRARY_PATH"
cd ~/go
/opt/homebrew/bin/python3.13 stock_ranking.py
