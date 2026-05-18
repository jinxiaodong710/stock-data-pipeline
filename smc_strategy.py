#!/usr/bin/env python3
"""复刻 SMC Simulator v33 策略（ST过滤修正版 v3）"""
import duckdb, pandas as pd, numpy as np
from collections import defaultdict

DB = '/Users/jin/go/data/stock_data.duckdb'  # Mac: '/Users/jin/go/data/stock_data.duckdb'
N, HOLD_DAYS, RANK_BOOST, MIN_PRICE, LOT = 10, 3, 25, 2.0, 100
CAP = 1_000_000
EXCLUDE = ('688', '8', '9', '30')

con = duckdb.connect(DB, read_only=True)

# 每日ST状态 { (code, date) }
st_set = set()
for r in con.execute("SELECT ts_code, trade_date FROM stock_st_history").fetchall():
    st_set.add((r[0][:6], r[1]))

# 曾有过ST/退/PT标记的股票（替代bad集，不依赖stock_basic_info.name）
ever_st = set()
for r in con.execute("SELECT DISTINCT ts_code FROM stock_st_history WHERE name LIKE '%ST%' OR name LIKE '%退%' OR name LIKE '%PT%'").fetchall():
    ever_st.add(r[0][:6])

# 行情数据
rows = con.execute("""
    SELECT stock_code, date, open, close, circ_mv, total_mv
    FROM stock_prices WHERE date>='2021-01-01' AND date<='2026-05-15'
    ORDER BY date, stock_code
""").fetchall()
con.close()
print(f"行情: {len(rows):,}行")

print("预计算每日选股池...")
by_date = defaultdict(list)
for r in rows:
    by_date[r[1]].append(r)
dates = sorted(by_date.keys())
print(f"交易日: {len(dates)}")

daily_pool = {}
price = {}
for dt in dates:
    pool = []
    for r in by_date[dt]:
        code = r[0]
        if code.startswith(EXCLUDE): continue
        cl = r[3]
        if cl is None or np.isnan(cl) or cl <= MIN_PRICE: continue
        # ST过滤：每日状态 + 曾有过ST/退/PT标记
        if (code, dt) in st_set: continue
        if code in ever_st: continue
        m = r[5] if r[5] and not np.isnan(r[5]) and r[5] > 0 else None
        if not m: continue
        pool.append((code, m, cl))
    pool.sort(key=lambda x: x[1])
    daily_pool[dt] = pool
    for r in by_date[dt]:
        price[(dt, r[0])] = (r[2], r[3])
print(f"  预计算完成")

print("回测...")
cash = float(CAP)
pos = {}
vals, trades = [], []

for idx, today in enumerate(dates[:-1]):
    tdt = pd.Timestamp(today)
    tomorrow = dates[idx+1]
    pool = daily_pool.get(today, [])
    valid = {s[0] for s in pool}
    rm = {s[0]: i+1 for i, s in enumerate(pool)}

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

    eq = sum(price.get((today,c),(0,0))[1]*pos[c][2] for c in pos if (today,c) in price)
    vals.append((today, cash+eq))

    for code, reason in to_sell:
        if code not in pos: continue
        k = (tomorrow, code)
        if k not in price: continue
        op, _ = price[k]
        if not op or np.isnan(op) or op <= 0: continue
        # 跌停检查(用昨收vs今开): 卖不出去, 保留持仓
        yest_k = (today, code)
        if yest_k in price:
            _, yest_cl = price[yest_k]
            if yest_cl and yest_cl > 0 and op <= yest_cl * (0.952 if (code, tomorrow) in st_set else 0.905):
                continue
        bd, bp, sh, cost, br = pos[code]
        cash += op*sh
        trades.append({'bd':bd,'sd':tomorrow,'code':code,'bp':bp,'sp':op,
                       'pnl':op*sh-cost,'ret':(op/bp-1)*100,'reason':reason})
        del pos[code]

    held = set(pos.keys())
    avail = [s for s in pool if s[0] not in held]
    nb = N - len(held)
    if nb > 0 and cash > 0:
        teq = cash + sum(price.get((today,c),(0,0))[1]*pos[c][2] for c in pos if (today,c) in price)
        ps = teq*0.99/N
        for code, _, cl_prev in avail[:nb]:
            k = (tomorrow, code)
            if k not in price: continue
            op, _ = price[k]
            # 涨停检查: 买不到, 跳过
            if cl_prev and cl_prev > 0 and op >= cl_prev * (1.048 if (code, tomorrow) in st_set else 1.095):
                continue
            if not op or np.isnan(op) or op <= 0: continue
            ts = int(ps/op/LOT)*LOT
            if ts == 0: continue
            cost = op*ts
            if cost > cash: continue
            cash -= cost
            pos[code] = (tomorrow, op, ts, cost, rm.get(code))

last = dates[-1]
eq = sum(price.get((last,c),(0,0))[1]*pos[c][2] for c in pos if (last,c) in price)
vals.append((last, cash+eq))

df = pd.DataFrame(vals, columns=['date','value'])
df['date'] = pd.to_datetime(df['date']); df.set_index('date',inplace=True)
tot = (df['value'].iloc[-1]/CAP-1)*100
ny = (df.index[-1]-df.index[0]).days/365.25
ann = ((1+tot/100)**(1/ny)-1)*100
df['ret'] = df['value'].pct_change()
ex = df['ret'].dropna()-0.02/252
sr = np.sqrt(252)*ex.mean()/ex.std() if ex.std()>0 else 0
maxdd = ((df['value']-df['value'].cummax())/df['value'].cummax()*100).min()

print(f"\n累计{tot:+.2f}%  年化{ann:+.2f}%  夏普{sr:.2f}  回撤{maxdd:.2f}%")
print(f"最终{df['value'].iloc[-1]:,.0f}元  交易{len(trades)}笔  胜率{(pd.DataFrame(trades)['pnl']>0).mean()*100:.1f}%")

if trades:
    print(f"年度:")
    df['year'] = df.index.year
    for yr, g in df.groupby('year'):
        ret = (g['value'].iloc[-1]/g['value'].iloc[0]-1)*100
        print(f"  {yr}: {ret:+.2f}%")
