# (版本 v1.5.1 - 修正缩进错误 & 恢复线程引用清理)
import sys
import os
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import math
import traceback
import threading
from PySide6.QtWidgets import QDateEdit, QTableWidget, QTableWidgetItem, QTextEdit, QFrame, QPushButton, QFileDialog, QMessageBox
from PySide6.QtCore import QTimer, QDate
import matplotlib.pyplot as plt # <--- 添加导入
import matplotlib # <--- 添加导入
from PySide6.QtWidgets import QMenu # 确保导入 QMenu
from PySide6.QtCore import QPoint   # 确保导入 QPoint

try:
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QPushButton, QLabel, QLineEdit, QDialog, QDialogButtonBox,
        QSpinBox, QMessageBox, QInputDialog, QTreeView, QHeaderView,
        QAbstractItemView, QSpacerItem, QSizePolicy
    )
    from PySide6.QtCore import Qt, Signal, Slot, QObject, QThread, QMetaObject, QAbstractTableModel, QModelIndex
    from PySide6.QtGui import QFont, QStandardItemModel, QStandardItem, QColor, QBrush
except ImportError:
    print("错误: PySide6 库未安装或导入失败。请运行 'pip install PySide6'")
    sys.exit(1)

# --- Configuration ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, 'stock_data.db') # 确保指向正确的数据库文件
NUM_STOCKS_TO_HOLD = 10
LOT_SIZE = 100
TARGET_PERCENT_BUFFER = 0.97

# --- V V V --- 新增：股票黑名单 --- V V V ---
# 在这个集合中添加你不想在模拟中买入的股票代码 (使用字符串格式, 6位数字)
# 例如: STOCK_BLACKLIST = {"600000", "000001"}
STOCK_BLACKLIST = {'300472','300736','603616','002856','002193','300329','300405','600847'
    # "600000", # 示例：浦发银行
    # "000001", # 示例：平安银行
    # 在这里添加更多不想买入的代码，每个代码用英文引号括起来，用逗号隔开
    # 例如: "000002", "600519"
}
# --- ^ ^ ^ --- 黑名单配置结束 --- ^ ^ ^ ---

# --- Core Logic Functions (直接包含在此文件中) ---
from PySide6.QtCore import Signal, QObject, QThread
from PySide6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QDialogButtonBox, QLabel
from PySide6.QtGui import QFont

class ConfirmRebalanceDialog(QDialog):
    def __init__(self, title, message, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(600) # 设置最小宽度，可以调整
        self.setMinimumHeight(400) # 设置最小高度

        layout = QVBoxLayout(self)

        # 可以选择添加一个简单的提示标签
        # layout.addWidget(QLabel("请检查以下计划执行的操作："))

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        # 设置等宽字体，方便对齐
        font = QFont("Courier New", 10) # 或者 Consolas, Monaco 等
        self.text_edit.setFont(font)
        self.text_edit.setText(message) # 显示详细信息
        layout.addWidget(self.text_edit)

        # 标准按钮 Yes / No
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No)
        self.button_box.accepted.connect(self.accept) # Yes 按钮连接到 accept
        self.button_box.rejected.connect(self.reject) # No 按钮连接到 reject
        layout.addWidget(self.button_box)

        self.setLayout(layout)


class HistSimWorker(QObject):
    finished = Signal(list)  # 回测日志列表
    error = Signal(str)

    def __init__(self, db_path, start_date, end_date, initial_cash):
        super().__init__()
        self.db_path = db_path
        self.start_date = start_date
        self.end_date = end_date
        self.initial_cash = initial_cash

    def run(self):
        try:
            logs = simulate_historical_rotation(
                db_path=self.db_path,
                start_date=self.start_date,
                end_date=self.end_date,
                initial_cash=self.initial_cash
            )
            self.finished.emit(logs)
        except Exception as e:
            import traceback
            self.error.emit(f"历史模拟线程异常: {e}\n{traceback.format_exc()}")
# --- Database Interaction Functions (使用 V1.5.1 强化错误返回的版本) ---
def connect_db(db_path):
    """连接到 SQLite 数据库。"""
    conn = None # 初始化 conn
    try:
        conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
        print(f"DB Connected: {db_path}")
        return conn
    except sqlite3.Error as e:
        print(f"DB Connection Failed: {e}")
        # 确保即使连接失败也返回 None
        return None
# --- Helper Function: Check Trading Day ---
def is_trading_day(db_path, date_to_check):
    """根据 exp_trade 表检查给定日期是否为交易日。"""
    conn = None
    is_open = False
    try:
        conn = connect_db(db_path) # 使用现有的 connect_db 函数
        if conn:
            cursor = conn.cursor()
            # 确保日期是 'YYYY-MM-DD' 格式的字符串
            date_str = date_to_check.strftime('%Y-%m-%d')
            cursor.execute("SELECT is_open FROM exp_trade WHERE date = ?", (date_str,))
            result = cursor.fetchone()
            # 如果找到记录且 is_open 为 1，则为交易日
            if result and result[0] == 1:
                is_open = True
            else:
                 print(f"is_trading_day: Date {date_str} not found or not open in exp_trade.")
        else:
            print("is_trading_day: Failed to connect to DB.")
    except Exception as e:
        print(f"Error checking trading day for {date_str}: {e}")
        traceback.print_exc() # 打印详细错误
    finally:
        if conn:
            try:
                conn.close()
            except Exception as e_close:
                 print(f"Error closing DB connection in is_trading_day: {e_close}")
    print(f"is_trading_day check for {date_str}: {is_open}") # 增加日志
    return is_open

def load_cash(conn):
    """从数据库加载当前现金。"""
    if not conn:
        print("Load cash failed: No connection.")
        return 0.0
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT amount FROM cash WHERE id = 1")
        result = cursor.fetchone()
        cash = float(result[0]) if result else 1000000.0 # 默认值
        return cash
    except (sqlite3.Error, TypeError, ValueError) as e:
        print(f"Load cash failed: {e}")
        return 0.0

def save_cash(conn, cash_amount):
    """保存现金到数据库。"""
    if not conn:
        print("Save cash failed: No connection.")
        return False
    try:
        cursor = conn.cursor()
        cursor.execute("REPLACE INTO cash (id, amount, last_updated) VALUES (1, ?, ?)",
                       (cash_amount, datetime.now().isoformat()))
        conn.commit()
        return True
    except sqlite3.Error as e:
        print(f"Save cash failed: {e}")
        try: # 尝试回滚
            conn.rollback()
        except Exception as rb_e:
            print(f"Rollback failed after save cash error: {rb_e}")
        return False

def load_positions(conn):
    """从数据库加载当前持仓。"""
    positions = {}
    if not conn:
        print("Load positions failed: No connection.")
        return positions
    try:
        # 从 positions 表加载基础持仓信息
        df = pd.read_sql("SELECT stock_code, buy_price, shares FROM positions WHERE status='HOLDING'", conn)
        for _, row in df.iterrows():
            positions[row['stock_code']] = {
                'cost_price': float(row['buy_price']), # 使用 cost_price 存储成本价
                'shares': int(row['shares'])
            }
        return positions
    except (sqlite3.Error, pd.io.sql.DatabaseError, Exception) as e:
        print(f"Load basic positions failed: {e}")
        traceback.print_exc() # 打印详细错误
        return {}

def load_full_positions(conn):
    """
    从数据库加载当前持仓，包含 cost_price, shares, buy_date, buy_rank。
    """
    positions = {}
    if not conn:
        print("Load full positions failed: No connection.")
        return positions
    try:
        # 确认 positions 表有需要的列
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(positions)")
        columns = [row[1] for row in cursor.fetchall()]
        required_cols = ['stock_code', 'buy_price', 'shares', 'buy_date', 'buy_rank']
        if not all(col in columns for col in required_cols):
            missing = [col for col in required_cols if col not in columns]
            print(f"错误: load_full_positions - positions 表缺少列: {missing}。")
            return {}

        query = "SELECT stock_code, buy_price, shares, buy_date, buy_rank FROM positions WHERE status='HOLDING'"
        df = pd.read_sql(query, conn)

        for _, row in df.iterrows():
            code = row['stock_code']
            buy_rank_val = None
            if row['buy_rank'] is not None:
                try: buy_rank_val = int(row['buy_rank'])
                except (ValueError, TypeError): pass

            positions[code] = {
                'cost_price': float(row['buy_price']) if row['buy_price'] is not None else 0.0,
                'shares': int(row['shares']) if row['shares'] is not None else 0,
                'buy_date': row['buy_date'],
                'buy_rank': buy_rank_val
            }
        print(f"成功加载 {len(positions)} 条完整持仓记录 (load_full_positions)。")
        return positions
    except Exception as e:
        print(f"加载完整持仓信息失败 (load_full_positions): {e}")
        traceback.print_exc(); return {}

def execute_sell_db(conn, code, sell_price, shares, sell_date_str_db, is_full_sell, cost_price):
    """(V1.5.1) 更新数据库以反映卖出操作 - 强化错误返回。"""
    if not conn:
        return False, "DB not connected" # 返回符合结构的元组
    try:
        cursor = conn.cursor()
        # 计算盈亏
        profit = (sell_price - cost_price) * shares if cost_price is not None else None
        profit_s = f"{profit:,.2f}" if profit is not None else "N/A"

        if is_full_sell:
            # 更新状态为 SOLD
            cursor.execute("UPDATE positions SET status='SOLD', sell_price=?, sell_date=? WHERE stock_code=? AND status='HOLDING'",
                           (sell_price, sell_date_str_db, code))
            log_msg = f"Sim Sell (Full) {code}: {shares:,} @ {sell_price:.2f}, PnL:{profit_s}"
        else:
             # 部分卖出（在轮动策略中较少用，但保留）
             cursor.execute("UPDATE positions SET shares = shares - ? WHERE stock_code=? AND status='HOLDING'", (shares, code))
             log_msg = f"Sim Sell (Part) {code}: {shares:,} @ {sell_price:.2f}, PnL:{profit_s}"
        conn.commit()
        return True, log_msg # 成功时返回 True 和日志
    except sqlite3.Error as e:
        print(f"DB Sell Fail {code}: {e}") # 打印错误到控制台
        traceback.print_exc() # 打印详细堆栈信息
        try: conn.rollback()
        except Exception as rb_e: print(f"Rollback failed after sell error: {rb_e}")
        # --- !!! 修改点：确保返回两个值的元组 !!! ---
        return False, f"数据库卖出失败 ({code}): {e}"
    except Exception as e_unexp: # 捕获其他可能的异常
        print(f"Unexpected DB Sell Fail {code}: {e_unexp}")
        traceback.print_exc()
        try: conn.rollback()
        except Exception as rb_e: print(f"Rollback failed after unexpected sell error: {rb_e}")
        # --- !!! 修改点：确保返回两个值的元组 !!! ---
        return False, f"意外的数据库卖出失败 ({code}): {e_unexp}"

def execute_buy_db(conn, code, price, shares, buy_date_str_db, buy_rank=None):
    """(V1.5.1) 更新数据库以反映买入或加仓操作 - 强化错误返回。支持 buy_rank。"""
    if not conn:
        return False, "DB not connected", 0, 0 # 返回符合结构的元组
    new_avg, new_shares = price, shares
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT shares, buy_price FROM positions WHERE stock_code=? AND status='HOLDING'", (code,))
        existing = cursor.fetchone()
        if existing:
            # 加仓逻辑
            old_shares, old_price = existing
            old_cost = old_price * old_shares
            current_cost = price * shares
            new_shares = old_shares + shares
            new_avg = (old_cost + current_cost) / new_shares if new_shares > 0 else 0
            cursor.execute("UPDATE positions SET shares=?, buy_price=?, buy_date=? WHERE stock_code=? AND status='HOLDING'",
                           (new_shares, new_avg, buy_date_str_db, code))
            log_msg = f"Sim Add {code}: {shares:,} @ {price:.2f}. NewAvg:{new_avg:.2f}, Total:{new_shares:,}"
        else:
            # 新买入逻辑，插入 buy_rank
            new_avg = price
            new_shares = shares
            if buy_rank is not None:
                cursor.execute("INSERT INTO positions (stock_code, buy_price, shares, buy_date, status, buy_rank) VALUES (?, ?, ?, ?, 'HOLDING', ?)",
                               (code, price, shares, buy_date_str_db, buy_rank))
            else:
                cursor.execute("INSERT INTO positions (stock_code, buy_price, shares, buy_date, status) VALUES (?, ?, ?, ?, 'HOLDING')",
                               (code, price, shares, buy_date_str_db))
            log_msg = f"Sim Buy {code}: {shares:,} @ {price:.2f} (Rank:{buy_rank if buy_rank is not None else 'N/A'})"
        conn.commit()
        return True, log_msg, new_avg, new_shares # 成功时返回四个值的元组
    except sqlite3.Error as e:
        print(f"DB Buy Fail {code}: {e}") # 打印错误到控制台
        traceback.print_exc() # 打印详细堆栈信息
        try: conn.rollback()
        except Exception as rb_e: print(f"Rollback failed after buy error: {rb_e}")
        return False, f"数据库买入失败 ({code}): {e}", 0, 0
    except Exception as e_unexp:
        print(f"Unexpected DB Buy Fail {code}: {e_unexp}")
        traceback.print_exc()
        try: conn.rollback()
        except Exception as rb_e: print(f"Rollback failed after unexpected buy error: {rb_e}")
        return False, f"意外的数据库买入失败 ({code}): {e_unexp}", 0, 0

# --- Core Logic Functions ---
# === 完整替换函数: get_latest_stock_data (v5 - 修正 stock_basic 列名 ts_code + 强制 60000 天调试) ===
def get_latest_stock_data(conn):
    """
    (v5 - 修正 stock_basic 列名 ts_code + 强制 60000 天调试) 获取最新的股票数据用于选股。
    - 数据来源:
        - stock_prices: 'close' (价格), 'total_mv' (总市值)。(假设 stock_code 是 6 位)
        - stock_basic: 'list_date' (上市日期)。(列名为 ts_code, 需处理)
        - stock_fundamentals: 'stock_name' (中文名称)。(假设 stock_code 是 6 位)
    - ... (其他筛选逻辑不变) ...
    """
    if not conn:
        print("Get latest stock data failed: No connection.")
        return pd.DataFrame()
    try:
        # 1. 获取 stock_prices 中的最新日期 (不变)
        latest_date_query = "SELECT MAX(date) FROM stock_prices"
        cursor = conn.cursor()
        cursor.execute(latest_date_query)
        result = cursor.fetchone()
        if not result or not result[0]:
            print("错误: 无法从 stock_prices 获取最新日期。")
            return pd.DataFrame()
        latest_date = result[0]
        print(f"步骤 1: 从 stock_prices 获取的最新日期: {latest_date}")

        # 2. 从 stock_prices 获取数据 (不变)
        prices_query = """
        SELECT stock_code, close, total_mv
        FROM stock_prices
        WHERE date = ? AND close > 2 AND total_mv > 0
        """
        df_prices = pd.read_sql(prices_query, conn, params=(latest_date,))
        # --- DEBUG: 检查初始 df_prices --- (保留这个调试)
        print(f"\nDEBUG: --- 检查初始筛选结果 (来自 stock_prices) ---")
        print(f"查询日期: {latest_date}")
        print(f"筛选条件: close > 2 AND total_mv > 0")
        if df_prices.empty:
            print("!!! 关键发现: df_prices 为空！说明在最新日期，没有任何股票同时满足 close>2 和 total_mv>0 的条件。")
            return pd.DataFrame()
        else:
            print(f"找到 {len(df_prices)} 条满足初始条件的记录。")
            print("这些记录的前 5 行是:")
            print(df_prices.head())
        print("--- 初始筛选结果检查结束 ---")
        # --- 调试结束 ---
        df_prices = df_prices.rename(columns={'close': 'latest_price', 'total_mv': 'market_cap'})

        # 3. 从 stock_basic 获取上市日期 (使用 ts_code)
        basic_query = "SELECT ts_code, list_date FROM stock_basic"
        try:
             df_basic = pd.read_sql(basic_query, conn)
        except sqlite3.OperationalError as e_basic_op:
             if "no such column: ts_code" in str(e_basic_op): print("!!! 严重错误: stock_basic 表中没有 'ts_code' 列！"); return pd.DataFrame()
             else: raise
        except Exception as e_basic: print(f"查询 stock_basic 出错: {e_basic}"); return pd.DataFrame()
        if df_basic.empty: print("错误: 无法从 stock_basic 读取数据或表为空。"); return pd.DataFrame()

        df_basic['stock_code'] = df_basic['ts_code'].astype(str).str[:6]
        df_basic = df_basic[['stock_code', 'list_date']] # 只保留需要的列
        # --- DEBUG: 检查 df_basic --- (保留这个调试)
        print("\nDEBUG: --- 合并前 df_basic 的状态 ---")
        print(f"df_basic 的列名: {df_basic.columns.tolist()}")
        print(f"df_basic 的形状: {df_basic.shape}")
        print(df_basic.head())
        print("--- df_basic 状态结束 ---")
        df_basic = df_basic.dropna(subset=['list_date'])
        df_basic = df_basic.drop_duplicates(subset=['stock_code'], keep='first')
        if df_basic.empty: print("错误: stock_basic 处理后为空"); return pd.DataFrame()


        # 4. 从 stock_fundamentals 获取股票名称 (不变)
        fundamentals_query = "SELECT stock_code, stock_name FROM stock_fundamentals"
        try:
             df_fundamentals = pd.read_sql(fundamentals_query, conn)
             if df_fundamentals.empty: print("警告: 无法从 stock_fundamentals 读取股票名称。"); df_fundamentals = pd.DataFrame(columns=['stock_code', 'stock_name'])
             else: print(f"步骤 4: 从 stock_fundamentals 获取 {len(df_fundamentals)} 条 stock_name 数据。")
        except Exception as e_fund: print(f"查询 stock_fundamentals 出错: {e_fund}。"); df_fundamentals = pd.DataFrame(columns=['stock_code', 'stock_name'])


        # 5. 合并数据 (现在都使用 6 位 stock_code)
        df_merged = pd.merge(df_prices, df_basic, on='stock_code', how='inner')
        # --- DEBUG: 检查合并 basic 后的 df_merged --- (保留这个调试)
        print("\nDEBUG: --- 与 df_basic 合并后的 df_merged 状态 ---")
        print(f"df_merged 的列名: {df_merged.columns.tolist()}")
        print(f"df_merged 的形状: {df_merged.shape}")
        print(df_merged.head())
        print("--- df_merged (合并basic后) 状态结束 ---")
        if df_merged.empty: print("错误: stock_prices 和 stock_basic 合并后为空。"); return pd.DataFrame()
        print(f"步骤 5a: 合并 prices 和 basic 后剩余 {len(df_merged)} 条记录。")

        df_merged = pd.merge(df_merged, df_fundamentals, on='stock_code', how='left')
        df_merged['stock_name'] = df_merged['stock_name'].fillna('N/A')
        print(f"步骤 5b: 合并 fundamentals 后剩余 {len(df_merged)} 条记录。")
        # --- DEBUG: 检查合并 fundamentals 后的 df_merged --- (保留这个调试)
        print("\nDEBUG: --- 与 df_fundamentals 合并后的 df_merged 状态 ---")
        print(f"df_merged 的列名: {df_merged.columns.tolist()}")
        print(f"df_merged 的形状: {df_merged.shape}")
        print(df_merged.head())
        print("--- df_merged (合并fundamentals后) 状态结束 ---")


        # 6.1 筛选: 名称不含 ST/*ST/退/PT (不变)
        original_count_filt = len(df_merged)
        df_merged = df_merged[~df_merged['stock_name'].str.contains('ST|\\*ST|退|PT', regex=True, na=False)]
        print(f"步骤 6.1 (名称过滤): 剩余 {len(df_merged)} 条记录 (移除 {original_count_filt - len(df_merged)} 条 ST/*ST/退/PT)。")

        # 6.2 筛选: 排除科创板/北交所 (不变)
        original_count_filt = len(df_merged)
        df_merged = df_merged[~df_merged['stock_code'].str.startswith(('688', '8', '9','30'))]
        print(f"步骤 6.2 (板块过滤): 剩余 {len(df_merged)} 条记录 (移除 {original_count_filt - len(df_merged)} 条 688/8/9/70开头)。")

        # --- DEBUG: 6.3 检查前的 df_merged --- (保留这个调试)
        print("\nDEBUG: --- 执行步骤 6.3 if 检查前的 df_merged 状态 ---")
        print(f"df_merged 的列名: {df_merged.columns.tolist()}")
        print(f"df_merged 的形状: {df_merged.shape}")
        print(df_merged.head())
        print("--- df_merged (6.3 检查前) 状态结束 ---")

        # 6.3 筛选: 上市时间 >= N 天
        if 'list_date' not in df_merged.columns:
            print("!!! 内部错误: 合并后的数据中缺少 'list_date' 列。 <<< 这个不应该再发生了！")
            return pd.DataFrame()
        else:
            # --- !!! 开始严格调试日期过滤步骤 (上次添加的调试块) !!! ---
            print(f"\nDEBUG: --- 开始严格调试日期过滤步骤 (6.3) ---")
            today = datetime.now().date()
            # --- !!! 关键调试: 在这里强制设定极端的 required_days_listed !!! ---
            required_days_listed = 60000 # 保持 60000 天测试
            print(f"DEBUG: 今日日期 (today) = {today}")
            print(f"DEBUG: 强制设定要求上市天数 (required_days_listed) = {required_days_listed}")
            print(f"DEBUG: 进入日期过滤前的 df_merged 行数: {len(df_merged)}")

            df_merged['list_date_dt'] = pd.to_datetime(df_merged['list_date'], format='%Y%m%d', errors='coerce').dt.date
            print(f"DEBUG: 转换为日期对象后，无效日期 (NaT) 的数量: {df_merged['list_date_dt'].isnull().sum()}")

            original_count_filt = len(df_merged)
            df_valid_dates = df_merged.dropna(subset=['list_date_dt']).copy()
            removed_invalid = original_count_filt - len(df_valid_dates)
            print(f"DEBUG: 移除了 {removed_invalid} 行无效或缺失的 list_date_dt。")
            print(f"DEBUG: 用于计算天数的 df_valid_dates 行数: {len(df_valid_dates)}")

            if not df_valid_dates.empty:
                df_valid_dates['days_listed'] = (today - df_valid_dates['list_date_dt']).dt.days
                print(f"DEBUG: 计算得到的 'days_listed' 范围：最小值={df_valid_dates['days_listed'].min()}, 最大值={df_valid_dates['days_listed'].max()}")
                print("DEBUG: 'days_listed' 计算结果抽样:")
                print(df_valid_dates[['stock_code', 'list_date', 'list_date_dt', 'days_listed']].head())

                filter_condition = df_valid_dates['days_listed'] >= required_days_listed
                num_passing_filter = filter_condition.sum()
                print(f"DEBUG: 检查过滤条件 (days_listed >= {required_days_listed})，满足条件的行数: {num_passing_filter}") # 预期是 0
                if num_passing_filter > 0: print("!!! 逻辑错误警告: 居然有股票满足上市 >= 60000 天！")

                df_filtered = df_valid_dates[filter_condition]
                print(f"DEBUG: 应用过滤条件后，df_filtered 的行数: {len(df_filtered)}") # 预期是 0

                print(f"DEBUG: 准备用 df_filtered (行数 {len(df_filtered)}) 更新 df_merged (当前行数 {len(df_merged)})")
                shape_before_update = df_merged.shape

                # --- 修正更新 df_merged 的逻辑 ---
                if not df_filtered.empty:
                    # 如果过滤后还有数据 (理论上不可能)，则删除临时列
                    df_merged = df_filtered.drop(columns=['list_date_dt', 'days_listed'])
                else:
                    # 如果过滤后为空 (预期情况)，则将 df_merged 也设置为空，但保留列定义
                    # 先获取除临时列外的所有列名
                    final_cols = df_merged.columns.drop(['list_date_dt','days_listed'], errors='ignore').tolist()
                    df_merged = pd.DataFrame(columns=final_cols) # 创建空的，但有正确列名

                print(f"DEBUG: 更新后，df_merged 的行数: {len(df_merged)}") # 预期是 0
                if len(df_merged) == 0: print("DEBUG: df_merged 已被正确更新为空，符合预期。")
                else: print("!!! 逻辑错误: df_merged 更新后没有变为空！")

            else: # 如果一开始就没有有效的 list_date_dt
                 print("DEBUG: 没有有效的 list_date_dt 用于计算天数，直接将 df_merged 设为空。")
                 df_merged = pd.DataFrame(columns=df_merged.columns.drop(['list_date_dt'], errors='ignore')) # 保留列名
                 print(f"DEBUG: df_merged 行数已设为: {len(df_merged)}")

            print(f"DEBUG: --- 结束日期过滤步骤 (6.3) ---")
            # --- 严格调试结束 ---
            # 注意：df_merged 在这里理论上应该是空的了

        # 7. 确认 market_cap (来自 total_mv) 是数值类型并处理单位 (如果需要)
        # --- !! 如果 df_merged 为空，这部分需要能正常处理 !! ---
        if df_merged.empty:
             print("步骤 7 & 8: 因日期过滤后无数据，跳过市值确认和排序。")
        else:
             # 只有在 df_merged 不为空时才执行
             df_merged['market_cap'] = pd.to_numeric(df_merged['market_cap'], errors='coerce')
             # --- !! 如果 total_mv 单位是"万元"，取消下面行的注释 !! ---
             # df_merged['market_cap'] = df_merged['market_cap'] * 10000
             df_merged.dropna(subset=['market_cap'], inplace=True)
             df_merged = df_merged[df_merged['market_cap'] > 0]
             print(f"步骤 7 (市值确认): 最终有效市值记录 {len(df_merged)} 条。")

             # 8. 按 market_cap 升序排序
             df_merged = df_merged.sort_values(by='market_cap', ascending=True)
             print(f"步骤 8: 按市值排序完成。")


        # 9. 准备并返回最终结果
        final_columns = ['stock_code', 'stock_name', 'latest_price', 'market_cap']
        # --- 即使 df_merged 为空，也应该返回一个有正确列名的空 DataFrame ---
        if df_merged.empty:
             print(f"步骤 9: 准备返回最终结果，共 0 条记录。")
             return pd.DataFrame(columns=final_columns) # 返回空的带列名的 DF

        # 如果 df_merged 不为空，检查列是否存在
        missing_cols = [col for col in final_columns if col not in df_merged.columns]
        if missing_cols:
            print(f"!!! 错误: 最终 DataFrame 缺少必需列: {missing_cols}")
            traceback.print_stack()
            return pd.DataFrame(columns=final_columns) # 返回空的

        print(f"步骤 9: 准备返回最终结果，共 {len(df_merged)} 条记录。")
        return df_merged[final_columns]

    # --- 错误处理保持不变 ---
    except (sqlite3.Error, pd.io.sql.DatabaseError, KeyError, ValueError, Exception) as e:
        print(f"Get latest stock data 函数执行失败: {e}")
        traceback.print_exc()
        return pd.DataFrame()
    
def select_target_stocks(latest_data_df, num_stocks):
    """根据最小市值选择目标股票列表。"""
    if latest_data_df.empty:
        return []
    try:
        # 确保 market_cap 是数值类型
        if not pd.api.types.is_numeric_dtype(latest_data_df['market_cap']):
             latest_data_df['market_cap'] = pd.to_numeric(latest_data_df['market_cap'], errors='coerce')
             latest_data_df.dropna(subset=['market_cap'], inplace=True)

        # 按市值升序排序
        sorted_df = latest_data_df.sort_values(by='market_cap', ascending=True, na_position='last')
        # 选择前 N 个
        target_df = sorted_df.head(num_stocks)
        return target_df['stock_code'].tolist()
    except Exception as e:
        print(f"Select target stocks failed: {e}")
        traceback.print_exc()
        return []

