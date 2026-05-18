#!/usr/bin/env python3.13
"""冠军策略 v2 - 新维度探索"""
import duckdb, pandas as pd, numpy as np
from collections import defaultdict
from datetime import datetime, timedelta

DB = 'data/stock_data.duckdb'
LOT = 100

BASE_P = {
    'start_date': '2021-01-04', 'end_date': '2026-05-15',
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
    'skip_months': set(),  # 新增
    'min_price': 0,       # 新增
    'min_turnover': 0,    # 新增
    'min_consecutive_up': 0,  # 新增
    'max_single_day_pct': 20.0,  # 新增: 单日涨幅上限(防涨停板)
    'index_ma_skip': False,  # 新增: 大盘破位不买
}

print("=== 加载数据 ===")
con = duckdb.connect(DB)

st_set = {}
for r in con.execute("SELECT ts_code, trade_date FROM stock_st_history").fetchall():
    st_set[(r[0][:6], r[1])] = True

names = {}
for r in con.execute("SELECT ts_code, name FROM stock_basic_info").fetchall():
    names[r[0][:6]] = r[1]

buf = str(pd.Timestamp(BASE_P['start_date'])-timedelta(days=370))
pdf = con.execute(f"""
    SELECT date, stock_code, open, close, total_mv, pe, volume, turnover_rate, amplitude
    FROM stock_prices WHERE date>='{buf}' AND date<='{BASE_P['end_date']}'
    AND (stock_code LIKE '30%' OR stock_code LIKE '00%' OR stock_code LIKE '60%')
    ORDER BY date, stock_code
""").fetchdf()
pdf['total_mv'] = pd.to_numeric(pdf['total_mv'], errors='coerce')*10000
dates_all = sorted(pdf['date'].unique())

pr = {}
for _, r in pdf.iterrows():
    pr[(r['date'], r['stock_code'])] = (r['open'], r['close'], r['total_mv'], r['pe'], r['volume'], r['turnover_rate'], r['amplitude'])
dt_idx = {d:i for i,d in enumerate(dates_all)}

# 指数(上证)
idx_close = {}
for r in con.execute("SELECT date, close FROM index_prices WHERE index_code='000001' AND date>='2021-01-01'").fetchall():
    idx_close[r[0]] = r[1]
idx_ma20 = {}
idxs = sorted(idx_close.keys())
for i,d in enumerate(idxs):
    if i>=19: idx_ma20[d] = sum(idx_close[idxs[j]] for j in range(i-19,i+1))/20

concept = defaultdict(list)
for r in con.execute("SELECT concept_name, con_code, concept_list_date FROM ths_concept_members").fetchall():
    code = r[1].split('.')[0] if '.' in str(r[1]) else str(r[1])
    concept[code].append((r[0], r[2][:10] if r[2] else '1990-01-01'))
for r in con.execute("SELECT DISTINCT ts_code, industry, area FROM stock_basic_info WHERE industry IS NOT NULL OR area IS NOT NULL").fetchall():
    code = r[0][:6]
    if r[1]: concept[code].append((f'行业-{r[1]}', '1990-01-01'))
    if r[2]: concept[code].append((f'地区-{r[2]}', '1990-01-01'))
con.close()

print("预计算板块冠军...")
champion_cache = {}
for i, today in enumerate(dates_all):
    if i == 0: continue
    prev = dates_all[i-1]
    day_pct = {}
    for code in concept:
        kt = (today, code); kp = (prev, code)
        if kt not in pr or kp not in pr: continue
        # ST过滤(冠军计算时也要排除)
        if (code, today) in st_set: continue
        cl = pr[kt][1]; pc = pr[kp][1]
        if not cl or not pc or pc <= 0: continue
        pct = (cl/pc-1)*100
        if BASE_P['min_pct_change'] <= pct <= BASE_P['max_pct_change']:
            day_pct[code] = pct
    champs = {}
    for code, pct in day_pct.items():
        for cname, cdate in concept.get(code, []):
            if cdate <= today:
                if cname not in champs or pct > champs[cname][1]:
                    champs[cname] = (code, pct)
    champion_cache[today] = champs
