#!/usr/bin/env python3
"""小五 DeepSeek 策略参数扫描 - 20个随机组合"""
import sys, os, json, random, itertools
sys.path.insert(0, os.path.expanduser('~/go/AlphaGPT_Tushare/src'))

import pandas as pd
import numpy as np
import duckdb
from datetime import datetime

# 1. 找活跃股票
con = duckdb.connect(os.path.expanduser('~/go/data/stock_data.duckdb'), read_only=True)
latest = con.execute("SELECT max(date) FROM stock_prices").fetchone()[0]
rows = con.execute("""
    SELECT stock_code, close, turnover 
    FROM stock_prices 
    WHERE date = ? AND turnover > 5
    AND stock_code NOT LIKE '688%' AND stock_code NOT LIKE '920%'
    AND stock_code NOT LIKE '8%'
    ORDER BY turnover DESC LIMIT 5
""", [latest]).fetchall()
con.close()

print(f"最新数据日期: {latest}")
print(f"候选活跃票:")
for r in rows:
    print(f"  {r[0]} 收盘{r[1]:.2f} 换手{r[2]}%")

# 选第一只
stock = rows[0][0]
stock_name = stock
print(f"\n🎯 选中: {stock}")

# 2. 加载数据
con = duckdb.connect(os.path.expanduser('~/go/data/stock_data.duckdb'), read_only=True)
df = con.execute(f"""
    SELECT date, open, high, low, close, turnover
    FROM stock_prices 
    WHERE stock_code = '{stock}' AND date >= '2024-01-01'
    ORDER BY date
""").df()
con.close()

if len(df) < 200:
    print(f"数据不足 ({len(df)}天), 换票")
    sys.exit(1)

df['ret'] = df['close'].pct_change()
df['ret1'] = df['ret'].shift(1)
df['ret5'] = df['close'].pct_change(5)
df['vol_chg'] = df['turnover'].pct_change()
df['ma5'] = df['close'].rolling(5).mean()
df['ma20'] = df['close'].rolling(20).mean()
df['ma5_ma20'] = df['ma5'] / df['ma20'] - 1
df['high_low'] = (df['high'] - df['low']) / df['close']
df = df.dropna()

print(f"有效数据: {len(df)} 天, {df['date'].iloc[0]} ~ {df['date'].iloc[-1]}")

# 3. 参数空间
param_space = {
    'lookback': [5, 10, 20, 30, 60],
    'hold_days': [1, 3, 5, 10],
    'threshold': [0.005, 0.01, 0.02, 0.03, 0.05],
    'stop_loss': [0.03, 0.05, 0.07, 0.10],
    'weight_ret': [0.3, 0.5, 0.7, 1.0],
    'weight_ma': [0.3, 0.5, 0.7, 1.0],
    'weight_vol': [0.3, 0.5, 0.7, 1.0],
}

# 4. 随机生成 20 个组合
random.seed(42)
combos = []
for i in range(20):
    combo = {k: random.choice(v) for k, v in param_space.items()}
    combo['id'] = i + 1
    combos.append(combo)

print(f"\n🔬 测试 {len(combos)} 个随机参数组合...\n")

results = []
for c in combos:
    lb = c['lookback']
    hd = c['hold_days']
    th = c['threshold']
    sl = c['stop_loss']
    
    # 合成信号
    signal = np.zeros(len(df))
    signal += c['weight_ret'] * df['ret1'].rolling(lb).mean().fillna(0) / df['ret1'].std()
    signal += c['weight_ma'] * df['ma5_ma20'].fillna(0)
    signal += c['weight_vol'] * df['vol_chg'].rolling(lb).mean().fillna(0)
    
    # 回测
    pos = 0
    trades = []
    entry_price = 0
    daily_ret = []
    
    for i in range(lb + 20, len(df) - hd):
        if pos == 0:
            if signal.iloc[i] > th:
                pos = 1
                entry_price = df['close'].iloc[i]
        else:
            fut_ret = (df['close'].iloc[i + hd] - entry_price) / entry_price
            exit_signal = signal.iloc[i] < -th
            stop_hit = (df['low'].iloc[i] - entry_price) / entry_price < -sl
            
            if exit_signal or stop_hit or i >= len(df) - hd - 1:
                if stop_hit:
                    ret = -sl
                else:
                    ret = fut_ret
                trades.append(ret)
                daily_ret.append(ret)
                pos = 0
    
    if not trades:
        continue
    
    trades_arr = np.array(trades)
    win_rate = (trades_arr > 0).mean()
    avg_ret = trades_arr.mean()
    total_ret = (1 + trades_arr).prod() - 1
    sharpe = avg_ret / trades_arr.std() * np.sqrt(252 / hd) if trades_arr.std() > 0 else 0
    max_dd = 0
    cum = np.cumprod(1 + trades_arr)
    peak = np.maximum.accumulate(cum)
    max_dd = ((cum - peak) / peak).min()
    
    results.append({
        **c,
        'stock': stock,
        'trades': len(trades),
        'win_rate': round(win_rate * 100, 1),
        'avg_ret': round(avg_ret * 100, 2),
        'total_ret': round(total_ret * 100, 1),
        'sharpe': round(sharpe, 2),
        'max_dd': round(max_dd * 100, 1),
        'score': round(sharpe * (1 + total_ret/100) * win_rate, 3)
    })

# 5. 排序输出
results.sort(key=lambda x: x['score'], reverse=True)
print(f"{'#':<4} {'look':<5} {'hold':<5} {'thr':<6} {'sl':<5} {'w_ret':<6} {'w_ma':<6} {'w_vol':<6} {'trades':<7} {'win%':<7} {'avgR%':<7} {'总收%':<8} {'sharpe':<7} {'maxDD%':<7} {'score':<7}")
print('-' * 110)
for r in results[:10]:
    print(f"{r['id']:<4} {r['lookback']:<5} {r['hold_days']:<5} {r['threshold']:<6} {r['stop_loss']:<5} {r['weight_ret']:<6} {r['weight_ma']:<6} {r['weight_vol']:<6} {r['trades']:<7} {r['win_rate']:<7} {r['avg_ret']:<7} {r['total_ret']:<8} {r['sharpe']:<7} {r['max_dd']:<7} {r['score']:<7.3f}")

print(f"\n🏆 最佳: ID={results[0]['id']} 总收益{results[0]['total_ret']}% 胜率{results[0]['win_rate']}% sharpe={results[0]['sharpe']}")