def rebalance_portfolio(conn, latest_data_df, current_positions, current_cash, num_stocks, hold_days, rank_boost):
    """
    按小市值轮动策略执行调仓：
    1. 先强制卖出所有不在最新合规股票池的持仓，并在日志中注明"因不符合选股规则被换出"；
    2. 其它股票再按持股天数和排名提升判断是否卖出；
    3. 卖出后补买，保持N只持仓；
    4. 所有操作详细日志输出。
    """
    status_msgs = ["--- 开始模拟调仓 ---"]
    today = datetime.now().date()
    # 1. 计算最新市值排名和合规股票池
    latest_data_df = latest_data_df.sort_values(by='market_cap', ascending=True)
    latest_data_df = latest_data_df.reset_index(drop=True)
    latest_data_df['rank'] = latest_data_df.index + 1
    rank_map = dict(zip(latest_data_df['stock_code'], latest_data_df['rank']))
    valid_stock_set = set(latest_data_df['stock_code'])
    # 2. 遍历持仓，先强制卖出不在合规池的股票
    codes_to_sell = []
    sell_reasons = {}
    for code, pos in current_positions.items():
        if code not in valid_stock_set:
            codes_to_sell.append(code)
            sell_reasons[code] = "因不符合选股规则被换出"
    # 3. 其它持仓再判断持股天数和排名提升
    for code, pos in current_positions.items():
        if code in codes_to_sell:
            continue
        buy_date_str = pos.get('buy_date')
        buy_rank = pos.get('buy_rank')
        cost_price = pos.get('cost_price')
        shares = pos.get('shares')
        if not buy_date_str or not cost_price or not shares:
            continue
        try:
            buy_date = datetime.strptime(buy_date_str, '%Y-%m-%d').date()
        except Exception:
            buy_date = today
        days_held = (today - buy_date).days
        current_rank = rank_map.get(code)
        if current_rank is None or buy_rank is None:
            continue
        rank_diff = int(buy_rank) - int(current_rank)
        reason = None
        if days_held >= hold_days:
            reason = f"持有{days_held}天≥{hold_days}天"
        if rank_diff >= rank_boost:
            reason = (reason + ", " if reason else "") + f"排名提升{rank_diff}≥{rank_boost}"
        if reason:
            codes_to_sell.append(code)
            sell_reasons[code] = reason
    # 4. 卖出操作
    cash_from_sells = 0.0
    latest_sell_prices = {}
    if codes_to_sell:
        placeholders = ','.join(['?'] * len(codes_to_sell))
        price_query = f"SELECT stock_code, latest_price FROM stock_fundamentals WHERE stock_code IN ({placeholders})"
        price_df = pd.read_sql(price_query, conn, params=codes_to_sell)
        latest_sell_prices = price_df.set_index('stock_code')['latest_price'].to_dict()
    for code in codes_to_sell:
        position = current_positions.get(code)
        if not position:
            continue
        shares_to_sell = position['shares']
        cost_price = position.get('cost_price')
        sell_price = latest_sell_prices.get(code)
        if sell_price is None or pd.isna(sell_price) or sell_price <= 0:
            status_msgs.append(f"卖出 {code} 失败: 无有效价格")
            continue
        sell_price = float(sell_price)
        sell_result = execute_sell_db(conn, code, sell_price, shares_to_sell, today.strftime('%Y-%m-%d'), True, cost_price)
        if isinstance(sell_result, tuple) and len(sell_result) == 2:
            success, log_msg = sell_result
            if success:
                cash_from_sells += sell_price * shares_to_sell
                status_msgs.append(f"卖出 {code}: {shares_to_sell} 股 @ {sell_price:.2f}，原因：{sell_reasons.get(code, '')}。{log_msg}")
            else:
                status_msgs.append(f"卖出 {code} 失败: {log_msg}")
        else:
            status_msgs.append(f"卖出 {code} 失败：内部函数 execute_sell_db 返回值异常 ({type(sell_result)})")
    updated_cash = current_cash + cash_from_sells
    status_msgs.append(f"卖出后现金: {updated_cash:,.2f}")
    # 5. 计算当前持仓和可买股票
    current_holdings = set(current_positions.keys())
    holdings_after_sell = current_holdings - set(codes_to_sell)
    codes_to_buy = []
    # 先排除已持有的
    available_df = latest_data_df[~latest_data_df['stock_code'].isin(holdings_after_sell)]
    # 选出补足N只的股票
    num_to_buy = num_stocks - len(holdings_after_sell)
    if num_to_buy > 0:
        codes_to_buy = available_df.head(num_to_buy)['stock_code'].tolist()
    # 6. 买入操作
    latest_buy_prices = {}
    if codes_to_buy:
        placeholders = ','.join(['?'] * len(codes_to_buy))
        price_query = f"SELECT stock_code, latest_price FROM stock_fundamentals WHERE stock_code IN ({placeholders})"
        price_df = pd.read_sql(price_query, conn, params=codes_to_buy)
        latest_buy_prices = price_df.set_index('stock_code')['latest_price'].to_dict()
    cash_used_for_buys = 0.0
    failed_buys = 0
    capital_per_stock = (updated_cash * 0.99) / num_stocks if num_stocks > 0 else 0
    for code in codes_to_buy:
        buy_price = latest_buy_prices.get(code)
        if buy_price is None or pd.isna(buy_price) or buy_price <= 0:
            status_msgs.append(f"买入 {code} 失败: 无有效价格"); failed_buys += 1; continue
        buy_price = float(buy_price)
        target_shares_raw = capital_per_stock / buy_price
        shares_to_buy = (int(target_shares_raw) // 100) * 100
        if shares_to_buy <= 0:
            status_msgs.append(f"买入 {code} 失败: 目标股数计算为0"); failed_buys += 1; continue
        cost_of_buy = shares_to_buy * buy_price
        buy_rank = rank_map.get(code)
        if updated_cash - cash_used_for_buys >= cost_of_buy:
            buy_result = execute_buy_db(conn, code, buy_price, shares_to_buy, today.strftime('%Y-%m-%d'), buy_rank)
            if isinstance(buy_result, tuple) and len(buy_result) == 4:
                success, log_msg, _, _ = buy_result
                if success:
                    cash_used_for_buys += cost_of_buy
                    status_msgs.append(f"买入 {code}: {shares_to_buy} 股 @ {buy_price:.2f}，市值排名:{buy_rank}。{log_msg}")
                else:
                    failed_buys += 1
                    status_msgs.append(f"买入 {code} 失败: {log_msg}")
            else:
                status_msgs.append(f"买入 {code} 失败：内部函数 execute_buy_db 返回值异常 ({type(buy_result)})")
                failed_buys += 1
        else:
            status_msgs.append(f"买入 {code} 失败: 现金不足 (需 {cost_of_buy:,.2f}, 余 {updated_cash - cash_used_for_buys:,.2f})")
            failed_buys += 1
    final_cash = updated_cash - cash_used_for_buys
    try:
        if not save_cash(conn, final_cash):
            status_msgs.append("警告: 保存最终现金失败!")
    except Exception as e_save:
        status_msgs.append(f"警告: 保存最终现金时出错: {e_save}")
    status_msgs.append(f"买入后现金: {final_cash:,.2f}")
    if failed_buys > 0:
        status_msgs.append(f"警告: {failed_buys} 只股票未能买入。")
    status_msgs.append("--- 模拟调仓结束 ---")
    return final_cash, "\n".join(status_msgs)

def add_funds(db_path, amount_to_adjust):
    """调整资金（可正可负），处理自己的数据库连接。返回 (success, message)"""
    # 移除必须大于0的检查
    conn = None
    try:
        conn = connect_db(db_path)
        if not conn:
            return False, "错误：无法连接数据库以调整资金。"

        current_cash = load_cash(conn)
        new_cash = current_cash + amount_to_adjust

        # 增加检查：确保调整后的现金不为负
        if new_cash < 0:
            msg = f"错误：无法减少资金 {abs(amount_to_adjust):,.2f}，会导致总现金变为负数 ({new_cash:,.2f})。"
            print(msg)
            return False, msg

        # 保存调整后的现金
        if save_cash(conn, new_cash):
            # 修改成功消息文本
            action_desc = "增加" if amount_to_adjust > 0 else "减少"
            msg = f"成功{action_desc}资金 {abs(amount_to_adjust):,.2f}。当前总现金: {new_cash:,.2f}"
            print(msg)
            return True, msg
        else:
            # 保存失败的消息保持不变
            msg = "错误：保存调整后的现金失败。"
            print(msg)
            return False, msg
    except Exception as e:
        # 错误消息保持不变
        msg = f"调整资金时发生错误: {e}"
        print(msg)
        traceback.print_exc()
        return False, msg
    finally:
        if conn:
            try:
                conn.close()
            except sqlite3.Error as e_close:
                print(f"关闭调整资金DB连接失败: {e_close}")

# --- Worker Thread (保持 V1.4 不变) ---
class RebalanceWorker(QObject):
    finished = Signal(float, str)
    log_message = Signal(str)
    def __init__(self, db_path, num_stocks, hold_days, rank_boost):
        super().__init__()
        self.db_path = db_path
        self.num_stocks = num_stocks
        self.hold_days = hold_days
        self.rank_boost = rank_boost
        self._final_cash = 0.0
    @Slot()
    def run(self):
        conn = None
        status_message = "开始执行..."
        thread_id = threading.current_thread().name
        print(f"[{thread_id}] Worker run started.")
        self.log_message.emit(f"[{thread_id}] 工作线程启动...")
        try:
            self.log_message.emit(f"[{thread_id}] 连接数据库..."); print(f"[{thread_id}] Connecting DB..."); conn = connect_db(self.db_path)
            if not conn: raise ConnectionError("无法连接数据库")
            self.log_message.emit(f"[{thread_id}] 连接成功."); print(f"[{thread_id}] DB Connected.")
            self.log_message.emit(f"[{thread_id}] 加载现金..."); print(f"[{thread_id}] Loading cash..."); current_cash = load_cash(conn); self._final_cash = current_cash; self.log_message.emit(f"[{thread_id}] 现金: {current_cash:,.2f}"); print(f"[{thread_id}] Cash: {current_cash:,.2f}")
            self.log_message.emit(f"[{thread_id}] 加载持仓..."); print(f"[{thread_id}] Loading positions..."); current_positions = load_positions(conn); self.log_message.emit(f"[{thread_id}] 持仓数: {len(current_positions)}"); print(f"[{thread_id}] Positions: {len(current_positions)}")
            self.log_message.emit(f"[{thread_id}] 获取最新数据..."); print(f"[{thread_id}] Getting latest data..."); latest_data = get_latest_stock_data(conn); self.log_message.emit(f"[{thread_id}] 获取数据 {len(latest_data)} 条"); print(f"[{thread_id}] Got {len(latest_data)} rows")
            if not latest_data.empty:
                self.log_message.emit(f"[{thread_id}] 选择目标..."); print(f"[{thread_id}] Selecting targets..."); target_codes = select_target_stocks(latest_data, self.num_stocks); self.log_message.emit(f"[{thread_id}] 选出 {len(target_codes)} 只"); print(f"[{thread_id}] Selected {len(target_codes)}")
                if target_codes:
                    self.log_message.emit(f"[{thread_id}] 执行调仓..."); print(f"[{thread_id}] Rebalancing..."); final_cash_result, rebalance_status_details = rebalance_portfolio(conn, latest_data, current_positions, current_cash, self.num_stocks, self.hold_days, self.rank_boost); self._final_cash = final_cash_result; status_message = rebalance_status_details; self.log_message.emit(f"[{thread_id}] 调仓完毕."); print(f"[{thread_id}] Rebalance done.")
                else: status_message = "未选出目标股票"; self.log_message.emit(status_message); print(f"[{thread_id}] {status_message}")
            else: status_message = "无最新数据"; self.log_message.emit(status_message); print(f"[{thread_id}] {status_message}")
        except ConnectionError as ce: detailed_error = f"[{thread_id}] DB连接错误: {ce}"; status_message = f"错误: {ce}"; print(detailed_error); self.log_message.emit(detailed_error); self._final_cash = 0.0
        except Exception as e: detailed_error = f"[{thread_id}] Worker错误: {e}\n{traceback.format_exc()}"; status_message = f"错误: {e}"; print(detailed_error); self.log_message.emit(detailed_error); self._final_cash = getattr(self, '_final_cash', 0.0)
        finally:
            if conn:
                try: conn.close(); close_msg = f"[{thread_id}] Worker DB closed."; print(close_msg); self.log_message.emit(close_msg)
                except sqlite3.Error as e_close: err_msg = f"[{thread_id}] Worker DB close fail: {e_close}"; print(err_msg); self.log_message.emit(err_msg)
            final_short_status = status_message.splitlines()[-1] if status_message else "完成 (未知)"
            finish_msg = f"[{thread_id}] Worker finished. Final status: {final_short_status}"; print(finish_msg); self.log_message.emit(finish_msg)
            self.finished.emit(self._final_cash, status_message)

# --- 持仓数据准备函数 (修改 V4.2 - 修正 stock_basic 列名 ts_code) ---
# --- 持仓数据准备函数 ---
# 用下面的完整版本替换你现有的 _prepare_positions_for_display 函数

def _prepare_positions_for_display(db_path):
    """
    (V4.3 - 基于提供的 schema 简化)
    加载持仓，获取价格/名称/指标(PE,PB,Turnover,PctChange)/上市日期。
    数据源: positions, stock_fundamentals, stock_basic
    """
    conn = None
    display_data = []
    print("DEBUG [PrepareDisplay]: 开始准备持仓显示数据 (V4.3)...")

    try:
        conn = connect_db(db_path)
        if not conn: raise ConnectionError("无法连接数据库")

        # 1. 加载当前持仓 (positions 表)
        positions = load_positions(conn) # {code: {'shares': S, 'cost_price': P}}
        if not positions:
            print("DEBUG [PrepareDisplay]: 无当前持仓记录。")
            return display_data
        holding_codes = list(positions.keys())
        print(f"DEBUG [PrepareDisplay]: 加载了 {len(holding_codes)} 条持仓代码。")

        # --- 初始化信息字典 ---
        fund_info = {}      # from fundamentals
        basic_info = {}     # from basic

        # 2. 获取基本面、价格和所需指标 (stock_fundamentals)
        if holding_codes:
            placeholders = ','.join(['?'] * len(holding_codes))
            # --- 查询语句包含所有需要的列 ---
            fund_columns = ['stock_code', 'stock_name', 'latest_price',
                            'pe_ratio', 'pb_ratio', 'turnover_rate', 'change_percent']
            fund_query = f"""
                SELECT {', '.join(fund_columns)}
                FROM stock_fundamentals
                WHERE stock_code IN ({placeholders})
            """
            try:
                 fund_df = pd.read_sql(fund_query, conn, params=holding_codes)
                 if not fund_df.empty:
                      # 检查列是否存在，不存在则填充 None
                      for col in fund_columns[1:]: # 跳过 stock_code
                           if col not in fund_df.columns:
                                fund_df[col] = None
                                print(f"警告: stock_fundamentals 表缺少列 '{col}'")
                      fund_info = fund_df.set_index('stock_code').to_dict('index')
                      print(f"DEBUG [PrepareDisplay]: 从 fundamentals 获取了 {len(fund_info)} 条记录。")
                 else:
                      print("DEBUG [PrepareDisplay]: 从 fundamentals 未查询到持仓信息。")
            except Exception as e_fund:
                 print(f"查询 stock_fundamentals 出错: {e_fund}")
                 traceback.print_exc()
                 # 出错则 fund_info 为空

            # 3. 获取上市日期 (stock_basic) - (逻辑保持不变)
            basic_query = "SELECT ts_code, list_date FROM stock_basic"
            try:
                basic_df_all = pd.read_sql(basic_query, conn)
                if not basic_df_all.empty:
                    basic_df_all['stock_code'] = basic_df_all['ts_code'].astype(str).str[:6]
                    basic_df_all = basic_df_all.drop_duplicates(subset=['stock_code'], keep='first')
                    basic_df_filtered = basic_df_all[basic_df_all['stock_code'].isin(holding_codes)]
                    if not basic_df_filtered.empty:
                         basic_info = basic_df_filtered.set_index('stock_code')[['list_date']].to_dict('index')
                         print(f"DEBUG [PrepareDisplay]: 从 basic 获取了 {len(basic_info)} 条上市日期。")
            except Exception as e_basic:
                 print(f"查询或处理 stock_basic 出错: {e_basic}")
                 traceback.print_exc()

        # --- 4. 整理数据，合并信息 ---
        print("DEBUG [PrepareDisplay]: 开始合并整理数据...")
        for code, pos_data in positions.items():
            info = fund_info.get(code, {})       # 获取 fundamentals 信息
            basic_data = basic_info.get(code, {}) # 获取 basic 信息

            # 基本信息
            name = info.get('stock_name', 'N/A')
            shares = pos_data.get('shares', 0)
            cost_price = pos_data.get('cost_price', 0.0)
            list_date = basic_data.get('list_date', 'N/A')

            # 最新价格和相关计算
            current_price_raw = info.get('latest_price')
            current_price = None
            market_value = None
            profit_loss = None
            profit_loss_pct = None
            if current_price_raw is not None and pd.notna(current_price_raw):
                 try:
                      current_price = float(current_price_raw)
                      if current_price > 0 and shares > 0:
                           market_value = current_price * shares
                           if cost_price > 0:
                                profit_loss = (current_price - cost_price) * shares
                                cost_basis = cost_price * shares
                                profit_loss_pct = (profit_loss / cost_basis * 100) if cost_basis != 0 else 0.0
                           else: profit_loss = 0.0; profit_loss_pct = 0.0
                 except (ValueError, TypeError): pass

            # 从 fund_info 获取新增指标
            pe_ratio = info.get('pe_ratio')     # 使用数据库列名
            pb_ratio = info.get('pb_ratio')
            turnover = info.get('turnover_rate')
            pct_change = info.get('change_percent') # 使用数据库列名

            # 添加到最终列表，使用与模型对应的键名
            display_data.append({
                'code': code, 'name': name, 'shares': shares, 'cost_price': cost_price,
                'current_price': current_price, 'market_value': market_value,
                'profit_loss': profit_loss, 'profit_loss_pct': profit_loss_pct,
                'list_date': list_date,
                # --- 新增键值对 (键名要和模型 Model 里的 header_key_map 对应) ---
                'pe': pe_ratio,
                'pb': pb_ratio,
                'turnover': turnover,
                'pct_change': pct_change
            })

    except ConnectionError as e: print(f"准备持仓显示数据时出错: {e}")
    except Exception as e: print(f"准备持仓显示数据时发生未预期错误: {e}"); traceback.print_exc()
    finally:
        if conn:
            try: conn.close()
            except Exception as e_close: print(f"关闭持仓显示DB连接失败: {e_close}")
        print(f"DEBUG [PrepareDisplay]: 函数结束，准备返回 {len(display_data)} 条数据。")
        if display_data:
             print("DEBUG [PrepareDisplay]: 第一个持仓的数据样本:")
             sample_keys = ['code', 'name', 'shares', 'current_price', 'pe', 'pb', 'turnover', 'pct_change']
             print({k: display_data[0].get(k) for k in sample_keys})
        else: print("DEBUG [PrepareDisplay]: display_data 为空。")

    return display_data if isinstance(display_data, list) else []


def simulate_historical_rotation(db_path, start_date, end_date, initial_cash=1000000, log_to_csv=None):
    """
    历史轮动模拟主循环：
    - db_path: 数据库路径
    - start_date, end_date: 回测起止日期（字符串 'YYYY-MM-DD'）
    - initial_cash: 初始资金
    - log_to_csv: 日志导出文件名（可选）
    """
    import sqlite3
    import pandas as pd
    conn = sqlite3.connect(db_path)
    # 1. 读取所有历史行情
    prices_df = pd.read_sql(f"SELECT * FROM stock_prices WHERE date >= ? AND date <= ?", conn, params=(start_date, end_date))
    prices_df['date'] = pd.to_datetime(prices_df['date'])
    # 2. 读取快照表（只用于补充名称）
    try:
        name_map = pd.read_sql("SELECT stock_code, stock_name FROM stock_fundamentals", conn).set_index('stock_code')['stock_name'].to_dict()
    except Exception:
        name_map = {}
    conn.close()
    # 3. 按日期排序
    all_dates = sorted(prices_df['date'].unique())
    # 4. 初始化资产
    cash = initial_cash
    positions = {}  # {code: {'shares': int, 'cost': float, 'buy_date': date, 'buy_rank': int}}
    logs = []
    pending_orders = []  # T日生成，T+1日执行 [{'action': 'buy'/'sell', 'code': str, 'shares': int, 'price': float, 'date': date, ...}]
    for i, cur_date in enumerate(all_dates[:-1]):  # 最后一天无法T+1成交
        today_str = cur_date.strftime('%Y-%m-%d')
        next_date = all_dates[i+1]
        # 1. 生成当天可用行情
        today_df = prices_df[prices_df['date'] == cur_date].copy()
        # 2. 选股池过滤
        valid_df = today_df[
            (today_df['close'] > 2) &
            (~today_df['stock_code'].str.startswith(('688', '8', '9','30')))
        ].copy()
        # 排除ST、*ST、退、PT（用快照表）
        if name_map:
            valid_df['name'] = valid_df['stock_code'].map(name_map)
            valid_df = valid_df[~valid_df['name'].fillna('').str.contains('ST|退|PT', regex=True)]
        # 市值
        valid_df['market_cap'] = valid_df['circ_mv'].fillna(valid_df['total_mv'])
        valid_df = valid_df[valid_df['market_cap'] > 0]
        # 按市值排序
        valid_df = valid_df.sort_values(by='market_cap', ascending=True)
        valid_codes = valid_df['stock_code'].tolist()
        rank_map = {code: idx+1 for idx, code in enumerate(valid_codes)}
        # 3. 处理T+1日待成交订单
        next_open_df = prices_df[prices_df['date'] == next_date].set_index('stock_code')
        day_log = [f"=== {today_str} ==="]
        # 卖出
        for order in [o for o in pending_orders if o['action']=='sell']:
            code = order['code']
            shares = order['shares']
            open_price = next_open_df.at[code, 'open'] if code in next_open_df.index else None
            if open_price and shares > 0:
                cash += shares * open_price
                if code in positions:
                    cost = positions[code]['cost']
                    pnl = (open_price - cost) * shares
                    day_log.append(f"卖出 {code}({name_map.get(code,'')}) {shares}股 @ {open_price:.2f}，盈亏:{pnl:.2f}，原因:{order.get('reason','')}")
                    del positions[code]
                else:
                    day_log.append(f"卖出 {code}({name_map.get(code,'')}) {shares}股 @ {open_price:.2f}（非持仓，异常！）")
            else:
                day_log.append(f"卖出 {code}({name_map.get(code,'')}) 失败：无次日开盘价")
        # 买入
        for order in [o for o in pending_orders if o['action']=='buy']:
            code = order['code']
            shares = order['shares']
            open_price = next_open_df.at[code, 'open'] if code in next_open_df.index else None
            if open_price and shares > 0 and cash >= shares * open_price:
                cash -= shares * open_price
                positions[code] = {'shares': shares, 'cost': open_price, 'buy_date': next_date, 'buy_rank': order.get('buy_rank', None)}
                day_log.append(f"买入 {code}({name_map.get(code,'')}) {shares}股 @ {open_price:.2f}，市值排名:{order.get('buy_rank','N/A')}")
            else:
                day_log.append(f"买入 {code}({name_map.get(code,'')}) 失败：无次日开盘价或现金不足")
        pending_orders = []  # 清空已执行订单
        # 4. 生成今日调仓指令
        # 4.1 卖出：不在最新池的、持股天数/排名提升
        codes_to_sell = []
        sell_reasons = {}
        for code, pos in positions.items():
            # 不在合规池
            if code not in valid_codes:
                codes_to_sell.append(code)
                sell_reasons[code] = "因不符合选股规则被换出"
                continue
            # 持股天数/排名提升
            buy_date = pos['buy_date']
            buy_rank = pos.get('buy_rank')
            days_held = (cur_date - buy_date).days
            cur_rank = rank_map.get(code)
            reason = None
            if days_held >= hold_days:
                reason = f"持有{days_held}天≥{hold_days}天"
            if buy_rank is not None and cur_rank is not None and (buy_rank - cur_rank) >= rank_boost:
                reason = (reason + ", " if reason else "") + f"排名提升{buy_rank - cur_rank}≥{rank_boost}"
            if reason:
                codes_to_sell.append(code)
                sell_reasons[code] = reason
        for code in codes_to_sell:
            if code in positions:
                pending_orders.append({'action': 'sell', 'code': code, 'shares': positions[code]['shares'], 'reason': sell_reasons.get(code, '')})
        # 4.2 买入：补足N只
        holdings_after_sell = set(positions.keys()) - set(codes_to_sell)
        available_df = valid_df[~valid_df['stock_code'].isin(holdings_after_sell)]
        num_to_buy = num_stocks - len(holdings_after_sell)
        capital_per_stock = (cash * 0.99) / num_stocks if num_stocks > 0 else 0
        for code in available_df.head(num_to_buy)['stock_code']:
            buy_price = today_df[today_df['stock_code']==code]['close'].values[0]
            target_shares_raw = capital_per_stock / buy_price
            shares_to_buy = (int(target_shares_raw) // lot_size) * lot_size
            if shares_to_buy > 0:
                pending_orders.append({'action': 'buy', 'code': code, 'shares': shares_to_buy, 'buy_rank': rank_map.get(code)})
        # 5. 统计资产
        market_value = 0.0
        for code, pos in positions.items():
            cur_price = today_df[today_df['stock_code']==code]['close'].values
            if len(cur_price) > 0:
                market_value += pos['shares'] * cur_price[0]
        total_assets = cash + market_value
        day_log.append(f"现金: {cash:,.2f} 持仓市值: {market_value:,.2f} 总资产: {total_assets:,.2f}")
        logs.append({'date': today_str, 'cash': cash, 'market_value': market_value, 'total_assets': total_assets, 'log': '\n'.join(day_log)})
    # 导出日志
    if log_to_csv:
        pd.DataFrame(logs).to_csv(log_to_csv, index=False, encoding='utf-8-sig')
    return logs
# --- 持仓表格模型 ---
# 用下面的完整版本替换你现有的 SimulatorPositionsModel 类

# --- 持仓表格模型 ---
# 用下面的完整版本替换你现有的 SimulatorPositionsModel 类

class SimulatorPositionsModel(QAbstractTableModel):
    def __init__(self, data=[], parent=None):
        super().__init__(parent)
        self._data = data
        # <--- MODIFICATION START: Swap column order in _headers --->
        self._headers = [
            "代码", "名称", "股数", "成本价", "当前价", "持仓市值",
            "盈亏额", "盈亏率(%)",
            "涨跌幅(%)", # <-- 涨跌幅(%) 移到前面
            "市盈率", "市净率", "换手率(%)",
            "上市日期"  # <-- 上市日期 移到最后
        ]
        # <--- MODIFICATION END --->

        # <--- MODIFICATION START: Adjust order in map (optional, for readability) --->
        self._header_key_map = { # 创建一个映射字典方便查找
            "代码": "code", "名称": "name", "股数": "shares",
            "成本价": "cost_price", "当前价": "current_price",
            "持仓市值": "market_value", "盈亏额": "profit_loss",
            "盈亏率(%)": "profit_loss_pct",
            "涨跌幅(%)": "pct_change", # <-- 涨跌幅(%)
            "市盈率": "pe",
            "市净率": "pb",
            "换手率(%)": "turnover",
            "上市日期": "list_date"   # <-- 上市日期
        }
        # <--- MODIFICATION END --->

        print(f"DEBUG: SimulatorPositionsModel 初始化完成。表头 (_headers): {self._headers}")
        print(f"DEBUG: SimulatorPositionsModel 初始化时的列数 (应为 {len(self._headers)}): {self.columnCount()}")

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        return len(self._headers) # 列数由表头列表长度决定

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        # (这个方法内部的逻辑不需要改变，因为它根据 header 文本来判断格式化和对齐)
        if not index.isValid(): return None
        row = index.row(); col = index.column()

        if row >= len(self._data) or col >= len(self._headers): return None
        item_data = self._data[row]
        header = self._headers[col]
        key = self._header_key_map.get(header)
        if key is None: return None
        value = item_data.get(key)

        # --- 处理数据显示 (DisplayRole) ---
        if role == Qt.ItemDataRole.DisplayRole:
            if value is None or pd.isna(value): return "N/A"
            try:
                if header == "股数": return f"{int(value):,}"
                elif header in ["成本价", "当前价", "持仓市值", "盈亏额"]: return f"{float(value):,.2f}"
                elif header == "盈亏率(%)": return f"{float(value):.2f}%"
                elif header == "上市日期": return str(value) # 直接显示字符串
                elif header == "市盈率": return f"{float(value):.2f}"
                elif header == "市净率": return f"{float(value):.2f}"
                elif header == "换手率(%)": return f"{float(value):.2f}%"
                elif header == "涨跌幅(%)": return f"{float(value):.2f}%" # 格式化不变
                else: return str(value)
            except (ValueError, TypeError):
                 return str(value)

        # --- 处理对齐 (TextAlignmentRole) ---
        elif role == Qt.ItemDataRole.TextAlignmentRole:
            # 之前修改的对齐逻辑仍然有效，现在 "涨跌幅(%)" 会靠左，"上市日期" 也会靠左
            if header in ["代码", "名称", "上市日期", ]: # 列表保持不变
                return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            else:
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter

        # --- 处理颜色 (ForegroundRole) ---
        elif role == Qt.ItemDataRole.ForegroundRole:
             # (颜色逻辑不变，仍然依赖 pct_change 的值)
             value_to_check = None
             color_key = None
             if header in ["盈亏额", "盈亏率(%)", "涨跌幅(%)", "当前价"]:
                 color_key = self._header_key_map.get("涨跌幅(%)")
                 if color_key:
                     value_to_check = item_data.get(color_key)

             if value_to_check is not None and pd.notna(value_to_check):
                 try:
                     num_value = float(value_to_check)
                     if num_value > 0.001: return QBrush(QColor("red"))
                     elif num_value < -0.001: return QBrush(QColor("darkGreen"))
                 except (ValueError, TypeError): pass
             return QBrush(QColor("black"))

        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        # (这部分代码不变)
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self._headers):
                return self._headers[section]
        return None

    # updateData 方法保持不变
    def updateData(self, new_data):
        self.beginResetModel()
        self._data = new_data
        self.endResetModel()

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        # (这部分代码不变)
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self._headers):
                return self._headers[section]
        return None

    # updateData 方法保持不变
    def updateData(self, new_data):
        self.beginResetModel()
        self._data = new_data
        self.endResetModel()

# --- Main GUI Window ---
class SimulatorAppUI(QMainWindow):
    # __init__ 方法 (使用 V1.7 添加汇总标签的版本)