print(f"  完成 {len(champion_cache)} 日")

def gp(code, date, col=1):
    v = pr.get((date, code))
    if v is None: return None
    r = v[col]
    return float(r) if pd.notna(r) and r > 0 else None

def get_vol_ratio(code, idx):
    if idx < 5: return 0
    cv = gp(code, dates_all[idx], 4)
    if not cv: return 0
    pv = sum(gp(code, dates_all[idx-j], 4) or 0 for j in range(1,6))
    return cv/(pv/min(5,5)) if pv>0 else 0

def get_guxing(code, idx, window=100):
    if idx < window: return (0,0,0)
    start = dates_all[idx-window]; end = dates_all[idx]
    t,a,m = 0,0,0; n=0
    si = dt_idx[start]; ei = dt_idx[end]
    for j in range(si, ei+1):
        d = dates_all[j]; vv = pr.get((d, code))
        if vv: t+=vv[5] or 0; a+=vv[6] or 0; n+=1
    if n<10: return (0,0,0)
    avg_t,avg_a = t/n,a/n
    sp = gp(code, start, 1); ep = gp(code, end, 1)
    mom = (ep/sp-1)*100 if sp and ep and sp>0 else 0
    return (avg_t, avg_a, mom)

def guxing_score(codes, idx, window=100):
    if not codes: return {}
    stats = {c: get_guxing(c, idx, window) for c in codes}
    t_r = pd.Series({c: s[0] for c,s in stats.items()}).rank(pct=True)*100
    a_r = pd.Series({c: s[1] for c,s in stats.items()}).rank(pct=True)*100
    m_r = pd.Series({c: s[2] for c,s in stats.items()}).rank(pct=True)*100
    return {c: 0.4*t_r[c]+0.3*a_r[c]+0.3*m_r[c] for c in codes}

