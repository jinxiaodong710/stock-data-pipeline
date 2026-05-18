#!/usr/bin/env python3.13
"""
纯小市值截面轮动 vFinal — 5只·周调 + 大盘止损 + 个股止损
"""
import duckdb, pandas as pd, numpy as np
from collections import defaultdict
import bisect
import warnings
warnings.filterwarnings('ignore')

DB_PATH = 'data/stock_data.duckdb'
N_STOCKS = 5
INIT_CAPITAL = 1_000_000
MIN_LIST_DAYS = 60
REBALANCE_INTERVAL = 5
ST_WINDOW = 30
MIN_PRICE = 2.0
SKIP_MONTHS = {1, 4}
INDEX_STOP = -3.0    # 上证单日跌超3% → 全清
STOCK_STOP = -15.0   # 个股从买入跌超15% → 止损

con = duckdb.connect(DB_PATH)
print("加载数据...")

# ST
st_all_dates = defaultdict(list)
for r in con.execute("SELECT ts_code, trade_date FROM stock_st_history").fetchall():
    st_all_dates[r[0][:6]].append(r[1])
for c in st_all_dates:
    st_all_dates[c] = sorted(set(st_all_dates[c]))
print(f"  ST: {len(st_all_dates)} 只")

# 基本信息
basic = {}
for r in con.execute("SELECT ts_code, list_date, name FROM stock_basic_info").fetchall():
    basic[r[0][:6]] = {'list_date': r[1], 'name': r[2]}

# 行情 + 指数
rows = con.execute("""
    SELECT stock_code, date, open, close, adj_open, adj_close, total_mv
    FROM stock_prices 
    WHERE date >= '2021-01-01' AND date <= '2026-05-15' AND total_mv > 0
    ORDER BY date, stock_code
""").fetchall()

idx_rows = con.execute("""
    SELECT date, close FROM index_prices
    WHERE index_code='000001' AND date >= '2021-01-01' AND date <= '2026-05-15'
    ORDER BY date
""").fetchall()
con.close()

price, mv = {}, {}
for r in rows:
    price[(r[1], r[0])] = (r[2], r[3], r[4], r[5])
    mv[(r[1], r[0])] = r[6]

# 指数日收益率
idx_dates = [r[0] for r in idx_rows]
idx_close = {r[0]: r[1] for r in idx_rows}
idx_ret = {}
for i in range(1, len(idx_dates)):
    prev, cur = idx_close[idx_dates[i-1]], idx_close[idx_dates[i]]
    if prev and prev > 0:
        idx_ret[idx_dates[i]] = (cur / prev - 1) * 100

trade_dates = sorted(set(r[1] for r in rows))
print(f"  行情: {len(rows):,}行, {len(trade_dates)}日")
print(f"  指数: {len(idx_rows)}日")

rebalance_dates = [trade_dates[i] for i in range(0, len(trade_dates), REBALANCE_INTERVAL)]
if trade_dates[0] not in rebalance_dates:
    rebalance_dates.insert(0, trade_dates[0])
print(f"  调仓: {len(rebalance_dates)} 次")

def is_st(code, date_str):
    dl = st_all_dates.get(code, [])
    if not dl:
        return False
    idx = bisect.bisect_left(dl, date_str)
    if idx > 0 and (pd.Timestamp(date_str) - pd.Timestamp(dl[idx-1])).days <= ST_WINDOW:
        return True
    if idx < len(dl) and (pd.Timestamp(dl[idx]) - pd.Timestamp(date_str)).days <= ST_WINDOW:
        return True
    return False

def get_universe(date_str):
    stocks = []
    for code in basic:
        if (pd.Timestamp(date_str) - pd.Timestamp(basic[code]['list_date'])).days < MIN_LIST_DAYS:
            continue
        if is_st(code, date_str):
            continue
        k = (date_str, code)
        if k not in mv:
            continue
        if k in price:
            _, cl, _, acl = price[k]
            p = cl if cl and not np.isnan(cl) else acl
            if p is not None and not np.isnan(p) and p < MIN_PRICE:
                continue
        stocks.append((code, mv[k], basic[code]['name']))
    return stocks

print("回测中...")
cash = float(INIT_CAPITAL)
positions = {}  # {code: (buy_date, buy_price, qty, cost, name)}
daily_values = []
trades = []
index_stops = 0
stock_stops = 0

