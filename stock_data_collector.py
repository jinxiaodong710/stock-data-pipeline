# -*- coding: utf-8 -*-
# stock_data_collector.py (v20.8 ST历史数据增强版 - 完整修复版)
# [!!! 核心修复 !!!]
# 1. (v20.8) 新增：自动采集 ST 股票历史数据 (stock_st_history)。
# 2. (v20.7) 增加日志：明确报告每个指数采集到的行数。
# 3. (v20.6) 恢复被遗漏的“采集指数历史”功能。
# 4. (v20.5) 修复快照采集的 "Binder Error"。
# 5. (v20.4) 修复 "has no attribute 'insert'" 错误。
# 6. (v20.3) 修复 "cannot rollback" 错误。
# 7. (v20.2) 修复 "Duplicate key" 错误 (列序错配)。
# 8. (v20.1) 修复采集数据丢失的问题 (添加 conn.commit())。
# 9. (Fix) 修复文件截断导致的 collect_fundamentals_data 语法错误。

import duckdb
import pandas as pd
import numpy as np
import time
import random
import traceback
from datetime import datetime, timedelta
import queue
import threading
from typing import List, Dict, Optional, Tuple, Callable, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import os

try:
    from PySide6.QtCore import QReadWriteLock
except ImportError:
    print("警告：无法导入 PySide6.QtCore.QReadWriteLock，数据库锁将无法工作。")
    class FakeLock:
        def lockForRead(self): pass
        def lockForWrite(self): pass
        def unlock(self): pass
    QReadWriteLock = FakeLock # type: ignore

class TusharePlaceholder:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None

# --- Tushare Token 设置 ---
try:
    import tushare as ts
    # 您的 Token
    TUSHARE_TOKEN = 'add29f4d5a76a75e6932801380bdf749ac11027e4ee98d3fe268d266' 
    if not TUSHARE_TOKEN or TUSHARE_TOKEN == 'YOUR_TUSHARE_TOKEN':
        raise ImportError("未设置 Tushare Token。")
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()
    print("--- Tushare 初始化成功 ---")
except Exception as e:
    print(f"错误：Tushare 初始化失败: {e}")
    pro = TusharePlaceholder()
    class FakeTS:
        def get_realtime_quotes(self, *args, **kwargs): return None
    ts = FakeTS()

try:
    import akshare as ak
    print(f"--- AkShare 版本: {ak.__version__} (用于快照功能) ---")
except ImportError:
    print("错误：无法导入 akshare。")
    class AkSharePlaceholder:
        def __getattr__(self, name): return lambda *args, **kwargs: None
    ak = AkSharePlaceholder() # type: ignore

# 指数代码字典
INDEX_CODES_TS = {
    "上证指数": "000001.SH",
    "深证成指": "399001.SZ",
    "创业板指": "399006.SZ",
    "微盘股指": "399270.SZ"
}

# --- 核心增强：API 重试装饰器/函数 ---
def fetch_with_retry(api_func: Callable, max_retries=3, delay=1.0, *args, **kwargs) -> Any:
    """通用重试函数，用于增强 Tushare 调用的稳定性"""
    for attempt in range(max_retries):
        try:
            result = api_func(*args, **kwargs)
            # Tushare 有时返回空 DataFrame 但不报错，视为空数据
            if result is not None:
                return result
        except Exception as e:
            # 如果是最后一次尝试，抛出异常或返回 None
            if attempt == max_retries - 1:
                print(f"[API重试失败] {e}")
                return None
            time.sleep(delay * (attempt + 1)) # 指数退避
    return None