def backtest(P):
    bs = dt_idx[P['start_date']]; be = dt_idx[P['end_date']]
    cash = float(P['initial_cash']); pos = {}; vals = []; trades = []
    
    for i in range(bs, be+1):
        today = dates_all[i]; is_last = (i==be)
        
        # 卖出
        for code in list(pos.keys()):
            p = pos[code]
            cp = gp(code, today, 1)
            if not cp:
                if p['sell_date']==today: p['sell_date'] = dates_all[min(i+1, len(dates_all)-1)]
                continue
            p['hp'] = max(p.get('hp',p['bp']), cp)
            dh = i-dt_idx.get(p['buy_date'], i)
            reason = None
            if dh==1 and P['stop_loss_pct']>0 and cp < p['bp']*(1-P['stop_loss_pct']): reason="止损"
            if not reason and dh>=2 and P['drawdown_sell_factor']<1.0:
                if cp < p['hp']*P['drawdown_sell_factor']: reason="回撤"
                elif i>0:
                    pp = gp(code, dates_all[i-1], 1)
                    if pp and pp>0 and (cp/pp-1)*100 <= P['hold_pct_req']: reason="不达标"
            if not reason and p['sell_date']==today and not is_last: reason="到期"
            if reason:
                sp=cp; sh=p['sh']; proceeds=sp*sh
                fee=proceeds*P['fee_rate']; cash+=proceeds-fee
                cost=sh*p['bp']; profit=proceeds-fee-cost
                trades.append({'pnl':profit,'ret':profit/cost*100 if cost>0 else 0})
                del pos[code]
        
        mv = sum((gp(c,today,1) or pos[c]['bp'])*pos[c]['sh'] for c in pos)
        vals.append((today, cash+mv))
        
        if i<1: continue
        
        # 新增: 月份跳过 + 大盘择时
        if P.get('skip_months'):
            if pd.Timestamp(today).month in P['skip_months']:
                continue
        if P.get('index_ma_skip'):
            if today in idx_ma20 and today in idx_close and idx_close[today] < idx_ma20[today]:
                continue
        
        champs = champion_cache.get(today, {})
        if not champs: continue
        
        cnt = defaultdict(int)
        for cname, (code, pct) in champs.items():
            cnt[code] += 1
        
        multi = {c:n for c,n in cnt.items() if P['champion_min_count']<=n<=P['champion_max_count']}
        if not multi: continue
        
        candidates = []
        for code, champ_n in multi.items():
            k = (today, code)
            if k not in pr: continue
            if (code, today) in st_set: continue
            _, cl, tmv, pe, vol, turn, amp = pr[k]
            if not cl or not tmv or tmv<=0: continue
            
            # 新增: 价格过滤
            if P.get('min_price', 0) > 0 and cl < P['min_price']: continue
            # 新增: 换手率过滤
            if P.get('min_turnover', 0) > 0 and (turn is None or turn < P['min_turnover']): continue
            
            prev_day = dates_all[i-1]
            pk = (prev_day, code)
            if pk not in pr: continue
            pc = pr[pk][1]
            if not pc or pc<=0: continue
            pct = (cl/pc-1)*100
            # 新增: 单日涨幅上限
            if pct > P.get('max_single_day_pct', 20): continue
            if not (P['min_stock_pct']<=pct<=P['max_stock_pct']): continue
            tmv_yi = tmv/1e8
            if not (P['min_mv']<=tmv_yi<=P['max_mv']): continue
            
            # 新增: 连续上涨天数
            if P.get('min_consecutive_up', 0) > 0:
                up_count = 0
                for dd in range(1, P['min_consecutive_up']+1):
                    if i-dd >= 0:
                        pd2 = gp(code, dates_all[i-dd], 1)
                        pp2 = gp(code, dates_all[i-dd-1], 1) if i-dd-1 >= 0 else None
                        if pd2 and pp2 and pp2 > 0 and pd2 >= pp2:
                            up_count += 1
                if up_count < P['min_consecutive_up']: continue
            
            best_sec_pct = 0
            for cname, (cc, cpct) in champs.items():
                if cc==code and cpct>best_sec_pct: best_sec_pct=cpct
            if not (P['min_sector_pct']<=best_sec_pct<=P['max_sector_pct']): continue
            candidates.append((code, pct, cl, tmv, pe, champ_n, best_sec_pct, turn or 0))
        
        if not candidates: continue
        
        valid = []
        for c in candidates:
            code, pct, cl, tmv, pe, cn, bsp, turn = c
            overbought = False
            for d in [5,15,60]:
                hi = i-d
                if hi>=0:
                    hc = gp(code, dates_all[hi], 1)
                    if hc and hc>0 and cl > hc*P['overbought_factor']: overbought = True; break
            if not overbought: valid.append(c)
        if not valid: continue
        
        final = pd.DataFrame(valid, columns=['code','pct','close','tmv','pe','champ_n','sec_pct','turn'])
        mt = P['market_type']
        if mt=='仅创业板': final = final[final['code'].str.startswith('30')]
        elif mt=='仅主板': final = final[final['code'].str.startswith(('00','60'))]
        if final.empty: continue
        
        final['vol_r'] = final['code'].apply(lambda c: get_vol_ratio(c, i))
        final = final[final['vol_r']<=30]
        if final.empty: continue
        
        if P.get('min_guxing_score', 0) > 0:
            gs = guxing_score(final['code'].tolist(), i, P.get('guxing_days', 100))
            final['gx'] = final['code'].map(gs)
            final = final[final['gx']>=P['min_guxing_score']]
        if final.empty: continue
        
        final.sort_values('tmv', ascending=True, inplace=True)
        
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
    
    df = pd.DataFrame(vals, columns=['date','value'])
    df['date']=pd.to_datetime(df['date']); df.set_index('date',inplace=True)
    tot = (df['value'].iloc[-1]/P['initial_cash']-1)*100
    ny = (df.index[-1]-df.index[0]).days/365.25
    ann = ((1+tot/100)**(1/ny)-1)*100
    df['ret']=df['value'].pct_change()
    ex=df['ret'].dropna()-0.02/252
    sr=np.sqrt(252)*ex.mean()/ex.std() if ex.std()>0 else 0
    maxdd=((df['value']-df['value'].cummax())/df['value'].cummax()*100).min()
    
    return {'ann':ann, 'tot':tot, 'sr':sr, 'dd':maxdd, 'trades':len(trades),
            'wr':(pd.DataFrame(trades)['pnl']>0).mean()*100 if trades else 0}