for i, today in enumerate(trade_dates):
    in_skip = pd.Timestamp(today).month in SKIP_MONTHS
    
    # ===== 每日止损检查 =====
    # 1. 大盘止损：上证单日跌超阈值 → 全清
    index_drop = idx_ret.get(today, 0)
    if index_drop <= INDEX_STOP and positions:
        to_sell_all = list(positions.keys())
        for code in to_sell_all:
            k = (today, code)
            if k in price:
                _, cl, _, acl = price[k]
                sp = cl if cl and not np.isnan(cl) else acl
                if sp and sp > 0 and not np.isnan(sp):
                    pos = positions[code]
                    sell_amt = sp * pos[2]
                    cash += sell_amt
                    trades.append({
                        'buy_date': pos[0], 'sell_date': today,
                        'code': code, 'name': pos[4],
                        'buy_price': pos[1], 'sell_price': sp,
                        'pnl': sell_amt - pos[3],
                        'return_pct': (sp / pos[1] - 1) * 100,
                        'reason': f'大盘止损(跌{index_drop:.1f}%)'
                    })
                    del positions[code]
        index_stops += 1
    
    # 2. 个股止损：从买入价跌超阈值
    if positions:
        stock_to_sell = []
        for code, pos in positions.items():
            k = (today, code)
            if k in price:
                _, cl, _, acl = price[k]
                sp = cl if cl and not np.isnan(cl) else acl
                if sp and sp > 0 and not np.isnan(sp):
                    loss_pct = (sp / pos[1] - 1) * 100
                    if loss_pct <= STOCK_STOP:
                        stock_to_sell.append((code, sp, loss_pct))
        
        for code, sp, loss_pct in stock_to_sell:
            pos = positions[code]
            sell_amt = sp * pos[2]
            cash += sell_amt
            trades.append({
                'buy_date': pos[0], 'sell_date': today,
                'code': code, 'name': pos[4],
                'buy_price': pos[1], 'sell_price': sp,
                'pnl': sell_amt - pos[3],
                'return_pct': loss_pct,
                'reason': f'个股止损(跌{loss_pct:.1f}%)'
            })
            del positions[code]
            stock_stops += 1
    
    # ===== 调仓日截面重排 =====
    if today in rebalance_dates:
        universe = get_universe(today)
        universe.sort(key=lambda x: x[1])
        top_codes = {s[0] for s in universe[:N_STOCKS]}
        
        # 卖出跌出前五/变ST的
        to_sell = [c for c in positions if c not in top_codes or is_st(c, today)]
        for code in to_sell:
            k = (today, code)
            if k in price:
                _, cl, _, acl = price[k]
                sp = cl if cl and not np.isnan(cl) else acl
                if sp and sp > 0 and not np.isnan(sp):
                    pos = positions[code]
                    sell_amt = sp * pos[2]
                    cash += sell_amt
                    reason = '变ST' if is_st(code, today) else '跌出前五'
                    trades.append({
                        'buy_date': pos[0], 'sell_date': today,
                        'code': code, 'name': pos[4],
                        'buy_price': pos[1], 'sell_price': sp,
                        'pnl': sell_amt - pos[3],
                        'return_pct': (sp / pos[1] - 1) * 100,
                        'reason': reason
                    })
                    del positions[code]
        
        # 买入
        new_codes = [] if in_skip else [s for s in universe[:N_STOCKS] if s[0] not in positions]
        if new_codes and cash > 0:
            total_eq = cash
            for code, pos in positions.items():
                k = (today, code)
                if k in price:
                    _, cl, _, acl = price[k]
                    p = cl if cl and not np.isnan(cl) else acl
                    if p and p > 0 and not np.isnan(p):
                        total_eq += p * pos[2]
            target_w = total_eq / N_STOCKS
            
            for code, mv_val, name in new_codes:
                k = (today, code)
                if k not in price:
                    continue
                op, _, aop, _ = price[k]
                bp = op if op and not np.isnan(op) else aop
                if not bp or bp <= 0 or np.isnan(bp):
                    continue
                qty = int(target_w / bp / 100) * 100
                if qty == 0:
                    continue
                cost = bp * qty
                if cost > cash:
                    continue
                cash -= cost
                positions[code] = (today, bp, qty, cost, name)
    
    # ===== 净值 =====
    eq = 0.0
    for code, (bd, bp, qty, cost, name) in positions.items():
        k = (today, code)
        if k in price:
            _, cl, _, acl = price[k]
            p = cl if cl and not np.isnan(cl) else acl
            if p and p > 0 and not np.isnan(p):
                eq += p * qty
    daily_values.append((today, cash + eq))

