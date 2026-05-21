#!/bin/bash
export DYLD_LIBRARY_PATH="/opt/homebrew/opt/expat/lib:$DYLD_LIBRARY_PATH"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

/opt/homebrew/bin/python3.13 -c "
import redis, json, sys, os
try:
    r = redis.Redis(host='localhost', port=6379, db=0, socket_connect_timeout=3)
    keys = r.keys('*')
    with open('/tmp/snapshot.jsonl','w') as f:
        for k in keys:
            raw = r.get(k)
            if not raw: continue
            p = raw.decode('utf-8','replace').split('\$')
            if len(p) < 30: continue
            code, name, ts = p[0], p[1], int(p[2]) if p[2].isdigit() else 0
            last = float(p[6]) if p[6] else 0
            pre = float(p[30]) if len(p)>30 and p[30] else 0
            pct = ((last-pre)/pre*100) if pre>0 else 0
            f.write(json.dumps({'code':code,'name':name,'ts':ts,'open':float(p[3] or 0),'high':float(p[4] or 0),'low':float(p[5] or 0),'last':last,'volume':float(p[7] or 0),'amount':float(p[8] or 0),'pre_close':pre,'pct_change':round(pct,2),'turnover':float(p[29] or 0) if len(p)>29 else 0})+'\n')
    print(f'JSON_OK {len(keys)}')
except Exception as e:
    print(f'JSON_ERR {e}', file=sys.stderr)
" 2>/dev/null

[ -f /tmp/snapshot.jsonl ] && /usr/bin/scp -o ProxyCommand=none -o ConnectTimeout=5 -i /Users/jin/.ssh/tencent_cloud /tmp/snapshot.jsonl ubuntu@43.155.197.236:/tmp/snapshot.jsonl 2>/dev/null && \
/usr/bin/ssh -o ProxyCommand=none -o ConnectTimeout=5 -i /Users/jin/.ssh/tencent_cloud ubuntu@43.155.197.236 "docker exec -i seoul-data python3 /app/snapshot_receiver.py < /tmp/snapshot.jsonl" 2>/dev/null && \
echo "$(date +%H:%M:%S) OK" >> /tmp/sync.log
