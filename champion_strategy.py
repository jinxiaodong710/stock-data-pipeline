#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多板块冠军策略 v1.43 (ST去未来函数+板块字段兼容版)
- 核心逻辑: 多板块冠军共振 + 漏斗筛选。
- [修复] 彻底移除 ST 未来函数:
  - 之前版本使用“最新名字”判断 ST，会导致回测买入“历史上是 ST 但现在摘帽”的股票。
  - 本版本加载 stock_st_history 表，使用“历史真实状态”进行过滤。
- 功能保持: 股性分筛选、自定义参数等保持不变。
- [修复] 兼容 ths_concept_members 缺少 concept_list_date、股票代码字段名不同的问题。
"""

import sys
from datetime import datetime, timedelta
import json

import duckdb
import numpy as np
import pandas as pd

from PySide6.QtCore import Qt, QThread, Signal, QDate
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QGroupBox, QGridLayout, QDateEdit, QSpinBox, QDoubleSpinBox,
    QPushButton, QTextEdit, QMessageBox, QComboBox, QFileDialog
)

try:
    import openpyxl
except ImportError:
    OPENPYXL_AVAILABLE = False
else:
    OPENPYXL_AVAILABLE = True

# 【重要】请确认数据库路径
DB_PATH = 'f:\\stock\\stock_data.duckdb'  
LOT_SIZE = 100

def to_str_date(qdate: QDate) -> str:
    return qdate.toString("yyyy-MM-dd")

# --- AI打分计算函数 (原有) ---
def calculate_ai_score(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty: return df
    temp = df.copy()
    
    sector_val = temp['max_sector_pct'].astype(float)
    mv_val = np.log1p(temp['total_mv'].astype(float))
    stock_val = temp['pct_change'].astype(float)
    
    def map_vol_score(v):
        if v <= 1.5: return 1.0
        if v <= 2.0: return 1.2
        if v <= 2.5: return 0.4
        if v <= 3.0: return 1.0
        if v <= 4.0: return 1.8
        if v <= 5.0: return -1.0
        if v <= 10.0: return 2.1
        return 0.0

    vol_score_raw = temp['vol_ratio'].apply(map_vol_score)

    def get_z_score(series):
        if len(series) < 2 or series.std() == 0: return np.zeros(len(series))
        return (series - series.mean()) / series.std()

    z_sector = get_z_score(sector_val)
    z_mv = get_z_score(mv_val)
    z_stock = get_z_score(stock_val)
    z_vol = get_z_score(vol_score_raw)

    temp['ai_score'] = (0.1 * z_sector) + (-0.8* z_mv) + (-0.1 * z_stock) + (0.028 * z_vol)
    return temp['ai_score']

# --- 股性评分计算函数 ---
def calculate_guxing_score(df: pd.DataFrame, dm, date_idx: int, window: int) -> pd.DataFrame:
    if df.empty: return pd.Series(dtype=float)
    stats = []
    for code in df['stock_code']:
        avg_turn, avg_amp, momentum = dm.get_guxing_stats(code, date_idx, window)
        stats.append({'avg_turn': avg_turn, 'avg_amp': avg_amp, 'momentum': momentum})
    stats_df = pd.DataFrame(stats, index=df.index)
    score_turn = stats_df['avg_turn'].rank(pct=True) * 100
    score_amp = stats_df['avg_amp'].rank(pct=True) * 100
    score_mom = stats_df['momentum'].rank(pct=True) * 100
    final_score = (0.4 * score_turn) + (0.3 * score_amp) + (0.3 * score_mom)
    return final_score

# --- 数据管理类 ---
class DataManager:
    def __init__(self, log_signal):
        self.log = log_signal
        self.prices = pd.DataFrame()
        self.indices = pd.DataFrame() 
        self.trading_dates = []
        self.names = {}
        self.concepts = pd.DataFrame()
        # [新增] 存储历史ST状态的集合 {(date, code), ...}
        self.st_history_set = set() 

    def load_st_history(self, conn):
        """加载ST历史数据到内存集合，用于快速判断"""
        self.log.emit("[加载] 读取 ST 历史状态 (去除未来函数)...")
        try:
            # 检查表是否存在
            check = conn.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'stock_st_history'").fetchone()
            if check[0] == 0:
                self.log.emit("<b style='color:orange;'>警告: 未找到 stock_st_history 表！将回退到使用当前名字判断(存在未来函数)。请运行采集器 v20.8 更新数据。</b>")
                return

            # 读取所有 ST 记录
            df_st = conn.execute("SELECT trade_date, ts_code FROM stock_st_history").fetchdf()
            if not df_st.empty:
                # 构建 (date_str, code_str) 的集合
                # 确保 ts_code 是 000001.SZ 格式 或者 000001 格式，这里统一处理
                # 假设数据库存的是 '2022-01-01', '000001.SZ'
                # 我们的 self.names 和 self.prices 里的 code 通常不带后缀或者已清洗
                # 这里为了匹配 self.prices (无后缀)
                
                # 统一转换为无后缀格式以便匹配
                df_st['stock_code'] = df_st['ts_code'].str.split('.').str[0].str.strip()
                df_st['trade_date'] = pd.to_datetime(df_st['trade_date']).dt.strftime('%Y-%m-%d')
                
                # 将 DataFrame 转为 set of tuples: {('2022-01-01', '000001'), ...}
                self.st_history_set = set(zip(df_st['trade_date'], df_st['stock_code']))
                
                self.log.emit(f"[系统] 已加载 {len(self.st_history_set)} 条 ST 历史记录")
        except Exception as e:
            self.log.emit(f"加载 ST 历史失败: {e}")

    def is_st(self, code: str, date: str) -> bool:
        """
        判断某股在某日是否为 ST
        优先使用 history_set (无未来函数)，如果表为空则回退到名字判断 (有未来函数)
        """
        if self.st_history_set:
            # 精确匹配历史数据
            return (date, code) in self.st_history_set
        else:
            # 回退模式：检查名字
            name = self.names.get(code, '')
            return 'ST' in name.upper()

    def load_basics(self, conn, start_date: str, end_date: str, market_type: str, need_buffer_days: int = 100):
        self.log.emit("[加载] 读取交易日历...")
        start_dt = pd.to_datetime(start_date) - timedelta(days=max(need_buffer_days, 150) * 2 + 60) 
        start_with_buf = start_dt.strftime("%Y-%m-%d")
        
        df_dates = conn.execute(
            "SELECT DISTINCT date FROM stock_prices WHERE date >= ? AND date <= ? ORDER BY date",
            [start_with_buf, end_date]
        ).fetchdf()
        if df_dates.empty: raise ValueError(f"数据库中未找到 {start_with_buf} 至 {end_date} 的交易日")
        self.trading_dates = df_dates['date'].tolist()

        self.log.emit(f"[加载] 读取全市场价格数据...")
        market_condition = "(stock_code LIKE '30%%' OR stock_code LIKE '00%%' OR stock_code LIKE '60%%')"
        
        self.prices = conn.execute(
            f"SELECT date, stock_code, open, close, total_mv, pe, volume, turnover_rate, amplitude FROM stock_prices WHERE date >= ? AND date <= ? AND ({market_condition})",
            [self.trading_dates[0], self.trading_dates[-1]]
        ).fetchdf()
        if self.prices.empty: raise ValueError(f"价格表为空")

        if 'total_mv' in self.prices.columns:
            self.prices['total_mv'] = pd.to_numeric(self.prices['total_mv'], errors='coerce') * 10000
        
        self.prices['stock_code'] = self.prices['stock_code'].str.strip()
        self.prices.set_index(['date', 'stock_code'], inplace=True)
        self.prices.sort_index(inplace=True)

        self.log.emit("[加载] 读取股票名称...")
        try:
            df_names = conn.execute("SELECT DISTINCT stock_code, stock_name FROM stock_fundamentals").fetchdf()
            df_names['stock_code'] = df_names['stock_code'].str.strip()
            self.names = df_names.drop_duplicates('stock_code').set_index('stock_code')['stock_name'].to_dict()
        except Exception: self.names = {}
        
        # [新增] 在此处加载 ST 历史
        self.load_st_history(conn)

        self.log.emit("[加载] 读取指数数据 (index_prices)...")
        try:
            idx_query = """
                SELECT date, index_code as stock_code, close 
                FROM index_prices 
                WHERE index_code IN ('000001', '399001', '399006')
                  AND date >= ? AND date <= ?
            """
            try:
                self.indices = conn.execute(idx_query, [self.trading_dates[0], self.trading_dates[-1]]).fetchdf()
                if not self.indices.empty:
                    self.indices['stock_code'] = self.indices['stock_code'].str.strip()
                    self.indices['date'] = pd.to_datetime(self.indices['date'])
                    self.indices.sort_values('date', inplace=True)
            except duckdb.BinderException: pass
            except duckdb.CatalogException: pass
        except Exception: pass

        self.log.emit("[加载] 读取板块数据...")
        try:
            # 兼容不同版本的 ths_concept_members 表结构：
            # 老库可能没有 concept_list_date；股票代码字段也可能叫 con_code / ts_code / code / stock_code。
            # 这里动态读取实际字段，避免 DuckDB Binder Error。
            cols_df = conn.execute("PRAGMA table_info('ths_concept_members')").fetchdf()
            col_names = set(cols_df['name'].tolist())

            if 'concept_name' in col_names:
                concept_col = 'concept_name'
            elif 'con_name' in col_names:
                concept_col = 'con_name'
            else:
                raise ValueError("ths_concept_members 缺少 concept_name/con_name 字段")

            if 'con_code' in col_names:
                stock_col = 'con_code'
            elif 'ts_code' in col_names:
                stock_col = 'ts_code'
            elif 'code' in col_names:
                stock_col = 'code'
            elif 'stock_code' in col_names:
                stock_col = 'stock_code'
            else:
                raise ValueError("ths_concept_members 缺少 con_code/ts_code/code/stock_code 字段")

            if 'concept_list_date' in col_names:
                date_expr = 'concept_list_date'
            else:
                date_expr = "'1990-01-01' AS concept_list_date"

            df_con = conn.execute(f"""
                SELECT
                    {concept_col} AS concept_name,
                    {stock_col} AS raw_stock_code,
                    {date_expr}
                FROM ths_concept_members
            """).fetchdf()

            if not df_con.empty:
                df_con['stock_code'] = df_con['raw_stock_code'].astype(str).str.split('.').str[0].str.strip()
                df_con['concept_list_date'] = pd.to_datetime(
                    df_con['concept_list_date'],
                    errors='coerce'
                ).fillna(pd.Timestamp('1990-01-01')).dt.strftime('%Y-%m-%d')
                df_con = df_con[['concept_name', 'stock_code', 'concept_list_date']]
            else:
                df_con = pd.DataFrame(columns=['concept_name', 'stock_code', 'concept_list_date'])

            try:
                # stock_basic_info 也做轻量兼容：如果 list_date 不存在，就用默认日期。
                basic_cols_df = conn.execute("PRAGMA table_info('stock_basic_info')").fetchdf()
                basic_cols = set(basic_cols_df['name'].tolist())
                if 'list_date' in basic_cols:
                    df_basic = conn.execute("SELECT ts_code, industry, area, list_date FROM stock_basic_info").fetchdf()
                else:
                    df_basic = conn.execute("SELECT ts_code, industry, area, '1990-01-01' AS list_date FROM stock_basic_info").fetchdf()

                df_ind = df_basic[['industry', 'ts_code', 'list_date']].copy().dropna(subset=['industry'])
                df_ind.columns = ['concept_name', 'stock_code', 'concept_list_date']
                df_ind['concept_name'] = '行业-' + df_ind['concept_name'].astype(str)
                df_ind['stock_code'] = df_ind['stock_code'].astype(str).str.split('.').str[0].str.strip()
                df_ind['concept_list_date'] = pd.to_datetime(df_ind['concept_list_date'], errors='coerce').fillna(pd.Timestamp('1990-01-01')).dt.strftime('%Y-%m-%d')

                df_area = df_basic[['area', 'ts_code', 'list_date']].copy().dropna(subset=['area'])
                df_area.columns = ['concept_name', 'stock_code', 'concept_list_date']
                df_area['concept_name'] = '地区-' + df_area['concept_name'].astype(str)
                df_area['stock_code'] = df_area['stock_code'].astype(str).str.split('.').str[0].str.strip()
                df_area['concept_list_date'] = pd.to_datetime(df_area['concept_list_date'], errors='coerce').fillna(pd.Timestamp('1990-01-01')).dt.strftime('%Y-%m-%d')

                self.concepts = pd.concat([df_con, df_ind, df_area], ignore_index=True)
            except Exception as e:
                self.log.emit(f"<b style='color:orange;'>行业/地区数据读取失败，仅使用同花顺概念板块：{e}</b>")
                self.concepts = df_con 

            self.concepts = self.concepts.dropna(subset=['stock_code']).drop_duplicates()
            self.log.emit(f"[系统] 已加载板块/行业/地区映射 {len(self.concepts)} 条")
        except Exception as e:
            raise ValueError(f"读取板块数据失败: {e}")

    def get_price(self, code: str, date: str, price_type='close'):
        try:
            v = self.prices.loc[(date, code), price_type]
            return float(v) if pd.notna(v) and v > 0 else None
        except KeyError:
            return None

    def get_volume_ratio(self, code: str, date_idx: int, window: int = 5) -> float:
        if date_idx < window: return 0.0
        try:
            curr_date = self.trading_dates[date_idx]
            try:
                curr_vol = float(self.prices.loc[(curr_date, code), 'volume'])
            except KeyError:
                return 0.0
            if curr_vol <= 0: return 0.0
            past_vols = []
            for i in range(1, window + 1):
                d = self.trading_dates[date_idx - i]
                try:
                    v = float(self.prices.loc[(d, code), 'volume'])
                    if v > 0: past_vols.append(v)
                except KeyError: pass
            if not past_vols: return 0.0
            avg_vol = sum(past_vols) / len(past_vols)
            if avg_vol <= 0: return 0.0
            return round(curr_vol / avg_vol, 2)
        except Exception: return 0.0

    def get_guxing_stats(self, code: str, date_idx: int, window: int):
        if date_idx < window: return 0, 0, 0
        curr_date = self.trading_dates[date_idx]
        try: curr_close = float(self.prices.loc[(curr_date, code), 'close'])
        except KeyError: return 0, 0, 0
        start_date = self.trading_dates[date_idx - window]
        try: start_close = float(self.prices.loc[(start_date, code), 'close'])
        except KeyError: start_close = curr_close 
        momentum = (curr_close / start_close - 1.0) * 100 if start_close > 0 else 0
        turnovers, amplitudes = [], []
        count = 0
        for i in range(window):
            d = self.trading_dates[date_idx - i]
            try:
                row = self.prices.loc[(d, code)]
                t = float(row['turnover_rate']) if pd.notna(row['turnover_rate']) else 0
                a = float(row['amplitude']) if pd.notna(row['amplitude']) else 0
                turnovers.append(t); amplitudes.append(a); count += 1
            except KeyError: pass
        avg_turn = sum(turnovers) / count if count > 0 else 0
        avg_amp = sum(amplitudes) / count if count > 0 else 0
        return avg_turn, avg_amp, momentum

    def _build_today_pct(self, today: str, prev_date: str) -> pd.DataFrame:
        try:
            df_t = self.prices.loc[today].reset_index()[['stock_code', 'close', 'total_mv', 'pe']]
            df_p = self.prices.loc[prev_date].reset_index()[['stock_code', 'close']]
            df = pd.merge(df_t, df_p, on='stock_code', how='inner', suffixes=('_t', '_p'))
            df = df[(df['close_t'] > 0) & (df['close_p'] > 0)]
            if df.empty: return pd.DataFrame()
            df['pct_change'] = (df['close_t'] / df['close_p'] - 1.0) * 100.0
            return df[['stock_code', 'pct_change', 'close_t', 'total_mv', 'pe']]
        except KeyError: return pd.DataFrame()

    def _sector_champions(self, df_today_pct: pd.DataFrame, current_date: str) -> pd.DataFrame:
        df = self.concepts.merge(df_today_pct, on='stock_code', how='inner')
        if df.empty: return pd.DataFrame()
        cur_date_str = str(current_date)[:10]
        df = df[df['concept_list_date'] <= cur_date_str]
        if df.empty: return pd.DataFrame()
        idx = df.groupby('concept_name')['pct_change'].idxmax()
        return df.loc[idx, ['concept_name', 'stock_code', 'pct_change', 'total_mv', 'pe']].reset_index(drop=True)

# --- 回测线程 ---
class BacktestThread(QThread):
    progress = Signal(str); finished = Signal(dict); error = Signal(str)
    def __init__(self, params): super().__init__(); self.p = params; self.dm = DataManager(self.progress)
    def run(self):
        try:
            conn = duckdb.connect(DB_PATH, read_only=True)
            buffer_days = max(100, self.p.get('guxing_days', 20) + 20)
            self.dm.load_basics(conn, self.p['start_date'], self.p['end_date'], self.p['market_type'], need_buffer_days=buffer_days)
            all_dates = self.dm.trading_dates
            
            bt_idx = [i for i, d in enumerate(all_dates) if self.p['start_date'] <= d <= self.p['end_date']]
            if not bt_idx: raise ValueError("指定回测区间内无交易日")

            cash = float(self.p['initial_cash']); positions = {}; daily_assets = []; trade_log = []
            overbought_factor = self.p['overbought_factor'] / 100.0; fee_rate = self.p.get('fee_rate', 0.0) / 100.0 
            min_guxing_score = self.p.get('min_guxing_score', 0)
            guxing_window = self.p.get('guxing_days', 20)

            for i in range(bt_idx[0], bt_idx[-1] + 1):
                if self.isInterruptionRequested(): self.progress.emit("[系统] 收到停止请求。"); break
                today = all_dates[i]
                is_last_backtest_day = (i == bt_idx[-1])

                # --- 卖出逻辑 ---
                to_sell_today_close = []
                drawdown_factor = self.p.get('drawdown_sell_factor', 1.0)
                hold_pct_req = self.p.get('hold_pct_req', -100.0)
                stop_loss_pct = self.p.get('stop_loss_pct', 0.0) / 100.0

                for code in list(positions.keys()):
                    pos = positions[code]
                    current_price = self.dm.get_price(code, today, 'close')
                    if not current_price:
                        if pos['sell_date'] == today: pos['sell_date'] = all_dates[min(i + 1, len(all_dates) - 1)]
                        continue
                    pos['highest_price'] = max(pos.get('highest_price', pos['buy_price']), current_price)
                    sell_reason = None
                    try: days_held = i - all_dates.index(pos['buy_date'])
                    except ValueError: continue

                    if days_held == 1 and stop_loss_pct > 0 and current_price < pos['buy_price'] * (1 - stop_loss_pct):
                        sell_reason = "T+2止损"
                    if not sell_reason and days_held >= 2 and drawdown_factor < 1.0:
                        if current_price < pos['highest_price'] * drawdown_factor: sell_reason = "回撤止盈"
                        else:
                            prev_day_idx = i - 1
                            if prev_day_idx >= 0:
                                prev_price = self.dm.get_price(code, all_dates[prev_day_idx], 'close')
                                if prev_price and prev_price > 0 and (current_price / prev_price - 1.0) * 100.0 <= hold_pct_req:
                                    sell_reason = f"涨幅未达标"
                    
                    if not sell_reason and pos['sell_date'] == today and not is_last_backtest_day: 
                        sell_reason = "到期卖出"
                    if sell_reason: to_sell_today_close.append((code, current_price, sell_reason))

                for code, sell_p, reason in to_sell_today_close:
                    if code not in positions: continue
                    pos = positions.pop(code)
                    proceeds = sell_p * pos['shares']; fee = proceeds * fee_rate; net_proceeds = proceeds - fee
                    profit = net_proceeds - (pos['shares'] * pos['buy_price']); cash += net_proceeds
                    buy_cost = pos['shares'] * pos['buy_price']
                    profit_rate = profit / buy_cost if buy_cost > 0 else 0
                    try: holding_days = all_dates.index(today) - all_dates.index(pos['buy_date']) + 1
                    except: holding_days = 0
                    
                    mv = sum(p['shares'] * (self.dm.get_price(c, today, 'close') or p['buy_price']) for c, p in positions.items())
                    total_assets = cash + mv
                    cumulative_return = (total_assets / self.p['initial_cash'] - 1.0) * 100

                    trade_log.append({
                        'code': code, 'name': f"{self.dm.names.get(code, code)}({reason})",
                        'buy_date': pos['buy_date'], 'buy_price': pos['buy_price'],
                        'buy_day_close': pos.get('buy_day_close', 0),
                        'buy_day_pct': pos.get('buy_day_pct', 0),
                        'sell_date': today, 'sell_price': sell_p, 'shares': pos['shares'],
                        'fee': fee, 'profit': profit,
                        'holding_days': holding_days, 'profit_rate': profit_rate,
                        'cumulative_return': cumulative_return,
                        'total_mv': pos.get('total_mv', 0), 'pe': pos.get('pe', 0),
                        'max_sector_pct': pos.get('max_sector_pct', 0),
                        'sector_name': pos.get('sector_name', '-'),
                        'guxing_score': pos.get('guxing_score', 0),
                        'selection_pct': pos.get('stock_pct_change', 0),
                        'vol_ratio': pos.get('vol_ratio', 0)
                    })
                
                mv = sum(pos['shares'] * (self.dm.get_price(c, today, 'close') or pos['buy_price']) for c, pos in positions.items())
                total_assets = cash + mv
                daily_assets.append({'date': today, 'total_assets': total_assets, 'cash': cash, 'held_stocks': {code: pos['shares'] for code, pos in positions.items()}})

                if i - 1 < 0: continue
                prev_date = all_dates[i - 1]
                
                # --- 选股逻辑 ---
                df_today_pct = self.dm._build_today_pct(today, prev_date)
                if df_today_pct.empty: continue
                
                # [核心修正] 使用历史真实数据过滤 ST (移除未来函数)
                # 使用 self.dm.is_st(code, today) 检查
                # 注意：这里需要过滤掉今天是 ST 的股票
                df_today_pct = df_today_pct[~df_today_pct['stock_code'].apply(lambda c: self.dm.is_st(c, today))]
                
                df_today_pct = df_today_pct[df_today_pct['pct_change'].between(self.p['min_pct_change'], self.p['max_pct_change'])]
                if df_today_pct.empty: continue

                champs = self.dm._sector_champions(df_today_pct, today)
                if champs.empty: continue

                counts = champs['stock_code'].value_counts().reset_index()
                counts.columns = ['stock_code', 'champion_count']
                multi = counts[counts['champion_count'].between(self.p['champion_min_count'], self.p['champion_max_count'])]
                if multi.empty: continue
                
                champs.rename(columns={'pct_change': 'sector_champion_pct'}, inplace=True)
                stock_concepts = self.dm.concepts[self.dm.concepts['stock_code'].isin(multi['stock_code'])]
                stock_sector_champs = stock_concepts.merge(champs[['concept_name', 'sector_champion_pct']], on='concept_name', how='left')
                stock_sector_champs.sort_values('sector_champion_pct', ascending=False, inplace=True)
                best_sector_info = stock_sector_champs.drop_duplicates(subset=['stock_code'])[['stock_code', 'concept_name', 'sector_champion_pct']]
                best_sector_info.rename(columns={'sector_champion_pct': 'max_sector_pct', 'concept_name': 'best_sector_name'}, inplace=True)

                multi = multi.merge(df_today_pct[['stock_code', 'pct_change', 'total_mv', 'pe', 'close_t']], on='stock_code', how='left')
                multi = multi.merge(best_sector_info, on='stock_code', how='left')
                multi['max_sector_pct'].fillna(0, inplace=True); multi['best_sector_name'].fillna('-', inplace=True)

                multi = multi[multi['max_sector_pct'].between(self.p['min_sector_pct'], self.p['max_sector_pct'])]
                multi = multi[multi['total_mv'].between(self.p['min_mv'] * 1e8, self.p['max_mv'] * 1e8)]
                multi = multi[multi['pct_change'].between(self.p['min_stock_pct'], self.p['max_stock_pct'])]
                
                check_days = [5, 15, 60]; valid_candidates = []
                for _, stock in multi.iterrows():
                    code = stock['stock_code']; current_close = stock['close_t']; is_overbought = False
                    for days_ago in check_days:
                        hist_idx = i - days_ago
                        if hist_idx >= 0:
                            hist_close = self.dm.get_price(code, all_dates[hist_idx], 'close')
                            if hist_close and hist_close > 0 and current_close > hist_close * overbought_factor: is_overbought = True; break
                    if not is_overbought: valid_candidates.append(stock)
                
                if not valid_candidates: continue
                multi_filtered = pd.DataFrame(valid_candidates)
                user_market = self.p['market_type']
                if user_market == '仅创业板': multi_filtered = multi_filtered[multi_filtered['stock_code'].str.startswith('30')]
                elif user_market == '仅主板': multi_filtered = multi_filtered[multi_filtered['stock_code'].str.startswith(('00', '60'))]
                if multi_filtered.empty: continue

                multi_filtered['vol_ratio'] = multi_filtered['stock_code'].apply(lambda c: self.dm.get_volume_ratio(c, i, 5))
                multi_filtered = multi_filtered[multi_filtered['vol_ratio'] <= 30]
                if multi_filtered.empty: continue

                multi_filtered['guxing_score'] = calculate_guxing_score(multi_filtered, self.dm, i, guxing_window)
                if min_guxing_score > 0:
                    multi_filtered = multi_filtered[multi_filtered['guxing_score'] >= min_guxing_score]
                
                if multi_filtered.empty: continue

                rank_mode = self.p.get('rank_mode', '按市值排序')
                if rank_mode == 'AI打分排序':
                    multi_filtered['ai_score'] = calculate_ai_score(multi_filtered)
                    multi_filtered.sort_values(by='ai_score', ascending=False, inplace=True)
                elif rank_mode == '按股性排序':
                    multi_filtered.sort_values(by='guxing_score', ascending=False, inplace=True)
                else:
                    multi_filtered['ai_score'] = 0 
                    multi_filtered.sort_values(by='total_mv', ascending=True, inplace=True)

                holding_days = self.p['holding_days']; t1_idx = i + 1; sell_idx = t1_idx + holding_days
                if t1_idx >= len(all_dates): continue 

                buy_date = all_dates[t1_idx]
                if sell_idx >= len(all_dates): sell_date = all_dates[-1]
                else: sell_date = all_dates[sell_idx]

                slots_to_fill = max(0, self.p['num_slots'] - len(positions))
                if slots_to_fill > 0 and cash > 1000:
                    buy_list = multi_filtered[~multi_filtered['stock_code'].isin(positions.keys())].head(slots_to_fill)
                    if not buy_list.empty:
                        cash_per_slot = cash / slots_to_fill
                        for _, row in buy_list.iterrows():
                            code = row['stock_code']
                            open_p = self.dm.get_price(code, buy_date, 'open')
                            if not open_p: continue
                            
                            buy_close_p = self.dm.get_price(code, buy_date, 'close')
                            buy_pct = 0.0
                            if buy_close_p and row['close_t'] > 0:
                                buy_pct = (buy_close_p / row['close_t'] - 1.0) * 100.0

                            # --- [核心修正] 买入前再次进行 PIT (Point-in-Time) 检查 ---
                            # 使用 ST 历史表检查买入日当天是否为 ST
                            if self.dm.is_st(code, buy_date): 
                                continue

                            # 仅跳过“ST一字板/封死涨停”
                            if (abs(open_p - buy_close_p) < 0.01) and (4.5 <= buy_pct <= 5.5):
                                continue

                            if row['close_t'] > 0 and self.p['open_pct_limit'] > 0:
                                if (open_p / row['close_t'] - 1.0) * 100.0 > self.p['open_pct_limit']: continue
                            
                            shares = int(cash_per_slot / open_p / LOT_SIZE) * LOT_SIZE
                            if shares <= 0 or shares * open_p > cash: continue
                            cash -= shares * open_p
                            
                            positions[code] = {
                                'shares': shares, 'buy_price': open_p, 'buy_date': buy_date, 'sell_date': sell_date,
                                'highest_price': open_p, 'stock_pct_change': row.get('pct_change', 0),
                                'total_mv': row.get('total_mv', 0), 'pe': row.get('pe', 0),
                                'max_sector_pct': row.get('max_sector_pct', 0), 'sector_name': row.get('best_sector_name', '-'),
                                'ai_score': row.get('ai_score', 0),
                                'guxing_score': row.get('guxing_score', 0),
                                'vol_ratio': row.get('vol_ratio', 0),
                                'buy_day_close': buy_close_p,
                                'buy_day_pct': buy_pct
                            }

            last_day = all_dates[bt_idx[-1]]
            mv_final = sum(p['shares'] * (self.dm.get_price(c, last_day, 'close') or p['buy_price']) for c, p in positions.items())
            total_assets_final = cash + mv_final
            cumulative_return_final = (total_assets_final / self.p['initial_cash'] - 1.0) * 100
            
            for code, pos in positions.items():
                current_price = self.dm.get_price(code, last_day, 'close') or pos['buy_price']
                buy_cost = pos['shares'] * pos['buy_price']
                profit = (current_price - pos['buy_price']) * pos['shares']
                profit_rate = profit / buy_cost if buy_cost > 0 else 0
                
                trade_log.append({
                    'code': code, 'name': f"{self.dm.names.get(code, code)}(持股中)",
                    'buy_date': pos['buy_date'], 'buy_price': pos['buy_price'],
                    'buy_day_close': pos.get('buy_day_close', 0),
                    'buy_day_pct': pos.get('buy_day_pct', 0),
                    'sell_date': last_day, 'sell_price': current_price,
                    'shares': pos['shares'],
                    'fee': 0,
                    'profit': profit,
                    'holding_days': (pd.to_datetime(last_day) - pd.to_datetime(pos['buy_date'])).days + 1,
                    'profit_rate': profit_rate,
                    'cumulative_return': cumulative_return_final,
                    'total_mv': pos.get('total_mv', 0), 'pe': pos.get('pe', 0),
                    'max_sector_pct': pos.get('max_sector_pct', 0),
                    'sector_name': pos.get('sector_name', '-'),
                    'guxing_score': pos.get('guxing_score', 0),
                    'selection_pct': pos.get('stock_pct_change', 0),
                    'vol_ratio': pos.get('vol_ratio', 0)
                })

            self.finished.emit({'daily_assets': daily_assets, 'trade_log': trade_log, 'initial_cash': self.p['initial_cash'], 'indices': self.dm.indices})
        except Exception as e:
            import traceback; traceback.print_exc(); self.error.emit(f"{e}\n(位置: {e.__traceback__.tb_lineno})")

# --- 漏斗选股线程 ---
class SuggestionThread(QThread):
    progress = Signal(str); finished = Signal(object); error = Signal(str)
    def __init__(self, params): super().__init__(); self.p = params; self.dm = DataManager(self.progress)
    def run(self):
        try:
            target_date_str = self.p['target_date']
            self.progress.emit(f"正在定位日期: {target_date_str}")

            conn = duckdb.connect(DB_PATH, read_only=True)
            date_check = conn.execute("SELECT MAX(date) FROM stock_prices WHERE date <= ?", [target_date_str]).fetchone()
            if not date_check or not date_check[0]:
                raise ValueError(f"数据库中没有 {target_date_str} 或之前的数据")
            
            actual_date = date_check[0]
            if actual_date != target_date_str:
                self.progress.emit(f"提示: 指定日期非交易日，自动使用最近交易日: <b>{actual_date}</b>")
            
            buffer_days = max(120, self.p.get('guxing_days', 20) + 20)
            start_load = (pd.to_datetime(actual_date) - timedelta(days=buffer_days)).strftime('%Y-%m-%d')
            self.dm.load_basics(conn, start_load, actual_date, self.p['market_type'], 0)
            
            all_dates = self.dm.trading_dates
            if actual_date not in all_dates:
                raise ValueError("日期定位错误，无法加载数据")
            
            curr_idx = all_dates.index(actual_date)
            if curr_idx == 0:
                raise ValueError("所选日期是数据库第一天，无法计算涨跌幅")
            
            prev_date = all_dates[curr_idx - 1]
            funnel_log = []

            df = self.dm._build_today_pct(actual_date, prev_date)
            funnel_log.append(("初始全市场数据 (有成交)", len(df)))
            
            if self.p['market_type'] == '仅创业板':
                df = df[df['stock_code'].str.startswith('30')]
            elif self.p['market_type'] == '仅主板':
                df = df[df['stock_code'].str.startswith(('00', '60'))]
            funnel_log.append((f"市场过滤 ({self.p['market_type']})", len(df)))

            # [核心修正] 使用历史真实数据过滤 ST (建议模式下也要去未来函数)
            df = df[~df['stock_code'].apply(lambda c: self.dm.is_st(c, actual_date))]
            
            funnel_log.append(("剔除 ST 股 (PIT历史检查)", len(df)))

            df = df[df['pct_change'].between(self.p['min_pct_change'], self.p['max_pct_change'])]
            funnel_log.append((f"涨幅过滤 ({self.p['min_pct_change']}~{self.p['max_pct_change']}%)", len(df)))
            
            if df.empty:
                self._emit_result(pd.DataFrame(), funnel_log); return

            champs = self.dm._sector_champions(df, actual_date)
            if champs.empty:
                funnel_log.append(("板块冠军计算 (无结果)", 0))
                self._emit_result(pd.DataFrame(), funnel_log); return

            cnt = champs['stock_code'].value_counts().reset_index()
            cnt.columns = ['stock_code', 'cnt']
            
            multi = cnt[cnt['cnt'].between(self.p['champion_min_count'], self.p['champion_max_count'])]
            funnel_log.append((f"多板块冠军交集 ({self.p['champion_min_count']} ~ {self.p['champion_max_count']} 个板块)", len(multi)))
            
            if multi.empty:
                self._emit_result(pd.DataFrame(), funnel_log); return

            champs.rename(columns={'pct_change': 'sp'}, inplace=True)
            s_con = self.dm.concepts[self.dm.concepts['stock_code'].isin(multi['stock_code'])]
            s_champs = s_con.merge(champs[['concept_name', 'sp']], on='concept_name', how='left')
            s_champs.sort_values('sp', ascending=False, inplace=True)
            best_sector = s_champs.drop_duplicates(subset=['stock_code'])[['stock_code', 'concept_name', 'sp']]
            best_sector.rename(columns={'sp': 'max_sector_pct', 'concept_name': 'best_sector_name'}, inplace=True)

            final = multi.merge(df, on='stock_code').merge(best_sector, on='stock_code', how='left')
            final['max_sector_pct'].fillna(0, inplace=True)
            final['best_sector_name'].fillna('-', inplace=True)
            
            final = final[final['max_sector_pct'].between(self.p['min_sector_pct'], self.p['max_sector_pct'])]
            final = final[final['total_mv'].between(self.p['min_mv']*1e8, self.p['max_mv']*1e8)]
            final = final[final['pct_change'].between(self.p['min_stock_pct'], self.p['max_stock_pct'])]
            funnel_log.append((f"严格指标过滤 (板块涨幅/市值/个股涨幅)", len(final)))
            
            valid = []
            factor = self.p['overbought_factor'] / 100.0
            
            for _, row in final.iterrows():
                code = row['stock_code']; cp = row['close_t']; bad = False
                for d in [5, 15, 60]:
                    idx = curr_idx - d
                    if idx >= 0:
                        hp = self.dm.get_price(code, all_dates[idx], 'close')
                        if hp and hp > 0 and cp > hp * factor: bad=True; break
                if not bad: valid.append(row)
            
            funnel_log.append((f"剔除短期超买 (阈值 {self.p['overbought_factor']}%)", len(valid)))
            
            res = pd.DataFrame(valid)
            if not res.empty:
                res['vol_ratio'] = res['stock_code'].apply(lambda c: self.dm.get_volume_ratio(c, curr_idx, 5))
                pre_filter_len = len(res)
                res = res[res['vol_ratio'] <= 10]
                funnel_log.append((f"剔除异常量比 (>10)", len(res)))

                guxing_window = self.p.get('guxing_days', 20)
                min_guxing_score = self.p.get('min_guxing_score', 0)
                
                res['guxing_score'] = calculate_guxing_score(res, self.dm, curr_idx, guxing_window)
                
                if min_guxing_score > 0:
                    res = res[res['guxing_score'] >= min_guxing_score]
                    funnel_log.append((f"剔除低股性分 (<{min_guxing_score})", len(res)))

                if not res.empty:
                    rank_mode = self.p.get('rank_mode', '按市值排序')
                    if rank_mode == 'AI打分排序':
                        res['ai_score'] = calculate_ai_score(res)
                        res.sort_values('ai_score', ascending=False, inplace=True)
                    elif rank_mode == '按股性排序':
                        res.sort_values('guxing_score', ascending=False, inplace=True)
                    else:
                        res['ai_score'] = 0; res.sort_values('total_mv', ascending=True, inplace=True)
                    
                    res['stock_name'] = res['stock_code'].map(self.dm.names).fillna('-')
            
            self.finished.emit({'df': res, 'log': funnel_log, 'date': actual_date})
            
        except Exception as e:
            import traceback; traceback.print_exc()
            self.error.emit(str(e))

    def _emit_result(self, df, log):
        self.finished.emit({'df': df, 'log': log, 'date': self.p.get('target_date')})

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("多板块冠军策略 v1.43 (PIT去未来函数+板块字段兼容版)")
        self.setGeometry(100, 100, 1500, 950) 
        self.thread = None
        self.detailed_trade_log_df = None
        self._build_ui()

    def _build_ui(self):
        central = QWidget(); self.setCentralWidget(central); layout = QVBoxLayout(central)
        g_params = QGroupBox("参数配置"); 
        g_params.setStyleSheet("QGroupBox { font-weight: bold; font-size: 10pt; margin-top: 3px; } QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 2px 0 2px; }")
        
        v_params = QVBoxLayout(g_params)
        v_params.setContentsMargins(8, 15, 8, 8)
        v_params.setSpacing(6)

        def lbl(text): 
            l = QLabel(text); l.setFont(QFont("Microsoft YaHei", 8.5)); return l
        
        def add_space(h_layout, width=15):
            spacer = QLabel(" " * width)
            spacer.setMaximumWidth(width)
            h_layout.addWidget(spacer)

        # Row 0
        h0 = QHBoxLayout(); h0.setSpacing(2); h0.setContentsMargins(0,0,0,0)
        h0.addWidget(lbl("回测开始")); 
        self.dt_start = QDateEdit(QDate(2021, 1, 1), calendarPopup=True); self.dt_start.setMinimumHeight(24); self.dt_start.setMaximumWidth(110);
        h0.addWidget(self.dt_start)
        add_space(h0, 10)
        h0.addWidget(lbl("结束")); 
        self.dt_end = QDateEdit(QDate.currentDate(), calendarPopup=True); self.dt_end.setMinimumHeight(24); self.dt_end.setMaximumWidth(110);
        h0.addWidget(self.dt_end)
        add_space(h0, 10)
        h0.addWidget(lbl("资金")); 
        self.sp_cash = QSpinBox(); self.sp_cash.setRange(100000, 1000000000); self.sp_cash.setValue(500000); self.sp_cash.setSingleStep(100000); self.sp_cash.setMinimumHeight(24); self.sp_cash.setMaximumWidth(90);
        h0.addWidget(self.sp_cash)
        add_space(h0, 10)
        h0.addWidget(lbl("持仓")); 
        self.sp_slots = QSpinBox(); self.sp_slots.setRange(1, 50); self.sp_slots.setValue(4); self.sp_slots.setMinimumHeight(24); self.sp_slots.setMaximumWidth(50);
        h0.addWidget(self.sp_slots)
        add_space(h0, 10)
        h0.addWidget(lbl("天数")); 
        self.sp_holding_days = QSpinBox(); self.sp_holding_days.setValue(4); self.sp_holding_days.setMinimumHeight(24); self.sp_holding_days.setMaximumWidth(50);
        h0.addWidget(self.sp_holding_days)
        add_space(h0, 10)
        h0.addWidget(lbl("市场")); 
        self.combo_market = QComboBox(); self.combo_market.addItems(["创业板+主板", "仅创业板", "仅主板"]); self.combo_market.setMinimumHeight(24); self.combo_market.setMaximumWidth(100);
        h0.addWidget(self.combo_market)
        add_space(h0, 10)
        
        # 优选排序UI
        h0.addWidget(lbl("优选")); 
        self.combo_rank_mode = QComboBox(); 
        self.combo_rank_mode.addItems(["按市值排序", "AI打分排序", "按股性排序"]); 
        self.combo_rank_mode.setMinimumHeight(24); self.combo_rank_mode.setMaximumWidth(100);
        h0.addWidget(self.combo_rank_mode)

        # 股性参数 (天数 + 最低分)
        add_space(h0, 5)
        h0.addWidget(lbl("股性(天/分)"));
        # 股性天数
        self.sp_guxing_days = QSpinBox(); 
        self.sp_guxing_days.setRange(5, 120); 
        self.sp_guxing_days.setValue(100); 
        self.sp_guxing_days.setSingleStep(5);
        self.sp_guxing_days.setMinimumHeight(24); self.sp_guxing_days.setMaximumWidth(45);
        self.sp_guxing_days.setToolTip("计算股性评分的统计周期(天)")
        h0.addWidget(self.sp_guxing_days)
        
        h0.addWidget(QLabel("/"))
        
        # [新增] 最低股性分
        self.sp_min_guxing_score = QSpinBox();
        self.sp_min_guxing_score.setRange(0, 100);
        self.sp_min_guxing_score.setValue(18); # 默认过滤掉后30%
        self.sp_min_guxing_score.setSingleStep(10);
        self.sp_min_guxing_score.setMinimumHeight(24); self.sp_min_guxing_score.setMaximumWidth(45);
        self.sp_min_guxing_score.setToolTip("最低股性评分(0-100)，低于此分数的股票将被剔除")
        h0.addWidget(self.sp_min_guxing_score)

        h0.addStretch()
        v_params.addLayout(h0)

        # Row 1
        h1 = QHBoxLayout(); h1.setSpacing(2); h1.setContentsMargins(0,0,0,0)
        
        h1.addWidget(lbl("选股涨幅"))
        self.sp_min_pct = QDoubleSpinBox(); self.sp_min_pct.setRange(-20.0, 20.0); self.sp_min_pct.setValue(2.0); self.sp_min_pct.setMinimumHeight(24); self.sp_min_pct.setMaximumWidth(50);
        self.sp_max_pct = QDoubleSpinBox(); self.sp_max_pct.setRange(-20.0, 20.0); self.sp_max_pct.setValue(9.0); self.sp_max_pct.setMinimumHeight(24); self.sp_max_pct.setMaximumWidth(50);
        h1.addWidget(self.sp_min_pct); h1.addWidget(self.sp_max_pct)
        add_space(h1, 15)
        
        h1.addWidget(lbl("个股涨幅"))
        self.sp_min_stock_pct = QDoubleSpinBox(); self.sp_min_stock_pct.setRange(-20.0, 20.0); self.sp_min_stock_pct.setValue(0.0); self.sp_min_stock_pct.setMinimumHeight(24); self.sp_min_stock_pct.setMaximumWidth(50);
        self.sp_max_stock_pct = QDoubleSpinBox(); self.sp_max_stock_pct.setRange(-20.0, 20.0); self.sp_max_stock_pct.setValue(20.0); self.sp_max_stock_pct.setMinimumHeight(24); self.sp_max_stock_pct.setMaximumWidth(50);
        h1.addWidget(self.sp_min_stock_pct); h1.addWidget(self.sp_max_stock_pct)
        add_space(h1, 15)
        
        h1.addWidget(lbl("板块涨幅"))
        self.sp_min_sec = QDoubleSpinBox(); self.sp_min_sec.setValue(8.0); self.sp_min_sec.setMinimumHeight(24); self.sp_min_sec.setMaximumWidth(50);
        self.sp_max_sec = QDoubleSpinBox(); self.sp_max_sec.setValue(20.0); self.sp_max_sec.setMinimumHeight(24); self.sp_max_sec.setMaximumWidth(50);
        h1.addWidget(self.sp_min_sec); h1.addWidget(self.sp_max_sec)
        
        h1.addStretch()
        v_params.addLayout(h1)

        # Row 2
        h2 = QHBoxLayout(); h2.setSpacing(2); h2.setContentsMargins(0,0,0,0)
        
        h2.addWidget(lbl("市值(亿)"))
        self.sp_min_mv = QDoubleSpinBox(); self.sp_min_mv.setRange(0, 100000); self.sp_min_mv.setValue(0.0); self.sp_min_mv.setMinimumHeight(24); self.sp_min_mv.setMaximumWidth(50);
        self.sp_max_mv = QDoubleSpinBox(); self.sp_max_mv.setRange(0, 100000); self.sp_max_mv.setValue(30.0); self.sp_max_mv.setMinimumHeight(24); self.sp_max_mv.setMaximumWidth(50);
        h2.addWidget(self.sp_min_mv); h2.addWidget(self.sp_max_mv)
        add_space(h2, 15)

        h2.addWidget(lbl("冠军数"))
        self.sp_min_champ = QSpinBox(); self.sp_min_champ.setRange(1, 100); self.sp_min_champ.setValue(1); self.sp_min_champ.setMinimumHeight(24); self.sp_min_champ.setMaximumWidth(45);
        self.sp_max_champ = QSpinBox(); self.sp_max_champ.setRange(1, 100); self.sp_max_champ.setValue(10); self.sp_max_champ.setMinimumHeight(24); self.sp_max_champ.setMaximumWidth(45);
        h2.addWidget(self.sp_min_champ); h2.addWidget(self.sp_max_champ)
        add_space(h2, 15)
        
        h2.addWidget(lbl("超买%"))
        self.sp_overbought = QDoubleSpinBox(); self.sp_overbought.setMaximum(500.0); self.sp_overbought.setValue(150.0); self.sp_overbought.setSingleStep(5.0); self.sp_overbought.setMinimumHeight(24); self.sp_overbought.setMaximumWidth(50);
        h2.addWidget(self.sp_overbought)
        add_space(h2, 10)
        
        h2.addWidget(lbl("开盘%"))
        self.sp_open_pct_limit = QDoubleSpinBox(); self.sp_open_pct_limit.setValue(7); self.sp_open_pct_limit.setMinimumHeight(24); self.sp_open_pct_limit.setMaximumWidth(50);
        h2.addWidget(self.sp_open_pct_limit)
        add_space(h2, 10)
        
        h2.addWidget(lbl("费率%"))
        self.sp_fee_rate = QDoubleSpinBox(); self.sp_fee_rate.setDecimals(3); self.sp_fee_rate.setValue(0.15); self.sp_fee_rate.setMinimumHeight(24); self.sp_fee_rate.setMaximumWidth(50);
        h2.addWidget(self.sp_fee_rate)
        add_space(h2, 15)
        
        h2.addWidget(lbl("止损%"))
        self.sp_stop_loss = QDoubleSpinBox(); self.sp_stop_loss.setValue(15.0); self.sp_stop_loss.setMinimumHeight(24); self.sp_stop_loss.setMaximumWidth(50);
        h2.addWidget(self.sp_stop_loss)
        add_space(h2, 10)
        
        h2.addWidget(lbl("回撤"))
        self.sp_drawdown_sell = QDoubleSpinBox(); self.sp_drawdown_sell.setSingleStep(0.01); self.sp_drawdown_sell.setValue(0.97); self.sp_drawdown_sell.setMinimumHeight(24); self.sp_drawdown_sell.setMaximumWidth(50);
        h2.addWidget(self.sp_drawdown_sell)
        add_space(h2, 10)
        
        h2.addWidget(lbl("持有%"))
        self.sp_hold_pct_req = QDoubleSpinBox(); self.sp_hold_pct_req.setRange(-20.0, 20.0); self.sp_hold_pct_req.setValue(4.0); self.sp_hold_pct_req.setMinimumHeight(24); self.sp_hold_pct_req.setMaximumWidth(50);
        h2.addWidget(self.sp_hold_pct_req)
        h2.addStretch()
        v_params.addLayout(h2)

        layout.addWidget(g_params)
        
        ctrl = QHBoxLayout()
        self.btn_run = QPushButton("开始回测"); self.btn_run.setMinimumHeight(35); self.btn_run.clicked.connect(self.on_run); ctrl.addWidget(self.btn_run)
        self.btn_stop = QPushButton("停止"); self.btn_stop.setMinimumHeight(35); self.btn_stop.setEnabled(False); self.btn_stop.clicked.connect(self.on_stop); ctrl.addWidget(self.btn_stop)
        
        ctrl.addSpacing(20)
        ctrl.addWidget(QLabel("建议日期:"))
        self.dt_suggest = QDateEdit(QDate.currentDate(), calendarPopup=True)
        self.dt_suggest.setMinimumHeight(35)
        ctrl.addWidget(self.dt_suggest)
        
        self.btn_suggest = QPushButton("生成该日建议"); self.btn_suggest.setMinimumHeight(35); self.btn_suggest.clicked.connect(self.on_suggest); ctrl.addWidget(self.btn_suggest)
        
        ctrl.addStretch()
        self.btn_export = QPushButton("导出交易记录 (Excel)"); self.btn_export.setMinimumHeight(35); self.btn_export.setEnabled(False); self.btn_export.clicked.connect(self.on_export)
        ctrl.addWidget(self.btn_export)
        layout.addLayout(ctrl)
        
        g_res = QGroupBox("结果"); g_res.setStyleSheet("QGroupBox { font-weight: bold; font-size: 11pt; }")
        vres = QVBoxLayout(g_res)
        self.lbl_summary = QLabel("准备就绪"); self.lbl_summary.setFont(QFont("Consolas", 11)); self.lbl_summary.setAlignment(Qt.AlignTop); vres.addWidget(self.lbl_summary)
        self.txt_log = QTextEdit(readOnly=True); self.txt_log.setFont(QFont("Consolas", 11)); vres.addWidget(self.txt_log)
        layout.addWidget(g_res)    

    def on_run(self):
        self.txt_log.clear(); self.lbl_summary.setText("回测初始化中...")
        self.btn_run.setEnabled(False); self.btn_stop.setEnabled(True); self.btn_export.setEnabled(False)
        self.detailed_trade_log_df = None
        params = {
            'start_date': to_str_date(self.dt_start.date()), 'end_date': to_str_date(self.dt_end.date()),
            'initial_cash': self.sp_cash.value(), 'num_slots': self.sp_slots.value(),
            'champion_min_count': self.sp_min_champ.value(), 'champion_max_count': self.sp_max_champ.value(),
            'min_pct_change': self.sp_min_pct.value(), 'max_pct_change': self.sp_max_pct.value(),
            'market_type': self.combo_market.currentText(), 'overbought_factor': self.sp_overbought.value(),
            'open_pct_limit': self.sp_open_pct_limit.value(), 'fee_rate': self.sp_fee_rate.value(),
            'stop_loss_pct': self.sp_stop_loss.value(), 'holding_days': self.sp_holding_days.value(),
            'drawdown_sell_factor': self.sp_drawdown_sell.value(), 'hold_pct_req': self.sp_hold_pct_req.value(),
            'min_sector_pct': self.sp_min_sec.value(), 'max_sector_pct': self.sp_max_sec.value(),
            'min_mv': self.sp_min_mv.value(), 'max_mv': self.sp_max_mv.value(),
            'min_stock_pct': self.sp_min_stock_pct.value(), 'max_stock_pct': self.sp_max_stock_pct.value(),
            'rank_mode': self.combo_rank_mode.currentText(),
            'guxing_days': self.sp_guxing_days.value(), # 获取GUI设置的股性天数
            'min_guxing_score': self.sp_min_guxing_score.value() # [新增] 获取最低股性分
        }
        self.thread = BacktestThread(params)
        self.thread.progress.connect(self._append_log)
        self.thread.finished.connect(self._on_finished)
        self.thread.error.connect(self._on_error)
        self.thread.start()

    def on_stop(self):
        if self.thread and self.thread.isRunning(): self.thread.requestInterruption()
        self.btn_stop.setEnabled(False)

    def on_export(self):
        if self.detailed_trade_log_df is None or self.detailed_trade_log_df.empty: return
        try:
            name = f"trade_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
            path, _ = QFileDialog.getSaveFileName(self, "保存", name, "Excel (*.xlsx)")
            if path:
                self.detailed_trade_log_df.to_excel(path, index=False, engine='openpyxl')
                QMessageBox.information(self, "成功", f"已保存：\n{path}")
        except Exception as e: self._on_error(f"导出失败: {e}")

    def on_suggest(self):
        target_d = to_str_date(self.dt_suggest.date())
        self.txt_log.clear(); self._append_log("="*60 + f"\n<b>生成建议: {target_d}</b>\n" + "="*60)
        self.btn_run.setEnabled(False); self.btn_suggest.setEnabled(False); self.btn_stop.setEnabled(True)
        params = {
            'target_date': target_d,
            'min_pct_change': self.sp_min_pct.value(), 'max_pct_change': self.sp_max_pct.value(),
            'market_type': self.combo_market.currentText(), 'champion_min_count': self.sp_min_champ.value(),
            'champion_max_count': self.sp_max_champ.value(), 'overbought_factor': self.sp_overbought.value(),
            'min_sector_pct': self.sp_min_sec.value(), 'max_sector_pct': self.sp_max_sec.value(),
            'min_mv': self.sp_min_mv.value(), 'max_mv': self.sp_max_mv.value(),
            'min_stock_pct': self.sp_min_stock_pct.value(), 'max_stock_pct': self.sp_max_stock_pct.value(),
            'rank_mode': self.combo_rank_mode.currentText(),
            'guxing_days': self.sp_guxing_days.value(), # 获取GUI设置的股性天数
            'min_guxing_score': self.sp_min_guxing_score.value() # [新增] 获取最低股性分
        }
        self.thread = SuggestionThread(params)
        self.thread.progress.connect(self._append_log)
        self.thread.finished.connect(self._on_suggest_finished)
        self.thread.error.connect(self._on_error)
        self.thread.start()

    def _on_suggest_finished(self, result: dict):
        self.btn_run.setEnabled(True); self.btn_suggest.setEnabled(True); self.btn_stop.setEnabled(False)
        df_res = result['df']
        funnel = result['log']
        actual_date = result['date']

        try:
            sug_file = 'suggestions.json'
            save_list = []
            if not df_res.empty:
                for _, row in df_res.iterrows():
                    save_list.append({
                        'code': row['stock_code'],
                        'name': row['stock_name'],
                        'price': row['close_t'],
                        'reason': f"{row.get('best_sector_name', '-')}({row.get('max_sector_pct', 0):.1f}%)"
                    })
            with open(sug_file, 'w', encoding='utf-8') as f:
                json.dump(save_list, f, ensure_ascii=False, indent=4)
            self._append_log(f"<b>[系统] 建议已自动同步至 {sug_file}</b>")
        except Exception as e:
            self._append_log(f"<b style='color:red;'>同步失败: {e}</b>")

        html_funnel = "<div style='margin:10px 0; border:1px solid #ccc; padding:10px; background-color:#f9f9f9;'>"
        html_funnel += f"<b>选股漏斗过程 ({actual_date})</b><br>"
        html_funnel += "<table style='font-size:10pt; width:100%;'>"
        
        max_width = funnel[0][1] if funnel else 1
        for i, (step, count_val) in enumerate(funnel):
            bar_len = int((count_val / max_width) * 100) if max_width > 0 else 0
            bar_color = "#4CAF50" if count_val > 0 else "#ccc"
            arrow = "<div style='text-align:center; color:#999;'>&#8595;</div>" if i < len(funnel)-1 else ""
            
            html_funnel += f"<tr><td width='200'>{step}</td>"
            html_funnel += f"<td width='80' align='right'><b>{count_val} 只</b></td>"
            html_funnel += f"<td><div style='background-color:{bar_color}; width:{bar_len}%; height:8px; border-radius:4px;'></div></td></tr>"
            if arrow:
                html_funnel += f"<tr><td colspan='3'>{arrow}</td></tr>"
        
        html_funnel += "</table></div>"
        self._append_log(html_funnel)

        if df_res.empty:
            self._append_log("<b style='color:orange;'>经过筛选，无符合条件的标的。</b>"); return
        
        self._append_log(f"<b>最终入选: {len(df_res)} 只 ({self.combo_rank_mode.currentText()})</b>")
        html = "<table style='width:100%; font-size: 10pt; border-collapse: collapse; line-height: 1.5;'>"
        html += "<tr style='background-color:#e0e0e0;'><th>代码</th><th>名称</th><th align='right'>股性分</th><th align='right'>涨幅%</th><th align='right'>量比</th><th align='right'>市值(亿)</th><th align='right'>冠军板块(涨幅%)</th></tr>"
        for _, row in df_res.iterrows():
            sec_str = f"{row.get('best_sector_name', '-')}({row.get('max_sector_pct', 0):.1f}%)"
            html += f"<tr><td style='padding:6px 8px;'>{row['stock_code']}</td><td style='padding:6px 8px;'>{row['stock_name']}</td>"
            html += f"<td align='right' style='padding:6px 8px;'><b>{row.get('guxing_score',0):.1f}</b></td><td align='right' style='padding:6px 8px;'>{row['pct_change']:.2f}</td>"
            html += f"<td align='right' style='padding:6px 8px;'>{row.get('vol_ratio', 0):.2f}</td>"
            html += f"<td align='right' style='padding:6px 8px;'>{row['total_mv']/1e8:.2f}</td><td align='right' style='padding:6px 8px;'>{sec_str}</td></tr>"
        html += "</table>"
        self._append_log(html)

    def _append_log(self, msg: str):
        self.txt_log.moveCursor(QTextCursor.End)
        self.txt_log.insertHtml(msg + "<br>") if "<" in msg else self.txt_log.insertPlainText(msg + "\n")
        QApplication.processEvents()

    def _on_error(self, err: str):
        self.btn_run.setEnabled(True); self.btn_stop.setEnabled(False); self.btn_suggest.setEnabled(True)
        self._append_log(f"<b style='color:red;'>错误: {err}</b>")

    def _on_finished(self, result: dict):
        self.btn_run.setEnabled(True); self.btn_stop.setEnabled(False)
        df_assets = pd.DataFrame(result['daily_assets'])
        df_indices = result.get('indices', pd.DataFrame())
        
        if df_assets.empty: 
            self.lbl_summary.setText("无资产数据")
            return
        
        df_assets['date'] = pd.to_datetime(df_assets['date'])
        df_trades = pd.DataFrame(result['trade_log'])
        if not df_trades.empty:
            df_trades['sell_date'] = pd.to_datetime(df_trades['sell_date'])
            df_trades['buy_date'] = pd.to_datetime(df_trades['buy_date'])
            df_trades['year'] = df_trades['sell_date'].dt.year

        self._append_log("="*60 + "\n<b>总体表现</b>\n" + "="*60)
        self.lbl_summary.setText(self._calculate_summary_text(df_assets, df_trades, result['initial_cash']))

        self._append_log("\n" + "="*60 + "\n<b>年度详情</b>\n" + "="*60)
        self._render_table(df_assets, df_trades, df_indices, 'Y', result['initial_cash'])

        self._append_log("\n" + "="*60 + "\n<b>月度详情</b>\n" + "="*60)
        self._render_table(df_assets, df_trades, df_indices, 'M', result['initial_cash'])

        self._append_log("\n" + "="*60 + "\n<b>资产日记账</b>\n" + "="*60)
        self._render_daily_assets(df_assets, result['initial_cash'])

        self._append_log("\n" + "="*60 + "\n<b>交易明细</b>\n" + "="*60)
        if not df_trades.empty:
            self._render_trade_log(df_trades)
            self.detailed_trade_log_df = df_trades.copy()
            self.btn_export.setEnabled(True)
        else:
            self._append_log("<b style='color:red;'>【注意】期间无交易成交。</b>")

    def _calculate_summary_text(self, df_assets, df_trades, initial_cash):
        final = df_assets.iloc[-1]['total_assets']; total_ret = final / initial_cash - 1.0
        days = (df_assets.iloc[-1]['date'] - df_assets.iloc[0]['date']).days
        ann_ret = (1 + total_ret) ** (365.25 / days) - 1.0 if days > 0 else 0
        
        daily_r = df_assets['total_assets'].pct_change().fillna(0)
        sharpe = (daily_r.mean() / daily_r.std()) * np.sqrt(252) if daily_r.std() > 0 else 0
        mdd = (df_assets['total_assets'] / df_assets['total_assets'].expanding().max() - 1).min()
        
        win, plr = 0.0, 0.0
        if not df_trades.empty:
            win = (df_trades['profit'] > 0).mean() * 100
            aw = df_trades.loc[df_trades['profit'] > 0, 'profit'].mean() if (df_trades['profit'] > 0).any() else 0
            al = -df_trades.loc[df_trades['profit'] < 0, 'profit'].mean() if (df_trades['profit'] < 0).any() else 0
            plr = (aw / al) if al > 0 else 999.0
        
        return (f"总收益: {total_ret:+.2%} | 年化: {ann_ret:+.2%} | 回撤: {mdd:+.2%} | 夏普: {sharpe:.2f}\n"
                f"胜率: {win:.1f}% | 盈亏比: {plr:.2f} | 交易: {len(df_trades)}次")

    def _render_table(self, df_assets, df_trades, df_indices, period_type, initial_cash):
        df = df_assets.copy()
        col = "年份" if period_type == 'Y' else "月份"
        df['p'] = df['date'].dt.year if period_type == 'Y' else df['date'].dt.strftime('%Y-%m')
        
        html = "<table style='width:100%; font-size: 10pt; border-collapse: collapse; line-height: 1.5;'>"
        html += f"<tr style='background-color:#e0e0e0;'><th>{col}</th><th align='right'>收益%</th>"
        if period_type == 'Y': html += "<th align='right'>年化%</th>"
        html += "<th align='right'>回撤%</th><th align='right'>夏普</th><th align='right'>胜率%</th><th align='right'>盈亏比</th><th align='right' style='color:#0000AA'>上证%</th><th align='right' style='color:#0000AA'>深证%</th><th align='right' style='color:#0000AA'>创板%</th></tr>"
        
        last = initial_cash
        for p_val, grp in df.groupby('p'):
            curr = grp.iloc[-1]['total_assets']; ret = (curr / last - 1) * 100
            ann_str = "-"
            if period_type == 'Y':
                d = (grp.iloc[-1]['date'] - grp.iloc[0]['date']).days
                if d > 300: ann_str = f"{((1+ret/100)**(365.25/d)-1)*100:.2f}"
                else: ann_str = f"{ret:.2f}"
            
            mdd = (grp['total_assets'] / grp['total_assets'].expanding().max() - 1).min() * 100
            dr = grp['total_assets'].pct_change().fillna(0)
            std = dr.std(); sharpe = (dr.mean()/std)*np.sqrt(252) if std>0 else 0
            
            sub_t = pd.DataFrame()
            if not df_trades.empty:
                if period_type == 'Y': sub_t = df_trades[df_trades['sell_date'].dt.year == p_val]
                else: sub_t = df_trades[df_trades['sell_date'].dt.strftime('%Y-%m') == p_val]
            
            win, plr = 0.0, 0.0
            if not sub_t.empty:
                win = (sub_t['profit'] > 0).mean() * 100
                aw = sub_t.loc[sub_t['profit']>0, 'profit'].mean() if (sub_t['profit']>0).any() else 0
                al = -sub_t.loc[sub_t['profit']<0, 'profit'].mean() if (sub_t['profit']<0).any() else 0
                plr = (aw/al) if al>0 else 999.0

            irets = {'000001':'-', '399001':'-', '399006':'-'}
            if not df_indices.empty:
                sd, ed = grp.iloc[0]['date'], grp.iloc[-1]['date']
                sub_i = df_indices[(df_indices['date']>=sd)&(df_indices['date']<=ed)]
                for c in irets:
                    si = sub_i[sub_i['stock_code']==c]
                    if not si.empty: irets[c] = f"{(si.iloc[-1]['close']/si.iloc[0]['close']-1)*100:+.1f}"

            clr = "red" if ret>0 else "green"
            html += f"<tr><td style='padding:6px 8px;'>{p_val}</td><td align='right' style='color:{clr}; padding:6px 8px;'><b>{ret:+.2f}</b></td>"
            if period_type == 'Y': html += f"<td align='right' style='padding:6px 8px;'>{ann_str}</td>"
            html += f"<td align='right' style='padding:6px 8px;'>{mdd:.2f}</td><td align='right' style='padding:6px 8px;'>{sharpe:.2f}</td>"
            html += f"<td align='right' style='padding:6px 8px;'>{win:.0f}</td><td align='right' style='padding:6px 8px;'>{plr:.2f}</td>"
            html += f"<td align='right' style='color:#0000AA; padding:6px 8px;'>{irets['000001']}</td><td align='right' style='color:#0000AA; padding:6px 8px;'>{irets['399001']}</td><td align='right' style='color:#0000AA; padding:6px 8px;'>{irets['399006']}</td></tr>"
            last = curr
        html += "</table>"
        self._append_log(html)

    def _render_daily_assets(self, df, initial_cash):
        html = "<table style='width:100%; font-size: 10pt; border-collapse: collapse; line-height: 1.5;'>"
        html += "<tr style='background-color:#e0e0e0;'><th>日期</th><th align='right'>总资产</th><th align='right'>现金</th><th align='right'>当日收益率%</th><th align='right'>累计收益率%</th><th>持仓(代码:名称:数量)</th></tr>"
        
        prev_assets = initial_cash
        for idx, row in df.iterrows():
            d_str = row['date'].strftime('%Y-%m-%d')
            if row['held_stocks']:
                h_list = []
                for code, shares in row['held_stocks'].items():
                    stock_name = self.thread.dm.names.get(code, code) if self.thread and hasattr(self.thread, 'dm') else code
                    h_list.append(f"{code}:{stock_name}:{shares}")
                h_str = ", ".join(h_list)
            else:
                h_str = "-"
            
            daily_return = (row['total_assets'] / prev_assets - 1.0) * 100 if prev_assets > 0 else 0
            daily_return_color = "red" if daily_return > 0 else "green"
            cumulative_return = (row['total_assets'] / initial_cash - 1.0) * 100
            cumulative_color = "red" if cumulative_return > 0 else "green"
            
            html += f"<tr><td style='padding:6px 8px;'>{d_str}</td><td align='right' style='padding:6px 8px;'>{row['total_assets']:,.0f}</td><td align='right' style='padding:6px 8px;'>{row['cash']:,.0f}</td>"
            html += f"<td align='right' style='color:{daily_return_color}; padding:6px 8px; font-weight:bold;'>{daily_return:+.2f}%</td>"
            html += f"<td align='right' style='color:{cumulative_color}; padding:6px 8px; font-weight:bold;'>{cumulative_return:+.2f}%</td><td style='padding:6px 8px;'>{h_str}</td></tr>"
            prev_assets = row['total_assets']
        
        html += "</table>"
        self._append_log(html)

    def _render_trade_log(self, df):
        html = "<table style='width:100%; font-size: 10pt; border-collapse: collapse; line-height: 1.5;'>"
        html += "<tr style='background-color:#e0e0e0;'><th>代码</th><th>名称</th><th>买入日期</th><th align='right'>买入价</th><th align='right'>买日收盘</th><th align='right'>买日涨幅%</th><th>卖出日期</th><th align='right'>卖出价</th><th align='right'>盈亏</th><th align='right'>盈利率%</th><th align='right'>累计%</th><th align='right'>天数</th><th align='right'>选股日涨幅%</th><th align='right'>量比</th></tr>"
        for _, row in df.iterrows():
            c = "red" if row['profit'] > 0 else "green"
            b_date = row['buy_date'].strftime('%Y-%m-%d') if hasattr(row['buy_date'], 'strftime') else str(row['buy_date'])
            s_date = row['sell_date'].strftime('%Y-%m-%d') if hasattr(row['sell_date'], 'strftime') else str(row['sell_date'])
            sec_str = f"{row.get('sector_name', '-')}({row.get('max_sector_pct', 0):.1f}%)"
            cumulative_ret = row.get('cumulative_return', 0)
            cumulative_color = "red" if cumulative_ret > 0 else "green"
            
            buy_close = row.get('buy_day_close', 0)
            buy_pct = row.get('buy_day_pct', 0)

            buy_pct_style = ""
            if 4.5 <= buy_pct <= 5.5: buy_pct_style = "color:orange; font-weight:bold;"

            html += f"<tr><td style='padding:6px 8px;'>{row['code']}</td><td style='padding:6px 8px;'>{row['name']}</td>"
            html += f"<td style='padding:6px 8px;'>{b_date}</td><td align='right' style='padding:6px 8px;'>{row['buy_price']:.2f}</td>"
            
            html += f"<td align='right' style='padding:6px 8px;'>{buy_close:.2f}</td>"
            html += f"<td align='right' style='padding:6px 8px; {buy_pct_style}'>{buy_pct:+.2f}</td>"
            
            html += f"<td style='padding:6px 8px;'>{s_date}</td><td align='right' style='padding:6px 8px;'>{row['sell_price']:.2f}</td>"
            html += f"<td align='right' style='color:{c}; padding:6px 8px;'>{row['profit']:.0f}</td><td align='right' style='color:{c}; padding:6px 8px;'>{row['profit_rate']*100:+.2f}</td>"
            html += f"<td align='right' style='color:{cumulative_color}; padding:6px 8px; font-weight:bold;'>{cumulative_ret:+.2f}</td>"
            html += f"<td align='right' style='padding:6px 8px;'>{row['holding_days']}</td>"
            html += f"<td align='right' style='padding:6px 8px;'>{row.get('selection_pct', 0):.2f}</td>"
            html += f"<td align='right' style='padding:6px 8px;'>{row.get('vol_ratio', 0):.2f}</td></tr>"
        html += "</table>"
        self._append_log(html)

if __name__ == "__main__":
    app = QApplication(sys.argv); app.setStyle("Fusion"); w = MainWindow(); w.show(); sys.exit(app.exec())