def _fetch_and_process_stock_hist_ts(args: Tuple) -> Tuple[str, Optional[pd.DataFrame]]:
    """
    (v19.5) 获取单只股票历史数据 (包含重试机制和完整复权计算)
    """
    ts_code, start_str_ts, end_str_ts, result_queue = args
    stock_code_6_digits = ts_code.split('.')[0]
    log_prefix = f"[采集子线程-{stock_code_6_digits}]"

    def thread_log(message, tag='debug'):
        if result_queue:
            result_queue.put(('collector_log', {'msg': f"{log_prefix} {message}", 'tag': tag}))
        else:
            print(f"{log_prefix} {message}")

    try:
        # 1. 获取日线行情 (Raw) - 增加重试
        def get_bar():
            return ts.pro_bar(ts_code=ts_code, start_date=start_str_ts, end_date=end_str_ts,
                              asset='E', adj=None, freq='D',
                              fields='ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount')
        
        bar_df = fetch_with_retry(get_bar, max_retries=3)
        
        if bar_df is None or bar_df.empty:
            return stock_code_6_digits, None

        # 2. 获取复权因子 - 增加重试
        def get_adj():
            return pro.adj_factor(ts_code=ts_code, start_date=start_str_ts, end_date=end_str_ts,
                                  fields='ts_code,trade_date,adj_factor')
        
        adj_factor_df = fetch_with_retry(get_adj, max_retries=3)

        # 3. 获取每日指标 - 增加重试
        def get_basic():
            return pro.daily_basic(ts_code=ts_code, start_date=start_str_ts, end_date=end_str_ts,
                                   fields='ts_code,trade_date,turnover_rate,turnover_rate_f,'
                                          'volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,dv_ratio,dv_ttm,'
                                          'total_share,float_share,free_share,total_mv,circ_mv')
        
        basic_df = fetch_with_retry(get_basic, max_retries=3)
        
        # --- 数据合并 ---
        merged_df = bar_df
        if adj_factor_df is not None and not adj_factor_df.empty:
            # 确保类型一致
            adj_factor_df['adj_factor'] = pd.to_numeric(adj_factor_df['adj_factor'], errors='coerce')
            merged_df = pd.merge(merged_df, adj_factor_df, on=['ts_code', 'trade_date'], how='left')
        
        if basic_df is not None and not basic_df.empty:
            merged_df = pd.merge(merged_df, basic_df, on=['ts_code', 'trade_date'], how='left')
        
        merged_df.sort_values('trade_date', ascending=True, inplace=True)
        merged_df.reset_index(drop=True, inplace=True)
        
        # --- 数据清洗与计算 ---
        df_cleaned = pd.DataFrame()
        df_cleaned['date'] = pd.to_datetime(merged_df['trade_date'], format='%Y%m%d').dt.strftime('%Y-%m-%d')
        
        df_cleaned['open'] = merged_df['open']
        df_cleaned['high'] = merged_df['high']
        df_cleaned['low'] = merged_df['low']
        df_cleaned['close'] = merged_df['close']
        
        # [核心] 复权价格计算 (前复权)
        # 公式: 价格 * (当日因子 / 最新因子)
        if 'adj_factor' in merged_df.columns:
            # 获取最新有效因子 (iloc[-1] 因为已经按日期排序)
            latest_valid_factor = merged_df['adj_factor'].iloc[-1]
            
            if pd.notna(latest_valid_factor) and latest_valid_factor > 0:
                # 向量化计算，速度快且准确
                factor_ratio = merged_df['adj_factor'] / latest_valid_factor
                df_cleaned['adj_close'] = (merged_df['close'] * factor_ratio).round(4)
                df_cleaned['adj_open'] = (merged_df['open'] * factor_ratio).round(4)
            else:
                # 如果没有因子，回退到原始价格
                df_cleaned['adj_close'] = merged_df['close']
                df_cleaned['adj_open'] = merged_df['open']
        else:
            df_cleaned['adj_close'] = merged_df['close']
            df_cleaned['adj_open'] = merged_df['open']

        # [修复] Volume 单位: Tushare 'vol' 是手 -> 数据库 'volume' 是股 (*100)
        df_cleaned['volume'] = (pd.to_numeric(merged_df['vol'], errors='coerce') * 100).fillna(0).astype('int64')
        
        df_cleaned['stock_code'] = merged_df['ts_code'].str.split('.').str[0]
        # Amount 单位: 千元 -> 元 (*1000)
        df_cleaned['amount'] = pd.to_numeric(merged_df['amount'], errors='coerce').fillna(0) * 1000 

        if 'pre_close' in merged_df.columns:
             df_cleaned['amplitude'] = ((df_cleaned['high'] - df_cleaned['low']) / merged_df['pre_close'] * 100).round(4)
        else:
            df_cleaned['amplitude'] = np.nan

        # [核心] 重新计算复权涨跌幅
        # Tushare 的 pct_chg 是基于未复权的，会导致复权K线回测错误
        # 必须用我们刚算好的 adj_close 来算
        df_cleaned['prev_adj_close'] = df_cleaned['adj_close'].shift(1)
        
        # 涨跌幅 (%) = (今收 - 昨收) / 昨收 * 100
        df_cleaned['ak_change_pct'] = np.where(
            (df_cleaned['prev_adj_close'].notna()) & (df_cleaned['prev_adj_close'] != 0),
            ((df_cleaned['adj_close'] / df_cleaned['prev_adj_close']) - 1) * 100,
            np.nan
        ).round(4)
        
        # 涨跌额
        df_cleaned['ak_change_amount'] = (df_cleaned['adj_close'] - df_cleaned['prev_adj_close']).round(4)
        
        df_cleaned.drop(columns=['prev_adj_close'], inplace=True)

        # 其他指标直接拷贝
        df_cleaned['turnover_rate'] = merged_df.get('turnover_rate', np.nan)
        df_cleaned['turnover_rate_f'] = merged_df.get('turnover_rate_f', np.nan)
        df_cleaned['volume_ratio'] = merged_df.get('volume_ratio', np.nan)
        df_cleaned['pe'] = merged_df.get('pe', np.nan)
        df_cleaned['pe_ttm'] = merged_df.get('pe_ttm', np.nan)
        df_cleaned['pb'] = merged_df.get('pb', np.nan)
        df_cleaned['ps'] = merged_df.get('ps', np.nan)
        df_cleaned['ps_ttm'] = merged_df.get('ps_ttm', np.nan)
        df_cleaned['dv_ratio'] = merged_df.get('dv_ratio', np.nan)
        df_cleaned['dv_ttm'] = merged_df.get('dv_ttm', np.nan)
        df_cleaned['total_share'] = merged_df.get('total_share', np.nan)
        df_cleaned['float_share'] = merged_df.get('float_share', np.nan)
        df_cleaned['free_share'] = merged_df.get('free_share', np.nan)
        df_cleaned['total_mv'] = merged_df.get('total_mv', np.nan)
        df_cleaned['circ_mv'] = merged_df.get('circ_mv', np.nan)
        df_cleaned['turnover'] = np.nan
        df_cleaned['limit_status'] = np.nan

        # 最终列筛选
        final_columns = [
            "date", "open", "high", "low", "close", "volume", 
            "adj_open", "adj_close", "stock_code",
            "amount", "amplitude", "ak_change_pct", "ak_change_amount", "turnover_rate",
            "turnover", "turnover_rate_f", "volume_ratio", "pe", "pe_ttm", "pb", 
            "ps", "ps_ttm", "dv_ratio", "dv_ttm", "total_share", "float_share", 
            "free_share", "total_mv", "circ_mv", "limit_status"
        ]

        # 填充缺失列
        for col in final_columns:
            if col not in df_cleaned.columns:
                df_cleaned[col] = np.nan
        
        df_cleaned = df_cleaned[final_columns]
        df_cleaned.dropna(subset=['open', 'close'], inplace=True)

        return stock_code_6_digits, df_cleaned if not df_cleaned.empty else None

    except Exception as e:
        thread_log(f"处理异常: {e}", 'error')
        traceback.print_exc(limit=1)
        return stock_code_6_digits, None

# [v20.8] 新增：分段采集 ST 历史数据
def _collect_st_history_chunk(
    conn: duckdb.DuckDBPyConnection,
    db_lock: QReadWriteLock,
    start_date_ts: str, # 格式 '20200101'
    end_date_ts: str,   # 格式 '20231231'
    result_queue: queue.Queue,
    stop_event: threading.Event
):
    """
    (新增) 专门用于分段采集 ST 股历史数据
    """
    log_prefix = "[ST采集]"
    def report_log(message, tag='info'): 
        result_queue.put(('collector_log', {'msg': f"{log_prefix} {message}", 'tag': tag}))

    try:
        report_log(f"开始同步 ST 状态历史 ({start_date_ts} - {end_date_ts})...", "info")
        
        # 将时间段按年拆分，防止一次请求数据量过大导致 Tushare 报错
        # 转换字符串日期为 datetime 对象以便计算
        s_date = datetime.strptime(start_date_ts, "%Y%m%d")
        e_date = datetime.strptime(end_date_ts, "%Y%m%d")
        
        curr_date = s_date
        total_inserted = 0
        
        while curr_date <= e_date:
            if stop_event.is_set(): return
            
            # 计算当前块的结束时间（每年取一次）
            next_year = curr_date.replace(year=curr_date.year + 1) - timedelta(days=1)
            chunk_end = min(next_year, e_date)
            
            s_str = curr_date.strftime("%Y%m%d")
            e_str = chunk_end.strftime("%Y%m%d")
            
            try:
                # 调用 Tushare 接口
                # pro.stock_st 支持范围查询
                df = fetch_with_retry(pro.stock_st, start_date=s_str, end_date=e_str, fields='trade_date,ts_code,name')
                
                if df is not None and not df.empty:
                    # 格式化日期：Tushare 返回 'YYYYMMDD', 数据库建议统一存 'YYYY-MM-DD' 以便查询
                    df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d')
                    
                    # 写入 DuckDB
                    db_lock.lockForWrite()
                    try:
                        # 使用 INSERT OR REPLACE 覆盖旧数据
                        conn.register('temp_st_df', df)
                        conn.execute("""
                            INSERT OR REPLACE INTO stock_st_history (trade_date, ts_code, name)
                            SELECT trade_date, ts_code, name FROM temp_st_df
                        """)
                        conn.unregister('temp_st_df')
                        conn.commit()
                        total_inserted += len(df)
                    finally:
                        db_lock.unlock()
            except Exception as e:
                report_log(f"获取 {s_str}-{e_str} 数据失败: {e}", "error")
            
            # 移动到下一段
            curr_date = chunk_end + timedelta(days=1)
            time.sleep(random.uniform(0.3, 0.6)) # 稍微休息防封
            
        report_log(f"ST 历史数据同步完成，累计更新 {total_inserted} 条记录。", "success")

    except Exception as e:
        report_log(f"ST 采集发生错误: {e}", "error")
        traceback.print_exc()

