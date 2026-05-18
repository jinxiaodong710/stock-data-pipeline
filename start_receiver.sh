#!/bin/bash
export DYLD_LIBRARY_PATH="/opt/homebrew/opt/expat/lib:$DYLD_LIBRARY_PATH"
cd ~/go
python3 ~/go/redis_data_receiver1.3.py
