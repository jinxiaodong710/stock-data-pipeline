#!/usr/bin/env python3.13
"""冠军策略 v1.43 Headless 优化版 - 预计算板块冠军"""
import duckdb, pandas as pd, numpy as np
from collections import defaultdict
from datetime import datetime, timedelta

DB = 'data/stock_data.duckdb'
LOT = 100

P = {
    'start_date': '2024-01-02', 'end_date': '2026-05-15',
    'initial_cash': 500_000, 'num_slots': 4, 'holding_days': 4,
    'market_type': '创业板+主板',
    'min_pct_change': 2.0, 'max_pct_change': 9.0,
    'min_stock_pct': 0.0, 'max_stock_pct': 20.0,
    'min_sector_pct': 8.0, 'max_sector_pct': 20.0,
    'min_mv': 0, 'max_mv': 30,
    'champion_min_count': 1, 'champion_max_count': 10,
    'overbought_factor': 1.5, 'open_pct_limit': 7,
    'fee_rate': 0.0015, 'stop_loss_pct': 0.15,
    'drawdown_sell_factor': 0.97, 'hold_pct_req': 4.0,
    'min_guxing_score': 18, 'guxing_days': 100,
    'rank_mode': '按市值排序',
}

print("=== 冠军策略 v1.43 优化版 ===")
con = duckdb.connect(DB)

# ── 1. 预加载全部数据 ──
print("加载ST..."); st_set={}
for r in con.execute("SELECT ts_code, trade_date FROM stock_st_history").fetchall():
    st_set[(r[0][:6], r[1])] = True
print(f"  {len(st_set)} 条")

print("加载名称..."); names={}
for r in con.execute("SELECT ts_code, name FROM stock_basic_info").fetchall():
    names[r[0][:6]] = r[1]

print("加载行情(全区间)...")
buf = str(pd.Timestamp(P['start_date'])-timedelta(days=360))
pdf = con.execute(f"""
    SELECT date, stock_code, open, close, total_mv, pe, volume, turnover_rate, amplitude
    FROM stock_prices WHERE date>='{buf}' AND date<='{P['end_date']}'
    AND (stock_code LIKE '30%' OR stock_code LIKE '00%' OR stock_code LIKE '60%')
    ORDER BY date, stock_code
""").fetchdf()
pdf['total_mv'] = pd.to_numeric(pdf['total_mv'], errors='coerce')*10000
dates_all = sorted(pdf['date'].unique())

# 构建快速查询: pr[(date,code)] = (open,close,tmv,pe,vol,turn,amp)
pr = {}
for _, r in pdf.iterrows():
    pr[(r['date'], r['stock_code'])] = (r['open'], r['close'], r['total_mv'], r['pe'], r['volume'], r['turnover_rate'], r['amplitude'])

dt_idx = {d:i for i,d in enumerate(dates_all)}
print(f"  行情: {len(pdf):,}行, {len(dates_all)}日, 查询:{len(pr):,}")

# ── 2. 预计算板块映射 (股票→板块列表) ──
print("加载板块...")
concept = defaultdict(list)  # code → [(concept_name, list_date)]
for r in con.execute("SELECT concept_name, con_code, concept_list_date FROM ths_concept_members").fetchall():
    code = r[1].split('.')[0] if '.' in str(r[1]) else str(r[1])
    concept[code].append((r[0], r[2][:10] if r[2] else '1990-01-01'))

for r in con.execute("SELECT DISTINCT ts_code, industry, area FROM stock_basic_info WHERE industry IS NOT NULL OR area IS NOT NULL").fetchall():
    code = r[0][:6]
    if r[1]: concept[code].append((f'行业-{r[1]}', '1990-01-01'))
    if r[2]: concept[code].append((f'地区-{r[2]}', '1990-01-01'))
con.close()
print(f"  板块: {len(concept)} 只股票有板块数据")

# ── 3. 预计算每日板块冠军 ──
print("预计算板块冠军(可能较久)...")
champion_cache = {}  # date → {concept_name → (code, pct)}
for i, today in enumerate(dates_all):
    if i == 0: continue
    prev = dates_all[i-1]
    # 当日涨跌幅
    day_pct = {}
    for code in concept:
        kt = (today, code); kp = (prev, code)
        if kt not in pr or kp not in pr: continue
        if (code, today) in st_set: continue
        cl = pr[kt][1]; pc = pr[kp][1]
        if not cl or not pc or pc <= 0: continue
        pct = (cl/pc-1)*100
        if P['min_pct_change'] <= pct <= P['max_pct_change']:
            day_pct[code] = pct
    
    # 板块冠军
    champs = {}
    for code, pct in day_pct.items():
        for cname, cdate in concept.get(code, []):
            if cdate <= today:
                if cname not in champs or pct > champs[cname][1]:
                    champs[cname] = (code, pct)
    
    champion_cache[today] = champs
    if (i+1) % 200 == 0:
        print(f"  {i+1}/{len(dates_all)} ({today})")