def collect_historical_data_stock_by_stock(
        db_path: str,
        conn: duckdb.DuckDBPyConnection,
        db_lock: QReadWriteLock,
        start_date_str: str,
        end_date_str: str,
        selected_indices_names: List[str],
        result_queue: queue.Queue,
        stop_event: threading.Event
    ):
    
    stock_success_count = 0
    stock_failed_count = 0
    index_success_count = 0
    index_failed_count = 0
    calendar_success = False
    total_saved_stock_rows = 0
    total_saved_index_rows = 0
    total_saved_calendar_rows = 0
    log_prefix = "[采集线程-TS]"
    failed_items = {'stocks':[], 'indices':[], 'calendar': False}

    def report_log(message: str, tag: str = 'info'): result_queue.put(('collector_log', {'msg': f"{log_prefix} {message}", 'tag': tag}))
    def report_progress(progress_value: int): result_queue.put(('collector_progress', progress_value))
    def report_status(running: bool, text: str): result_queue.put(('collector_status', {'running': running, 'text': text}))
    def report_complete(status:str, failed_dict: dict):
         result_queue.put(('collector_complete', {'status': status, 'failed_items': failed_dict}))
         final_progress = 100 if status == "完成" and not stop_event.is_set() else -1
         result_queue.put(('collector_progress', final_progress))

    try:
        report_log(f"开始执行 (v20.8 ST增强版)...", "info")
        report_status(True, "准备中...")
        
        if isinstance(pro, TusharePlaceholder):
            report_log("错误：Tushare 未初始化。", "error")
            report_complete("依赖缺失", failed_items)
            return

        start_str_ts = start_date_str.replace('-', '')
        end_str_ts = end_date_str.replace('-', '')

        # 1. 获取股票列表 (增加重试)
        report_status(True, "获取股票列表...")
        stock_ts_codes = []
        try:
            stock_df = fetch_with_retry(pro.stock_basic, exchange='', list_status='L', fields='ts_code,symbol,name')
            if stock_df is not None and not stock_df.empty:
                stock_df = stock_df[stock_df['ts_code'].str.match(r'^(00|30|60|68)\d{4}\.(SH|SZ)$')]
                stock_ts_codes = stock_df['ts_code'].tolist()
                report_log(f"成功获取 {len(stock_ts_codes)} 只 A 股代码。", "info")
            else:
                report_log("严重警告：三次重试后仍未能获取股票列表！可能是Token无效或IP被封。", "error")
        except Exception as e:
            report_log(f"获取股票列表异常: {e}", "error")

        if not stock_ts_codes:
            report_log("因无股票列表，跳过股票采集。", "warning")

        if stop_event.is_set(): raise InterruptedError("用户停止")

        # 2. 获取交易日历 (增加重试)
        report_status(True, "获取交易日历...")
        try:
            calendar_df = fetch_with_retry(pro.trade_cal, exchange='', start_date=start_str_ts, end_date=end_str_ts, fields='cal_date,is_open,pretrade_date')
            if calendar_df is not None and not calendar_df.empty:
                calendar_df['date'] = pd.to_datetime(calendar_df['cal_date']).dt.strftime('%Y-%m-%d')
                calendar_df['pretrade_date1'] = pd.to_datetime(calendar_df['pretrade_date']).dt.strftime('%Y-%m-%d')
                calendar_df['exchange'] = 'CNS'
                data_tuples = calendar_df[['exchange', 'is_open', 'date', 'pretrade_date1']].values.tolist()
                db_lock.lockForWrite()
                try:
                    cursor = conn.cursor()
                    cursor.executemany("INSERT OR REPLACE INTO exp_trade (exchange, is_open, date, pretrade_date1) VALUES (?, ?, ?, ?)", data_tuples)
                    conn.commit() # [v20.1] 提交日历
                    calendar_success = True
                    report_log(f"交易日历更新完成 ({len(data_tuples)} 天)。", "success")
                except Exception as e_cal_db:
                    report_log(f"日历数据库写入异常: {e_cal_db}", "error")
                finally:
                    db_lock.unlock()
            else:
                report_log("警告：无法获取交易日历。", "warning")
                failed_items['calendar'] = True
        except Exception as e:
            report_log(f"日历处理异常: {e}", "error")
            failed_items['calendar'] = True

        if stop_event.is_set(): raise InterruptedError("用户停止")

        # ----------------------------------------------------
        # 2.5 采集 ST 历史数据 (v20.8 新增)
        # ----------------------------------------------------
        if not stop_event.is_set():
            _collect_st_history_chunk(conn, db_lock, start_str_ts, end_str_ts, result_queue, stop_event)
        
        if stop_event.is_set(): raise InterruptedError("用户停止")

        # 3. 并行采集股票
        if stock_ts_codes:
            
            # [v19.9] 性能优化：在开始循环前，先清空历史数据表
            try:
                report_log("正在清空历史价格表 (stock_prices)...", "info")
                db_lock.lockForWrite()
                conn.execute("DELETE FROM stock_prices")
                conn.commit() # [v20.1] 提交 DELETE
                report_log("清空完成，即将开始批量插入...", "success")
            except Exception as e_del:
                report_log(f"清空 stock_prices 表失败: {e_del}", "error")
            finally:
                db_lock.unlock()

            report_log(f"开始采集 {len(stock_ts_codes)} 只股票历史数据...", "info")
            # 限制并发数为 3，防止 Tushare 封 IP
            with ThreadPoolExecutor(max_workers=3) as executor:
                # 提交任务
                futures = {executor.submit(_fetch_and_process_stock_hist_ts, (code, start_str_ts, end_str_ts, result_queue)): code for code in stock_ts_codes}
                
                total = len(stock_ts_codes)
                for i, future in enumerate(as_completed(futures)):
                    if stop_event.is_set(): 
                        executor.shutdown(wait=False)
                        break
                    
                    code = futures[future]
                    try:
                        res_code, df = future.result() # df 是一个 Pandas DataFrame
                        if df is not None and not df.empty:
                            # 写入数据库
                            db_lock.lockForWrite()
                            try:
                                # [v20.2] 关键修复：
                                # 显式指定列名，防止 "SELECT *" 导致的列序错配
                                cols_str = ', '.join([f'"{c}"' for c in df.columns])
                                conn.execute(f"INSERT INTO stock_prices ({cols_str}) SELECT * FROM df")
                                
                                # [v20.1] 关键修复：
                                # 每次写入后立即提交
                                conn.commit()
                                
                                total_saved_stock_rows += len(df)
                                stock_success_count += 1
                            except Exception as e_db:
                                report_log(f"写入 {res_code} 数据时数据库出错: {e_db}", "error")
                                stock_failed_count += 1
                            finally:
                                db_lock.unlock()
                        else:
                            stock_failed_count += 1
                    except Exception as e:
                        stock_failed_count += 1
                        # print(f"Error processing {code}: {e}")
                    
                    # 进度报告
                    if i % 10 == 0:
                        progress = int((i / total) * 100)
                        report_progress(progress)
                        report_status(True, f"采集进度: {i}/{total} (成功:{stock_success_count})")
                    
                    # [v20.1] 礼貌休眠
                    time.sleep(random.uniform(0.1, 0.2)) # 100-200毫秒
        
        # 4. [v20.6 修复] 采集指数历史数据
        if selected_indices_names:
            report_log(f"开始采集 {len(selected_indices_names)} 个指数的历史数据...", "info")
            try:
                report_log("正在清空历史指数表 (index_prices)...", "info")
                db_lock.lockForWrite()
                conn.execute("DELETE FROM index_prices")
                conn.commit()
                report_log("清空完成，即将开始批量插入指数数据...", "success")
            except Exception as e_del_idx:
                report_log(f"清空 index_prices 表失败: {e_del_idx}", "error")
            finally:
                db_lock.unlock()

            for index_name in selected_indices_names:
                if stop_event.is_set(): break
                index_ts_code = INDEX_CODES_TS.get(index_name)
                if not index_ts_code:
                    report_log(f"跳过指数 '{index_name}'：未在 INDEX_CODES_TS 字典中找到。", "warning")
                    continue
                
                report_log(f"正在获取指数 {index_name} ({index_ts_code}) 的历史数据...", "debug")
                
                try:
                    def get_index_bar():
                        return ts.pro_bar(
                            ts_code=index_ts_code,
                            asset='I', # 'I' for Index
                            start_date=start_str_ts,
                            end_date=end_str_ts,
                            fields='ts_code,trade_date,open,high,low,close,vol,amount'
                        )
                    
                    df_index = fetch_with_retry(get_index_bar, max_retries=3)
                    
                    if df_index is None or df_index.empty:
                        report_log(f"未能获取指数 {index_name} 的数据。", "warning")
                        index_failed_count += 1
                        continue

                    # 数据清洗
                    df_cleaned = pd.DataFrame()
                    df_cleaned['index_code'] = df_index['ts_code'].str.split('.').str[0]
                    df_cleaned['date'] = pd.to_datetime(df_index['trade_date'], format='%Y%m%d').dt.strftime('%Y-%m-%d')
                    df_cleaned['open'] = pd.to_numeric(df_index['open'], errors='coerce')
                    df_cleaned['high'] = pd.to_numeric(df_index['high'], errors='coerce')
                    df_cleaned['low'] = pd.to_numeric(df_index['low'], errors='coerce')
                    df_cleaned['close'] = pd.to_numeric(df_index['close'], errors='coerce')
                    df_cleaned['volume'] = (pd.to_numeric(df_index['vol'], errors='coerce') * 100).fillna(0).astype('int64') # Tushare 'vol' 是手
                    df_cleaned['amount'] = pd.to_numeric(df_index['amount'], errors='coerce').fillna(0) * 1000 # Tushare 'amount' 是千元
                    
                    # 写入数据库
                    db_lock.lockForWrite()
                    try:
                        cols_str = ', '.join([f'"{c}"' for c in df_cleaned.columns])
                        conn.execute(f"INSERT INTO index_prices ({cols_str}) SELECT * FROM df_cleaned")
                        conn.commit()
                        total_saved_index_rows += len(df_cleaned)
                        
                        # [v20.7] 关键修复：明确报告采集到的指数行数
                        report_log(f"成功写入指数 [{index_name}] {len(df_cleaned)} 条数据。", "success") 

                        index_success_count += 1
                    except Exception as e_db_idx:
                        report_log(f"写入指数 {index_name} 数据时数据库出错: {e_db_idx}", "error")
                        index_failed_count += 1
                    finally:
                        db_lock.unlock()
                        
                except Exception as e_idx:
                    report_log(f"处理指数 {index_name} 时发生意外错误: {e_idx}", "error")
                    index_failed_count += 1
                
                # 指数采集也需要限速
                time.sleep(random.uniform(0.1, 0.2))

        report_complete("完成", failed_items)

    except InterruptedError:
        report_log("任务已停止", "warning")
        report_complete("已停止", failed_items)
    except Exception as e:
        report_log(f"主线程严重错误: {e}", "error")
        traceback.print_exc()
        report_complete("错误", failed_items)

