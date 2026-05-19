# -*- coding: utf-8 -*-
"""
Redis旁路写库优化版
- 午休/闭市自动降频，避免空转高CPU
- 对行情做内存去重，未变化不重复写DuckDB
- Redis读取使用 mget，减少大量 get 往返
- raw_messages 默认关闭，避免每秒重复追加
"""
import os, time, re, math
from pathlib import Path
from datetime import datetime, date, time as dtime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple
import duckdb, redis
from dotenv import load_dotenv

load_dotenv()
REDIS_HOST=os.getenv('REDIS_HOST','127.0.0.1')
REDIS_PORT=int(os.getenv('REDIS_PORT','6379'))
REDIS_DB=int(os.getenv('REDIS_DB','0'))
REDIS_PASSWORD=os.getenv('REDIS_PASSWORD') or None
POLL_SECONDS=float(os.getenv('POLL_SECONDS','1.0'))
LUNCH_SLEEP_SECONDS=float(os.getenv('LUNCH_SLEEP_SECONDS','10.0'))
CLOSED_SLEEP_SECONDS=float(os.getenv('CLOSED_SLEEP_SECONDS','30.0'))
AFTER_CLOSE_SLEEP_SECONDS=float(os.getenv('AFTER_CLOSE_SLEEP_SECONDS','10.0'))
STAT_SECONDS=float(os.getenv('STAT_SECONDS','30.0'))
FORCE_RUN_ALL_DAY=os.getenv('FORCE_RUN_ALL_DAY','0') == '1'
SAVE_RAW_MESSAGES=os.getenv('SAVE_RAW_MESSAGES','0') == '1'
_default_data = Path(__file__).resolve().parent / 'data'
DATA_DIR=Path(os.getenv('DATA_DIR', str(_default_data))); DATA_DIR.mkdir(parents=True, exist_ok=True)
PREFIXES=tuple(x.strip() for x in os.getenv('STOCK_PREFIXES','00,30,60,68').split(',') if x.strip())
MAX_SCAN_KEYS=int(os.getenv('MAX_SCAN_KEYS','0'))
CST_TZ = timezone(timedelta(hours=8))

def _now():
    return datetime.now(CST_TZ)

CODE_RE=re.compile(r'^(?:SH|SZ|BJ)?(\d{6})$')
LAST_SIG: Dict[str, Tuple[Any, ...]] = {}

def fnum(x):
    try:
        if x is None or x=='': return None
        v=float(x); return v if math.isfinite(v) else None
    except Exception: return None

def parse_ts(raw):
    try: n=int(float(raw))
    except Exception: return datetime.now()
    return datetime.fromtimestamp(n) if 946684800 <= n <= 4102444800 else datetime.now()

def normalize_code(key, full_code):
    for item in (full_code, key):
        m=CODE_RE.match(str(item or '').strip().upper())
        if m: return m.group(1)
    return None

def parse_quote(key: str, value: str) -> Optional[Dict[str,Any]]:
    parts=(value or '').strip().strip('#').split('$')
    if len(parts) < 35: return None
    code=normalize_code(key, parts[0])
    if not code or not code.startswith(PREFIXES): return None
    name=parts[1].strip() if len(parts)>1 else ''
    if not name: return None
    ts=parse_ts(parts[2] if len(parts)>2 else None)
    trade_date=ts.date(); minute_ts=ts.replace(second=0, microsecond=0); minute_str=minute_ts.strftime('%Y-%m-%d %H:%M')
    last=fnum(parts[6])
    if last is None or last <= 0: return None
    return {
        'trade_date':trade_date, 'minute_ts':minute_ts, 'minute_str':minute_str, 'code':code, 'name':name,
        'high_price':fnum(parts[4]), 'last_price':last, 'pre_close':fnum(parts[30]) if len(parts)>30 else None,
        'pct_change':fnum(parts[29]) if len(parts)>29 else None, 'turnover_rate':fnum(parts[34]) if len(parts)>34 else None,
        'volume':fnum(parts[7]), 'amount':fnum(parts[8]), 'volume_ratio':None,
        'inner_volume':fnum(parts[35]) if len(parts)>35 else None, 'outer_volume':fnum(parts[36]) if len(parts)>36 else None,
        'up_limit_price':fnum(parts[31]) if len(parts)>31 else None, 'down_limit_price':fnum(parts[32]) if len(parts)>32 else None,
        'raw_message': value
    }

def quote_sig(q: Dict[str,Any]) -> Tuple[Any, ...]:
    return (q.get('minute_str'), q.get('last_price'), q.get('volume'), q.get('amount'), q.get('pct_change'), q.get('high_price'))

def db_path_for(d: date) -> Path: return DATA_DIR / f'intraday_snapshots_{d:%Y-%m-%d}.duckdb'