print(f"  预计算完成: {len(champion_cache)} 日")

# ── 4. 快速查询函数 ──
def gp(code, date, col=1):
    """col: 1=close, 0=open, 2=tmv, 3=pe"""
    v = pr.get((date, code))
    if v is None: return None
    r = v[col]
    return float(r) if pd.notna(r) and r > 0 else None

def get_vol_ratio(code, idx):
    if idx < 5: return 0
    cv = gp(code, dates_all[idx], 4)  # volume
    if not cv: return 0
    pv = 0
    for j in range(1, 6):
        v = gp(code, dates_all[idx-j], 4)
        if v: pv += v
    return cv/(pv/min(5,5)) if pv>0 else 0

def get_guxing(code, idx):
    if idx < P['guxing_days']: return (0,0,0)
    start = dates_all[idx-P['guxing_days']]; end = dates_all[idx]
    t,a,m = 0,0,0; n=0
    si = dt_idx[start]; ei = dt_idx[end]
    for j in range(si, ei+1):
        d = dates_all[j]; vv = pr.get((d, code))
        if vv:
            t+=vv[5] or 0; a+=vv[6] or 0; n+=1
    if n<10: return (0,0,0)
    avg_t,avg_a = t/n,a/n
    sp = gp(code, start, 1); ep = gp(code, end, 1)
    mom = (ep/sp-1)*100 if sp and ep and sp>0 else 0
    return (avg_t, avg_a, mom)

def guxing_score(codes, idx):
    if not codes: return {}
    stats = {c: get_guxing(c, idx) for c in codes}
    t_r = pd.Series({c: s[0] for c,s in stats.items()}).rank(pct=True)*100
    a_r = pd.Series({c: s[1] for c,s in stats.items()}).rank(pct=True)*100
    m_r = pd.Series({c: s[2] for c,s in stats.items()}).rank(pct=True)*100
    return {c: 0.4*t_r[c]+0.3*a_r[c]+0.3*m_r[c] for c in codes}

# ── 5. 回测 ──
print("回测...")
bs = dt_idx[P['start_date']]; be = dt_idx[P['end_date']]
cash = float(P['initial_cash']); pos = {}; vals = []; trades = []