# ... [_get_snapshot_dates, _fetch_stock_snapshot_ts, _fetch_index_snapshot_ts, _fetch_board_snapshot_ts 保持不变] ...
def _get_snapshot_dates(conn: duckdb.DuckDBPyConnection, report_log) -> Tuple[Optional[str], Optional[str]]:
    try:
        today_str_db = datetime.now().strftime('%Y-%m-%d')
        t_minus_1_trade_date_ts: Optional[str] = None
        row_t_minus_1 = conn.execute("SELECT date FROM exp_trade WHERE is_open=1 AND date < ? ORDER BY date DESC LIMIT 1", (today_str_db,)).fetchone()
        if row_t_minus_1:
            t_minus_1_trade_date_ts = row_t_minus_1[0].replace('-', '') 
        else:
            report_log("警告：无法在 exp_trade 中找到 T-1 交易日，将尝试查找 T-0 或更早。", "warning")
            row_latest = conn.execute("SELECT date FROM exp_trade WHERE is_open=1 AND date <= ? ORDER BY date DESC LIMIT 1", (today_str_db,)).fetchone()
            if row_latest: t_minus_1_trade_date_ts = row_latest[0].replace('-', '')
        today_trade_date_ts: Optional[str] = None
        row_today = conn.execute("SELECT date FROM exp_trade WHERE is_open=1 AND date = ? LIMIT 1", (today_str_db,)).fetchone()
        if row_today:
            today_trade_date_ts = row_today[0].replace('-', '')
        else:
            today_trade_date_ts = t_minus_1_trade_date_ts
        if not today_trade_date_ts or not t_minus_1_trade_date_ts:
             report_log("错误：无法在 exp_trade 表中找到交易日。请先运行历史数据采集。", "error")
             return None, None
        return today_trade_date_ts, t_minus_1_trade_date_ts
    except Exception as e:
        report_log(f"获取快照日期时出错: {e}", "error")
        return None, None
