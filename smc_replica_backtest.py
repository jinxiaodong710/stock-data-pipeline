#!/usr/bin/env python3.13
"""复刻 SMC Simulator v33 策略 (优化版)"""
import duckdb, pandas as pd, numpy as np
from collections import defaultdict

DB = 'data/stock_data.duckdb'
N, HOLD_DAYS, RANK_BOOST, MIN_PRICE, LOT = 10, 3, 25, 2.0, 100
CAP = 1_000_000
EXCLUDE = ('688', '8', '9', '30')

con = duckdb.connect(DB)

# ST
st_set = set()
for r in con.execute("SELECT ts_code, trade_date FROM stock_st_history").fetchall():
    st_set.add((r[0][:6], r[1]))

# 名字含ST/退/PT
bad = set()
for r in con.execute("SELECT ts_code, name FROM stock_basic_info").fetchall():
    if any(k in r[1] for k in ['ST','退','PT']):
        bad.add(r[0][:6])

# 行情
rows = con.execute("""
    SELECT stock_code, date, open, close, circ_mv, total_mv
    FROM stock_prices WHERE date>='2021-01-01' AND date<='2026-05-15'
    ORDER BY date, stock_code
""").fetchall()
con.close()
print(f"行情: {len(rows):,}行")

# 按日期分组，预计算每日可用池
print("预计算每日选股池...")
by_date = defaultdict(list)
for r in rows:
    by_date[r[1]].append(r)

dates = sorted(by_date.keys())
print(f"交易日: {len(dates)}")

daily_pool = {}  # date -> [(code, mv, close), ...]
for dt in dates:
    pool = []
    for r in by_date[dt]:
        code = r[0]
        if code.startswith(EXCLUDE): continue
        cl = r[3]
        if cl is None or np.isnan(cl) or cl <= MIN_PRICE: continue
        if (code, dt) in st_set: continue
        if code in bad: continue
        # 总市值（total_mv）
        m = r[5] if r[5] and not np.isnan(r[5]) and r[5] > 0 else None
        if not m: continue
        pool.append((code, m, cl))
    pool.sort(key=lambda x: x[1])  # 市值升序
    daily_pool[dt] = pool
print(f"  预计算完成")

# 价格字典
price = {}  # (date,code) -> (open,close)
for dt, pool in daily_pool.items():
    seen = set()
    for r in by_date[dt]:
        k = (dt, r[0])
        if k not in seen:
            price[k] = (r[2], r[3])
            seen.add(k)

# ===== 回测 =====
print("回测...")
cash = float(CAP)
pos = {}  # {code: (buy_date, buy_price, shares, cost, buy_rank)}
vals, trades = [], []

for idx, today in enumerate(dates[:-1]):
    tdt = pd.Timestamp(today)
    tomorrow = dates[idx+1]
    pool = daily_pool.get(today, [])
    valid = {s[0] for s in pool}
    rm = {s[0]: i+1 for i, s in enumerate(pool)}
    
    # 卖出检查
    to_sell = []
    for code, (bd, bp, sh, cost, br) in pos.items():
        if code not in valid:
            to_sell.append((code, "不合规")); continue
        dh = (tdt - pd.Timestamp(bd)).days
        cr = rm.get(code)
        reason = None
        if dh >= HOLD_DAYS: reason = f"持有{dh}天"
        if br is not None and cr is not None and (br - cr) >= RANK_BOOST:
            reason = (reason+"," if reason else "") + f"排名升{br-cr}"
        if reason: to_sell.append((code, reason))
    
    # 净值
    eq = sum(price.get((today,c),(0,0))[1]*pos[c][2] for c in pos if (today,c) in price)
    vals.append((today, cash+eq))
    
    # T+1执行卖出
    for code, reason in to_sell:
        if code not in pos: continue
        k = (tomorrow, code)
        if k not in price: continue
        op, _ = price[k]
        if not op or np.isnan(op) or op <= 0: continue
        bd, bp, sh, cost, br = pos[code]
        cash += op*sh
        trades.append({'bd':bd,'sd':tomorrow,'code':code,'bp':bp,'sp':op,
                       'pnl':op*sh-cost,'ret':(op/bp-1)*100,'reason':reason})
        del pos[code]
    
    # T+1买入
    held = set(pos.keys())
    avail = [s for s in pool if s[0] not in held]
    nb = N - len(held)
    if nb > 0 and cash > 0:
        teq = cash + sum(price.get((today,c),(0,0))[1]*pos[c][2] for c in pos if (today,c) in price)
        ps = teq*0.99/N
        for code, _, _ in avail[:nb]:
            k = (tomorrow, code)
            if k not in price: continue
            op, _ = price[k]
            if not op or np.isnan(op) or op <= 0: continue
            ts = int(ps/op/LOT)*LOT
            if ts == 0: continue
            cost = op*ts
            if cost > cash: continue
            cash -= cost
            pos[code] = (tomorrow, op, ts, cost, rm.get(code))