# 跑基准
base = backtest(BASE_P)
print(f"\n=== 新维度扫描 ===")
print(f"基准: 年化{base['ann']:.1f}% 回撤{base['dd']:.1f}% 夏普{base['sr']:.2f}")

# 新维度
tests = []

# 1. 板块≥5% (最佳单参数)
p = BASE_P.copy(); p['min_sector_pct'] = 5.0
tests.append(('板块≥5%', p))

# 2. 组合: 板块≥5% + 持有3天 + 持要≥6%
p = BASE_P.copy(); p['min_sector_pct'] = 5.0; p['holding_days'] = 3; p['hold_pct_req'] = 6.0
tests.append(('板块5+持3天+要6%', p))

# 3. 组合 + 回撤止盈0.99
p = BASE_P.copy(); p['min_sector_pct'] = 5.0; p['holding_days'] = 3
p['hold_pct_req'] = 6.0; p['drawdown_sell_factor'] = 0.99
tests.append(('板块5+持3+要6+止0.99', p))

# 4. 新: 1月4月不买
p = BASE_P.copy(); p['skip_months'] = {1, 4}
tests.append(('1月4月不买', p))

# 5. 新: 大盘MA20破位不买
p = BASE_P.copy(); p['index_ma_skip'] = True
tests.append(('大盘MA20破不买', p))

# 6. 新: 最低价≥3元
p = BASE_P.copy(); p['min_price'] = 3.0
tests.append(('最低价≥3元', p))

# 7. 新: 换手率≥2%
p = BASE_P.copy(); p['min_turnover'] = 2.0
tests.append(('换手≥2%', p))

# 8. 新: 单日涨幅≤19%(去涨停板)
p = BASE_P.copy(); p['max_single_day_pct'] = 19.0
tests.append(('单日≤19%', p))

# 9. 新: 连续上涨≥2天
p = BASE_P.copy(); p['min_consecutive_up'] = 2
tests.append(('连涨≥2天', p))

# 10. 最佳组合 + 新维度
p = BASE_P.copy(); p['min_sector_pct'] = 5.0; p['holding_days'] = 3
p['hold_pct_req'] = 6.0; p['drawdown_sell_factor'] = 0.99
p['min_price'] = 3.0; p['min_turnover'] = 2.0
tests.append(('组合+价3+换2', p))

# 11. 全组合 + 1月4月
p2 = p.copy(); p2['skip_months'] = {1, 4}
tests.append(('全组合+1/4休', p2))

# 12. 持有2天 + 板块5 + 要6
p = BASE_P.copy(); p['min_sector_pct'] = 5.0; p['holding_days'] = 2; p['hold_pct_req'] = 6.0
tests.append(('板块5+持2+要6', p))

for name, p in [('🏆基准', BASE_P)] + tests:
    r = backtest(p)
    marker = '⭐' if r['ann'] > base['ann']+5 and abs(r['dd']) < abs(base['dd'])+5 else ''
    if r['ann'] > base['ann'] and abs(r['dd']) < abs(base['dd']):
        marker = '🔥'
    print(f"{name:24s} 年化{r['ann']:+6.1f}% 回撤{r['dd']:+6.1f}% 夏普{r['sr']:5.2f} {marker}")