def _fetch_stock_snapshot_ts(trade_date_ts: str, report_log) -> Optional[pd.DataFrame]:
    try:
        report_log("Tushare: 正在获取股票基础列表 (name, industry)...", "info")
        stock_list_df = pro.stock_basic(list_status='L', fields='ts_code,symbol,name,industry')
        if stock_list_df is None or stock_list_df.empty:
            report_log("Tushare: pro.stock_basic 返回空数据。", "error"); return None
        stock_list_df.rename(columns={'symbol': 'stock_code', 'name': 'stock_name'}, inplace=True)
        stock_list_df = stock_list_df[stock_list_df['ts_code'].str.match(r'^(00|30|60|68)\d{4}\.(SH|SZ)$')]
        report_log(f"Tushare: 正在获取 {trade_date_ts} 的日线指标 (PE, PB, MV)...", "info")
        daily_basic_fields = ['ts_code', 'trade_date', 'turnover_rate', 'volume_ratio', 'pe', 'pe_ttm', 'pb', 'total_mv', 'circ_mv']
        daily_basic_df = pro.daily_basic(trade_date=trade_date_ts, fields=','.join(daily_basic_fields))
        if daily_basic_df is None or daily_basic_df.empty:
            report_log(f"Tushare: pro.daily_basic 在 {trade_date_ts} 返回空数据。", "warning")
            daily_basic_df = pd.DataFrame(columns=daily_basic_fields) 
        daily_basic_df.rename(columns={'pe_ttm': 'pe_ratio', 'pb': 'pb_ratio'}, inplace=True)
        if 'total_mv' in daily_basic_df: daily_basic_df['total_market_cap'] = daily_basic_df['total_mv'] * 10000
        if 'circ_mv' in daily_basic_df: daily_basic_df['circulating_market_cap'] = daily_basic_df['circ_mv'] * 10000
        report_log("Tushare: 正在获取股票实时行情 (price, ohlc)...", "info")
        if stock_list_df.empty or 'stock_code' not in stock_list_df.columns:
            report_log("Tushare: 基础列表(stock_list_df)为空或缺少'stock_code'，无法获取实时行情。", "error"); return None
        all_6_digit_codes = stock_list_df['stock_code'].dropna().unique().tolist()
        if not all_6_digit_codes:
            report_log("Tushare: 基础列表未提供有效股票代码。", "error"); return None
        quotes_df = None; batch_size = 500; all_quotes_dfs = []
        for i in range(0, len(all_6_digit_codes), batch_size):
            batch_codes = all_6_digit_codes[i:i+batch_size]
            try:
                batch_df = ts.get_realtime_quotes(batch_codes)
                if batch_df is not None: all_quotes_dfs.append(batch_df)
                time.sleep(random.uniform(0.1, 0.2)) 
            except Exception as e_quote:
                report_log(f"Tushare: ts.get_realtime_quotes 批次 {i//batch_size} 失败: {e_quote}", "warning")
        if not all_quotes_dfs:
             report_log("Tushare: ts.get_realtime_quotes 未返回任何数据。", "error"); return None
        quotes_df = pd.concat(all_quotes_dfs, ignore_index=True)
        rename_map_quotes = {'code': 'stock_code', 'price': 'latest_price', 'pre_close': 'prev_close', 'open': 'open', 'high': 'high', 'low': 'low', 'volume': 'volume', 'amount': 'trade_amount'}
        quotes_df = quotes_df[list(rename_map_quotes.keys())].rename(columns=rename_map_quotes)
        for col in ['latest_price', 'prev_close', 'open', 'high', 'low', 'volume', 'trade_amount']:
             quotes_df[col] = pd.to_numeric(quotes_df[col], errors='coerce')
        quotes_df['volume'] = quotes_df['volume'] * 100
        quotes_df.dropna(subset=['stock_code', 'latest_price'], inplace=True)
        report_log("Tushare: 正在合并基础列表、日线指标和实时行情...", "info")
        df = pd.merge(stock_list_df, daily_basic_df, on='ts_code', how='left')
        df_final_stock = pd.merge(df, quotes_df, on='stock_code', how='left')
        df_final_stock['change_percent'] = ((df_final_stock['latest_price'] / df_final_stock['prev_close']) - 1) * 100
        df_final_stock['change_amount'] = df_final_stock['latest_price'] - df_final_stock['prev_close']
        df_final_stock['amplitude'] = ((df_final_stock['high'] - df_final_stock['low']) / df_final_stock['prev_close']) * 100
        df_final_stock['change_rate'] = np.nan; df_final_stock['change_pct_5min'] = np.nan
        df_final_stock['change_pct_60d'] = np.nan; df_final_stock['change_pct_ytd'] = np.nan
        df_final_stock['last_updated'] = datetime.now().isoformat()
        report_log(f"Tushare: 股票快照合并完成，共 {len(df_final_stock)} 条记录。", "info")
        return df_final_stock
    except Exception as e:
        report_log(f"Tushare: _fetch_stock_snapshot_ts 发生严重错误: {e}", "error"); traceback.print_exc(); return None
def _fetch_index_snapshot_ts(report_log) -> Optional[pd.DataFrame]:
    try:
        report_log("Tushare: 正在获取指数实时行情 (using ts.get_realtime_quotes)...", "info")
        index_ts_codes = list(INDEX_CODES_TS.values()) 
        if not index_ts_codes:
            report_log("Tushare: INDEX_CODES_TS 为空，跳过指数快照。", "warning"); return pd.DataFrame()
        codes_with_prefix = []
        for ts_code in index_ts_codes:
            parts = ts_code.split('.'); 
            if len(parts) == 2: code, suffix = parts[0], parts[1]; codes_with_prefix.append(suffix.lower() + code)
        if not codes_with_prefix:
             report_log("Tushare: 无法生成带前缀的指数代码。", "error"); return pd.DataFrame()
        report_log(f"Tushare: 正在查询: {codes_with_prefix}", "debug")
        index_df = ts.get_realtime_quotes(codes_with_prefix)
        if index_df is None or index_df.empty:
            report_log("Tushare: ts.get_realtime_quotes 未返回指数数据。", "warning"); return pd.DataFrame()
        rename_map = {'code': 'index_code', 'name': 'index_name', 'price': 'latest_price', 'pre_close': 'prev_close', 'open': 'open', 'high': 'high', 'low': 'low', 'volume': 'volume', 'amount': 'amount'}
        keep_cols = list(rename_map.keys())
        missing_cols = [col for col in keep_cols if col not in index_df.columns]
        if missing_cols:
             report_log(f"Tushare: ts.get_realtime_quotes 返回数据缺少列: {missing_cols}", "error"); return pd.DataFrame()
        index_df = index_df[keep_cols].rename(columns=rename_map)
        for col in ['latest_price', 'prev_close', 'open', 'high', 'low', 'volume', 'amount']:
             index_df[col] = pd.to_numeric(index_df[col], errors='coerce')
        index_df['change_amount'] = index_df['latest_price'] - index_df['prev_close']
        index_df['change_percent'] = ((index_df['latest_price'] / index_df['prev_close']) - 1) * 100
        index_df['volume'] = index_df['volume'] * 100
        index_df['index_code'] = index_df['index_code'].str.replace('sh|sz', '', regex=True)
        index_df['last_updated'] = datetime.now().isoformat()
        report_log(f"Tushare: 指数快照获取完成，共 {len(index_df)} 条记录。", "info")
        return index_df
    except Exception as e:
        report_log(f"Tushare: _fetch_index_snapshot_ts 发生错误: {e}", "error"); traceback.print_exc(); return None
def _fetch_board_snapshot_ts(trade_date_ts: str, report_log) -> Optional[pd.DataFrame]:
    try:
        report_log(f"Tushare: 正在获取 {trade_date_ts} 的同花顺概念(板块)日线...", "info")
        all_fields = 'ts_code,trade_date,name,close,pct_change,vol,amount,total_mv,turn_rate,lead_stock_code,lead_stock_name,lead_stock_pct_change,net_mf_rate'
        board_df = pro.ths_daily(trade_date=trade_date_ts, fields=all_fields)
        if board_df is None or board_df.empty:
            report_log(f"Tushare: pro.ths_daily 在 {trade_date_ts} 未返回板块数据。", "warning"); return pd.DataFrame()
        rename_map = {'ts_code': 'board_code', 'name': 'board_name', 'close': 'avg_price', 'pct_change': 'change_percent', 'total_mv': 'total_market_cap', 'turn_rate': 'turnover_rate', 'lead_stock_code': 'leading_stock_code', 'lead_stock_name': 'leading_stock_name', 'lead_stock_pct_change': 'leading_stock_change_percent', 'net_mf_rate': 'main_net_inflow_rate'}
        board_df = board_df.rename(columns=rename_map)
        if 'total_market_cap' in board_df: board_df['total_market_cap'] = board_df['total_market_cap'] * 10000
        board_df['company_count'] = np.nan; board_df['main_net_inflow'] = np.nan 
        board_df['last_updated'] = datetime.now().isoformat()
        report_log(f"Tushare: 板块快照获取完成，共 {len(board_df)} 条记录。", "info")
        return board_df
    except Exception as e:
        report_log(f"Tushare: _fetch_board_snapshot_ts 发生错误: {e}", "error"); traceback.print_exc(); return None