def init_db(con):
    con.execute('''CREATE TABLE IF NOT EXISTS minute_snapshots (
        trade_date DATE NOT NULL, minute_ts TIMESTAMP, minute_str VARCHAR NOT NULL, code VARCHAR NOT NULL, name VARCHAR,
        high_price DOUBLE, last_price DOUBLE, pre_close DOUBLE, pct_change DOUBLE, turnover_rate DOUBLE, volume DOUBLE, amount DOUBLE,
        volume_ratio DOUBLE, inner_volume DOUBLE, outer_volume DOUBLE, up_limit_price DOUBLE, down_limit_price DOUBLE,
        PRIMARY KEY (trade_date, minute_str, code))''')
    if SAVE_RAW_MESSAGES:
        con.execute('''CREATE TABLE IF NOT EXISTS raw_messages (trade_date DATE, minute_ts TIMESTAMP, minute_str VARCHAR, code VARCHAR, raw_message VARCHAR, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

def flush_rows(con, rows: List[Dict[str,Any]]):
    if not rows: return 0
    data=[(r['trade_date'],r['minute_ts'],r['minute_str'],r['code'],r['name'],r['high_price'],r['last_price'],r['pre_close'],r['pct_change'],r['turnover_rate'],r['volume'],r['amount'],r['volume_ratio'],r['inner_volume'],r['outer_volume'],r['up_limit_price'],r['down_limit_price']) for r in rows]
    con.executemany('''INSERT OR REPLACE INTO minute_snapshots (trade_date,minute_ts,minute_str,code,name,high_price,last_price,pre_close,pct_change,turnover_rate,volume,amount,volume_ratio,inner_volume,outer_volume,up_limit_price,down_limit_price) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', data)
    if SAVE_RAW_MESSAGES:
        raw=[(r['trade_date'], r['minute_ts'], r['minute_str'], r['code'], r['raw_message']) for r in rows]
        con.executemany('INSERT INTO raw_messages (trade_date, minute_ts, minute_str, code, raw_message) VALUES (?, ?, ?, ?, ?)', raw)
    return len(rows)

def connect_db(d):
    p=db_path_for(d); con=duckdb.connect(str(p)); init_db(con); return con,p

def market_sleep_seconds(now: datetime) -> float:
    if FORCE_RUN_ALL_DAY: return POLL_SECONDS
    t=now.time()
    if dtime(9,15) <= t < dtime(11,30): return POLL_SECONDS
    if dtime(13,0) <= t < dtime(15,5): return POLL_SECONDS
    if dtime(11,30) <= t < dtime(13,0): return LUNCH_SLEEP_SECONDS
    if dtime(15,5) <= t < dtime(16,0): return AFTER_CLOSE_SLEEP_SECONDS
    return CLOSED_SLEEP_SECONDS

def should_scan_now(now: datetime) -> bool:
    return FORCE_RUN_ALL_DAY or market_sleep_seconds(now) == POLL_SECONDS

def collect_keys(r) -> List[str]:
    keys=[]; scanned=0
    for key in r.scan_iter(match='*', count=1000):
        sk=str(key)
        if not re.match(r'^\d{6}$', sk): continue
        if not sk.startswith(PREFIXES): continue
        keys.append(sk); scanned += 1
        if MAX_SCAN_KEYS and scanned>=MAX_SCAN_KEYS: break
    return keys

def main():
    print('='*70)
    print(f'Redis旁路写库优化版：{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}')
    print(f'写入目录：{DATA_DIR.resolve()}')
    print(f'股票前缀：{PREFIXES}')
    print(f'交易轮询={POLL_SECONDS}s 午休={LUNCH_SLEEP_SECONDS}s 收盘后={AFTER_CLOSE_SLEEP_SECONDS}s 闭市={CLOSED_SLEEP_SECONDS}s')
    print(f'去重写入：开启；raw_messages保存：{SAVE_RAW_MESSAGES}')
    print('写库格式：intraday_snapshots_YYYY-MM-DD.duckdb / minute_snapshots')
    print('='*70)

    r=redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB, password=REDIS_PASSWORD, decode_responses=True)
    print('PING:', r.ping())
    cur_date=datetime.now(CST_TZ).date(); con,path=connect_db(cur_date); print('当前数据库：', path)
    last_stat=0; last_phase_msg=0

    while True:
        try:
            now_dt=_now(); sleep_s=market_sleep_seconds(now_dt)
            today=datetime.now(CST_TZ).date()
            if today != cur_date:
                con.close(); cur_date=today; LAST_SIG.clear(); con,path=connect_db(cur_date); print('切换数据库：', path)

            if not should_scan_now(now_dt):
                if time.time() - last_phase_msg >= 60:
                    print(f"[{now_dt:%Y-%m-%d %H:%M:%S}] 非连续交易写库时段，降频 sleep={sleep_s}s")
                    last_phase_msg=time.time()
                time.sleep(sleep_s)
                continue

            keys=collect_keys(r)
            vals=r.mget(keys) if keys else []
            rows=[]; parsed=0; changed=0
            for sk, val in zip(keys, vals):
                q=parse_quote(sk, val or '')
                if not q: continue
                parsed += 1
                sig=quote_sig(q)
                if LAST_SIG.get(q['code']) == sig:
                    continue
                LAST_SIG[q['code']] = sig
                rows.append(q); changed += 1

            if rows: flush_rows(con, rows)

            now=time.time()
            if now-last_stat>=STAT_SECONDS:
                mx=con.execute('SELECT COUNT(*), COUNT(DISTINCT code), MAX(minute_str) FROM minute_snapshots').fetchone()
                print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] keys={len(keys)} parsed={parsed} changed_write={changed} 今日库行={mx[0]} 股票={mx[1]} 最新={mx[2]}")
                last_stat=now
            time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            print('收到 Ctrl+C，退出。'); break
        except Exception as e:
            print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] ❌ 异常：{e}"); time.sleep(3)
    try: con.close()
    except Exception: pass

if __name__=='__main__': main()
