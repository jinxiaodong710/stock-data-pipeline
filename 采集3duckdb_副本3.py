# -*- coding: utf-8 -*-
# 采集2duckdb.py (v21.0 - ST历史数据增强版)
# [更新说明]
# v21.0: 在历史数据采集准备阶段，增加 stock_st_history 表的创建逻辑。

import duckdb
import pandas as pd
import numpy as np
import time
import random
import traceback
from datetime import datetime, timedelta
import queue
import threading
from typing import List, Dict, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import os

# 导入 PySide6.QtCore 中的 QReadWriteLock
try:
    from PySide6.QtCore import QReadWriteLock
    from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                               QLabel, QPushButton, QProgressBar, QDateEdit, QCheckBox,
                               QTextEdit, QGroupBox, QMessageBox, QGridLayout)
    from PySide6.QtCore import Qt, Signal, Slot, QDate, QTimer
    from PySide6.QtGui import QTextCursor
except ImportError:
    print("警告：无法导入 PySide6 模块。UI 将无法正常工作。")
    class QMainWindow: pass
    class QWidget: pass
    class QVBoxLayout: pass
    class QHBoxLayout: pass
    class QLabel: pass
    class QPushButton: pass
    class QProgressBar: pass
    class QDateEdit: pass
    class QCheckBox: pass
    class QTextEdit: pass
    class QGroupBox: pass
    class QMessageBox: pass
    class QGridLayout: pass
    class Qt:
        class ItemDataRole: UserRole = 32
        class AlignmentFlag: AlignRight = 0x0002; AlignVCenter = 0x0080; AlignLeft = 0x0001
        class StandardButton: Ok = 0x00000400; Cancel = 0x00000800; Yes = 0x00004000; No = 0x00010000
        class Corner: TopLeftCorner = 0
    class Signal:
        def __init__(self, *args): pass
        def emit(self, *args): pass
    class Slot:
        def __init__(self, *args): pass
        def __call__(self, func): return func
    class QDate:
        @staticmethod
        def currentDate(): return QDate()
        def addYears(self, n): return self
        def toString(self, fmt): return ""
    class QTimer:
        def __init__(self): pass
        def setParent(self, parent): pass
        def timeout(self): return Signal()
        def start(self, interval): pass
        def isActive(self): return False
        def stop(self, interval): pass
        def interval(self): return 0
        def singleShot(self, interval, func): pass
        
    class QTextCursor:
        def movePosition(self, op): pass
        class MoveOperation: Start = 0
    class QReadWriteLock: 
        def lockForRead(self): pass
        def lockForWrite(self): pass
        def unlock(self): pass
    
# 导入 stock_data_collector
from stock_data_collector import (collect_historical_data_stock_by_stock,
                                collect_full_snapshot, 
                                collect_stock_snapshot_auto,
                                collect_fundamentals_data)

# 尝试导入akshare库
try:
    import akshare as ak
    AKSHARE_AVAILABLE = True
except ImportError:
    AKSHARE_AVAILABLE = False