# --- (v18.4) MIGRATED: 快照采集函数 (已迁移至 Tushare) ---
def collect_full_snapshot(db_path: str, conn: duckdb.DuckDBPyConnection, db_lock: QReadWriteLock, result_queue: queue.Queue, stop_event: threading.Event): # [DuckDB] 更改
    """(v18.4) MIGRATED: 使用 Tushare 采集股票、指数和板块的快照。"""
    log_prefix = "[快照采集-TS]"
    def report_log(message: str, tag: str = 'info'): result_queue.put(('collector_log', {'msg': f"{log_prefix} {message}", 'tag': tag}))
    def report_status(running: bool, text: str): result_queue.put(('snapshot_status', {'running': running, 'text': text}))
    def report_complete(status:str, details: dict): result_queue.put(('snapshot_complete', {'status': status, 'details': details}))
    
    report_log("开始执行完整快照采集 (使用 Tushare)...", "info")
    report_status(True, "采集中 (Tushare)...")
    start_time = time.time()
    results_summary = {'stocks': 0, 'indices': 0, 'boards': 0}
    errors = []

    if isinstance(pro, TusharePlaceholder):
        report_log("Tushare 未加载，无法执行快照采集。", "error"); return
    if stop_event.is_set(): return

    try:
        report_log("获取 T-1 和 T-0 交易日...", "info")
        today_ts, t_minus_1_ts = _get_snapshot_dates(conn, report_log)
        if not t_minus_1_ts:
             errors.append("获取最新交易日失败"); raise InterruptedError("无法获取交易日")
        report_log(f"T-1 日期 (用于 T-1 数据): {t_minus_1_ts}", "info")
        report_log(f"T-0 日期 (用于实时数据): {today_ts}", "info")
        report_log("获取股票快照 (Tushare)...", "info")
        stock_df = _fetch_stock_snapshot_ts(t_minus_1_ts, report_log)
        if stock_df is None: errors.append("股票快照")
        else: report_log(f"获取到 {len(stock_df)} 条股票快照原始数据。", "info")
        if stop_event.is_set(): raise InterruptedError()
        report_log("获取指数快照 (Tushare)...", "info")
        index_df = _fetch_index_snapshot_ts(report_log)
        if index_df is None: report_log("警告: Tushare 未返回指数快照数据。", "warning")
        if stop_event.is_set(): raise InterruptedError()
        report_log("获取板块(概念)快照 (Tushare)...", "info")
        board_df = _fetch_board_snapshot_ts(t_minus_1_ts, report_log)
        if board_df is None: report_log("警告: Tushare 未返回板块快照数据。", "warning")
        if stop_event.is_set(): raise InterruptedError()
        report_log("准备写入数据库...", "info")
        db_lock.lockForWrite()
        try:
            cursor = conn.cursor()
            
            # --- 写入股票快照 ---
            if stock_df is not None and not stock_df.empty:
                target_stock_cols = [ 'stock_code', 'stock_name', 'latest_price', 'change_percent', 'change_amount', 'volume', 'trade_amount', 'open', 'high', 'low', 'prev_close', 'amplitude', 'turnover_rate', 'volume_ratio', 'pe_ratio', 'pb_ratio', 'total_market_cap', 'circulating_market_cap', 'change_rate', 'change_pct_5min', 'change_pct_60d', 'change_pct_ytd', 'industry', 'last_updated' ]
                cursor.execute("DESCRIBE stock_fundamentals"); db_cols = [info[0] for info in cursor.fetchall()]
                stock_df_final = stock_df[[col for col in target_stock_cols if col in stock_df.columns and col in db_cols]]
                
                if not stock_df_final.empty:
                    cursor.execute("DELETE FROM stock_fundamentals")
                    # [v20.5] 关键修复：显式指定列名
                    cols_str = ', '.join([f'"{c}"' for c in stock_df_final.columns])
                    conn.execute(f"INSERT INTO stock_fundamentals ({cols_str}) SELECT * FROM stock_df_final")
                    
                    results_summary['stocks'] = len(stock_df_final)
                    report_log(f"写入 {results_summary['stocks']} 条股票快照到 stock_fundamentals。", "info")

            # --- 写入指数快照 ---
            if index_df is not None and not index_df.empty:
                final_index_cols = ['index_code', 'index_name', 'latest_price', 'change_amount', 'change_percent', 'volume', 'amount', 'last_updated']
                cursor.execute("DESCRIBE index_snapshots"); db_cols = [info[0] for info in cursor.fetchall()] 
                index_df_final = index_df[[col for col in final_index_cols if col in index_df.columns and col in db_cols]]
                
                if not index_df_final.empty:
                    cursor.execute("DELETE FROM index_snapshots")
                    # [v20.5] 关键修复：显式指定列名
                    cols_str = ', '.join([f'"{c}"' for c in index_df_final.columns])
                    conn.execute(f"INSERT INTO index_snapshots ({cols_str}) SELECT * FROM index_df_final")
                    
                    results_summary['indices'] = len(index_df_final)
                    report_log(f"写入 {results_summary['indices']} 条指数快照到 index_snapshots。", "info")

            # --- 写入板块快照 ---
            if board_df is not None and not board_df.empty:
                target_board_cols = ['board_code', 'board_name', 'company_count', 'avg_price', 'change_percent', 'total_market_cap', 'main_net_inflow', 'main_net_inflow_rate', 'turnover_rate', 'leading_stock_code', 'leading_stock_name', 'leading_stock_change_percent', 'last_updated']
                cursor.execute("DESCRIBE board_snapshots"); db_cols = [info[0] for info in cursor.fetchall()] 
                final_cols_to_write = [col for col in target_board_cols if col in board_df.columns and col in db_cols]
                report_log(f"Tushare: 准备写入板块的列: {final_cols_to_write}", "debug") 
                board_df_final = board_df[final_cols_to_write]
                
                if not board_df_final.empty:
                    cursor.execute("DELETE FROM board_snapshots")
                    # [v20.5] 关键修复：显式指定列名
                    cols_str = ', '.join([f'"{c}"' for c in board_df_final.columns])
                    conn.execute(f"INSERT INTO board_snapshots ({cols_str}) SELECT * FROM board_df_final")
                    
                    results_summary['boards'] = len(board_df_final)
                    report_log(f"写入 {results_summary['boards']} 条板块快照到 board_snapshots。", "info")
            
            conn.commit() # [v20.1] 提交快照数据
            report_log("数据库写入操作完成 (Tushare 快照)。", "success")
        except Exception as db_err:
            # [v20.3] 移除 conn.rollback()
            report_log(f"数据库写入快照(Tushare)时出错: {db_err}", "error"); traceback.print_exc(); errors.append("数据库写入")
        finally: db_lock.unlock()
        duration = time.time() - start_time
        final_status = "完成" if not errors else f"部分失败 ({', '.join(errors)})"
        final_tag = "success" if not errors else "warning"
        summary_msg = f"快照采集(Tushare)结束 (耗时 {duration:.2f} 秒)。 股票: {results_summary['stocks']} 条。 指数: {results_summary['indices']} 条。 板块: {results_summary['boards']} 条。"
        if errors: summary_msg += f" 失败环节: {', '.join(errors)}。"
        report_log(summary_msg, final_tag); report_complete(final_status, results_summary)
    except InterruptedError:
        report_log("快照采集被用户停止。", "warning"); report_complete("已停止", results_summary)
    except Exception as e:
        report_log(f"快照采集发生未知严重错误: {e}", "error"); traceback.print_exc(); report_complete(f"严重错误:{type(e).__name__}", results_summary)
    finally:
        report_status(False, "空闲" if not stop_event.is_set() else "已停止")