# ===== 报告 =====
print("\n" + "=" * 60)
print(f"   🏆 纯小市值 vFinal（{N_STOCKS}只·周调·止损）")
print("=" * 60)
print(f"  选股: 每周按总市值取最小{N_STOCKS}只")
print(f"  过滤: ST历史 | 价格≥{MIN_PRICE}元")
print(f"  风控: {','.join(str(m)+'月' for m in sorted(SKIP_MONTHS))}不买")
print(f"        大盘跌{INDEX_STOP:.0f}%→全清 | 个股跌{STOCK_STOP:.0f}%→止损")
print(f"  区间: 2021-01-01 → {trade_dates[-1]}")

df = pd.DataFrame(daily_values, columns=['date', 'value'])
df['date'] = pd.to_datetime(df['date'])
df.set_index('date', inplace=True)

total_ret = (df['value'].iloc[-1] / INIT_CAPITAL - 1) * 100
n_years = (df.index[-1] - df.index[0]).days / 365.25
ann_ret = ((1 + total_ret/100) ** (1/n_years) - 1) * 100

df['ret'] = df['value'].pct_change()
excess = df['ret'].dropna() - 0.02/252
sharpe = np.sqrt(252) * excess.mean() / excess.std() if excess.std() > 0 else 0

cummax = df['value'].cummax()
max_dd = (df['value'] - cummax) / cummax * 100

print(f"\n📊 全区间:")
print(f"  累计: {total_ret:+.2f}%  年化: {ann_ret:+.2f}%")
print(f"  夏普: {sharpe:.2f}  最大回撤: {max_dd.min():.2f}%")
print(f"  最终: {df['value'].iloc[-1]:,.0f}元")
print(f"  大盘止损: {index_stops}次  个股止损: {stock_stops}次")

if trades:
    df_t = pd.DataFrame(trades)
    win = (df_t['pnl'] > 0).mean() * 100
    print(f"  交易: {len(trades)}笔  胜率: {win:.1f}%")
    print(f"  均值: {df_t['return_pct'].mean():+.2f}%")
    # 止损统计
    stop_trades = df_t[df_t['reason'].str.contains('止损')]
    if len(stop_trades):
        print(f"  止损交易: {len(stop_trades)}笔  止损均值: {stop_trades['return_pct'].mean():+.2f}%")

print(f"\n📅 年度:")
df['year'] = df.index.year
for yr, g in df.groupby('year'):
    ret = (g['value'].iloc[-1] / g['value'].iloc[0] - 1) * 100
    bar = '🟢'*min(10,max(0,int(ret/10))) if ret>0 else '🔴'*min(10,max(0,int(-ret/10)))
    print(f"  {yr}: {ret:+.2f}% {bar}")

df_pre = df[:'2025-12-31']
if len(df_pre) > 1:
    tp = (df_pre['value'].iloc[-1]/INIT_CAPITAL-1)*100
    ap = ((1+tp/100)**(1/((df_pre.index[-1]-df_pre.index[0]).days/365.25))-1)*100
    print(f"\n📊 2021-2025: 累计{tp:+.1f}%  年化{ap:+.1f}%")

st_now = sum(1 for c in positions if is_st(c, trade_dates[-1]))
print(f"\n📋 当前持仓 ({len(positions)}只, ST:{st_now}只):")
for code, (bd,bp,qty,cost,name) in positions.items():
    k = (trade_dates[-1], code)
    cur = None
    if k in price:
        _, cl, _, acl = price[k]
        cur = cl if cl and not np.isnan(cl) else acl
    pnl_pct = (cur/bp-1)*100 if cur and bp else 0
    print(f"  {code} {name:10s} {bd}@{bp:.2f} 现{cur} {pnl_pct:+.1f}%")

if trades:
    df_t2 = df_t.sort_values('sell_date').tail(8).iloc[::-1]
    stop_rows = df_t2[df_t2['reason'].str.contains('止损')]
    normal_rows = df_t2[~df_t2['reason'].str.contains('止损')]
    if len(stop_rows):
        print(f"\n📋 最近止损:")
        for t in stop_rows.itertuples():
            print(f"  {t.buy_date}→{t.sell_date} {t.name:10s} {t.buy_price:.2f}→{t.sell_price:.2f} "
                  f"{t.pnl:+,.0f} ({t.return_pct:+.1f}%) [{t.reason}]")
    print(f"\n📋 最近调仓卖出:")
    for t in normal_rows.itertuples():
        s = '+' if t.pnl > 0 else ''
        print(f"  {t.buy_date}→{t.sell_date} {t.name:10s} {t.buy_price:.2f}→{t.sell_price:.2f} "
              f"{s}{t.pnl:+,.0f} ({t.return_pct:+.1f}%) [{t.reason}]")

print("\n⚠️ 回测≠实盘")