class CollectorUI(QMainWindow):
    # 信号定义
    collectorLogSignal = Signal(str, str)
    collectorProgressSignal = Signal(int)
    collectorStatusSignal = Signal(bool, str)
    snapshotStatusSignal = Signal(bool, str)
    fundamentalsStatusSignal = Signal(bool, str)
    
    def __init__(self, db_path):
        super().__init__()
        self.db_path = db_path
        self.db_connection_lock = QReadWriteLock()
        self.result_queue = queue.Queue()
        self.snapshot_stop_event = threading.Event()
        self.collector_stop_event = threading.Event()
        self.auto_update_stop_event = threading.Event()
        self.fundamentals_stop_event = threading.Event()
        
        # 定义基本面字段
        self.fundamentals_fields_list = [
            'ts_code', 'ann_date', 'end_date', 'eps', 'dt_eps', 'total_revenue_ps', 
            'revenue_ps', 'capital_rese_ps', 'surplus_rese_ps', 'undist_profit_ps', 
            'extra_item', 'profit_dedt', 'gross_margin', 'current_ratio', 'quick_ratio', 
            'cash_ratio', 'ar_turn', 'ca_turn', 'fa_turn', 'assets_turn', 'op_income', 
            'ebit', 'ebitda', 'fcff', 'fcfe', 'current_exint', 'noncurrent_exint', 
            'interestdebt', 'netdebt', 'tangible_asset', 'working_capital', 
            'networking_capital', 'invest_capital', 'retained_earnings', 'diluted2_eps', 
            'bps', 'ocfps', 'retainedps', 'cfps', 'ebit_ps', 'fcff_ps', 'fcfe_ps', 
            'netprofit_margin', 'grossprofit_margin', 'cogs_of_sales', 'expense_of_sales', 
            'profit_to_gr', 'saleexp_to_gr', 'adminexp_of_gr', 'finaexp_of_gr', 
            'impai_ttm', 'gc_of_gr', 'op_of_gr', 'ebit_of_gr', 'roe', 'roe_waa', 
            'roe_dt', 'roa', 'npta', 'roic', 'roe_yearly', 'roa2_yearly', 
            'debt_to_assets', 'assets_to_eqt', 'dp_assets_to_eqt', 'ca_to_assets', 
            'nca_to_assets', 'tbassets_to_totalassets', 'int_to_talcap', 
            'eqt_to_talcapital', 'currentdebt_to_debt', 'longdeb_to_debt', 
            'ocf_to_shortdebt', 'debt_to_eqt', 'eqt_to_debt', 'eqt_to_interestdebt', 
            'tangibleasset_to_debt', 'tangasset_to_intdebt', 'tangibleasset_to_netdebt', 
            'ocf_to_debt', 'turn_days', 'roa_yearly', 'roa_dp', 'fixed_assets', 
            'profit_to_op', 'q_saleexp_to_gr', 'q_gc_to_gr', 'q_roe', 'q_dt_roe', 
            'q_npta', 'q_ocf_to_sales', 'basic_eps_yoy', 'dt_eps_yoy', 'cfps_yoy', 
            'op_yoy', 'ebt_yoy', 'netprofit_yoy', 'dt_netprofit_yoy', 'ocf_yoy', 
            'roe_yoy', 'bps_yoy', 'assets_yoy', 'eqt_yoy', 'tr_yoy', 'or_yoy', 
            'q_sales_yoy', 'q_op_qoq', 'equity_yoy', 'update_flag'
        ]

        self.auto_update_timer = QTimer()
        self.auto_update_timer.setParent(self)
        
        self.queue_check_timer = QTimer()
        self.queue_check_timer.setParent(self)
        
        self.collector_thread = None
        self.snapshot_thread = None
        self.auto_update_thread = None
        self.fundamentals_thread = None
        
        self.is_snapshot_running = False
        self.is_historical_collector_running = False
        self.is_fundamentals_running = False
        
        self.conn = None
        
        self._setup_ui()
        self.auto_update_check.setChecked(False)
        self._connect_signals()
        self._check_environment()
        
        self.queue_check_timer.timeout.connect(self._check_queue)
        self.queue_check_timer.start(100)
        
        self.setWindowTitle("股票数据采集器 (v21.0 - ST增强版)")
        self.resize(600, 800)
    
    def _setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        top_layout = QHBoxLayout()
        
        # --- 左侧：历史数据采集区域 ---
        historical_group = QGroupBox("历史/基本面数据采集")
        historical_layout = QVBoxLayout()
        
        date_layout = QGridLayout()
        date_layout.addWidget(QLabel("开始日期:"), 0, 0)
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate.currentDate().addYears(-1))
        date_layout.addWidget(self.start_date_edit, 0, 1)
        
        date_layout.addWidget(QLabel("结束日期:"), 1, 0)
        self.end_date_edit = QDateEdit()
        self.end_date_edit.setCalendarPopup(True)
        self.end_date_edit.setDate(QDate.currentDate())
        date_layout.addWidget(self.end_date_edit, 1, 1)
        
        historical_layout.addLayout(date_layout)
        
        self.test_connection_btn = QPushButton("测试数据库连接")
        historical_layout.addWidget(self.test_connection_btn)
        
        self.collect_btn = QPushButton("开始历史数据采集")
        historical_layout.addWidget(self.collect_btn)
        
        self.collect_fundamentals_btn = QPushButton("采集基本面数据")
        self.collect_fundamentals_btn.setToolTip("按季度采集指定日期范围内的财务指标")
        historical_layout.addWidget(self.collect_fundamentals_btn)
        
        historical_group.setLayout(historical_layout)
        top_layout.addWidget(historical_group)
        
        # --- 右侧：快照数据采集区域 ---
        snapshot_group = QGroupBox("快照数据采集")
        snapshot_layout = QVBoxLayout()
        
        self.snapshot_btn = QPushButton("采集快照")
        snapshot_layout.addWidget(self.snapshot_btn)
        
        self.auto_update_check = QCheckBox("自动更新")
        snapshot_layout.addWidget(self.auto_update_check)
        
        snapshot_group.setLayout(snapshot_layout)
        top_layout.addWidget(snapshot_group)
        
        main_layout.addLayout(top_layout)
        
        self.stop_btn = QPushButton("停止所有任务")
        self.stop_btn.setStyleSheet("background-color: #ffaaaa;")
        main_layout.addWidget(self.stop_btn)
        
        status_group = QGroupBox("状态")
        status_layout = QVBoxLayout()
        self.progress_bar = QProgressBar()
        status_layout.addWidget(self.progress_bar)
        self.status_label = QLabel("空闲")
        status_layout.addWidget(self.status_label)
        status_group.setLayout(status_layout)
        main_layout.addWidget(status_group)
        
        log_group = QGroupBox("日志")
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        main_layout.addWidget(log_group)

    def _connect_signals(self):
        try:
            self.test_connection_btn.clicked.connect(self._test_db_connection)
            self.collect_btn.clicked.connect(self._handle_historical_collection)
            self.snapshot_btn.clicked.connect(self._handle_snapshot_collection)
            self.stop_btn.clicked.connect(self._handle_stop)
            self.collect_fundamentals_btn.clicked.connect(self._handle_fundamentals_collection)
            self.auto_update_check.stateChanged.connect(self._toggle_auto_update)
            
            self.collectorLogSignal.connect(self._update_log)
            self.collectorProgressSignal.connect(self._update_progress)
            self.collectorStatusSignal.connect(self._update_collector_status)
            self.snapshotStatusSignal.connect(self._update_snapshot_status)
            self.fundamentalsStatusSignal.connect(self._update_fundamentals_status)
        except Exception as e:
            print(f"连接信号时出错: {e}")
            traceback.print_exc()

    def _test_db_connection(self):
        try:
            conn = duckdb.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT version();")
            version = cursor.fetchone()
            self._ensure_fundamentals_table(conn)
            conn.commit()
            conn.close()
            self._log_message(f"数据库连接测试成功 (DuckDB v{version[0]})", "success")
            return True
        except Exception as e:
            self._log_message(f"数据库连接测试失败: {e}", "error")
            QMessageBox.critical(self, "数据库错误", f"无法连接到数据库: {e}")
            return False

    def _handle_historical_collection(self):
        self._start_historical_collection()

    def _handle_fundamentals_collection(self):
        self._start_fundamentals_collection()

    def _handle_snapshot_collection(self):
        self._start_snapshot_collection()

    def _start_historical_collection(self):
        if self.is_historical_collector_running:
            self.collector_stop_event.set()
            self.collect_btn.setText("开始历史数据采集")
            return
        if self.is_snapshot_running or self.is_fundamentals_running:
            self._log_message("其他采集任务正在运行，请等待", "warning")
            return
            
        start_date = self.start_date_edit.date().toString("yyyy-MM-dd")
        end_date = self.end_date_edit.date().toString("yyyy-MM-dd")
        
        self.collector_stop_event.clear()
        self.is_historical_collector_running = True
        self.collector_thread = threading.Thread(
            target=self._run_historical_collection,
            args=(start_date, end_date)
        )
        self.collector_thread.start()
        self.collect_btn.setText("停止历史数据采集")

    def _start_fundamentals_collection(self):
        if self.is_fundamentals_running:
            self.fundamentals_stop_event.set()
            self.collect_fundamentals_btn.setText("采集基本面数据")
            return
        if self.is_historical_collector_running or self.is_snapshot_running:
            self._log_message("其他采集任务正在运行，请等待", "warning")
            return
            
        start_date = self.start_date_edit.date().toString("yyyy-MM-dd")
        end_date = self.end_date_edit.date().toString("yyyy-MM-dd")
        
        self.fundamentals_stop_event.clear()
        self.is_fundamentals_running = True
        self.fundamentals_thread = threading.Thread(
            target=self._run_fundamentals_collection,
            args=(start_date, end_date)
        )
        self.fundamentals_thread.start()
        self.collect_fundamentals_btn.setText("停止基本面采集")
        
    def _run_historical_collection(self, start_date, end_date):
        conn = None
        try:
            conn = duckdb.connect(self.db_path)
            cursor = conn.cursor()

            # --- stock_prices ---
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS stock_prices (
                stock_code TEXT,
                date TEXT,
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
            
            # 检查 stock_prices 字段
            try:
                cursor.execute("DESCRIBE stock_prices")
                current_cols_stock = [col[0] for col in cursor.fetchall()]
                for col in ['adj_open', 'adj_close', 'ak_change_pct', 'ak_change_amount']:
                    if col not in current_cols_stock:
                        cursor.execute(f"ALTER TABLE stock_prices ADD COLUMN {col} REAL")
                        self._log_message(f"补全 stock_prices 字段: {col}", "info")
            except Exception as e_alter:
                self._log_message(f"表结构检查警告: {e_alter}", "warning")

            # --- index_prices ---
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS index_prices (
                index_code TEXT, date TEXT,
                open REAL, high REAL, low REAL, close REAL,
                volume BIGINT, amount REAL,
                PRIMARY KEY (index_code, date)
            )
            """)

            # --- exp_trade ---
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS exp_trade (
                exchange TEXT, is_open INTEGER, date TEXT, pretrade_date1 TEXT,
                PRIMARY KEY (exchange, date)
            )
            """)
            
            # --- (新增) stock_st_history ---
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS stock_st_history (
                trade_date TEXT,
                ts_code TEXT,
                name TEXT,
                PRIMARY KEY (trade_date, ts_code)
            )
            """)
            # 检查 stock_st_history 字段
            cursor.execute("DESCRIBE stock_st_history")
            current_cols_st = [col[0] for col in cursor.fetchall()]
            if 'name' not in current_cols_st:
                try: cursor.execute("ALTER TABLE stock_st_history ADD COLUMN name TEXT")
                except: pass

            # --- stock_fundamentals ---
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS stock_fundamentals (
                stock_code TEXT PRIMARY KEY, stock_name TEXT, latest_price REAL,
                change_percent REAL, change_amount REAL, volume BIGINT, trade_amount REAL,
                open REAL, high REAL, low REAL, prev_close REAL, amplitude REAL,
                turnover_rate REAL, volume_ratio REAL, pe_ratio REAL, pb_ratio REAL,
                total_market_cap REAL, circulating_market_cap REAL, change_rate REAL,
                change_pct_5min REAL, change_pct_60d REAL, change_pct_ytd REAL,
                industry TEXT, last_updated TEXT
            )
            """) 
            
            # --- index_snapshots ---
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS index_snapshots (
                index_code TEXT PRIMARY KEY, index_name TEXT, latest_price REAL,
                change_amount REAL, change_percent REAL, volume REAL, amount REAL,
                last_updated TEXT
            )
            """) 

            # --- board_snapshots ---
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS board_snapshots (
                board_code TEXT PRIMARY KEY, board_name TEXT, company_count INTEGER,
                avg_price REAL, change_percent REAL, total_market_cap REAL,
                main_net_inflow REAL, last_updated TEXT
            )
            """)

            self._ensure_fundamentals_table(conn)
            conn.commit()
            self._log_message("数据库表结构检查完成", "info")

            # 执行历史数据采集 (注意：需更新 stock_data_collector.py 才能真正下载 ST 数据)
            collect_historical_data_stock_by_stock(
                self.db_path, conn, self.db_connection_lock, start_date, end_date,
                ["上证指数", "深证成指", "创业板指", "微盘股指"], 
                self.result_queue, self.collector_stop_event
            )
            
            conn.commit()
            self._log_message("历史数据采集已提交到数据库。", "success")
            
        except Exception as e:
            self._log_message(f"历史数据采集出错: {e}", "error")
            traceback.print_exc()
        finally:
            if conn: conn.close()
            self.is_historical_collector_running = False
            self.collectorStatusSignal.emit(False, "历史数据采集完成" if not self.collector_stop_event.is_set() else "历史数据采集已停止")
            if self.collector_stop_event.is_set(): self.collectorProgressSignal.emit(0)
            else: self.collectorProgressSignal.emit(100)

    def _run_fundamentals_collection(self, start_date, end_date):
        conn = None
        try:
            conn = duckdb.connect(self.db_path)
            self._ensure_fundamentals_table(conn)
            conn.commit()
            
            collect_fundamentals_data(
                self.db_path, conn, self.db_connection_lock, start_date, end_date,
                self.result_queue, self.fundamentals_stop_event
            )
            conn.commit()
            self._log_message("基本面数据采集已提交到数据库。", "success")

        except Exception as e:
            self._log_message(f"基本面数据采集出错: {e}", "error")
            traceback.print_exc()
        finally:
            if conn: conn.close()
            self.is_fundamentals_running = False

    def _ensure_fundamentals_table(self, conn):
        try:
            cursor = conn.cursor()
            fields_sql_definitions = []
            for field in self.fundamentals_fields_list:
                if field in ['ts_code', 'ann_date', 'end_date', 'update_flag']:
                    fields_sql_definitions.append(f'"{field}" TEXT')
                else:
                    fields_sql_definitions.append(f'"{field}" REAL')
            
            create_table_sql = f"""
            CREATE TABLE IF NOT EXISTS stock_financial_indicators (
                {', '.join(fields_sql_definitions)},
                PRIMARY KEY (ts_code, end_date)
            )
            """
            cursor.execute(create_table_sql)
            
            cursor.execute("DESCRIBE stock_financial_indicators")
            current_cols = [col[0] for col in cursor.fetchall()]
            for field in self.fundamentals_fields_list:
                if field not in current_cols:
                    try:
                        col_type = 'TEXT' if field in ['ts_code', 'ann_date', 'end_date', 'update_flag'] else 'REAL'
                        cursor.execute(f'ALTER TABLE stock_financial_indicators ADD COLUMN "{field}" {col_type}')
                    except Exception: pass
        except Exception as e:
            self._log_message(f"检查 stock_financial_indicators 表时出错: {e}", "error")

    def _start_snapshot_collection(self):
        if self.is_snapshot_running:
            self.snapshot_stop_event.set()
            self.snapshot_btn.setText("采集快照")
            return
        if self.is_historical_collector_running or self.is_fundamentals_running:
            self._log_message("其他采集任务正在运行，请等待", "warning")
            return
        
        self.snapshot_stop_event.clear()
        self.is_snapshot_running = True
        self.snapshot_thread = threading.Thread(target=self._run_snapshot_collection)
        self.snapshot_thread.start()
        self.snapshot_btn.setText("停止快照采集")
        
    def _run_snapshot_collection(self):
        conn = None
        try:
            conn = duckdb.connect(self.db_path)
            self._ensure_db_tables(conn)
            conn.commit()

            collect_full_snapshot(
                self.db_path, conn, self.db_connection_lock,
                self.result_queue, self.snapshot_stop_event
            )
            conn.commit()
            self._log_message("快照数据采集已提交到数据库。", "success")
        except Exception as e:
            self._log_message(f"快照采集错误: {e}", "error")
            traceback.print_exc()
        finally:
            if conn: conn.close()
            self.is_snapshot_running = False 
            self._log_message("快照采集线程完成。", "info")

    def _toggle_auto_update(self, state):
        if self.auto_update_check.isChecked():
            self._enable_auto_update()
        else:
            self._disable_auto_update()

    def _enable_auto_update(self):
        if self.auto_update_timer.isActive(): self.auto_update_timer.stop()
        try: self.auto_update_timer.timeout.disconnect(self._execute_auto_update)
        except: pass
        
        self.auto_update_timer.timeout.connect(self._execute_auto_update)
        self.auto_update_timer.setInterval(60000)
        self.auto_update_timer.start()
        self._log_message("自动更新已启用，每分钟执行一次", "info")
        QTimer.singleShot(1000, self._execute_auto_update)

    def _disable_auto_update(self):
        if self.auto_update_timer.isActive(): self.auto_update_timer.stop()
        try: self.auto_update_timer.timeout.disconnect(self._execute_auto_update)
        except: pass
        if self.auto_update_thread and self.auto_update_thread.is_alive():
            self.auto_update_stop_event.set()
            self.auto_update_thread.join(0.5)
        self._log_message("自动更新已禁用", "info")

    def _execute_auto_update(self):
        if self.is_snapshot_running or self.is_historical_collector_running or self.is_fundamentals_running:
            self._log_message("自动更新跳过：其他任务正在运行。", "info")
            return
        
        self.auto_update_stop_event.clear()
        self.is_snapshot_running = True  
        self.snapshotStatusSignal.emit(True, "自动更新中...")
        
        self.auto_update_thread = threading.Thread(
            target=self._run_auto_update_snapshot,
            name="AutoUpdateThread",
            daemon=True
        )
        self.auto_update_thread.start()

    def _run_auto_update_snapshot(self):
        try:
            conn = duckdb.connect(self.db_path)
            self._ensure_tables_exist(conn)
            conn.commit()
            
            collect_stock_snapshot_auto(
                self.db_path, conn, self.db_connection_lock,
                self.result_queue, self.auto_update_stop_event
            )
            conn.commit()
            conn.close()
            self.is_snapshot_running = False
        except Exception as e:
            self.result_queue.put(("log", f"自动更新异常: {e}", "error"))
            traceback.print_exc()
            self.is_snapshot_running = False
            self.snapshotStatusSignal.emit(False, "自动更新出错")

    def _ensure_tables_exist(self, conn):
        # 简化版：复用逻辑，这里直接调用 run_historical 里类似的逻辑，
        # 但为防止重复代码，这里只写关键快照表
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE IF NOT EXISTS stock_fundamentals (stock_code TEXT PRIMARY KEY, stock_name TEXT, latest_price REAL, change_percent REAL, change_amount REAL, volume BIGINT, trade_amount REAL, open REAL, high REAL, low REAL, prev_close REAL, amplitude REAL, turnover_rate REAL, volume_ratio REAL, pe_ratio REAL, pb_ratio REAL, total_market_cap REAL, circulating_market_cap REAL, change_rate REAL, change_pct_5min REAL, change_pct_60d REAL, change_pct_ytd REAL, industry TEXT, last_updated TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS index_snapshots (index_code TEXT PRIMARY KEY, index_name TEXT, latest_price REAL, change_amount REAL, change_percent REAL, volume REAL, amount REAL, last_updated TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS board_snapshots (board_code TEXT PRIMARY KEY, board_name TEXT, company_count INTEGER, avg_price REAL, change_percent REAL, total_market_cap REAL, main_net_inflow REAL, last_updated TEXT)")
        self._ensure_fundamentals_table(conn) # 确保基本面表

    def _ensure_db_tables(self, conn):
        return self._ensure_tables_exist(conn)

    def _check_queue(self):
        try:
            max_messages = 10; processed = 0
            while not self.result_queue.empty() and processed < max_messages:
                try:
                    item = self.result_queue.get_nowait(); processed += 1
                    if isinstance(item, tuple) and len(item) == 3 and item[0] == 'log':
                        self._log_message(item[1], item[2]); continue 
                    if isinstance(item, tuple):
                        msg_type = item[0]; msg_data = item[1]
                        if msg_type == "collector_log": self._log_message(msg_data['msg'], msg_data['tag'])
                        elif msg_type == "fundamentals_log": self._log_message(msg_data['msg'], msg_data['tag'])
                        elif msg_type == "collector_progress": self.collectorProgressSignal.emit(msg_data)
                        elif msg_type == "fundamentals_progress": self.collectorProgressSignal.emit(msg_data)
                        elif msg_type == "collector_status": self.collectorStatusSignal.emit(msg_data['running'], msg_data['text'])
                        elif msg_type == "fundamentals_status": self.fundamentalsStatusSignal.emit(msg_data['running'], msg_data['text'])
                        elif msg_type == "snapshot_status": self.snapshotStatusSignal.emit(msg_data['running'], msg_data['text'])
                        elif msg_type == "collector_error": QMessageBox.critical(self, msg_data['title'], msg_data['msg'])
                        elif msg_type == "collector_complete":
                            self._log_message(f"历史数据采集结束: {msg_data.get('status', '')}", "info")
                            self.collectorStatusSignal.emit(False, "完成")
                            self.collectorProgressSignal.emit(100)
                        elif msg_type == "fundamentals_complete":
                            self._log_message(f"基本面采集结束: {msg_data.get('status', '')}", "info")
                            self.fundamentalsStatusSignal.emit(False, "完成")
                        elif msg_type == "snapshot_complete":
                            self._log_message(f"快照采集结束: {msg_data.get('status', '')}", "info")
                            self.snapshotStatusSignal.emit(False, "完成")
                        elif msg_type == "auto_snapshot_complete":
                             self.snapshotStatusSignal.emit(False, "自动更新已开启")
                except queue.Empty: break 
        except Exception as e:
            traceback.print_exc()

    @Slot(str, str)
    def _update_log(self, message, level="info"):
        color_map = {"info": "black", "warning": "orange", "error": "red", "success": "green", "debug": "gray"}
        color = color_map.get(level, "black"); timestamp = datetime.now().strftime("[%H:%M:%S]")
        self.log_text.append(f'<span style="color:{color};">{timestamp} {message}</span>')

    @Slot(int)
    def _update_progress(self, value): self.progress_bar.setValue(value)
        
    @Slot(bool, str)
    def _update_collector_status(self, running, status_text):
        self.status_label.setText(status_text)
        if not running: self.collect_btn.setText("开始历史数据采集")

    @Slot(bool, str)
    def _update_fundamentals_status(self, running, status_text):
        self.status_label.setText(status_text)
        if not running: self.collect_fundamentals_btn.setText("采集基本面数据")
            
    @Slot(bool, str)
    def _update_snapshot_status(self, running, status_text):
        self.status_label.setText(status_text)
        if not running: self.snapshot_btn.setText("采集快照")
            
    def _log_message(self, message, level="info"):
        self.collectorLogSignal.emit(message, level)
        print(f"[CONSOLE LOG] {message} ({level})")

    def closeEvent(self, event):
        self.snapshot_stop_event.set(); self.collector_stop_event.set()
        self.auto_update_stop_event.set(); self.fundamentals_stop_event.set() 
        if self.auto_update_timer.isActive(): self.auto_update_timer.stop()
        event.accept() 

    def _check_environment(self):
        self._test_db_connection()
        # 简单检查 Tushare
        try:
            from stock_data_collector import TUSHARE_TOKEN
            if not TUSHARE_TOKEN: self._log_message("警告: Tushare TOKEN 未设置", "warning")
        except: pass

    def _handle_stop(self):
        self.snapshot_stop_event.set(); self.collector_stop_event.set()
        self.auto_update_stop_event.set(); self.fundamentals_stop_event.set() 
        self.collect_btn.setText("开始历史数据采集")
        self.snapshot_btn.setText("采集快照")
        self._log_message("已发送停止信号给所有任务", "warning")

if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    import sys
    app = QApplication(sys.argv)
    window = CollectorUI("f:\stock\stock_data.duckdb")
    window.show()
    sys.exit(app.exec())