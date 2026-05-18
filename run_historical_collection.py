#!/usr/bin/env python3.13
# -*- coding: utf-8 -*-
"""
Headless runner for historical data collection
Usage: python3.13 run_historical_collection.py
"""

import os, sys, time, queue, threading

# Fix expat on macOS
os.environ.setdefault('DYLD_LIBRARY_PATH', '/opt/homebrew/opt/expat/lib')

# Add current dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import duckdb
from stock_data_collector import collect_historical_data_stock_by_stock

# Config
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'stock_data.duckdb')
START_DATE = '2020-01-01'
END_DATE = '2026-05-15'
INDICES = ["上证指数", "深证成指", "创业板指", "微盘股指"]

# Fake lock (no PySide6 needed)
class FakeLock:
    def lockForRead(self): pass
    def lockForWrite(self): pass
    def unlock(self): pass

print(f"数据库路径: {DB_PATH}")
print(f"日期范围: {START_DATE} ~ {END_DATE}")
print(f"指数列表: {INDICES}")
print("=" * 60)

# Create DB and tables
conn = duckdb.connect(DB_PATH)
cursor = conn.cursor()

# Create tables (same as in 采集3duckdb_副本3.py)
cursor.execute("""
CREATE TABLE IF NOT EXISTS stock_prices (
    stock_code TEXT, date TEXT,
    open REAL, high REAL, low REAL, close REAL,
    adj_open REAL, adj_close REAL,
    volume BIGINT, amount REAL, amplitude REAL,
    ak_change_pct REAL, ak_change_amount REAL,
    turnover_rate REAL, turnover_rate_f REAL, volume_ratio REAL,
    pe REAL, pe_ttm REAL, pb REAL, ps REAL, ps_ttm REAL,
    dv_ratio REAL, dv_ttm REAL,
    total_share REAL, float_share REAL, free_share REAL,
    total_mv REAL, circ_mv REAL, turnover REAL, limit_status REAL,
    PRIMARY KEY (stock_code, date)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS index_prices (
    index_code TEXT, date TEXT,
    open REAL, high REAL, low REAL, close REAL,
    volume BIGINT, amount REAL,
    PRIMARY KEY (index_code, date)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS exp_trade (
    exchange TEXT, is_open INTEGER, date TEXT, pretrade_date1 TEXT,
    PRIMARY KEY (exchange, date)
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS stock_st_history (
    trade_date TEXT, ts_code TEXT, name TEXT,
    PRIMARY KEY (trade_date, ts_code)
)
""")

conn.commit()
print("数据库表结构就绪。")

# Result queue and stop event
result_queue = queue.Queue()
stop_event = threading.Event()
db_lock = FakeLock()

# Progress monitoring thread
def monitor_progress():
    while True:
        try:
            item = result_queue.get(timeout=5)
            if isinstance(item, tuple):
                msg_type = item[0]
                if msg_type == 'collector_log':
                    data = item[1]
                    print(f"[{data.get('tag','info').upper()}] {data.get('msg','')}")
                elif msg_type == 'collector_progress':
                    val = item[1]
                    if val >= 0:
                        print(f"  📊 进度: {val}%")
                elif msg_type == 'collector_status':
                    data = item[1]
                    print(f"  🔄 状态: {data.get('text','')}")
                elif msg_type == 'collector_complete':
                    data = item[1]
                    print(f"\n{'='*60}")
                    print(f"✅ 采集完成! 状态: {data.get('status','未知')}")
                    failed = data.get('failed_items', {})
                    if failed:
                        print(f"失败项: {failed}")
                    break
        except queue.Empty:
            continue
        except Exception:
            break

monitor_thread = threading.Thread(target=monitor_progress, daemon=True)
monitor_thread.start()

print(f"\n开始采集 {START_DATE} 至 {END_DATE} 的历史数据...\n")

try:
    collect_historical_data_stock_by_stock(
        DB_PATH, conn, db_lock,
        START_DATE, END_DATE,
        INDICES,
        result_queue, stop_event
    )
finally:
    conn.commit()
    conn.close()
    print("数据库已关闭。")