for i in range(bs, be+1):
    today = dates_all[i]; is_last = (i==be)
    
    # ── 卖出 ──
    for code in list(pos.keys()):
        p = pos[code]
        cp = gp(code, today, 1)
        if not cp:
            if p['sell_date']==today: p['sell_date'] = dates_all[min(i+1, len(dates_all)-1)]
            continue
        p['hp'] = max(p.get('hp',p['bp']), cp)
        dh = i-dt_idx.get(p['buy_date'], i)
        reason = None
        if dh==1 and P['stop_loss_pct']>0 and cp < p['bp']*(1-P['stop_loss_pct']): reason="T+2止损"
        if not reason and dh>=2 and P['drawdown_sell_factor']<1.0:
            if cp < p['hp']*P['drawdown_sell_factor']: reason="回撤止盈"
            elif i>0:
                pp = gp(code, dates_all[i-1], 1)
                if pp and pp>0 and (cp/pp-1)*100 <= P['hold_pct_req']: reason="涨幅未达标"
        if not reason and p['sell_date']==today and not is_last: reason="到期卖出"
        
        if reason:
            sp=cp; sh=p['sh']; proceeds=sp*sh
            fee=proceeds*P['fee_rate']; cash+=proceeds-fee
            cost=sh*p['bp']; profit=proceeds-fee-cost
            pr2 = profit/cost*100 if cost>0 else 0
            trades.append({'code':code,'buy_date':p['buy_date'],'sell_date':today,'bp':p['bp'],'sp':sp,'pnl':profit,'ret':pr2,'reason':reason,'name':names.get(code,'?')})
            del pos[code]
    
    mv = sum((gp(c,today,1) or pos[c]['bp'])*pos[c]['sh'] for c in pos)
    vals.append((today, cash+mv))
    
    # ── 选股 ──
    if i<1: continue
    
    # 从champion_cache获取当日板块冠军
    champs = champion_cache.get(today, {})
    if not champs: continue
    
    # 统计每只股票是几个板块的冠军
    cnt = defaultdict(int)
    for cname, (code, pct) in champs.items():
        cnt[code] += 1
    
    # 共振筛选
    multi = {c:n for c,n in cnt.items() if P['champion_min_count']<=n<=P['champion_max_count']}
    if not multi: continue
    
    # 过滤涨跌幅范围
    candidates = []
    for code, champ_n in multi.items():
        k = (today, code)
        if k not in pr: continue
        # ST检查
        if (code, today) in st_set: continue
        _, cl, tmv, pe, *_ = pr[k]
        if not cl or not tmv or tmv<=0: continue
        # 涨跌幅
        prev_day = dates_all[i-1]
        pk = (prev_day, code)
        if pk not in pr: continue
        pc = pr[pk][1]
        if not pc or pc<=0: continue
        pct = (cl/pc-1)*100
        if not (P['min_stock_pct']<=pct<=P['max_stock_pct']): continue
        tmv_yi = tmv/1e8
        if not (P['min_mv']<=tmv_yi<=P['max_mv']): continue
        
        # 最佳板块pct
        best_sec_pct = 0
        for cname, (cc, cpct) in champs.items():
            if cc==code and cpct>best_sec_pct: best_sec_pct=cpct
        if not (P['min_sector_pct']<=best_sec_pct<=P['max_sector_pct']): continue
        
        candidates.append((code, pct, cl, tmv, pe, champ_n, best_sec_pct))
    
    if not candidates: continue
    
    # 超买检查
    valid = []
    for code, pct, cl, tmv, pe, cn, bsp in candidates:
        overbought = False
        for d in [5,15,60]:
            hi = i-d
            if hi>=0:
                hc = gp(code, dates_all[hi], 1)
                if hc and hc>0 and cl > hc*P['overbought_factor']:
                    overbought = True; break
        if not overbought: valid.append((code, pct, cl, tmv, pe, cn, bsp))
    
    if not valid: continue
    final = pd.DataFrame(valid, columns=['code','pct','close','tmv','pe','champ_n','sec_pct'])
    
    # 市场过滤
    mt = P['market_type']
    if mt=='仅创业板': final = final[final['code'].str.startswith('30')]
    elif mt=='仅主板': final = final[final['code'].str.startswith(('00','60'))]
    if final.empty: continue
    
    # 量比+股性
    final['vol_r'] = final['code'].apply(lambda c: get_vol_ratio(c, i))
    final = final[final['vol_r']<=30]
    if final.empty: continue
    
    if P['min_guxing_score']>0:
        gs = guxing_score(final['code'].tolist(), i)
        final['gx'] = final['code'].map(gs)
        final = final[final['gx']>=P['min_guxing_score']]
    if final.empty: continue
    
    # 排序
    final.sort_values('tmv', ascending=True, inplace=True)
    
    # ── 买入 ──
    t1 = i+1; si = t1+P['holding_days']
    if t1>=len(dates_all): continue
    bd = dates_all[t1]; sd = dates_all[min(si, len(dates_all)-1)]
    
    slots = max(0, P['num_slots']-len(pos))
    if slots>0 and cash>1000:
        buy = final[~final['code'].isin(pos.keys())].head(slots)
        if not buy.empty:
            cps = cash/slots
            for _, r in buy.iterrows():
                code = r['code']; op = gp(code, bd, 0)
                if not op: continue
                if (code, bd) in st_set: continue
                bc = gp(code, bd, 1)
                if bc and r['close']>0:
                    bpct = (bc/r['close']-1)*100
                    if abs(op-bc)<0.01 and 4.5<=bpct<=5.5: continue
                if P['open_pct_limit']>0 and r['close']>0:
                    if (op/r['close']-1)*100>P['open_pct_limit']: continue
                sh = int(cps/op/LOT)*LOT
                if sh<=0 or sh*op>cash: continue
                cash-=sh*op
                pos[code]={'sh':sh,'bp':op,'buy_date':bd,'sell_date':sd,'hp':op}

last = dates_all[be]
mv = sum((gp(c,last,1) or pos[c]['bp'])*pos[c]['sh'] for c in pos)
vals.append((last, cash+mv))

# ── 报告 ──
df = pd.DataFrame(vals, columns=['date','value'])
df['date']=pd.to_datetime(df['date']); df.set_index('date',inplace=True)
tot = (df['value'].iloc[-1]/P['initial_cash']-1)*100
ny = (df.index[-1]-df.index[0]).days/365.25
ann = ((1+tot/100)**(1/ny)-1)*100
df['ret']=df['value'].pct_change()
ex=df['ret'].dropna()-0.02/252
sr=np.sqrt(252)*ex.mean()/ex.std() if ex.std()>0 else 0
maxdd=((df['value']-df['value'].cummax())/df['value'].cummax()*100).min()

print(f"\n{'='*60}")
print(f"  🏆 冠军策略 v1.43")
print(f"{'='*60}")
print(f"  累计{tot:+.2f}% 年化{ann:+.2f}% 夏普{sr:.2f} 回撤{maxdd:.2f}%")
print(f"  最终{df['value'].iloc[-1]:,.0f} 交易{len(trades)}笔")
if trades:
    dt=pd.DataFrame(trades); print(f"  胜率{(dt['pnl']>0).mean()*100:.1f}% 均值{dt['ret'].mean():+.2f}%")
print(f"\n📅 年度:")
df['year']=df.index.year
for yr,g in df.groupby('year'):
    print(f"  {yr}: {(g['value'].iloc[-1]/g['value'].iloc[0]-1)*100:+.2f}%")
