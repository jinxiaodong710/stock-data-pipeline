#!/usr/bin/env python3
"""从 Redis 取全市场快照，通过 SSH 发到首尔"""
import redis, json, sys, subprocess

r = redis.Redis(host='localhost', port=6379, db=0)
keys = r.keys('*')
rows = []
skipped = 0

for k in keys:
    try:
        raw = r.get(k)
        if not raw: continue
        parts = raw.decode('utf-8', errors='replace').split('$')
        # L1格式: code$name$ts$open$high$low$last$vol$amt$...[五档]...$turnover$pre_close$up$down
        if len(parts) < 30: 
            skipped += 1; continue
        
        code = parts[0]
        name = parts[1]
        ts = int(parts[2]) if parts[2].isdigit() else 0
        open_p = float(parts[3]) if parts[3] else 0
        high = float(parts[4]) if parts[4] else 0
        low = float(parts[5]) if parts[5] else 0
        last = float(parts[6]) if parts[6] else 0
        volume = float(parts[7]) if parts[7] else 0
        amount = float(parts[8]) if parts[8] else 0
        turnover = float(parts[29]) if len(parts) > 29 and parts[29] else 0
        pre_close = float(parts[30]) if len(parts) > 30 and parts[30] else 0
        
        pct = ((last - pre_close) / pre_close * 100) if pre_close > 0 else 0
        
        rows.append(json.dumps({
            'code': code, 'name': name, 'ts': ts,
            'open': open_p, 'high': high, 'low': low, 'last': last,
            'volume': volume, 'amount': amount,
            'pre_close': pre_close, 'pct_change': round(pct, 2), 'turnover': turnover
        }))
    except:
        skipped += 1

if not rows:
    print('NO_DATA')
    sys.exit(0)

data = '\n'.join(rows)
proc = subprocess.run(
    ['ssh', '-o', 'ProxyCommand=none', '-i',
     '/Users/jin/.ssh/tencent_cloud',
     'ubuntu@43.155.197.236',
     'python3 ~/go/snapshot_receiver.py'],
    input=data, capture_output=True, text=True, timeout=30
)
print(proc.stdout.strip() if proc.stdout else proc.stderr.strip()[:100])
print(f'StockCount:{len(rows)}')

