#!/usr/bin/env python3.13
# -*- coding: utf-8 -*-
"""
增量采集：只拉最近 N 天数据，不删旧数据，不膨胀数据库
"""

import os, sys, time, queue, threading
os.environ.setdefault('DYLD_LIBRARY_PATH', '/opt/homebrew/opt/expat/lib')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import duckdb
from stock_data_collector import (
    fetch_with_retry, _fetch_and_process_stock_hist_ts,
    _collect_st_history_chunk, INDEX_CODES_TS,
    pro, TusharePlaceholder
)
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import random

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'stock_data.duckdb')
DAYS_BACK = 3  # 拉最近3天(263只×3=789<1000，不超API限制)

class FakeLock:
    def lockForRead(self): pass
    def lockForWrite(self): pass
    def unlock(self): pass

def main():
    conn = duckdb.connect(DB_PATH)
    
    # 确保表存在
    conn.execute("""
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
    conn.execute("""
    CREATE TABLE IF NOT EXISTS index_prices (
        index_code TEXT, date TEXT,
        open REAL, high REAL, low REAL, close REAL,
        volume BIGINT, amount REAL,
        PRIMARY KEY (index_code, date)
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS exp_trade (
        exchange TEXT, is_open INTEGER, date TEXT, pretrade_date1 TEXT,
        PRIMARY KEY (exchange, date)
    )
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS stock_st_history (
        trade_date TEXT, ts_code TEXT, name TEXT,
        PRIMARY KEY (trade_date, ts_code)
    )
    """)
    conn.commit()
    
    # 获取日期范围
    end_date = pd.Timestamp.now().strftime('%Y-%m-%d')
    start_date = (pd.Timestamp.now() - pd.Timedelta(days=DAYS_BACK)).strftime('%Y-%m-%d')
    start_str_ts = start_date.replace('-', '')
    end_str_ts = end_date.replace('-', '')
    
    print(f"增量采集: {start_date} ~ {end_date}")
    
    # 更新交易日历
    try:
        calendar_df = fetch_with_retry(pro.trade_cal, exchange='', start_date=start_str_ts, end_date=end_str_ts)
        if calendar_df is not None and not calendar_df.empty:
            calendar_df['date'] = pd.to_datetime(calendar_df['cal_date']).dt.strftime('%Y-%m-%d')
            calendar_df['pretrade_date1'] = pd.to_datetime(calendar_df['pretrade_date']).dt.strftime('%Y-%m-%d')
            calendar_df['exchange'] = 'CNS'
            data_tuples = calendar_df[['exchange', 'is_open', 'date', 'pretrade_date1']].values.tolist()
            conn.executemany("INSERT OR REPLACE INTO exp_trade VALUES (?, ?, ?, ?)", data_tuples)
            conn.commit()
            print(f"交易日历更新: {len(data_tuples)} 天")
    except Exception as e:
        print(f"日历更新失败: {e}")
    
    # 更新 ST 历史 (需要 queue 而非 None)
    st_q = queue.Queue()
    st_t = threading.Thread(target=lambda: [st_q.get() for _ in iter(st_q.get, None)], daemon=True)
    st_t.start()
    _collect_st_history_chunk(conn, FakeLock(), start_str_ts, end_str_ts, st_q, threading.Event())
    st_q.put(None)
    
    # 获取股票列表
    stock_df = fetch_with_retry(pro.stock_basic, exchange='', list_status='L')
    if stock_df is None or stock_df.empty:
        print("获取股票列表失败")
        conn.close()
        return
    
    stock_df = stock_df[stock_df['ts_code'].str.match(r'^(00|30|60|68)\d{4}\.(SH|SZ)$')]
    stock_codes = stock_df['ts_code'].tolist()
    print(f"股票: {len(stock_codes)} 只")
    
    # 增量采集（只拉最近数据，3 线程并发）
    result_queue = queue.Queue()
    success = 0
    failed = 0
    
    print(f"开始增量采集...")
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(_fetch_and_process_stock_hist_ts, (code, start_str_ts, end_str_ts, result_queue)): code 
                   for code in stock_codes}
        total = len(stock_codes)
        
        for i, future in enumerate(as_completed(futures)):
            code = futures[future]
            try:
                res_code, df = future.result()
                if df is not None and not df.empty:
                    # INSERT OR REPLACE — 有就覆盖，没有就插入，不删旧数据
                    cols_str = ', '.join([f'"{c}"' for c in df.columns])
                    conn.execute(f"INSERT OR REPLACE INTO stock_prices ({cols_str}) SELECT * FROM df")
                    conn.commit()
                    success += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
            
            if i % 100 == 0:
                print(f"  进度: {i}/{total} (成功:{success})")
            time.sleep(random.uniform(0.05, 0.1))
    
    print(f"\n个股: 成功 {success}, 失败 {failed}")
    
    # 指数增量
    indices = ["上证指数", "深证成指", "创业板指", "微盘股指"]
    for name in indices:
        code = INDEX_CODES_TS.get(name)
        if not code:
            continue
        try:
            df = fetch_with_retry(
                lambda: pro.ts_bar(ts_code=code, asset='I', start_date=start_str_ts, end_date=end_str_ts),
                max_retries=2
            )
            if df is not None and not df.empty:
                df_clean = pd.DataFrame({
                    'index_code': df['ts_code'].str.split('.').str[0],
                    'date': pd.to_datetime(df['trade_date'], format='%Y%m%d').dt.strftime('%Y-%m-%d'),
                    'open': df['open'], 'high': df['high'], 'low': df['low'], 'close': df['close'],
                    'volume': (pd.to_numeric(df['vol'], errors='coerce') * 100).fillna(0).astype('int64'),
                    'amount': pd.to_numeric(df['amount'], errors='coerce').fillna(0) * 1000
                })
                cols = ', '.join([f'"{c}"' for c in df_clean.columns])
                conn.execute(f"INSERT OR REPLACE INTO index_prices ({cols}) SELECT * FROM df_clean")
                conn.commit()
                print(f"指数 {name}: {len(df_clean)} 条")
        except Exception as e:
            print(f"指数 {name} 失败: {e}")
    
    # 确认数据库大小
    import pathlib
    size_mb = pathlib.Path(DB_PATH).stat().st_size / 1024 / 1024
    print(f"\n数据库: {size_mb:.0f}MB")
    conn.close()
    print("增量采集完成 ✅")

if __name__ == "__main__":
    main()