def collect_stock_snapshot_auto(db_path: str, conn: duckdb.DuckDBPyConnection, db_lock: QReadWriteLock, result_queue: queue.Queue, stop_event: threading.Event): # [DuckDB] 更改
    """(v18.3) MIGRATED: 使用 Tushare 自动采集股票和指数快照。"""
    log_prefix = "[自动快照-TS]"
    def report_log(message: str, tag: str = 'info'): result_queue.put(('collector_log', {'msg': f"{log_prefix} {message}", 'tag': tag}))
    def report_status(running: bool, text: str): result_queue.put(('snapshot_status', {'running': running, 'text': text}))
    def report_complete(status:str, details: dict): result_queue.put(('auto_snapshot_complete', {'status': status, 'details': details}))
    
    report_log("开始执行自动快照 (使用 Tushare)...", "info")
    start_time = time.time(); results_summary = {'stocks': 0, 'indices': 0}; errors = []
    if isinstance(pro, TusharePlaceholder): return
    if stop_event.is_set(): return
    try:
        report_log("获取 T-1 和 T-0 交易日...", "info")
        today_ts, t_minus_1_ts = _get_snapshot_dates(conn, report_log)
        if not t_minus_1_ts:
             errors.append("获取最新交易日失败"); raise InterruptedError("无法获取交易日")
        report_log(f"T-1 日期 (用于 T-1 数据): {t_minus_1_ts}", "info")
        report_log("获取股票快照 (Tushare)...", "info")
        stock_df = _fetch_stock_snapshot_ts(t_minus_1_ts, report_log)
        if stock_df is None: errors.append("股票快照")
        else:
            try:
                db_lock.lockForRead()
                existing_industry = dict(conn.cursor().execute("SELECT stock_code, industry FROM stock_fundamentals").fetchall())
                db_lock.unlock()
                if existing_industry: stock_df['industry'] = stock_df['stock_code'].map(existing_industry)
            except Exception as e_ind: report_log(f"读取现有行业数据失败: {e_ind}", "warning")
        if stop_event.is_set(): raise InterruptedError()
        report_log("获取指数快照 (Tushare)...", "info")
        index_df = _fetch_index_snapshot_ts(report_log)
        if index_df is None: report_log("警告: Tushare 未返回指数快照数据。", "warning")
        if stop_event.is_set(): raise InterruptedError()
        report_log("准备写入数据库...", "info")
        db_lock.lockForWrite()
        try:
            # [v20.1] 自动快照也是 DELETE + INSERT
            cursor = conn.cursor()
            if stock_df is not None and not stock_df.empty:
                target_stock_cols = [ 'stock_code', 'stock_name', 'latest_price', 'change_percent', 'change_amount', 'volume', 'trade_amount', 'open', 'high', 'low', 'prev_close', 'amplitude', 'turnover_rate', 'volume_ratio', 'pe_ratio', 'pb_ratio', 'total_market_cap', 'circulating_market_cap', 'change_rate', 'change_pct_5min', 'change_pct_60d', 'change_pct_ytd', 'industry', 'last_updated' ]
                cursor.execute("DESCRIBE stock_fundamentals"); db_cols = [info[0] for info in cursor.fetchall()]
                stock_df_final = stock_df[[col for col in target_stock_cols if col in stock_df.columns and col in db_cols]]
                
                if not stock_df_final.empty:
                    cursor.execute("DELETE FROM stock_fundamentals")
                    # [v20.5] 关键修复：显式指定列名
                    cols_str = ', '.join([f'"{c}"' for c in stock_df_final.columns])
                    conn.execute(f"INSERT INTO stock_fundamentals ({cols_str}) SELECT * FROM stock_df_final")
                    
                    results_summary['stocks'] = len(stock_df_final)

            if index_df is not None and not index_df.empty:
                final_index_cols = ['index_code', 'index_name', 'latest_price', 'change_amount', 'change_percent', 'volume', 'amount', 'last_updated']
                cursor.execute("DESCRIBE index_snapshots"); db_cols = [info[0] for info in cursor.fetchall()]
                index_df_final = index_df[[col for col in final_index_cols if col in index_df.columns and col in db_cols]]
                
                if not index_df_final.empty:
                    cursor.execute("DELETE FROM index_snapshots")
                    # [v20.5] 关键修复：显式指定列名
                    cols_str = ', '.join([f'"{c}"' for c in index_df_final.columns])
                    conn.execute(f"INSERT INTO index_snapshots ({cols_str}) SELECT * FROM index_df_final")
                    
                    results_summary['indices'] = len(index_df_final)

            conn.commit() # [v20.1] 提交自动快照
        except Exception as db_err:
            # [v20.3] 移除 conn.rollback()
            report_log(f"数据库写入自动快照(Tushare)时出错: {db_err}", "error"); traceback.print_exc(); errors.append("数据库写入")
        finally: db_lock.unlock()
        duration = time.time() - start_time
        final_status = "完成" if not errors else f"部分失败 ({', '.join(errors)})"
        final_tag = "success" if not errors else "warning"
        summary_msg = f"自动快照(Tushare)更新结束 (耗时 {duration:.2f} 秒)。 股票: {results_summary['stocks']} 条。 指数: {results_summary['indices']} 条。"
        if errors: summary_msg += f" 失败环节: {', '.join(errors)}。"
        report_log(summary_msg, final_tag); report_complete(final_status, results_summary)
    except InterruptedError:
        report_log("自动快照(Tushare)被用户停止。", "warning"); report_complete("已停止", results_summary)
    except Exception as e:
        report_log(f"自动快G照(Tushare)发生未知严重错误: {e}", "error"); traceback.print_exc(); report_complete(f"严重错误:{type(e).__name__}", results_summary)
    finally:
        current_status = "已停止" if stop_event.is_set() else "空闲"
        result_queue.put(('snapshot_status', {'running': False, 'text': current_status}))

