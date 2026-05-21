#!/usr/bin/env python3
"""回测：每日换手率TOP10 尾盘买→次日尾盘卖 (2025-2026)"""
import duckdb, sys
from datetime import datetime

DB = '/Users/jin/go/data/stock_data.duckdb'
con = duckdb.connect(DB, read_only=True)

# 获取每日换手率前10
query = """
WITH ranked AS (
    SELECT date, stock_code, close, turnover_rate, limit_status,
        ROW_NUMBER() OVER (PARTITION BY date ORDER BY turnover_rate DESC) as rn
    FROM stock_prices
    WHERE date >= '2025-01-01'
      AND turnover_rate > 0
      AND turnover_rate < 100  -- 排除异常值
      AND stock_code NOT LIKE '%ST%'  -- 排除ST（需要通过名称过滤）
)
SELECT date, stock_code, close, turnover_rate, rn
FROM ranked
WHERE rn <= 10
ORDER BY date, rn
"""
df = con.execute(query).fetchdf()
con.close()

print(f"选出 {len(df)} 条记录, {df['date'].nunique()} 个交易日")
print(f"日期范围: {df['date'].min()} ~ {df['date'].max()}")

# 重新连接（读模式不够用）
con = duckdb.connect(DB, read_only=True)

trades = []
for date, group in df.groupby('date'):
    codes = group['stock_code'].tolist()
    buy_prices = group.set_index('stock_code')['close'].to_dict()
    
    # 查次日收盘价
    next_day = con.execute("""
        SELECT MIN(date) FROM stock_prices 
        WHERE date > ? AND stock_code = ?
    """, [date, codes[0]]).fetchone()
    
    if next_day is None: continue
    next_date = next_day[0]
    
    # 批量获取次日收盘价
    next_prices = {}
    for code in codes:
        r = con.execute("""
            SELECT close FROM stock_prices 
            WHERE date = ? AND stock_code = ?
        """, [next_date, code]).fetchone()
        if r:
            next_prices[code] = r[0]
    
    if len(next_prices) == 0: continue
    
    # 计算当日收益
    day_ret = 0
    valid = 0
    for code in codes:
        if code not in next_prices or code not in buy_prices: continue
        buy = buy_prices[code]
        sell = next_prices[code]
        if buy <= 0 or sell <= 0: continue
        ret = (sell - buy) / buy
        day_ret += ret
        valid += 1
    
    if valid > 0:
        trades.append({
            'buy_date': date,
            'sell_date': next_date,
            'avg_return': day_ret / valid,
            'n_stocks': valid
        })

con.close()

if not trades:
    print("无交易数据")
    sys.exit(0)

# 统计
import pandas as pd
import numpy as np
tdf = pd.DataFrame(trades)

cum = 1.0
cum_vals = [1.0]
for _, row in tdf.iterrows():
    cum *= (1 + row['avg_return'])
    cum_vals.append(cum)

total_ret = cum - 1
win_rate = (tdf['avg_return'] > 0).mean()
win_days = (tdf['avg_return'] > 0).sum()
lose_days = (tdf['avg_return'] < 0).sum()
avg_win = tdf[tdf['avg_return'] > 0]['avg_return'].mean() if win_days > 0 else 0
avg_lose = tdf[tdf['avg_return'] < 0]['avg_return'].mean() if lose_days > 0 else 0

# 最大回撤
peak = np.maximum.accumulate(cum_vals)
dd = (np.array(cum_vals) - peak) / peak
max_dd = dd.min()

# 按年/月统计
tdf['month'] = pd.to_datetime(tdf['buy_date']).dt.to_period('M')
monthly = tdf.groupby('month')['avg_return'].sum()

print(f"\n{'='*50}")
print(f"📊 换手率TOP10 隔日策略回测 (2025-2026)")
print(f"{'='*50}")
print(f"交易日数: {len(tdf)}")
print(f"累计收益: {total_ret:+.2%}")
print(f"胜率: {win_rate:.1%} ({win_days}胜/{lose_days}负)")
print(f"平均盈利日: {avg_win:+.2%}")
print(f"平均亏损日: {avg_lose:+.2%}")
print(f"盈亏比: {abs(avg_win/avg_lose):.2f}" if avg_lose != 0 else "盈亏比: N/A")
print(f"最大回撤: {max_dd:.2%}")
print(f"年化收益: {(cum ** (252/len(tdf)) - 1):.2%}")

print(f"\n📅 月度收益:")
for m, r in monthly.items():
    bar = '🟢' if r > 0 else '🔴'
    print(f"  {m}: {bar} {r:+.2%}")

print(f"\n🏆 最佳10日:")
best10 = tdf.nlargest(10, 'avg_return')
for _, row in best10.iterrows():
    print(f"  {row['buy_date']}: {row['avg_return']:+.2%}")

print(f"\n💀 最差10日:")
worst10 = tdf.nsmallest(10, 'avg_return')
for _, row in worst10.iterrows():
    print(f"  {row['buy_date']}: {row['avg_return']:+.2%}")