# __init__ 方法 (使用 V1.7 添加汇总标签的版本 + 新增 final_pending_orders)
    def __init__(self):
        super().__init__()
        self.setWindowTitle("小市值自动模拟交易 v7.37 Final (挂单同步版)") # 更新标题
        self.setGeometry(100, 100, 900, 700)
        self.db_exists = os.path.exists(DB_PATH)
        self.current_cash = 0.0
        self.current_portfolio_value = 0.0
        # 添加交易记录列表
        self.trade_records = []
        self.hist_simulation_finished = False # <-- 增加完成标志，初始为 False
        self.final_predicted_sells = None # <-- 这个可能不再需要，取决于是否还用旧的预测显示
        self.final_predicted_buys = None  # <-- 这个可能不再需要

        # --- 保存模拟最终状态的变量 ---
        self.final_sim_positions = None # <-- 保存最终模拟持仓 {code: {'shares': S, 'cost':C ...}}
        self.final_sim_cash = None      # <-- 保存最终模拟现金
        self.final_sim_prices = None    # <-- 保存最后一日价格 {code: price}
        self.final_rank_dict_stored = None # <-- 保存最后一日未筛选排名
        self.final_decision_pool_df_stored = None # <-- 保存最后一日筛选后决策池
        self.final_pending_orders = None # <-- 新增：保存最终待执行订单列表
        # --- 结束状态变量 ---

        self._setup_ui()
        if self.db_exists:
            self._refresh_all_displays()
        else:
            QMessageBox.critical(self, "数据库错误", f"数据库文件未找到: {DB_PATH}\n程序将无法正常工作。")

        # --- 线程相关初始化 ---
        self.worker_thread = None
        # self.rebalance_worker = None # 旧的 worker，可能不再使用
        self.db_rebalance_worker = None # 新的 worker 用于同步
        self.hist_worker_thread = None
        self.hist_worker = None
        # --- 结束线程初始化 ---

        # self.hist_simulation_finished = False # 重复了，删除一个

    def _setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        # --- 主布局设为垂直布局 ---
        main_layout = QVBoxLayout(central_widget)

        # --- 历史模拟顶部控件 ---
        hist_controls_widget = QWidget()
        hist_layout = QHBoxLayout(hist_controls_widget)
        hist_layout.addWidget(QLabel("历史模拟起始日:"))
        self.hist_start_date = QDateEdit()
        self.hist_start_date.setCalendarPopup(True)
        # 设置一个更合理的默认起始日期，比如一年前
        self.hist_start_date.setDate(QDate.currentDate().addYears(-1))
        hist_layout.addWidget(self.hist_start_date)
        hist_layout.addWidget(QLabel("结束日:"))
        self.hist_end_date = QDateEdit()
        self.hist_end_date.setCalendarPopup(True)
        self.hist_end_date.setDate(QDate.currentDate()) # 默认结束日期为当天
        hist_layout.addWidget(self.hist_end_date)
        hist_layout.addWidget(QLabel("初始资金:"))
        self.hist_init_cash = QLineEdit("1000000") # 默认100万
        hist_layout.addWidget(self.hist_init_cash)
        self.hist_start_btn = QPushButton("开始历史模拟")
        self.hist_next_btn = QPushButton("前进一天")
        self.hist_auto_btn = QPushButton("自动演变")
        hist_layout.addWidget(self.hist_start_btn)
        hist_layout.addWidget(self.hist_next_btn)
        hist_layout.addWidget(self.hist_auto_btn)
        # 添加导出交易记录按钮
        self.export_trades_btn = QPushButton("导出交易记录")
        hist_layout.addWidget(self.export_trades_btn)
        hist_layout.addStretch()
        main_layout.addWidget(hist_controls_widget)

        # --- 历史模拟状态标签 ---
        self.hist_status_label = QLabel("历史模拟未开始")
        self.hist_status_label.setWordWrap(True)
        main_layout.addWidget(self.hist_status_label)

        # --- 历史模拟显示区域 - 左右布局 ---
        hist_display_container = QWidget()
        hist_display_layout = QHBoxLayout(hist_display_container)

        # 左侧：历史持仓表格
        positions_container = QWidget()
        positions_container_layout = QVBoxLayout(positions_container)
        positions_container_layout.addWidget(QLabel("历史模拟持仓:"))
        self.hist_positions_table = QTableWidget(0, 13) # 初始化列数
        hist_headers = ["代码", "名称", "股数", "成本价", "开盘价", "收盘价", "持仓市值", "市值(亿)", "持股天数", "盈亏率(%)", "买入排名", "当前", "最低"]
        self.hist_positions_table.setHorizontalHeaderLabels(hist_headers)
        # 设置每列的宽度
        column_widths = [60, 70, 60, 70, 70, 70, 80, 70, 70, 70, 70, 60, 60]  # 每列宽度值
        for col, width in enumerate(column_widths):
            self.hist_positions_table.setColumnWidth(col, width)
        self.hist_positions_table.setAlternatingRowColors(True)
        self.hist_positions_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers) # 不可编辑
        self.hist_positions_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows) # 整行选择
        positions_container_layout.addWidget(self.hist_positions_table)

        # 右侧：历史日志区域
        log_container = QWidget()
        log_container_layout = QVBoxLayout(log_container)
        log_container_layout.addWidget(QLabel("历史模拟日志:"))
        self.hist_log_display = QTextEdit()
        self.hist_log_display.setReadOnly(True)
        self.hist_log_display.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        log_container_layout.addWidget(self.hist_log_display)

        # 设置历史区域左右比例 60:40
        hist_display_layout.addWidget(positions_container, 3) # 左侧历史表格，拉伸因子 3
        hist_display_layout.addWidget(log_container, 2)      # 右侧历史日志，拉伸因子 2

        # 将历史显示区添加到主布局，占据主要高度
        main_layout.addWidget(hist_display_container, 7) # 占据 70% 高度

        # ==== 分隔线 ====
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        main_layout.addWidget(separator)

        # ==== 常规模式区域 - 左右布局 ====
        regular_container = QWidget()
        regular_layout = QHBoxLayout(regular_container)

        # 左侧（原 right_widget 内容）: 当前持仓表格
        current_positions_widget = QWidget()
        current_positions_layout = QVBoxLayout(current_positions_widget)
        current_positions_layout.addWidget(QLabel("当前持仓 (数据库):")) # 明确是数据库持仓
        self.positions_view = QTreeView() # 使用 QTreeView
        self.positions_model = SimulatorPositionsModel([]) # 使用自定义模型
        self.positions_view.setModel(self.positions_model)
        # --- 设置 QTreeView 属性 ---
        self.positions_view.setAlternatingRowColors(True)
        self.positions_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows) # 整行选择
        self.positions_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers) # 不可编辑
        self.positions_view.setSortingEnabled(True) # 允许排序
        header = self.positions_view.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents) # 根据内容调整列宽
        # header.setStretchLastSection(True) # 可选：让最后一列填充剩余空间

        # --- 设置右键菜单 ---
        self.positions_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.positions_view.customContextMenuRequested.connect(self._show_positions_context_menu)

  # 在 SimulatorAppUI 类的 _setup_ui 方法内部

        # --- 设置选中颜色和悬浮颜色样式表 ---
        # <--- MODIFICATION START: Update Stylesheet for Hover --->
        selection_style_sheet = """
            QTreeView {
                /* 你可以设置基础颜色，但这可能会覆盖交替行颜色效果 */
                /* background-color: white; */
                /* color: black; */
                /* border: 1px solid lightgrey; */ /* 可选：给整个 TreeView 加边框 */
            }

            /* 选中项（有焦点时） */
            QTreeView::item:selected:active {
                background-color: #E0F2FF; /* 淡天蓝色 */
                color: black;           /* 黑色文字 */
            }

            /* 选中项（无焦点时） */
            QTreeView::item:selected:!active {
                background-color: #D8EBFD; /* 失去焦点时稍深一点 */
                color: black;
            }

            /* 鼠标悬浮项 */
            QTreeView::item:hover {
                background-color: transparent; /* 设置为透明，即不改变背景色 */
                /* color: black; */          /* 通常不需要改变悬浮时的文字颜色 */

                /* --- 如果你希望悬浮时也变淡蓝，注释掉上面一行，取消下面一行的注释 --- */
                /* background-color: #F0F8FF; */ /* 可以用一个比选中色更淡的颜色，如 AliceBlue */
                /* background-color: #E0F2FF; */ /* 或者和选中时完全一样的淡蓝色 */
            }
        """
        self.positions_view.setStyleSheet(selection_style_sheet)
        # <--- MODIFICATION END --->

        # ... (_setup_ui 中 self.positions_view 后面的代码) ...

        current_positions_layout.addWidget(self.positions_view)

        # 右侧（原 left_widget 内容）: 现金信息、参数、按钮、日志
        controls_widget = QWidget()
        controls_layout = QVBoxLayout(controls_widget)
        # 资产显示
        asset_layout = QHBoxLayout()
        asset_layout.addWidget(QLabel("现金:"))
        self.cash_label = QLabel("N/A")
        font = self.cash_label.font(); font.setPointSize(12); font.setBold(True) # 字体稍大加粗
        self.cash_label.setFont(font)
        asset_layout.addWidget(self.cash_label)
        asset_layout.addItem(QSpacerItem(40, 20, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)) # 增加间距
        asset_layout.addWidget(QLabel("持仓市值:"))
        self.portfolio_value_label = QLabel("N/A")
        self.portfolio_value_label.setFont(font)
        asset_layout.addWidget(self.portfolio_value_label)
        asset_layout.addItem(QSpacerItem(40, 20, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum))
        asset_layout.addWidget(QLabel("总资产:"))
        self.total_assets_label = QLabel("N/A")
        self.total_assets_label.setFont(font)
        asset_layout.addWidget(self.total_assets_label)
        asset_layout.addStretch()
        controls_layout.addLayout(asset_layout)

        # 参数设置
        params_layout = QHBoxLayout()
        params_layout.addWidget(QLabel("股票数量:"))
        self.num_stocks_spinbox = QSpinBox()
        self.num_stocks_spinbox.setRange(1, 100); self.num_stocks_spinbox.setValue(NUM_STOCKS_TO_HOLD)
        params_layout.addWidget(self.num_stocks_spinbox)
        params_layout.addWidget(QLabel("持有天数:"))
        self.hold_days_spinbox = QSpinBox()
        self.hold_days_spinbox.setRange(1, 60); self.hold_days_spinbox.setValue(3) # 默认值设为 3
        params_layout.addWidget(self.hold_days_spinbox)
        params_layout.addWidget(QLabel("卖出阈值:")) # 排名恶化阈值
        self.rank_boost_spinbox = QSpinBox()
        self.rank_boost_spinbox.setRange(1, 1000); self.rank_boost_spinbox.setValue(25) # 默认值设为 25
        params_layout.addWidget(self.rank_boost_spinbox)
        params_layout.addStretch()
        controls_layout.addLayout(params_layout)

        # 操作按钮
        button_layout = QHBoxLayout()
        # 修改按钮文本，使其更清晰
        self.rebalance_button = QPushButton("同步至模拟结果") # 修改按钮文本
        self.add_funds_button = QPushButton("调整资金")
        self.refresh_display_button = QPushButton("刷新显示")
        self.clear_positions_button = QPushButton("一键清空持仓") # 新增按钮
        button_layout.addWidget(self.rebalance_button)
        button_layout.addWidget(self.add_funds_button)
        button_layout.addWidget(self.refresh_display_button)
        button_layout.addWidget(self.clear_positions_button) 
        button_layout.addStretch()
        controls_layout.addLayout(button_layout)

        # 状态标签和短日志
        self.status_label = QLabel("状态: 空闲")
        self.status_label.setWordWrap(True)
        controls_layout.addWidget(self.status_label)
        controls_layout.addWidget(QLabel("运行日志:"))
        self.log_display = QLineEdit()
        self.log_display.setReadOnly(True)
        controls_layout.addWidget(self.log_display)
        controls_layout.addStretch() # 让控件靠上

        # 添加左右 widget 到常规布局 (表格在左60%，控制在右40%)
        regular_layout.addWidget(current_positions_widget, 3) # 当前持仓表格 60%
        regular_layout.addWidget(controls_widget, 2)          # 控制区域 40%

        # 将常规模式容器添加到主布局，占据下方空间
        main_layout.addWidget(regular_container, 3) # 占据 30% 高度

        # --- 连接信号与槽 ---
        # （注意：确保连接了所有需要的信号）
        # 常规模式按钮
        self.rebalance_button.clicked.connect(self.run_rebalance) # 连接到修改后的同步逻辑
        self.add_funds_button.clicked.connect(self.show_add_funds_dialog)
        self.refresh_display_button.clicked.connect(self._refresh_all_displays)
        self.clear_positions_button.clicked.connect(self.clear_all_positions)
        # 历史模拟按钮
        self.hist_start_btn.clicked.connect(self._hist_start_sim)
        self.hist_next_btn.clicked.connect(self._hist_next_day)
        self.hist_auto_btn.clicked.connect(self._hist_toggle_auto)
        self.export_trades_btn.clicked.connect(self._export_trade_records) # 导出按钮信号

        # --- 初始状态设置 ---
        self.hist_next_btn.setEnabled(False)
        self.hist_auto_btn.setEnabled(False)
        # 数据库检查
        if not self.db_exists:
            self.rebalance_button.setEnabled(False)
            self.add_funds_button.setEnabled(False)
            self.refresh_display_button.setEnabled(False)
            self.hist_start_btn.setEnabled(False)
            self.status_label.setText("状态: DB文件丢失")
            QMessageBox.warning(self, "数据库错误", f"数据库文件未找到: {DB_PATH}\n部分功能将不可用。")

        # 历史模拟状态变量 (保持不变)
        self.hist_sim_dates = []
        self.hist_sim_idx = 0
        self.hist_sim_state = {}
        self.hist_sim_running = False
        self.hist_sim_timer = QTimer(self)
        self.hist_sim_timer.timeout.connect(self._hist_next_day)

    def clear_all_positions(self):
            """一键清仓功能：清空所有持仓记录"""
            # --- 1. 确认对话框 ---
            reply = QMessageBox.question(
                self,
                '确认清仓',
                '确定要清空所有当前持仓记录吗？\n此操作将把所有持仓标记为已卖出/已清除，并尝试更新现金。\n操作不可撤销！',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No  # 默认焦点在 No 上
            )

            if reply != QMessageBox.StandardButton.Yes:
                self.status_label.setText("状态: 用户取消清仓操作")
                return

            self.status_label.setText("状态: 正在执行一键清仓...")
            QApplication.processEvents()

            conn = None
            try:
                # --- 2. 连接数据库 ---
                conn = connect_db(DB_PATH)
                if not conn:
                    QMessageBox.critical(self, "错误", "无法连接数据库执行清仓")
                    self.status_label.setText("状态: 清仓失败 - 无法连接数据库")
                    return

                # --- 3. 获取当前持仓 ---
                # 使用 load_full_positions 以便获取成本价用于粗略 PnL
                current_positions = load_full_positions(conn)
                if not current_positions:
                    QMessageBox.information(self, "提示", "当前没有需要清空的持仓记录")
                    self.status_label.setText("状态: 无持仓可清空")
                    return

                holding_codes = list(current_positions.keys())
                print(f"准备清空 {len(holding_codes)} 条持仓记录: {holding_codes}")

                # --- 4. 获取最新价格 (用于估算回收现金和记录) ---
                # 从 fundamentals 获取最新价，如果获取不到，则按 0 处理或标记清除
                latest_prices = {}
                if holding_codes:
                    placeholders = ','.join(['?'] * len(holding_codes))
                    # 尝试从 stock_fundamentals 获取最新价格
                    # 注意：如果 fundamentals 更新不及时，这里价格可能不准
                    price_query = f"SELECT stock_code, latest_price FROM stock_fundamentals WHERE stock_code IN ({placeholders})"
                    try:
                        price_df = pd.read_sql(price_query, conn, params=holding_codes)
                        latest_prices = price_df.set_index('stock_code')['latest_price'].to_dict()
                        print(f"获取到 {len(latest_prices)}/{len(holding_codes)} 只股票的最新价格用于清仓记录。")
                    except Exception as e_price:
                        print(f"查询 stock_fundamentals 获取价格时出错: {e_price}，将尝试无价格清仓。")
                        latest_prices = {} # 出错则价格字典为空

                # --- 5. 执行清仓 ---
                total_cash_recovered = 0.0
                cleared_count = 0
                failed_count = 0
                # 使用今天的日期作为清仓日期
                today_str_db = datetime.now().strftime('%Y-%m-%d')
                status_msgs_details = [f"=== 一键清仓 ({today_str_db}) ==="]

                for code, pos_data in current_positions.items():
                    shares_to_clear = pos_data.get('shares', 0)
                    cost_price_clear = pos_data.get('cost_price') # 获取成本用于 PnL 估算

                    if shares_to_clear <= 0:
                        continue # 跳过股数为0的记录

                    sell_price = latest_prices.get(code)
                    sell_price_valid = None

                    # 检查获取到的价格是否有效
                    if sell_price is not None and pd.notna(sell_price):
                        try:
                            sell_price_f = float(sell_price)
                            if sell_price_f > 0:
                                sell_price_valid = sell_price_f
                        except (ValueError, TypeError):
                            pass # 价格无效

                    if sell_price_valid is not None:
                        # --- 情况A: 有有效价格，调用 execute_sell_db ---
                        # is_full_sell 设为 True，因为是清仓
                        sell_ok, log_msg = execute_sell_db(conn, code, sell_price_valid, shares_to_clear, today_str_db, True, cost_price_clear)
                        status_msgs_details.append(log_msg)
                        if sell_ok:
                            total_cash_recovered += shares_to_clear * sell_price_valid
                            cleared_count += 1
                        else:
                            failed_count += 1
                    else:
                        # --- 情况B: 无有效价格，直接更新数据库状态 ---
                        try:
                            cursor = conn.cursor()
                            # 将状态设为 SOLD，但 sell_price 留空或设为 0/NULL
                            cursor.execute(
                                "UPDATE positions SET status='SOLD', sell_price=?, sell_date=?, shares=0 WHERE stock_code=? AND status='HOLDING'",
                                (None, today_str_db, code) # sell_price 设为 None (即 NULL)
                            )
                            conn.commit()
                            if cursor.rowcount > 0:
                                log_msg = f"清空 {code}: {shares_to_clear:,} 股 (无有效价格记录，标记为SOLD)"
                                status_msgs_details.append(log_msg)
                                cleared_count += 1
                                print(log_msg)
                            else:
                                log_msg = f"清空 {code} 失败: 未找到 HOLDING 记录？"
                                status_msgs_details.append(log_msg)
                                failed_count += 1
                                print(log_msg)
                        except Exception as e_update:
                            log_msg = f"直接更新清空 {code} 状态时数据库出错: {e_update}"
                            status_msgs_details.append(log_msg)
                            failed_count += 1
                            print(log_msg)
                            try: conn.rollback()
                            except: pass

                # --- 6. 更新现金 ---
                current_cash = load_cash(conn)
                new_cash = current_cash + total_cash_recovered
                if save_cash(conn, new_cash):
                    status_msgs_details.append(f"\n现金更新成功: {current_cash:,.2f} + {total_cash_recovered:,.2f} = {new_cash:,.2f}")
                else:
                    status_msgs_details.append("\n严重警告: 保存最终现金失败!")

                # --- 7. 显示结果 & 刷新 ---
                final_summary = f"清仓完成: {cleared_count} 个成功标记, {failed_count} 个失败。回收现金约 {total_cash_recovered:,.2f}"
                status_msgs_details.append(f"\n{final_summary}")
                print(final_summary)
                QMessageBox.information(self, "清仓结果", "\n".join(status_msgs_details))
                self.status_label.setText(f"状态: {final_summary}")
                self._refresh_all_displays() # 刷新界面

            except Exception as e:
                error_msg = f"一键清仓过程中发生错误: {e}"
                QMessageBox.critical(self, "清仓错误", error_msg)
                self.status_label.setText(f"状态: 清仓错误 - {e}")
                print(error_msg)
                traceback.print_exc()
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception as e_close:
                        print(f"关闭清仓DB连接时出错: {e_close}")
    @Slot()
    def _refresh_all_displays(self):
        if not self.db_exists: QMessageBox.warning(self, "错误", "数据库文件未找到。"); return
        self.status_label.setText("状态: 正在刷新显示..."); QApplication.processEvents()
        self._update_cash_display()
        portfolio_value = self._update_positions_display() # 更新持仓并获取市值
        self._update_summary_display(portfolio_value) # 更新汇总标签
        self.status_label.setText("状态: 显示已刷新")

    def clear_all_positions(self):
        """一键清仓功能：清空所有持仓"""
        # 确认对话框
        reply = QMessageBox.question(
            self, 
            '确认清仓', 
            '确定要清空所有持仓吗？\n此操作不可撤销！',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return

        conn = None
        try:
            # 连接数据库
            conn = connect_db(DB_PATH)
            if not conn:
                QMessageBox.critical(self, "错误", "无法连接数据库")
                return

            # 获取当前持仓
            positions = load_positions(conn)
            if not positions:
                QMessageBox.information(self, "提示", "当前没有持仓需要清空")
                return

            # 获取最新价格
            codes = list(positions.keys())
            placeholders = ','.join(['?'] * len(codes))
            price_query = f"SELECT stock_code, latest_price FROM stock_fundamentals WHERE stock_code IN ({placeholders})"
            price_df = pd.read_sql(price_query, conn, params=codes)
            latest_prices = price_df.set_index('stock_code')['latest_price'].to_dict()

            # 执行清仓
            total_cash_recovered = 0
            today_str = datetime.now().strftime('%Y-%m-%d')
            cleared_count = 0
            failed_count = 0
            status_msgs = ["=== 一键清仓执行记录 ==="]

            for code, pos in positions.items():
                shares = pos.get('shares', 0)
                if shares <= 0:
                    continue

                price = latest_prices.get(code)
                if price and pd.notna(price) and price > 0:
                    # 有价格数据时正常卖出
                    success, msg = execute_sell_db(
                        conn, 
                        code, 
                        price, 
                        shares, 
                        today_str, 
                        True, 
                        pos.get('cost_price')
                    )
                    if success:
                        total_cash_recovered += shares * price
                        cleared_count += 1
                        status_msgs.append(f"成功卖出 {code}: {shares}股 @ {price:.2f}")
                    else:
                        failed_count += 1
                        status_msgs.append(f"卖出失败 {code}: {msg}")
                else:
                    # 无价格数据时直接清零
                    try:
                        cursor = conn.cursor()
                        cursor.execute(
                            "UPDATE positions SET status='CLEARED', sell_date=? WHERE stock_code=? AND status='HOLDING'",
                            (today_str, code)
                        )
                        conn.commit()
                        cleared_count += 1
                        status_msgs.append(f"直接清零 {code}: {shares}股 (无价格数据)")
                    except Exception as e:
                        failed_count += 1
                        status_msgs.append(f"清零失败 {code}: {str(e)}")

            # 更新现金
            current_cash = load_cash(conn)
            new_cash = current_cash + total_cash_recovered
            if save_cash(conn, new_cash):
                status_msgs.append(f"\n清仓后现金: {new_cash:,.2f} (回收: {total_cash_recovered:,.2f})")
            else:
                status_msgs.append("\n警告: 保存最终现金失败！")

            # 显示结果
            status_msgs.append(f"\n清仓完成: {cleared_count}个成功, {failed_count}个失败")
            QMessageBox.information(self, "清仓结果", "\n".join(status_msgs))
            
            # 刷新显示
            self._refresh_all_displays()

        except Exception as e:
            error_msg = f"清仓过程出错: {str(e)}"
            QMessageBox.critical(self, "错误", error_msg)
            print(error_msg)
            traceback.print_exc()
        finally:
            if conn:
                try:
                    conn.close()
                except Exception as e:
                    print(f"关闭数据库连接失败: {e}")

        # _update_positions_display 方法 (使用 V1.7 返回市值的版本)
    @Slot()
    def _update_positions_display(self):
        """(V1.7) 加载准备好的持仓数据，更新模型，并返回总持仓市值。"""
        total_portfolio_value = 0.0
        if not self.db_exists: return total_portfolio_value
        print("正在更新持仓显示...")
        position_data_list = _prepare_positions_for_display(DB_PATH)
        if position_data_list:
            for pos in position_data_list:
                if pos.get('market_value') is not None:
                    total_portfolio_value += pos['market_value']
        self.positions_model.updateData(position_data_list)
        print(f"持仓表格已更新，共 {len(position_data_list)} 条记录。总市值: {total_portfolio_value:,.2f}")
        return total_portfolio_value # 返回计算值

    # _update_summary_display 方法 (新增 V1.7)
    def _update_summary_display(self, portfolio_value):
        """根据当前的现金和传入的持仓市值，更新界面上的汇总标签。"""
        self.current_portfolio_value = portfolio_value
        total_assets = self.current_cash + self.current_portfolio_value
        self.cash_label.setText(f"￥{self.current_cash:,.2f}")
        self.portfolio_value_label.setText(f"￥{self.current_portfolio_value:,.2f}")
        self.total_assets_label.setText(f"￥{total_assets:,.2f}")
        print(f"汇总信息更新：现金={self.current_cash:,.2f}, 持仓={self.current_portfolio_value:,.2f}, 总资产={total_assets:,.2f}")

    # _update_cash_display 方法 (保持 V1.5.3 不变 - 短连接)
    def _update_cash_display(self):
        conn = None
        try:
            conn = connect_db(DB_PATH)
            if conn:
                self.current_cash = load_cash(conn)  # 只加载到 self.current_cash
            else:
                self.cash_label.setText("错误: 无法连接DB")
                self.rebalance_button.setEnabled(False)
                self.add_funds_button.setEnabled(False)
                self.refresh_display_button.setEnabled(False)
        except Exception as e:
            print(f"更新现金显示时出错: {e}")
            self.cash_label.setText("错误: 加载失败")
        finally:
            if conn:
                try:
                    conn.close()
                except sqlite3.Error as e_close:
                    print(f"关闭现金更新DB连接失败: {e_close}")

# 在 SimulatorAppUI 类中添加新方法

    @Slot(QPoint) # QPoint 是点击位置的坐标
    def _show_positions_context_menu(self, pos):
        """显示当前持仓表格的右键菜单"""
        # 获取全局坐标，用于菜单定位
        global_pos = self.positions_view.viewport().mapToGlobal(pos)
        # 获取点击位置对应的模型索引
        index = self.positions_view.indexAt(pos)

        menu = QMenu(self)

        # --- 增加持仓 Action ---
        add_action = menu.addAction("增加持仓...")
        add_action.triggered.connect(self._add_position_dialog) # 连接到处理函数

        # --- 如果点击在有效的数据行上 ---
        if index.isValid():
            try:
                # 获取被点击行的数据 (需要模型支持或直接读取)
                # 为了简单，我们先只获取股票代码 (假设在第0列)
                stock_code_item = self.positions_model.index(index.row(), 0) # 获取代码列的索引
                stock_code = self.positions_model.data(stock_code_item, Qt.ItemDataRole.DisplayRole)

                if stock_code: # 确保获取到了代码
                    menu.addSeparator() # 分隔符

                    # --- 修改持仓 Action ---
                    modify_action = menu.addAction(f"修改 {stock_code} 持仓...")
                    # 使用 lambda 传递当前股票代码给处理函数
                    modify_action.triggered.connect(lambda checked=False, code=stock_code: self._modify_position_dialog(code))

                    # --- 删除持仓 Action ---
                    delete_action = menu.addAction(f"删除 {stock_code} 持仓...")
                    # 使用 lambda 传递当前股票代码给处理函数
                    delete_action.triggered.connect(lambda checked=False, code=stock_code: self._delete_position_confirm(code))

            except Exception as e:
                print(f"获取右键菜单数据时出错: {e}")
                traceback.print_exc()

        # 显示菜单
        menu.exec(global_pos)
# 在 SimulatorAppUI 类中添加新方法

    @Slot()
    def _add_position_dialog(self):
        """弹出对话框让用户输入新持仓信息"""
        # 使用 QInputDialog 获取多个输入比较繁琐，理想情况是用自定义 QDialog
        # 这里用 QInputDialog 做简单示例

        # 1. 获取股票代码
        code, ok1 = QInputDialog.getText(self, "增加持仓", "请输入股票代码 (6位数字):")
        if not ok1 or not code or not code.isdigit() or len(code) != 6:
            if ok1: QMessageBox.warning(self, "输入无效", "股票代码必须是6位数字。")
            return

        # 2. 获取股数 (整数，需大于0，最好是 LOT_SIZE 的倍数)
        shares, ok2 = QInputDialog.getInt(self, "增加持仓", f"请输入 {code} 的股数:", 100, 100, 10000000, 100)
        if not ok2 or shares <= 0:
            if ok2: QMessageBox.warning(self, "输入无效", "股数必须大于0。")
            return
        # 可选：强制为 LOT_SIZE 的倍数
        shares = (shares // LOT_SIZE) * LOT_SIZE
        if shares == 0:
             QMessageBox.warning(self, "输入无效", f"股数向下取整到 {LOT_SIZE} 的倍数后为0。")
             return


        # 3. 获取成本价 (浮点数，需大于0)
        cost, ok3 = QInputDialog.getDouble(self, "增加持仓", f"请输入 {code} 的成本价:", 10.0, 0.01, 10000.0, 2)
        if not ok3 or cost <= 0:
            if ok3: QMessageBox.warning(self, "输入无效", "成本价必须大于0。")
            return

        # 4. 执行数据库添加操作 (调用 DB 函数)
        self._execute_db_add(code, shares, cost)


    @Slot(str) # 接收传入的 stock_code
    def _modify_position_dialog(self, stock_code):
        """弹出对话框修改现有持仓"""
        print(f"准备修改 {stock_code}...")
        # --- 先从数据库获取当前信息作为默认值 ---
        current_shares = 0
        current_cost = 0.0
        conn = None
        try:
            conn = connect_db(DB_PATH)
            if not conn: raise ConnectionError("无法连接数据库获取当前信息")
            cursor = conn.cursor()
            # 使用 buy_price 作为成本价
            cursor.execute("SELECT shares, buy_price FROM positions WHERE stock_code=? AND status='HOLDING'", (stock_code,))
            result = cursor.fetchone()
            if result:
                current_shares = int(result[0]) if result[0] is not None else 0
                current_cost = float(result[1]) if result[1] is not None else 0.0
            else:
                QMessageBox.warning(self, "错误", f"在数据库中未找到代码为 {stock_code} 的持仓记录。")
                return
        except Exception as e:
            QMessageBox.critical(self, "数据库错误", f"获取当前持仓信息失败: {e}")
            return
        finally:
            if conn: conn.close()

        # --- 弹出对话框获取新值 ---
        new_shares, ok1 = QInputDialog.getInt(self, "修改持仓", f"修改 {stock_code} 的股数:", current_shares, 0, 10000000, 100)
        if not ok1: return # 用户取消

        new_cost, ok2 = QInputDialog.getDouble(self, "修改持仓", f"修改 {stock_code} 的成本价:", current_cost, 0.01, 10000.0, 2)
        if not ok2: return # 用户取消

        if new_shares == current_shares and new_cost == current_cost:
            QMessageBox.information(self, "提示", "未做任何修改。")
            return

        if new_shares < 0 or new_cost <= 0: # 股数可以为0（相当于清仓）
             QMessageBox.warning(self, "输入无效", "股数不能为负，成本价必须大于0。")
             return

        # 可选：强制股数为 LOT_SIZE 倍数
        new_shares = (new_shares // LOT_SIZE) * LOT_SIZE

        # 5. 执行数据库修改操作
        self._execute_db_modify(stock_code, new_shares, new_cost)


    @Slot(str) # 接收传入的 stock_code
    def _delete_position_confirm(self, stock_code):
        """确认并执行删除操作"""
        reply = QMessageBox.question(self, '确认删除',
                                     f"确定要删除持仓记录 {stock_code} 吗？\n（注意：这将把该记录状态标记为DELETED）",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            self._execute_db_delete(stock_code)

    def _execute_db_add(self, code, shares, cost_price):
        """执行数据库插入或更新操作（通过调用 execute_buy_db）"""
        print(f"准备向数据库添加/更新持仓: {code}, Shares: {shares}, Cost: {cost_price}")
        conn = None
        success = False
        log_msg = "未知错误"
        try:
            conn = connect_db(DB_PATH)
            if not conn: raise ConnectionError("无法连接数据库")

            today_str = datetime.now().strftime('%Y-%m-%d')
            # 调用现有的 execute_buy_db，它能处理插入新纪录或更新现有记录（加仓）
            # 注意：我们没有 rank 信息，传入 None
            buy_ok, log_msg_db, _, _ = execute_buy_db(conn, code, cost_price, shares, today_str, buy_rank=None)
            success = buy_ok
            log_msg = log_msg_db

        except Exception as e:
            log_msg = f"添加/更新持仓 {code} 时出错: {e}"
            print(log_msg); traceback.print_exc()
            success = False
        finally:
            if conn: conn.close()

        if success:
            QMessageBox.information(self, "成功", f"持仓 {code} 添加/更新成功。\n{log_msg}")
            self._refresh_all_displays() # 操作成功后刷新界面
        else:
            QMessageBox.critical(self, "失败", f"添加/更新持仓 {code} 失败。\n{log_msg}")


    def _execute_db_modify(self, code, new_shares, new_cost_price):
        """执行数据库修改操作（直接 UPDATE）"""
        print(f"准备修改数据库持仓: {code}, New Shares: {new_shares}, New Cost: {new_cost_price}")
        conn = None
        success = False
        log_msg = f"准备修改 {code}..."
        try:
            conn = connect_db(DB_PATH)
            if not conn: raise ConnectionError("无法连接数据库")
            cursor = conn.cursor()
            today_str = datetime.now().strftime('%Y-%m-%d') # 更新修改日期

            # 如果新股数为 0，则执行删除逻辑
            if new_shares == 0:
                 reply = QMessageBox.warning(self, "确认清空",
                                      f"股数设置为 0，将删除持仓 {code}。\n确定吗？",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
                 if reply == QMessageBox.StandardButton.Yes:
                      return self._execute_db_delete(code) # 调用删除逻辑
                 else:
                      return # 用户取消

            # 执行 UPDATE
            # 注意：这里直接修改了 buy_price 和 shares，以及 buy_date
            # 如果需要更复杂的成本计算（比如加权平均），逻辑会更复杂
            sql = """
                UPDATE positions
                SET shares = ?, buy_price = ?, buy_date = ?
                WHERE stock_code = ? AND status = 'HOLDING'
            """
            cursor.execute(sql, (new_shares, new_cost_price, today_str, code))
            conn.commit()

            if cursor.rowcount > 0:
                success = True
                log_msg = f"成功修改持仓 {code} 为: {new_shares} 股, 成本价 {new_cost_price:.2f}"
                print(log_msg)
            else:
                success = False
                log_msg = f"修改持仓 {code} 失败: 未找到状态为 HOLDING 的记录，或数据无变化。"
                print(log_msg)

        except Exception as e:
            log_msg = f"修改持仓 {code} 时出错: {e}"
            print(log_msg); traceback.print_exc()
            try: conn.rollback()
            except: pass
            success = False
        finally:
            if conn: conn.close()

        if success:
            QMessageBox.information(self, "成功", log_msg)
            self._refresh_all_displays()
        else:
            QMessageBox.critical(self, "失败", log_msg)


    def _execute_db_delete(self, code):
        """执行数据库删除操作（更新状态为 DELETED）"""
        print(f"准备从数据库删除持仓: {code}")
        conn = None
        success = False
        log_msg = f"准备删除 {code}..."
        try:
            conn = connect_db(DB_PATH)
            if not conn: raise ConnectionError("无法连接数据库")
            cursor = conn.cursor()
            today_str = datetime.now().strftime('%Y-%m-%d')
            # 使用更新状态的方式代替物理删除，更安全
            sql = """
                UPDATE positions
                SET status = 'DELETED', shares = 0, sell_date = ?, sell_price = NULL
                WHERE stock_code = ? AND status = 'HOLDING'
            """
            cursor.execute(sql, (today_str, code))
            conn.commit()

            if cursor.rowcount > 0:
                success = True
                log_msg = f"成功将持仓 {code} 状态标记为 DELETED。"
                print(log_msg)
            else:
                success = False
                log_msg = f"删除持仓 {code} 失败: 未找到状态为 HOLDING 的记录。"
                print(log_msg)

        except Exception as e:
            log_msg = f"删除持仓 {code} 时出错: {e}"
            print(log_msg); traceback.print_exc()
            try: conn.rollback()
            except: pass
            success = False
        finally:
            if conn: conn.close()

        if success:
            QMessageBox.information(self, "成功", log_msg)
            self._refresh_all_displays()
        else:
            QMessageBox.critical(self, "失败", log_msg)

# --- !! 替换这个方法 !! ---
    @Slot()
    def run_rebalance(self):
        """
        (修正版 - 同步至模拟最终状态 + 交易日检查 + 使用最新交易日执行)
        修改后的“执行模拟调仓”功能：
        0. 检查当前是否为交易日。
        1. 检查历史模拟是否完成，并获取其最终持仓状态作为目标。
        2. 对比当前DB持仓与目标状态，计算同步所需的操作(Delta)。
        3. 获取最新交易日的价格用于执行。
        4. 使用自定义对话框显示具体操作计划并向用户确认。
        5. 如果确认，则通过后台线程执行数据库同步操作（使用最新交易日记录）。
        """
        print("--- '执行模拟调仓'按钮点击 (同步至模拟最终状态 + 交易日检查) ---")
        thread_id_main = threading.current_thread().name
        print(f"[{thread_id_main}] 主线程开始执行 run_rebalance...")

        # --- 0. 交易日检查 ---
        today_date = datetime.now().date()
        # 确保 is_trading_day 函数已定义
        try:
            if not is_trading_day(DB_PATH, today_date):
                QMessageBox.warning(self, "操作无效", f"今天是 {today_date.strftime('%Y-%m-%d')}，非交易日，无法执行同步操作。\n请在下一个交易日尝试。")
                print(f"[{thread_id_main}] 操作中止：今天是 {today_date.strftime('%Y-%m-%d')}，非交易日。")
                self.status_label.setText("状态: 非交易日，无法同步")
                return
        except NameError:
             QMessageBox.critical(self, "代码错误", "辅助函数 'is_trading_day' 未定义！")
             print(f"[{thread_id_main}] 错误: is_trading_day 未定义。")
             return
        except Exception as e_check_day:
             QMessageBox.critical(self, "错误", f"检查交易日时出错: {e_check_day}")
             print(f"[{thread_id_main}] 错误: 检查交易日失败 - {e_check_day}")
             return
        print(f"[{thread_id_main}] 今天是交易日，继续执行同步...")
        # --- 交易日检查结束 ---

        # --- 检查数据库文件 ---
        if not self.db_exists:
            QMessageBox.critical(self, "错误", "数据库文件未找到。")
            return

        # --- 检查是否有其他数据库任务在运行 ---
        current_worker = getattr(self, 'db_rebalance_worker', None)
        current_thread = getattr(self, 'worker_thread', None)
        if current_worker and current_thread and current_thread.isRunning():
             QMessageBox.information(self, "提示", "数据库调仓任务正在进行中，请稍候。")
             return

        # --- 1. 检查历史模拟是否完成 & 获取最终状态 ---
        if not getattr(self, 'hist_simulation_finished', False):
            QMessageBox.warning(self, "操作无效", "请先运行并等待历史模拟回测完成。")
            print(f"[{thread_id_main}] 检查失败: 历史模拟未标记为完成。")
            return

        # 从 self 获取最终状态 (这些应在 _hist_next_day 结束时被正确赋值)
        target_positions_state = getattr(self, 'final_sim_positions', None)
        target_cash_state = getattr(self, 'final_sim_cash', None)
        latest_prices_state = getattr(self, 'final_sim_prices', None) # 这是模拟最后一日的价格，可能不用
        final_rank_dict = getattr(self, 'final_rank_dict_stored', None) # 未筛选排名

        # 检查必要数据是否存在
        if target_positions_state is None or target_cash_state is None or final_rank_dict is None:
             QMessageBox.warning(self, "操作无效", "未能获取到历史模拟完整的最终状态数据 (持仓/现金/排名)。\n请重新运行历史模拟。")
             print(f"[{thread_id_main}] 检查失败: 最终模拟状态数据不完整 (positions:{target_positions_state is not None}, cash:{target_cash_state is not None}, ranks:{final_rank_dict is not None})。")
             return

        # --- 转换目标状态为字典 {code: shares} ---
        target_portfolio_dict = {code: data.get('shares', 0)
                                 for code, data in target_positions_state.items()
                                 if data.get('shares', 0) > 0}

        print(f"[{thread_id_main}] 获取到模拟最终目标持仓 {len(target_portfolio_dict)} 只。")
        name_map = getattr(self, 'name_map', {}) # 获取名称映射

        self.status_label.setText("状态: 正在准备数据库同步计划...")
        QApplication.processEvents()

        # --- 2. 加载当前数据库状态 ---
        print(f"[{thread_id_main}] 加载当前数据库状态...")
        conn = None
        current_db_holdings = {}
        current_db_cash = 0.0
        try:
            conn = connect_db(DB_PATH)
            if not conn: raise ConnectionError("无法连接数据库加载当前状态")
            # *** 必须确保 load_full_positions 函数在 7.28 文件中存在且能工作 ***
            current_db_holdings = load_full_positions(conn)
            current_db_cash = load_cash(conn)
        except NameError:
            QMessageBox.critical(self, "代码错误", "函数 'load_full_positions' 未定义！\n无法加载当前持仓详情。")
            print(f"[{thread_id_main}] 错误: load_full_positions 未定义。")
            if conn: conn.close()
            self.status_label.setText("状态: 错误 - load_full_positions 未定义")
            return
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载当前数据库状态失败: {e}")
            print(f"[{thread_id_main}] 错误: 加载当前DB状态失败 - {e}")
            if conn: conn.close()
            self.status_label.setText("状态: 加载DB状态失败")
            return
        finally:
            if conn:
                try: conn.close()
                except Exception as e_close: print(f"关闭DB连接(加载状态)出错: {e_close}")

        print(f"[{thread_id_main}] 当前数据库状态: 现金 {current_db_cash:,.2f}, 持仓 {len(current_db_holdings)} 只")

        # --- 3. 对比当前与目标，计算操作列表 (Delta) ---
        print(f"[{thread_id_main}] 执行比较逻辑，生成具体同步操作列表...")
        actions_sell = {} # {code: shares_to_sell}
        actions_buy = {}  # {code: {'shares': shares_to_buy, 'rank': rank}}

        db_codes = set(current_db_holdings.keys())
        target_codes = set(target_portfolio_dict.keys())

        codes_to_sell_fully = db_codes - target_codes
        codes_to_buy_new = target_codes - db_codes
        codes_to_compare = db_codes.intersection(target_codes)

        # 1. 完全卖出
        for code in codes_to_sell_fully:
            shares_in_db = current_db_holdings[code].get('shares', 0)
            if shares_in_db > 0:
                actions_sell[code] = shares_in_db
                print(f"  - 同步计划: 完全卖出 {code} {actions_sell[code]} 股")

        # 2. 全新买入
        for code in codes_to_buy_new:
            target_shares = target_portfolio_dict.get(code, 0)
            if target_shares > 0:
                buy_rank_val = final_rank_dict.get(code) # 使用模拟最终排名
                actions_buy[code] = {'shares': target_shares, 'rank': buy_rank_val}
                print(f"  - 同步计划: 全新买入 {code} {target_shares} 股 (模拟Rank: {buy_rank_val})")

        # 3. 比较共同持有
        for code in codes_to_compare:
            db_shares = current_db_holdings[code].get('shares', 0)
            target_shares = target_portfolio_dict.get(code, 0)
            diff = target_shares - db_shares
            if diff > 0: # 加仓
                buy_rank_val = final_rank_dict.get(code)
                actions_buy[code] = {'shares': diff, 'rank': buy_rank_val}
                print(f"  - 同步计划: 加仓买入 {code} {diff} 股 (从 {db_shares} 到 {target_shares}, 模拟Rank: {buy_rank_val})")
            elif diff < 0: # 减仓
                actions_sell[code] = abs(diff)
                print(f"  - 同步计划: 部分卖出 {code} {abs(diff)} 股 (从 {db_shares} 到 {target_shares})")

        print(f"[{thread_id_main}] 对比完成: 计划卖出 {len(actions_sell)} 项, 计划买入 {len(actions_buy)} 项")

# --- 4. 获取执行价格 (优先最新日，回退至前一日) ---
        print(f"[{thread_id_main}] 获取执行操作所需的交易日价格...")
        all_involved_codes = list(set(actions_sell.keys()) | set(actions_buy.keys()))
        latest_prices = {}          # 最终使用的价格字典 {code: price}
        used_prev_day_price = {}    # 记录哪些股票用了前一日价格 {code: price}
        skipped_due_no_price = set()
        latest_trading_date = None  # 存储找到的最新交易日 (用于记录和执行日期)
        previous_trading_date = None # 存储找到的前一个交易日 (用于价格回退)

        if all_involved_codes:
            conn = None
            try:
                conn = connect_db(DB_PATH)
                if not conn: raise ConnectionError("无法连接数据库获取价格")

                # --- 获取最新交易日和前一个交易日 (从 exp_trade) ---
                cursor = conn.cursor()
                cursor.execute("SELECT date FROM exp_trade WHERE is_open = 1 ORDER BY date DESC LIMIT 1")
                latest_trade_date_result = cursor.fetchone()
                latest_trading_date = latest_trade_date_result[0] if latest_trade_date_result else None

                if latest_trading_date:
                    cursor.execute("SELECT date FROM exp_trade WHERE date < ? AND is_open = 1 ORDER BY date DESC LIMIT 1", (latest_trading_date,))
                    prev_trade_date_result = cursor.fetchone()
                    previous_trading_date = prev_trade_date_result[0] if prev_trade_date_result else None
                    print(f"[{thread_id_main}] 获取到最新交易日: {latest_trading_date}, 前一交易日: {previous_trading_date}")
                else:
                    raise ValueError("无法从 exp_trade 获取最新交易日。")

                # --- 从 stock_prices 获取价格，优先最新日，其次前一日 ---
                # 1. 尝试获取最新交易日价格
                placeholders = ','.join(['?'] * len(all_involved_codes))
                query_latest = f"""SELECT stock_code, close
                            FROM stock_prices
                            WHERE stock_code IN ({placeholders}) AND date = ?"""
                price_df_latest = pd.read_sql(query_latest, conn, params=(*all_involved_codes, latest_trading_date))
                latest_prices_temp = price_df_latest.set_index('stock_code')['close'].to_dict()

                for code in all_involved_codes:
                    price = latest_prices_temp.get(code)
                    if price is not None and pd.notna(price):
                        try:
                            price_f = float(price)
                            if price_f > 0:
                                latest_prices[code] = price_f # 存储有效的最新日价格
                        except (ValueError, TypeError):
                            pass # 转换失败，视为无有效价格，后面会尝试前一日

                # 2. 对缺失的股票，尝试获取前一日价格
                codes_needing_prev_price = [code for code in all_involved_codes if code not in latest_prices]
                if previous_trading_date and codes_needing_prev_price:
                    print(f"[{thread_id_main}] {len(codes_needing_prev_price)} 只股票在 {latest_trading_date} 无有效价格，尝试获取 {previous_trading_date} 的价格...")
                    placeholders_prev = ','.join(['?'] * len(codes_needing_prev_price))
                    query_prev = f"""SELECT stock_code, close
                                FROM stock_prices
                                WHERE stock_code IN ({placeholders_prev}) AND date = ?"""
                    price_df_prev = pd.read_sql(query_prev, conn, params=(*codes_needing_prev_price, previous_trading_date))
                    prev_prices_temp = price_df_prev.set_index('stock_code')['close'].to_dict()

                    for code in codes_needing_prev_price:
                        prev_price = prev_prices_temp.get(code)
                        if prev_price is not None and pd.notna(prev_price):
                            try:
                                price_f = float(prev_price)
                                if price_f > 0:
                                    latest_prices[code] = price_f # 使用前一日价格填充
                                    used_prev_day_price[code] = price_f # 记录使用了前一日价格
                                    # 可选：打印更详细的日志
                                    # print(f"  - Info: 股票 {code} 使用了前一日 ({previous_trading_date}) 的价格: {price_f:.2f}")
                            except (ValueError, TypeError):
                                pass # 转换失败，视为无有效价格

                # 3. 计算最终跳过的股票 (两天都找不到价格)
                skipped_due_no_price = set(all_involved_codes) - set(latest_prices.keys())

                print(f"[{thread_id_main}] 最终获取了 {len(latest_prices)} 只股票的有效价格（其中 {len(used_prev_day_price)} 只使用了前一日 {previous_trading_date} 的价格）。")
                if skipped_due_no_price:
                    print(f"警告: 以下股票因在 {latest_trading_date} 和 {previous_trading_date} (若尝试) 均无有效价格，将跳过操作: {skipped_due_no_price}")

            except Exception as e:
                QMessageBox.critical(self, "错误", f"获取交易日或价格时出错: {e}\n同步操作无法进行。")
                print(f"[{thread_id_main}] 获取价格时出错: {e}")
                traceback.print_exc()
                self.status_label.setText("状态: 获取价格失败")
                if conn: conn.close() # 确保关闭连接
                return # 获取价格失败则中止
            finally:
                if conn:
                    try: conn.close()
                    except Exception as e_close: print(f"关闭DB连接(获取价格)出错: {e_close}")
        else:
            print(f"[{thread_id_main}] 无需执行买卖操作，跳过价格获取。")
            QMessageBox.information(self,"提示", "对比后发现无需执行任何数据库操作。")
            self.status_label.setText("状态: 无需操作")
            return # 如果无需操作，直接返回

        # --- 5. 格式化确认信息 --- (这部分及之后基本不变，但需要利用 used_prev_day_price)
        print(f"[{thread_id_main}] 格式化确认信息...")
        # 确保 latest_trading_date 有值
        if latest_trading_date is None:
            QMessageBox.critical(self, "内部错误", "未能确定最新交易日，无法继续。")
            return
        execution_date_str = latest_trading_date # 使用获取到的最新交易日作为执行日期

        message_lines = [f"将当前数据库同步至历史模拟最终状态。\n操作将在数据库中以日期【{execution_date_str}】记录，并使用该日或前一日收盘价执行。\n"]
        if skipped_due_no_price:
             message_lines.append(f"警告：以下股票因在 {execution_date_str} 及前一日均无有效价格将跳过操作：{', '.join(skipped_due_no_price)}\n")
        if used_prev_day_price:
             message_lines.append(f"提示：以下股票将使用前一交易日 ({previous_trading_date}) 的价格：{', '.join(used_prev_day_price.keys())}\n")

        # ... (后续格式化卖出和买入信息时，可以检查 code 是否在 used_prev_day_price 中，并添加提示) ...
        # 例如：
        # if code in used_prev_day_price: value_str += f" (注: 使用 {previous_trading_date} 价格)"

        # ... (确认对话框和启动 Worker 的逻辑保持不变) ...
        # --- 5. 格式化确认信息 ---
        print(f"[{thread_id_main}] 格式化确认信息...")
        # 确保 latest_trading_date 有值
        if latest_trading_date is None:
            QMessageBox.critical(self, "内部错误", "未能确定最新交易日，无法继续。")
            return
        execution_date_str = latest_trading_date # 使用获取到的最新交易日作为执行日期

        message_lines = [f"将当前数据库同步至历史模拟最终状态。\n操作将在数据库中以日期【{execution_date_str}】记录，并使用该日的收盘价执行。\n"]
        if skipped_due_no_price:
             message_lines.append(f"警告：以下股票因在 {execution_date_str} 无有效价格将跳过操作：{', '.join(skipped_due_no_price)}\n")

        estimated_sell_proceeds = 0; sell_count = 0; executed_sell_actions = {}
        message_lines.append("【计划卖出】:")
        if actions_sell:
            sorted_sell_codes = sorted(actions_sell.keys())
            for code in sorted_sell_codes:
                 if code in skipped_due_no_price: continue
                 shares_to_sell = actions_sell[code]
                 name = name_map.get(code, "N/A"); price = latest_prices.get(code); value_str = ""
                 sell_type = "部分卖出"
                 if code in codes_to_sell_fully: sell_type = "完全卖出"

                 if price is not None:
                      value = shares_to_sell * price
                      estimated_sell_proceeds += value
                      value_str = f" (估价: {price:.2f}, 价值: {value:,.2f})"
                 else: value_str = " (错误: 价格缺失?)" # 不应发生

                 message_lines.append(f"  - {sell_type}: {code:<7s} ({name:<6}) {shares_to_sell:>8,} 股{value_str}")
                 sell_count += 1
                 executed_sell_actions[code] = shares_to_sell # 只记录实际要执行的
        if sell_count == 0: message_lines.append("  (无或均无法操作)")
        if estimated_sell_proceeds > 0: message_lines.append(f"  >> 预估卖出回收现金: {estimated_sell_proceeds:,.2f}")

        estimated_buy_cost = 0; buy_count = 0; executed_buy_actions = {}
        message_lines.append("\n【计划买入】:")
        if actions_buy:
            sorted_buy_codes = sorted(actions_buy.keys())
            for code in sorted_buy_codes:
                if code in skipped_due_no_price: continue
                buy_info = actions_buy[code]
                shares_to_buy = buy_info['shares']; rank = buy_info.get('rank', 'N/A'); name = name_map.get(code, "N/A"); price = latest_prices.get(code); cost_str = ""; buy_type = "加仓买入"
                if code in codes_to_buy_new: buy_type = "全新买入"

                if price is not None:
                     cost = shares_to_buy * price
                     estimated_buy_cost += cost
                     rank_str = str(rank) if rank is not None else 'N/A'
                     cost_str = f" (估价: {price:.2f}, 成本: {cost:,.2f}, 模拟排名: {rank_str})"
                else: cost_str = f" (错误: 价格缺失?, 模拟排名: {rank_str})" # 不应发生

                message_lines.append(f"  - {buy_type}: {code:<7s} ({name:<6}) {shares_to_buy:>8,} 股{cost_str}")
                buy_count += 1
                executed_buy_actions[code] = buy_info # 只记录实际要执行的
        if buy_count == 0: message_lines.append("  (无或均无法操作)")
        if estimated_buy_cost > 0: message_lines.append(f"  >> 预估买入所需现金: {estimated_buy_cost:,.2f}")

        # 检查是否有任何实际操作
        if not executed_sell_actions and not executed_buy_actions:
             message_lines.append("\n*** 无需执行任何数据库操作 (可能因无价格已跳过)。 ***")
             QMessageBox.information(self,"提示", "对比后没有需要执行的有效数据库操作。")
             print(f"[{thread_id_main}] 无有效操作，不启动 Worker。")
             self.status_label.setText("状态: 无需操作")
             return

        # 预估最终现金
        estimated_final_cash = current_db_cash + estimated_sell_proceeds - estimated_buy_cost
        message_lines.append(f"\n当前数据库现金: {current_db_cash:,.2f}")
        message_lines.append(f"预估操作后现金 ({execution_date_str}): {estimated_final_cash:,.2f}")
        if estimated_final_cash < 0: message_lines.append("\n\n警告：预估最终现金为负！可能无法完成所有买入！")
        message_lines.append(f"\n\n是否确认执行这些同步操作？(将以 {execution_date_str} 记录)")
        confirmation_message = "\n".join(message_lines)

        # --- 6. 使用自定义对话框进行确认 ---
        print(f"[{thread_id_main}] 显示确认对话框...")
        # 确保 ConfirmRebalanceDialog 类已定义
        try:
            dialog = ConfirmRebalanceDialog("确认数据库同步操作 (同步至最终状态)", confirmation_message, self)
            result = dialog.exec()
        except NameError:
             QMessageBox.critical(self, "代码错误", "类 'ConfirmRebalanceDialog' 未定义！")
             print(f"[{thread_id_main}] 错误: ConfirmRebalanceDialog 未定义。")
             self.status_label.setText("状态: 内部错误")
             return
        except Exception as e_dialog:
             QMessageBox.critical(self, "对话框错误", f"显示确认对话框时出错: {e_dialog}")
             print(f"[{thread_id_main}] 错误: 显示确认对话框失败 - {e_dialog}")
             self.status_label.setText("状态: 对话框错误")
             return

        # --- 7. 如果确认，启动后台线程执行 ---
        if result == QDialog.Accepted:
            print(f"[{thread_id_main}] 用户确认执行数据库同步 (同步至最终状态)。")

            # --- 准备 Worker 数据 (获取卖出成本) ---
            print(f"[{thread_id_main}] 准备 Worker 数据...")
            full_sell_details_for_db = {}
            codes_to_fetch_sell = list(executed_sell_actions.keys())
            if codes_to_fetch_sell:
                 # current_db_holdings 包含所需成本信息
                 for code in codes_to_fetch_sell:
                     if code in current_db_holdings:
                         full_sell_details_for_db[code] = {'cost_price': current_db_holdings[code].get('cost_price')}
                     else:
                         # 理论上不会发生，因为是基于 current_db_holdings 计算的卖出
                         print(f"警告：计划卖出的股票 {code} 在当前数据库持仓中未找到？卖出日志可能不含盈亏。")
                         full_sell_details_for_db[code] = {'cost_price': None}

            # --- 禁用按钮，更新状态 ---
            self.status_label.setText(f"状态: 正在执行数据库同步 (日期: {execution_date_str})...");
            self.log_display.setText("启动数据库同步线程...");
            self.rebalance_button.setEnabled(False);
            self.add_funds_button.setEnabled(False);
            self.refresh_display_button.setEnabled(False);

            # --- 创建并启动 Worker ---
            print(f"[{thread_id_main}] 创建并启动 ExecuteDbRebalanceWorker...")
            try:
                # 确保 ExecuteDbRebalanceWorker 类已更新以接收 execution_date
                self.db_rebalance_worker = ExecuteDbRebalanceWorker(
                    db_path=DB_PATH,
                    actions_sell=executed_sell_actions,       # 实际要执行的卖出
                    full_sell_details=full_sell_details_for_db, # 卖出成本
                    actions_buy=executed_buy_actions,        # 实际要执行的买入
                    latest_prices=latest_prices,             # 执行价格 (来自最新交易日)
                    target_percent_buffer=TARGET_PERCENT_BUFFER,
                    execution_date=execution_date_str        # 传递执行日期
                )
                self.worker_thread = QThread()
                self.db_rebalance_worker.moveToThread(self.worker_thread)

                # 连接信号槽
                self.db_rebalance_worker.finished.connect(self.on_db_rebalance_finished)
                self.db_rebalance_worker.log_message.connect(self.update_log_display)
                self.worker_thread.started.connect(self.db_rebalance_worker.run)
                # 清理连接
                self.db_rebalance_worker.finished.connect(self.worker_thread.quit)
                self.db_rebalance_worker.finished.connect(self.db_rebalance_worker.deleteLater)
                self.worker_thread.finished.connect(self.worker_thread.deleteLater)

                self.worker_thread.start()
                self.status_label.setText(f"状态: 数据库同步任务 (日期: {execution_date_str}) 已启动...")
                print(f"[{thread_id_main}] Worker 线程已启动。")

            except TypeError as e_worker_init:
                 # 捕获 Worker 初始化参数不匹配的错误
                 if 'execution_date' in str(e_worker_init):
                      msg = "启动后台线程失败：ExecuteDbRebalanceWorker 初始化缺少 execution_date 参数。\n请确保 Worker 类已更新。"
                 else:
                      msg = f"启动后台同步线程时初始化出错: {e_worker_init}"
                 QMessageBox.critical(self, "代码错误", msg)
                 print(f"[{thread_id_main}] 错误: 启动 Worker 失败 - {e_worker_init}")
                 traceback.print_exc()
                 # 恢复按钮状态
                 self.status_label.setText("状态: 启动后台任务失败")
                 self.rebalance_button.setEnabled(True);
                 self.add_funds_button.setEnabled(True);
                 self.refresh_display_button.setEnabled(True);

            except Exception as e_worker_start:
                 # 处理其他启动 Worker 时的异常
                 QMessageBox.critical(self, "线程错误", f"启动后台同步线程时出错: {e_worker_start}")
                 print(f"[{thread_id_main}] 错误: 启动 Worker 失败 - {e_worker_start}")
                 traceback.print_exc()
                 # 恢复按钮状态
                 self.status_label.setText("状态: 启动后台任务失败")
                 self.rebalance_button.setEnabled(True);
                 self.add_funds_button.setEnabled(True);
                 self.refresh_display_button.setEnabled(True);

        else: # 用户点击了 No 或关闭了对话框
            print(f"[{thread_id_main}] 用户取消数据库同步。")
            self.status_label.setText("状态: 用户取消同步")

    # --- !! 确保 on_db_rebalance_finished 方法存在且逻辑正确 !! ---
    # (这个方法在 7.27 和 7.28 中似乎是相同的，不需要修改)
    @Slot(bool, str)
    def on_db_rebalance_finished(self, success, status_message):
        """Slot to handle the completion of the database rebalance worker."""
        print("数据库调仓线程完成信号接收。")
        final_log_line = status_message.splitlines()[-1] if status_message else "完成 (未知)"
        status_prefix = "状态: DB调仓 "
        if success:
            status_prefix += f"成功完成 - {final_log_line}"
        else:
            status_prefix += f"执行中遇到问题 - {final_log_line}"

        self.status_label.setText(status_prefix)
        self.log_display.setText(final_log_line) # Show last line in short log display
        self._refresh_all_displays() # Refresh UI to show new DB state

        # Clean up worker/thread references
        worker_ref = getattr(self, 'db_rebalance_worker', None) # 获取引用以检查
        thread_ref = getattr(self, 'worker_thread', None)       # 获取引用以检查
        self.worker_thread = None
        self.db_rebalance_worker = None
        print(f"  - DB Rebalance Worker/Thread UI引用已清除 (Worker: {worker_ref is not None}, Thread: {thread_ref is not None})")

        # Re-enable buttons
        self.rebalance_button.setEnabled(True)
        self.add_funds_button.setEnabled(True)
        self.refresh_display_button.setEnabled(True)
        print("on_db_rebalance_finished 槽函数执行完毕。")
    # update_log_display 方法不变
    @Slot(str)
    def update_log_display(self, message): self.log_display.setText(message)

    # show_add_funds_dialog 方法 (保持 V1.7 不变 - 成功后调用刷新)
    @Slot()
    def show_add_funds_dialog(self):
        if not self.db_exists: QMessageBox.critical(self, "错误", "数据库文件未找到。"); return
        # 修改对话框标题和提示文本，允许负数输入 (设置一个很大的负数作为下限)
        amount, ok = QInputDialog.getDouble(self, "调整资金", "输入要调整的现金金额 (正数增加, 负数减少):", 0, -1000000000, 1000000000, 2)
        # 允许执行非零操作 (包括负数)
        if ok and amount != 0:
            # 根据正负调整状态文本
            action_text = "增加" if amount > 0 else "减少"
            self.status_label.setText(f"状态: 正在{action_text}资金 {abs(amount):,.2f}..."); QApplication.processEvents()
            # 调用 add_funds (该函数也需要修改)
            success, message = add_funds(DB_PATH, amount)
            self.status_label.setText(f"状态: {message}"); self.log_display.setText(message)
            if success:
                 self._refresh_all_displays();
                 QMessageBox.information(self, "成功", message) # 显示成功消息
            else:
                 QMessageBox.warning(self, "失败", message) # 显示失败消息
        elif ok and amount == 0:
            QMessageBox.information(self, "提示", "调整金额为 0，未做任何操作。")
    # closeEvent 方法不变
    def closeEvent(self, event):
        if self.worker_thread and self.worker_thread.isRunning():
            reply = QMessageBox.question(self, '确认退出', "调仓任务仍在运行，确定要退出吗？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.No: event.ignore(); return
            else: print("尝试请求工作线程退出..."); self.worker_thread.quit(); self.worker_thread.wait(500)
        print("应用程序准备退出..."); event.accept()

# --- !!! 请用这个【使用 exp_trade 获取日期】的完整版本替换 !!! ---
    @Slot()
    def _hist_start_sim(self):
        # 1. 重置状态和标志位
        self.hist_simulation_finished = False
        self.final_sim_positions = None
        self.final_sim_cash = None
        self.final_sim_prices = None
        self.final_rank_dict_stored = None
        self.final_decision_pool_df_stored = None
        self.final_pending_orders = None # <-- 新增：重置最终待执行订单列表
        # self.final_predicted_sells = None # 不再需要
        # self.final_predicted_buys = None  # 不再需要
        self.hist_sim_idx = 0
        self.hist_sim_state = {} # 清空旧状态
        self.hist_log_display.clear() # 清空日志显示
        self.hist_positions_table.setRowCount(0) # 清空表格

        # 2. 读取并验证日期范围
        start_date = self.hist_start_date.date().toString("yyyy-MM-dd")
        end_date = self.hist_end_date.date().toString("yyyy-MM-dd")
        conn = None # 初始化 conn 为 None

        # 3. 加载股票名称映射 (作为实例属性)
        self.name_map = {} # Initialize as instance variable
        print("加载股票名称映射...")
        conn_temp_name = None
        try:
            conn_temp_name = connect_db(DB_PATH)
            if conn_temp_name:
                try:
                    name_map_df = pd.read_sql("SELECT stock_code, stock_name FROM stock_fundamentals", conn_temp_name)
                    if not name_map_df.empty:
                        self.name_map = name_map_df.set_index('stock_code')['stock_name'].to_dict()
                        print(f"成功加载 {len(self.name_map)} 个股票名称。")
                    else:
                        print("警告: 从 stock_fundamentals 未加载到名称。")
                except Exception as e_name:
                    print(f"加载股票名称时出错: {e_name}")
            else:
                 print("无法连接数据库加载股票名称。")
        except Exception as e_conn:
             print(f"连接数据库加载名称时出错: {e_conn}")
        finally:
            if conn_temp_name:
                 try: conn_temp_name.close()
                 except Exception as e_close: print(f"关闭名称加载DB连接时出错: {e_close}")

        # 4. 加载交易日历和上市日期
        try:
            print(f"开始历史模拟，日期范围: {start_date} 至 {end_date}")
            conn = connect_db(DB_PATH) # 尝试连接数据库
            if not conn:
                raise ConnectionError("无法连接数据库")

            # --- 从 exp_trade 加载交易日历日期列表 ---
            print(f"从 exp_trade 表加载交易日历 ({start_date} to {end_date})...")
            calendar_query = "SELECT date FROM exp_trade WHERE is_open = 1 AND date >= ? AND date <= ? ORDER BY date"
            dates_df = pd.read_sql(calendar_query, conn, params=(start_date, end_date))

            if dates_df.empty:
                QMessageBox.critical(self, "日历错误", f"在 exp_trade 表中未找到日期范围内的任何交易日 (is_open=1)。请检查交易日历数据。")
                if conn: conn.close(); return

            self.hist_sim_dates = pd.to_datetime(dates_df['date']).tolist()
            print(f"从交易日历加载了 {len(self.hist_sim_dates)} 个交易日")
            # --- 日期加载修改结束 ---

            if len(self.hist_sim_dates) <= 1:
                QMessageBox.warning(self, "数据错误", f"从 exp_trade 表找到的有效交易日不足 ({len(self.hist_sim_dates)})，至少需要2个才能进行模拟。")
                if conn: conn.close(); return

            # --- 加载股票上市日期 ---
            print("正在加载所有股票的上市日期...")
            self.stock_list_dates = {}
            basic_query = "SELECT ts_code, list_date FROM stock_basic"
            try:
                df_basic = pd.read_sql(basic_query, conn)
                if not df_basic.empty:
                    df_basic['stock_code'] = df_basic['ts_code'].astype(str).str[:6]
                    df_basic['list_date_dt'] = pd.to_datetime(df_basic['list_date'], format='%Y%m%d', errors='coerce')
                    df_basic = df_basic.dropna(subset=['stock_code', 'list_date_dt'])
                    df_basic = df_basic.drop_duplicates(subset=['stock_code'], keep='first')
                    self.stock_list_dates = dict(zip(df_basic['stock_code'], df_basic['list_date_dt']))
                    print(f"成功加载 {len(self.stock_list_dates)} 只股票的有效上市日期。")
                else: print("警告: 从 stock_basic 读取数据为空。")
            except Exception as e_basic_load:
                print(f"!!! 错误: 加载上市日期失败: {e_basic_load}")
                QMessageBox.warning(self, "数据错误", f"加载股票上市日期失败: {e_basic_load}\n历史模拟可能无法正确排除新股。")
                self.stock_list_dates = {}
            # --- 加载上市日期结束 ---

        except Exception as e:
             print(f"历史模拟初始化 - 加载日期或连接数据库失败: {e}"); traceback.print_exc()
             QMessageBox.critical(self, "错误", f"历史模拟初始化失败: {e}")
             return
        finally:
             print("进入 _hist_start_sim finally 块，检查数据库连接...")
             if conn:
                 try:
                     print("  尝试关闭数据库连接...")
                     conn.close(); print("  数据库连接已关闭。")
                 except Exception as e_close: print(f"  关闭DB连接时出错(finally块): {e_close}")
             else: print("  数据库连接未打开或已关闭，无需操作。")

        # 5. 初始化模拟状态字典 (hist_sim_state)
        try: init_cash = float(self.hist_init_cash.text())
        except ValueError: init_cash = 1000000; self.hist_init_cash.setText("1000000")
        if init_cash <= 0: init_cash = 1000000; self.hist_init_cash.setText("1000000")

        self.hist_sim_idx = 0
        self.hist_sim_state = {
            'cash': init_cash,
            'positions': {},          # 当前持仓 {code: {shares, cost, buy_date, buy_rank, lowest_rank}}
            'pending_orders': [],     # T日决策，T+1执行
            'logs': [],               # 每日日志 [{date, cash, market_value, total_assets, log, positions_snapshot}]
            'trade_results': [],      # 每笔已完成交易盈亏 PnL [float, ...]
            'allow_buy': True,        # 市场择时状态
            'trade_records': [],      # 详细交易记录 (用于Excel导出) [{dict}, ...]
            'last_day_closes_map': {},# 最后处理日的收盘价 {code: price} - 会在循环中更新
            # --- 用于存储最后一日的决策依据 ---
            'final_rank_dict_unfiltered': {}, # 最后处理日的【未筛选】排名 {code: rank} - 会在循环中更新
            'final_decision_pool_df': pd.DataFrame() # 最后处理日的【筛选后】选股结果 DataFrame - 会在循环中更新
            # 'final_valid_codes_set' 不再需要在 state 中存储
        }
        self.hist_initial_cash = init_cash # 记录初始资金用于计算指标

        # 6. 更新 UI 状态
        self.hist_next_btn.setEnabled(True); self.hist_auto_btn.setEnabled(True)
        if self.hist_sim_dates:
            self.hist_status_label.setText(f"历史模拟已加载，准备开始于: {self.hist_sim_dates[0].strftime('%Y-%m-%d')}")
        else:
             self.hist_status_label.setText("历史模拟加载失败：无有效交易日")
        self._hist_refresh_display() # 显示初始状态（通常是空的）
        print("历史模拟初始化完成")
    # 在 SimulatorAppUI 类中添加新方法
    def _display_final_sim_portfolio(self):
        """仅显示历史模拟结束时的最终持仓状态和估值。"""
        logs = ["--- ========== 历史模拟最终持仓状态 ========== ---"]
        if not self.hist_simulation_finished or self.final_sim_positions is None or self.final_sim_prices is None:
            logs.append("错误: 未找到有效的最终模拟状态。")
            self._show_prediction_dialog("\n".join(logs)); return

        target_portfolio = self.final_sim_positions # 直接使用最终模拟持仓
        latest_price_map = self.final_sim_prices    # 使用对应的价格
        final_cash = self.final_sim_cash if self.final_sim_cash is not None else 0
        name_map = getattr(self, 'name_map', {})

        total_estimated_target_cost = 0
        if not target_portfolio:
            logs.append("最终模拟持仓为空。")
        else:
            logs.append(f"最终模拟持仓 {len(target_portfolio)} 只:")
            sorted_target_codes = sorted(target_portfolio.keys())
            for code in sorted_target_codes:
                # 从模拟状态获取信息，注意键名可能为 'shares', 'cost' 等
                shares = target_portfolio[code].get('shares', 0)
                name = name_map.get(code, 'N/A')
                price = latest_price_map.get(code) # 使用最后一日价格估值
                cost_item = 0; price_str = "N/A"
                if price is not None and pd.notna(price) and shares > 0:
                    try:
                        price = float(price); cost_item = shares * price; total_estimated_target_cost += cost_item; price_str = f"{price:.2f}"
                    except Exception: price_str = f"无效价格({price})"
                logs.append(f"  - {code} ({name}): {shares:,} 股 (估价: {price_str})")
            logs.append(f"\n模拟结束时持仓总市值估算: {total_estimated_target_cost:,.2f}")

        logs.append("--- =========================================== ---")
        logs.append(f"\n模拟结束时现金: {final_cash:,.2f}")
        logs.append(f"模拟结束时总资产估算: {final_cash + total_estimated_target_cost:,.2f}")
        logs.append("\n这是历史模拟结束时的状态，供您参考。\n“执行模拟调仓”按钮将尝试使数据库与此状态同步。")

        self._show_prediction_dialog("\n".join(logs))
            # --- 方法定义结束 ---

# --- !!! 新增：独立的性能指标计算函数 (已集成前五大回撤计算) !!! ---
# 用这个完整版本替换你现有的 calculate_performance_metrics 方法
    def calculate_performance_metrics(self, logs, trade_results, initial_cash, sim_dates, trading_days_per_year=252, risk_free_rate=0.0):
        """
        根据模拟日志和交易结果计算性能指标 (作为类方法)。
        新增：计算前五大回撤。

        Args:
            self: 类实例。
            logs (list): 包含每日状态的日志列表。
            trade_results (list): 包含每笔已完成交易盈亏 (PnL) 的列表。
            initial_cash (float): 初始投入资金。
            sim_dates (list): 包含模拟期间所有交易日 datetime 对象的列表。
            trading_days_per_year (int): 一年的交易日数。
            risk_free_rate (float): 无风险利率 (年化)。

        Returns:
            dict: 包含计算出的性能指标的字典。
        """
        metrics = {
            'final_assets': 0, 'total_return_pct': 0, 'annualized_return_pct': 0,
            'total_trades': 0, 'win_rate_pct': 0, 'profit_loss_ratio': 'N/A',
            'sharpe_ratio': 'N/A',
            # --- V V V 新增回撤指标初始化 V V V ---
            'max_drawdown_pct': 0,
            'mdd_period': "N/A",
            'second_max_drawdown_pct': 0,
            'second_mdd_period': "N/A",
            'third_max_drawdown_pct': 0,
            'third_mdd_period': "N/A",
            'fourth_max_drawdown_pct': 0,
            'fourth_mdd_period': "N/A",
            'fifth_max_drawdown_pct': 0,
            'fifth_mdd_period': "N/A"
            # --- ^ ^ ^ 新增回撤指标初始化 ^ ^ ^ ---
        }

        if not logs: return metrics
        metrics['final_assets'] = logs[-1].get('total_assets', 0)
        if initial_cash > 0: total_return = (metrics['final_assets'] / initial_cash) - 1; metrics['total_return_pct'] = total_return * 100
        else: total_return = 0
        if initial_cash > 0 and len(sim_dates) > 1:
            start_date = sim_dates[0]; end_date_str = logs[-1].get('date')
            try: end_date = pd.to_datetime(end_date_str) if end_date_str else sim_dates[-1]
            except Exception: end_date = sim_dates[-1]
            total_days = (end_date - start_date).days + 1; years = total_days / 365.25
            if years > 0: annualized_return = ((1 + total_return) ** (1 / years)) - 1; metrics['annualized_return_pct'] = annualized_return * 100
            else: metrics['annualized_return_pct'] = metrics['total_return_pct']
        metrics['total_trades'] = len(trade_results)
        if metrics['total_trades'] > 0:
            winning_trades = [pnl for pnl in trade_results if pnl > 0]; losing_trades = [pnl for pnl in trade_results if pnl < 0]
            num_winning = len(winning_trades); num_losing = len(losing_trades)
            metrics['win_rate_pct'] = (num_winning / metrics['total_trades']) * 100 if metrics['total_trades'] > 0 else 0
            avg_profit = sum(winning_trades) / num_winning if num_winning > 0 else 0
            avg_loss = abs(sum(losing_trades) / num_losing) if num_losing > 0 else 0
            if avg_loss > 0: metrics['profit_loss_ratio'] = f"{avg_profit / avg_loss:.2f}"
            else: metrics['profit_loss_ratio'] = "Inf" if avg_profit > 0 else "N/A"
        else: metrics['win_rate_pct'] = 0; metrics['profit_loss_ratio'] = 'N/A'
        if len(logs) > 1:
            assets_series = pd.Series([log.get('total_assets', np.nan) for log in logs], index=pd.to_datetime([log.get('date') for log in logs]))
            assets_series.dropna(inplace=True)
            daily_returns = assets_series.pct_change().dropna(); daily_returns = daily_returns[~np.isinf(daily_returns)]
            if len(daily_returns) > 1:
                 mean_daily_return = daily_returns.mean(); std_dev_daily_return = daily_returns.std()
                 if std_dev_daily_return > 0:
                     daily_risk_free_rate = (1 + risk_free_rate)**(1/trading_days_per_year) - 1
                     daily_sharpe = (mean_daily_return - daily_risk_free_rate) / std_dev_daily_return
                     metrics['sharpe_ratio'] = f"{daily_sharpe * np.sqrt(trading_days_per_year):.2f}"
                 else: metrics['sharpe_ratio'] = "N/A (StdDev=0)"
            else: metrics['sharpe_ratio'] = "N/A (No enough returns)"

            # --- V V V 新增：计算所有回撤周期的逻辑 V V V ---
            if not assets_series.empty:
                all_drawdown_periods = []
                peak_value = assets_series.iloc[0]
                peak_date = assets_series.index[0]
                trough_value = assets_series.iloc[0]
                trough_date = assets_series.index[0]
                in_drawdown_period = False

                for date, value in assets_series.items():
                    if value > peak_value:
                        if in_drawdown_period:
                            drawdown_pct = (peak_value - trough_value) / peak_value
                            if drawdown_pct > 0:
                                all_drawdown_periods.append({
                                    'drawdown': drawdown_pct,
                                    'start_date': peak_date,
                                    'end_date': trough_date
                                })
                            in_drawdown_period = False
                        
                        peak_value = value
                        peak_date = date
                        trough_value = value
                        trough_date = date
                    else:
                        in_drawdown_period = True
                        if value < trough_value:
                            trough_value = value
                            trough_date = date
                
                if in_drawdown_period:
                    drawdown_pct = (peak_value - trough_value) / peak_value
                    if drawdown_pct > 0:
                        all_drawdown_periods.append({
                            'drawdown': drawdown_pct,
                            'start_date': peak_date,
                            'end_date': trough_date
                        })

                if all_drawdown_periods:
                    all_drawdown_periods.sort(key=lambda x: x['drawdown'], reverse=True)

                    # 提取前五大回撤信息
                    if len(all_drawdown_periods) > 0:
                        mdd = all_drawdown_periods[0]
                        mdd_days = (mdd['end_date'] - mdd['start_date']).days
                        metrics['max_drawdown_pct'] = mdd['drawdown'] * 100
                        metrics['mdd_period'] = f"{mdd['start_date'].strftime('%Y-%m-%d')} 至 {mdd['end_date'].strftime('%Y-%m-%d')} ({mdd_days}天)"

                    if len(all_drawdown_periods) > 1:
                        smdd = all_drawdown_periods[1]
                        smdd_days = (smdd['end_date'] - smdd['start_date']).days
                        metrics['second_max_drawdown_pct'] = smdd['drawdown'] * 100
                        metrics['second_mdd_period'] = f"{smdd['start_date'].strftime('%Y-%m-%d')} 至 {smdd['end_date'].strftime('%Y-%m-%d')} ({smdd_days}天)"
                    
                    if len(all_drawdown_periods) > 2:
                        tmdd = all_drawdown_periods[2]
                        tmdd_days = (tmdd['end_date'] - tmdd['start_date']).days
                        metrics['third_max_drawdown_pct'] = tmdd['drawdown'] * 100
                        metrics['third_mdd_period'] = f"{tmdd['start_date'].strftime('%Y-%m-%d')} 至 {tmdd['end_date'].strftime('%Y-%m-%d')} ({tmdd_days}天)"

                    if len(all_drawdown_periods) > 3:
                        fomdd = all_drawdown_periods[3]
                        fomdd_days = (fomdd['end_date'] - fomdd['start_date']).days
                        metrics['fourth_max_drawdown_pct'] = fomdd['drawdown'] * 100
                        metrics['fourth_mdd_period'] = f"{fomdd['start_date'].strftime('%Y-%m-%d')} 至 {fomdd['end_date'].strftime('%Y-%m-%d')} ({fomdd_days}天)"

                    if len(all_drawdown_periods) > 4:
                        fimdd = all_drawdown_periods[4]
                        fimdd_days = (fimdd['end_date'] - fimdd['start_date']).days
                        metrics['fifth_max_drawdown_pct'] = fimdd['drawdown'] * 100
                        metrics['fifth_mdd_period'] = f"{fimdd['start_date'].strftime('%Y-%m-%d')} 至 {fimdd['end_date'].strftime('%Y-%m-%d')} ({fimdd_days}天)"
            # --- ^ ^ ^ 新增回撤计算逻辑结束 ^ ^ ^ ---

        return metrics


# --- !!! 修改：添加更多回撤信息的显示 !!! ---
# 用这个完整版本替换你现有的 _calculate_and_display_metrics 方法
    def _calculate_and_display_metrics(self):
        """在模拟结束后计算并显示性能指标。"""
        if not hasattr(self, 'hist_sim_state') or not self.hist_sim_state.get('logs'):
            print("无法计算指标：缺少模拟日志。")
            self.hist_log_display.append("\n--- 无法计算性能指标：缺少模拟日志 ---")
            return

        logs = self.hist_sim_state.get('logs', [])
        trade_results = self.hist_sim_state.get('trade_results', [])
        initial_cash = getattr(self, 'hist_initial_cash', 1000000)
        sim_dates = getattr(self, 'hist_sim_dates', [])

        print("-" * 20)
        print(f"开始计算指标...")
        print(f"  - 日志条数: {len(logs)}")
        print(f"  - 传入 trade_results 列表长度: {len(trade_results)}")
        print(f"  - 初始资金: {initial_cash}")
        print(f"  - 模拟日期数: {len(sim_dates)}")
        print("-" * 20)


        if not logs or not sim_dates:
             print("无法计算指标：日志或日期列表为空。")
             self.hist_log_display.append("\n--- 无法计算性能指标：日志或日期列表为空 ---")
             return

        # 调用包含回撤计算的函数
        metrics = self.calculate_performance_metrics(logs, trade_results, initial_cash, sim_dates)

        # 格式化并显示结果
        results_text = ["\n--- 历史模拟性能指标 ---"]
        results_text.append(f"模拟周期: {sim_dates[0].strftime('%Y-%m-%d')} 至 {logs[-1].get('date', sim_dates[-1].strftime('%Y-%m-%d'))}")
        results_text.append(f"初始资产: {initial_cash:,.2f}")
        results_text.append(f"最终资产: {metrics.get('final_assets', 0):,.2f}")
        results_text.append(f"总收益率: {metrics.get('total_return_pct', 0):.2f}%")
        results_text.append(f"年化收益率: {metrics.get('annualized_return_pct', 0):.2f}%")
        results_text.append(f"交易次数: {metrics.get('total_trades', 0)}")
        results_text.append(f"胜率: {metrics.get('win_rate_pct', 0):.2f}%")
        results_text.append(f"盈亏比: {metrics.get('profit_loss_ratio', 'N/A')}")
        results_text.append(f"夏普比率: {metrics.get('sharpe_ratio', 'N/A')}")
        # --- V V V 新增：添加回撤显示 V V V ---
        results_text.append(f"最大回撤: {metrics.get('max_drawdown_pct', 0):.2f}%, 时间: {metrics.get('mdd_period', 'N/A')}")
        results_text.append(f"第二回撤: {metrics.get('second_max_drawdown_pct', 0):.2f}%, 时间: {metrics.get('second_mdd_period', 'N/A')}")
        results_text.append(f"第三回撤: {metrics.get('third_max_drawdown_pct', 0):.2f}%, 时间: {metrics.get('third_mdd_period', 'N/A')}")
        results_text.append(f"第四回撤: {metrics.get('fourth_max_drawdown_pct', 0):.2f}%, 时间: {metrics.get('fourth_mdd_period', 'N/A')}")
        results_text.append(f"第五回撤: {metrics.get('fifth_max_drawdown_pct', 0):.2f}%, 时间: {metrics.get('fifth_mdd_period', 'N/A')}")
        # --- ^ ^ ^ 新增回撤显示结束 ^ ^ ^ ---
        results_text.append("--------------------------")

        self.hist_log_display.append("\n".join(results_text))
        if hasattr(self.hist_log_display, 'verticalScrollBar'):
             QTimer.singleShot(0, lambda: self.hist_log_display.verticalScrollBar().setValue(self.hist_log_display.verticalScrollBar().maximum()))
        print("性能指标计算和显示完成。")

# 用这个完整版本替换你现有的 _hist_next_day 方法

# 请用这个【完整的新版本】替换掉您代码中现有的 _hist_next_day 方法

# 请用这个【修正后的完整版本】替换掉您代码中现有的 _hist_next_day 方法

    @Slot()
    def _hist_next_day(self):
        """
        (V-Final - 集成5日线策略 - 修正KeyError)
        执行一天的历史模拟逻辑。
        新策略:
        1. 卖出: 增加“价格跌破5日线”作为卖出条件。
        2. 买入: 排名后，依次检查，只有“价格站上5日线”的才买入，直到买满。
        """
        # --- 0. Initializations ---
        # (此部分无改动)
        valid_df = pd.DataFrame()
        unfiltered_rank_dict = {}
        unfiltered_valid_codes_set = set()
        today_data_indexed = pd.DataFrame()
        micro_cap_today_data = None 
        micro_cap_prev_data = None  

        # --- 1. Check Simulation Boundaries ---
        # (此部分无改动)
        if not hasattr(self, 'hist_sim_dates') or not self.hist_sim_dates or self.hist_sim_idx >= len(self.hist_sim_dates):
            self.hist_status_label.setText("历史模拟已结束")
            self.hist_next_btn.setEnabled(False); self.hist_auto_btn.setEnabled(False)
            if hasattr(self, 'hist_sim_timer') and self.hist_sim_timer.isActive(): self.hist_sim_timer.stop(); self.hist_auto_btn.setText("自动演变"); self.hist_sim_running = False
            if hasattr(self, 'hist_sim_idx') and hasattr(self, 'hist_sim_dates') and self.hist_sim_idx == len(self.hist_sim_dates) and len(self.hist_sim_dates) > 0:
                self.hist_simulation_finished = True
                print("历史模拟完成标志位已设置。")
                try:
                    print("模拟结束，开始存储最终状态...")
                    final_state = self.hist_sim_state
                    self.final_sim_positions = final_state.get('positions', {}).copy()
                    self.final_sim_cash = final_state.get('cash')
                    self.final_sim_prices = final_state.get('last_day_closes_map', {}).copy()
                    self.final_rank_dict_stored = final_state.get('final_rank_dict_unfiltered', {}).copy()
                    self.final_decision_pool_df_stored = final_state.get('final_decision_pool_df', pd.DataFrame()).copy()
                    self.final_pending_orders = final_state.get('pending_orders', []).copy()
                    cash_str = f"{self.final_sim_cash:,.2f}" if self.final_sim_cash is not None else "N/A"
                    print(f"最终模拟状态已存储：持仓 {len(self.final_sim_positions)} 只, 现金 {cash_str}, 最终挂单 {len(self.final_pending_orders)} 条")
                    if self.final_sim_positions is None or self.final_sim_cash is None or self.final_sim_prices is None or self.final_pending_orders is None:
                        print("警告：最终模拟状态部分数据未能成功存储到 self！")
                        self.hist_simulation_finished = False
                except Exception as e_state_store:
                    print(f"!!! 存储最终模拟状态时出错: {e_state_store}"); traceback.print_exc()
                    self.hist_simulation_finished = False; self.final_sim_positions = None; self.final_sim_cash = None; self.final_sim_prices = None;
                    self.final_rank_dict_stored = None; self.final_decision_pool_df_stored = None; self.final_pending_orders = None;
                    QMessageBox.critical(self, "内部错误", f"存储最终模拟状态失败:\n{e_state_store}"); return
                if self.hist_simulation_finished:
                    try:
                        print("模拟结束，开始最终处理步骤...");print("  - 刷新最后一天显示...");self._hist_refresh_display();print("  - 计算性能指标...");self._calculate_and_display_metrics();print("  - 生成对比图...");self._plot_comparison_chart()
                        self._display_final_pending_orders() 
                        QMessageBox.information(self, "历史模拟完成", "模拟结束。\n性能指标已附加，对比图已弹出，最终交易预测已显示。")
                    except Exception as e_final:
                        error_msg = f"最终处理步骤（刷新/指标/绘图/预测）出错: {e_final}";print(error_msg); traceback.print_exc();self.hist_log_display.append(f"\n--- {error_msg} ---");QMessageBox.critical(self, "模拟结束处理错误", error_msg)
            else: print("模拟提前中止或状态异常，不执行结束任务。")
            return

        # --- 2. Setup Dates and State ---
        # (此部分无改动)
        cur_date = self.hist_sim_dates[self.hist_sim_idx]
        today_str = cur_date.strftime('%Y-%m-%d')
        has_next_day = self.hist_sim_idx < len(self.hist_sim_dates) - 1
        next_day_str = self.hist_sim_dates[self.hist_sim_idx + 1].strftime('%Y-%m-%d') if has_next_day else None
        next_date = self.hist_sim_dates[self.hist_sim_idx + 1] if has_next_day else None
        self.hist_status_label.setText(f"正在处理决策日: {today_str}...")
        QApplication.processEvents()
        lot_size = LOT_SIZE
        num_stocks = self.num_stocks_spinbox.value()
        hold_days = self.hold_days_spinbox.value()
        rank_boost = self.rank_boost_spinbox.value()
        target_percent_buffer = TARGET_PERCENT_BUFFER
        day_log = [f"=== {today_str} (决策日) ==="]
        state = self.hist_sim_state
        cash = state['cash']
        positions = state['positions']
        pending_orders = state['pending_orders']
        if 'allow_buy' not in state:
            state['allow_buy'] = True

        # --- 3. Load Data ---
        # (此部分无改动)
        conn = None
        prices_df_today = pd.DataFrame()
        today_closes_map = {}
        today_data_indexed = pd.DataFrame()
        next_open_df = pd.DataFrame()
        micro_cap_today_data = None
        start_date_for_ma = (cur_date - timedelta(days=10)).strftime('%Y-%m-%d')
        historical_prices_for_ma = pd.DataFrame()

        try:
            conn = connect_db(DB_PATH) 
            if not conn: raise ConnectionError(f"无法连接数据库(步骤3, 日期: {today_str})")
            prices_df_today = pd.read_sql("SELECT * FROM stock_prices WHERE date = ?", conn, params=(today_str,))
            if not prices_df_today.empty:
                prices_df_today['date'] = pd.to_datetime(prices_df_today['date'])
                today_closes_map = prices_df_today.set_index('stock_code')['close'].to_dict()
                today_data_indexed = prices_df_today.set_index('stock_code')
            historical_prices_for_ma = pd.read_sql(
                "SELECT date, stock_code, close FROM stock_prices WHERE date >= ? AND date <= ?",
                conn, params=(start_date_for_ma, today_str)
            )
            day_log.append(f"  - 为计算5日线，加载了从 {start_date_for_ma} 到 {today_str} 的 {len(historical_prices_for_ma)} 条价格数据。")
            if has_next_day: 
                next_open_df = pd.read_sql("SELECT stock_code, open FROM stock_prices WHERE date = ?", conn, params=(next_day_str,)).set_index('stock_code')
            mc_query = "SELECT date, index_value, ma5, ma20 FROM micro_cap_index WHERE date = ?"
            mc_today_df = pd.read_sql(mc_query, conn, params=(today_str,))
            if not mc_today_df.empty:
                micro_cap_today_data = mc_today_df.iloc[0].to_dict()
        except Exception as db_err: 
            print(f"数据库或数据加载严重错误 ({today_str}): {db_err}"); traceback.print_exc(); 
            QMessageBox.critical(self, "数据库错误", f"数据加载失败 (步骤3): {db_err}\n模拟可能无法继续。");
            if conn: conn.close(); return
        finally:
            if conn:
                try: conn.close()
                except Exception as e_close: print(f"关闭DB连接时出错(步骤3 finally): {e_close}")

        # --- 4. 计算当天选股池 (valid_df) & 5日均线 ---
        day_log.append("--- 计算当天选股池与5日均线 ---"); valid_df = pd.DataFrame(); unfiltered_rank_dict = {}; unfiltered_valid_codes_set = set()
        ma5_map = {} 
        
        try:
            if prices_df_today.empty: print(f"[{today_str}] 警告: prices_df_today 为空，无法计算选股池。"); valid_df = pd.DataFrame()
            else:
                print(f"[{today_str}] 开始计算和过滤 valid_df (V4-Map)..."); base_df = prices_df_today[(prices_df_today['close'] > 2) & (~prices_df_today['stock_code'].str.startswith(('688', '8', '9','30')))].copy(); print(f"  - 初始过滤后: {len(base_df)} 条")
                if hasattr(self, 'name_map') and self.name_map and not base_df.empty:
                    if 'stock_code' not in base_df.columns: base_df.reset_index(inplace=True)
                    base_df['name'] = base_df['stock_code'].map(self.name_map); base_df = base_df[~base_df['name'].fillna('').str.contains('ST|\\*ST|退|PT', regex=True)]; print(f"  - 名称过滤后: {len(base_df)} 条")
                valid_df_market = pd.DataFrame(columns=base_df.columns.tolist() + ['market_cap'])
                if not base_df.empty:
                    actual_mv_col_name = 'total_mv'
                    if actual_mv_col_name in prices_df_today.columns: total_mv_map = prices_df_today.set_index('stock_code')[actual_mv_col_name].to_dict();
                    else: base_df['market_cap_raw'] = np.nan; print(f"  - 警告: prices_df_today 中缺少列 '{actual_mv_col_name}'")
                    if 'stock_code' not in base_df.columns: base_df.reset_index(inplace=True) 
                    base_df['market_cap_raw'] = base_df['stock_code'].map(total_mv_map if 'total_mv_map' in locals() else {}) 
                    base_df['market_cap'] = pd.to_numeric(base_df['market_cap_raw'], errors='coerce').fillna(0) * 10000;
                    valid_df_market = base_df[base_df['market_cap'] > 0].copy(); 
                    valid_df_market = valid_df_market.drop(columns=['market_cap_raw'], errors='ignore'); print(f"  - 市值过滤后 (要求 > 0): {len(valid_df_market)} 条")
                else: valid_df_market = base_df.copy()
                valid_df_temp = pd.DataFrame(columns=[col for col in valid_df_market.columns if col not in ['list_date_dt']])
                if hasattr(self, 'stock_list_dates') and self.stock_list_dates and not valid_df_market.empty:
                    required_days_listed = 365
                    if 'stock_code' not in valid_df_market.columns: valid_df_market.reset_index(inplace=True)
                    valid_df_market['list_date_dt'] = valid_df_market['stock_code'].map(self.stock_list_dates); valid_df_with_date = valid_df_market.dropna(subset=['list_date_dt']).copy()
                    if not valid_df_with_date.empty:
                        valid_df_with_date['days_listed'] = (cur_date - valid_df_with_date['list_date_dt']).dt.days; final_columns = [col for col in valid_df_market.columns if col not in ['list_date_dt','days_listed']]; valid_df_temp = valid_df_with_date[valid_df_with_date['days_listed'] >= required_days_listed][final_columns].copy(); print(f"  - 上市日期过滤后: {len(valid_df_temp)} 条")
                    else: print(f"  - 无股票满足上市日期条件（或无法匹配上市日期）。")
                elif valid_df_market.empty: print(f"  - 市值过滤后为空，跳过上市日期过滤。")
                else: print(f"  - 跳过上市日期过滤 (无日期数据)。"); valid_df_temp = valid_df_market.copy()
                valid_df = valid_df_temp; print(f"  - 【检查】准备排序前 valid_df 数量: {len(valid_df)}")
                if not valid_df.empty and STOCK_BLACKLIST:
                    original_count_before_blacklist = len(valid_df); valid_df = valid_df[~valid_df['stock_code'].isin(STOCK_BLACKLIST)]; removed_count = original_count_before_blacklist - len(valid_df)
                    if removed_count > 0: day_log.append(f"  - 应用黑名单过滤: 移除 {removed_count} 只股票。剩余 {len(valid_df)} 只。"); print(f"  - 应用黑名单后: 剩余 {len(valid_df)} 条 (移除了 {removed_count} 条在黑名单中的股票)")
                    else: print(f"  - 无股票在黑名单中，数量不变: {len(valid_df)}")
                elif not STOCK_BLACKLIST: print(f"  - 黑名单为空，跳过黑名单筛选。")

            if not valid_df.empty:
                if 'market_cap' in valid_df.columns:
                    valid_df = valid_df.sort_values(by='market_cap', ascending=True).reset_index(drop=True)
                    valid_df['rank'] = valid_df.index + 1
                    unfiltered_rank_dict = dict(zip(valid_df['stock_code'], valid_df['rank']))
                    unfiltered_valid_codes_set = set(unfiltered_rank_dict.keys())
                    print(f"  - 按市值排序完成，保存了未筛选排名信息 ({len(unfiltered_rank_dict)} 条) 和代码集合 ({len(unfiltered_valid_codes_set)} 条)")
                    
                    codes_for_ma_calc = set(positions.keys()) | unfiltered_valid_codes_set
                    
                    if not historical_prices_for_ma.empty and codes_for_ma_calc:
                        ma5_series = historical_prices_for_ma[historical_prices_for_ma['stock_code'].isin(codes_for_ma_calc)] \
                            .groupby('stock_code')['close'] \
                            .rolling(window=5, min_periods=5) \
                            .mean() \
                            .reset_index()

                        latest_ma5 = ma5_series.groupby('stock_code').last()
                        
                        # <--- 修正点：修复KeyError ---
                        if not latest_ma5.empty:
                            # stock_code 在 groupby().last() 后是索引，可以直接取 'close' 列
                            ma5_map = latest_ma5['close'].to_dict()
                        # --- 修正结束 ---
                        day_log.append(f"  - 成功计算了 {len(ma5_map)} 只股票的5日均线。")
                    else:
                        day_log.append(f"  - 警告：无历史数据或无股票需要计算5日线。")
                else:
                    print(f"[{today_str}] 严重错误: 排序前 'market_cap' 列丢失！")
                    valid_df = pd.DataFrame()
            
            state['final_rank_dict_unfiltered'] = unfiltered_rank_dict 
            state['final_decision_pool_df'] = valid_df
            state['last_day_closes_map'] = today_closes_map if 'today_closes_map' in locals() else {}
            print(f"[{today_str}] 已将未筛选排名字典 ({len(unfiltered_rank_dict)}) 和【完整的】决策池 ({len(valid_df)}) 等信息存入 state。")

        except Exception as e_valid_df:
            print(f"[{today_str}] 计算 valid_df 或5日线时出错: {e_valid_df}"); traceback.print_exc()
            valid_df = pd.DataFrame(); unfiltered_rank_dict = {}; unfiltered_valid_codes_set = set(); ma5_map = {}
            state['final_rank_dict_unfiltered'] = {}; state['final_decision_pool_df'] = pd.DataFrame()
            state['last_day_closes_map'] = today_closes_map if 'today_closes_map' in locals() else {}

        day_log.append(f"--- 当天选股池和5日线计算完毕 (决策池 {len(valid_df)} 只, 基础池 {len(unfiltered_valid_codes_set)} 只) ---")

        # --- 5. Market Timing Logic (MA Cross Rule) ---
        # (此部分无改动)
        day_log.append(f"--- 开始微盘指数【均线交叉规则】择时检查 ---")
        trigger_sell_all_due_ma_state_today = False
        allow_buy = state.get('allow_buy', True) 
        if micro_cap_today_data:
            try:
                f_today_val = float(micro_cap_today_data['index_value'])
                f_today_ma5 = float(micro_cap_today_data['ma5'])
                f_today_ma20 = float(micro_cap_today_data['ma20'])
                is_clear_condition = (f_today_val < f_today_ma5) and (f_today_ma5 < f_today_ma20)
                is_recover_condition = (f_today_val > f_today_ma5) or (f_today_ma5 > f_today_ma20)
                if is_clear_condition:
                    day_log.append(f"!!! MA择时清仓触发 (指数 < MA5 且 MA5 < MA20) !!!")
                    if positions:
                        trigger_sell_all_due_ma_state_today = True
                    allow_buy = False
                elif is_recover_condition:
                    day_log.append("  - !!! MA择时恢复买入触发 (指数 > MA5 或 MA5 > MA20) !!!")
                    allow_buy = True
                else:
                    day_log.append("  - 状态: 处于均线交叉观察区，不允许买入。")
                    allow_buy = False
            except (TypeError, KeyError, ValueError) as e:
                day_log.append(f"  - 警告: 计算均线交叉规则时数据不完整或转换失败: {e}。沿用上一日allow_buy状态。")
                allow_buy = state.get('allow_buy', True)
        else:
            day_log.append("  - 警告: 当日微盘指数数据缺失，无法执行择时。沿用上一日allow_buy状态。")
            allow_buy = state.get('allow_buy', True)
        state['allow_buy'] = allow_buy
        day_log.append(f"--- 微盘指数择时检查结束 (本决策日最终 allow_buy: {allow_buy}) ---")

        # --- 6. 执行 T+1 订单 (来自昨日决策) ---
        # (此部分无改动)
        executed_sells_today = []; executed_buys_today = {}
        if has_next_day and not next_open_df.empty:
            day_log.append("--- 开始执行 T+1 订单 (来自昨日决策) ---")
            for order in [o for o in pending_orders if o['action']=='sell']:
                code = order['code']; shares = order['shares']
                open_price_series = next_open_df.get('open')
                open_price = open_price_series.get(code) if open_price_series is not None else None
                if open_price and pd.notna(open_price) and open_price > 0 and shares > 0:
                    position_data = positions.get(code); sell_value = shares * open_price; cash += sell_value; executed_sells_today.append(code); pnl = 0.0; cost_sell = 0.0
                    if position_data:
                        cost_sell = position_data.get('cost', 0);
                        if cost_sell > 0: pnl = (open_price - cost_sell) * shares
                        state['trade_results'].append(pnl)
                        trade_record = {'交易日期': next_day_str,'股票代码': code,'股票名称': self.name_map.get(code, 'N/A'),'操作': '卖出', '成交价': float(open_price), '成交数量': int(shares),'成交金额': float(sell_value), '成本价': float(cost_sell), '盈亏金额': float(pnl),'盈亏率': float((pnl / (cost_sell * shares) * 100) if cost_sell > 0 and shares > 0 else 0),'持有天数': int((pd.to_datetime(next_day_str) - pd.to_datetime(position_data.get('buy_date'))).days + 1 if position_data.get('buy_date') else 0),'原因': str(order.get('reason', 'N/A'))}; state['trade_records'].append(trade_record)
                    else: day_log.append(f"警告：执行卖出 {code} 时未在持仓中找到记录，无法计算盈亏。")
                    day_log.append(f"执行卖出 {code}({self.name_map.get(code,'')}) {shares:,}股 @ {open_price:.2f} | 获现:{sell_value:,.2f} | 成本:{cost_sell:.2f} | 盈亏:{pnl:,.2f} | 原因:{order.get('reason','')}")
                else: 
                    fail_reason = "";
                    if not open_price or pd.isna(open_price) or open_price <= 0: fail_reason += "无有效执行价 ";
                    if shares <= 0: fail_reason += "股数无效 "; 
                    day_log.append(f"卖出委托失败 {code}: {fail_reason}(价:{open_price}, 股:{shares})")
            for code in executed_sells_today:
                if code in positions: del positions[code]
            for order in [o for o in pending_orders if o['action']=='buy']:
                code = order['code']; shares = order['shares']; buy_rank_order = order.get('buy_rank')
                open_price_series = next_open_df.get('open')
                open_price = open_price_series.get(code) if open_price_series is not None else None
                prev_close_for_buy_check = today_closes_map.get(code); skip_buy_price_jump = False
                if open_price and pd.notna(open_price) and open_price > 0 and prev_close_for_buy_check and pd.notna(prev_close_for_buy_check) and prev_close_for_buy_check > 0:
                    if ((open_price - prev_close_for_buy_check) / prev_close_for_buy_check) >= 0.095:
                        skip_buy_price_jump = True; day_log.append(f"跳过买入 {code}: 执行日开盘涨停或涨幅过大 (开:{open_price:.2f}, 昨收:{prev_close_for_buy_check:.2f})")
                elif not (open_price and pd.notna(open_price) and open_price > 0): 
                    skip_buy_price_jump = True; day_log.append(f"跳过买入 {code}: 无有效执行日开盘价 ({open_price})")
                if skip_buy_price_jump: continue
                cost_of_buy = shares * open_price
                if cash >= cost_of_buy:
                    cash -= cost_of_buy; initial_lowest_rank = buy_rank_order if buy_rank_order is not None else 99999; 
                    executed_buys_today[code] = {'shares': shares, 'cost': open_price, 'buy_date': next_date,'buy_rank': buy_rank_order, 'lowest_rank': initial_lowest_rank, 'last_close': open_price}
                    trade_record = {'交易日期': next_day_str, '股票代码': code,'股票名称': self.name_map.get(code, 'N/A'),'操作': '买入', '成交价': float(open_price), '成交数量': int(shares),'成交金额': float(cost_of_buy), '买入排名': buy_rank_order, '原因': '常规买入'}; state['trade_records'].append(trade_record)
                    day_log.append(f"执行买入 {code}({self.name_map.get(code,'')}) {shares:,}股 @ {open_price:.2f} | 花费:{cost_of_buy:,.2f} | 买入排名:{buy_rank_order if buy_rank_order is not None else 'N/A'}")
                else: day_log.append(f"买入委托失败 {code}: 现金不足 (需 {cost_of_buy:,.2f}, 有 {cash:,.2f})")
            positions.update(executed_buys_today); 
            day_log.append("--- T+1 订单执行完毕 ---"); 
            pending_orders = []
        elif not has_next_day: 
            day_log.append("--- 最后决策日，跳过订单执行 (无下一日) ---"); pending_orders = []; state['pending_orders'] = []
        else:
            day_log.append("--- 警告: 无次日开盘数据，无法执行 T+1 订单 ---"); pending_orders = []; state['pending_orders'] = []
        state['pending_orders'] = pending_orders

        # --- 8. 更新持仓最低排名 ---
        # (此部分无改动)
        day_log.append("--- 更新持仓最低排名 ---")
        if positions and unfiltered_rank_dict:
            for code, pos in positions.items():
                current_rank = unfiltered_rank_dict.get(code)
                if current_rank is not None:
                    try:
                        current_rank_int = int(current_rank); stored_lowest_rank = pos.get('lowest_rank')
                        if stored_lowest_rank is None or stored_lowest_rank == 99999: pos['lowest_rank'] = current_rank_int; day_log.append(f"  - {code}: 初始化/修正 最低排名为 {current_rank_int}")
                        else:
                            stored_lowest_rank_int = int(stored_lowest_rank)
                            if current_rank_int < stored_lowest_rank_int: pos['lowest_rank'] = current_rank_int; day_log.append(f"  - {code}: 最低排名从 {stored_lowest_rank_int} 更新为 {current_rank_int}")
                    except (ValueError, TypeError) as e_rank: print(f"警告: 更新最低排名时转换错误 for {code}: {e_rank}"); pass
        
        # --- 9. 生成 T 日决策订单 (用于 T+1 执行) ---
        # (这部分已是最新版，包含5日线逻辑，无需改动)
        newly_generated_pending_orders = [] 
        day_log.append(f"--- 开始生成 {today_str} 决策日订单 (当日微盘择时 allow_buy: {allow_buy}, 新MA规则清仓信号: {trigger_sell_all_due_ma_state_today}) ---")
        force_sell_reason = None
        if trigger_sell_all_due_ma_state_today:
            force_sell_reason = "MA择时(指数<MA5 & 指数<MA15)"
        else:
            current_month = cur_date.month
            next_trading_day_month = next_date.month if has_next_day and next_date is not None else None
            if current_month == 12 and next_trading_day_month == 51: force_sell_reason = "年底清仓(1月不交易)"
            elif current_month == 3 and next_trading_day_month == 54: force_sell_reason = "季末清仓(4月不交易)"
        
        if force_sell_reason:
            day_log.append(f"--- {today_str}: 触发强制清仓信号: {force_sell_reason} ---")
            if not positions:
                day_log.append("    - 当前无持仓，无需执行清仓。")
            else:
                sells_for_force_count = 0
                for code_sell, pos_sell in positions.items():
                    shares_to_sell = pos_sell.get('shares', 0)
                    if shares_to_sell > 0:
                        newly_generated_pending_orders.append({'action': 'sell', 'code': code_sell, 'shares': shares_to_sell,'reason': force_sell_reason})
                        day_log.append(f"    - 添加强制清仓委托: {code_sell} {shares_to_sell:,} 股")
                        sells_for_force_count +=1
                if sells_for_force_count > 0:
                    day_log.append(f"    - 共添加 {sells_for_force_count} 只股票的强制清仓委托。")
            day_log.append(f"    - 因强制清仓，本日不进行其他常规买卖决策。")
        else:
            current_month = cur_date.month
            if current_month == 51 or current_month == 54:
                month_name = "51月" if current_month == 51 else "54月"
                day_log.append(f"--- {today_str} 处于 {month_name}，本月不进行新的买卖决策 ---")
            else:
                day_log.append(f"--- {today_str}: 进入常规交易决策流程 (当日微盘择时 allow_buy: {allow_buy}) ---")
                is_portfolio_rules_rebalance_day = (self.hist_sim_idx > 0 and hold_days > 0 and self.hist_sim_idx % hold_days == 0) or (self.hist_sim_idx == 0)

                codes_sold_or_pending_sell_this_cycle = [] 
                sells_generated_this_block_for_cash_calc = [] 

                if is_portfolio_rules_rebalance_day:
                    day_log.append(f"  --- 执行常规组合规则卖出决策 (调仓日: {is_portfolio_rules_rebalance_day}) ---")
                    codes_to_sell_next_decision_local = []
                    sell_reasons_next_decision_local = {}
                    for code, pos in positions.items():
                        sell_flag = False; reason = ""
                        current_price_for_sell_decision = today_closes_map.get(code) 
                        cost_price_for_sell_decision = pos.get('cost')
                        ma5_price = ma5_map.get(code)
                        #if current_price_for_sell_decision and ma5_price and current_price_for_sell_decision < ma5_price:
                        #    sell_flag = True
                        #    reason = f"价格 {current_price_for_sell_decision:.2f} < 5日线 {ma5_price:.2f}"
                        if not sell_flag and current_price_for_sell_decision is not None and pd.notna(current_price_for_sell_decision) and \
                        cost_price_for_sell_decision is not None and pd.notna(cost_price_for_sell_decision) and cost_price_for_sell_decision > 0:
                            profit_pct = (current_price_for_sell_decision - cost_price_for_sell_decision) / cost_price_for_sell_decision
                            if profit_pct > 2.2:
                                sell_flag = True; reason = f"止盈120% (实际盈利{profit_pct*100:.2f}%)" 
                            elif profit_pct < -0.99:
                                sell_flag = True; reason = f"止损-9% (实际{profit_pct*100:.2f}%)"
                        if not sell_flag and code not in unfiltered_valid_codes_set: 
                            sell_flag = True; reason = "不符合当日基础选股规则"
                        if not sell_flag: 
                            lowest_rank = pos.get('lowest_rank') 
                            cur_rank_val = unfiltered_rank_dict.get(code)
                            if lowest_rank is not None and cur_rank_val is not None:
                                try:
                                    rank_deterioration = int(cur_rank_val) - int(lowest_rank)
                                    if rank_deterioration >= rank_boost: 
                                        sell_flag = True; reason = f"排名较最低({lowest_rank})恶化{rank_deterioration}≥{rank_boost}"
                                except (ValueError, TypeError): pass 
                        if sell_flag:
                            codes_to_sell_next_decision_local.append(code)
                            sell_reasons_next_decision_local[code] = reason if reason else "常规卖出触发"
                            day_log.append(f"      - 标记常规卖出 ({reason}): {code}")
                    sell_orders_added_count = 0
                    for code_sell_local in set(codes_to_sell_next_decision_local):
                        if code_sell_local in positions and positions[code_sell_local].get('shares', 0) > 0:
                            shares_to_sell = positions[code_sell_local]['shares']
                            sell_order_item = {'action': 'sell', 'code': code_sell_local, 'shares': shares_to_sell, 'reason': sell_reasons_next_decision_local.get(code_sell_local, 'N/A')}
                            newly_generated_pending_orders.append(sell_order_item) 
                            sells_generated_this_block_for_cash_calc.append(sell_order_item) 
                            day_log.append(f"    - 添加常规卖出委托: {code_sell_local} {shares_to_sell:,} 股, 原因: {sell_reasons_next_decision_local.get(code_sell_local, 'N/A')}")
                            sell_orders_added_count += 1
                            codes_sold_or_pending_sell_this_cycle.append(code_sell_local) 
                    if sell_orders_added_count == 0: day_log.append("    - 无常规组合规则卖出订单生成。")
                    day_log.append("    --- 常规组合规则卖出决策完毕 ---")
                else: 
                    day_log.append(f"  --- 非组合规则调仓日，不执行常规组合规则相关的卖出 ---")
                
                if allow_buy:
                    if is_portfolio_rules_rebalance_day: 
                        day_log.append(f"  --- 符合买入条件 (allow_buy=True, 调仓日)，执行【新】5日线买入决策 ---")
                        holdings_after_pending_sell = set(positions.keys()) - set(codes_sold_or_pending_sell_this_cycle)
                        num_target_holdings = num_stocks 
                        num_slots_to_fill = num_target_holdings - len(holdings_after_pending_sell)
                        day_log.append(f"      - 计划买入以补齐 {num_slots_to_fill} 个持仓槽位。")

                        if num_slots_to_fill > 0:
                            estimated_cash_from_sells_today_decision = 0
                            for sell_order_info in sells_generated_this_block_for_cash_calc: 
                                price_ps = today_closes_map.get(sell_order_info['code'])
                                if price_ps and pd.notna(price_ps) and sell_order_info['shares'] > 0: estimated_cash_from_sells_today_decision += sell_order_info['shares'] * price_ps
                            market_value_of_retained_positions = 0.0
                            for code_mv, pos_mv in positions.items():
                                if code_mv not in codes_sold_or_pending_sell_this_cycle:
                                    close_price_mv = today_closes_map.get(code_mv)
                                    shares_mv = pos_mv.get('shares', 0)
                                    if close_price_mv and pd.notna(close_price_mv) and shares_mv > 0: market_value_of_retained_positions += shares_mv * close_price_mv
                            decision_time_cash_for_buy_calc = cash + estimated_cash_from_sells_today_decision 
                            total_assets_for_buy_decision = decision_time_cash_for_buy_calc + market_value_of_retained_positions
                            day_log.append(f"      - 买入决策时总资产估算: {total_assets_for_buy_decision:,.2f} (现金估算:{decision_time_cash_for_buy_calc:,.2f}, 预留市值:{market_value_of_retained_positions:,.2f})")
                            target_capital_per_slot = (total_assets_for_buy_decision * target_percent_buffer) / num_target_holdings if num_target_holdings > 0 else 0
                            day_log.append(f"      - 每只股票目标分配资金: {target_capital_per_slot:,.2f}")

                            buy_candidates_final = []
                            potential_buys_df = valid_df[~valid_df['stock_code'].isin(holdings_after_pending_sell)]

                            day_log.append(f"      - 开始从 {len(potential_buys_df)} 只候选股中寻找站上5日线的标的...")
                            for _, row_buy in potential_buys_df.iterrows():
                                if len(buy_candidates_final) >= num_slots_to_fill:
                                    day_log.append(f"      - 已找到 {num_slots_to_fill} 个符合条件的买入标的，停止寻找。")
                                    break
                                
                                code_buy = row_buy['stock_code']
                                buy_decision_price = today_closes_map.get(code_buy)
                                ma5_price = ma5_map.get(code_buy)

                                if buy_decision_price and ma5_price and buy_decision_price > ma5_price:
                                    buy_candidates_final.append(row_buy)
                                    day_log.append(f"      - > 候选股 {code_buy} (排名:{row_buy['rank']}) 价格 {buy_decision_price:.2f} > 5日线 {ma5_price:.2f}，加入备选。")
                            
                            if buy_candidates_final and target_capital_per_slot > 0:
                                cash_remaining_for_buys_loop = decision_time_cash_for_buy_calc 
                                buy_orders_generated_count = 0; total_buy_cost_estimated = 0.0
                                
                                for row_buy in buy_candidates_final:
                                    code_buy = row_buy['stock_code']; buy_decision_price = today_closes_map.get(code_buy); buy_rank_val_inner = unfiltered_rank_dict.get(code_buy)
                                    if buy_decision_price and pd.notna(buy_decision_price) and buy_decision_price > 0:
                                        shares_to_buy = math.floor((target_capital_per_slot / buy_decision_price) / lot_size) * lot_size
                                        if shares_to_buy > 0:
                                            cost_this_buy_estimated = shares_to_buy * buy_decision_price
                                            if cost_this_buy_estimated <= cash_remaining_for_buys_loop:
                                                newly_generated_pending_orders.append({'action': 'buy', 'code': code_buy, 'shares': shares_to_buy, 'buy_rank': buy_rank_val_inner })
                                                cash_remaining_for_buys_loop -= cost_this_buy_estimated; total_buy_cost_estimated += cost_this_buy_estimated; buy_orders_generated_count += 1
                                                day_log.append(f"      - 标记买入: {code_buy} {shares_to_buy:,} 股 @ {buy_decision_price:.2f} (成本约 {cost_this_buy_estimated:,.2f}) Rank:{buy_rank_val_inner if buy_rank_val_inner is not None else 'N/A'} | 剩余现金估算: {cash_remaining_for_buys_loop:,.2f}")
                                            else: 
                                                day_log.append(f"      - 跳过买入 {code_buy}: 预估现金不足 (需 {cost_this_buy_estimated:,.2f}, 余 {cash_remaining_for_buys_loop:,.2f})，停止买入。"); break
                                        else: day_log.append(f"      - 跳过买入 {code_buy}: 目标股数计算为0")
                                    else: day_log.append(f"      - 跳过买入 {code_buy}: 无有效当日收盘价")

                                day_log.append(f"      - 共标记 {buy_orders_generated_count} 个买入订单，预估总成本: {total_buy_cost_estimated:,.2f}")
                            else:
                                day_log.append("      - 未找到符合5日线条件的买入标的或资金不足。")
                        else:
                            day_log.append(f"      - 无需补齐新的持仓槽位。")

                        day_log.append("    --- 【新】5日线买入决策完毕 ---")
                    else: 
                        day_log.append("  --- 非调仓日 (allow_buy=True)，不生成常规买入订单 ---")
                elif not trigger_sell_all_due_ma_state_today: 
                    day_log.append(f"--- 当前禁止买入 (allow_buy=False)，不生成常规买入订单 ---")

        state['pending_orders'] = newly_generated_pending_orders 
        day_log.append(f"--- {today_str} 决策日订单生成完毕 ({len(newly_generated_pending_orders)} 条待执行) ---")
        
        # --- 11. Calculate End-of-Day Snapshot and Assets ---
        # (此部分无改动)
        day_log.append("--- 计算决策日收盘资产 ---")
        final_market_value = 0.0
        positions_snapshot = [] 
        ranks_for_snapshot = unfiltered_rank_dict 
        for code, pos_data in positions.items(): 
            name = self.name_map.get(code, 'N/A'); pos_item_market_value = 0.0; company_total_market_cap_billion = None; open_price_snap = None; close_price_snap = None; cost_price_snap = pos_data.get('cost', 0.0); shares_snap = pos_data.get('shares', 0); buy_rank_snap = pos_data.get('buy_rank', 'N/A'); days_held_snap = 0; pnl_percentage_snap = 0.0; lowest_rank_snap = str(pos_data.get('lowest_rank', 'N/A')); current_rank_snap = str(ranks_for_snapshot.get(code, 'N/A')) 
            try: 
                buy_date_obj = pos_data.get('buy_date')
                if buy_date_obj:
                    try:
                        buy_date_dt = pd.to_datetime(buy_date_obj)
                        if pd.notna(buy_date_dt): days_held_snap = max(0, (cur_date - buy_date_dt).days + 1)
                    except Exception as e_date_calc: print(f"警告: 计算持股天数出错({code}): {e_date_calc}"); days_held_snap = 0 
                if code in today_data_indexed.index:
                    stock_day_data = today_data_indexed.loc[code]
                    raw_close_price = stock_day_data.get('close')
                    if pd.notna(raw_close_price):
                        try: close_price_snap = float(raw_close_price); pos_data['last_close'] = close_price_snap
                        except (ValueError, TypeError): close_price_snap = None
                    else: close_price_snap = None
                    raw_open_price = stock_day_data.get('open')
                    if pd.notna(raw_open_price):
                        try: open_price_snap = float(raw_open_price)
                        except (ValueError, TypeError): open_price_snap = None
                    market_value_column = 'total_mv' 
                    if market_value_column in stock_day_data and pd.notna(stock_day_data[market_value_column]):
                        try:
                            company_total_market_cap_raw = float(stock_day_data[market_value_column]) * 10000.0 
                            company_total_market_cap_billion = company_total_market_cap_raw / 100000000.0 
                        except (ValueError, TypeError): print(f"警告: 无法转换总市值 '{stock_day_data[market_value_column]}' 为数值 (代码: {code})"); company_total_market_cap_billion = None
                if close_price_snap is None or close_price_snap <= 0:
                    close_price_for_calc = pos_data.get('last_close')
                    if close_price_for_calc: day_log.append(f"  - 提示: {code} 当日无价格，资产计算沿用上一日价格 {close_price_for_calc:.2f}")
                else: close_price_for_calc = close_price_snap
                if shares_snap > 0 and close_price_for_calc is not None and close_price_for_calc > 0:
                    pos_item_market_value = shares_snap * close_price_for_calc
                    final_market_value += pos_item_market_value 
                    if cost_price_snap > 0: pnl_percentage_snap = ((close_price_for_calc - cost_price_snap) / cost_price_snap * 100)
                positions_snapshot.append({'code': code, 'name': name, 'shares': shares_snap, 'cost': cost_price_snap, 'open': open_price_snap, 'close': close_price_snap,'market_value_pos': pos_item_market_value, 'company_market_cap_billion': company_total_market_cap_billion, 'current_rank': current_rank_snap, 'buy_rank': buy_rank_snap, 'days_held': days_held_snap, 'pnl_pct': pnl_percentage_snap, 'lowest_rank': lowest_rank_snap})
            except Exception as e_snapshot_item:
                print(f"[{today_str}] 创建单个持仓快照项 {code} 时发生意外错误: {e_snapshot_item}"); traceback.print_exc()
                positions_snapshot.append({'code': code, 'name': name, 'error': str(e_snapshot_item)})

        total_assets_end_of_day = cash + final_market_value
        day_log.append(f"--- 决策日收盘状态 ({today_str}) ---")
        day_log.append(f"现金: {cash:,.2f}")
        day_log.append(f"持仓市值: {final_market_value:,.2f} ({len(positions)} 只股票)")
        day_log.append(f"总资产: {total_assets_end_of_day:,.2f}")
        day_log.append(f"允许买入状态 (allow_buy): {allow_buy}")
        day_log.append(f"--- {today_str} 处理结束 ---")

        # --- 12. Update State with Final Cash for the day ---
        state['cash'] = cash

        # --- 13. Store Log Entry for the Day ---
        current_log_entry = {'date': today_str, 'cash': cash, 'market_value': final_market_value, 'total_assets': total_assets_end_of_day, 'log': '\n'.join(day_log), 'positions_snapshot': positions_snapshot}
        state['logs'].append(current_log_entry)

        # --- 14. Advance Index ---
        self.hist_sim_idx += 1

        # --- 15. Refresh Display ---
        try: self._hist_refresh_display()
        except Exception as e_ref: print(f"刷新界面时出错 ({today_str}): {e_ref}")

        # --- 16. Trigger Next Step if Auto-Running ---
        if getattr(self, 'hist_sim_running', False):
            QTimer.singleShot(50, self._hist_next_day)

    def _get_target_portfolio_actions(self):
        """
        (版本：带详细调试打印)
        根据最终的回测状态和决策依据，计算目标交易动作。
        返回:
            tuple: (predicted_sells, predicted_buys, logs) 或 (None, None, logs) 如果出错。
                   predicted_sells: 包含字典的列表 [{'code': C, 'shares': S, 'reason': R}, ...]
                   predicted_buys: 包含字典的列表 [{'code': C, 'shares': S, 'estimated_price': P, 'rank': R}, ...]
        """
        logs = ["--- [DEBUG] 开始计算最终目标交易动作 ---"] # 添加 DEBUG 标记
        print("\n" + logs[-1]) # 打印到控制台，标记开始
        predicted_sells = [] # 使用局部变量
        predicted_buys = []  # 使用局部变量

        # --- 检查并获取 State ---
        if not hasattr(self, 'hist_sim_state') or not self.hist_sim_state:
            logs.append("错误: 历史模拟状态未找到。")
            print(logs[-1])
            return None, None, logs
        final_state = self.hist_sim_state

        # --- 从 state 获取所需信息 ---
        current_cash_at_end = final_state.get('cash', 0)
        positions_at_end = final_state.get('positions', {}) # 回测结束时的持仓
        current_rank_dict = final_state.get('final_rank_dict', {}) # 最后一日排名
        valid_codes_set = final_state.get('final_valid_codes_set', set()) # 最后一日有效代码
        latest_price_map = final_state.get('last_day_closes_map', {}) # 最后一日收盘价
        valid_df_lastday = final_state.get('final_decision_pool_df', pd.DataFrame()) # 最后一日选股DF
        name_map = getattr(self, 'name_map', {}) # 获取名称映射

        # --- 打印获取到的初始状态 ---
        print(f"DEBUG [GET_ACTIONS]: 获取到 final_state:")
        print(f"  - current_cash_at_end: {current_cash_at_end:,.2f}")
        print(f"  - len(positions_at_end): {len(positions_at_end)}")
        print(f"  - len(current_rank_dict): {len(current_rank_dict)}")
        print(f"  - len(valid_codes_set): {len(valid_codes_set)}")
        print(f"  - len(latest_price_map): {len(latest_price_map)}")
        print(f"  - valid_df_lastday is empty: {valid_df_lastday.empty}")
        print(f"  - len(name_map): {len(name_map)}")
        # --- 结束打印初始状态 ---

        # --- 检查数据完整性 ---
        # 注意：这里允许 positions_at_end 为空，因为刚开始或清仓后就是空的
        if not latest_price_map or not current_rank_dict or valid_df_lastday.empty:
             logs.append(f"错误: 最终状态决策所需信息不完整 (价格/排名/决策池)。")
             print(logs[-1]) # 打印错误
             return None, None, logs
        # if not name_map: # 名称缺失不阻止计算
        #      logs.append("警告: 缺少股票名称映射。")
        #      print(logs[-1])

        # --- 获取策略参数 ---
        num_stocks = self.num_stocks_spinbox.value()
        rank_boost = self.rank_boost_spinbox.value()
        lot_size = LOT_SIZE
        target_percent_buffer = TARGET_PERCENT_BUFFER
        print(f"DEBUG [GET_ACTIONS]: 策略参数 - num_stocks={num_stocks}, rank_boost={rank_boost}, lot_size={lot_size}, buffer={target_percent_buffer}")

        # --- 计算预测卖出 ---
        print("DEBUG [GET_ACTIONS]: --- 开始计算预测卖出 ---")
        codes_to_sell_predict = []
        sell_reasons_predict = {}
        for code, pos in positions_at_end.items():
             reason = None
             # 规则1：不在有效池
             if code not in valid_codes_set:
                 reason = "不符合最新选股规则"
                 print(f"DEBUG [GET_ACTIONS]:  标记卖出 {code}, 原因: {reason}")
             # 规则2：排名恶化
             elif (pos.get('buy_rank') is not None or pos.get('lowest_rank') is not None) and code in current_rank_dict:
                 try:
                    current_rank = current_rank_dict[code]
                    base_rank_key = 'lowest_rank' if 'lowest_rank' in pos and pos['lowest_rank'] is not None else 'buy_rank'
                    base_rank = pos.get(base_rank_key)
                    if base_rank is not None:
                         base_rank = int(base_rank)
                         rank_deterioration = int(current_rank) - base_rank
                         if rank_deterioration >= rank_boost:
                               reason = f"排名较基准({base_rank_key}:{base_rank})恶化{rank_deterioration} >= 阈值({rank_boost})"
                               print(f"DEBUG [GET_ACTIONS]:  标记卖出 {code}, 原因: {reason}") # 打印原因
                 except Exception as e_sell_rank:
                      print(f"DEBUG [GET_ACTIONS]: 计算卖出排名恶化时出错 for {code}: {e_sell_rank}")
             # 如果有原因，则加入列表
             if reason:
                 codes_to_sell_predict.append(code)
                 sell_reasons_predict[code] = reason
        # 填充最终卖出列表
        for code_sell in set(codes_to_sell_predict):
            # 确保 positions_at_end 里有这个 code 且 shares > 0 才加入最终列表
            if code_sell in positions_at_end and positions_at_end[code_sell].get('shares', 0) > 0:
                 shares_to_sell = positions_at_end[code_sell]['shares']
                 predicted_sells.append({'code': code_sell,'shares': shares_to_sell,'reason': sell_reasons_predict.get(code_sell, 'N/A')})
        logs.append(f"计算完成: 预测卖出 {len(predicted_sells)} 只")
        print(f"DEBUG [GET_ACTIONS]: 最终预测卖出列表 (predicted_sells): {predicted_sells}")


        # --- 计算预测买入 ---
        print("DEBUG [GET_ACTIONS]: --- 开始计算预测买入 ---")
        holdings_after_predicted_sell_codes = set(positions_at_end.keys()) - set(codes_to_sell_predict)
        num_to_buy = num_stocks - len(holdings_after_predicted_sell_codes)
        logs.append(f"计算完成: 预测买入 {num_to_buy} 只") # Log how many are needed
        print(f"DEBUG [GET_ACTIONS]: 目标持股数={num_stocks}, 卖出后剩余代码数={len(holdings_after_predicted_sell_codes)}, 需买入数={num_to_buy}")

        if num_to_buy > 0:
            # 使用 state 中存储的最后一日的 valid_df
            available_buy_df = valid_df_lastday[
                ~valid_df_lastday['stock_code'].isin(holdings_after_predicted_sell_codes)
            ].head(num_to_buy)
            print(f"DEBUG [GET_ACTIONS]: 从决策池筛选出可用买入标的数量 len(available_buy_df) = {len(available_buy_df)}")

            if not available_buy_df.empty:
                 estimated_cash_from_sells = sum(
                     # 确保价格有效再计算
                     sell_order['shares'] * float(latest_price_map.get(sell_order['code'], 0))
                     for sell_order in predicted_sells
                     if latest_price_map.get(sell_order['code']) is not None and pd.notna(latest_price_map.get(sell_order['code']))
                 )
                 available_cash_for_buys = current_cash_at_end + estimated_cash_from_sells
                 print(f"DEBUG [GET_ACTIONS]: 模拟结束时现金 current_cash_at_end = {current_cash_at_end:,.2f}")
                 print(f"DEBUG [GET_ACTIONS]: 预估卖出回收现金 estimated_cash_from_sells = {estimated_cash_from_sells:,.2f}")
                 print(f"DEBUG [GET_ACTIONS]: 预估可用于买入的总现金 available_cash_for_buys = {available_cash_for_buys:,.2f}")

                 if available_cash_for_buys > 0 and num_stocks > 0:
                     capital_per_stock = (available_cash_for_buys * target_percent_buffer) / num_stocks
                     print(f"DEBUG [GET_ACTIONS]: 平均每股分配资金 capital_per_stock = {capital_per_stock:,.2f}")
                     cash_used_for_buys_est = 0
                     print("DEBUG [GET_ACTIONS]: --- 开始遍历可用买入标的 ---")
                     for i, row in available_buy_df.iterrows(): # 使用 i 追踪是第几个候选
                          code_buy = row['stock_code']
                          latest_price = latest_price_map.get(code_buy) # Use stored price
                          rank_buy = row['rank'] # Use stored rank
                          print(f"DEBUG [GET_ACTIONS]:  候选 {i+1}: {code_buy}, 最新价 price={latest_price}, 排名 rank={rank_buy}")

                          # 增加对 latest_price 的详细检查
                          if latest_price and pd.notna(latest_price):
                               try:
                                   latest_price_f = float(latest_price) # 尝试转换为浮点数
                                   if latest_price_f > 0 and capital_per_stock > 0:
                                       target_shares_raw = capital_per_stock / latest_price_f
                                       shares_to_buy = math.floor(target_shares_raw / lot_size) * lot_size
                                       print(f"DEBUG [GET_ACTIONS]:   计算得到 shares_to_buy = {shares_to_buy}")

                                       if shares_to_buy > 0:
                                            cost_of_buy_est = shares_to_buy * latest_price_f
                                            cash_remaining_before_this_buy = available_cash_for_buys - cash_used_for_buys_est
                                            cash_check = cost_of_buy_est <= cash_remaining_before_this_buy
                                            print(f"DEBUG [GET_ACTIONS]:   预估成本 cost_est={cost_of_buy_est:.2f}, 需现金 <= 剩余现金 {cash_remaining_before_this_buy:.2f}? {cash_check}")

                                            if cash_check:
                                                 predicted_buys.append({'code': code_buy,'shares': shares_to_buy,'estimated_price': latest_price_f,'estimated_cost': cost_of_buy_est,'rank': rank_buy})
                                                 cash_used_for_buys_est += cost_of_buy_est
                                                 print(f"DEBUG [GET_ACTIONS]:    >> 加入买入列表。累计预估花费: {cash_used_for_buys_est:.2f}")
                                            else:
                                                 print("DEBUG [GET_ACTIONS]:    现金不足，停止本次及后续买入尝试。")
                                                 break # Stop trying to buy more
                                       else:
                                            print(f"DEBUG [GET_ACTIONS]:   目标股数计算为0，跳过。")
                                   else:
                                        print(f"DEBUG [GET_ACTIONS]:   价格无效 ({latest_price_f}) 或目标资金为0，跳过。")
                               except (ValueError, TypeError) as e_price:
                                    print(f"DEBUG [GET_ACTIONS]:   价格转换错误 for {code_buy} (price={latest_price}): {e_price}，跳过。")
                          else:
                               print(f"DEBUG [GET_ACTIONS]:   无效价格 (price={latest_price})，跳过。")
                     print("DEBUG [GET_ACTIONS]: --- 遍历买入标的结束 ---")
                 else:
                      print("DEBUG [GET_ACTIONS]: 预估可买入现金 <= 0 或目标持股数 <= 0，无法买入。")
            else:
                 print("DEBUG [GET_ACTIONS]: 没有可供买入的股票（从决策池筛选后为空）。")
        else:
             print(f"DEBUG [GET_ACTIONS]: 无需买入 (num_to_buy = {num_to_buy})。")

        logs.append("最终目标交易动作计算完毕。")
        print(f"DEBUG [GET_ACTIONS]: 最终预测买入列表 (predicted_buys): {predicted_buys}") # 打印最终买入列表
        print("DEBUG [GET_ACTIONS]: --- 计算最终目标交易动作结束 ---")
        # --- 返回计算结果 ---
        return predicted_sells, predicted_buys, logs
# --- (其他方法保持不变) ---

    def _generate_and_display_target_portfolio(self):
        """
        (调试增强版)
        在模拟结束后，计算最终的目标持仓组合，存储建议，并弹窗显示。
        使用 state 中存储的最后一日决策依据进行强制评估。
        """
        # <--- 函数入口打印 --->
        print("\n***** DEBUG: 已成功进入 _generate_and_display_target_portfolio 函数内部 *****")

        logs = ["--- [DEBUG] 开始计算最终目标持仓和交易动作 ---"]
        print(logs[-1]) # 打印到控制台

        # 初始化本次计算的局部变量
        predicted_sells_local = []
        predicted_buys_local = []
        # 先重置/清空最终存储结果，以防函数中途出错导致使用旧数据
        self.final_predicted_sells = None
        self.final_predicted_buys = None

        # --- 检查并获取 State ---
        if not hasattr(self, 'hist_sim_state') or not self.hist_sim_state:
            logs.append("错误: 历史模拟状态未找到。")
            print(logs[-1])
            self._show_prediction_dialog("\n".join(logs)); return # 显示错误并退出
        final_state = self.hist_sim_state

        # --- 从 state 获取所需信息 ---
        current_cash_at_end = final_state.get('cash', 0)
        positions_at_end = final_state.get('positions', {}) # 回测结束时的持仓
        current_rank_dict = final_state.get('final_rank_dict', {}) # 最后一日排名
        valid_codes_set = final_state.get('final_valid_codes_set', set()) # 最后一日有效代码
        latest_price_map = final_state.get('last_day_closes_map', {}) # 最后一日收盘价
        valid_df_lastday = final_state.get('final_decision_pool_df', pd.DataFrame()) # 最后一日选股DF
        name_map = getattr(self, 'name_map', {}) # 获取名称映射

        # --- 打印获取到的初始状态 ---
        print(f"DEBUG [GET_ACTIONS]: 获取到 final_state:")
        print(f"  - current_cash_at_end: {current_cash_at_end:,.2f}")
        print(f"  - len(positions_at_end): {len(positions_at_end)}")
        print(f"  - len(current_rank_dict): {len(current_rank_dict)}")
        print(f"  - len(valid_codes_set): {len(valid_codes_set)}")
        print(f"  - len(latest_price_map): {len(latest_price_map)}")
        print(f"  - valid_df_lastday is empty: {valid_df_lastday.empty}")
        print(f"  - len(name_map): {len(name_map)}")
        # --- 结束打印初始状态 ---

        # --- 检查数据完整性 ---
        if not latest_price_map or not current_rank_dict or valid_df_lastday.empty:
             logs.append(f"错误: 最终状态决策所需信息不完整 (价格/排名/决策池)。")
             print(logs[-1])
             self._show_prediction_dialog("\n".join(logs)); return
        # if not name_map: logs.append("警告: 缺少股票名称映射。"); print(logs[-1]) # 名称缺失不阻止计算

        # --- 获取策略参数 ---
        num_stocks = self.num_stocks_spinbox.value()
        rank_boost = self.rank_boost_spinbox.value()
        lot_size = LOT_SIZE
        target_percent_buffer = TARGET_PERCENT_BUFFER
        print(f"DEBUG [GET_ACTIONS]: 策略参数 - num_stocks={num_stocks}, rank_boost={rank_boost}, lot_size={lot_size}, buffer={target_percent_buffer}")

        # --- 计算预测卖出 ---
        print("DEBUG [GET_ACTIONS]: --- 开始计算预测卖出 ---")
        codes_to_sell_predict = []
        sell_reasons_predict = {}
        for code, pos in positions_at_end.items():
             reason = None
             if code not in valid_codes_set: reason = "不符合最新选股规则"
             elif (pos.get('buy_rank') is not None or pos.get('lowest_rank') is not None) and code in current_rank_dict:
                 try:
                    current_rank = current_rank_dict[code]
                    base_rank_key = 'lowest_rank' if 'lowest_rank' in pos and pos['lowest_rank'] is not None else 'buy_rank'
                    base_rank = pos.get(base_rank_key)
                    if base_rank is not None:
                         base_rank = int(base_rank)
                         rank_deterioration = int(current_rank) - base_rank
                         if rank_deterioration >= rank_boost:
                               reason = f"排名较基准({base_rank_key}:{base_rank})恶化{rank_deterioration} >= 阈值({rank_boost})"
                 except Exception as e_sell_rank: print(f"DEBUG [GET_ACTIONS]: 计算卖出排名恶化时出错 for {code}: {e_sell_rank}")
             if reason:
                 codes_to_sell_predict.append(code); sell_reasons_predict[code] = reason
                 print(f"DEBUG [GET_ACTIONS]:  标记卖出 {code}, 原因: {reason}")
        # 填充 predicted_sells_local 列表
        for code_sell in set(codes_to_sell_predict):
            if code_sell in positions_at_end and positions_at_end[code_sell].get('shares', 0) > 0:
                 shares_to_sell = positions_at_end[code_sell]['shares']
                 predicted_sells_local.append({'code': code_sell,'shares': shares_to_sell,'reason': sell_reasons_predict.get(code_sell, 'N/A')})
        logs.append(f"计算完成: 预测卖出 {len(predicted_sells_local)} 只")
        print(f"DEBUG [GET_ACTIONS]: 最终预测卖出列表 (predicted_sells_local): {predicted_sells_local}")

        # --- 计算预测买入 ---
        print("DEBUG [GET_ACTIONS]: --- 开始计算预测买入 ---")
        holdings_after_predicted_sell_codes = set(positions_at_end.keys()) - set(codes_to_sell_predict)
        num_to_buy = num_stocks - len(holdings_after_predicted_sell_codes)
        logs.append(f"计算完成: 预测买入 {num_to_buy} 只")
        print(f"DEBUG [GET_ACTIONS]: 目标持股数={num_stocks}, 卖出后剩余代码数={len(holdings_after_predicted_sell_codes)}, 需买入数={num_to_buy}")

        if num_to_buy > 0:
            available_buy_df = valid_df_lastday[
                ~valid_df_lastday['stock_code'].isin(holdings_after_predicted_sell_codes)
            ].head(num_to_buy)
            print(f"DEBUG [GET_ACTIONS]: 从决策池筛选出可用买入标的数量 len(available_buy_df) = {len(available_buy_df)}")

            if not available_buy_df.empty:
                 estimated_cash_from_sells = sum(
                     sell_order['shares'] * float(latest_price_map.get(sell_order['code'], 0))
                     for sell_order in predicted_sells_local # 使用局部变量
                     if latest_price_map.get(sell_order['code']) is not None and pd.notna(latest_price_map.get(sell_order['code']))
                 )
                 available_cash_for_buys = current_cash_at_end + estimated_cash_from_sells
                 print(f"DEBUG [GET_ACTIONS]: 模拟结束时现金 current_cash_at_end = {current_cash_at_end:,.2f}")
                 print(f"DEBUG [GET_ACTIONS]: 预估卖出回收现金 estimated_cash_from_sells = {estimated_cash_from_sells:,.2f}")
                 print(f"DEBUG [GET_ACTIONS]: 预估可用于买入的总现金 available_cash_for_buys = {available_cash_for_buys:,.2f}")

                 if available_cash_for_buys > 0 and num_stocks > 0:
                     capital_per_stock = (available_cash_for_buys * target_percent_buffer) / num_stocks
                     print(f"DEBUG [GET_ACTIONS]: 平均每股分配资金 capital_per_stock = {capital_per_stock:,.2f}")
                     cash_used_for_buys_est = 0
                     print("DEBUG [GET_ACTIONS]: --- 开始遍历可用买入标的 ---")
                     for i, row in available_buy_df.iterrows():
                          code_buy = row['stock_code']
                          latest_price = latest_price_map.get(code_buy)
                          rank_buy = row['rank']
                          print(f"DEBUG [GET_ACTIONS]:  候选 {i+1}: {code_buy}, 最新价 price={latest_price}, 排名 rank={rank_buy}")

                          if latest_price and pd.notna(latest_price):
                               try:
                                   latest_price_f = float(latest_price)
                                   if latest_price_f > 0 and capital_per_stock > 0:
                                       target_shares_raw = capital_per_stock / latest_price_f
                                       shares_to_buy = math.floor(target_shares_raw / lot_size) * lot_size
                                       print(f"DEBUG [GET_ACTIONS]:   计算得到 shares_to_buy = {shares_to_buy}")
                                       if shares_to_buy > 0:
                                            cost_of_buy_est = shares_to_buy * latest_price_f
                                            cash_remaining_before_this_buy = available_cash_for_buys - cash_used_for_buys_est
                                            cash_check = cost_of_buy_est <= cash_remaining_before_this_buy
                                            print(f"DEBUG [GET_ACTIONS]:   预估成本 cost_est={cost_of_buy_est:.2f}, 需现金 <= 剩余现金 {cash_remaining_before_this_buy:.2f}? {cash_check}")
                                            if cash_check:
                                                 predicted_buys_local.append({'code': code_buy,'shares': shares_to_buy,'estimated_price': latest_price_f,'estimated_cost': cost_of_buy_est,'rank': rank_buy})
                                                 cash_used_for_buys_est += cost_of_buy_est
                                                 print(f"DEBUG [GET_ACTIONS]:    >> 加入买入列表。累计预估花费: {cash_used_for_buys_est:.2f}")
                                            else:
                                                 print("DEBUG [GET_ACTIONS]:    现金不足，停止本次及后续买入尝试。")
                                                 break
                                       else: print(f"DEBUG [GET_ACTIONS]:   目标股数计算为0，跳过。")
                                   else: print(f"DEBUG [GET_ACTIONS]:   价格无效 ({latest_price_f}) 或目标资金为0，跳过。")
                               except (ValueError, TypeError) as e_price: print(f"DEBUG [GET_ACTIONS]:   价格转换错误 for {code_buy} (price={latest_price}): {e_price}，跳过。")
                          else: print(f"DEBUG [GET_ACTIONS]:   无效价格 (price={latest_price})，跳过。")
                     print("DEBUG [GET_ACTIONS]: --- 遍历买入标的结束 ---")
                 else: print("DEBUG [GET_ACTIONS]: 预估可买入现金 <= 0 或目标持股数 <= 0，无法买入。")
            else: print("DEBUG [GET_ACTIONS]: 没有可供买入的股票（从决策池筛选后为空）。")
        else: print(f"DEBUG [GET_ACTIONS]: 无需买入 (num_to_buy = {num_to_buy})。")

        logs.append("最终目标交易动作计算完毕。")
        print(f"DEBUG [GET_ACTIONS]: 最终预测买入列表 (predicted_buys_local): {predicted_buys_local}")
        print("DEBUG [GET_ACTIONS]: --- 计算最终目标交易动作结束 ---")

        # --- 存储计算结果 ---
        self.final_predicted_sells = predicted_sells_local
        self.final_predicted_buys = predicted_buys_local
        print(f"DEBUG [Generate/Display]: 最终建议已存储到 self - Sells={len(self.final_predicted_sells)}, Buys={len(self.final_predicted_buys)}")

        # --- 构建目标持仓清单用于显示 ---
        print("DEBUG [Generate/Display]: --- 开始构建目标持仓清单用于显示 ---")
        target_portfolio = {} # {code: {'shares': S, 'name': N, 'price': P}}
        codes_to_be_sold = set(s['code'] for s in predicted_sells_local)
        # 1. 保留未卖出的旧持仓
        for code, pos_data in positions_at_end.items():
            if code not in codes_to_be_sold:
                shares_held = pos_data.get('shares', 0)
                if shares_held > 0:
                    target_portfolio[code] = {'shares': shares_held, 'name': name_map.get(code, 'N/A'), 'price': latest_price_map.get(code)}
                    print(f"  保留: {code} {shares_held}股")
        # 2. 添加新买入的持仓
        for buy_order in predicted_buys_local:
            shares_to_buy = buy_order.get('shares', 0)
            if shares_to_buy > 0:
                target_portfolio[buy_order['code']] = {'shares': shares_to_buy, 'name': name_map.get(buy_order['code'], 'N/A'), 'price': buy_order.get('estimated_price')}
                print(f"  新增/更新为: {buy_order['code']} {shares_to_buy}股")

        # --- 格式化输出 ---
        total_estimated_target_cost = 0
        output_lines = logs # 可以选择是否在最终弹窗中包含这么多 DEBUG 日志
        # 或者重新开始:
        output_lines = ["--- ========== 目标持仓清单 (模拟次日初始化) ========== ---"] # 清爽的输出

        if not target_portfolio:
            output_lines.append("目标持仓为空。")
        else:
            output_lines.append(f"目标持仓 {len(target_portfolio)} 只:")
            sorted_target_codes = sorted(target_portfolio.keys())
            for code in sorted_target_codes:
                item = target_portfolio[code]
                shares = item.get('shares', 0)
                name = item.get('name', 'N/A')
                price = item.get('price')
                cost_item = 0; price_str = "N/A"
                if price is not None and pd.notna(price) and shares > 0:
                    try:
                        price = float(price); cost_item = shares * price; total_estimated_target_cost += cost_item; price_str = f"{price:.2f}"
                    except Exception: price_str = f"无效价格({price})"
                output_lines.append(f"  - {code} ({name}): {shares:,} 股 (估价: {price_str})")
            output_lines.append(f"\n构建此目标持仓所需预估总市值: {total_estimated_target_cost:,.2f}")

        output_lines.append("--- ================================================== ---")
        output_lines.append(f"\n回测结束时现金: {current_cash_at_end:,.2f}")
        output_lines.append("\n注意：此清单代表模拟下一日的理想持仓。价格和总市值为基于回测最后一天的收盘价估算。")

        # --- 调用显示对话框 ---
        print(f"DEBUG [Generate/Display]: 即将显示最终目标清单对话框。")
        self._show_prediction_dialog("\n".join(output_lines))
        # 函数结束


# 4. Add Helper `_show_prediction_dialog` (if not already present)
    def _show_prediction_dialog(self, content):
        """Helper function to display text content in a QDialog."""
        result_dialog = QDialog(self)
        result_dialog.setWindowTitle("交易预测/目标清单") # Generic Title
        layout = QVBoxLayout(result_dialog)
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setText(content)
        font = QFont("Courier New", 10) # Use a monospaced font
        text_edit.setFont(font)
        layout.addWidget(text_edit)
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        button_box.accepted.connect(result_dialog.accept)
        layout.addWidget(button_box)
        result_dialog.resize(700, 500) # Adjust size as needed
        result_dialog.exec()

# 在 SimulatorAppUI 类中，替换现有的 _get_target_portfolio_actions 方法
# (或者如果你把这个逻辑合并在 _generate_and_display_target_portfolio 里，
#  就需要把这些 print 添加到那个函数的相应计算位置)



    def _display_final_pending_orders(self):
        """
        在模拟结束后，格式化并显示最后一天生成的待处理订单（即下一日预测）。
        """
        logs = ["--- ========== 最终交易预测 (来自回测最后一日决策) ========== ---"]
        if not hasattr(self, 'hist_sim_state'):
            logs.append("错误: 历史模拟状态 (hist_sim_state) 未找到。")
            # (可以选择在这里就显示错误消息并返回)
            # QMessageBox.warning(self, "预测错误", "\n".join(logs))
            # return
        elif 'pending_orders' not in self.hist_sim_state or 'last_day_closes_map' not in self.hist_sim_state:
            logs.append("错误: 模拟状态中缺少 'pending_orders' 或 'last_day_closes_map'。")
        # 确保 self.name_map 存在
        elif not hasattr(self, 'name_map'):
            logs.append("错误: 缺少股票名称映射 (self.name_map)。")

        pending_orders = self.hist_sim_state.get('pending_orders', [])
        last_closes = self.hist_sim_state.get('last_day_closes_map', {})
        name_map = getattr(self, 'name_map', {}) # 使用 getattr 获取，避免属性不存在错误

        if not pending_orders:
            logs.append("\n无待执行订单（无建议操作）。")
        else:
            total_estimated_buy_cost = 0
            sell_logs = []
            buy_logs = []

            for order in pending_orders:
                code = order.get('code')
                shares = order.get('shares', 0)
                name = name_map.get(code, 'N/A') # 从 self.name_map 获取

                if not code or shares <= 0: continue # 跳过无效订单

                if order.get('action') == 'sell':
                    reason = order.get('reason', 'N/A')
                    sell_logs.append(f"  - 股票: {code} ({name}), 数量: {shares:,} 股 (原因: {reason})")
                elif order.get('action') == 'buy':
                    rank = order.get('buy_rank', 'N/A')
                    # 从 state 中获取最后一日的收盘价用于估算
                    estimated_price = last_closes.get(code)
                    estimated_cost = 0
                    price_str = "N/A"
                    cost_str = "无法估算 (无价格)"

                    if estimated_price is not None and pd.notna(estimated_price):
                        try:
                            estimated_price = float(estimated_price)
                            if estimated_price > 0: # 价格需有效
                                estimated_cost = shares * estimated_price
                                total_estimated_buy_cost += estimated_cost
                                price_str = f"{estimated_price:.2f}"
                                cost_str = f"{estimated_cost:,.2f}"
                        except (ValueError, TypeError):
                            price_str = f"无效价格({estimated_price})" # 记录原始值
                    # else: 价格为 None 或 NaN

                    buy_logs.append(f"  - 股票: {code} ({name}), 数量: {shares:,} 股")
                    buy_logs.append(f"      (预估价: {price_str}, 预估成本: {cost_str}, 买入排名: {rank})")

            # 汇总输出
            if sell_logs:
                logs.append("\n【预测卖出】:")
                logs.extend(sell_logs)
            else:
                logs.append("\n【预测卖出】: 无")

            if buy_logs:
                logs.append("\n【预测买入】:")
                logs.extend(buy_logs)
                logs.append(f"\n买入操作预估总成本: {total_estimated_buy_cost:,.2f}")
            else:
                logs.append("\n【预测买入】: 无")

        logs.append("--- ================================================== ---")
        logs.append("\n注意：此预测基于回测最后一日的决策生成。买入价格和成本为预估。")

        # 使用 QDialog 显示结果 (保持不变)
        result_dialog = QDialog(self)
        result_dialog.setWindowTitle("最终交易预测 (来自回测)")
        layout = QVBoxLayout(result_dialog)
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setText("\n".join(logs))
        font = QFont("Courier New", 10) # 等宽字体
        text_edit.setFont(font)
        layout.addWidget(text_edit)
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        button_box.accepted.connect(result_dialog.accept)
        layout.addWidget(button_box)
        result_dialog.resize(700, 500) # 调整大小
        result_dialog.exec()
    @Slot()
    def _hist_refresh_display(self):
        """刷新历史模拟的表格和日志显示，包含最低排名。"""
        # --- 检查状态 ---
        if not hasattr(self, 'hist_sim_state') or not hasattr(self, 'hist_sim_dates') or not self.hist_sim_dates:
            self.hist_status_label.setText("历史模拟未开始或状态异常"); self.hist_positions_table.setRowCount(0); self.hist_log_display.setText(""); return

        # --- 获取当前应显示的日志索引 ---
        # 如果 idx=0 (刚开始)，显示空状态；否则显示 idx-1 的日志结果
        log_idx_to_display = self.hist_sim_idx - 1
        if log_idx_to_display < 0: # 初始状态
             current_date_str = self.hist_sim_dates[0].strftime('%Y-%m-%d')
             cash_val = self.hist_sim_state.get('cash', 0)
             self.hist_status_label.setText(f"历史模拟已加载，准备开始于: {current_date_str} | 现金: {cash_val:,.2f}")
             self.hist_positions_table.setRowCount(0); self.hist_log_display.setText('点击"前进一天"开始模拟。'); return

        logs = self.hist_sim_state['logs']
        if log_idx_to_display >= len(logs): # 防止索引越界 (可能发生在最后一天)
             log_idx_to_display = len(logs) - 1
             if log_idx_to_display < 0: # 日志为空
                  self.hist_status_label.setText("历史模拟日志为空"); self.hist_positions_table.setRowCount(0); self.hist_log_display.setText(""); return

        log = logs[log_idx_to_display]

        # --- 更新状态栏和日志 ---
        cash = log.get('cash', 0); market_value = log.get('market_value', 0); total_assets = log.get('total_assets', 0); date_str = log.get('date', '')
        self.hist_status_label.setText(f"历史模拟日期: {date_str} | 现金: {cash:,.2f} | 市值: {market_value:,.2f} | 总资产: {total_assets:,.2f}")
        self.hist_log_display.setText(log.get('log', ''))
        if hasattr(self.hist_log_display, 'verticalScrollBar'): QTimer.singleShot(0, lambda: self.hist_log_display.verticalScrollBar().setValue(self.hist_log_display.verticalScrollBar().maximum()))

        # --- 更新持仓表格 ---
        positions = log.get('positions_snapshot', [])
        print(f"[{date_str}] 刷新显示持仓: {len(positions)} 条记录")

        # --- !!! 修改: 设置表头和列数 (13列) !!! ---
        headers = ["代码", "名称", "股数", "成本价", "开盘价", "收盘价", "资产", "市值(亿)", "天数", "盈亏率", "买入排名", "当前", "最低"]# 设置每列的宽度
        column_widths = [60, 70, 60, 70, 70, 70, 70, 70, 70, 70, 70, 50, 50]  # 每列宽度值
        for col, width in enumerate(column_widths):
         self.hist_positions_table.setColumnWidth(col, width)        

        self.hist_positions_table.clearContents()
        self.hist_positions_table.setColumnCount(len(headers))
        self.hist_positions_table.setHorizontalHeaderLabels(headers)
        self.hist_positions_table.setRowCount(len(positions))

        # --- 填充数据 ---
        for row, pos in enumerate(positions):
            # 准备数据
            code = str(pos.get('code', '')); name = str(pos.get('name', ''))
            shares = pos.get('shares', 0); cost = pos.get('cost', 0.0)
            open_price = pos.get('open'); close_price = pos.get('close')
            pos_market_value = pos.get('market_value_pos', 0.0)
            company_market_cap_billion = pos.get('company_market_cap_billion')
            days_held = pos.get('days_held', 0); pnl_pct = pos.get('pnl_pct', 0.0)
            buy_rank = pos.get('buy_rank', 'N/A'); current_rank = pos.get('current_rank', 'N/A')
            # --- !!! 获取最低排名 !!! ---
            lowest_rank = pos.get('lowest_rank', 'N/A')

            # 创建 Items
            codeItem = QTableWidgetItem(code); nameItem = QTableWidgetItem(name)
            sharesItem = QTableWidgetItem(f"{shares:,}"); costItem = QTableWidgetItem(f"{cost:.2f}")
            openItem = QTableWidgetItem(f"{open_price:.2f}" if open_price is not None else "N/A")
            closeItem = QTableWidgetItem(f"{close_price:.2f}" if close_price is not None else "N/A")
            marketValueItem = QTableWidgetItem(f"{pos_market_value:,.2f}")
            companyMarketCapItem = QTableWidgetItem(f"{company_market_cap_billion:.2f}" if company_market_cap_billion is not None else "N/A")
            daysHeldItem = QTableWidgetItem(str(days_held))
            pnlPctItem = QTableWidgetItem(f"{pnl_pct:.2f}%")
            buyRankItem = QTableWidgetItem(str(buy_rank)); currentRankItem = QTableWidgetItem(str(current_rank))
            # --- !!! 创建最低排名 Item !!! ---
            lowestRankItem = QTableWidgetItem(str(lowest_rank))

            # 设置对齐
            nameItem.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            sharesItem.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter); costItem.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            openItem.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter); closeItem.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            marketValueItem.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter); companyMarketCapItem.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            daysHeldItem.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter); pnlPctItem.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            buyRankItem.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter); currentRankItem.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            # --- !!! 设置最低排名对齐 !!! ---
            lowestRankItem.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            # 颜色
            color = QColor("black")
            # 检查 pnl_pct 是否为有效数值
            try:
                pnl_float = float(pnl_pct)
                if pnl_float > 0.01: color = QColor("red")
                elif pnl_float < -0.01: color = QColor("darkGreen")
            except (ValueError, TypeError):
                pass # 如果不是有效数值，保持黑色
            pnlPctItem.setForeground(QBrush(color)); closeItem.setForeground(QBrush(color))

            # 设置到表格
            self.hist_positions_table.setItem(row, 0, codeItem); self.hist_positions_table.setItem(row, 1, nameItem)
            self.hist_positions_table.setItem(row, 2, sharesItem); self.hist_positions_table.setItem(row, 3, costItem)
            self.hist_positions_table.setItem(row, 4, openItem); self.hist_positions_table.setItem(row, 5, closeItem)
            self.hist_positions_table.setItem(row, 6, marketValueItem); self.hist_positions_table.setItem(row, 7, companyMarketCapItem)
            self.hist_positions_table.setItem(row, 8, daysHeldItem); self.hist_positions_table.setItem(row, 9, pnlPctItem)
            self.hist_positions_table.setItem(row, 10, buyRankItem); self.hist_positions_table.setItem(row, 11, currentRankItem)
            # --- !!! 设置最低排名 Item !!! ---
            self.hist_positions_table.setItem(row, 12, lowestRankItem)

        # --- 调整列宽和刷新 ---
        self.hist_positions_table.resizeColumnsToContents()
        QApplication.processEvents()
        print(f"[{date_str}] 界面刷新完成")

    @Slot(bool, str)
    def on_db_rebalance_finished(self, success, status_message):
        """Slot to handle the completion of the database rebalance worker."""
        print("数据库调仓线程完成信号接收。")
        final_log_line = status_message.splitlines()[-1] if status_message else "完成 (未知)"
        status_prefix = "状态: DB调仓 "
        if success:
            status_prefix += f"成功完成 - {final_log_line}"
        else:
            status_prefix += f"执行中遇到问题 - {final_log_line}"

        self.status_label.setText(status_prefix)
        self.log_display.setText(final_log_line) # Show last line in short log display

        # Display full log in message box? Or rely on console? For now, just update status.
        # Optional: Show full log
        # result_dialog = QDialog(self)
        # ... setup dialog with status_message ...
        # result_dialog.exec()

        self._refresh_all_displays() # Refresh UI to show new DB state

        # Clean up worker/thread references
        self.worker_thread = None
        self.db_rebalance_worker = None # Use the correct worker name
        print("  - DB Rebalance Worker/Thread UI引用已清除")

        # Re-enable buttons
        self.rebalance_button.setEnabled(True)
        self.add_funds_button.setEnabled(True)
        self.refresh_display_button.setEnabled(True)
        print("on_db_rebalance_finished 槽函数执行完毕。")

    # Add the helper function load_full_positions if you don't have it
    # Make sure load_full_positions retrieves 'cost_price' needed by worker


    def _plot_comparison_chart(self):
                """
                (V-Corrected - 统一基准归一化)
                从数据库提取策略和微盘指数数据，使用统一基准进行归一化，并用Matplotlib绘制对比图。
                这个版本修正了移动平均线因独立归一化导致的视觉比例失真问题。
                """
                print("开始执行 _plot_comparison_chart (V-Corrected - 统一基准归一化)")

                # 1. 从模拟状态中提取策略资产数据
                try:
                    strategy_data = pd.DataFrame(self.hist_sim_state['logs'])[['date', 'total_assets']]
                    strategy_data['date'] = pd.to_datetime(strategy_data['date'])
                    strategy_data = strategy_data.set_index('date').sort_index()
                    strategy_data['total_assets'] = pd.to_numeric(strategy_data['total_assets'], errors='coerce')
                    strategy_data = strategy_data.dropna(subset=['total_assets'])
                    # 过滤掉资产为0或负的异常点
                    strategy_data = strategy_data[strategy_data['total_assets'] > 0]
                except Exception as e:
                    QMessageBox.critical(self, "数据错误", f"提取策略数据时出错: {e}")
                    return

                if strategy_data.empty:
                    QMessageBox.warning(self, "绘图错误", "无法提取有效的策略资产数据。")
                    return

                # 获取回测的起止日期
                start_date_dt = strategy_data.index.min()
                end_date_dt = strategy_data.index.max()
                start_date_str = start_date_dt.strftime('%Y-%m-%d')
                end_date_str = end_date_dt.strftime('%Y-%m-%d')

                # 2. 从数据库的 micro_cap_index 表中提取微盘指数及均线数据
                conn = None
                micro_cap_df = pd.DataFrame()
                try:
                    conn = connect_db(DB_PATH)
                    if conn:
                        query = """
                            SELECT date, index_value, ma20, ma5
                            FROM micro_cap_index
                            WHERE date >= ? AND date <= ?
                            ORDER BY date
                        """
                        micro_cap_df = pd.read_sql(query, conn, params=(start_date_str, end_date_str))
                        if not micro_cap_df.empty:
                            micro_cap_df['date'] = pd.to_datetime(micro_cap_df['date'])
                            micro_cap_df = micro_cap_df.set_index('date').sort_index()
                            # 清理数据，确保为数值类型
                            for col in ['index_value', 'ma20', 'ma5']:
                                if col in micro_cap_df.columns:
                                    micro_cap_df[col] = pd.to_numeric(micro_cap_df[col], errors='coerce')
                        else:
                            QMessageBox.warning(self, "绘图错误", f"在 micro_cap_index 表的日期范围 {start_date_str} 到 {end_date_str} 内未找到数据。")
                    else:
                        QMessageBox.critical(self, "数据库错误", "无法连接数据库获取微盘指数数据。")
                        return
                except Exception as e:
                    QMessageBox.critical(self, "数据库错误", f"查询 micro_cap_index 表时出错: {e}")
                    return
                finally:
                    if conn:
                        try: conn.close()
                        except Exception: pass

                if micro_cap_df.empty or 'index_value' not in micro_cap_df.columns or micro_cap_df['index_value'].isnull().all():
                    QMessageBox.warning(self, "绘图错误", "无法获取有效的微盘指数数据 (index_value)。")
                    return

                # 3. 合并策略数据和指数数据
                combined_data = pd.merge(strategy_data, micro_cap_df, left_index=True, right_index=True, how='inner')
                if combined_data.empty:
                    QMessageBox.warning(self, "绘图错误", "策略数据和微盘指数数据在此期间没有共同的交易日期。")
                    return

                # 4. **核心修正：执行正确的统一基准归一化**
                print("开始使用统一基准进行数据归一化...")
                try:
                    first_valid_index = combined_data.index[0]

                    # a. 归一化策略净值
                    first_strategy_value = combined_data.loc[first_valid_index, 'total_assets']
                    combined_data['strategy_normalized'] = (combined_data['total_assets'] / first_strategy_value) * 100

                    # b. 获取指数的统一基准值
                    first_micro_cap_value = combined_data.loc[first_valid_index, 'index_value']
                    
                    # c. 使用【同一个】基准值归一化指数、5日线和20日线
                    if first_micro_cap_value > 0:
                        combined_data['micro_cap_normalized'] = (combined_data['index_value'] / first_micro_cap_value) * 100
                        if 'ma5' in combined_data.columns:
                            combined_data['ma5_normalized'] = (combined_data['ma5'] / first_micro_cap_value) * 100
                        if 'ma20' in combined_data.columns:
                            combined_data['ma20_normalized'] = (combined_data['ma20'] / first_micro_cap_value) * 100
                        print("数据归一化完成。")
                    else:
                        QMessageBox.warning(self, "归一化错误", "指数的起始基准值为0，无法进行归一化。")
                        return

                except Exception as e:
                    QMessageBox.critical(self, "计算错误", f"归一化数据时出错: {e}")
                    traceback.print_exc()
                    return

                # 5. 绘图
                print("开始绘图...")
                try:
                    plt.style.use('seaborn-v0_8-darkgrid')
                    try:
                        plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS']
                        plt.rcParams['axes.unicode_minus'] = False
                    except Exception as font_e:
                        print(f"设置中文字体时出现警告/错误: {font_e}")

                    fig, ax = plt.subplots(figsize=(20, 8))

                    # 绘制背景填充区域
                    # 条件：归一化后的指数值 < 归一化后的均线值
                    if 'micro_cap_normalized' in combined_data.columns and 'ma20_normalized' in combined_data.columns:
                        ax.fill_between(combined_data.index,
                                        combined_data['micro_cap_normalized'], combined_data['ma20_normalized'],
                                        where=(combined_data['micro_cap_normalized'] < combined_data['ma20_normalized']),
                                        facecolor='lightgreen', alpha=0.3, label='指数 < 20日线')
                    if 'micro_cap_normalized' in combined_data.columns and 'ma5_normalized' in combined_data.columns:
                        ax.fill_between(combined_data.index,
                                        combined_data['micro_cap_normalized'], combined_data['ma5_normalized'],
                                        where=(combined_data['micro_cap_normalized'] < combined_data['ma5_normalized']),
                                        facecolor='khaki', alpha=0.5, label='指数 < 5日线')

                    # 绘制曲线
                    ax.plot(combined_data.index, combined_data['strategy_normalized'], label='策略净值', linewidth=2, color='red', zorder=5)
                    ax.plot(combined_data.index, combined_data['micro_cap_normalized'], label='微盘指数归一化净值', linewidth=1.5, linestyle='--', color='blue', zorder=4)

                    if 'ma20_normalized' in combined_data.columns and combined_data['ma20_normalized'].notna().any():
                        ax.plot(combined_data.index, combined_data['ma20_normalized'], label='微盘指数20日线归一化', linewidth=1, linestyle=':', color='green', zorder=3)

                    if 'ma5_normalized' in combined_data.columns and combined_data['ma5_normalized'].notna().any():
                        ax.plot(combined_data.index, combined_data['ma5_normalized'], label='微盘指数5日线归一化', linewidth=1, linestyle='-.', color='orange', zorder=2)

                    # 设置图表标题和标签
                    ax.set_title(f'策略表现 vs 微盘指数 ({start_date_str} to {end_date_str})', fontsize=16)
                    ax.set_xlabel('日期', fontsize=12)
                    ax.set_ylabel('归一化净值 (起始点=100)', fontsize=12)
                    
                    # 获取并显示去重后的图例
                    handles, labels = ax.get_legend_handles_labels()
                    by_label = dict(zip(labels, handles))
                    ax.legend(by_label.values(), by_label.keys(), fontsize=10)

                    ax.grid(True, linestyle=':', linewidth=0.6)
                    fig.autofmt_xdate()
                    plt.tight_layout()
                    print("绘图完成，准备显示...")
                    plt.show()

                except Exception as e:
                    QMessageBox.critical(self, "绘图错误", f"使用 Matplotlib 生成图表时出错: {e}")
                    traceback.print_exc()


# --- (保留 Main Execution Block 和 ensure_positions_table_has_buy_rank) ---
    def _hist_toggle_auto(self):
        if getattr(self, 'hist_sim_running', False):
            # 停止自动模拟：只需设置标志位
            self.hist_sim_running = False
            self.hist_auto_btn.setText("自动演变")
            print("停止自动模拟")
        else:
            # 启动自动模拟：设置标志位并手动触发第一步
            # 确保模拟还没结束
            if hasattr(self, 'hist_sim_dates') and self.hist_sim_idx < len(self.hist_sim_dates):
                self.hist_sim_running = True
                self.hist_auto_btn.setText("暂停自动")
                print("启动自动模拟，开始处理第一天...")
                # 直接调用一次 _hist_next_day 来启动链式反应
                self._hist_next_day()
            else:
                print("无法启动自动模拟：模拟已结束或未初始化。")
                self.hist_auto_btn.setText("自动演变") # 保持按钮文本一致

    def _export_trade_records(self):
        """导出所有交易记录到Excel文件。"""
        print("开始导出交易记录...")
        
        if not hasattr(self, 'hist_sim_state'):
            print("错误：hist_sim_state 不存在")
            QMessageBox.warning(self, "导出错误", "没有可导出的交易记录。")
            return
            
        trade_records = self.hist_sim_state.get('trade_records', [])
        print(f"找到 {len(trade_records)} 条交易记录")
        
        if not trade_records:
            print("错误：trade_records 为空")
            QMessageBox.warning(self, "导出错误", "没有找到可导出的交易记录。")
            return

        try:
            print("开始处理交易数据...")
            # 创建DataFrame
            df = pd.DataFrame(trade_records)
            print(f"创建DataFrame成功，共 {len(df)} 行")
            
            # 计算每只股票的累计盈亏
            sell_records = df[df['操作'] == '卖出']
            print(f"卖出记录数量: {len(sell_records)}")
            
            summary_df = sell_records.groupby(['股票代码', '股票名称']).agg({
                '盈亏金额': 'sum',
                '交易日期': 'count',
                '成交金额': 'sum'
            }).reset_index()
            
            summary_df.columns = ['股票代码', '股票名称', '累计盈亏', '交易次数', '累计交易金额']
            summary_df['收益率'] = summary_df['累计盈亏'] / summary_df['累计交易金额'] * 100
            print(f"汇总数据处理完成，共 {len(summary_df)} 只股票")
            
            # 获取桌面路径作为默认保存位置
            desktop_path = os.path.join(os.path.expanduser("~"), "Desktop")
            default_filename = os.path.join(desktop_path, f"交易记录_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")
            print(f"默认保存路径: {default_filename}")
            
            filename, _ = QFileDialog.getSaveFileName(
                self, 
                "保存交易记录", 
                default_filename,
                "Excel文件 (*.xlsx)"
            )
            
            if filename:
                print(f"用户选择的保存路径: {filename}")
                with pd.ExcelWriter(filename) as writer:
                    print("正在写入详细交易记录...")
                    df.to_excel(writer, sheet_name='详细交易记录', index=False)
                    print("正在写入盈亏汇总...")
                    summary_df.to_excel(writer, sheet_name='股票盈亏汇总', index=False)
                
                print("文件保存完成")
                QMessageBox.information(self, "导出成功", f"交易记录已导出到:\n{filename}")
            else:
                print("用户取消了保存操作")
                
        except Exception as e:
            error_msg = f"导出交易记录时出错:\n{str(e)}"
            print(error_msg)
            traceback.print_exc()
            QMessageBox.critical(self, "导出错误", error_msg)

# --- !! 替换这个类 !! ---
class ExecuteDbRebalanceWorker(QObject):
    finished = Signal(bool, str) # Signal: success(bool), message(str)
    log_message = Signal(str)

    # --- 修改：添加 execution_date 参数 ---
    def __init__(self, db_path, actions_sell, full_sell_details, actions_buy, latest_prices, target_percent_buffer, execution_date):
        super().__init__()
        self.db_path = db_path
        self.actions_sell = actions_sell # {code: shares_to_sell}
        self.full_sell_details = full_sell_details # {code: {'cost_price': P}} for PnL
        self.actions_buy = actions_buy   # {code: {'shares': S, 'rank': R}}
        self.latest_prices = latest_prices # {code: price} Execution prices
        self.target_percent_buffer = target_percent_buffer
        self.execution_date = execution_date # 存储执行日期 (YYYY-MM-DD string)

    @Slot()
    def run(self):
        conn = None
        # --- 修改：在日志中加入执行日期 ---
        status_msgs = [f"--- 开始执行数据库调仓 (目标日期: {self.execution_date}) ---"]
        success = True
        thread_id = threading.current_thread().name
        print(f"[{thread_id}] DB Rebalance Worker started (Exec Date: {self.execution_date}).")
        self.log_message.emit(f"[{thread_id}] DB调仓线程启动...")

        try:
            self.log_message.emit(f"[{thread_id}] 连接数据库..."); conn = connect_db(self.db_path)
            if not conn: raise ConnectionError("无法连接数据库")
            self.log_message.emit(f"[{thread_id}] 连接成功.")

            # --- 1. Load initial cash ---
            current_cash = load_cash(conn)
            status_msgs.append(f"初始现金: {current_cash:,.2f}")

            # --- 2. Execute Sells ---
            self.log_message.emit(f"[{thread_id}] 执行卖出...")
            cash_from_sells = 0.0
            # --- 修改：使用 self.execution_date ---
            exec_date_str_db = self.execution_date
            # --- 结束修改 ---

            for code, shares_to_sell in self.actions_sell.items():
                self.log_message.emit(f"[{thread_id}] 准备卖出 {code} ({shares_to_sell}股)...")
                sell_price = self.latest_prices.get(code)
                cost_price = self.full_sell_details.get(code, {}).get('cost_price')

                if sell_price is None: # 价格在 run_rebalance 已检查，这里只检查 None
                    msg = f"卖出 {code} 失败: 内部错误 - 价格丢失"
                    status_msgs.append(msg); success = False; print(msg)
                    continue

                # --- 修改：传递 exec_date_str_db ---
                # 假设总是全卖（因为是基于状态差异计算的）
                # 注意：如果 actions_sell 包含部分卖出，is_full_sell 需要相应调整
                is_full_sell_flag = True # 假设总是完全卖出或减仓到0
                # 如果需要处理部分卖出，需要从 current_db_holdings 比较
                # if code in current_db_holdings and current_db_holdings[code]['shares'] > shares_to_sell:
                #    is_full_sell_flag = False # 这是部分卖出

                sell_ok, log_msg = execute_sell_db(conn, code, sell_price, shares_to_sell, exec_date_str_db, is_full_sell_flag, cost_price)
                status_msgs.append(log_msg)
                if sell_ok:
                    cash_from_sells += shares_to_sell * sell_price
                    print(f"[{thread_id}] DB Sell OK: {code}")
                else:
                    success = False
                    print(f"[{thread_id}] DB Sell FAIL: {code} - {log_msg}")
                    # continue # 决定是否继续

            updated_cash = current_cash + cash_from_sells
            status_msgs.append(f"卖出后现金: {updated_cash:,.2f}")

            # --- 3. Execute Buys ---
            self.log_message.emit(f"[{thread_id}] 执行买入...")
            cash_used_for_buys = 0.0

            # 重新加载数据库持仓以计算目标数量（可选但更准确）
            db_positions_after_sell = {}
            try:
                 db_positions_after_sell = load_full_positions(conn) # 确保函数存在
            except NameError: print("警告: load_full_positions 未定义，无法精确计算目标持股数")
            except Exception as e_load: print(f"警告: 加载卖出后持仓失败: {e}")

            num_current_holdings = len(db_positions_after_sell)
            num_target_holdings = num_current_holdings + len(self.actions_buy) # 买入后目标持股数

            capital_available = updated_cash * self.target_percent_buffer
            capital_per_stock = capital_available / num_target_holdings if num_target_holdings > 0 else 0
            status_msgs.append(f"用于买入的总资金上限: {capital_available:,.2f}")
            status_msgs.append(f"预估每只分配资金: {capital_per_stock:,.2f}")

            failed_buys_count = 0
            for code, buy_info in self.actions_buy.items():
                self.log_message.emit(f"[{thread_id}] 准备买入 {code}...")
                buy_price = self.latest_prices.get(code)
                shares_to_buy = buy_info['shares'] # 使用计算好的股数
                buy_rank = buy_info.get('rank')

                if buy_price is None: # 同样，只检查 None
                     msg = f"买入 {code} 失败: 内部错误 - 价格丢失"
                     status_msgs.append(msg); failed_buys_count += 1; print(msg)
                     continue

                if shares_to_buy <= 0: # 再次检查以防万一
                    msg = f"买入 {code} 失败: 目标股数计算为0"
                    status_msgs.append(msg); failed_buys_count += 1; print(msg)
                    continue

                cost_of_buy = shares_to_buy * buy_price

                if cost_of_buy <= (updated_cash - cash_used_for_buys):
                    # --- 修改：传递 exec_date_str_db ---
                    buy_ok, log_msg, _, _ = execute_buy_db(conn, code, buy_price, shares_to_buy, exec_date_str_db, buy_rank)
                    status_msgs.append(log_msg)
                    if buy_ok:
                        cash_used_for_buys += cost_of_buy
                        print(f"[{thread_id}] DB Buy OK: {code}")
                    else:
                        success = False; failed_buys_count += 1
                        print(f"[{thread_id}] DB Buy FAIL: {code} - {log_msg}")
                        # continue
                else:
                     msg = f"买入 {code} 失败: 现金不足 (需 {cost_of_buy:,.2f}, 可用 {updated_cash - cash_used_for_buys:,.2f})"
                     status_msgs.append(msg); failed_buys_count += 1; print(msg)
                     # 可以在这里停止后续买入，或者尝试减少购买股数（更复杂）
                     # break # 如果现金不足，停止后续买入

            final_cash = updated_cash - cash_used_for_buys
            status_msgs.append(f"买入后现金: {final_cash:,.2f}")
            if failed_buys_count > 0:
                status_msgs.append(f"警告: {failed_buys_count} 只股票未能成功买入。")

            # --- 4. Save Final Cash ---
            self.log_message.emit(f"[{thread_id}] 保存最终现金...")
            if not save_cash(conn, final_cash):
                status_msgs.append("严重警告: 保存最终现金失败!")
                success = False
                print(f"[{thread_id}] Save cash FAIL.")
            else:
                 print(f"[{thread_id}] Save cash OK.")

        except Exception as e:
            detailed_error = f"[{thread_id}] DB调仓线程错误: {e}\n{traceback.format_exc()}"
            status_msgs.append(f"错误: {e}")
            success = False
            print(detailed_error)
            # 可以考虑在这里尝试回滚事务（如果使用了事务）
        finally:
            if conn:
                try: conn.close(); print(f"[{thread_id}] DB调仓线程数据库连接关闭。")
                except Exception as e_close: print(f"[{thread_id}] 关闭DB连接失败: {e_close}")

            final_message = "\n".join(status_msgs)
            self.log_message.emit(f"[{thread_id}] DB调仓线程结束.")
            self.finished.emit(success, final_message) # 发送最终状态

# --- Main Execution Block ---
if __name__ == "__main__":
    app = QApplication(sys.argv)

    bright_stylesheet = """
    /* 全局样式 */
    QMainWindow, QDialog {
        background-color: #f0f0f5;
        color: #2c3e50;
    }

    /* 按钮样式 - 银色渐变 */
    QPushButton {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #E8E8E8, stop:1 #D1D1D1);
        color: #2c3e50;
        border: 1px solid #C0C0C0;
        padding: 8px 16px;
        border-radius: 6px;
     
        font-size: 14px;
    }

    QPushButton:hover {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #F5F5F5, stop:1 #E8E8E8);
    }

    QPushButton:pressed {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #D1D1D1, stop:1 #C0C0C0);
    }

    /* 表格样式 */
    QTableView {
        background-color: white;
        alternate-background-color: #f8f9fa;
        border: 2px solid #C0C0C0;
        border-radius: 8px;
        gridline-color: #D3D3D3;
    }

    QTableView::item {
        padding: 8px;
        border-bottom: 1px solid #E8E8E8;
    }

    QTableView::item:selected {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #E0E0E0, stop:1 #D1D1D1);
        color: #2c3e50;
    }

    QHeaderView::section {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #F5F5F5, stop:1 #E0E0E0);
        color: #2c3e50;
        padding: 8px;
        border: 1px solid #C0C0C0;
        font-weight: bold;
    }

    /* 输入框样式 */
    QLineEdit, QSpinBox, QDoubleSpinBox {
        background-color: white;
        border: 2px solid #C0C0C0;
        border-radius: 6px;
        padding: 8px;
        color: #2c3e50;
        font-size: 14px;
    }

    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
        border: 2px solid #A8A8A8;
    }

    /* 下拉框样式 */
    QComboBox {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #F5F5F5, stop:1 #E8E8E8);
        border: 2px solid #C0C0C0;
        border-radius: 6px;
        padding: 8px;
        color: #2c3e50;
        font-size: 14px;
    }

    QComboBox:hover {
        border: 2px solid #A8A8A8;
    }

    QComboBox::drop-down {
        border: none;
        width: 20px;
    }

    /* 标签样式 */
    QLabel {
        color: #2c3e50;
        font-size: 14px;
    }

    /* 菜单样式 */
    QMenuBar {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #F5F5F5, stop:1 #E0E0E0);
        color: #2c3e50;
        font-weight: bold;
        border-bottom: 1px solid #C0C0C0;
    }

    QMenuBar::item:selected {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #E0E0E0, stop:1 #D1D1D1);
    }

    QMenu {
        background-color: white;
        border: 1px solid #C0C0C0;
        border-radius: 6px;
    }

    QMenu::item {
        padding: 8px 20px;
    }

    QMenu::item:selected {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #E0E0E0, stop:1 #D1D1D1);
        color: #2c3e50;
    }

    /* 状态栏样式 */
    QStatusBar {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #F5F5F5, stop:1 #E0E0E0);
        color: #2c3e50;
        border-top: 1px solid #C0C0C0;
    }

    /* 标签页样式 */
    QTabWidget::pane {
        border: 2px solid #C0C0C0;
        border-radius: 8px;
        top: -2px;
    }

    QTabBar::tab {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #F5F5F5, stop:1 #E8E8E8);
        color: #2c3e50;
        padding: 10px 20px;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        margin-right: 2px;
        font-weight: bold;
        border: 1px solid #C0C0C0;
    }

    QTabBar::tab:selected {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #E0E0E0, stop:1 #D1D1D1);
        border-bottom: none;
    }

    /* 分组框样式 */
    QGroupBox {
        background-color: white;
        border: 2px solid #C0C0C0;
        border-radius: 8px;
        margin-top: 1em;
        padding-top: 1em;
        font-weight: bold;
    }

    QGroupBox::title {
        color: #2c3e50;
        subcontrol-origin: margin;
        subcontrol-position: top center;
        padding: 0 10px;
    }

    /* 滚动条样式 */
    QScrollBar:vertical {
        background-color: #F5F5F5;
        width: 12px;
        margin: 0px;
        border-radius: 6px;
    }

    QScrollBar::handle:vertical {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #E0E0E0, stop:1 #D1D1D1);
        min-height: 30px;
        border-radius: 6px;
    }

    QScrollBar:horizontal {
        background-color: #F5F5F5;
        height: 12px;
        margin: 0px;
        border-radius: 6px;
    }

    QScrollBar::handle:horizontal {
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #E0E0E0, stop:1 #D1D1D1);
        min-width: 30px;
        border-radius: 6px;
    }
    """
    app.setStyleSheet(bright_stylesheet)
    mainWin = SimulatorAppUI()
    mainWin.show()
    sys.exit(app.exec())


# --- 修正 DB 关闭格式 ---
def ensure_positions_table_has_buy_rank(db_path):
    conn = None # 初始化 conn
    try:
        conn = sqlite3.connect(db_path) # 尝试连接
        c = conn.cursor()
        # 检查字段是否已存在
        c.execute("PRAGMA table_info(positions)")
        columns = [row[1] for row in c.fetchall()]
        if 'buy_rank' not in columns:
            try:
                c.execute("ALTER TABLE positions ADD COLUMN buy_rank INTEGER")
                conn.commit()
                print("已自动添加 buy_rank 字段。")
            except Exception as e:
                print(f"添加 buy_rank 字段失败: {e}")
                try: conn.rollback() # 如果添加失败，尝试回滚
                except Exception as rb_e: print(f"添加字段失败后回滚错误: {rb_e}")

    except Exception as e_outer:
        # 处理连接数据库或PRAGMA查询时可能发生的错误
        print(f"检查或修改 positions 表结构时出错: {e_outer}")

    finally:
        # --- !! 使用标准的多行格式关闭连接 !! ---
        if conn: # 确保 conn 不是 None
            try:
                conn.close()
                # print("数据库连接已关闭 (ensure_positions_table_has_buy_rank)") # 可选打印
            except Exception as e_close:
                print(f"关闭数据库连接时发生错误 (ensure_positions_table_has_buy_rank): {e_close}")
                pass # 忽略关闭错误
        # --- 关闭连接格式修正结束 ---

# 在主程序启动时调用 (这行保持不变)
ensure_positions_table_has_buy_rank(DB_PATH)