# (*** v18.9 MODIFIED & FIX COMPLETED ***)
def collect_fundamentals_data(
        db_path: str,
        conn: duckdb.DuckDBPyConnection, # [DuckDB] 更改
        db_lock: QReadWriteLock,
        start_date_str: str,
        end_date_str: str,
        result_queue: queue.Queue,
        stop_event: threading.Event
    ):
    """
    (v18.9) 采集详细的股票基本面数据
    (Fix) 修复了被截断的逻辑，补充了 Tushare 调用和 DuckDB 写入
    """
    log_prefix = "[基本面采集-TS]"
    def report_log(message: str, tag: str = 'info'): result_queue.put(('fundamentals_log', {'msg': f"{log_prefix} {message}", 'tag': tag}))
    def report_progress(progress_value: int): result_queue.put(('fundamentals_progress', progress_value))
    def report_status(running: bool, text: str): result_queue.put(('fundamentals_status', {'running': running, 'text': text}))
    def report_complete(status: str, total_rows: int): result_queue.put(('fundamentals_complete', {'status': status, 'total_rows': total_rows}))
    def report_error_box(title: str, message: str): result_queue.put(('collector_error', {'title': title, 'msg': message})) 
    
    total_saved_rows = 0
    
    try:
        report_log(f"开始执行基本面数据采集 ({start_date_str} 到 {end_date_str}) (v20.4)", "info")
        report_status(True, "准备中...")
        if not conn: raise ValueError("数据库连接无效。")
        if isinstance(pro, TusharePlaceholder):
            report_log("错误：Tushare 未正确初始化。", "error"); report_error_box("Tushare初始化失败", "Tushare API未能正确初始化，请检查Token设置或库安装情况"); report_complete("依赖缺失", 0); return
        
        try:
            # 生成季度末日期列表 (Tushare 基本面数据通常按季度发布)
            q_dates = pd.date_range(start=start_date_str, end=end_date_str, freq='QE').strftime('%Y%m%d').tolist()
            if not q_dates:
                 report_log(f"在 {start_date_str} 和 {end_date_str} 之间未找到季度末日期。", "warning")
                 q_dates = [end_date_str.replace('-', '')] 
            report_log(f"将查询 {len(q_dates)} 个季度的报告期: {', '.join(q_dates[:5])}...", "info")
        except Exception as e_date:
            report_log(f"生成季度日期时出错: {e_date}", "error"); report_complete("日期错误", 0); return
        
        if stop_event.is_set(): raise InterruptedError("用户停止(准备阶段)")
        
        fields_list = [
            'ts_code', 'ann_date', 'end_date', 'eps', 'dt_eps', 'total_revenue_ps', 'revenue_ps', 'capital_rese_ps', 'surplus_rese_ps', 'undist_profit_ps', 
            'extra_item', 'profit_dedt', 'gross_margin', 'current_ratio', 'quick_ratio', 'cash_ratio', 'ar_turn', 'ca_turn', 'fa_turn', 'assets_turn', 'op_income', 
            'ebit', 'ebitda', 'fcff', 'fcfe', 'current_exint', 'noncurrent_exint', 'interestdebt', 'netdebt', 'tangible_asset', 'working_capital', 
            'networking_capital', 'invest_capital', 'retained_earnings', 'diluted2_eps', 'bps', 'ocfps', 'retainedps', 'cfps', 'ebit_ps', 'fcff_ps', 'fcfe_ps', 
            'netprofit_margin', 'grossprofit_margin', 'cogs_of_sales', 'expense_of_sales', 'profit_to_gr', 'saleexp_to_gr', 'adminexp_of_gr', 'finaexp_of_gr', 
            'impai_ttm', 'gc_of_gr', 'op_of_gr', 'ebit_of_gr', 'roe', 'roe_waa', 'roe_dt', 'roa', 'npta', 'roic', 'roe_yearly', 'roa2_yearly', 
            'debt_to_assets', 'assets_to_eqt', 'dp_assets_to_eqt', 'ca_to_assets', 'nca_to_assets', 'tbassets_to_totalassets', 'int_to_talcap', 
            'eqt_to_talcapital', 'currentdebt_to_debt', 'longdeb_to_debt', 'ocf_to_shortdebt', 'debt_to_eqt', 'eqt_to_debt', 'eqt_to_interestdebt', 
            'tangibleasset_to_debt', 'tangasset_to_intdebt', 'tangibleasset_to_netdebt', 'ocf_to_debt', 'turn_days', 'roa_yearly', 'roa_dp', 'fixed_assets', 
            'profit_to_op', 'q_saleexp_to_gr', 'q_gc_to_gr', 'q_roe', 'q_dt_roe', 'q_npta', 'q_ocf_to_sales', 'basic_eps_yoy', 'dt_eps_yoy', 'cfps_yoy', 
            'op_yoy', 'ebt_yoy', 'netprofit_yoy', 'dt_netprofit_yoy', 'ocf_yoy', 'roe_yoy', 'bps_yoy', 'assets_yoy', 'eqt_yoy', 'tr_yoy', 'or_yoy', 
            'q_sales_yoy', 'q_op_qoq', 'equity_yoy', 'update_flag'
        ]
        fields_str = ','.join(fields_list)
        
        try:
            report_log("正在清空基本面表 (stock_financial_indicators)...", "info")
            db_lock.lockForWrite()
            conn.execute("DELETE FROM stock_financial_indicators")
            conn.commit() # [v20.1] 提交 DELETE
            report_log("清空完成，即将开始批量插入...", "success")
        except Exception as e_del:
            # [v20.3] 移除 conn.rollback()
            report_log(f"清空 stock_financial_indicators 表失败: {e_del}", "error")
        finally:
            db_lock.unlock()
            
        # [核心] 循环采集每个季度的数据
        for i, q_date in enumerate(q_dates):
            if stop_event.is_set():
                report_log("用户停止 (采集期间)。", "warning")
                raise InterruptedError("用户停止")
            
            progress = int(((i + 1) / len(q_dates)) * 100)
            report_progress(progress)
            report_status(True, f"获取 {q_date} ({i+1}/{len(q_dates)})...")
            
            report_log(f"正在获取 {q_date} 报告期的所有A股数据 (pro.fina_indicator)...", "info")
            
            try:
                # 1. 调用 Tushare 接口 (带重试)
                df = fetch_with_retry(pro.fina_indicator, period=q_date, fields=fields_str)

                if df is not None and not df.empty:
                    # 2. 写入 DuckDB
                    db_lock.lockForWrite()
                    try:
                        # 动态注册为临时视图，方便使用 SQL 插入
                        conn.register('temp_fina_df', df)
                        
                        # 构建插入 SQL，确保列匹配
                        # 使用列表推导式筛选出确实存在的列
                        valid_cols = [c for c in df.columns if c in fields_list]
                        cols_sql = ', '.join([f'"{c}"' for c in valid_cols])
                        
                        insert_query = f"""
                            INSERT INTO stock_financial_indicators ({cols_sql}) 
                            SELECT {cols_sql} FROM temp_fina_df
                        """
                        conn.execute(insert_query)
                        
                        conn.unregister('temp_fina_df')
                        conn.commit()
                        
                        count = len(df)
                        total_saved_rows += count
                        report_log(f"成功保存 {q_date} 数据: {count} 条。", "success")
                        
                    except Exception as e_db:
                        report_log(f"写入 {q_date} 数据失败: {e_db}", "error")
                    finally:
                        db_lock.unlock()
                else:
                    report_log(f"警告: {q_date} 未获取到数据或数据为空。", "warning")

            except Exception as e_fetch:
                report_log(f"获取 {q_date} 数据时发生API错误: {e_fetch}", "error")

            # 礼貌休眠，防止触发 Tushare 频率限制
            time.sleep(random.uniform(0.5, 1.0))

        report_complete("完成", total_saved_rows)

    except InterruptedError:
        report_log("基本面采集被用户停止。", "warning")
        report_complete("已停止", total_saved_rows)
    except Exception as e:
        report_log(f"基本面采集发生严重错误: {e}", "error")
        traceback.print_exc()
        report_complete("错误", total_saved_rows)
    finally:
        report_status(False, "空闲")