# 最后一天净值
last = dates[-1]
eq = sum(price.get((last,c),(0,0))[1]*pos[c][2] for c in pos if (last,c) in price)
vals.append((last, cash+eq))

# ===== 报告 =====
print("\n" + "="*60)
print(f"  🎯 复刻 SMC v33 | N={N} hold={HOLD_DAYS}d boost={RANK_BOOST}")
print("="*60)
print(f"  选股: close>{MIN_PRICE} 排688/8/9/30 排ST total_mv 市值最小{N}")
print(f"  买卖: T+1开 等资 卖=持≥{HOLD_DAYS}d/排升≥{RANK_BOOST}/不合规")
print(f"  区间: 2021-01-01 → {last}")

df = pd.DataFrame(vals, columns=['date','value'])
df['date'] = pd.to_datetime(df['date']); df.set_index('date',inplace=True)
tot = (df['value'].iloc[-1]/CAP-1)*100
ny = (df.index[-1]-df.index[0]).days/365.25
ann = ((1+tot/100)**(1/ny)-1)*100
df['ret'] = df['value'].pct_change()
ex = df['ret'].dropna()-0.02/252
sr = np.sqrt(252)*ex.mean()/ex.std() if ex.std()>0 else 0
maxdd = ((df['value']-df['value'].cummax())/df['value'].cummax()*100).min()

print(f"\n📊 全区间:")
print(f"  累计{tot:+.2f}% 年化{ann:+.2f}% 夏普{sr:.2f} 回撤{maxdd:.2f}%")
print(f"  最终{df['value'].iloc[-1]:,.0f}元")

if trades:
    dt = pd.DataFrame(trades)
    wr = (dt['pnl']>0).mean()*100
    print(f"  交易{len(trades)}笔 胜率{wr:.1f}% 均值{dt['ret'].mean():+.2f}%")

print(f"\n📅 年度:")
df['year'] = df.index.year
for yr, g in df.groupby('year'):
    ret = (g['value'].iloc[-1]/g['value'].iloc[0]-1)*100
    bar = '🟢'*min(10,max(0,int(ret/10))) if ret>0 else '🔴'*min(10,max(0,int(-ret/10)))
    print(f"  {yr}: {ret:+.2f}% {bar}")

dp = df[:'2025-12-31']
if len(dp)>1:
    tp = (dp['value'].iloc[-1]/CAP-1)*100
    ap = ((1+tp/100)**(1/((dp.index[-1]-dp.index[0]).days/365.25))-1)*100
    print(f"\n📊 2021-2025: 累计{tp:+.1f}% 年化{ap:+.1f}%")

print(f"\n📋 持仓{len(pos)}只:")
for c,(bd,bp,sh,cost,br) in pos.items():
    cur = price.get((last,c),(0,0))[1]; pnl = (cur/bp-1)*100 if cur and bp else 0
    print(f"  {c} {bd}@{bp:.2f} {sh}股 现{cur} {pnl:+.1f}% r{br}")

if trades:
    print(f"\n📋 最近卖出:")
    for t in dt.sort_values('sd').tail(5).iloc[::-1].itertuples():
        s = '+' if t.pnl>0 else ''
        print(f"  {t.bd}→{t.sd} {t.code} {t.bp:.2f}→{t.sp:.2f} {s}{t.pnl:+,.0f} ({t.ret:+.1f}%) [{t.reason}]")
print("\n⚠️ 回测≠实盘")
